"""Work tracking and correlation models (DATA_AND_EVIDENCE.md 6.3, DEC-009)."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.canonical import Project, Repository


class WorkItem(Base):
    """A Jira issue."""

    __tablename__ = "work_item"
    __table_args__ = (
        Index("ix_work_item_project_external", "project_id", "external_id"),
        Index("ix_work_item_external_id", "external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. AUTH-245
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)  # normalized status
    raw_status: Mapped[str] = mapped_column(String(64), nullable=False)  # Jira native status
    assignee_app_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    priority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Freshness contract columns (both non-null)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    project: Mapped["Project"] = relationship("Project", back_populates="work_items")
    links: Mapped[list["WorkItemLink"]] = relationship(
        "WorkItemLink", back_populates="work_item", cascade="all, delete-orphan"
    )


class WorkItemDependency(Base):
    """Jira blocking relationships between work items."""

    __tablename__ = "work_item_dependency"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("work_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blocked_by_work_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("work_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # e.g. "blocks", "is blocked by"
    link_type: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class PullRequest(Base):
    """A GitHub pull request."""

    __tablename__ = "pull_request"
    __table_args__ = (Index("ix_pull_request_repo_number", "repository_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repository.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False)  # open, merged, closed
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    author_app_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    review_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checks_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at_source: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Freshness contract columns (both non-null)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    repository: Mapped["Repository"] = relationship("Repository", back_populates="pull_requests")
    reviews: Mapped[list["Review"]] = relationship(
        "Review", back_populates="pull_request", cascade="all, delete-orphan"
    )


class Commit(Base):
    """A GitHub commit."""

    __tablename__ = "commit"
    __table_args__ = (
        UniqueConstraint("repository_id", "sha", name="uq_commit_repo_sha"),
        Index("ix_commit_repo_committed", "repository_id", "committed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repository.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sha: Mapped[str] = mapped_column(String(64), nullable=False)
    message_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    author_app_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    repository: Mapped["Repository"] = relationship("Repository", back_populates="commits")


class Review(Base):
    """A GitHub pull request review."""

    __tablename__ = "review"
    __table_args__ = (
        UniqueConstraint("pull_request_id", "external_id", name="uq_review_pr_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pull_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pull_request.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewer_app_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # approved, changes_requested, commented
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    body_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    pull_request: Mapped["PullRequest"] = relationship("PullRequest", back_populates="reviews")


class ReviewRequest(Base):
    """A review requested on a pull request and not given yet (W11, DEC-025).

    Mirrors GitHub's requested_reviewers at the last sync: GitHub drops a reviewer from
    that list once they review, so a row means the request is still waiting.
    """

    __tablename__ = "review_request"
    __table_args__ = (
        UniqueConstraint(
            "pull_request_id", "reviewer_external_id", name="uq_review_request_reviewer"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pull_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pull_request.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewer_app_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class WorkItemLink(Base):
    """Correlation between planned work (Jira) and code (GitHub).

    Built during ingestion, not at request time (DEC-009).
    """

    __tablename__ = "work_item_link"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id", "target_type", "target_id", name="uq_work_item_link_target"
        ),
        Index("ix_work_item_link_work_item_id", "work_item_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("work_item.id", ondelete="CASCADE"), nullable=False
    )
    # pull_request, branch, commit, review
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # target PK or external identifier
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # branch_name, pr_title, commit_message, etc.
    link_method: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)  # HIGH, MEDIUM, LOW
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    work_item: Mapped["WorkItem"] = relationship("WorkItem", back_populates="links")
