"""Operations and ingestion tracking models (DATA_AND_EVIDENCE.md 6.3, FR-008, FR-009)."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SyncRun(Base):
    """Execution audit for every ingestion attempt (FR-008)."""

    __tablename__ = "sync_run"
    __table_args__ = (
        Index("ix_sync_run_source_started", "source", "started_at"),
        Index("ix_sync_run_workspace_started", "workspace_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # github, jira
    # scope: repo name, project key, or team id
    scope: Mapped[str] = mapped_column(String(255), nullable=False)
    # The workspace this run synced for (W5). Null for the legacy global syncs.
    workspace_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("workspace.id", ondelete="CASCADE", name="fk_sync_run_workspace"),
        nullable=True,
    )
    # status: running, success, partial, failed
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items_fetched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_skipped: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncCursor(Base):
    """Resume position per source and scope for incremental sync (FR-009).

    Primary key is composite (source, scope).
    """

    __tablename__ = "sync_cursor"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)  # github, jira
    scope: Mapped[str] = mapped_column(String(255), primary_key=True)  # repo name, project key
    cursor_value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )
