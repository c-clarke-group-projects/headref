"""Connect a workspace to GitHub through the headref GitHub App (W3, DEC-022).

Flow:
1. An owner presses Connect GitHub. POST /w/{id}/connections/github/start signs a state
   value naming the workspace and the account, keeps it in a cookie, and sends the
   browser to the App's install page.
2. GitHub sends the browser back to GET /connections/github/setup with the installation
   id and the state. The state must match the cookie, be signed, be fresh and name the
   signed-in account, and the account must still own the workspace. An installation on
   the signed-in user's own GitHub account is then attached.
2a. An installation on an organisation needs proof that the signed-in user administers
   it. Sign-in asks GitHub for no scopes (DEC-026), so the owner is sent once through
   GitHub's approval for read:org, back to GET /auth/github/callback/org with a second
   signed state naming the workspace, the account and the installation. The token is
   used for the admin check and then dropped; it is never stored.
3. The owner picks repositories. The server re-reads the installation's repositories and
   accepts only ids that are really in it.

Routes with {workspace_id} use workspace_access. The setup callback cannot carry the
workspace in its path, because GitHub chooses that URL, so it checks ownership itself.
"""

import logging
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete, select

from app.config import get_settings
from app.integrations.errors import IntegrationError, NotFoundError
from app.integrations.github_app import (
    GitHubAppClient,
    Installation,
    exchange_oauth_code,
    user_can_attach,
)
from app.models.connections import GitHubInstallation, WorkspaceRepository
from app.models.workspace import Workspace, WorkspaceMember
from app.web.auth import DbDep, cookie_secure, read_signed_value, sign_value
from app.web.ui_format import templates
from app.web.workspace_pages import _base_context
from app.web.workspaces import AccountDep, OwnerAccess

logger = logging.getLogger(__name__)

INSTALL_STATE_COOKIE = "headref_gh_install_state"
INSTALL_STATE_MAX_AGE_SECONDS = 15 * 60
ORG_CHECK_COOKIE = "headref_gh_org_check"
# The only scope the organisation check asks for: membership and role in an org.
ORG_CHECK_SCOPE = "read:org"

# Fixed copy for every outcome. Pages show these, never exception text.
GITHUB_MESSAGES = {
    "connected": "GitHub is connected. Choose the repositories this workspace should use.",
    "saved": "Repository selection saved.",
    "disconnected": "GitHub was disconnected from this workspace. To remove the App's "
    "access entirely, uninstall it in your GitHub settings under Applications.",
    "not_configured": "The headref GitHub App is not configured on this server.",
    "bad_state": "That GitHub connection could not be verified. Start again from the "
    "workspace page.",
    "not_owner": "Only the workspace owner can connect GitHub.",
    "not_found": "GitHub did not recognise that installation of the headref App.",
    "not_allowed": "That installation belongs to an account or organisation you do not "
    "administer, so it cannot be connected.",
    "org_check_failed": "GitHub did not confirm that you administer that organisation, "
    "so it was not connected.",
    "org_check_unavailable": "Connecting an organisation needs GitHub sign-in to be "
    "configured on this server.",
    "upstream": "GitHub could not be reached. Nothing was changed; try again shortly.",
    "invalid_selection": "1 or more selected repositories are not in this installation. "
    "Nothing was saved.",
}

connections_pages = APIRouter(tags=["connections"])


def get_app_client() -> GitHubAppClient:
    """The GitHub App client. A function, so tests can replace it."""
    return GitHubAppClient()


def _to_workspace(workspace_id: int, code: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/w/{workspace_id}?{urlencode({'github': code})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _to_picker(workspace_id: int, code: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/w/{workspace_id}/connections/github?{urlencode({'github': code})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@connections_pages.post("/w/{workspace_id}/connections/github/start")
def start_github_install(access: OwnerAccess) -> Response:
    """Send the owner to the App's install page with a signed, single-use state."""
    settings = get_settings()
    if not settings.github_app_enabled:
        return _to_workspace(access.workspace.id, "not_configured")
    state = make_connection_state(access.workspace.id, access.account.id)
    url = f"https://github.com/apps/{settings.github_app_slug}/installations/new?" + urlencode(
        {"state": state}
    )
    response = RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        INSTALL_STATE_COOKIE,
        state,
        max_age=INSTALL_STATE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
    )
    return response


def make_connection_state(workspace_id: int, account_id: int) -> str:
    """A signed, single-flow state naming the workspace and account. Shared with Jira."""
    payload = ".".join(
        [str(workspace_id), str(account_id), str(int(time.time())), secrets.token_urlsafe(16)]
    )
    return sign_value(payload)


def workspace_from_state(state: str | None, cookie: str | None, account_id: int) -> int | None:
    """The workspace id in a valid state, or None. Valid means all checks pass:
    matches the cookie, signed by this server, made for this account, under 15 minutes."""
    if not state or not cookie or not secrets.compare_digest(state, cookie):
        return None
    payload = read_signed_value(state)
    if payload is None:
        return None
    parts = payload.split(".")
    if len(parts) != 4 or not (parts[0].isdecimal() and parts[1].isdecimal()):
        return None
    workspace_id, state_account_id, issued = int(parts[0]), int(parts[1]), parts[2]
    if state_account_id != account_id or not issued.isdecimal():
        return None
    if time.time() - int(issued) > INSTALL_STATE_MAX_AGE_SECONDS:
        return None
    return workspace_id


def owns_workspace(db: Any, workspace_id: int, account_id: int) -> bool:
    """True if the account owns the workspace and it is not archived. For callbacks,
    whose path cannot carry {workspace_id}; every other route uses workspace_access."""
    return (
        db.scalar(
            select(WorkspaceMember.id)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.account_id == account_id,
                WorkspaceMember.role == "owner",
                Workspace.archived_at.is_(None),
            )
        )
        is not None
    )


@connections_pages.get("/connections/github/setup")
def github_setup_callback(request: Request, account: AccountDep, db: DbDep) -> Response:
    """Attach a new installation to the workspace named in the verified state.

    The state cookie is deleted whatever the outcome, so a state is used at most once.
    """
    response = _github_setup(request, account, db)
    response.delete_cookie(INSTALL_STATE_COOKIE)
    return response


def _github_setup(request: Request, account: Any, db: Any) -> Response:
    params = request.query_params
    setup_action = params.get("setup_action", "")
    installation_raw = params.get("installation_id", "")

    if setup_action == "update" and not params.get("state"):
        # GitHub sends repository changes here without our state. Attach nothing; take
        # the account to a workspace that already uses this installation, if any.
        row = db.execute(
            select(GitHubInstallation.workspace_id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == GitHubInstallation.workspace_id)
            .where(
                GitHubInstallation.installation_id
                == (int(installation_raw) if installation_raw.isdecimal() else -1),
                WorkspaceMember.account_id == account.id,
            )
        ).first()
        if row is None:
            return RedirectResponse(url="/workspaces", status_code=status.HTTP_303_SEE_OTHER)
        return _to_picker(row[0], "saved")

    workspace_id = workspace_from_state(
        params.get("state"), request.cookies.get(INSTALL_STATE_COOKIE), account.id
    )
    if workspace_id is None or not installation_raw.isdecimal():
        logger.warning("github setup rejected: bad state, account_id=%s", account.id)
        return RedirectResponse(
            url="/workspaces?" + urlencode({"github": "bad_state"}),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if not owns_workspace(db, workspace_id, account.id):
        return RedirectResponse(
            url="/workspaces?" + urlencode({"github": "not_owner"}),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    try:
        installation = get_app_client().get_installation(int(installation_raw))
    except NotFoundError:
        return _to_workspace(workspace_id, "not_found")
    except IntegrationError as exc:
        logger.warning("github setup upstream failure: %s", type(exc).__name__)
        return _to_workspace(workspace_id, "upstream")
    if installation.account_type == "Organization":
        return _start_org_check(request, workspace_id, account.id, installation)
    if not user_can_attach(installation, None, account.github_user_id):
        return _refused(workspace_id, account.id, installation)
    return _attach(db, workspace_id, account.id, installation)


def _refused(workspace_id: int, account_id: int, installation: Installation) -> Response:
    logger.warning(
        "github setup refused: account_id=%s installation_id=%s owner=%s",
        account_id,
        installation.installation_id,
        installation.account_login,
    )
    return _to_workspace(workspace_id, "not_allowed")


def _attach(db: Any, workspace_id: int, account_id: int, installation: Installation) -> Response:
    """Attach a verified installation to the workspace and open the repository picker."""
    existing = db.scalar(
        select(GitHubInstallation).where(
            GitHubInstallation.workspace_id == workspace_id,
            GitHubInstallation.installation_id == installation.installation_id,
        )
    )
    if existing is None:
        db.add(
            GitHubInstallation(
                workspace_id=workspace_id,
                installation_id=installation.installation_id,
                account_login=installation.account_login,
                account_type=installation.account_type,
                connected_by_account_id=account_id,
            )
        )
    else:
        existing.account_login = installation.account_login
    db.commit()
    logger.info(
        "github connected: workspace_id=%s installation_id=%s",
        workspace_id,
        installation.installation_id,
    )
    return _to_picker(workspace_id, "connected")


# ---------------------------------------------------------------------------
# Organisation installations: a 1-time read:org approval (DEC-026)
# ---------------------------------------------------------------------------


def _start_org_check(
    request: Request, workspace_id: int, account_id: int, installation: Installation
) -> Response:
    """Send the owner to GitHub to approve read:org once, for the admin check."""
    settings = get_settings()
    if not settings.github_oauth_enabled:
        return _to_workspace(workspace_id, "org_check_unavailable")
    payload = ".".join(
        [
            "org",
            str(workspace_id),
            str(account_id),
            str(installation.installation_id),
            str(int(time.time())),
            secrets.token_urlsafe(16),
        ]
    )
    state = sign_value(payload)
    url = "https://github.com/login/oauth/authorize?" + urlencode(
        {
            "client_id": settings.github_oauth_client_id,
            "redirect_uri": str(request.url_for("github_org_check")),
            "scope": ORG_CHECK_SCOPE,
            "state": state,
        }
    )
    response = RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        ORG_CHECK_COOKIE,
        state,
        max_age=INSTALL_STATE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
    )
    logger.info(
        "github org check started: workspace_id=%s installation_id=%s",
        workspace_id,
        installation.installation_id,
    )
    return response


def org_check_from_state(
    state: str | None, cookie: str | None, account_id: int
) -> tuple[int, int] | None:
    """(workspace id, installation id) from a valid organisation-check state, or None.

    Valid means: matches the cookie, signed here, made for this account, under 15 minutes.
    """
    if not state or not cookie or not secrets.compare_digest(state, cookie):
        return None
    payload = read_signed_value(state)
    if payload is None:
        return None
    parts = payload.split(".")
    if len(parts) != 6 or parts[0] != "org" or not all(p.isdecimal() for p in parts[1:5]):
        return None
    workspace_id, state_account, installation_id, issued = (int(p) for p in parts[1:5])
    if state_account != account_id or time.time() - issued > INSTALL_STATE_MAX_AGE_SECONDS:
        return None
    return workspace_id, installation_id


@connections_pages.get("/auth/github/callback/org", name="github_org_check")
def github_org_check_callback(request: Request, account: AccountDep, db: DbDep) -> Response:
    """Finish attaching an organisation installation once GitHub confirms the admin role.

    The state cookie is deleted whatever the outcome, so a state is used at most once.
    """
    response = _github_org_check(request, account, db)
    response.delete_cookie(ORG_CHECK_COOKIE)
    return response


def _github_org_check(request: Request, account: Any, db: Any) -> Response:
    params = request.query_params
    found = org_check_from_state(
        params.get("state"), request.cookies.get(ORG_CHECK_COOKIE), account.id
    )
    if found is None:
        logger.warning("github org check rejected: bad state, account_id=%s", account.id)
        return RedirectResponse(
            url="/workspaces?" + urlencode({"github": "bad_state"}),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    workspace_id, installation_id = found
    if not owns_workspace(db, workspace_id, account.id):
        return RedirectResponse(
            url="/workspaces?" + urlencode({"github": "not_owner"}),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    code = params.get("code")
    if params.get("error") or not code:
        # The owner declined the approval on GitHub, or GitHub sent no code.
        return _to_workspace(workspace_id, "org_check_failed")

    settings = get_settings()
    try:
        # The token lives only in this call: used for the admin check, never stored.
        token = exchange_oauth_code(
            code,
            str(request.url_for("github_org_check")),
            settings.github_oauth_client_id,
            settings.github_oauth_client_secret,
        )
        installation = get_app_client().get_installation(installation_id)
        allowed = user_can_attach(installation, token, account.github_user_id)
    except NotFoundError:
        return _to_workspace(workspace_id, "not_found")
    except IntegrationError as exc:
        logger.warning("github org check failure: %s", type(exc).__name__)
        return _to_workspace(workspace_id, "org_check_failed")
    if not allowed:
        return _refused(workspace_id, account.id, installation)
    return _attach(db, workspace_id, account.id, installation)


def _installations(db: Any, workspace_id: int) -> list[GitHubInstallation]:
    return list(
        db.scalars(
            select(GitHubInstallation)
            .where(GitHubInstallation.workspace_id == workspace_id)
            .order_by(GitHubInstallation.account_login)
        ).all()
    )


@connections_pages.get("/w/{workspace_id}/connections/github", response_class=HTMLResponse)
def github_repo_picker(request: Request, access: OwnerAccess, db: DbDep) -> HTMLResponse:
    """The owner picks which repositories of each installation the workspace uses."""
    workspace = access.workspace
    selected = set(
        db.scalars(
            select(WorkspaceRepository.github_repo_id).where(
                WorkspaceRepository.workspace_id == workspace.id
            )
        ).all()
    )
    groups: list[dict[str, Any]] = []
    for inst in _installations(db, workspace.id):
        group: dict[str, Any] = {"installation": inst, "repos": [], "error": None}
        try:
            group["repos"] = get_app_client().list_installation_repositories(inst.installation_id)
        except IntegrationError as exc:
            # Rule 9: a failure is shown as a failure, never as "no repositories".
            group["error"] = exc.error_type
        groups.append(group)

    context = _base_context(request, db, access.account, "workspaces")
    context.update(
        {
            "workspace": workspace,
            "groups": groups,
            "selected": selected,
            "message": GITHUB_MESSAGES.get(request.query_params.get("github", "")),
            "app_configured": get_settings().github_app_enabled,
        }
    )
    return templates.TemplateResponse(
        request=request, name="github_connection.html", context=context
    )


@connections_pages.post("/w/{workspace_id}/connections/github/repos")
async def save_github_repos(request: Request, access: OwnerAccess, db: DbDep) -> Response:
    """Replace the workspace's repository selection with the submitted one.

    Each submitted value is "<installation row id>:<GitHub repo id>". The full name is
    taken from GitHub, never from the form, and an id not in the installation rejects
    the whole submission.
    """
    workspace = access.workspace
    form = parse_qs((await request.body()).decode("utf-8"))
    wanted: dict[int, set[int]] = {}
    seen_repo_ids: set[int] = set()
    for value in form.get("repo", []):
        inst_part, _, repo_part = value.partition(":")
        if not (inst_part.isdecimal() and repo_part.isdecimal()):
            return _to_picker(workspace.id, "invalid_selection")
        if int(repo_part) in seen_repo_ids:
            # The same repository through 2 installations: keep 1, never both.
            continue
        seen_repo_ids.add(int(repo_part))
        wanted.setdefault(int(inst_part), set()).add(int(repo_part))

    installations = {inst.id: inst for inst in _installations(db, workspace.id)}
    if not set(wanted) <= set(installations):
        return _to_picker(workspace.id, "invalid_selection")

    rows: list[WorkspaceRepository] = []
    for inst_row_id, repo_ids in wanted.items():
        inst = installations[inst_row_id]
        try:
            available = {
                r["id"]: r["full_name"]
                for r in get_app_client().list_installation_repositories(inst.installation_id)
            }
        except IntegrationError:
            return _to_picker(workspace.id, "upstream")
        if not repo_ids <= set(available):
            return _to_picker(workspace.id, "invalid_selection")
        rows.extend(
            WorkspaceRepository(
                workspace_id=workspace.id,
                github_installation_id=inst.id,
                github_repo_id=repo_id,
                full_name=available[repo_id],
                added_by_account_id=access.account.id,
            )
            for repo_id in sorted(repo_ids)
        )

    db.execute(delete(WorkspaceRepository).where(WorkspaceRepository.workspace_id == workspace.id))
    db.add_all(rows)
    db.commit()
    logger.info("github repositories saved: workspace_id=%s count=%s", workspace.id, len(rows))
    return _to_picker(workspace.id, "saved")


@connections_pages.post("/w/{workspace_id}/connections/github/{installation_row_id}/disconnect")
def disconnect_github(installation_row_id: int, access: OwnerAccess, db: DbDep) -> Response:
    """Detach 1 installation, and its selected repositories, from this workspace."""
    deleted = db.execute(
        delete(GitHubInstallation).where(
            GitHubInstallation.id == installation_row_id,
            GitHubInstallation.workspace_id == access.workspace.id,
        )
    ).rowcount
    db.execute(
        delete(WorkspaceRepository).where(
            WorkspaceRepository.github_installation_id == installation_row_id,
            WorkspaceRepository.workspace_id == access.workspace.id,
        )
    )
    db.commit()
    if not deleted:
        return _to_workspace(access.workspace.id, "not_found")
    logger.info(
        "github disconnected: workspace_id=%s installation_row_id=%s",
        access.workspace.id,
        installation_row_id,
    )
    return _to_workspace(access.workspace.id, "disconnected")
