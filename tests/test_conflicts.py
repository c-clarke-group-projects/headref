"""Unit tests for conflict detection rules CF-1 through CF-4 (P4-002, Issue #26)."""

from datetime import UTC, datetime

from app.agent.conflicts import detect_conflicts
from app.schemas.tools import CommitOut, PullRequestOut, WorkItemOut


def _make_work_item(
    external_id: str = "AUTH-245",
    status: str = "in_progress",
    raw_status: str = "In Progress",
    is_flagged: bool = False,
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
        blocked_by=[],
        source_url=f"https://jira.example.com/{external_id}",
        source_updated_at=now,
        retrieved_at=now,
    )


def _make_pull_request(
    number: int = 182,
    state: str = "open",
    branch_name: str = "feature/AUTH-245-token",
    review_state: str = "none",
    checks_state: str = "passing",
) -> PullRequestOut:
    now = datetime.now(UTC)
    return PullRequestOut(
        pull_request_id=10,
        number=number,
        repo_full_name="keshan-dev/headref",
        title="feat: refresh token rotation",
        state=state,
        is_draft=False,
        branch_name=branch_name,
        review_state=review_state,
        checks_state=checks_state,
        created_at=now,
        source_updated_at=now,
        retrieved_at=now,
        source_url=f"https://github.com/keshan-dev/headref/pull/{number}",
    )


# ---------------------------------------------------------------------------
# CF-1: Jira in_progress vs GitHub PR merged
# ---------------------------------------------------------------------------


def test_cf1_in_progress_and_merged_positive() -> None:
    """CF-1 fires when Jira shows in_progress and linked PR is merged."""
    wi = _make_work_item(status="in_progress")
    pr = _make_pull_request(state="merged")

    conflicts = detect_conflicts([wi], [pr])
    assert len(conflicts) == 1
    cf = conflicts[0]
    assert cf.conflict_id == "CF-1"
    assert cf.work_item_external_id == "AUTH-245"
    expected = "Jira shows AUTH-245 In Progress, but linked pull request #182 is merged."
    assert expected in cf.description
    assert cf.evidence_keys == ["AUTH-245", "PR-182"]


def test_cf1_in_progress_and_open_negative() -> None:
    """CF-1 does not fire when Jira in_progress agrees with an open PR."""
    wi = _make_work_item(status="in_progress")
    pr = _make_pull_request(state="open")

    conflicts = detect_conflicts([wi], [pr])
    assert not any(c.conflict_id == "CF-1" for c in conflicts)


# ---------------------------------------------------------------------------
# CF-2: Jira done vs GitHub PR open
# ---------------------------------------------------------------------------


def test_cf2_done_and_open_positive() -> None:
    """CF-2 fires when Jira shows done and linked PR is still open."""
    wi = _make_work_item(status="done", raw_status="Done")
    pr = _make_pull_request(state="open")

    conflicts = detect_conflicts([wi], [pr])
    assert len(conflicts) == 1
    cf = conflicts[0]
    assert cf.conflict_id == "CF-2"
    assert cf.work_item_external_id == "AUTH-245"
    expected = "Jira shows AUTH-245 Done, but linked pull request #182 is still open."
    assert expected in cf.description


def test_cf2_done_and_merged_negative() -> None:
    """CF-2 does not fire when Jira done agrees with a merged PR."""
    wi = _make_work_item(status="done", raw_status="Done")
    pr = _make_pull_request(state="merged")

    conflicts = detect_conflicts([wi], [pr])
    assert not any(c.conflict_id == "CF-2" for c in conflicts)


# ---------------------------------------------------------------------------
# CF-3: Jira done with commits after transition
# ---------------------------------------------------------------------------


def test_cf3_done_with_subsequent_commits_positive() -> None:
    """CF-3 fires when commits on linked branch exist after Jira done transition."""
    done_time = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    commit_time = datetime(2026, 9, 12, 15, 30, tzinfo=UTC)

    wi = _make_work_item(status="done", raw_status="Done", source_updated_at=done_time)
    commit = CommitOut(
        commit_id=1,
        sha="c123456789",
        repo_full_name="keshan-dev/headref",
        message_excerpt="fix: extra cleanup on AUTH-245",
        branch_name="feature/AUTH-245-token",
        committed_at=commit_time,
        source_url="https://github.com/keshan-dev/headref/commit/c123456789",
        retrieved_at=commit_time,
    )

    conflicts = detect_conflicts([wi], [], commits=[commit])
    assert len(conflicts) == 1
    cf = conflicts[0]
    assert cf.conflict_id == "CF-3"
    expected = (
        "AUTH-245 was marked Done on 2026-09-10, but commits on its branch continued to 2026-09-12."
    )
    assert expected in cf.description


def test_cf3_done_with_prior_commits_negative() -> None:
    """CF-3 does not fire when commits are older than the Jira done transition."""
    done_time = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    commit_time = datetime(2026, 9, 10, 15, 30, tzinfo=UTC)

    wi = _make_work_item(status="done", raw_status="Done", source_updated_at=done_time)
    commit = CommitOut(
        commit_id=1,
        sha="c123456789",
        repo_full_name="keshan-dev/headref",
        message_excerpt="fix: extra cleanup on AUTH-245",
        branch_name="feature/AUTH-245-token",
        committed_at=commit_time,
        source_url="https://github.com/keshan-dev/headref/commit/c123456789",
        retrieved_at=commit_time,
    )

    conflicts = detect_conflicts([wi], [], commits=[commit])
    assert not any(c.conflict_id == "CF-3" for c in conflicts)


# ---------------------------------------------------------------------------
# CF-4: Jira blocked vs PR approved with passing checks
# ---------------------------------------------------------------------------


def test_cf4_blocked_and_approved_passing_positive() -> None:
    """CF-4 fires when Jira status is blocked but linked PR is approved with passing checks."""
    wi = _make_work_item(status="blocked", raw_status="Blocked", is_flagged=True)
    pr = _make_pull_request(state="open", review_state="approved", checks_state="passing")

    conflicts = detect_conflicts([wi], [pr])
    assert len(conflicts) == 1
    cf = conflicts[0]
    assert cf.conflict_id == "CF-4"
    expected = (
        "AUTH-245 is flagged blocked, but linked pull request #182 is approved with passing checks."
    )
    assert expected in cf.description


def test_cf4_blocked_and_failing_checks_negative() -> None:
    """CF-4 does not fire when PR has failing checks or changes requested."""
    wi = _make_work_item(status="blocked", raw_status="Blocked", is_flagged=True)
    pr = _make_pull_request(state="open", review_state="changes_requested", checks_state="failing")

    conflicts = detect_conflicts([wi], [pr])
    assert not any(c.conflict_id == "CF-4" for c in conflicts)


def test_conflicts_unlinked_pr_no_false_positive() -> None:
    """PR with an unrelated branch name and title produces zero conflicts for work item."""
    wi = _make_work_item(external_id="AUTH-245", status="in_progress")
    pr = _make_pull_request(number=99, branch_name="feature/PAY-100-billing", state="merged")

    conflicts = detect_conflicts([wi], [pr])
    assert len(conflicts) == 0
