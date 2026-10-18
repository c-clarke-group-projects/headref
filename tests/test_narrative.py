"""Unit tests for narrative stage S4b (P4-005, Issue #29)."""

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.agent.narrative import generate_narrative


@pytest.mark.anyio
async def test_narrative_success() -> None:
    """Test successful narrative generation with schema validation and metric capture."""
    valid_content = json.dumps(
        {
            "summary": "The contributor is working on authentication token rotation "
            "under AUTH-245. Linked pull request #182 is actively passing "
            "continuous integration checks.",
            "needs_attention": "No blockers or critical risks require immediate attention.",
            "attention_needed": False,
        }
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {"content": valid_content},
        "eval_count": 42,
        "prompt_eval_count": 120,
    }
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = mock_resp

    result = await generate_narrative(
        findings_text="AUTH-245 in progress, PR #182 passing.",
        client=mock_client,
    )

    assert result.fallback_used is False
    assert "token rotation" in result.summary
    assert result.attention_needed is False
    assert result.eval_count == 42
    assert result.prompt_eval_count == 120

    # Assert AC-3: No tools are passed in payload
    call_args = mock_client.post.call_args
    payload = call_args.kwargs["json"]
    assert "tools" not in payload
    assert payload["options"]["temperature"] == 0
    assert payload["options"]["seed"] == 42


@pytest.mark.anyio
async def test_narrative_retry_on_schema_failure() -> None:
    """Test retry once when attempt 1 produces invalid schema, succeeded by attempt 2."""
    invalid_content = json.dumps({"summary": "too short"})
    valid_content = json.dumps(
        {
            "summary": "Valid summary describing the ongoing ticket work in exactly two sentences. "
            "Continuous integration status is green across all test pipelines.",
            "needs_attention": "Pull request #182 has changes requested.",
            "attention_needed": True,
        }
    )

    resp_invalid = MagicMock()
    resp_invalid.status_code = 200
    resp_invalid.json.return_value = {"message": {"content": invalid_content}}
    resp_invalid.raise_for_status = MagicMock()

    resp_valid = MagicMock()
    resp_valid.status_code = 200
    resp_valid.json.return_value = {
        "message": {"content": valid_content},
        "eval_count": 30,
        "prompt_eval_count": 90,
    }
    resp_valid.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.side_effect = [resp_invalid, resp_valid]

    result = await generate_narrative(
        findings_text="Findings text",
        client=mock_client,
    )

    assert mock_client.post.call_count == 2
    assert result.fallback_used is False
    assert result.attention_needed is True


@pytest.mark.anyio
async def test_narrative_fallback_on_double_failure() -> None:
    """Test fallback when two successive attempts fail schema validation."""
    invalid_content = json.dumps({"summary": "short"})
    resp_invalid = MagicMock()
    resp_invalid.status_code = 200
    resp_invalid.json.return_value = {"message": {"content": invalid_content}}
    resp_invalid.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.side_effect = [resp_invalid, resp_invalid]

    result = await generate_narrative(
        findings_text="Findings text",
        fallback_text="Deterministic fallback text for member.",
        client=mock_client,
    )

    assert mock_client.post.call_count == 2
    assert result.fallback_used is True
    assert result.error_type == "SCHEMA_INVALID"
    assert "Deterministic fallback text for member." in result.summary


@pytest.mark.anyio
async def test_narrative_timeout_retries_once_then_falls_back() -> None:
    """A model timeout is retried once, then the deterministic summary is used (EV-12 B)."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.side_effect = httpx.ReadTimeout("model too slow")

    result = await generate_narrative(
        findings_text="Findings text",
        fallback_text="Fallback summary.",
        client=mock_client,
    )

    assert mock_client.post.call_count == 2
    assert result.fallback_used is True
    assert result.error_type == "TIMEOUT"
    assert result.summary == "Fallback summary."


@pytest.mark.anyio
async def test_narrative_upstream_error_falls_back() -> None:
    """A 500 from Ollama falls back with UPSTREAM_ERROR instead of raising."""
    resp_error = MagicMock()
    resp_error.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500", request=httpx.Request("POST", "http://ollama"), response=httpx.Response(500)
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.return_value = resp_error

    result = await generate_narrative(
        findings_text="Findings text",
        fallback_text="Fallback summary.",
        client=mock_client,
    )

    assert mock_client.post.call_count == 2
    assert result.fallback_used is True
    assert result.error_type == "UPSTREAM_ERROR"


@pytest.mark.anyio
async def test_narrative_connection_refused_no_retry() -> None:
    """Test that connection error falls back immediately without retrying."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.side_effect = httpx.ConnectError("Connection refused")

    result = await generate_narrative(
        findings_text="Findings text",
        fallback_text="Fallback summary.",
        client=mock_client,
    )

    # Must only call once: do not retry on connection refusal per implementation notes
    assert mock_client.post.call_count == 1
    assert result.fallback_used is True
    assert result.error_type == "UPSTREAM_ERROR"
    assert result.summary == "Fallback summary."
