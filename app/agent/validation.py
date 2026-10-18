"""Narrative validator stage S5 (P4-006, Issue #30).

Validates model-generated narrative against invented entities, forbidden language,
and shape constraints, falling back to deterministic summary on any failure (DEC-018, FR-022).
"""

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("headref.agent.validation")

# Forbidden person-judgment terms per AI_BEHAVIOR.md 5.11
FORBIDDEN_TERMS = [
    "lazy",
    "incompetent",
    "stupid",
    "negligent",
    "careless",
    "unprofessional",
    "failure",
    "useless",
    "bad",
    "behind",
    "slow",
    "slacking",
    "failing",
    "guilty",
    "responsible",
]

_ENTITY_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b|#\d+")


def extract_entities(text: str) -> set[str]:
    """Extract ticket keys (e.g. AUTH-245) and PR numbers (e.g. #182) from text."""
    if not text:
        return set()
    return set(_ENTITY_PATTERN.findall(text))


class ValidatedNarrative(BaseModel):
    """Result of narrative validation with fallback tracking."""

    summary: str
    needs_attention: str
    attention_needed: bool
    fallback_used: bool = False
    dropped_claims: list[dict[str, Any]] = Field(default_factory=list)


def validate_narrative(
    summary: str,
    needs_attention: str,
    attention_needed: bool,
    allowed_entities: set[str],
    fallback_text: str,
) -> ValidatedNarrative:
    """Validate narrative output against invented entities, forbidden terms, and bounds.

    Under DEC-018: A validation rejection discards the narrative and substitutes
    the deterministic summary without modifying underlying findings.
    """
    # Check 1: Shape and length constraints
    if not summary or len(summary.strip()) < 20 or not needs_attention:
        logger.warning("Narrative failed shape check. Using deterministic fallback.")
        return ValidatedNarrative(
            summary=fallback_text,
            needs_attention="See detailed findings below.",
            attention_needed=False,
            fallback_used=True,
            dropped_claims=[{"reason": "SHAPE_INVALID", "detail": "Output failed length bounds"}],
        )

    # Check 2: Invented entities (tickets and PR numbers must exist in findings)
    found_entities = extract_entities(summary) | extract_entities(needs_attention)
    invented = [e for e in found_entities if e not in allowed_entities]
    if invented:
        logger.warning(
            "Narrative cited invented entities: %s. Using deterministic fallback.", invented
        )
        return ValidatedNarrative(
            summary=fallback_text,
            needs_attention="See detailed findings below.",
            attention_needed=False,
            fallback_used=True,
            dropped_claims=[{"reason": "INVENTED_ENTITY", "entity": entity} for entity in invented],
        )

    # Check 3: Forbidden language (person-judgment terms)
    combined_lower = f"{summary} {needs_attention}".lower()
    matched_forbidden = []
    for term in FORBIDDEN_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", combined_lower):
            matched_forbidden.append(term)

    if matched_forbidden:
        logger.warning(
            "Narrative contained forbidden language: %s. Using deterministic fallback.",
            matched_forbidden,
        )
        return ValidatedNarrative(
            summary=fallback_text,
            needs_attention="See detailed findings below.",
            attention_needed=False,
            fallback_used=True,
            dropped_claims=[
                {"reason": "FORBIDDEN_LANGUAGE", "term": term} for term in matched_forbidden
            ],
        )

    # Narrative passed all checks
    return ValidatedNarrative(
        summary=summary,
        needs_attention=needs_attention,
        attention_needed=attention_needed,
        fallback_used=False,
        dropped_claims=[],
    )
