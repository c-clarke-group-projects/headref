"""Canonical organizational and project models (DATA_AND_EVIDENCE.md 6.3)."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.identity import IdentityLink
    from app.models.work import Commit, PullRequest, WorkItem


class Organization(Base):
    """An organization tenant. Single row in MVP."""

    __tablename__ = "organization"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    teams: Mapped[list["Team"]] = relationship("Team", back_populates="organization")
    users: Mapped[list["AppUser"]] = relationship("AppUser", back_populates="organization")
    repositories: Mapped[list["Repository"]] = relationship(
        "Repository", back_populates="organization"
    )
    projects: Mapped[list["Project"]] = relationship("Project", back_populates="organization")


class Team(Base):
    """A team group that a lead is responsible for."""

    __tablename__ = "team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship("Organization", back_populates="teams")
    members: Mapped[list["AppUser"]] = relationship("AppUser", back_populates="team")


class AppUser(Base):
    """An internal team member.

    Named app_user because 'user' is a reserved keyword in PostgreSQL.
    """

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("team.id", ondelete="SET NULL"), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    organization: Mapped["Organization"] = relationship("Organization", back_populates="users")
    team: Mapped["Team | None"] = relationship("Team", back_populates="members")
    identity_links: Mapped[list["IdentityLink"]] = relationship(
        "IdentityLink", back_populates="app_user"
    )

    @property
    def user_id(self) -> int:
        """Alias id to user_id for template and schema compatibility."""
        return self.id


class Repository(Base):
    """A configured GitHub repository."""

    __tablename__ = "repository"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    default_branch: Mapped[str] = mapped_column(String(255), default="main", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="repositories"
    )
    pull_requests: Mapped[list["PullRequest"]] = relationship(
        "PullRequest", back_populates="repository"
    )
    commits: Mapped[list["Commit"]] = relationship("Commit", back_populates="repository")


class Project(Base):
    """A configured Jira project."""

    __tablename__ = "project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    organization: Mapped["Organization"] = relationship("Organization", back_populates="projects")
    work_items: Mapped[list["WorkItem"]] = relationship("WorkItem", back_populates="project")
