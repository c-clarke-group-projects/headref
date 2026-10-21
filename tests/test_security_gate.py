"""P7-001 security gate, Developer 1 half: agent_run holds no secret, nothing writes upstream.

Developer 2 covers the prompt and the templates (no secret in the prompt, no |safe).
The remaining items are already tested elsewhere: no HTTP client under app/tools/
(test_tool_import_guard.py), no tools in the model call (test_narrative.py), no secret
in logs or sync error details (test_http_client.py, test_sync.py).
"""

import ast
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.config
from app.agent.orchestrator import run_agent
from app.db import Base
from app.models.agent import AgentRun
from app.models.canonical import AppUser, Organization
from tests.test_orchestrator import NOW, _health, _pull_request, make_tools

APP_DIR = Path(__file__).resolve().parents[1] / "app"

SECRET_SETTINGS = (
    "GITHUB_TOKEN",
    "JIRA_API_TOKEN",
    "GITHUB_OAUTH_CLIENT_SECRET",
    "GITHUB_APP_PRIVATE_KEY_B64",
    "ATLASSIAN_CLIENT_SECRET",
    "CREDENTIAL_ENCRYPTION_KEY",
    "SESSION_SECRET",
)
# 1 distinctive value per secret setting, so any leak is unambiguous. Built from the
# name, not written out: a literal that looks like a key makes the CI secret scanner
# (gitleaks) fail. The padding keeps SESSION_SECRET over its 32 character minimum.
CANARIES = {name: f"canary-{name.lower()}-{'x' * 24}" for name in SECRET_SETTINGS}


def _model_reply(content: dict[str, Any]) -> MagicMock:
    """An Ollama chat reply, as narrative.py reads it."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "message": {"content": json.dumps(content)},
        "eval_count": 30,
        "prompt_eval_count": 90,
    }
    response.raise_for_status = MagicMock()
    return response


async def _agent_runs_after(client: AsyncMock) -> list[AgentRun]:
    """Run 1 real agent pass on fake tools with this model client; return its rows."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session: Session = sessionmaker(bind=engine)()
    try:
        org = Organization(name="Test Org")
        session.add(org)
        session.flush()
        user = AppUser(organization_id=org.id, display_name="Keshan")
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
        return list(session.scalars(select(AgentRun)).all())
    finally:
        session.close()


def _row_text(run: AgentRun) -> str:
    """Every stored column of 1 agent_run row as 1 string."""
    values = {column.name: getattr(run, column.name) for column in AgentRun.__table__.columns}

    def _plain(value: Any) -> str:
        return value.isoformat() if isinstance(value, datetime) else str(value)

    return json.dumps(values, default=_plain)


@pytest.fixture
def canary_secrets(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Load settings with canary secrets. conftest restores the real settings after."""
    for name, value in CANARIES.items():
        monkeypatch.setenv(name, value)
    app.config.settings = None
    app.config.get_settings()
    return CANARIES


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["model_answered", "model_failed"])
async def test_agent_run_rows_hold_no_secret(canary_secrets: dict[str, str], outcome: str) -> None:
    """No secret reaches any agent_run column, on a model answer or a model failure."""
    client = AsyncMock(spec=httpx.AsyncClient)
    if outcome == "model_answered":
        client.post.return_value = _model_reply(
            {
                "summary": "The contributor has 1 open pull request that is waiting for "
                "review, and no other recorded activity in the window.",
                "needs_attention": "Nothing needs attention.",
                "attention_needed": False,
            }
        )
    else:
        # An error message that carries a secret must not be stored either.
        client.post.side_effect = httpx.ConnectError(
            f"refused, token={canary_secrets['GITHUB_TOKEN']}"
        )

    runs = await _agent_runs_after(client)

    assert runs, "the agent pass recorded no agent_run row"
    assert client.post.called, "the model client was never called, so nothing was tested"
    for run in runs:
        text = _row_text(run)
        leaked = [name for name, value in canary_secrets.items() if value in text]
        assert leaked == [], f"agent_run {run.id} holds {leaked}"


# Every outbound write-method call in app/. Each is a token exchange or the local model,
# never a write to GitHub or Jira data (AC-17, DEC-001).
ALLOWED_OUTBOUND_WRITES = {
    ("agent/narrative.py", "url"): "the local Ollama chat call (DEC-017)",
    ("integrations/atlassian.py", "TOKEN_URL"): "Atlassian OAuth code exchange and refresh",
    ("integrations/github_app.py", "'/login/oauth/access_token'"): "GitHub OAuth, org check",
    (
        "integrations/github_app.py",
        "f'/app/installations/{installation_id}/access_tokens'",
    ): "mints a read-only installation token",
    ("web/routes.py", "'https://github.com/login/oauth/access_token'"): "GitHub sign-in",
}
WRITE_METHODS = {"post", "put", "patch", "delete"}
# Receivers that are not HTTP clients: SQLAlchemy sessions delete rows.
ORM_RECEIVERS = {"session", "db"}


def _outbound_write_calls() -> set[tuple[str, str]]:
    """(file under app/, target expression) for each write-method call that is not a route."""
    found: set[tuple[str, str]] = set()
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        decorators = {id(d) for node in ast.walk(tree) for d in getattr(node, "decorator_list", [])}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if id(node) in decorators:
                continue  # @router.post(...) declares a route; it calls nothing
            receiver = ast.unparse(node.func.value)
            if receiver in ORM_RECEIVERS:
                continue
            name = node.func.attr
            if name in WRITE_METHODS and node.args:
                target = node.args[0]
            elif (
                name in ("request", "stream")
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and str(node.args[0].value).upper() != "GET"
            ):
                target = node.args[1]
            else:
                continue
            found.add((path.relative_to(APP_DIR).as_posix(), ast.unparse(target)))
    return found


def test_no_write_call_to_github_or_jira_data() -> None:
    """Every outbound POST, PUT, PATCH or DELETE is a known token or model endpoint."""
    unexpected = _outbound_write_calls() - set(ALLOWED_OUTBOUND_WRITES)

    assert unexpected == set(), (
        f"New write-method call(s) in app/: {sorted(unexpected)}. headref never writes to "
        "GitHub or Jira (AC-17). A token or model endpoint goes in ALLOWED_OUTBOUND_WRITES "
        "with its reason."
    )


def test_the_write_scan_finds_the_known_calls() -> None:
    """The scan itself works: it sees every allowed call, so an empty diff means something."""
    assert set(ALLOWED_OUTBOUND_WRITES) <= _outbound_write_calls()
