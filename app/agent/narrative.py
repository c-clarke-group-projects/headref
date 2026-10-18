"""Narrative stage S4b, the single local model call (P4-005, Issue #29).

Turns finished findings into 2 readable sentences with zero tool calls and zero
claim/confidence/evidence generation (DEC-001, DEC-018, FR-016, FR-022, FR-029).
"""

import json
import logging
import os
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings

logger = logging.getLogger("headref.agent.narrative")

# A local model on CPU took 22 seconds in the DEC-018 measurement, so this is well above
# the 10 second HTTP_TIMEOUT_SECONDS used for the GitHub and Jira APIs.
REQUEST_TIMEOUT_SECONDS = 30.0
ATTEMPTS = 2


def _default_ollama_url() -> str:
    return get_settings().ollama_url.rstrip("/") + "/api/chat"


def _default_model_id() -> str:
    return get_settings().model_id


FIXED_SEED = 42
MAX_PREDICT = 300


class NarrativeOutput(BaseModel):
    """Structured JSON schema enforced on Ollama response."""

    summary: str = Field(..., min_length=20, max_length=500)
    needs_attention: str = Field(..., min_length=5, max_length=300)
    attention_needed: bool = Field(...)


class NarrativeResult(BaseModel):
    """Result container including execution metrics and fallback state."""

    summary: str
    needs_attention: str
    attention_needed: bool
    eval_count: int = 0
    prompt_eval_count: int = 0
    latency_ms: int = 0
    fallback_used: bool = False
    error_type: str | None = None
    raw_response: dict[str, Any] | None = None


def load_prompt(findings_text: str) -> str:
    """Load the versioned prompt template and substitute findings data."""
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "narrative_v1.txt")
    if os.path.exists(prompt_path):
        with open(prompt_path, encoding="utf-8") as f:
            template = f.read()
        return template.replace("{findings}", findings_text)
    return (
        "Summarize these findings into 2 sentences and 1 attention line as JSON:\n"
        f"{findings_text}"
    )


def deterministic_fallback(
    fallback_text: str | None = None,
    latency_ms: int = 0,
    error_type: str | None = None,
) -> NarrativeResult:
    """Produce deterministic fallback result when model call fails or is unavailable."""
    summary = fallback_text or "Deterministic summary generated from recorded activity."
    return NarrativeResult(
        summary=summary,
        needs_attention="See detailed findings below.",
        attention_needed=False,
        eval_count=0,
        prompt_eval_count=0,
        latency_ms=latency_ms,
        fallback_used=True,
        error_type=error_type,
    )


async def generate_narrative(
    findings_text: str,
    fallback_text: str | None = None,
    model_id: str | None = None,
    ollama_url: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> NarrativeResult:
    """Generate narrative summary via 1 local Ollama call with schema and retry.

    Per DEC-018 and AC-3: pass NO tools, emit NO claims or evidence IDs.
    """
    url = ollama_url or _default_ollama_url()
    model = model_id or _default_model_id()
    prompt = load_prompt(findings_text)

    # Note: No 'tools' key in payload per AC-3
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {
            "temperature": 0,
            "seed": FIXED_SEED,
            "num_predict": MAX_PREDICT,
        },
        "format": NarrativeOutput.model_json_schema(),
    }

    start_time = time.perf_counter()
    error_type = "UPSTREAM_ERROR"

    should_close_client = False
    http_client = client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
        should_close_client = True

    try:
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = await http_client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()

                content = data.get("message", {}).get("content", "{}")
                parsed = json.loads(content)
                validated = NarrativeOutput(**parsed)

                latency_ms = int((time.perf_counter() - start_time) * 1000)
                return NarrativeResult(
                    summary=validated.summary,
                    needs_attention=validated.needs_attention,
                    attention_needed=validated.attention_needed,
                    eval_count=data.get("eval_count", 0),
                    prompt_eval_count=data.get("prompt_eval_count", 0),
                    latency_ms=latency_ms,
                    fallback_used=False,
                    raw_response=data,
                )
            except httpx.ConnectError:
                logger.error(
                    "Ollama is not running at %s. Start it with 'ollama serve' "
                    "and pull the model with 'ollama pull %s'.",
                    url,
                    model,
                )
                error_type = "UPSTREAM_ERROR"
                break
            except httpx.TimeoutException as err:
                error_type = "TIMEOUT"
                logger.warning("Narrative attempt %d of %d timed out: %s", attempt, ATTEMPTS, err)
            except (json.JSONDecodeError, ValidationError) as err:
                error_type = "SCHEMA_INVALID"
                logger.warning(
                    "Narrative attempt %d of %d failed schema validation: %s",
                    attempt,
                    ATTEMPTS,
                    err,
                )
            except (httpx.HTTPStatusError, httpx.TransportError) as err:
                error_type = "UPSTREAM_ERROR"
                logger.warning("Narrative attempt %d of %d failed: %s", attempt, ATTEMPTS, err)
    finally:
        if should_close_client:
            await http_client.aclose()

    latency_ms = int((time.perf_counter() - start_time) * 1000)
    logger.info("Falling back to deterministic summary (error_type=%s).", error_type)
    return deterministic_fallback(
        fallback_text=fallback_text, latency_ms=latency_ms, error_type=error_type
    )
