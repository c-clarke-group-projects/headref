"""Unit tests for deterministic member summary generator (P4-008, Issue #32)."""

from datetime import UTC, datetime, timedelta

from app.agent.deterministic_summary import (
    FALLBACK_HEADER,
    PRODUCTIVITY_CONTEXT_NOTE,
    generate_deterministic_summary,
)
from app.schemas.tools import CommitOut, PullRequestOut, ReviewOut, WorkItemOut


def _sample_work_items() -> list[WorkItemOut]:
    now = datetime.now(UTC)
    return [
        WorkItemOut(
            work_item_id=1,
            external_id="AUTH-245",
            title="Refresh token rotation",
            status="in_progress",
            raw_status="In Progress",
            priority="High",
            is_flagged=False,
            blocked_by=["AUTH-246"],
            source_url="https://jira.example.com/AUTH-245",
            source_updated_at=now,
            retrieved_at=now,
        ),
        WorkItemOut(
            work_item_id=2,
            external_id="AUTH-240",
            title="Cookie spec",
            status="done",
            raw_status="Done",
            priority="Medium",
            is_flagged=False,
            blocked_by=[],
            source_url="https://jira.example.com/AUTH-240",
            source_updated_at=now,
            retrieved_at=now,
        ),
    ]


def _sample_pull_requests() -> list[PullRequestOut]:
    now = datetime.now(UTC)
    return [
        PullRequestOut(
            pull_request_id=10,
            number=182,
            repo_full_name="keshan-dev/headref",
            title="feat: refresh token rotation",
            state="open",
            is_draft=False,
            branch_name="feature/AUTH-245-token",
            review_state="approved",
            checks_state="passing",
            created_at=now - timedelta(days=1),
            source_updated_at=now,
            retrieved_at=now,
            source_url="https://github.com/keshan-dev/headref/pull/182",
        ),
        PullRequestOut(
            pull_request_id=11,
            number=180,
            repo_full_name="keshan-dev/headref",
            title="docs: cookie spec",
            state="merged",
            is_draft=False,
            branch_name="docs/cookie",
            review_state="approved",
            checks_state="passing",
            created_at=now - timedelta(days=2),
            source_updated_at=now,
            retrieved_at=now,
            source_url="https://github.com/keshan-dev/headref/pull/180",
        ),
    ]


def _sample_commits() -> list[CommitOut]:
    now = datetime.now(UTC)
    return [
        CommitOut(
            commit_id=101,
            sha="abcdef123456",
            repo_full_name="keshan-dev/headref",
            message_excerpt="feat: implement token rotation",
            branch_name="feature/AUTH-245-token",
            committed_at=now,
            source_url="https://github.com/keshan-dev/headref/commit/abcdef123456",
            retrieved_at=now,
        )
    ]


def _sample_reviews() -> list[ReviewOut]:
    now = datetime.now(UTC)
    return [
        ReviewOut(
            review_id=201,
            pull_request_id=10,
            pull_request_number=182,
            repo_full_name="keshan-dev/headref",
            reviewer_user_id=1,
            state="approved",
            body_excerpt="Looks solid.",
            submitted_at=now,
            source_url="https://github.com/keshan-dev/headref/pull/182#r201",
            retrieved_at=now,
        )
    ]


def test_deterministic_summary_populated() -> None:
    """Deterministic summary aggregates items and PRs and includes disclaimer (FR-026)."""
    wis = _sample_work_items()
    prs = _sample_pull_requests()
    commits = _sample_commits()
    reviews = _sample_reviews()

    result = generate_deterministic_summary(wis, prs, commits, reviews, is_fallback=False)

    assert result.is_fallback is False
    assert result.assigned_count == 2
    assert result.open_pr_count == 1
    assert result.commit_count == 1
    assert result.review_count == 1
    assert result.disclaimer == PRODUCTIVITY_CONTEXT_NOTE

    text = result.text
    assert "AUTH-245: Refresh token rotation [In Progress, High]" in text
    assert "(blocked by: AUTH-246)" in text
    assert "Completed: AUTH-240" in text
    assert "#182 (keshan-dev/headref): feat: refresh token rotation" in text
    assert "checks: passing" in text
    assert "review: approved" in text
    assert PRODUCTIVITY_CONTEXT_NOTE in text
    assert FALLBACK_HEADER not in text


def test_deterministic_summary_fallback_mode() -> None:
    """When is_fallback=True, the fallback header is prepended (FR-022)."""
    result = generate_deterministic_summary([], [], [], [], is_fallback=True)

    assert result.is_fallback is True
    assert result.text.startswith(FALLBACK_HEADER)
    assert PRODUCTIVITY_CONTEXT_NOTE in result.text


def test_deterministic_summary_empty_lists() -> None:
    """Deterministic summary handles empty input lists gracefully without error."""
    result = generate_deterministic_summary([], [], [], [], is_fallback=False)

    assert result.assigned_count == 0
    assert result.open_pr_count == 0
    assert "No work items currently assigned in this window." in result.text
    assert "No authored pull requests in this window." in result.text
    assert PRODUCTIVITY_CONTEXT_NOTE in result.text
