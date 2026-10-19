"""Accounts, workspaces and workspace membership (DEC-021).

An account is a person who signs in with GitHub. A workspace is the isolation boundary a
manager creates; workspace_member gives an account a role in it. The people whose work is
described stay in app_user until W6 moves them under a workspace.
"""

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

WorkspaceRole = Literal["owner", "manager", "viewer"]

# Higher rank includes everything a lower rank may do.
ROLE_RANK: dict[str, int] = {"viewer": 1, "manager": 2, "owner": 3}


def _now() -> datetime:
    return datetime.now(UTC)


class Account(Base):
    """A person who signs in with GitHub OAuth."""

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    github_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    github_login: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Transitional: the app_user the existing team and member pages act as. Removed in W7
    # when those pages are replaced by the workspace page.
    app_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("app_user.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="account",
        foreign_keys="WorkspaceMember.account_id",
    )


class Workspace(Base):
    """An isolated set of repositories, Jira projects and people (DEC-021)."""

    __tablename__ = "workspace"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The organization row this workspace's source data hangs off (W5): repositories,
    # Jira projects and people are scoped by organization_id.
    organization_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organization.id", name="fk_workspace_organization"),
        nullable=True,
        unique=True,
    )
    created_by_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    members: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember", back_populates="workspace", cascade="all, delete-orphan"
    )


class WorkspaceMember(Base):
    """An account's role in 1 workspace."""

    __tablename__ = "workspace_member"
    __table_args__ = (
        UniqueConstraint("workspace_id", "account_id", name="uq_workspace_member_account"),
        CheckConstraint("role IN ('owner', 'manager', 'viewer')", name="ck_workspace_member_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    invited_by_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    # When this account last opened the workspace, to mark newer feed items (W8).
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="members")
    account: Mapped["Account"] = relationship(
        "Account", back_populates="memberships", foreign_keys=[account_id]
    )
