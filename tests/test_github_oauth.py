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
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.main import app
from app.models.canonical import AppUser, Organization, Team
from app.models.identity import IdentityLink
from app.web import auth, routes


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session.

    StaticPool and check_same_thread=False keep 1 connection, so the TestClient thread
    sees the same database as the test instead of a new empty one.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
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
    assert "repo" in location
    assert "read%3Aorg" in location
    assert "state=" in location

    cookies = response.cookies
    assert "headref_oauth_state" in cookies
    assert "headref_oauth_next" in cookies
    # Starlette quotes a cookie value containing "/" and unquotes it again on read.
    assert cookies["headref_oauth_next"].strip('"') == "/teams/1"


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
    client.cookies.set("headref_oauth_state", "expected-secret-state")
    response = client.get(
        "/auth/github/callback?code=mock_code&state=wrong-state", follow_redirects=False
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "CSRF" in response.headers["location"]


@respx.mock
def test_github_callback_auto_provisions_unmapped_user(
    client: TestClient, db_session: Session, oauth_settings: Settings
) -> None:
    """Callback auto-provisions AppUser and IdentityLink when GitHub user is not yet in map."""
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=Response(200, json={"access_token": "gho_test_token_123"})
    )
    respx.get("https://api.github.com/user").mock(
        return_value=Response(
            200,
            json={"id": 9999999, "login": "stranger", "name": "Stranger Danger"},
        )
    )

    client.cookies.set("headref_oauth_state", "valid-state-123")
    response = client.get(
        "/auth/github/callback?code=test-code&state=valid-state-123",
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/repos"
    assert auth.SESSION_COOKIE in response.cookies
    assert routes.GITHUB_TOKEN_COOKIE in response.cookies
    assert response.cookies[routes.GITHUB_TOKEN_COOKIE] == "gho_test_token_123"

    user = db_session.query(AppUser).filter_by(display_name="Stranger Danger").first()
    assert user is not None
    assert user.is_active is True
    link = db_session.query(IdentityLink).filter_by(external_id="9999999").first()
    assert link is not None
    assert link.app_user_id == user.id


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

    client.cookies.set("headref_oauth_state", "state_abc_456")
    client.cookies.set("headref_oauth_next", "/teams/1")

    response = client.get(
        "/auth/github/callback?code=valid-code-789&state=state_abc_456",
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/teams/1"

    # Session cookie must be set and state cookies cleared
    assert auth.SESSION_COOKIE in response.cookies
    assert routes.GITHUB_TOKEN_COOKIE in response.cookies
    assert response.cookies[routes.GITHUB_TOKEN_COOKIE] == "gho_valid_oauth_token"
    assert auth.read_session(response.cookies[auth.SESSION_COOKIE]) == user.id
    assert (
        "headref_oauth_state" not in response.cookies
        or response.cookies["headref_oauth_state"] == ""
    )

    # Sign-in creates the account, matched on the numeric GitHub id (DEC-021).
    from sqlalchemy import select

    from app.models.workspace import Account

    account = db_session.scalar(select(Account).where(Account.github_user_id == 219891474))
    assert account is not None
    assert account.github_login == "keshan-dev"
    assert account.app_user_id == user.id


def test_repos_page_requires_auth(client: TestClient) -> None:
    """Unauthenticated access to /repos redirects to /login."""
    response = client.get("/repos", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "/login" in response.headers["location"]


@respx.mock
def test_repos_page_renders_with_mocked_github(client: TestClient, db_session: Session) -> None:
    """Authenticated user visiting /repos sees their organizations and repositories."""
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
    db_session.commit()

    respx.get("https://api.github.com/user/orgs").mock(
        return_value=Response(
            200,
            json=[{"id": 1, "login": "my-cool-org", "description": "Cool Org"}],
        )
    )
    respx.get("https://api.github.com/user/repos").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": 101,
                    "name": "argus",
                    "full_name": "keshan-dev/argus",
                    "owner": {"login": "keshan-dev"},
                    "description": "Headref repository",
                    "private": False,
                    "stargazers_count": 5,
                    "language": "Python",
                }
            ],
        )
    )

    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(user.id))
    client.cookies.set(routes.GITHUB_TOKEN_COOKIE, "gho_dummy_token")

    response = client.get("/repos")
    assert response.status_code == 200
    assert "Available Repositories" in response.text
    assert "my-cool-org" in response.text
    assert "keshan-dev/argus" in response.text


def test_select_repo_triggers_sync_and_redirects(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /repos/select invokes sync_github and redirects with ?synced=true."""
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
    db_session.commit()

    sync_called = []

    def mock_sync_github(session, team_id, scope, gh_client, auto_create_contributors):
        sync_called.append(
            {
                "team_id": team_id,
                "scope": scope,
                "auto_create_contributors": auto_create_contributors,
            }
        )

    monkeypatch.setattr(routes, "sync_github", mock_sync_github)

    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(user.id))
    client.cookies.set(routes.GITHUB_TOKEN_COOKIE, "gho_dummy_token")

    response = client.post(
        "/repos/select",
        data={"repo_full_name": "keshan-dev/argus"},
        follow_redirects=False,
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "/?synced=true" in response.headers["location"]
    assert "keshan-dev%2Fargus" in response.headers["location"]
    assert len(sync_called) == 1
    assert sync_called[0]["scope"] == "keshan-dev/argus"
    assert sync_called[0]["auto_create_contributors"] is True


def test_select_repo_rejects_invalid_name(client: TestClient, db_session: Session) -> None:
    """POST /repos/select rejects invalid repository format."""
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
    db_session.commit()

    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(user.id))

    response = client.post(
        "/repos/select",
        data={"repo_full_name": "invalid_format_without_slash"},
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "/repos?error=" in response.headers["location"]
