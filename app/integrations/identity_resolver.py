"""Identity resolution and unmatched entity tracking (P2-006, Issue #17).

Attributes canonical WorkItem, PullRequest, Commit, and Review records to internal
AppUsers using verified manual IdentityLinks ONLY. Enforces DEC-008 and AC-13:
no display-name matching exists anywhere in this module. Unmatched accounts are
tracked in unmatched_entity with occurrence counts and timestamps.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.canonical import AppUser, Team
from app.models.identity import IdentityLink, UnmatchedEntity
from app.models.work import Commit, PullRequest, Review, WorkItem

logger = logging.getLogger("headref.integrations.identity_resolver")


def resolve_actor(
    session: Session,
    integration: str,
    external_id: str | int | None,
    external_handle: str | None = None,
    now: datetime | None = None,
    auto_create: bool = False,
    team_id: int | None = None,
) -> int | None:
    """Resolve an external account identifier to an internal AppUser ID.

    STRICT ATTRIBUTION RULES (DEC-008, AC-13):
    1. Only verified 'manual' or 'oauth' IdentityLink records are used for attribution.
    2. Inferred links MUST NOT be used for attribution until confirmed.
    3. Display name matching is strictly forbidden.
    4. When auto_create is True (repository onboarding), new active team members and
       verified IdentityLinks are provisioned automatically for discovered contributors.
    """
    if not external_id:
        return None

    str_external_id = str(external_id).strip()
    if not str_external_id:
        return None

    current_time = now or datetime.now(UTC)

    # 1. Query for an existing identity link by ID
    stmt = select(IdentityLink).where(
        IdentityLink.integration == integration,
        IdentityLink.external_id == str_external_id,
    )
    link = session.scalar(stmt)
    if not link and external_handle:
        stmt_handle = select(IdentityLink).where(
            IdentityLink.integration == integration,
            func.lower(IdentityLink.external_handle) == external_handle.lower(),
        )
        link = session.scalar(stmt_handle)

    if link is not None:
        if link.match_method in ("manual", "oauth") or link.verified_at is not None:
            return link.app_user_id

        logger.info(
            "Account (%s, %s) has unverified/inferred link; leaving actor unassigned.",
            integration,
            str_external_id,
        )

    # If auto_create is enabled, automatically provision an active team member
    if auto_create:
        team = session.get(Team, team_id) if team_id else session.scalar(select(Team))
        org_id = team.organization_id if team else 1
        resolved_team_id = team.id if team else None

        display_name = external_handle or f"Contributor {str_external_id}"
        new_user = AppUser(
            organization_id=org_id,
            team_id=resolved_team_id,
            display_name=display_name,
            role_label="Contributor",
            is_active=True,
        )
        session.add(new_user)
        session.flush()

        new_link = IdentityLink(
            app_user_id=new_user.id,
            integration=integration,
            external_id=str_external_id,
            external_handle=external_handle or display_name,
            match_method="manual",
            confidence="HIGH",
            verified_at=current_time,
        )
        session.add(new_link)
        session.flush()
        logger.info("Auto-provisioned contributor: %s (id=%d)", display_name, new_user.id)
        return new_user.id

    # 2. Account is unverified or unknown: record in unmatched_entity
    record_unmatched(
        session=session,
        integration=integration,
        external_id=str_external_id,
        external_handle=external_handle,
        current_time=current_time,
    )
    return None


def record_unmatched(
    session: Session,
    integration: str,
    external_id: str,
    external_handle: str | None = None,
    current_time: datetime | None = None,
) -> UnmatchedEntity:
    """Create or increment an UnmatchedEntity record for an unknown account."""
    now = current_time or datetime.now(UTC)

    stmt = select(UnmatchedEntity).where(
        UnmatchedEntity.integration == integration,
        UnmatchedEntity.external_id == external_id,
    )
    existing = session.scalar(stmt)

    if existing:
        existing.occurrence_count += 1
        existing.last_seen_at = now
        if external_handle and not existing.external_handle:
            existing.external_handle = external_handle[:255]
        return existing

    unmatched = UnmatchedEntity(
        integration=integration,
        external_id=external_id[:255],
        external_handle=external_handle[:255] if external_handle else None,
        occurrence_count=1,
        first_seen_at=now,
        last_seen_at=now,
        resolved_app_user_id=None,
    )
    session.add(unmatched)
    session.flush()
    return unmatched


def record_inferred_link(
    session: Session,
    app_user_id: int,
    integration: str,
    external_id: str,
    external_handle: str,
    confidence: str = "LOW",
) -> IdentityLink | None:
    """Store an inferred identity link candidate without verifying it.

    Inferred links are stored for human review and MUST NOT be used for attribution (DEC-008).
    """
    stmt = select(IdentityLink).where(
        IdentityLink.integration == integration,
        IdentityLink.external_id == external_id,
    )
    existing = session.scalar(stmt)
    if existing:
        return existing

    link = IdentityLink(
        app_user_id=app_user_id,
        integration=integration,
        external_id=external_id[:255],
        external_handle=external_handle[:255],
        match_method="inferred",
        confidence=confidence,
        verified_at=None,
    )
    session.add(link)
    session.flush()
    return link


def attribute_pull_requests(
    session: Session,
    prs: list[PullRequest],
    raw_payloads: list[dict[str, Any]],
    now: datetime | None = None,
    auto_create: bool = False,
    team_id: int | None = None,
) -> int:
    """Resolve and assign author_app_user_id for pull requests."""
    attributed = 0
    for pr, raw in zip(prs, raw_payloads, strict=False):
        user_data = raw.get("user") or {}
        gh_user_id = user_data.get("id")
        gh_login = user_data.get("login")
        user_id = resolve_actor(
            session=session,
            integration="github",
            external_id=gh_user_id,
            external_handle=gh_login,
            now=now,
            auto_create=auto_create,
            team_id=team_id,
        )
        pr.author_app_user_id = user_id
        if user_id is not None:
            attributed += 1
    return attributed


def attribute_commits(
    session: Session,
    commits: list[Commit],
    raw_payloads: list[dict[str, Any]],
    now: datetime | None = None,
    auto_create: bool = False,
    team_id: int | None = None,
) -> int:
    """Resolve and assign author_app_user_id for commits."""
    attributed = 0
    for commit, raw in zip(commits, raw_payloads, strict=False):
        author_data = raw.get("author") or {}
        gh_user_id = author_data.get("id")
        gh_login = author_data.get("login")
        user_id = resolve_actor(
            session=session,
            integration="github",
            external_id=gh_user_id,
            external_handle=gh_login,
            now=now,
            auto_create=auto_create,
            team_id=team_id,
        )
        commit.author_app_user_id = user_id
        if user_id is not None:
            attributed += 1
    return attributed


def attribute_reviews(
    session: Session,
    reviews: list[Review],
    raw_payloads: list[dict[str, Any]],
    now: datetime | None = None,
    auto_create: bool = False,
    team_id: int | None = None,
) -> int:
    """Resolve and assign reviewer_app_user_id for reviews."""
    attributed = 0
    for review, raw in zip(reviews, raw_payloads, strict=False):
        user_data = raw.get("user") or {}
        gh_user_id = user_data.get("id")
        gh_login = user_data.get("login")
        user_id = resolve_actor(
            session=session,
            integration="github",
            external_id=gh_user_id,
            external_handle=gh_login,
            now=now,
            auto_create=auto_create,
            team_id=team_id,
        )
        review.reviewer_app_user_id = user_id
        if user_id is not None:
            attributed += 1
    return attributed


def attribute_work_items(
    session: Session,
    work_items: list[WorkItem],
    raw_payloads: list[dict[str, Any]],
    now: datetime | None = None,
) -> int:
    """Resolve and assign assignee_app_user_id for Jira issues."""
    attributed = 0
    for item, raw in zip(work_items, raw_payloads, strict=False):
        fields = raw.get("fields") or {}
        assignee = fields.get("assignee")
        if assignee:
            account_id = assignee.get("accountId")
            display_name = assignee.get("displayName")
            user_id = resolve_actor(
                session=session,
                integration="jira",
                external_id=account_id,
                external_handle=display_name,
                now=now,
            )
            item.assignee_app_user_id = user_id
            if user_id is not None:
                attributed += 1
        else:
            item.assignee_app_user_id = None
    return attributed
