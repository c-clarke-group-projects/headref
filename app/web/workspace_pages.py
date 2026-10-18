"""Workspace pages: portfolio home, create workspace, workspace shell (W2, DEC-021).

Every page under /w/{workspace_id} takes a workspace_access dependency from
app.web.workspaces; the route coverage test in tests/test_workspaces.py fails if one
does not. Connections, sync, analysis and the feed fill the shell in W3 to W8; until
then each section says plainly that it is not set up, never shows invented data.
"""

from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.canonical import AppUser
from app.models.connections import GitHubInstallation, WorkspaceRepository
from app.models.workspace import Account
from app.schemas.workspace import WorkspaceCreate
from app.web.auth import DbDep
from app.web.ui_format import templates
from app.web.workspaces import (
    AccountDep,
    ViewerAccess,
    create_workspace,
    list_workspaces_for,
)

workspace_pages = APIRouter(tags=["workspace pages"])


def _github_message(code: str) -> str | None:
    """Fixed copy for a ?github= outcome code. Imported late: connections imports us."""
    from app.web.connections import GITHUB_MESSAGES

    return GITHUB_MESSAGES.get(code)


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
def workspace_page(request: Request, access: ViewerAccess, db: DbDep) -> HTMLResponse:
    """The workspace shell: connections, sync, members, analysis and feed sections."""
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
        }
    )
    return templates.TemplateResponse(request=request, name="workspace.html", context=context)
