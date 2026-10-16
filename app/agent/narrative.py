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

logger = logging.getLogger("headref.agent.narrative")

OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/api/chat"
DEFAULT_MODEL_ID = os.getenv("MODEL_ID", "llama3.2")
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
    url = ollama_url or OLLAMA_URL
    model = model_id or DEFAULT_MODEL_ID
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
    attempts = 2

    should_close_client = False
    http_client = client
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=30.0)
        should_close_client = True

    try:
        for attempt in range(attempts):
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
                break
            except (httpx.HTTPStatusError, json.JSONDecodeError, ValidationError) as err:
                logger.warning("Narrative attempt %d failed: %s. Retrying...", attempt + 1, err)
                if attempt == attempts - 1:
                    break
    finally:
        if should_close_client:
            await http_client.aclose()

    latency_ms = int((time.perf_counter() - start_time) * 1000)
    logger.info("Falling back to deterministic summary (LLM unavailable or failed).")
    return deterministic_fallback(fallback_text=fallback_text, latency_ms=latency_ms)
