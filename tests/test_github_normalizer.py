"""Unit tests for GitHub normalizer and canonical database loader (P2-003, Issue #14)."""

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import EXCERPT_MAX_CHARS
from app.db import Base
from app.integrations.github_normalizer import (
    clean_excerpt,
    ingest_commits,
    ingest_pull_requests,
    normalize_commit,
    normalize_pull_request,
    normalize_repository,
    normalize_review,
    parse_datetime,
    upsert_commit,
    upsert_pull_request,
    upsert_repository,
    upsert_review,
)
from app.models.canonical import Organization, Repository
from app.models.work import Commit, PullRequest, Review


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_clean_excerpt_removes_urls_and_truncates() -> None:
    """clean_excerpt removes URLs and truncates to EXCERPT_MAX_CHARS per FR-030."""
    # None or empty returns None
    assert clean_excerpt(None) is None
    assert clean_excerpt("") is None
    assert clean_excerpt("   ") is None

    # URLs removed
    text_with_urls = "Fixed bug https://github.com/keshan-dev/headref/issues/14 in production."
    assert clean_excerpt(text_with_urls) == "Fixed bug  in production."

    # Only URL returns None
    assert clean_excerpt("https://example.com/test") is None

    # Length truncation
    long_text = "a" * (EXCERPT_MAX_CHARS + 50)
    cleaned = clean_excerpt(long_text)
    assert cleaned is not None
    assert len(cleaned) == EXCERPT_MAX_CHARS


def test_parse_datetime_handles_iso_and_utc() -> None:
    """parse_datetime parses ISO-8601 strings and guarantees UTC timezone."""
    assert parse_datetime(None) is None
    assert parse_datetime("invalid-date") is None

    dt = parse_datetime("2026-09-20T12:00:00Z")
    assert dt is not None
    assert dt.tzinfo == UTC
    assert dt.year == 2026
    assert dt.month == 9
    assert dt.day == 20
    assert dt.hour == 12

    # Naive datetime gets converted to UTC
    naive = datetime(2026, 9, 20, 10, 0, 0)
    aware = parse_datetime(naive)
    assert aware is not None
    assert aware.tzinfo == UTC


def test_normalize_repository_pure() -> None:
    """normalize_repository transforms payload without database access."""
    payload = {
        "id": 123456,
        "name": "headref",
        "full_name": "keshan-dev/headref",
        "default_branch": "main",
    }
    repo_dict = normalize_repository(payload, organization_id=1)
    assert repo_dict["organization_id"] == 1
    assert repo_dict["full_name"] == "keshan-dev/headref"
    assert repo_dict["default_branch"] == "main"
    assert repo_dict["is_active"] is True

    # Missing full_name raises ValueError
    with pytest.raises(ValueError, match="Repository payload missing"):
        normalize_repository({}, organization_id=1)


def test_normalize_pull_request_state_derivation_and_fields() -> None:
    """normalize_pull_request derives open, merged, closed states and enforces UTC fields."""
    retrieved = datetime(2026, 9, 20, 15, 0, 0, tzinfo=UTC)

    # 1. Open PR
    open_payload = {
        "number": 14,
        "title": "GitHub normalizer implementation",
        "body": "Fixes #14. See https://github.com/keshan-dev/headref/pull/14",
        "state": "open",
        "draft": False,
        "head": {"ref": "feat/phase-02"},
        "created_at": "2026-09-20T10:00:00Z",
        "updated_at": "2026-09-20T11:00:00Z",
        "merged_at": None,
        "html_url": "https://github.com/keshan-dev/headref/pull/14",
    }
    pr_open = normalize_pull_request(open_payload, repository_id=10, retrieved_at=retrieved)
    assert pr_open["repository_id"] == 10
    assert pr_open["number"] == 14
    assert pr_open["title"] == "GitHub normalizer implementation"
    assert pr_open["state"] == "open"
    assert pr_open["is_draft"] is False
    assert pr_open["branch_name"] == "feat/phase-02"
    assert pr_open["author_app_user_id"] is None
    assert "https://" not in (pr_open["body_excerpt"] or "")
    assert pr_open["retrieved_at"] == retrieved
    assert pr_open["merged_at"] is None

    # 2. Merged PR (merged_at overrides state: "closed")
    merged_payload = {
        **open_payload,
        "state": "closed",
        "merged_at": "2026-09-20T14:30:00Z",
    }
    pr_merged = normalize_pull_request(merged_payload, repository_id=10, retrieved_at=retrieved)
    assert pr_merged["state"] == "merged"
    assert pr_merged["merged_at"] is not None

    # 3. Closed (unmerged) PR
    closed_payload = {
        **open_payload,
        "state": "closed",
        "merged_at": None,
    }
    pr_closed = normalize_pull_request(closed_payload, repository_id=10, retrieved_at=retrieved)
    assert pr_closed["state"] == "closed"
    assert pr_closed["merged_at"] is None

    # 4. Missing required number or title raises ValueError
    with pytest.raises(ValueError, match="PullRequest payload missing"):
        normalize_pull_request({"title": "No number"}, repository_id=10)


def test_normalize_commit_pure() -> None:
    """normalize_commit transforms commit payload into canonical attributes."""
    retrieved = datetime(2026, 9, 20, 15, 0, 0, tzinfo=UTC)
    payload = {
        "sha": "a1b2c3d4e5f67890123456789abcdef012345678",
        "commit": {
            "message": "feat: normalizer logic https://example.com/doc",
            "author": {"date": "2026-09-20T09:00:00Z"},
        },
        "html_url": "https://github.com/keshan-dev/headref/commit/a1b2c3d",
    }
    commit_dict = normalize_commit(
        payload, repository_id=10, retrieved_at=retrieved, branch_name="feat/phase-02"
    )
    assert commit_dict["repository_id"] == 10
    assert commit_dict["sha"] == "a1b2c3d4e5f67890123456789abcdef012345678"
    assert commit_dict["branch_name"] == "feat/phase-02"
    assert commit_dict["author_app_user_id"] is None
    assert "https://" not in (commit_dict["message_excerpt"] or "")
    assert commit_dict["retrieved_at"] == retrieved

    with pytest.raises(ValueError, match="Commit payload missing sha"):
        normalize_commit({}, repository_id=10)


def test_normalize_review_pure() -> None:
    """normalize_review transforms review payload into canonical attributes."""
    retrieved = datetime(2026, 9, 20, 15, 0, 0, tzinfo=UTC)
    payload = {
        "id": 987654321,
        "state": "APPROVED",
        "body": "Looks great! Check https://example.com",
        "submitted_at": "2026-09-20T12:00:00Z",
        "html_url": "https://github.com/keshan-dev/headref/pull/14#pullrequestreview-987654321",
    }
    review_dict = normalize_review(payload, pull_request_id=5, retrieved_at=retrieved)
    assert review_dict["pull_request_id"] == 5
    assert review_dict["external_id"] == "987654321"
    assert review_dict["state"] == "approved"
    assert review_dict["reviewer_app_user_id"] is None
    assert "https://" not in (review_dict["body_excerpt"] or "")
    assert review_dict["retrieved_at"] == retrieved

    with pytest.raises(ValueError, match="Review payload missing id"):
        normalize_review({}, pull_request_id=5)


def test_upsert_repository_idempotent(db_session: Session) -> None:
    """upsert_repository updates attributes on repeat call without creating duplicates."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    repo_dict = {
        "organization_id": org.id,
        "full_name": "keshan-dev/headref",
        "default_branch": "main",
        "is_active": True,
    }

    # First insert
    repo1 = upsert_repository(db_session, repo_dict)
    assert repo1.id is not None

    repos = db_session.scalars(select(Repository)).all()
    assert len(repos) == 1
    assert repos[0].default_branch == "main"

    # Second upsert with modified branch
    updated_dict = {**repo_dict, "default_branch": "develop"}
    repo2 = upsert_repository(db_session, updated_dict)
    assert repo2.id == repo1.id

    repos_after = db_session.scalars(select(Repository)).all()
    assert len(repos_after) == 1
    assert repos_after[0].default_branch == "develop"


def test_upsert_pull_request_idempotent(db_session: Session) -> None:
    """upsert_pull_request updates attributes on re-run without duplicating records."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add(repo)
    db_session.flush()

    now = datetime.now(UTC)
    pr_dict = {
        "repository_id": repo.id,
        "number": 14,
        "title": "Initial PR title",
        "body_excerpt": "Initial excerpt",
        "state": "open",
        "is_draft": False,
        "branch_name": "feat/phase-02",
        "author_app_user_id": None,
        "created_at_source": now,
        "merged_at": None,
        "source_url": "https://github.com/keshan-dev/headref/pull/14",
        "source_updated_at": now,
        "retrieved_at": now,
    }

    # First call
    pr1 = upsert_pull_request(db_session, pr_dict)
    assert pr1.id is not None
    assert len(db_session.scalars(select(PullRequest)).all()) == 1

    # Second call with updated state and title
    updated_dict = {
        **pr_dict,
        "title": "Updated PR title",
        "state": "merged",
        "merged_at": now,
    }
    pr2 = upsert_pull_request(db_session, updated_dict)
    assert pr2.id == pr1.id

    prs = db_session.scalars(select(PullRequest)).all()
    assert len(prs) == 1
    assert prs[0].title == "Updated PR title"
    assert prs[0].state == "merged"


def test_upsert_commit_idempotent(db_session: Session) -> None:
    """upsert_commit updates attributes on re-run without creating duplicates."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add(repo)
    db_session.flush()

    now = datetime.now(UTC)
    commit_dict = {
        "repository_id": repo.id,
        "sha": "a1b2c3d4e5f67890123456789abcdef012345678",
        "message_excerpt": "Original commit message",
        "branch_name": "feat/phase-02",
        "author_app_user_id": None,
        "committed_at": now,
        "source_url": "https://github.com/keshan-dev/headref/commit/a1b2c3d",
        "retrieved_at": now,
    }

    # First call
    c1 = upsert_commit(db_session, commit_dict)
    assert c1.id is not None
    assert len(db_session.scalars(select(Commit)).all()) == 1

    # Second call
    updated_dict = {**commit_dict, "message_excerpt": "Updated commit message"}
    c2 = upsert_commit(db_session, updated_dict)
    assert c2.id == c1.id

    commits = db_session.scalars(select(Commit)).all()
    assert len(commits) == 1
    assert commits[0].message_excerpt == "Updated commit message"


def test_upsert_review_idempotent(db_session: Session) -> None:
    """upsert_review updates review state on re-run without creating duplicates."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add(repo)
    db_session.flush()

    now = datetime.now(UTC)
    pr = PullRequest(
        repository_id=repo.id,
        number=14,
        title="Test PR",
        state="open",
        branch_name="feat/review-fixture",
        created_at_source=now,
        source_url="https://github.com/keshan-dev/headref/pull/14",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(pr)
    db_session.flush()

    review_dict = {
        "pull_request_id": pr.id,
        "external_id": "review-12345",
        "reviewer_app_user_id": None,
        "state": "changes_requested",
        "body_excerpt": "Please fix tests",
        "submitted_at": now,
        "source_url": "https://github.com/keshan-dev/headref/reviews/12345",
        "retrieved_at": now,
    }

    # First call
    r1 = upsert_review(db_session, review_dict)
    assert r1.id is not None
    assert len(db_session.scalars(select(Review)).all()) == 1

    # Second call
    updated_dict = {**review_dict, "state": "approved", "body_excerpt": "Now looks good"}
    r2 = upsert_review(db_session, updated_dict)
    assert r2.id == r1.id

    reviews = db_session.scalars(select(Review)).all()
    assert len(reviews) == 1
    assert reviews[0].state == "approved"
    assert reviews[0].body_excerpt == "Now looks good"


def test_batch_ingest_pull_requests_skips_malformed(db_session: Session) -> None:
    """ingest_pull_requests skips malformed records without failing the batch."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add(repo)
    db_session.flush()

    payloads = [
        {
            "number": 1,
            "title": "Valid PR 1",
            "state": "open",
            "created_at": "2026-09-20T10:00:00Z",
        },
        {
            # Missing number -> malformed
            "title": "Invalid PR missing number",
        },
        {
            "number": 2,
            "title": "Valid PR 2",
            "state": "closed",
            "merged_at": "2026-09-20T11:00:00Z",
        },
    ]

    prs, counts = ingest_pull_requests(db_session, repository_id=repo.id, payloads=payloads)
    assert counts.fetched == 3
    assert counts.written == 2
    assert counts.skipped == 1
    assert len(prs) == 2

    persisted = db_session.scalars(select(PullRequest)).all()
    assert len(persisted) == 2
    assert {p.number for p in persisted} == {1, 2}


def test_batch_ingest_commits_skips_malformed(db_session: Session) -> None:
    """ingest_commits skips malformed commit records without failing the batch."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add(repo)
    db_session.flush()

    payloads = [
        {
            "sha": "1111111111111111111111111111111111111111",
            "commit": {"message": "Valid commit 1"},
        },
        {
            # Missing sha -> malformed
            "commit": {"message": "Invalid commit"},
        },
        {
            "sha": "2222222222222222222222222222222222222222",
            "commit": {"message": "Valid commit 2"},
        },
    ]

    commits, counts = ingest_commits(
        db_session, repository_id=repo.id, payloads=payloads, branch_name="main"
    )
    assert counts.fetched == 3
    assert counts.written == 2
    assert counts.skipped == 1
    assert len(commits) == 2

    persisted = db_session.scalars(select(Commit)).all()
    assert len(persisted) == 2
    assert {c.sha for c in persisted} == {
        "1111111111111111111111111111111111111111",
        "2222222222222222222222222222222222222222",
    }
