"""Workspace settings: details, members, invites and archive (W9, DEC-021).

Only the owner changes anything here; the routes enforce that with OwnerAccess. Every id
taken from a form is checked against the workspace it is used in, so an id from another
workspace is "not_found", never acted on.

Invites are made for a GitHub login, which is looked up once and stored as GitHub's
numeric account id. Accepting matches that id against the signed-in account, so renaming
a login, or taking over a login someone gave up, does not carry an invite with it. The
invited person accepts or declines on their workspace list.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.errors import IntegrationError, NotFoundError
from app.integrations.github import lookup_github_user
from app.models.workspace import ROLE_RANK, Account, Workspace, WorkspaceInvite, WorkspaceMember
from app.schemas.workspace import WorkspaceCreate

logger = logging.getLogger("headref.workspace_admin")

INVITE_LIFETIME = timedelta(days=14)
INVITABLE_ROLES = ("manager", "viewer")


class AdminError(Exception):
    """A settings action that was refused. code selects the message shown."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass
class MemberRow:
    """1 account with access to the workspace."""

    member_id: int
    account_id: int
    github_login: str
    display_name: str
    role: str


def _aware(moment: datetime) -> datetime:
    """SQLite returns naive datetimes; every stored time is UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def is_expired(invite: WorkspaceInvite, now: datetime) -> bool:
    return _aware(invite.expires_at) <= now


# ---------------------------------------------------------------------------
# Details and archive
# ---------------------------------------------------------------------------


def update_details(db: Session, workspace: Workspace, name: str, description: str) -> None:
    """Rename the workspace or change its description."""
    try:
        body = WorkspaceCreate(name=name, description=description)
    except ValidationError as exc:
        raise AdminError("invalid_details") from exc
    workspace.name = body.name
    workspace.description = body.description
    db.commit()
    logger.info("workspace details changed: workspace_id=%s", workspace.id)


def archive(db: Session, workspace: Workspace, confirmation: str, account_id: int) -> None:
    """Archive the workspace once its name is typed. It closes for everyone and stops syncing.

    The data stays; archiving is undone in the database, not in the app.
    """
    if confirmation.strip() != workspace.name:
        raise AdminError("confirm_mismatch")
    workspace.archived_at = datetime.now(UTC)
    db.commit()
    logger.info("workspace archived: workspace_id=%s account_id=%s", workspace.id, account_id)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


def list_members(db: Session, workspace_id: int) -> list[MemberRow]:
    """Everyone with access: the owner first, then managers, then viewers, by login."""
    rows = db.execute(
        select(WorkspaceMember, Account)
        .join(Account, Account.id == WorkspaceMember.account_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
    ).all()
    members = [
        MemberRow(
            member_id=member.id,
            account_id=account.id,
            github_login=account.github_login,
            display_name=account.display_name,
            role=member.role,
        )
        for member, account in rows
    ]
    return sorted(members, key=lambda m: (-ROLE_RANK[m.role], m.github_login.lower()))


def _member(db: Session, workspace_id: int, member_id: int) -> WorkspaceMember:
    member = db.get(WorkspaceMember, member_id)
    if member is None or member.workspace_id != workspace_id:
        raise AdminError("not_found")
    if member.role == "owner":
        raise AdminError("owner_fixed")
    return member


def change_role(db: Session, workspace_id: int, member_id: int, role: str) -> None:
    """Make a manager a viewer or a viewer a manager. The owner's role does not change."""
    if role not in INVITABLE_ROLES:
        raise AdminError("invalid_role")
    member = _member(db, workspace_id, member_id)
    member.role = role
    db.commit()
    logger.info(
        "member role changed: workspace_id=%s member_id=%s role=%s", workspace_id, member_id, role
    )


def remove_member(db: Session, workspace_id: int, member_id: int) -> None:
    """Take away an account's access. The owner cannot be removed."""
    member = _member(db, workspace_id, member_id)
    db.delete(member)
    db.commit()
    logger.info("member removed: workspace_id=%s member_id=%s", workspace_id, member_id)


# ---------------------------------------------------------------------------
# Invites, owner side
# ---------------------------------------------------------------------------


def invite(
    db: Session,
    workspace: Workspace,
    inviter: Account,
    login: str,
    role: str,
    lookup: Callable[[str], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> WorkspaceInvite:
    """Invite a GitHub account, or renew its pending invite with the new role."""
    moment = now or datetime.now(UTC)
    if role not in INVITABLE_ROLES:
        raise AdminError("invalid_role")
    login = login.strip().removeprefix("@")
    try:
        found = (lookup or lookup_github_user)(login)
    except ValueError as exc:
        raise AdminError("invalid_login") from exc
    except NotFoundError as exc:
        raise AdminError("no_such_user") from exc
    except IntegrationError as exc:
        raise AdminError("github_unavailable", exc.error_type) from exc
    if found["type"] != "User":
        raise AdminError("not_a_user")

    already = db.scalar(
        select(WorkspaceMember.id)
        .join(Account, Account.id == WorkspaceMember.account_id)
        .where(WorkspaceMember.workspace_id == workspace.id, Account.github_user_id == found["id"])
    )
    if already is not None:
        raise AdminError("already_member")

    pending = db.scalar(
        select(WorkspaceInvite).where(
            WorkspaceInvite.workspace_id == workspace.id,
            WorkspaceInvite.github_user_id == found["id"],
        )
    )
    fields = {
        "github_login": found["login"],
        "role": role,
        "invited_by_account_id": inviter.id,
        "created_at": moment,
        "expires_at": moment + INVITE_LIFETIME,
    }
    if pending is None:
        pending = WorkspaceInvite(workspace_id=workspace.id, github_user_id=found["id"], **fields)
        db.add(pending)
    else:
        for name, value in fields.items():
            setattr(pending, name, value)
    db.commit()
    logger.info(
        "invite made: workspace_id=%s invite_id=%s github_user_id=%s role=%s",
        workspace.id,
        pending.id,
        found["id"],
        role,
    )
    return pending


def list_invites(db: Session, workspace_id: int) -> list[WorkspaceInvite]:
    """Invites not yet accepted or declined, newest first. Expired ones included."""
    return list(
        db.scalars(
            select(WorkspaceInvite)
            .where(WorkspaceInvite.workspace_id == workspace_id)
            .order_by(WorkspaceInvite.created_at.desc(), WorkspaceInvite.id.desc())
        ).all()
    )


def revoke_invite(db: Session, workspace_id: int, invite_id: int) -> None:
    """Withdraw an invite before it is accepted."""
    pending = db.get(WorkspaceInvite, invite_id)
    if pending is None or pending.workspace_id != workspace_id:
        raise AdminError("not_found")
    db.delete(pending)
    db.commit()
    logger.info("invite revoked: workspace_id=%s invite_id=%s", workspace_id, invite_id)


# ---------------------------------------------------------------------------
# Invites, invited side
# ---------------------------------------------------------------------------


@dataclass
class InviteForMe:
    """An invite waiting for the signed-in account."""

    invite_id: int
    workspace_name: str
    role: str
    invited_by: str | None
    expires_at: datetime


def invites_for(db: Session, account: Account, now: datetime | None = None) -> list[InviteForMe]:
    """Open invites for this account's GitHub id, to workspaces that are not archived."""
    moment = now or datetime.now(UTC)
    rows = db.execute(
        select(WorkspaceInvite, Workspace)
        .join(Workspace, Workspace.id == WorkspaceInvite.workspace_id)
        .where(
            WorkspaceInvite.github_user_id == account.github_user_id,
            Workspace.archived_at.is_(None),
        )
        .order_by(Workspace.name, WorkspaceInvite.id)
    ).all()
    out: list[InviteForMe] = []
    for pending, workspace in rows:
        if is_expired(pending, moment):
            continue
        inviter = (
            db.get(Account, pending.invited_by_account_id)
            if pending.invited_by_account_id
            else None
        )
        out.append(
            InviteForMe(
                invite_id=pending.id,
                workspace_name=workspace.name,
                role=pending.role,
                invited_by=inviter.github_login if inviter else None,
                expires_at=_aware(pending.expires_at),
            )
        )
    return out


def _my_invite(db: Session, account: Account, invite_id: int, now: datetime) -> WorkspaceInvite:
    pending = db.get(WorkspaceInvite, invite_id)
    if pending is None or pending.github_user_id != account.github_user_id:
        raise AdminError("not_found")
    workspace = db.get(Workspace, pending.workspace_id)
    if workspace is None or workspace.archived_at is not None or is_expired(pending, now):
        raise AdminError("not_found")
    return pending


def accept_invite(
    db: Session, account: Account, invite_id: int, now: datetime | None = None
) -> int:
    """Join the workspace with the invited role. Returns the workspace id."""
    pending = _my_invite(db, account, invite_id, now or datetime.now(UTC))
    workspace_id = pending.workspace_id
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.account_id == account.id,
        )
    )
    if member is None:
        db.add(
            WorkspaceMember(
                workspace_id=workspace_id,
                account_id=account.id,
                role=pending.role,
                invited_by_account_id=pending.invited_by_account_id,
            )
        )
    db.delete(pending)
    db.commit()
    logger.info(
        "invite accepted: workspace_id=%s invite_id=%s account_id=%s",
        workspace_id,
        invite_id,
        account.id,
    )
    return workspace_id


def decline_invite(
    db: Session, account: Account, invite_id: int, now: datetime | None = None
) -> None:
    """Turn the invite down. The owner can invite again."""
    pending = _my_invite(db, account, invite_id, now or datetime.now(UTC))
    db.delete(pending)
    db.commit()
    logger.info("invite declined: invite_id=%s account_id=%s", invite_id, account.id)
