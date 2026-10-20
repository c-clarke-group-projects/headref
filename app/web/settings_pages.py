"""Workspace settings and invites (W9, DEC-021).

Every member can open the settings page and see who has access. Only the owner changes
details, invites, roles and membership, or archives the workspace. Invited people accept
or decline from their workspace list; an invite is theirs only when its GitHub id is the
id they signed in with.
"""

from datetime import UTC, datetime
from typing import get_args
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import workspace_admin
from app.schemas.errors import ToolErrorType
from app.web.auth import DbDep
from app.web.ui_format import templates
from app.web.workspace_pages import _base_context
from app.web.workspaces import AccountDep, OwnerAccess, ViewerAccess

settings_pages = APIRouter(tags=["settings"])

SETTINGS_MESSAGES = {
    "saved": "Saved.",
    "invited": "Invite saved. It is waiting for that person to accept from their workspace "
    "list, and expires in 14 days.",
    "revoked": "Invite withdrawn.",
    "role_changed": "Role changed.",
    "removed": "Access removed.",
    "invalid_details": "Give the workspace a name of 1 to 100 characters. The description can "
    "be up to 500 characters.",
    "invalid_login": "That is not a GitHub username.",
    "no_such_user": "GitHub has no account with that username. Nothing was saved.",
    "not_a_user": "That username belongs to a GitHub organization, not a person.",
    "already_member": "That GitHub account already has access to this workspace.",
    "invalid_role": "Choose manager or viewer.",
    "owner_fixed": "The owner's access cannot be changed or removed.",
    "not_found": "That member or invite is not in this workspace.",
    "confirm_mismatch": "The name you typed does not match. The workspace was not archived.",
}
_GITHUB_ERRORS = set(get_args(ToolErrorType))


def settings_message(code: str, error: str) -> str | None:
    """Fixed copy for a settings result code; never echoes the query string."""
    if code == "github_unavailable":
        reason = error if error in _GITHUB_ERRORS else "UPSTREAM_ERROR"
        return f"GitHub could not be reached to check that username ({reason}). Nothing was saved."
    return SETTINGS_MESSAGES.get(code)


def _back(workspace_id: int, code: str, error: str | None = None) -> RedirectResponse:
    query = {"settings": code} | ({"error": error} if error else {})
    return RedirectResponse(
        url=f"/w/{workspace_id}/settings?{urlencode(query)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _form(request: Request) -> dict[str, list[str]]:
    return parse_qs((await request.body()).decode("utf-8"))


def _field(form: dict[str, list[str]], name: str) -> str:
    return form.get(name, [""])[0]


@settings_pages.get("/w/{workspace_id}/settings", response_class=HTMLResponse)
def settings_page(request: Request, access: ViewerAccess, db: DbDep) -> HTMLResponse:
    """Details, who has access, invites and archive."""
    is_owner = access.role == "owner"
    now = datetime.now(UTC)
    invites = workspace_admin.list_invites(db, access.workspace.id) if is_owner else []
    context = _base_context(request, db, access.account, "workspaces")
    context.update(
        {
            "workspace": access.workspace,
            "role": access.role,
            "is_owner": is_owner,
            "members": workspace_admin.list_members(db, access.workspace.id),
            "invites": [(inv, workspace_admin.is_expired(inv, now)) for inv in invites],
            "invite_roles": workspace_admin.INVITABLE_ROLES,
            "message": settings_message(
                request.query_params.get("settings", ""), request.query_params.get("error", "")
            ),
        }
    )
    return templates.TemplateResponse(
        request=request, name="workspace_settings.html", context=context
    )


@settings_pages.post("/w/{workspace_id}/settings/details")
async def save_details(request: Request, access: OwnerAccess, db: DbDep) -> Response:
    form = await _form(request)
    try:
        workspace_admin.update_details(
            db, access.workspace, _field(form, "name"), _field(form, "description")
        )
    except workspace_admin.AdminError as exc:
        return _back(access.workspace.id, exc.code)
    return _back(access.workspace.id, "saved")


@settings_pages.post("/w/{workspace_id}/settings/invites")
async def make_invite(request: Request, access: OwnerAccess, db: DbDep) -> Response:
    """Look the login up on GitHub and save the invite by GitHub id."""
    form = await _form(request)
    try:
        await run_in_threadpool(
            workspace_admin.invite,
            db,
            access.workspace,
            access.account,
            _field(form, "login"),
            _field(form, "role"),
        )
    except workspace_admin.AdminError as exc:
        return _back(access.workspace.id, exc.code, exc.detail)
    return _back(access.workspace.id, "invited")


@settings_pages.post("/w/{workspace_id}/settings/invites/{invite_id}/revoke")
def revoke_invite(invite_id: int, access: OwnerAccess, db: DbDep) -> Response:
    try:
        workspace_admin.revoke_invite(db, access.workspace.id, invite_id)
    except workspace_admin.AdminError as exc:
        return _back(access.workspace.id, exc.code)
    return _back(access.workspace.id, "revoked")


@settings_pages.post("/w/{workspace_id}/settings/members/{membership_id}/role")
async def change_role(
    membership_id: int, request: Request, access: OwnerAccess, db: DbDep
) -> Response:
    role = _field(await _form(request), "role")
    try:
        workspace_admin.change_role(db, access.workspace.id, membership_id, role)
    except workspace_admin.AdminError as exc:
        return _back(access.workspace.id, exc.code)
    return _back(access.workspace.id, "role_changed")


@settings_pages.post("/w/{workspace_id}/settings/members/{membership_id}/remove")
def remove_member(membership_id: int, access: OwnerAccess, db: DbDep) -> Response:
    try:
        workspace_admin.remove_member(db, access.workspace.id, membership_id)
    except workspace_admin.AdminError as exc:
        return _back(access.workspace.id, exc.code)
    return _back(access.workspace.id, "removed")


@settings_pages.post("/w/{workspace_id}/settings/archive")
async def archive_workspace(request: Request, access: OwnerAccess, db: DbDep) -> Response:
    """Archive after the owner types the workspace name."""
    confirmation = _field(await _form(request), "confirm_name")
    try:
        workspace_admin.archive(db, access.workspace, confirmation, access.account.id)
    except workspace_admin.AdminError as exc:
        return _back(access.workspace.id, exc.code)
    return RedirectResponse(
        url="/workspaces?invite=archived", status_code=status.HTTP_303_SEE_OTHER
    )


def _invite_result(code: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/workspaces?{urlencode({'invite': code})}", status_code=status.HTTP_303_SEE_OTHER
    )


@settings_pages.post("/invites/{invite_id}/accept")
def accept_invite(invite_id: int, account: AccountDep, db: DbDep) -> Response:
    """Join the workspace the invite is for, if the invite is this account's."""
    try:
        workspace_id = workspace_admin.accept_invite(db, account, invite_id)
    except workspace_admin.AdminError as exc:
        return _invite_result(exc.code)
    return RedirectResponse(url=f"/w/{workspace_id}", status_code=status.HTTP_303_SEE_OTHER)


@settings_pages.post("/invites/{invite_id}/decline")
def decline_invite(invite_id: int, account: AccountDep, db: DbDep) -> Response:
    try:
        workspace_admin.decline_invite(db, account, invite_id)
    except workspace_admin.AdminError as exc:
        return _invite_result(exc.code)
    return _invite_result("declined")
