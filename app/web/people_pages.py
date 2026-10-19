"""The People screen of a workspace (W6, DEC-024).

Any member can see who is suggested, confirmed, ignored or a bot. Managers and owners
confirm, ignore, mark bots, merge and split. Every id is checked against the workspace's
organization in app/people.py.
"""

from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import people
from app.web.auth import DbDep
from app.web.ui_format import templates
from app.web.workspace_pages import _base_context
from app.web.workspaces import ManagerAccess, ViewerAccess
from app.workspace_sync import ensure_organization

people_pages = APIRouter(tags=["people"])

PEOPLE_MESSAGES = {
    "confirmed": "Confirmed. This person now appears in the workspace analysis.",
    "ignored": "Ignored. This person stays out of the workspace analysis.",
    "bot": "Marked as a bot. Bots stay out of the workspace analysis.",
    "suggested": "Moved back to waiting for confirmation.",
    "merged": "Merged. The accounts and their activity now belong to 1 person.",
    "split": "Split. The account and its activity are now a separate person.",
    "not_found": "That person or account is not in this workspace.",
    "same_person": "Choose 2 different people to merge.",
    "only_account": "This is the person's only account, so there is nothing to split off.",
    "ambiguous_split": "This person has more than 1 account of that kind, so headref cannot "
    "tell which activity belongs to which. Nothing was changed.",
    "invalid": "That action is not available.",
}


def _back(workspace_id: int, code: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/w/{workspace_id}/people?{urlencode({'people': code})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


async def _form(request: Request) -> dict[str, list[str]]:
    return parse_qs((await request.body()).decode("utf-8"))


def _int(form: dict[str, list[str]], name: str) -> int | None:
    value = form.get(name, [""])[0]
    return int(value) if value.isdecimal() else None


def _org(db: Any, access: Any) -> int:
    org_id = ensure_organization(db, access.workspace)
    db.commit()
    return org_id


@people_pages.get("/w/{workspace_id}/people", response_class=HTMLResponse)
def people_page(request: Request, access: ViewerAccess, db: DbDep) -> HTMLResponse:
    """Who headref found in this workspace's sources, and what a manager decided."""
    grouped = people.list_people(db, _org(db, access))
    context = _base_context(request, db, access.account, "workspaces")
    context.update(
        {
            "workspace": access.workspace,
            "groups": grouped,
            "everyone": [p for group in grouped.values() for p in group],
            "can_manage": access.role in ("owner", "manager"),
            "message": PEOPLE_MESSAGES.get(request.query_params.get("people", "")),
        }
    )
    return templates.TemplateResponse(request=request, name="people.html", context=context)


@people_pages.post("/w/{workspace_id}/people/{person_id}/status")
async def change_status(
    person_id: int, request: Request, access: ManagerAccess, db: DbDep
) -> Response:
    """Confirm, ignore, mark as bot, or return to suggested."""
    new_status = (await _form(request)).get("status", [""])[0]
    try:
        people.set_status(db, _org(db, access), person_id, new_status)
    except people.PeopleError as exc:
        return _back(access.workspace.id, exc.code)
    return _back(access.workspace.id, new_status)


@people_pages.post("/w/{workspace_id}/people/merge")
async def merge_people(request: Request, access: ManagerAccess, db: DbDep) -> Response:
    """Merge the source person into the target person."""
    form = await _form(request)
    source_id, target_id = _int(form, "source"), _int(form, "target")
    if source_id is None or target_id is None:
        return _back(access.workspace.id, "invalid")
    try:
        people.merge(db, _org(db, access), source_id, target_id)
    except people.PeopleError as exc:
        return _back(access.workspace.id, exc.code)
    return _back(access.workspace.id, "merged")


@people_pages.post("/w/{workspace_id}/people/{person_id}/split")
async def split_person(
    person_id: int, request: Request, access: ManagerAccess, db: DbDep
) -> Response:
    """Move 1 account of the person to a new suggested person."""
    link_id = _int(await _form(request), "link")
    if link_id is None:
        return _back(access.workspace.id, "invalid")
    try:
        people.split(db, _org(db, access), person_id, link_id)
    except people.PeopleError as exc:
        return _back(access.workspace.id, exc.code)
    return _back(access.workspace.id, "split")
