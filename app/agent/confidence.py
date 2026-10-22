"""Deterministic confidence rules engine (P4-004, Issue #28).

Assigns HIGH, MEDIUM, LOW, or UNKNOWN from resolved evidence per AI_BEHAVIOR.md 5.4.
Confidence is computed strictly by deterministic application logic. The model is never
asked for confidence, and any model-supplied confidence is discarded (DEC-006, DEC-018).
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.config import get_settings
from app.schemas.insight import EvidenceItem

ConfidenceLevel = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]

# Authoritative source mapping per DATA_AND_EVIDENCE.md 6.6
AUTHORITATIVE_SOURCES: dict[str, list[str]] = {
    "work_item_assignment": ["jira"],
    "work_item_status": ["jira"],
    "priority": ["jira"],
    "due_date": ["jira"],
    "declared_blocker": ["jira"],
    "issue_dependency": ["jira"],
    "pull_request_state": ["github"],
    "draft_status": ["github"],
    "review_state": ["github"],
    "commit_activity": ["github"],
    "branch_existence": ["github"],
    "check_status": ["github"],
    "current_work": ["jira", "github"],  # Requires both
    "general": ["jira", "github"],
}

_LEVEL_ORDER: list[ConfidenceLevel] = ["HIGH", "MEDIUM", "LOW", "UNKNOWN"]


def _drop_level(level: ConfidenceLevel) -> ConfidenceLevel:
    """Drop confidence by 1 level. Dropping below LOW produces UNKNOWN."""
    if level == "HIGH":
        return "MEDIUM"
    if level == "MEDIUM":
        return "LOW"
    return "UNKNOWN"


def evaluate_confidence(
    evidence_items: Sequence[EvidenceItem],
    claim_type: str = "general",
    has_unresolved_conflict: bool = False,
    link_chain_confidence: Literal["HIGH", "MEDIUM", "LOW"] | None = None,
    is_truncated: bool = False,
    required_source_unavailable: bool = False,
    model_supplied_confidence: str | None = None,  # Intentionally ignored per DEC-006
    as_of: datetime | None = None,
) -> ConfidenceLevel:
    """Compute confidence from resolved evidence and modifiers per AI_BEHAVIOR.md 5.4.

    Evaluates base level first, then applies the 5 ordered modifiers.
    Model-supplied confidence is discarded entirely.
    """
    settings = get_settings()
    now = as_of or datetime.now(UTC)

    # -------------------------------------------------------------------------
    # Gate: If a source required by the question is unavailable -> UNKNOWN
    # -------------------------------------------------------------------------
    if required_source_unavailable or not evidence_items:
        return "UNKNOWN"

    # -------------------------------------------------------------------------
    # Base Level Calculation (AI_BEHAVIOR.md 5.4 Base level)
    # -------------------------------------------------------------------------
    sources = {item.source for item in evidence_items}
    num_items = len(evidence_items)

    auth_sources = AUTHORITATIVE_SOURCES.get(claim_type, ["jira", "github"])
    has_auth_item = any(item.source in auth_sources for item in evidence_items)

    all_items_fresh = all(item.source_state == "fresh" for item in evidence_items)

    recent_cutoff = now - timedelta(days=settings.recent_activity_days)
    all_items_old = all(item.observed_at < recent_cutoff for item in evidence_items)

    # Determine Base Level. "All evidence old" is checked first: old evidence says nothing
    # about now, however much of it there is, so it is never MEDIUM or HIGH (#40).
    if all_items_old:
        level: ConfidenceLevel = "LOW"
    elif num_items >= 3 and len(sources) >= 2 and has_auth_item and all_items_fresh:
        level = "HIGH"
    elif num_items >= 2 or (num_items == 1 and has_auth_item):
        level = "MEDIUM"
    else:
        level = "LOW"

    # -------------------------------------------------------------------------
    # Ordered Modifiers (AI_BEHAVIOR.md 5.4 Modifiers)
    # -------------------------------------------------------------------------
    # Modifier 1: Any cited evidence comes from a stale source -> Drop 1 level
    has_stale_source = any(item.source_state == "stale" for item in evidence_items)
    if has_stale_source:
        level = _drop_level(level)

    # Modifier 2: An unresolved conflict affects this claim -> Drop 1 level
    if has_unresolved_conflict:
        level = _drop_level(level)

    # Modifier 3: The supporting work_item_link chain has confidence MEDIUM -> Cap at MEDIUM
    if link_chain_confidence == "MEDIUM" and level == "HIGH":
        level = "MEDIUM"

    # Modifier 4: A source required by question type is unavailable -> Force UNKNOWN
    if required_source_unavailable:
        return "UNKNOWN"

    # Modifier 5: truncated was true on any contributing tool result -> Drop 1 level
    if is_truncated:
        level = _drop_level(level)

    return level
