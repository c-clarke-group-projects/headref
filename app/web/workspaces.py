"""Workspace access and the workspace API (W1, DEC-021).

``workspace_access(min_role)`` is the only place the rule "an account may open a
workspace it is a member of, with at least this role" is written. Every route with
``{workspace_id}`` in its path MUST depend on it; a test fails if one does not.

A workspace the account is not a member of, or that is archived, answers 404, so a
request cannot learn whether another workspace exists (EV-10). A member whose role is
too low answers 403: they already know the workspace exists.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workspace import ROLE_RANK, Account, Workspace, WorkspaceMember
from app.schemas.workspace import WorkspaceCreate, WorkspaceOut
from app.web.auth import SESSION_COOKIE, DbDep, read_session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def upsert_account(db: Session, gh_user: dict[str, Any], app_user_id: int) -> Account:
    """Create or update the account for a GitHub user who just signed in.

    Matched on the numeric GitHub id, never on the login, which a user can change.
    The caller commits.
    """
    gh_user_id = int(gh_user["id"])
    login = str(gh_user.get("login") or "")
    display_name = str(gh_user.get("name") or login)
    now = datetime.now(UTC)

    account = db.scalar(select(Account).where(Account.github_user_id == gh_user_id))
    if account is None:
        account = Account(
            github_user_id=gh_user_id,
            github_login=login,
            display_name=display_name,
            app_user_id=app_user_id,
            created_at=now,
            last_login_at=now,
        )
        db.add(account)
    else:
        account.github_login = login
        account.display_name = display_name
        account.app_user_id = app_user_id
        account.last_login_at = now
    db.flush()
    return account


def current_account(request: Request, db: DbDep) -> Account:
    """Identify the signed-in account from the session cookie, or answer 401.

    The session carries the app_user id the GitHub callback signed in (transitional,
    removed in W7), and the account is found through Account.app_user_id.
    """
    user_id = read_session(request.cookies.get(SESSION_COOKIE))
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not logged in")
    account = db.scalar(select(Account).where(Account.app_user_id == user_id))
    if account is None:
        raise HTTPException(status_code=401, detail="Not logged in")
    return account


AccountDep = Annotated[Account, Depends(current_account)]


# ---------------------------------------------------------------------------
# Workspace access
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceContext:
    """The workspace a request is about, who is asking, and their role in it."""

    workspace: Workspace
    account: Account
    role: str


def workspace_access(min_role: str) -> Callable[..., WorkspaceContext]:
    """Return a dependency that admits members of {workspace_id} with at least min_role."""
    if min_role not in ROLE_RANK:
        raise ValueError(f"Unknown role {min_role!r}")

    def require_workspace(workspace_id: int, account: AccountDep, db: DbDep) -> WorkspaceContext:
        row = db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(
                Workspace.id == workspace_id,
                Workspace.archived_at.is_(None),
                WorkspaceMember.account_id == account.id,
            )
        ).first()
        if row is None:
            logger.warning(
                "workspace access denied: account_id=%s workspace_id=%s",
                account.id,
                workspace_id,
            )
            raise HTTPException(status_code=404, detail="Workspace not found")
        workspace, role = row
        if ROLE_RANK[role] < ROLE_RANK[min_role]:
            logger.warning(
                "workspace role too low: account_id=%s workspace_id=%s role=%s needs=%s",
                account.id,
                workspace_id,
                role,
                min_role,
            )
            raise HTTPException(status_code=403, detail="Your role cannot do this")
        return WorkspaceContext(workspace=workspace, account=account, role=role)

    require_workspace.min_role = min_role  # type: ignore[attr-defined]
    return require_workspace


ViewerAccess = Annotated[WorkspaceContext, Depends(workspace_access("viewer"))]
ManagerAccess = Annotated[WorkspaceContext, Depends(workspace_access("manager"))]
OwnerAccess = Annotated[WorkspaceContext, Depends(workspace_access("owner"))]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def _workspace_out(workspace: Workspace, role: str) -> WorkspaceOut:
    return WorkspaceOut(
        workspace_id=workspace.id,
        name=workspace.name,
        description=workspace.description,
        role=role,  # type: ignore[arg-type]
        created_at=workspace.created_at,
    )


def list_workspaces_for(db: Session, account: Account) -> list[WorkspaceOut]:
    """Workspaces the account is a member of, not archived, by name."""
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.account_id == account.id, Workspace.archived_at.is_(None))
        .order_by(Workspace.name, Workspace.id)
    ).all()
    return [_workspace_out(workspace, role) for workspace, role in rows]


def create_workspace(db: Session, account: Account, body: WorkspaceCreate) -> WorkspaceOut:
    """Create a workspace with the account as its owner, and commit."""
    workspace = Workspace(
        name=body.name,
        description=body.description,
        created_by_account_id=account.id,
        created_at=datetime.now(UTC),
    )
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, account_id=account.id, role="owner"))
    db.commit()
    logger.info("workspace created: workspace_id=%s account_id=%s", workspace.id, account.id)
    return _workspace_out(workspace, "owner")


workspace_api = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


@workspace_api.get("", response_model=list[WorkspaceOut])
def get_my_workspaces(account: AccountDep, db: DbDep) -> list[WorkspaceOut]:
    """The signed-in account's workspaces."""
    return list_workspaces_for(db, account)


@workspace_api.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def post_workspace(body: WorkspaceCreate, account: AccountDep, db: DbDep) -> WorkspaceOut:
    """Create a workspace. The caller becomes its owner."""
    return create_workspace(db, account, body)


@workspace_api.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(access: ViewerAccess) -> WorkspaceOut:
    """1 workspace, if the caller is a member."""
    return _workspace_out(access.workspace, access.role)
