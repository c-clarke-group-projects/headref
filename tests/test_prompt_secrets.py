"""P7-001 security gate, Developer 2 half: no secret reaches the model prompt.

Every secret setting is loaded with a distinctive canary value, 1 real agent pass runs
on fake tools with a mocked Ollama client, and the request body the client received is
searched for each canary. The request body is everything the model sees: the prompt
template, the findings text built from the evidence, and the call options.

The |safe half of this gate is test_no_safe_filter_in_templates in
tests/web/test_ui_templates.py. agent_run and outbound write calls are Developer 1's half.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import app.config
from app.agent.orchestrator import run_agent
from app.db import Base
from app.models.canonical import AppUser, Organization
from tests.test_orchestrator import NOW, _health, _pull_request, make_tools

# Every secret setting in app/config.py. The database password is included: DATABASE_URL
# carries it.
SECRET_SETTINGS = (
    "DATABASE_URL",
    "GITHUB_TOKEN",
    "JIRA_API_TOKEN",
    "GITHUB_OAUTH_CLIENT_SECRET",
    "GITHUB_APP_PRIVATE_KEY_B64",
    "ATLASSIAN_CLIENT_SECRET",
    "CREDENTIAL_ENCRYPTION_KEY",
    "SESSION_SECRET",
)

# The part of each value that must never appear: 1 distinctive value per setting, so any
# leak is unambiguous. Built from the name at runtime, not written as literals, so the
# secret scanner does not read them as real keys. The padding keeps SESSION_SECRET over
# its 32 character minimum. For DATABASE_URL this is the password, not the whole URL.
SEARCH_FOR = {name: f"canary-{name.lower()}-{'x' * 24}" for name in SECRET_SETTINGS}

CANARIES = {
    **SEARCH_FOR,
    "DATABASE_URL": (
        f"postgresql+psycopg://headref:{SEARCH_FOR['DATABASE_URL']}@localhost:5432/headref"
    ),
}


@pytest.fixture
def canary_secrets(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Load settings with canary secrets. conftest restores the real settings after."""
    for name, value in CANARIES.items():
        monkeypatch.setenv(name, value)
    app.config.settings = None
    loaded = app.config.get_settings()
    # Guard the test itself: if a canary did not load, a clean prompt proves nothing.
    assert loaded.github_token == CANARIES["GITHUB_TOKEN"]
    assert loaded.session_secret == CANARIES["SESSION_SECRET"]
    return SEARCH_FOR


def _model_reply() -> MagicMock:
    """An Ollama chat reply, as narrative.py reads it."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "message": {
            "content": json.dumps(
                {
                    "summary": "The contributor has 1 open pull request that is waiting for "
                    "review, and no other recorded activity in the window.",
                    "needs_attention": "Nothing needs attention.",
                    "attention_needed": False,
                }
            )
        },
        "eval_count": 30,
        "prompt_eval_count": 90,
    }
    response.raise_for_status = MagicMock()
    return response


async def _request_bodies_after_agent_pass() -> list[dict[str, Any]]:
    """Run 1 real agent pass on fake tools; return every body sent to the model client."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _model_reply()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session: Session = sessionmaker(bind=engine)()
    try:
        org = Organization(name="Test Org")
        session.add(org)
        session.flush()
        user = AppUser(organization_id=org.id, display_name="Isiwara")
        session.add(user)
        session.flush()
        pr = _pull_request(7)
        pr.author_user_id = user.id
        tools, _, _ = make_tools(health=[_health("jira"), _health("github")], pull_requests=[pr])

        await run_agent(
            session=session,
            subject_user_id=user.id,
            team_id=1,
            question_type="current_work",
            tools=tools,
            now=NOW,
            client=client,
        )
    finally:
        session.close()

    return [call.kwargs["json"] for call in client.post.call_args_list]


@pytest.mark.anyio
async def test_no_secret_reaches_the_model_prompt(canary_secrets: dict[str, str]) -> None:
    """No secret setting appears anywhere in what is sent to the model."""
    bodies = await _request_bodies_after_agent_pass()

    assert bodies, "the model client was never called, so nothing was tested"
    prompt = bodies[0]["messages"][0]["content"]
    assert "rotate refresh tokens" in prompt, "no findings in the prompt, so nothing was tested"

    for body in bodies:
        sent = json.dumps(body)
        leaked = [name for name, value in canary_secrets.items() if value in sent]
        assert leaked == [], f"secret setting(s) reached the model prompt: {leaked}"
