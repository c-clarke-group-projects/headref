"""Workspace connections to GitHub (W3, DEC-022).

github_installation records which installations of the headref GitHub App a workspace
uses; workspace_repository records which of their repositories the workspace selected.
No token is stored: installation tokens are minted per use (app/integrations/github_app).
"""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class GitHubInstallation(Base):
    """1 installation of the headref GitHub App, attached to 1 workspace."""

    __tablename__ = "github_installation"
    __table_args__ = (
        UniqueConstraint("workspace_id", "installation_id", name="uq_github_installation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_login: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(32), nullable=False)
    connected_by_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    repositories: Mapped[list["WorkspaceRepository"]] = relationship(
        "WorkspaceRepository", back_populates="installation", cascade="all, delete-orphan"
    )


class WorkspaceRepository(Base):
    """A repository a workspace selected from 1 of its installations."""

    __tablename__ = "workspace_repository"
    __table_args__ = (
        UniqueConstraint("workspace_id", "github_repo_id", name="uq_workspace_repository"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False, index=True
    )
    github_installation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("github_installation.id", ondelete="CASCADE"), nullable=False
    )
    # Matched on GitHub's numeric id, so a renamed repository stays connected.
    github_repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    added_by_account_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    installation: Mapped["GitHubInstallation"] = relationship(
        "GitHubInstallation", back_populates="repositories"
    )
