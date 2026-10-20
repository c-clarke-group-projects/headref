"""Pytest configuration and environment fixtures for headref."""

import os
from collections.abc import Generator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

# Ensure dummy configuration secrets exist so app.config can load in test environments.
# An empty value counts as unset: docker compose passes the blank lines of .env through
# as empty variables, which would otherwise make a local run differ from CI.
_TEST_ENV = {
    "DATABASE_URL": "postgresql+psycopg://headref:headref@localhost:5432/headref",
    "GITHUB_TOKEN": "test-github-token",
    "JIRA_BASE_URL": "https://example.atlassian.net",
    "JIRA_EMAIL": "test@example.com",
    "JIRA_API_TOKEN": "test-jira-token",
    "SCHEDULER_ENABLED": "false",
}
for _key, _value in _TEST_ENV.items():
    if not os.environ.get(_key):
        os.environ[_key] = _value


@pytest.fixture(autouse=True)
def _restore_configuration() -> Generator[None, None, None]:
    """Undo any change a test makes to the environment or the settings singleton."""
    import app.config

    saved_env = dict(os.environ)
    saved_settings = app.config.settings
    yield
    os.environ.clear()
    os.environ.update(saved_env)
    app.config.settings = saved_settings


def log_in(client: TestClient, user_id: int) -> None:
    """Sign a test client in as user_id, as GitHub OAuth does after a real sign-in.

    Sets the signed session cookie directly. There is no login route to call: the
    P3-001 stub that trusted any user id was removed.
    """
    from app.web.auth import SESSION_COOKIE, sign_session

    # TestClient serves http://testserver, which the cookie jar files under
    # "testserver.local". Using that domain lets the app's logout delete this cookie.
    client.cookies.set(SESSION_COOKIE, sign_session(user_id), domain="testserver.local")


def iter_api_routes(application: FastAPI) -> Iterator[tuple[str, Any]]:
    """Yield (full path, dependant) for every API route, including included routers.

    FastAPI 0.143 keeps an included router as 1 ``_IncludedRouter`` entry in
    ``app.routes``, so a plain ``isinstance(route, APIRoute)`` scan sees none of the
    routes inside it and a coverage check passes without checking anything. Older
    versions flatten them into APIRoute entries. Both layouts are handled.
    """
    for route in application.routes:
        if isinstance(route, APIRoute):
            yield route.path, route.dependant
        elif hasattr(route, "effective_candidates"):
            for candidate in route.effective_candidates():
                if hasattr(candidate, "dependant"):
                    yield candidate.path, candidate.dependant
