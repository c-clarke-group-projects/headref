"""Tests for the login stub and the authorization seam (P3-001).

No test here touches a database. Users are plain in-memory objects and the database
dependency is replaced with a stub.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as real_app
from app.web import auth
from app.web.auth import (
    MemberGuard,
    auth_router,
    can_view_member,
    read_session,
    require_member_access,
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


def _depends_on(dependant: Any, target: Any) -> bool:
    """Return True if a route's dependency tree includes the target callable."""
    for dependency in dependant.dependencies:
        if dependency.call is target or _depends_on(dependency, target):
            return True
    return False


def _is_member_route(path: str) -> bool:
    """A member-data route is anything under /api/members/ or with {member_id}."""
    return path.startswith("/api/members/") or "{member_id}" in path


def unguarded_member_routes(application: FastAPI) -> list[str]:
    """Return the paths of member routes that do not use the access guard."""
    return [
        path
        for path, dependant in iter_api_routes(application)
        if _is_member_route(path) and not _depends_on(dependant, require_member_access)
    ]


@pytest.fixture
def member_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """A test app with the auth routes, one guarded member route, and no database."""
    monkeypatch.setattr(auth, "load_user", lambda db, user_id: USERS.get(user_id))

    def fake_db() -> Iterator[None]:
        yield None

    application = FastAPI()
    application.include_router(auth_router)
    application.dependency_overrides[auth.get_db] = fake_db

    @application.get("/api/members/{member_id}/insight")
    def member_insight(member: MemberGuard) -> dict[str, int]:
        return {"member_id": member.id}

    return application


@pytest.fixture
def client(member_app: FastAPI) -> TestClient:
    """A test client for member_app. It keeps cookies between requests."""
    return TestClient(member_app)


def _log_in(client: TestClient, user_id: int) -> None:
    log_in(client, user_id)


# ---------------------------------------------------------------------------
# can_view_member
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actor_id", "subject_id", "expected"),
    [
        (1, 2, True),  # same team
        (1, 1, True),  # yourself, when you are on a team
        (1, 3, False),  # different team
        (4, 4, False),  # two people with no team must NOT match (None == None)
        (4, 1, False),  # actor has no team
        (1, 4, False),  # subject has no team
    ],
)
def test_can_view_member(actor_id: int, subject_id: int, expected: bool) -> None:
    """The MVP rule is same team, and no team means no access."""
    assert can_view_member(USERS[actor_id], USERS[subject_id]) is expected


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
# The guard on member routes
# ---------------------------------------------------------------------------


def test_same_team_member_is_allowed(client: TestClient) -> None:
    """A colleague on the same team is visible."""
    _log_in(client, 1)

    response = client.get("/api/members/2/insight")

    assert response.status_code == 200
    assert response.json() == {"member_id": 2}


def test_cross_team_member_is_403_and_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """Someone on another team gets 403, and the denial is logged."""
    _log_in(client, 1)

    with caplog.at_level(logging.WARNING, logger="app.web.auth"):
        response = client.get("/api/members/3/insight")

    assert response.status_code == 403
    assert "access denied" in caplog.text
    assert "actor_id=1" in caplog.text
    assert "subject_id=3" in caplog.text


def test_member_route_without_login_is_401(client: TestClient) -> None:
    """Member data is never served to an anonymous request."""
    assert client.get("/api/members/2/insight").status_code == 401


def test_unknown_member_is_404(client: TestClient) -> None:
    """A member id that does not exist answers 404."""
    _log_in(client, 1)

    assert client.get("/api/members/999/insight").status_code == 404


def test_user_with_no_team_can_view_nobody(client: TestClient) -> None:
    """A user without a team is denied, including their own record."""
    _log_in(client, 4)

    assert client.get("/api/members/1/insight").status_code == 403
    assert client.get("/api/members/4/insight").status_code == 403


# ---------------------------------------------------------------------------
# Route coverage: no member route may skip the check
# ---------------------------------------------------------------------------


def test_checker_flags_member_routes_without_the_guard() -> None:
    """The coverage check really catches an unguarded route, so it is not vacuous."""
    leaky = FastAPI()

    @leaky.get("/api/members/{member_id}/insight")
    def by_member_id(member_id: int) -> dict[str, int]:
        return {"member_id": member_id}

    @leaky.get("/api/members/{id}/activity")
    def by_other_name() -> dict[str, str]:
        return {}

    assert unguarded_member_routes(leaky) == [
        "/api/members/{member_id}/insight",
        "/api/members/{id}/activity",
    ]


def test_checker_accepts_a_guarded_route(member_app: FastAPI) -> None:
    """A route that takes MemberGuard passes the coverage check."""
    assert unguarded_member_routes(member_app) == []


def test_no_member_route_in_the_real_app_bypasses_the_guard() -> None:
    """Every member-data route in the real application uses the access guard."""
    member_paths = [p for p, _ in iter_api_routes(real_app) if _is_member_route(p)]
    # Guards against a vacuous pass: this check once saw 0 routes (FastAPI 0.143).
    assert len(member_paths) >= 3, member_paths
    assert unguarded_member_routes(real_app) == []
