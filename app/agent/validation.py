"""Narrative validator stage S5 (P4-006, Issue #30).

Validates model-generated narrative against invented entities, forbidden language, refusals
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

# A model refusal written as valid JSON (EV-12 C2). The narrative describes work in the
# third person, so first-person refusal phrasing never belongs in it.
REFUSAL_PATTERN = re.compile(
    r"\b(?:i am sorry|i'm sorry|i apologi[sz]e"
    r"|i (?:cannot|can't|can not|am unable to|am not able to|won't|will not)"
    r" (?:help|assist|answer|provide|comply|do that|complete)"
    r"|as an ai)\b",
    re.IGNORECASE,
)


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
    findings_text: str = "",
) -> ValidatedNarrative:
    """Validate narrative output against invented entities, forbidden terms, and bounds.

    Under DEC-018: A validation rejection discards the narrative and substitutes
    the deterministic summary without modifying underlying findings.

    findings_text is the code-built text the model summarised. A forbidden term that
    already appears there describes work, not a person ("pull request #5 has failing
    CI checks"), so the narrative may repeat it. A term the model adds is rejected.
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
    findings_lower = findings_text.lower()
    matched_forbidden = []
    for term in FORBIDDEN_TERMS:
        pattern = rf"\b{re.escape(term)}\b"
        if re.search(pattern, combined_lower) and not re.search(pattern, findings_lower):
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

    # Check 4: A refusal is not a summary, even when it fits the schema (EV-12 C2)
    refusal = REFUSAL_PATTERN.search(f"{summary} {needs_attention}")
    if refusal:
        logger.warning("Narrative was a refusal. Using deterministic fallback.")
        return ValidatedNarrative(
            summary=fallback_text,
            needs_attention="See detailed findings below.",
            attention_needed=False,
            fallback_used=True,
            dropped_claims=[{"reason": "REFUSAL", "phrase": refusal.group(0)}],
        )

    # Narrative passed all checks
    return ValidatedNarrative(
        summary=summary,
        needs_attention=needs_attention,
        attention_needed=attention_needed,
        fallback_used=False,
        dropped_claims=[],
    )
