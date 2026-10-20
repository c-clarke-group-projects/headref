"""Tests for the session cookie and who is asking (P3-001, DEC-026).

No test here touches a database. Users are plain in-memory objects and the database
dependency is replaced with a stub. Workspace authorization is tested in
tests/test_workspaces.py and tests/test_isolation.py.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as real_app
from app.web import auth
from app.web.auth import (
    auth_router,
    cookie_secure,
    read_session,
    sign_session,
)
from tests.conftest import iter_api_routes, log_in


@dataclass
class FakeUser:
    """Stand-in for an app_user row."""

    id: int
    team_id: int | None
    display_name: str = "Test"
    is_active: bool = True


USERS = {
    1: FakeUser(id=1, team_id=10, display_name="Alice"),
    2: FakeUser(id=2, team_id=10, display_name="Bob"),
    3: FakeUser(id=3, team_id=20, display_name="Carol"),
    4: FakeUser(id=4, team_id=None, display_name="Dana"),
    5: FakeUser(id=5, team_id=10, display_name="Erin", is_active=False),
}


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def member_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """A test app with the auth routes and no database."""
    monkeypatch.setattr(auth, "load_user", lambda db, user_id: USERS.get(user_id))

    def fake_db() -> Iterator[None]:
        yield None

    application = FastAPI()
    application.include_router(auth_router)
    application.dependency_overrides[auth.get_db] = fake_db

    return application


@pytest.fixture
def client(member_app: FastAPI) -> TestClient:
    """A test client for member_app. It keeps cookies between requests."""
    return TestClient(member_app)


def _log_in(client: TestClient, user_id: int) -> None:
    log_in(client, user_id)


# ---------------------------------------------------------------------------
# Session cookie
# ---------------------------------------------------------------------------


def test_session_round_trip() -> None:
    """A signed cookie reads back as the same user id."""
    assert read_session(sign_session(42)) == 42


def test_session_rejects_a_changed_user_id() -> None:
    """Editing the id while keeping the old signature is rejected."""
    _, signature = sign_session(1).split(".")
    assert read_session(f"2.{signature}") is None


@pytest.mark.parametrize("token", [None, "", "abc", "1", "1.", "x.y", "1.\u00e9"])
def test_session_rejects_garbage(token: str | None) -> None:
    """Missing, malformed or non-ASCII values return None and never raise."""
    assert read_session(token) is None


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/auth/login", {"json": {"user_id": 1}}),
        ("/login", {"data": {"user_id": "1", "next": "/"}}),
    ],
)
def test_the_login_stubs_are_gone(path: str, body: dict[str, Any]) -> None:
    """A user id alone must never sign anyone in. GitHub OAuth is the only way in."""

    def no_db() -> Iterator[None]:
        yield None

    real_app.dependency_overrides[auth.get_db] = no_db
    try:
        with TestClient(real_app) as client:
            response = client.post(path, follow_redirects=False, **body)

            assert response.status_code in (404, 405)
            assert auth.SESSION_COOKIE not in response.cookies
            assert client.get("/api/auth/me").status_code == 401
    finally:
        real_app.dependency_overrides.clear()


def test_a_signed_session_identifies_the_user(client: TestClient) -> None:
    """The cookie GitHub OAuth sets is what /me reads."""
    _log_in(client, 1)

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {"user_id": 1, "display_name": "Alice", "team_id": 10}


def test_a_session_for_an_unknown_user_is_401(client: TestClient) -> None:
    _log_in(client, 999)
    assert client.get("/api/auth/me").status_code == 401


def test_a_session_for_an_inactive_user_is_401(client: TestClient) -> None:
    _log_in(client, 5)
    assert client.get("/api/auth/me").status_code == 401


def test_session_secret_signs_the_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    """With SESSION_SECRET set, a cookie survives a restart (a new signing key load)."""
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    import app.config

    monkeypatch.setattr(app.config, "settings", None)
    monkeypatch.setattr(auth, "_signing_key", None)
    token = sign_session(7)

    monkeypatch.setattr(auth, "_signing_key", None)  # as after a restart
    assert read_session(token) == 7


def test_a_short_session_secret_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "too-short")
    import app.config

    monkeypatch.setattr(app.config, "settings", None)
    monkeypatch.setattr(auth, "_signing_key", None)
    with pytest.raises(RuntimeError, match="at least 32"):
        sign_session(1)


def test_me_without_a_session_is_401(client: TestClient) -> None:
    """No cookie means not logged in."""
    assert client.get("/api/auth/me").status_code == 401


def test_a_forged_cookie_is_rejected(client: TestClient) -> None:
    """A cookie with a made-up signature does not log anyone in."""
    client.cookies.set(auth.SESSION_COOKIE, "1.deadbeef")

    assert client.get("/api/auth/me").status_code == 401


def test_logout_ends_the_session(client: TestClient) -> None:
    """After logout the cookie is gone and /me answers 401."""
    _log_in(client, 1)
    assert client.get("/api/auth/me").status_code == 200

    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# What W10 removed (DEC-026)
# ---------------------------------------------------------------------------


def test_the_team_pages_and_team_api_are_gone() -> None:
    """Member and team data is served only under /w/{workspace_id} now."""
    paths = [p for p, _ in iter_api_routes(real_app)]
    assert paths, "the route scan saw nothing"
    legacy = ("/api/members", "/api/teams", "/teams", "/members", "/admin", "/repos", "/jira")
    assert [p for p in paths if p.startswith(legacy)] == []


@pytest.mark.parametrize(("env", "secure"), [("local", False), ("production", True)])
def test_cookies_are_secure_outside_local(
    monkeypatch: pytest.MonkeyPatch, env: str, secure: bool
) -> None:
    monkeypatch.setenv("APP_ENV", env)
    import app.config

    monkeypatch.setattr(app.config, "settings", None)

    assert cookie_secure() is secure
