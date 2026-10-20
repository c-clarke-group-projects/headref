"""Workspace feed models (W8, DEC-025).

finding_snapshot keeps the latest code-built findings per subject ("person:<id>" or
"work_state") so the next sync can be diffed against it. workspace_event is the feed:
1 short, code-written line per change. No feed text comes from the model.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class FindingSnapshot(Base):
    """The latest findings of 1 subject in 1 workspace."""

    __tablename__ = "finding_snapshot"
    __table_args__ = (
        UniqueConstraint("workspace_id", "subject", name="uq_finding_snapshot_subject"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class WorkspaceEvent(Base):
    """1 line of the workspace feed."""

    __tablename__ = "workspace_event"
    __table_args__ = (
        Index("ix_workspace_event_workspace_occurred", "workspace_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    person_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    entity_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
