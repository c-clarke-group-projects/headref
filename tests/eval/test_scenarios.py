"""Evaluation scenarios EV-11 and EV-12 (P6-004, TESTING_AND_EVALUATION.md 11.2).

Each scenario runs the full pipeline, run_agent S1 to S6, against an in-memory database.
The read tools are the fakes from tests/test_orchestrator.py. The model is an
httpx.MockTransport standing in for Ollama, so narrative.py, the validator and the
agent_run write all run as they do in production.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.deterministic_summary import generate_deterministic_summary
from app.agent.orchestrator import run_agent
from app.db import Base
from app.models.agent import AgentRun
from app.models.canonical import AppUser, Organization
from app.schemas.insight import MemberInsight
from tests.test_orchestrator import NOW, _health, _pull_request, _review, _work_item, make_tools

# The findings name AUTH-1 and pull request #182. PAY-999 and #999 exist nowhere.
INVENTED_TICKET = "PAY-999"
INVENTED_PR = "#999"


def ollama_reply(content: str) -> dict[str, Any]:
    """An Ollama /api/chat reply carrying `content` as the message text."""
    return {
        "message": {"role": "assistant", "content": content},
        "done": True,
        "prompt_eval_count": 90,
        "eval_count": 40,
    }


def narrative(summary: str, needs_attention: str, attention_needed: bool = True) -> str:
    return json.dumps(
        {
            "summary": summary,
            "needs_attention": needs_attention,
            "attention_needed": attention_needed,
        }
    )


GROUNDED = narrative(
    "Working on AUTH-1, refresh token rotation, through pull request #182.",
    "Pull request #182 has changes requested.",
)


class FakeOllama:
    """Answers every model call through `handler` and counts the calls."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.calls = 0
        self._handler = handler

    def client(self) -> httpx.AsyncClient:
        def handle(request: httpx.Request) -> httpx.Response:
            self.calls += 1
            return self._handler(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def replying(content: str) -> FakeOllama:
    return FakeOllama(lambda request: httpx.Response(200, json=ollama_reply(content)))


def timing_out() -> FakeOllama:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("model too slow", request=request)

    return FakeOllama(handler)


async def run_scenario(model: FakeOllama) -> tuple[MemberInsight, AgentRun, str]:
    """Run current_work for 1 member on a fresh database.

    Returns the answer, its agent_run row and the deterministic fallback text built from
    the same data. A fresh database per run keeps the insight cache out of the result.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session: Session = sessionmaker(bind=engine)()
    try:
        org = Organization(name="Eval Org")
        session.add(org)
        session.flush()
        user = AppUser(organization_id=org.id, display_name="Member")
        session.add(user)
        session.flush()

        work_item = _work_item(1)
        work_item.assignee_user_id = user.id
        pull_request = _pull_request(7)
        pull_request.author_user_id = user.id
        pull_request.review_state = "changes_requested"
        reviews = [_review()]
        tools, _, _ = make_tools(
            health=[_health("jira"), _health("github")],
            work_items=[work_item],
            pull_requests=[pull_request],
            reviews=reviews,
        )

        async with model.client() as client:
            insight = await run_agent(
                session=session,
                subject_user_id=user.id,
                team_id=1,
                question_type="current_work",
                tools=tools,
                now=NOW,
                client=client,
            )
        run = session.scalars(select(AgentRun)).one()
        fallback_text = generate_deterministic_summary(
            [work_item], [pull_request], [], reviews, is_fallback=True
        ).text
        return insight, run, fallback_text
    finally:
        session.close()


def findings(insight: MemberInsight) -> dict[str, Any]:
    """The code-built part of the answer, which no narrative may change."""
    return insight.model_dump(
        include={"likely_current_work", "assigned", "blockers", "risks", "conflicts"}
    )


# ---------------------------------------------------------------------------
# EV-11 Invented entity in the narrative (DEC-018)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ev11_a_grounded_narrative_is_used() -> None:
    """Baseline for EV-11: the same data with a grounded narrative is not rejected."""
    insight, run, _ = await run_scenario(replying(GROUNDED))

    assert insight.fallback_used is False
    assert run.error_type is None
    assert run.dropped_claims == []


@pytest.mark.anyio
@pytest.mark.parametrize("invented", [INVENTED_TICKET, INVENTED_PR])
async def test_ev11_an_invented_entity_is_dropped_and_recorded(invented: str) -> None:
    clean, _, _ = await run_scenario(replying(GROUNDED))
    content = narrative(
        f"Working on AUTH-1 and on {invented}, which is not in the findings.",
        f"{invented} needs a review.",
    )
    model = replying(content)

    insight, run, fallback_text = await run_scenario(model)

    assert model.calls == 1
    assert insight.fallback_used is True
    assert insight.summary == fallback_text
    assert run.dropped_claims == [{"reason": "INVENTED_ENTITY", "entity": invented}]
    assert run.error_type == "INVENTED_ENTITY"
    assert invented not in insight.model_dump_json()
    assert findings(insight) == findings(clean)
    assert insight.blockers, "the scenario must carry a blocker for the comparison to mean much"


# ---------------------------------------------------------------------------
# EV-12 Invalid model output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("setup", "make_model", "error_type"),
    [
        # A: valid JSON that fails the NarrativeOutput schema, on both attempts.
        ("A", lambda: replying(json.dumps({"summary": "Too short."})), "SCHEMA_INVALID"),
        # B: the model call times out, on both attempts.
        ("B", timing_out, "TIMEOUT"),
        # C1: the model answers with refusal text instead of the JSON narrative.
        (
            "C1",
            lambda: replying("I am sorry, but I cannot help with that request."),
            "SCHEMA_INVALID",
        ),
    ],
)
async def test_ev12_invalid_output_retries_once_then_falls_back(
    setup: str, make_model: Callable[[], FakeOllama], error_type: str
) -> None:
    clean, _, _ = await run_scenario(replying(GROUNDED))
    model = make_model()

    insight, run, fallback_text = await run_scenario(model)

    assert model.calls == 2, f"EV-12 {setup}: 1 attempt and exactly 1 retry"
    assert insight.fallback_used is True
    assert insight.summary == fallback_text
    assert run.error_type == error_type
    assert findings(insight) == findings(clean)


@pytest.mark.anyio
async def test_ev12_c2_a_refusal_inside_valid_json_falls_back() -> None:
    content = narrative(
        "I am sorry, but I cannot help with that request.",
        "I cannot answer this question.",
        attention_needed=False,
    )

    insight, _, fallback_text = await run_scenario(replying(content))

    assert insight.fallback_used is True
    assert insight.summary == fallback_text
