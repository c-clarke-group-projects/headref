"""Connect a workspace to Jira through Atlassian OAuth 2.0 (W4, DEC-023).

Flow:
1. An owner presses Connect Jira. POST /w/{id}/connections/jira/start signs a state (the
   same helper as GitHub), keeps it in a cookie and sends the browser to Atlassian.
2. Atlassian returns to GET /connections/jira/callback with a code and the state. The
   state must match the cookie, be signed, fresh and made for this account, and the
   account must still own the workspace. Then the code is exchanged and the tokens are
   stored encrypted.
3. With 1 reachable Jira site it is chosen; with several the owner picks 1. Then the
   owner picks projects, checked against what Atlassian returns.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete, select

from app.config import get_settings
from app.integrations.atlassian import AtlassianClient, authorize_url
from app.integrations.crypto import CredentialError
from app.integrations.errors import IntegrationError
from app.integrations.jira_tokens import store_tokens, valid_access_token
from app.models.connections import JiraConnection, WorkspaceJiraProject
from app.web.auth import DbDep
from app.web.connections import make_connection_state, owns_workspace, workspace_from_state
from app.web.ui_format import templates
from app.web.workspace_pages import _base_context
from app.web.workspaces import AccountDep, OwnerAccess

logger = logging.getLogger(__name__)

JIRA_STATE_COOKIE = "headref_jira_state"
JIRA_STATE_MAX_AGE_SECONDS = 15 * 60

JIRA_MESSAGES = {
    "connected": "Jira is connected. Choose the projects this workspace should use.",
    "choose_site": "Your Atlassian account can reach more than 1 Jira site. Choose 1.",
    "saved": "Project selection saved.",
    "disconnected": "Jira was disconnected from this workspace and its stored tokens were "
    "deleted. To remove headref's access entirely, open your Atlassian account settings, "
    "Connected apps.",
    "not_configured": "The headref Atlassian app is not configured on this server.",
    "bad_state": "That Jira connection could not be verified. Start again from the "
    "workspace page.",
    "not_owner": "Only the workspace owner can connect Jira.",
    "denied": "Jira access was not granted on the Atlassian consent screen.",
    "no_sites": "Your Atlassian account cannot read any Jira site with the requested "
    "read-only scopes.",
    "auth_failed": "Atlassian no longer accepts this connection. Reconnect Jira.",
    "upstream": "Atlassian could not be reached. Nothing was changed; try again shortly.",
    "invalid_selection": "1 or more selected items are not available on this site. "
    "Nothing was saved.",
}

jira_pages = APIRouter(tags=["connections"])


def get_atlassian_client() -> AtlassianClient:
    """The Atlassian client. A function, so tests can replace it."""
    return AtlassianClient()


def _to(workspace_id: int, code: str, picker: bool = False) -> RedirectResponse:
    path = f"/w/{workspace_id}/connections/jira" if picker else f"/w/{workspace_id}"
    return RedirectResponse(
        url=f"{path}?{urlencode({'jira': code})}", status_code=status.HTTP_303_SEE_OTHER
    )


def _home(code: str) -> RedirectResponse:
    return RedirectResponse(
        url="/workspaces?" + urlencode({"jira": code}), status_code=status.HTTP_303_SEE_OTHER
    )


def _connection(db: Any, workspace_id: int) -> JiraConnection | None:
    return db.scalar(select(JiraConnection).where(JiraConnection.workspace_id == workspace_id))


@jira_pages.post("/w/{workspace_id}/connections/jira/start")
def start_jira_connect(request: Request, access: OwnerAccess) -> Response:
    """Send the owner to the Atlassian consent screen with a signed state."""
    settings = get_settings()
    if not settings.atlassian_enabled:
        return _to(access.workspace.id, "not_configured")
    state = make_connection_state(access.workspace.id, access.account.id)
    url = authorize_url(settings.atlassian_client_id, str(request.url_for("jira_callback")), state)
    response = RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        JIRA_STATE_COOKIE,
        state,
        max_age=JIRA_STATE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@jira_pages.get("/connections/jira/callback", name="jira_callback")
def jira_callback(request: Request, account: AccountDep, db: DbDep) -> Response:
    """Exchange the code, store the tokens encrypted, and choose the site if only 1."""
    params = request.query_params
    workspace_id = workspace_from_state(
        params.get("state"), request.cookies.get(JIRA_STATE_COOKIE), account.id
    )
    if workspace_id is None:
        logger.warning("jira callback rejected: bad state, account_id=%s", account.id)
        return _home("bad_state")
    if not owns_workspace(db, workspace_id, account.id):
        return _home("not_owner")
    if params.get("error") or not params.get("code"):
        return _to(workspace_id, "denied")

    client = get_atlassian_client()
    try:
        tokens = client.exchange_code(params["code"], str(request.url_for("jira_callback")))
        sites = client.accessible_sites(tokens.access_token)
    except IntegrationError as exc:
        logger.warning("jira callback upstream failure: %s", exc.error_type)
        return _to(workspace_id, "upstream")
    if not sites:
        return _to(workspace_id, "no_sites")

    connection = _connection(db, workspace_id)
    if connection is None:
        connection = JiraConnection(workspace_id=workspace_id, created_at=datetime.now(UTC))
        db.add(connection)
    elif connection.cloud_id and connection.cloud_id not in {s.cloud_id for s in sites}:
        # Reconnected with an account that cannot reach the old site: its projects go.
        db.execute(
            delete(WorkspaceJiraProject).where(WorkspaceJiraProject.workspace_id == workspace_id)
        )
        connection.cloud_id = None
    store_tokens(connection, tokens)
    connection.connected_by_account_id = account.id
    if len(sites) == 1:
        connection.cloud_id = sites[0].cloud_id
        connection.site_url = sites[0].url[:255]
        connection.site_name = sites[0].name[:255]
    if connection.cloud_id:
        connection.status = "active"
        code = "connected"
    else:
        connection.status = "pending_site"
        code = "choose_site"
    db.commit()
    logger.info("jira connected: workspace_id=%s status=%s", workspace_id, connection.status)
    response = _to(workspace_id, code, picker=True)
    response.delete_cookie(JIRA_STATE_COOKIE)
    return response


@jira_pages.get("/w/{workspace_id}/connections/jira", response_class=HTMLResponse)
def jira_picker(request: Request, access: OwnerAccess, db: DbDep) -> HTMLResponse:
    """Choose the site (when several) and the projects this workspace reads."""
    workspace = access.workspace
    connection = _connection(db, workspace.id)
    sites: list[Any] = []
    projects: list[dict[str, str]] = []
    error: str | None = None
    if connection is not None and connection.status != "auth_failed":
        client = get_atlassian_client()
        try:
            token = valid_access_token(db, connection.id, client)
            if connection.status == "pending_site":
                sites = client.accessible_sites(token)
            else:
                projects = client.list_projects(str(connection.cloud_id), token)
        except IntegrationError as exc:
            # Rule 9: a failure is shown as a failure, never as "no projects".
            error = exc.error_type
        except CredentialError:
            error = "AUTH_FAILED"
        db.refresh(connection)
    selected = set(
        db.scalars(
            select(WorkspaceJiraProject.project_id).where(
                WorkspaceJiraProject.workspace_id == workspace.id
            )
        ).all()
    )
    context = _base_context(request, db, access.account, "workspaces")
    context.update(
        {
            "workspace": workspace,
            "connection": connection,
            "sites": sites,
            "projects": projects,
            "selected": selected,
            "error": error,
            "message": JIRA_MESSAGES.get(request.query_params.get("jira", "")),
            "app_configured": get_settings().atlassian_enabled,
        }
    )
    return templates.TemplateResponse(request=request, name="jira_connection.html", context=context)


async def _form(request: Request) -> dict[str, list[str]]:
    return parse_qs((await request.body()).decode("utf-8"))


@jira_pages.post("/w/{workspace_id}/connections/jira/site")
async def choose_jira_site(request: Request, access: OwnerAccess, db: DbDep) -> Response:
    """Choose 1 of the reachable sites. The cloud id must be among them."""
    workspace_id = access.workspace.id
    connection = _connection(db, workspace_id)
    if connection is None or connection.status != "pending_site":
        return _to(workspace_id, "invalid_selection", picker=True)
    cloud_id = (await _form(request)).get("cloud_id", [""])[0]
    client = get_atlassian_client()
    try:
        sites = {
            s.cloud_id: s
            for s in client.accessible_sites(valid_access_token(db, connection.id, client))
        }
    except IntegrationError:
        return _to(workspace_id, "upstream", picker=True)
    site = sites.get(cloud_id)
    if site is None:
        return _to(workspace_id, "invalid_selection", picker=True)
    connection = _connection(db, workspace_id)
    connection.cloud_id = site.cloud_id
    connection.site_url = site.url[:255]
    connection.site_name = site.name[:255]
    connection.status = "active"
    db.commit()
    return _to(workspace_id, "connected", picker=True)


@jira_pages.post("/w/{workspace_id}/connections/jira/projects")
async def save_jira_projects(request: Request, access: OwnerAccess, db: DbDep) -> Response:
    """Replace the project selection. Ids must be on the site; keys and names come from
    Atlassian, never from the form."""
    workspace_id = access.workspace.id
    connection = _connection(db, workspace_id)
    if connection is None or connection.status != "active":
        return _to(workspace_id, "invalid_selection", picker=True)
    wanted = set((await _form(request)).get("project", []))
    client = get_atlassian_client()
    try:
        available = {
            p["id"]: p
            for p in client.list_projects(
                str(connection.cloud_id), valid_access_token(db, connection.id, client)
            )
        }
    except IntegrationError:
        return _to(workspace_id, "upstream", picker=True)
    if not wanted <= set(available):
        return _to(workspace_id, "invalid_selection", picker=True)
    connection = _connection(db, workspace_id)
    db.execute(
        delete(WorkspaceJiraProject).where(WorkspaceJiraProject.workspace_id == workspace_id)
    )
    db.add_all(
        WorkspaceJiraProject(
            workspace_id=workspace_id,
            jira_connection_id=connection.id,
            project_id=pid,
            key=available[pid]["key"][:64],
            name=available[pid]["name"][:255],
            added_by_account_id=access.account.id,
        )
        for pid in sorted(wanted)
    )
    db.commit()
    logger.info("jira projects saved: workspace_id=%s count=%s", workspace_id, len(wanted))
    return _to(workspace_id, "saved", picker=True)


@jira_pages.post("/w/{workspace_id}/connections/jira/disconnect")
def disconnect_jira(access: OwnerAccess, db: DbDep) -> Response:
    """Delete this workspace's Jira connection, its tokens and its project selection."""
    workspace_id = access.workspace.id
    db.execute(
        delete(WorkspaceJiraProject).where(WorkspaceJiraProject.workspace_id == workspace_id)
    )
    db.execute(delete(JiraConnection).where(JiraConnection.workspace_id == workspace_id))
    db.commit()
    logger.info("jira disconnected: workspace_id=%s", workspace_id)
    return _to(workspace_id, "disconnected")
