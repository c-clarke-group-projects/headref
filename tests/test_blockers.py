"""Unit tests for deterministic blocker detection rules BL-1 through BL-8 (P4-003, Issue #27)."""

from datetime import UTC, datetime, timedelta

from app.agent.blockers import detect_blockers
from app.schemas.tools import CommitOut, PullRequestOut, WorkItemOut


def _base_work_item(
    external_id: str = "AUTH-245",
    status: str = "in_progress",
    raw_status: str = "In Progress",
    is_flagged: bool = False,
    blocked_by: list[str] | None = None,
    source_updated_at: datetime | None = None,
) -> WorkItemOut:
    now = source_updated_at or datetime.now(UTC)
    return WorkItemOut(
        work_item_id=1,
        external_id=external_id,
        title="Refresh token rotation",
        status=status,
        raw_status=raw_status,
        priority="High",
        is_flagged=is_flagged,
        blocked_by=blocked_by or [],
        source_url=f"https://jira.example.com/{external_id}",
        source_updated_at=now,
        retrieved_at=now,
    )


def _base_pr(
    number: int = 182,
    state: str = "open",
    is_draft: bool = False,
    review_state: str = "none",
    checks_state: str = "passing",
    created_at_source: datetime | None = None,
    last_review_at: datetime | None = None,
    branch_name: str = "feature/AUTH-245-token",
) -> PullRequestOut:
    now = datetime.now(UTC)
    return PullRequestOut(
        pull_request_id=10,
        number=number,
        repo_full_name="keshan-dev/headref",
        title="feat: refresh token rotation",
        state=state,
        is_draft=is_draft,
        branch_name=branch_name,
        review_state=review_state,
        checks_state=checks_state,
        created_at=created_at_source or now,
        last_review_at=last_review_at,
        source_updated_at=now,
        retrieved_at=now,
        source_url=f"https://github.com/keshan-dev/headref/pull/{number}",
    )


# ---------------------------------------------------------------------------
# BL-1: Work item status is blocked
# ---------------------------------------------------------------------------


def test_bl1_status_blocked_positive() -> None:
    wi = _base_work_item(status="blocked", raw_status="Blocked")
    blockers = detect_blockers([wi], [])
    assert any(b.signal_id == "BL-1" and b.blocker_type == "explicit" for b in blockers)


def test_bl1_status_blocked_negative() -> None:
    wi = _base_work_item(status="in_progress")
    blockers = detect_blockers([wi], [])
    assert not any(b.signal_id == "BL-1" for b in blockers)


# ---------------------------------------------------------------------------
# BL-2: Impediment flag is set
# ---------------------------------------------------------------------------


def test_bl2_impediment_flag_positive() -> None:
    wi = _base_work_item(is_flagged=True)
    blockers = detect_blockers([wi], [])
    assert any(b.signal_id == "BL-2" and b.blocker_type == "explicit" for b in blockers)


def test_bl2_impediment_flag_negative() -> None:
    wi = _base_work_item(is_flagged=False)
    blockers = detect_blockers([wi], [])
    assert not any(b.signal_id == "BL-2" for b in blockers)


# ---------------------------------------------------------------------------
# BL-3: Open is blocked by dependency exists
# ---------------------------------------------------------------------------


def test_bl3_dependency_positive() -> None:
    wi = _base_work_item(blocked_by=["AUTH-246"])
    blockers = detect_blockers([wi], [])
    assert any(
        b.signal_id == "BL-3" and b.blocker_type == "dependency" and "AUTH-246" in b.description
        for b in blockers
    )


def test_bl3_dependency_negative() -> None:
    wi = _base_work_item(blocked_by=[])
    blockers = detect_blockers([wi], [])
    assert not any(b.signal_id == "BL-3" for b in blockers)


# ---------------------------------------------------------------------------
# BL-4: Pull request changes_requested with no subsequent commit
# ---------------------------------------------------------------------------


def test_bl4_changes_requested_no_commit_positive() -> None:
    review_time = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    pr = _base_pr(review_state="changes_requested", last_review_at=review_time)
    # Commit happened BEFORE review
    commit = CommitOut(
        commit_id=1,
        sha="c1",
        repo_full_name="keshan-dev/headref",
        message_excerpt="old commit",
        branch_name="feature/AUTH-245-token",
        committed_at=review_time - timedelta(hours=2),
        source_url="https://github.com/keshan-dev/headref/commit/c1",
        retrieved_at=review_time,
    )
    blockers = detect_blockers([], [pr], commits=[commit])
    assert any(b.signal_id == "BL-4" and b.blocker_type == "review" for b in blockers)


def test_bl4_changes_requested_with_commit_negative() -> None:
    review_time = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    pr = _base_pr(review_state="changes_requested", last_review_at=review_time)
    # Commit happened AFTER review
    commit = CommitOut(
        commit_id=1,
        sha="c2",
        repo_full_name="keshan-dev/headref",
        message_excerpt="addressed review feedback",
        branch_name="feature/AUTH-245-token",
        committed_at=review_time + timedelta(hours=1),
        source_url="https://github.com/keshan-dev/headref/commit/c2",
        retrieved_at=review_time,
    )
    blockers = detect_blockers([], [pr], commits=[commit])
    assert not any(b.signal_id == "BL-4" for b in blockers)


# ---------------------------------------------------------------------------
# BL-5: Pull request open with no review > PR_REVIEW_WAIT_DAYS (3 days)
# ---------------------------------------------------------------------------


def test_bl5_open_unreviewed_positive() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    pr = _base_pr(
        review_state="none",
        is_draft=False,
        created_at_source=now - timedelta(days=4),
    )
    blockers = detect_blockers([], [pr], as_of=now)
    assert any(b.signal_id == "BL-5" and b.blocker_type == "review" for b in blockers)


def test_bl5_open_unreviewed_negative() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    # Opened only 1 day ago (under threshold)
    pr = _base_pr(
        review_state="none",
        is_draft=False,
        created_at_source=now - timedelta(days=1),
    )
    blockers = detect_blockers([], [pr], as_of=now)
    assert not any(b.signal_id == "BL-5" for b in blockers)


# ---------------------------------------------------------------------------
# BL-6: Draft pull request older than DRAFT_PR_STALE_DAYS (5 days)
# ---------------------------------------------------------------------------


def test_bl6_draft_stale_positive() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    pr = _base_pr(
        is_draft=True,
        created_at_source=now - timedelta(days=6),
    )
    blockers = detect_blockers([], [pr], as_of=now)
    assert any(b.signal_id == "BL-6" and b.blocker_type == "progress" for b in blockers)


def test_bl6_draft_stale_negative() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    # Draft opened 2 days ago (under threshold)
    pr = _base_pr(
        is_draft=True,
        created_at_source=now - timedelta(days=2),
    )
    blockers = detect_blockers([], [pr], as_of=now)
    assert not any(b.signal_id == "BL-6" for b in blockers)


# ---------------------------------------------------------------------------
# BL-7: CI checks failing on head commit
# ---------------------------------------------------------------------------


def test_bl7_checks_failing_positive() -> None:
    pr = _base_pr(checks_state="failing")
    blockers = detect_blockers([], [pr])
    assert any(b.signal_id == "BL-7" and b.blocker_type == "environment" for b in blockers)


def test_bl7_checks_failing_negative() -> None:
    pr = _base_pr(checks_state="passing")
    blockers = detect_blockers([], [pr])
    assert not any(b.signal_id == "BL-7" for b in blockers)


# ---------------------------------------------------------------------------
# BL-8: In progress with no code > ISSUE_NO_CODE_DAYS (3 days)
# ---------------------------------------------------------------------------


def test_bl8_in_progress_no_code_positive() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    wi = _base_work_item(
        status="in_progress",
        source_updated_at=now - timedelta(days=5),
    )
    # No pull requests or commits linked
    blockers = detect_blockers([wi], [], as_of=now)
    assert any(b.signal_id == "BL-8" and b.blocker_type == "progress" for b in blockers)


def test_bl8_in_progress_with_linked_code_negative() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    wi = _base_work_item(
        external_id="AUTH-245",
        status="in_progress",
        source_updated_at=now - timedelta(days=5),
    )
    # Linked PR exists matching branch name
    pr = _base_pr(branch_name="feature/AUTH-245-token")
    blockers = detect_blockers([wi], [pr], as_of=now)
    assert not any(b.signal_id == "BL-8" for b in blockers)
