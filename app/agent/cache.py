"""Agent run persistence and insight cache (P4-007, Issue #31, FR-024, FR-031).

Provides stable evidence hashing, cache lookup/store for S6, and comprehensive
audit persistence into the agent_run table with token, latency, and dropped claims tracking.
"""

import hashlib
import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.agent import AgentRun
from app.models.agent import Insight as CachedInsightModel
from app.schemas.insight import MemberInsight

logger = logging.getLogger("headref.agent.cache")

DEFAULT_CACHE_TTL_SECONDS = 3600


def compute_evidence_hash(
    subject_user_id: int | str,
    question_type: str,
    evidence_items: Sequence[Any],
) -> str:
    """Compute a deterministic SHA-256 hash across subject, question, and sorted evidence.

    Serializes evidence items with sorted keys and ordered entity keys to guarantee
    cross-process stability.
    """
    serialized_items: list[dict[str, Any]] = []
    for item in evidence_items:
        if hasattr(item, "model_dump"):
            dumped = item.model_dump(mode="json")
        elif isinstance(item, dict):
            dumped = dict(item)
        else:
            dumped = {"data": str(item)}
        serialized_items.append(dumped)

    # Sort items by source and entity_key for deterministic serialization
    sorted_items = sorted(
        serialized_items,
        key=lambda x: (str(x.get("source", "")), str(x.get("entity_key", ""))),
    )
    serialized_json = json.dumps(sorted_items, sort_keys=True, default=str)
    raw_material = f"{subject_user_id}:{question_type}:{serialized_json}"
    return hashlib.sha256(raw_material.encode("utf-8")).hexdigest()


def get_cached_insight(
    session: Session,
    subject_user_id: int,
    question_type: str,
    evidence_hash: str,
) -> MemberInsight | None:
    """Look up cached insight by evidence hash. Returns None on cache miss or expiration."""
    try:
        now = datetime.now(UTC)
        entry = (
            session.query(CachedInsightModel)
            .filter(
                CachedInsightModel.subject_app_user_id == subject_user_id,
                CachedInsightModel.question_type == question_type,
                CachedInsightModel.evidence_hash == evidence_hash,
                CachedInsightModel.expires_at > now,
            )
            .first()
        )
        if entry and entry.payload:
            return MemberInsight.model_validate(entry.payload)
    except Exception as err:
        logger.warning("Cache lookup failed: %s. Continuing with fresh execution.", err)
    return None


def save_cached_insight(
    session: Session,
    subject_user_id: int,
    question_type: str,
    evidence_hash: str,
    insight: MemberInsight,
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> None:
    """Save or update cached insight entry in database."""
    try:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)
        payload = insight.model_dump(mode="json")

        entry = (
            session.query(CachedInsightModel)
            .filter(
                CachedInsightModel.subject_app_user_id == subject_user_id,
                CachedInsightModel.question_type == question_type,
                CachedInsightModel.evidence_hash == evidence_hash,
            )
            .first()
        )
        if entry:
            entry.payload = payload
            entry.expires_at = expires_at
        else:
            new_entry = CachedInsightModel(
                subject_app_user_id=subject_user_id,
                question_type=question_type,
                evidence_hash=evidence_hash,
                payload=payload,
                created_at=now,
                expires_at=expires_at,
            )
            session.add(new_entry)
        session.commit()
    except Exception as err:
        session.rollback()
        logger.warning("Saving insight cache failed: %s.", err)


def persist_agent_run(
    session: Session,
    subject_user_id: int,
    question_type: str,
    window_start: datetime,
    window_end: datetime,
    evidence_set: Sequence[Any],
    source_health: dict[str, Any] | list[Any],
    prompt_version: str = "narrative_v1",
    model: str = "llama3.2",
    raw_model_output: dict[str, Any] | None = None,
    validated_output: dict[str, Any] | None = None,
    dropped_claims: list[dict[str, Any]] | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
    actor_user_id: int | None = None,
    error_type: str | None = None,
) -> AgentRun | None:
    """Record agent execution to agent_run table for audit, cost, and replay (FR-024).

    Fail-safe: A failure to persist never fails the user request.
    """
    try:
        now = datetime.now(UTC)
        ev_list = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in evidence_set
        ]
        if isinstance(source_health, list):
            health_dict = {
                (h.source if hasattr(h, "source") else str(idx)): (
                    h.state if hasattr(h, "state") else str(h)
                )
                for idx, h in enumerate(source_health)
            }
        else:
            health_dict = dict(source_health)

        run = AgentRun(
            actor_app_user_id=actor_user_id,
            subject_app_user_id=subject_user_id,
            question_type=question_type,
            window_start=window_start,
            window_end=window_end,
            evidence_set=ev_list,
            source_health=health_dict,
            prompt_version=prompt_version,
            model=model,
            raw_model_output=raw_model_output,
            validated_output=validated_output,
            dropped_claims=dropped_claims,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            error_type=error_type,
            created_at=now,
        )
        session.add(run)
        session.commit()
        return run
    except Exception as err:
        session.rollback()
        logger.error("Failed to persist agent_run: %s.", err)
        return None
