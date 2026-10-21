"""End-to-end test: seed, ask, assert the full answer (P6-005, Issue #43).

An empty database is seeded through the real ingestion path (seed_demo_database runs the
Jira and GitHub sync over fixture transport, DEC-012). The agent then runs with the real
read tools, findings engine, narrative call, validator and persistence. Only Ollama is
replaced, at the HTTP layer, so the run needs no model, no key and no network.
"""

import asyncio
import json
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.agent.orchestrator import run_agent
from app.config import get_settings
from app.db import Base
from app.models.agent import AgentRun
from app.models.canonical import AppUser
from app.models.work import Commit, PullRequest, Review, WorkItem
from app.schemas.insight import Insight, MemberInsight
from seed.seed_demo import seed_demo_database

SUMMARY = "Keshan is working on the session validation seam and its open pull request."


def _mark_utc(target: Any, _context: Any) -> None:
    """Give loaded datetimes the UTC zone PostgreSQL returns and SQLite drops.

    The findings engine subtracts timezone-aware times. PostgreSQL timestamptz columns
    load aware, SQLite loads them naive, so without this the run fails on SQLite only.
    set_committed_value keeps the row clean, so nothing is written back.
    """
    for name, value in list(vars(target).items()):
        if isinstance(value, datetime) and value.tzinfo is None:
            set_committed_value(target, name, value.replace(tzinfo=UTC))


@pytest.fixture
def seeded() -> Generator[Session, None, None]:
    """An empty in-memory database, seeded through the real sync path."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    event.listen(Base, "load", _mark_utc, propagate=True)
    try:
        with Session(engine) as session:
            seed_demo_database(session)
            yield session
    finally:
        event.remove(Base, "load", _mark_utc)


def _ollama_reply() -> httpx.Response:
    narrative = {
        "summary": SUMMARY,
        "needs_attention": "Nothing needs attention.",
        "attention_needed": False,
    }
    return httpx.Response(
        200,
        json={
            "message": {"content": json.dumps(narrative)},
            "eval_count": 40,
            "prompt_eval_count": 90,
        },
    )


def _ask(session: Session, question_type: str) -> tuple[MemberInsight, respx.Route]:
    subject = session.scalar(select(AppUser).where(AppUser.display_name == "Keshan"))
    assert subject is not None
    url = get_settings().ollama_url.rstrip("/") + "/api/chat"
    with respx.mock(assert_all_mocked=True) as mock:
        route = mock.post(url).mock(return_value=_ollama_reply())
        insight = asyncio.run(
            run_agent(
                session,
                subject_user_id=subject.id,
                team_id=subject.team_id,
                question_type=question_type,
            )
        )
    return insight, route


def _claims(insight: MemberInsight) -> list[Insight]:
    current = [insight.likely_current_work] if insight.likely_current_work else []
    return current + insight.blockers + insight.risks


def _seeded_urls(session: Session) -> set[str]:
    urls: set[str] = set()
    for model in (WorkItem, PullRequest, Commit, Review):
        urls.update(session.scalars(select(model.source_url)).all())
    return urls


@pytest.mark.parametrize("question_type", ["current_work", "blockers", "risks"])
def test_seeded_database_answers_a_full_member_insight(seeded: Session, question_type: str) -> None:
    insight, route = _ask(seeded, question_type)

    # 1 model call, with no tools offered to the model (DEC-018).
    assert route.call_count == 1
    payload = json.loads(route.calls.last.request.content)
    assert "tools" not in payload

    # The model's narrative passed validation and is the summary shown.
    assert insight.fallback_used is False
    assert insight.summary == SUMMARY

    # A complete answer built from the seeded data.
    assert insight.likely_current_work is not None
    assert insight.assigned
    assert insight.blockers
    if question_type == "risks":
        assert insight.risks
    else:
        # The risks question does not call every activity tool, so it has no card.
        assert insight.activity is not None

    # Every claim carries evidence, and every evidence item resolves to a seeded record.
    seeded_urls = _seeded_urls(seeded)
    for claim in _claims(insight):
        assert claim.evidence, claim.claim
        for item in claim.evidence:
            assert item.source_url.startswith("https://")
            assert item.source_url in seeded_urls
            assert item.source_state == "fresh"
            assert item.retrieved_at is not None
            assert item.observed_at is not None

    # Freshness: both sources synced successfully in the seed run.
    assert set(insight.last_synced) == {"github", "jira"}
    assert all(moment is not None for moment in insight.last_synced.values())
    assert {h.source: h.state for h in insight.source_health} == {
        "github": "fresh",
        "jira": "fresh",
    }

    # The run is recorded, with nothing dropped and no error.
    run = seeded.scalars(select(AgentRun).order_by(AgentRun.id.desc())).first()
    assert run is not None
    assert run.question_type == question_type
    assert run.dropped_claims == []
    assert run.error_type is None
    assert run.evidence_set
