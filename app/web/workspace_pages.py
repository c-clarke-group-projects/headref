"""Workspace pages: portfolio home, create workspace, workspace shell (W2, DEC-021).

Every page under /w/{workspace_id} takes a workspace_access dependency from
app.web.workspaces; the route coverage test in tests/test_workspaces.py fails if one
does not. Connections, sync, analysis and the feed fill the shell in W3 to W8; until
then each section says plainly that it is not set up, never shows invented data.
"""

from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.canonical import AppUser
from app.models.connections import (
    GitHubInstallation,
    JiraConnection,
    WorkspaceJiraProject,
    WorkspaceRepository,
)
from app.models.workspace import Account
from app.people import confirmed_people, waiting_count
from app.schemas.workspace import WorkspaceCreate
from app.web.auth import DbDep
from app.web.ui_format import templates
from app.web.workspaces import (
    AccountDep,
    ManagerAccess,
    ViewerAccess,
    create_workspace,
    list_workspaces_for,
)
from app.workspace_analysis import analyze_workspace, member_summary
from app.workspace_feed import feed, mark_seen
from app.workspace_sync import (
    ensure_organization,
    has_sources,
    is_running,
    latest_runs,
    start_workspace_sync,
    sync_history,
)

# Fixed copy for ?sync= outcomes. Never exception text.
SYNC_MESSAGES = {
    "started": "Sync started. This page updates when it finishes.",
    "already_running": "A sync is already running for this workspace.",
    "nothing_to_sync": "Nothing to sync yet: select a repository or a Jira project first.",
}


def get_session_factory() -> Any:
    """Opens a new database session for a background sync. A function, so tests can
    replace it."""
    from app.db import get_sync_session

    return get_sync_session


def _run_out(run: Any) -> dict[str, Any]:
    return {
        "source": run.source,
        "scope": run.scope,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error_type": run.error_type,
        "items_written": run.items_written,
        "items_skipped": run.items_skipped,
    }


workspace_pages = APIRouter(tags=["workspace pages"])


def _github_message(code: str) -> str | None:
    """Fixed copy for a ?github= outcome code. Imported late: connections imports us."""
    from app.web.connections import GITHUB_MESSAGES

    return GITHUB_MESSAGES.get(code)


def _jira_message(code: str) -> str | None:
    """Fixed copy for a ?jira= outcome code. Imported late, as above."""
    from app.web.jira_connections import JIRA_MESSAGES

    return JIRA_MESSAGES.get(code)


def _base_context(request: Request, db: Any, account: Account, nav_current: str) -> dict:
    """What base.html needs. actor is the transitional app_user linked to the account."""
    actor = db.get(AppUser, account.app_user_id) if account.app_user_id else None
    return {
        "request": request,
        "actor": actor or account,
        "account": account,
        "team_name": "",
        "unmatched_count": 0,
        "nav_current": nav_current,
    }


@workspace_pages.get("/workspaces", response_class=HTMLResponse)
def portfolio_home(request: Request, account: AccountDep, db: DbDep) -> HTMLResponse:
    """1 card per workspace the account belongs to. Compares projects, never people."""
    context = _base_context(request, db, account, "workspaces")
    context["workspaces"] = list_workspaces_for(db, account)
    context["github_message"] = _github_message(request.query_params.get("github", ""))
    context["jira_message"] = _jira_message(request.query_params.get("jira", ""))
    return templates.TemplateResponse(request=request, name="workspaces.html", context=context)


@workspace_pages.get("/workspaces/new", response_class=HTMLResponse)
def new_workspace_form(request: Request, account: AccountDep, db: DbDep) -> HTMLResponse:
    """The create workspace form."""
    context = _base_context(request, db, account, "workspaces")
    context.update({"name": "", "description": "", "error": None})
    return templates.TemplateResponse(request=request, name="workspace_new.html", context=context)


@workspace_pages.post("/workspaces")
async def submit_new_workspace(request: Request, account: AccountDep, db: DbDep) -> Response:
    """Create a workspace from the form and open it. The caller becomes the owner."""
    form = parse_qs((await request.body()).decode("utf-8"))
    name = form.get("name", [""])[0]
    description = form.get("description", [""])[0]
    try:
        body = WorkspaceCreate(name=name, description=description)
    except ValidationError:
        context = _base_context(request, db, account, "workspaces")
        context.update(
            {
                "name": name[:100],
                "description": description[:500],
                "error": "Give the workspace a name of 1 to 100 characters. "
                "The description can be up to 500 characters.",
            }
        )
        return templates.TemplateResponse(
            request=request,
            name="workspace_new.html",
            context=context,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    created = create_workspace(db, account, body)
    return RedirectResponse(url=f"/w/{created.workspace_id}", status_code=status.HTTP_303_SEE_OTHER)


@workspace_pages.get("/w/{workspace_id}", response_class=HTMLResponse)
async def workspace_page(request: Request, access: ViewerAccess, db: DbDep) -> HTMLResponse:
    """The workspace: connections, sync, members, analysis and the selected member.

    ?member=<id> selects 1 confirmed person; anything else shows the whole team.
    """
    org_id = ensure_organization(db, access.workspace)
    db.commit()
    team = confirmed_people(db, org_id)
    member_raw = request.query_params.get("member", "")
    selected = next((p for p in team if member_raw.isdecimal() and p.id == int(member_raw)), None)
    analysis = await analyze_workspace(
        db, access.workspace, only_person_id=selected.id if selected else None
    )
    last_seen = mark_seen(db, access.workspace.id, access.account.id)
    context = _base_context(request, db, access.account, "workspaces")
    context.update(
        {
            "workspace": access.workspace,
            "role": access.role,
            "can_manage": access.role in ("owner", "manager"),
            "is_owner": access.role == "owner",
            "my_workspaces": list_workspaces_for(db, access.account),
            "github_accounts": list(
                db.scalars(
                    select(GitHubInstallation.account_login)
                    .where(GitHubInstallation.workspace_id == access.workspace.id)
                    .order_by(GitHubInstallation.account_login)
                ).all()
            ),
            "github_repo_count": db.scalar(
                select(func.count(WorkspaceRepository.id)).where(
                    WorkspaceRepository.workspace_id == access.workspace.id
                )
            )
            or 0,
            "github_message": _github_message(request.query_params.get("github", "")),
            "jira_message": _jira_message(request.query_params.get("jira", "")),
            "jira": db.scalar(
                select(JiraConnection).where(JiraConnection.workspace_id == access.workspace.id)
            ),
            "sync_runs": latest_runs(db, access.workspace.id),
            "team": team,
            "selected": selected,
            "analysis": analysis,
            "feed": feed(db, access.workspace.id, last_seen, selected.id if selected else None),
            "waiting": waiting_count(db, org_id),
            "sync_running": is_running(db, access.workspace.id),
            "now": datetime.now(UTC),
            "has_sources": has_sources(db, access.workspace.id),
            "sync_message": SYNC_MESSAGES.get(request.query_params.get("sync", "")),
            "jira_project_count": db.scalar(
                select(func.count(WorkspaceJiraProject.id)).where(
                    WorkspaceJiraProject.workspace_id == access.workspace.id
                )
            )
            or 0,
        }
    )
    return templates.TemplateResponse(request=request, name="workspace.html", context=context)


@workspace_pages.post("/w/{workspace_id}/sync")
def sync_now(access: ManagerAccess, db: DbDep) -> Response:
    """Start a background sync of the workspace's sources; never wait for it (DEC-002)."""
    workspace_id = access.workspace.id
    if not has_sources(db, workspace_id):
        code = "nothing_to_sync"
    elif is_running(db, workspace_id):
        code = "already_running"
    elif start_workspace_sync(workspace_id, get_session_factory()):
        code = "started"
    else:
        code = "already_running"
    return RedirectResponse(
        url=f"/w/{workspace_id}?sync={code}", status_code=status.HTTP_303_SEE_OTHER
    )


@workspace_pages.get("/api/workspaces/{workspace_id}/sync/status")
def sync_status(access: ViewerAccess, db: DbDep) -> dict[str, Any]:
    """Whether a sync is running, and the latest run per repository and project."""
    workspace_id = access.workspace.id
    return {
        "running": is_running(db, workspace_id),
        "runs": [_run_out(r) for r in latest_runs(db, workspace_id)],
    }


@workspace_pages.get("/w/{workspace_id}/syncs", response_class=HTMLResponse)
def sync_history_page(request: Request, access: ViewerAccess, db: DbDep) -> HTMLResponse:
    """The workspace's last 50 sync runs."""
    context = _base_context(request, db, access.account, "workspaces")
    context.update(
        {
            "workspace": access.workspace,
            "runs": sync_history(db, access.workspace.id),
            "now": datetime.now(UTC),
        }
    )
    return templates.TemplateResponse(request=request, name="sync_history.html", context=context)


@workspace_pages.get("/w/{workspace_id}/members/{person_id}/summary", response_class=HTMLResponse)
async def member_summary_fragment(
    person_id: int, request: Request, access: ViewerAccess, db: DbDep
) -> HTMLResponse:
    """The 2 sentence summary for 1 confirmed person, loaded after the page (W7).

    The only model call. A fragment, so the page never waits for the model.
    """
    org_id = ensure_organization(db, access.workspace)
    if person_id not in {p.id for p in confirmed_people(db, org_id)}:
        raise HTTPException(status_code=404, detail="Not found")
    insight = await member_summary(db, access.workspace, person_id)
    return templates.TemplateResponse(
        request=request,
        name="partials/member_summary.html",
        context={"request": request, "insight": insight},
    )
