"""Unit tests for narrative validator stage S5 (P4-006, Issue #30)."""

import pytest

from app.agent.validation import validate_narrative


def test_validator_success() -> None:
    """Valid narrative mentioning only allowed entities passes without rejection."""
    summary = (
        "The contributor is working on authentication token rotation under AUTH-245. "
        "Work is actively progressing on linked pull request #182."
    )
    needs_attention = "No blockers or critical risks require immediate attention."
    allowed = {"AUTH-245", "#182"}
    fallback = "Fallback summary text."

    result = validate_narrative(
        summary=summary,
        needs_attention=needs_attention,
        attention_needed=False,
        allowed_entities=allowed,
        fallback_text=fallback,
    )

    assert result.fallback_used is False
    assert result.summary == summary
    assert result.dropped_claims == []


def test_validator_invented_ticket_rejected() -> None:
    """Narrative citing a ticket not in findings is rejected and recorded."""
    summary = (
        "The contributor is currently implementing payment webhook processing under PAY-999. "
        "Work is ongoing on linked pull request #182."
    )
    needs_attention = "No blockers identified."
    allowed = {"AUTH-245", "#182"}  # PAY-999 is invented
    fallback = "Fallback summary text."

    result = validate_narrative(
        summary=summary,
        needs_attention=needs_attention,
        attention_needed=False,
        allowed_entities=allowed,
        fallback_text=fallback,
    )

    assert result.fallback_used is True
    assert result.summary == fallback
    assert any(
        c.get("reason") == "INVENTED_ENTITY" and c.get("entity") == "PAY-999"
        for c in result.dropped_claims
    )


def test_validator_invented_pr_rejected() -> None:
    """Narrative citing a PR number not in findings is rejected and recorded."""
    summary = (
        "Work on AUTH-245 is nearing completion in pull request #999. "
        "Automated regression checks are running."
    )
    needs_attention = "No blockers identified."
    allowed = {"AUTH-245", "#182"}  # #999 is invented
    fallback = "Fallback summary text."

    result = validate_narrative(
        summary=summary,
        needs_attention=needs_attention,
        attention_needed=False,
        allowed_entities=allowed,
        fallback_text=fallback,
    )

    assert result.fallback_used is True
    assert result.summary == fallback
    assert any(
        c.get("reason") == "INVENTED_ENTITY" and c.get("entity") == "#999"
        for c in result.dropped_claims
    )


def test_validator_forbidden_language_rejected() -> None:
    """Narrative containing person-judgment words is rejected and recorded."""
    summary = (
        "The engineer is extremely lazy and falling behind on AUTH-245 tasks. "
        "Development progress is slower than expected."
    )
    needs_attention = "The engineer is behind on deliverable dates."
    allowed = {"AUTH-245"}
    fallback = "Fallback summary text."

    result = validate_narrative(
        summary=summary,
        needs_attention=needs_attention,
        attention_needed=True,
        allowed_entities=allowed,
        fallback_text=fallback,
    )

    assert result.fallback_used is True
    assert result.summary == fallback
    assert any(c.get("reason") == "FORBIDDEN_LANGUAGE" for c in result.dropped_claims)


def test_validator_allows_a_term_the_findings_already_use() -> None:
    """Repeating "failing checks" from a BL-7 finding is not a judgement of a person."""
    findings = "Blockers: Pull request #5 has failing CI checks."
    summary = "Work continues on HRS-9, but pull request #5 has failing CI checks to fix."

    result = validate_narrative(
        summary=summary,
        needs_attention="Pull request #5 checks are failing.",
        attention_needed=True,
        allowed_entities={"HRS-9", "#5"},
        fallback_text="Fallback summary text.",
        findings_text=findings,
    )

    assert result.fallback_used is False
    assert result.summary == summary


def test_validator_rejects_a_term_the_model_added() -> None:
    """The same word is still rejected when the findings never used it."""
    result = validate_narrative(
        summary="The engineer is failing to keep up with HRS-9 and pull request #5.",
        needs_attention="Pull request #5 needs review.",
        attention_needed=True,
        allowed_entities={"HRS-9", "#5"},
        fallback_text="Fallback summary text.",
        findings_text="Blockers: Pull request #5 has no review after 4 days.",
    )

    assert result.fallback_used is True
    assert any(c.get("term") == "failing" for c in result.dropped_claims)


def test_validator_shape_invalid_rejected() -> None:
    """Empty or too-short narrative fails shape check and falls back."""
    result = validate_narrative(
        summary="Too short",
        needs_attention="",
        attention_needed=False,
        allowed_entities={"AUTH-245"},
        fallback_text="Fallback summary text.",
    )

    assert result.fallback_used is True
    assert result.summary == "Fallback summary text."
    assert any(c.get("reason") == "SHAPE_INVALID" for c in result.dropped_claims)


@pytest.mark.parametrize(
    "summary",
    [
        "I am sorry, but I cannot help with that request.",
        "I'm sorry, I can't provide a summary of this person's work.",
        "As an AI, I am unable to assess what HRS-9 involves.",
        "I cannot answer questions about individual contributors.",
    ],
)
def test_validator_rejects_a_refusal_that_fits_the_schema(summary: str) -> None:
    """EV-12 C2: a refusal in valid JSON is not a summary; the fallback is used."""
    result = validate_narrative(
        summary=summary,
        needs_attention="Nothing to report.",
        attention_needed=False,
        allowed_entities={"HRS-9"},
        fallback_text="Fallback summary text.",
    )

    assert result.fallback_used is True
    assert result.summary == "Fallback summary text."
    assert [c["reason"] for c in result.dropped_claims] == ["REFUSAL"]


@pytest.mark.parametrize(
    "summary",
    [
        "Keshan is working on HRS-9, which is In Progress, with 1 open pull request.",
        "HRS-9 is blocked; the reviewer asked for changes and no commit has followed.",
        "The pull request cannot merge until its failing check passes, per the findings.",
    ],
)
def test_validator_keeps_a_third_person_summary(summary: str) -> None:
    """Ordinary summaries, including 'cannot merge', are not refusals."""
    result = validate_narrative(
        summary=summary,
        needs_attention="HRS-9 needs attention.",
        attention_needed=True,
        allowed_entities={"HRS-9"},
        fallback_text="Fallback summary text.",
        findings_text="HRS-9 In Progress. Pull request has a failing check and changes requested.",
    )

    assert result.fallback_used is False
    assert result.summary == summary
