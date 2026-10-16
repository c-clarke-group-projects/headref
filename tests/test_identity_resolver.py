"""Unit tests for identity resolution and unmatched entity queue (P2-006, Issue #17)."""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.integrations.identity_resolver import (
    attribute_pull_requests,
    attribute_work_items,
    record_inferred_link,
    resolve_actor,
)
from app.models.canonical import AppUser, Organization, Project, Repository
from app.models.identity import IdentityLink, UnmatchedEntity
from app.models.work import PullRequest, WorkItem


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_verified_manual_link_attributes_correctly(db_session: Session) -> None:
    """A verified manual identity link attributes the record to the internal app_user."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    user = AppUser(organization_id=org.id, display_name="Keshan", role_label="Backend")
    db_session.add(user)
    db_session.flush()

    now = datetime.now(UTC)
    link = IdentityLink(
        app_user_id=user.id,
        integration="github",
        external_id="219891474",
        external_handle="keshan-dev",
        match_method="manual",
        confidence="HIGH",
        verified_at=now,
    )
    db_session.add(link)
    db_session.flush()

    resolved = resolve_actor(db_session, "github", "219891474", external_handle="keshan-dev")
    assert resolved == user.id

    # Verify no unmatched entity row was created
    unmatched = db_session.scalars(select(UnmatchedEntity)).all()
    assert len(unmatched) == 0


def test_unmapped_account_leaves_actor_null_and_creates_unmatched_entity(
    db_session: Session,
) -> None:
    """An unmapped external account leaves actor None and queues an unmatched entity."""
    resolved = resolve_actor(db_session, "github", "999999", external_handle="ghost-contributor")
    assert resolved is None

    unmatched = db_session.scalars(select(UnmatchedEntity)).all()
    assert len(unmatched) == 1
    record = unmatched[0]
    assert record.integration == "github"
    assert record.external_id == "999999"
    assert record.external_handle == "ghost-contributor"
    assert record.occurrence_count == 1
    assert record.resolved_app_user_id is None
    assert record.first_seen_at is not None
    assert record.last_seen_at is not None


def test_repeat_unmapped_account_increments_count(db_session: Session) -> None:
    """Seeing the same unmapped account multiple times increments occurrence_count."""
    resolve_actor(db_session, "jira", "unmapped-jira-acc", external_handle="Jane Doe")
    resolve_actor(db_session, "jira", "unmapped-jira-acc", external_handle="Jane Doe")
    resolve_actor(db_session, "jira", "unmapped-jira-acc", external_handle="Jane Doe")

    records = db_session.scalars(select(UnmatchedEntity)).all()
    assert len(records) == 1
    assert records[0].integration == "jira"
    assert records[0].external_id == "unmapped-jira-acc"
    assert records[0].occurrence_count == 3


def test_inferred_link_stored_but_never_used_for_attribution(db_session: Session) -> None:
    """An unverified inferred link is stored for review but MUST NOT be used for attribution."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    user = AppUser(organization_id=org.id, display_name="Isiwara", role_label="Frontend")
    db_session.add(user)
    db_session.flush()

    # Store an inferred link (verified_at is None)
    inferred = record_inferred_link(
        db_session,
        app_user_id=user.id,
        integration="github",
        external_id="inferred-gh-id",
        external_handle="inferred-handle",
        confidence="MEDIUM",
    )
    assert inferred is not None
    assert inferred.match_method == "inferred"
    assert inferred.verified_at is None

    # Resolution attempt MUST return None (DEC-008)
    resolved = resolve_actor(db_session, "github", "inferred-gh-id")
    assert resolved is None

    # And creates an unmatched entity entry
    unmatched = db_session.scalars(select(UnmatchedEntity)).all()
    assert any(u.external_id == "inferred-gh-id" for u in unmatched)


def test_no_display_name_matching_exists(db_session: Session) -> None:
    """Display name matching is strictly forbidden (AC-13).

    Even if external_handle matches an AppUser's display_name exactly,
    the account must NOT be attributed without a verified IdentityLink.
    """
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    user = AppUser(organization_id=org.id, display_name="Keshan", role_label="Backend")
    db_session.add(user)
    db_session.flush()

    # External account with handle 'Keshan' but unknown external_id
    resolved = resolve_actor(db_session, "github", "imposter-id-123", external_handle="Keshan")
    assert resolved is None
    assert resolved != user.id

    # Code audit assertion: verify identity_resolver.py never queries AppUser.display_name
    resolver_code = Path("app/integrations/identity_resolver.py").read_text(encoding="utf-8")
    assert "AppUser.display_name" not in resolver_code
    assert "display_name ==" not in resolver_code
    assert "== display_name" not in resolver_code


def test_attribute_pull_requests_never_discards_records(db_session: Session) -> None:
    """attribute_pull_requests sets user ID when verified, None when unmapped; never discards."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    user = AppUser(organization_id=org.id, display_name="Keshan")
    db_session.add(user)
    db_session.flush()

    now = datetime.now(UTC)
    link = IdentityLink(
        app_user_id=user.id,
        integration="github",
        external_id="219891474",
        external_handle="keshan-dev",
        match_method="manual",
        confidence="HIGH",
        verified_at=now,
    )
    db_session.add(link)
    db_session.flush()

    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add(repo)
    db_session.flush()

    pr1 = PullRequest(
        repository_id=repo.id,
        number=1,
        title="Verified author PR",
        state="open",
        created_at_source=now,
        source_url="url1",
        source_updated_at=now,
        retrieved_at=now,
    )
    pr2 = PullRequest(
        repository_id=repo.id,
        number=2,
        title="Unverified author PR",
        state="open",
        created_at_source=now,
        source_url="url2",
        source_updated_at=now,
        retrieved_at=now,
    )
    prs = [pr1, pr2]
    raw_payloads = [
        {"user": {"id": 219891474, "login": "keshan-dev"}},
        {"user": {"id": 888888, "login": "external-author"}},
    ]

    attributed_count = attribute_pull_requests(db_session, prs, raw_payloads)
    assert attributed_count == 1
    assert pr1.author_app_user_id == user.id
    assert pr2.author_app_user_id is None

    # Both PRs still exist (neither was discarded)
    assert len(prs) == 2


def test_attribute_work_items_jira(db_session: Session) -> None:
    """attribute_work_items resolves Jira assignee accountId; handles unassigned issues."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    user = AppUser(organization_id=org.id, display_name="Keshan")
    db_session.add(user)
    db_session.flush()

    now = datetime.now(UTC)
    link = IdentityLink(
        app_user_id=user.id,
        integration="jira",
        external_id="5f3a1b2c3d4e",
        external_handle="Keshan P.",
        match_method="manual",
        confidence="HIGH",
        verified_at=now,
    )
    db_session.add(link)
    db_session.flush()

    proj = Project(organization_id=org.id, key="AUTH", name="Auth")
    db_session.add(proj)
    db_session.flush()

    w1 = WorkItem(
        project_id=proj.id,
        external_id="AUTH-245",
        title="Assigned issue",
        status="in_progress",
        raw_status="In Progress",
        source_url="url1",
        source_updated_at=now,
        retrieved_at=now,
    )
    w2 = WorkItem(
        project_id=proj.id,
        external_id="AUTH-246",
        title="Unassigned issue",
        status="todo",
        raw_status="To Do",
        source_url="url2",
        source_updated_at=now,
        retrieved_at=now,
    )
    items = [w1, w2]
    raw_payloads = [
        {"fields": {"assignee": {"accountId": "5f3a1b2c3d4e", "displayName": "Keshan P."}}},
        {"fields": {"assignee": None}},
    ]

    attributed = attribute_work_items(db_session, items, raw_payloads)
    assert attributed == 1
    assert w1.assignee_app_user_id == user.id
    assert w2.assignee_app_user_id is None
