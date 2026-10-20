"""Identity mapping and unmatched entity models (DEC-008, DATA_AND_EVIDENCE.md 6.3)."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.canonical import AppUser


class IdentityLink(Base):
    """Maps an external account to an internal person (DEC-008)."""

    __tablename__ = "identity_link"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "integration",
            "external_id",
            name="uq_identity_link_org_integration_external",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The workspace's organization (W5). Lookups always filter on it.
    organization_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organization.id", ondelete="CASCADE", name="fk_identity_link_organization"),
        nullable=True,
    )
    app_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    integration: Mapped[str] = mapped_column(String(32), nullable=False)  # github, jira
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_handle: Mapped[str] = mapped_column(String(255), nullable=False)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)  # manual, inferred
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)  # HIGH, MEDIUM, LOW
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    app_user: Mapped["AppUser"] = relationship("AppUser", back_populates="identity_links")


class UnmatchedEntity(Base):
    """External accounts seen during ingestion that could not be mapped to a person."""

    __tablename__ = "unmatched_entity"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "integration",
            "external_id",
            name="uq_unmatched_entity_org_integration_external",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organization.id", ondelete="CASCADE", name="fk_unmatched_entity_organization"),
        nullable=True,
    )
    integration: Mapped[str] = mapped_column(String(32), nullable=False)  # github, jira
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    resolved_app_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("app_user.id", ondelete="SET NULL"), nullable=True, index=True
    )
