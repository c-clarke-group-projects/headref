"""People in a workspace: list, confirm, ignore, mark as bot, merge, split (W6, DEC-024).

A workspace sync creates a suggested person per external account it meets, holding
exactly that account, so attribution is exact. A manager decides what is a judgement:
whether the person belongs on the team (confirm, ignore, bot) and whether 2 accounts are
the same human (merge). Nothing here matches by name.

Every function takes the workspace's organization id and touches only people and
links in it, so an id from another workspace changes nothing.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.canonical import AppUser
from app.models.identity import IdentityLink
from app.models.work import Commit, PullRequest, Review, WorkItem

logger = logging.getLogger("headref.people")

STATUSES = ("suggested", "confirmed", "ignored", "bot")

# Which rows each integration's account is the author or assignee of.
_ROWS_BY_INTEGRATION = {
    "jira": [(WorkItem, WorkItem.assignee_app_user_id)],
    "github": [
        (PullRequest, PullRequest.author_app_user_id),
        (Commit, Commit.author_app_user_id),
        (Review, Review.reviewer_app_user_id),
    ],
}


class PeopleError(ValueError):
    """An action that cannot be applied. code is a fixed message key for the page."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Account:
    link_id: int
    integration: str
    handle: str
    external_id: str


@dataclass
class Person:
    """A person with their accounts and their own activity counts (context only)."""

    id: int
    display_name: str
    status: str
    accounts: list[Account] = field(default_factory=list)
    pull_requests: int = 0
    commits: int = 0
    work_items: int = 0


def _person(db: Session, org_id: int, person_id: int) -> AppUser:
    person = db.get(AppUser, person_id)
    if person is None or person.organization_id != org_id:
        raise PeopleError("not_found")
    return person


def _counts(db: Session, column: object, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    rows = db.execute(
        select(column, func.count()).where(column.in_(ids)).group_by(column)  # type: ignore[attr-defined]
    ).all()
    return {person_id: count for person_id, count in rows}


def list_people(db: Session, org_id: int) -> dict[str, list[Person]]:
    """People of the organization grouped by status, each group alphabetical."""
    users = db.scalars(
        select(AppUser)
        .where(AppUser.organization_id == org_id)
        .order_by(func.lower(AppUser.display_name), AppUser.id)
    ).all()
    ids = [u.id for u in users]
    links = (
        db.scalars(
            select(IdentityLink)
            .where(IdentityLink.app_user_id.in_(ids))
            .order_by(IdentityLink.integration, IdentityLink.external_handle)
        ).all()
        if ids
        else []
    )
    prs = _counts(db, PullRequest.author_app_user_id, ids)
    commits = _counts(db, Commit.author_app_user_id, ids)
    items = _counts(db, WorkItem.assignee_app_user_id, ids)

    grouped: dict[str, list[Person]] = {status: [] for status in STATUSES}
    by_id: dict[int, Person] = {}
    for user in users:
        person = Person(
            id=user.id,
            display_name=user.display_name,
            status=user.status,
            pull_requests=prs.get(user.id, 0),
            commits=commits.get(user.id, 0),
            work_items=items.get(user.id, 0),
        )
        by_id[user.id] = person
        grouped.setdefault(user.status, []).append(person)
    for link in links:
        by_id[link.app_user_id].accounts.append(
            Account(link.id, link.integration, link.external_handle, link.external_id)
        )
    return grouped


def set_status(db: Session, org_id: int, person_id: int, status: str) -> None:
    """Confirm, ignore, mark as bot, or return a person to suggested."""
    if status not in STATUSES:
        raise PeopleError("invalid")
    person = _person(db, org_id, person_id)
    person.status = status
    db.commit()
    logger.info("person status: org=%s person=%s status=%s", org_id, person_id, status)


def merge(db: Session, org_id: int, source_id: int, target_id: int) -> None:
    """The source's accounts and activity become the target's; the source is removed.

    Use when 2 accounts are the same human, typically a Jira account and a GitHub
    account. The target becomes confirmed: a manager has just vouched for it.
    """
    if source_id == target_id:
        raise PeopleError("same_person")
    source = _person(db, org_id, source_id)
    target = _person(db, org_id, target_id)
    db.execute(
        update(IdentityLink)
        .where(IdentityLink.app_user_id == source.id)
        .values(app_user_id=target.id)
    )
    for row_sets in _ROWS_BY_INTEGRATION.values():
        for model, column in row_sets:
            db.execute(update(model).where(column == source.id).values({column: target.id}))
    target.status = "confirmed"
    db.delete(source)
    db.commit()
    logger.info("people merged: org=%s source=%s target=%s", org_id, source_id, target_id)


def split(db: Session, org_id: int, person_id: int, link_id: int) -> int:
    """Move 1 account to a new suggested person, with the activity of that account.

    Rows record the person, not the account, so activity can only move when this is
    the person's only account of that integration; otherwise the split is refused.
    """
    person = _person(db, org_id, person_id)
    link = db.get(IdentityLink, link_id)
    if link is None or link.app_user_id != person.id:
        raise PeopleError("not_found")
    links = db.scalars(select(IdentityLink).where(IdentityLink.app_user_id == person.id)).all()
    if len(links) < 2:
        raise PeopleError("only_account")
    if sum(1 for other in links if other.integration == link.integration) > 1:
        raise PeopleError("ambiguous_split")

    new_person = AppUser(
        organization_id=org_id,
        display_name=link.external_handle,
        role_label=person.role_label,
        is_active=True,
        status="suggested",
    )
    db.add(new_person)
    db.flush()
    link.app_user_id = new_person.id
    for model, column in _ROWS_BY_INTEGRATION.get(link.integration, []):
        db.execute(update(model).where(column == person.id).values({column: new_person.id}))
    db.commit()
    logger.info(
        "person split: org=%s person=%s link=%s new_person=%s",
        org_id,
        person_id,
        link_id,
        new_person.id,
    )
    return new_person.id


def waiting_count(db: Session, org_id: int) -> int:
    """How many people are suggested and waiting for a manager."""
    return int(
        db.scalar(
            select(func.count(AppUser.id)).where(
                AppUser.organization_id == org_id, AppUser.status == "suggested"
            )
        )
        or 0
    )


def confirmed_people(db: Session, org_id: int) -> list[AppUser]:
    """Confirmed people, alphabetical. Never ranked (AI_BEHAVIOR 5.11)."""
    return list(
        db.scalars(
            select(AppUser)
            .where(AppUser.organization_id == org_id, AppUser.status == "confirmed")
            .order_by(func.lower(AppUser.display_name), AppUser.id)
        ).all()
    )


__all__ = [
    "STATUSES",
    "PeopleError",
    "Person",
    "confirmed_people",
    "list_people",
    "merge",
    "set_status",
    "split",
    "waiting_count",
]
