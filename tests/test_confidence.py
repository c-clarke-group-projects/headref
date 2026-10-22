"""Unit tests for deterministic confidence engine (P4-004, Issue #28)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.agent.confidence import evaluate_confidence
from app.config import get_settings
from app.schemas.insight import EvidenceItem


def _make_evidence(
    eid: str,
    source: str,
    entity_type: str = "work_item",
    entity_key: str = "AUTH-245",
    source_state: str = "fresh",
    observed_at: datetime | None = None,
) -> EvidenceItem:
    now = observed_at or datetime.now(UTC)
    return EvidenceItem(
        id=eid,
        source=source,  # "jira" or "github"
        entity_type=entity_type,
        entity_key=entity_key,
        source_url=f"https://example.com/{eid}",
        summary=f"Summary for {eid}",
        excerpt=None,
        observed_at=now,
        retrieved_at=now,
        source_state=source_state,
    )


# ---------------------------------------------------------------------------
# Base Levels Tests
# ---------------------------------------------------------------------------


def test_base_high_confidence() -> None:
    """HIGH: 3+ items, 2+ sources, 1+ authoritative, all fresh."""
    evs = [
        _make_evidence("ev_1", "jira", "work_item", "AUTH-245"),
        _make_evidence("ev_2", "github", "pull_request", "182"),
        _make_evidence("ev_3", "github", "commit", "c1"),
    ]
    conf = evaluate_confidence(evs, claim_type="work_item_assignment")
    assert conf == "HIGH"


def test_base_medium_confidence_two_items() -> None:
    """MEDIUM: 2 items from same source."""
    evs = [
        _make_evidence("ev_1", "github", "pull_request", "182"),
        _make_evidence("ev_2", "github", "commit", "c1"),
    ]
    conf = evaluate_confidence(evs, claim_type="pull_request_state")
    assert conf == "MEDIUM"


def test_base_medium_confidence_one_authoritative() -> None:
    """MEDIUM: exactly 1 item from authoritative source."""
    evs = [_make_evidence("ev_1", "jira", "work_item", "AUTH-245")]
    conf = evaluate_confidence(evs, claim_type="work_item_assignment")
    assert conf == "MEDIUM"


def test_base_low_confidence_one_non_authoritative() -> None:
    """LOW: exactly 1 item not from authoritative source."""
    evs = [_make_evidence("ev_1", "github", "commit", "c1")]
    # Jira is authoritative for work item assignment
    conf = evaluate_confidence(evs, claim_type="work_item_assignment")
    assert conf == "LOW"


def test_base_unknown_confidence_no_evidence() -> None:
    """UNKNOWN: empty evidence list."""
    conf = evaluate_confidence([], claim_type="general")
    assert conf == "UNKNOWN"


# ---------------------------------------------------------------------------
# The 7 Worked Examples from AI_BEHAVIOR.md 5.4
# ---------------------------------------------------------------------------


def test_worked_example_1_all_fresh() -> None:
    """Example 1: Jira assignment + branch activity + open pull request, all fresh -> HIGH."""
    evs = [
        _make_evidence("ev_1", "jira", "work_item", "AUTH-245", source_state="fresh"),
        _make_evidence("ev_2", "github", "pull_request", "182", source_state="fresh"),
        _make_evidence("ev_3", "github", "commit", "c1", source_state="fresh"),
    ]
    conf = evaluate_confidence(evs, claim_type="work_item_assignment")
    assert conf == "HIGH"


def test_worked_example_2_two_fresh() -> None:
    """Example 2: Jira assignment + open pull request, both fresh -> MEDIUM."""
    evs = [
        _make_evidence("ev_1", "jira", "work_item", "AUTH-245", source_state="fresh"),
        _make_evidence("ev_2", "github", "pull_request", "182", source_state="fresh"),
    ]
    conf = evaluate_confidence(evs, claim_type="work_item_assignment")
    assert conf == "MEDIUM"


def test_worked_example_3_jira_assignment_only() -> None:
    """Example 3: Jira assignment only -> MEDIUM (1 authoritative item)."""
    evs = [_make_evidence("ev_1", "jira", "work_item", "AUTH-245", source_state="fresh")]
    conf = evaluate_confidence(evs, claim_type="work_item_assignment")
    assert conf == "MEDIUM"


def test_worked_example_4_commit_activity_only() -> None:
    """Example 4: Commit activity only, no Jira record -> LOW."""
    evs = [_make_evidence("ev_1", "github", "commit", "c1", source_state="fresh")]
    conf = evaluate_confidence(evs, claim_type="work_item_assignment")
    assert conf == "LOW"


def test_worked_example_5_stale_source_drops_level() -> None:
    """Example 5: Jira assignment + PR, but Jira last synced 40 hours ago (stale) -> LOW."""
    evs = [
        _make_evidence("ev_1", "jira", "work_item", "AUTH-245", source_state="stale"),
        _make_evidence("ev_2", "github", "pull_request", "182", source_state="fresh"),
    ]
    # Base is MEDIUM (2 items, 1 auth), modifier 1 (stale) drops to LOW
    conf = evaluate_confidence(evs, claim_type="work_item_assignment")
    assert conf == "LOW"


def test_worked_example_6_conflict_drops_level() -> None:
    """Example 6: Jira assignment + merged PR while Jira in_progress -> MEDIUM dropped to LOW."""
    evs = [
        _make_evidence("ev_1", "jira", "work_item", "AUTH-245", source_state="fresh"),
        _make_evidence("ev_2", "github", "pull_request", "182", source_state="fresh"),
    ]
    # Base is MEDIUM, modifier 2 (conflict) drops to LOW
    conf = evaluate_confidence(
        evs,
        claim_type="work_item_assignment",
        has_unresolved_conflict=True,
    )
    assert conf == "LOW"


def test_worked_example_7_required_source_unavailable() -> None:
    """Example 7: Anything, but Jira is unavailable and question is blockers -> UNKNOWN."""
    evs = [
        _make_evidence("ev_1", "jira", "work_item", "AUTH-245", source_state="fresh"),
        _make_evidence("ev_2", "github", "pull_request", "182", source_state="fresh"),
        _make_evidence("ev_3", "github", "commit", "c1", source_state="fresh"),
    ]
    conf = evaluate_confidence(
        evs,
        claim_type="work_item_assignment",
        required_source_unavailable=True,
    )
    assert conf == "UNKNOWN"


# ---------------------------------------------------------------------------
# Additional Modifiers & Prohibited Behavior
# ---------------------------------------------------------------------------


def test_link_chain_medium_caps_high_at_medium() -> None:
    """Modifier 3: Link chain confidence MEDIUM caps HIGH confidence at MEDIUM."""
    evs = [
        _make_evidence("ev_1", "jira", "work_item", "AUTH-245"),
        _make_evidence("ev_2", "github", "pull_request", "182"),
        _make_evidence("ev_3", "github", "commit", "c1"),
    ]
    conf = evaluate_confidence(
        evs,
        claim_type="work_item_assignment",
        link_chain_confidence="MEDIUM",
    )
    assert conf == "MEDIUM"


def test_truncated_tool_result_drops_level() -> None:
    """Modifier 5: Truncated tool result drops confidence 1 level."""
    evs = [
        _make_evidence("ev_1", "jira", "work_item", "AUTH-245"),
        _make_evidence("ev_2", "github", "pull_request", "182"),
    ]
    # Base MEDIUM dropped to LOW by truncation
    conf = evaluate_confidence(evs, claim_type="work_item_assignment", is_truncated=True)
    assert conf == "LOW"


def test_model_supplied_confidence_strictly_ignored() -> None:
    """DEC-006: Model-supplied confidence is ignored and never alters the calculated confidence."""
    evs = [_make_evidence("ev_1", "github", "commit", "c1")]
    # Single non-authoritative item gives LOW
    conf = evaluate_confidence(
        evs,
        claim_type="work_item_assignment",
        model_supplied_confidence="HIGH",  # Should be completely discarded
    )
    assert conf == "LOW"


# ---------------------------------------------------------------------------
# Old evidence (P6-002, Issue #40)
# ---------------------------------------------------------------------------


def _old_evidence(count: int) -> list[EvidenceItem]:
    """Fresh-source evidence observed 1 day before the RECENT_ACTIVITY_DAYS cutoff."""
    old = datetime.now(UTC) - timedelta(days=get_settings().recent_activity_days + 1)
    items = [
        _make_evidence("ev_1", "jira", "work_item", "AUTH-245", observed_at=old),
        _make_evidence("ev_2", "github", "pull_request", "182", observed_at=old),
        _make_evidence("ev_3", "github", "commit", "c1", observed_at=old),
    ]
    return items[:count]


def test_single_old_non_authoritative_item_is_low() -> None:
    """LOW: 1 old commit, which is also a non-authoritative item for an assignment."""
    old = datetime.now(UTC) - timedelta(days=get_settings().recent_activity_days + 1)
    evs = [_make_evidence("ev_1", "github", "commit", "c1", observed_at=old)]
    assert evaluate_confidence(evs, claim_type="work_item_assignment") == "LOW"


@pytest.mark.parametrize("count", [1, 2, 3])
def test_all_evidence_older_than_recent_activity_days_is_low(count: int) -> None:
    """LOW: every evidence item is older than RECENT_ACTIVITY_DAYS."""
    assert evaluate_confidence(_old_evidence(count), claim_type="work_item_assignment") == "LOW"
