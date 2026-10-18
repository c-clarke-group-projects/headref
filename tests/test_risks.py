"""Unit tests for deterministic risk detection rules RK-1 through RK-4 (P4-003, Issue #27)."""

import re
from datetime import UTC, datetime, timedelta

from app.agent.conflicts import ConflictFinding
from app.agent.risks import detect_risks
from app.schemas.tools import CommitOut, WorkItemOut


def _base_work_item(
    external_id: str = "AUTH-245",
    status: str = "in_progress",
    raw_status: str = "In Progress",
    priority: str = "Medium",
    due_date: datetime | None = None,
    source_updated_at: datetime | None = None,
) -> WorkItemOut:
    now = source_updated_at or datetime.now(UTC)
    return WorkItemOut(
        work_item_id=1,
        external_id=external_id,
        title="Refresh token rotation",
        status=status,
        raw_status=raw_status,
        priority=priority,
        due_date=due_date,
        is_flagged=False,
        blocked_by=[],
        source_url=f"https://jira.example.com/{external_id}",
        source_updated_at=now,
        retrieved_at=now,
    )


# ---------------------------------------------------------------------------
# RK-1: Due date within DUE_SOON_DAYS (3 days) and not done
# ---------------------------------------------------------------------------


def test_rk1_due_soon_positive() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    wi = _base_work_item(
        due_date=now + timedelta(days=2),
        status="in_progress",
    )
    risks = detect_risks([wi], as_of=now)
    assert any(
        r.signal_id == "RK-1" and "AUTH-245 is due in 2 day(s)" in r.description for r in risks
    )


def test_rk1_due_soon_negative() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    # Due in 10 days (well beyond threshold)
    wi_far = _base_work_item(due_date=now + timedelta(days=10))
    # Due soon but already done
    wi_done = _base_work_item(due_date=now + timedelta(days=1), status="done")
    # No due date
    wi_none = _base_work_item(due_date=None)

    risks = detect_risks([wi_far, wi_done, wi_none], as_of=now)
    assert not any(r.signal_id == "RK-1" for r in risks)


# ---------------------------------------------------------------------------
# RK-2: Status stuck > STATUS_STUCK_DAYS (5 days)
# ---------------------------------------------------------------------------


def test_rk2_status_stuck_positive() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    wi = _base_work_item(
        status="in_progress",
        raw_status="In Progress",
        source_updated_at=now - timedelta(days=7),
    )
    risks = detect_risks([wi], as_of=now)
    assert any(
        r.signal_id == "RK-2"
        and "AUTH-245 has been in status In Progress for 7 days" in r.description
        for r in risks
    )


def test_rk2_status_stuck_negative() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    # Updated 2 days ago
    wi_recent = _base_work_item(source_updated_at=now - timedelta(days=2))
    # Updated 10 days ago but already done
    wi_done = _base_work_item(status="done", source_updated_at=now - timedelta(days=10))

    risks = detect_risks([wi_recent, wi_done], as_of=now)
    assert not any(r.signal_id == "RK-2" for r in risks)


# ---------------------------------------------------------------------------
# RK-3: High priority item with no activity > NO_ACTIVITY_DAYS (7 days)
# ---------------------------------------------------------------------------


def test_rk3_high_priority_inactive_positive() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    wi = _base_work_item(
        priority="High",
        status="in_progress",
        source_updated_at=now - timedelta(days=9),
    )
    risks = detect_risks([wi], as_of=now)
    assert any(
        r.signal_id == "RK-3"
        and "High priority item AUTH-245 has had no recorded activity for 9 days" in r.description
        for r in risks
    )


def test_rk3_high_priority_inactive_negative() -> None:
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    # Low priority item inactive for 9 days (threshold only applies to High/Highest)
    wi_low = _base_work_item(
        priority="Low",
        status="in_progress",
        source_updated_at=now - timedelta(days=9),
    )
    # High priority item with recent commit 2 days ago
    wi_active = _base_work_item(
        external_id="AUTH-250",
        priority="High",
        status="in_progress",
        source_updated_at=now - timedelta(days=9),
    )
    commit = CommitOut(
        commit_id=1,
        sha="c1",
        repo_full_name="keshan-dev/headref",
        message_excerpt="commit on AUTH-250",
        branch_name="feature/AUTH-250",
        committed_at=now - timedelta(days=2),
        source_url="https://github.com/keshan-dev/headref/commit/c1",
        retrieved_at=now,
    )

    risks = detect_risks([wi_low, wi_active], commits=[commit], as_of=now)
    assert not any(r.signal_id == "RK-3" for r in risks)


# ---------------------------------------------------------------------------
# RK-4: Unresolved Jira / GitHub conflict exists
# ---------------------------------------------------------------------------


def test_rk4_unresolved_conflict_positive() -> None:
    cf = ConflictFinding(
        conflict_id="CF-1",
        description="Jira shows AUTH-245 In Progress, but linked pull request #182 is merged.",
        work_item_id=1,
        work_item_external_id="AUTH-245",
        target_type="pull_request",
        target_identifier="#182",
        evidence_keys=["AUTH-245", "PR-182"],
    )
    risks = detect_risks([], conflicts=[cf])
    assert len(risks) == 1
    assert risks[0].signal_id == "RK-4"
    assert "Unresolved cross-system conflict" in risks[0].description
    assert risks[0].entity_key == "AUTH-245"


def test_rk4_no_conflicts_negative() -> None:
    risks = detect_risks([], conflicts=[])
    assert not any(r.signal_id == "RK-4" for r in risks)


# ---------------------------------------------------------------------------
# Compliance with FR-021: Statements describe work items, NEVER people
# ---------------------------------------------------------------------------


def test_fr021_risk_descriptions_never_describe_person() -> None:
    """FR-021: Risk statements strictly describe work items and dates, never the person."""
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    wi1 = _base_work_item(external_id="AUTH-245", due_date=now + timedelta(days=1))
    wi2 = _base_work_item(external_id="AUTH-246", source_updated_at=now - timedelta(days=10))
    wi3 = _base_work_item(
        external_id="AUTH-247",
        priority="Highest",
        source_updated_at=now - timedelta(days=10),
    )

    risks = detect_risks([wi1, wi2, wi3], as_of=now)
    assert len(risks) >= 3

    forbidden_patterns = [
        re.compile(
            r"\b(he|she|they|you|i|we|developer|author|assignee|engineer|person)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(behind|slow|lazy|slacking|failing|guilty|responsible)\b",
            re.IGNORECASE,
        ),
    ]

    for r in risks:
        for pat in forbidden_patterns:
            assert not pat.search(
                r.description
            ), f"Risk description '{r.description}' violated FR-021"
