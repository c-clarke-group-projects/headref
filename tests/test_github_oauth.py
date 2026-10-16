"""Unit and integration tests for GitHub OAuth authentication flow."""

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
import respx
from fastapi import status
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.main import app
from app.models.canonical import AppUser, Organization, Team
from app.models.identity import IdentityLink
from app.web import auth, routes


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """Provide a TestClient with overridden get_db dependency."""

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[auth.get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def oauth_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Provide Settings with GitHub OAuth enabled."""
    cfg = Settings(
        database_url="sqlite:///:memory:",
        github_token="dummy-gh-token",
        jira_base_url="https://dummy.atlassian.net",
        jira_email="dummy@example.com",
        jira_api_token="dummy-jira-token",
        github_oauth_client_id="test-client-id",
        github_oauth_client_secret="test-client-secret",
        _env_file=None,
    )
    monkeypatch.setattr(routes, "get_settings", lambda: cfg)
    return cfg


def test_login_page_shows_oauth_button_when_configured(
    client: TestClient, oauth_settings: Settings
) -> None:
    """When GitHub OAuth is configured, login page renders 'Sign in with GitHub' button."""
    response = client.get("/login")
    assert response.status_code == 200
    assert "Sign in with GitHub" in response.text
    assert "/auth/github/login" in response.text


def test_login_page_shows_banner_when_unconfigured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When GitHub OAuth credentials are not set, page shows an unconfigured banner."""
    cfg = Settings(
        database_url="sqlite:///:memory:",
        github_token="dummy-gh-token",
        jira_base_url="https://dummy.atlassian.net",
        jira_email="dummy@example.com",
        jira_api_token="dummy-jira-token",
        github_oauth_client_id="",
        github_oauth_client_secret="",
        _env_file=None,
    )
    monkeypatch.setattr(routes, "get_settings", lambda: cfg)

    response = client.get("/login")
    assert response.status_code == 200
    assert "GitHub OAuth not configured" in response.text


def test_github_login_redirects_to_github(client: TestClient, oauth_settings: Settings) -> None:
    """GET /auth/github/login sets state cookie and redirects to GitHub OAuth authorize."""
    response = client.get("/auth/github/login?next=/teams/1", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER

    location = response.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=test-client-id" in location
    assert "scope=read%3Auser" in location
    assert "state=" in location

    cookies = response.cookies
    assert "argus_oauth_state" in cookies
    assert "argus_oauth_next" in cookies
    assert cookies["argus_oauth_next"] == "/teams/1"


def test_github_login_fails_when_unconfigured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /auth/github/login redirects to /login with error if OAuth is unconfigured."""
    cfg = Settings(
        database_url="sqlite:///:memory:",
        github_token="dummy-gh-token",
        jira_base_url="https://dummy.atlassian.net",
        jira_email="dummy@example.com",
        jira_api_token="dummy-jira-token",
        github_oauth_client_id="",
        github_oauth_client_secret="",
        _env_file=None,
    )
    monkeypatch.setattr(routes, "get_settings", lambda: cfg)

    response = client.get("/auth/github/login", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "/login?error=" in response.headers["location"]


def test_github_callback_handles_upstream_error(client: TestClient) -> None:
    """Callback redirects to /login with error if GitHub returned an error parameter."""
    response = client.get(
        "/auth/github/callback?error=access_denied&error_description=User+cancelled",
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "/login?error=" in response.headers["location"]
    assert "User+cancelled" in response.headers["location"]


def test_github_callback_rejects_csrf_mismatch(client: TestClient) -> None:
    """Callback rejects request if state parameter does not match state cookie."""
    client.cookies.set("argus_oauth_state", "expected-secret-state")
    response = client.get(
        "/auth/github/callback?code=mock_code&state=wrong-state", follow_redirects=False
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "CSRF" in response.headers["location"]


@respx.mock
def test_github_callback_unmapped_user(
    client: TestClient, db_session: Session, oauth_settings: Settings
) -> None:
    """Callback returns clear error when authenticated GitHub user is not in identity map."""
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=Response(200, json={"access_token": "gho_test_token_123"})
    )
    respx.get("https://api.github.com/user").mock(
        return_value=Response(
            200,
            json={"id": 9999999, "login": "stranger", "name": "Stranger Danger"},
        )
    )

    client.cookies.set("argus_oauth_state", "valid-state-123")
    response = client.get(
        "/auth/github/callback?code=test-code&state=valid-state-123",
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "/login?error=" in response.headers["location"]
    assert "stranger" in response.headers["location"]


@respx.mock
def test_github_callback_successful_login(
    client: TestClient, db_session: Session, oauth_settings: Settings
) -> None:
    """Mapped active user successfully signs in and receives an authenticated session cookie."""
    org = Organization(name="Demo Org")
    db_session.add(org)
    db_session.flush()

    team = Team(organization_id=org.id, name="Eng")
    db_session.add(team)
    db_session.flush()

    user = AppUser(
        organization_id=org.id,
        team_id=team.id,
        display_name="Keshan",
        role_label="Backend",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    link = IdentityLink(
        app_user_id=user.id,
        integration="github",
        external_id="219891474",
        external_handle="keshan-dev",
        match_method="manual",
        confidence="HIGH",
        created_at=datetime.now(UTC),
    )
    db_session.add(link)
    db_session.commit()

    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=Response(200, json={"access_token": "gho_valid_oauth_token"})
    )
    respx.get("https://api.github.com/user").mock(
        return_value=Response(
            200,
            json={"id": 219891474, "login": "keshan-dev", "name": "Keshan"},
        )
    )

    client.cookies.set("argus_oauth_state", "state_abc_456")
    client.cookies.set("argus_oauth_next", "/teams/1")

    response = client.get(
        "/auth/github/callback?code=valid-code-789&state=state_abc_456",
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/teams/1"

    # Session cookie must be set and state cookies cleared
    assert auth.SESSION_COOKIE in response.cookies
    assert auth.read_session(response.cookies[auth.SESSION_COOKIE]) == user.id
    assert "argus_oauth_state" not in response.cookies or response.cookies["argus_oauth_state"] == ""
