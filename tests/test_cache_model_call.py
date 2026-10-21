"""A cache hit makes no model call (P7-002, Issue #45).

run_agent runs twice against the same in-memory database with a mocked Ollama client.
The second run on the same evidence must be answered from the insight cache without
calling the model. Changed evidence has a different hash and must call the model again.
"""

import json
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.orchestrator import run_agent
from app.db import Base
from app.models.agent import AgentRun
from app.models.canonical import AppUser, Organization
from tests.test_orchestrator import NOW, _health, _pull_request, make_tools

SUMMARY = "This member is rotating refresh tokens in an open pull request this week."


def _model_client() -> AsyncMock:
    """An Ollama client that always returns the same valid narrative."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "message": {
            "content": json.dumps(
                {
                    "summary": SUMMARY,
                    "needs_attention": "Nothing needs attention.",
                    "attention_needed": False,
                }
            )
        },
        "eval_count": 40,
        "prompt_eval_count": 100,
    }
    response.raise_for_status = MagicMock()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = response
    return client


@pytest.fixture
def session_and_user() -> Iterator[tuple[Session, int]]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        org = Organization(name="Test Org")
        session.add(org)
        session.flush()
        user = AppUser(organization_id=org.id, display_name="Keshan")
        session.add(user)
        session.flush()
        yield session, user.id
    finally:
        session.close()


def _tools(user_id: int, number: int = 182) -> Any:
    pr = _pull_request(7)
    pr.author_user_id = user_id
    pr.number = number
    tools, _, _ = make_tools(health=[_health("jira"), _health("github")], pull_requests=[pr])
    return tools


async def _ask(session: Session, user_id: int, tools: Any, client: AsyncMock) -> Any:
    return await run_agent(
        session=session,
        subject_user_id=user_id,
        team_id=1,
        question_type="current_work",
        tools=tools,
        now=NOW,
        client=client,
    )


@pytest.mark.anyio
async def test_a_cache_hit_makes_no_model_call(session_and_user: tuple[Session, int]) -> None:
    session, user_id = session_and_user
    client = _model_client()
    tools = _tools(user_id)

    first = await _ask(session, user_id, tools, client)
    second = await _ask(session, user_id, tools, client)

    assert client.post.await_count == 1
    assert first.fallback_used is False
    assert first.summary == SUMMARY
    assert second.model_dump() == first.model_dump()

    # Both runs are recorded. The cached run has no model output and no token counts.
    runs = session.scalars(select(AgentRun).order_by(AgentRun.id)).all()
    assert len(runs) == 2
    assert runs[0].raw_model_output is not None
    assert runs[1].raw_model_output is None
    assert runs[1].output_tokens == 0


@pytest.mark.anyio
async def test_changed_evidence_calls_the_model_again(
    session_and_user: tuple[Session, int],
) -> None:
    session, user_id = session_and_user
    client = _model_client()

    await _ask(session, user_id, _tools(user_id), client)
    await _ask(session, user_id, _tools(user_id, number=183), client)

    assert client.post.await_count == 2
