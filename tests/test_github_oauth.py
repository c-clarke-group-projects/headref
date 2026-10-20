"""Unit and integration tests for GitHub OAuth authentication flow."""

from collections.abc import Generator

import pytest
import respx
from fastapi import status
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.integrations.identity_resolver import resolve_actor
from app.main import app
from app.models.canonical import AppUser, Organization, Team
from app.models.identity import IdentityLink
from app.models.workspace import Account
from app.web import auth, routes
from app.web.workspaces import upsert_account
from app.workspace_sync import ensure_organization
from tests.test_workspaces import _account, _workspace


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
    response = client.get("/auth/github/login?next=/w/1", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER

    location = response.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=test-client-id" in location
    assert "state=" in location
    # Identity only: no scope means GitHub grants read access to public profile data.
    assert "scope=" not in location

    cookies = response.cookies
    assert "headref_oauth_state" in cookies
    assert "headref_oauth_next" in cookies
    # Starlette quotes a cookie value containing "/" and unquotes it again on read.
    assert cookies["headref_oauth_next"].strip('"') == "/w/1"


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
    assert response.headers["location"] == "/workspaces"
    assert auth.SESSION_COOKIE in response.cookies
    assert "gho_test_token_123" not in response.headers.get("set-cookie", "")

    user = db_session.query(AppUser).filter_by(display_name="Stranger Danger").first()
    assert user is not None
    assert user.is_active is True
    assert user.team_id is None
    assert user.organization.name == routes.ACCOUNTS_ORGANIZATION
    assert db_session.query(IdentityLink).count() == 0


def _sign_in(client: TestClient, gh_id: int, login: str, next_path: str | None = None):
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=Response(200, json={"access_token": f"gho_{gh_id}"})
    )
    respx.get("https://api.github.com/user").mock(
        return_value=Response(200, json={"id": gh_id, "login": login, "name": login})
    )
    client.cookies.clear()
    client.cookies.set("headref_oauth_state", "state-1")
    if next_path:
        client.cookies.set("headref_oauth_next", next_path)
    return client.get("/auth/github/callback?code=c&state=state-1", follow_redirects=False)


@respx.mock
def test_an_existing_account_keeps_its_user_and_returns_to_next(
    client: TestClient, db_session: Session, oauth_settings: Settings
) -> None:
    org = Organization(name="Demo Org")
    db_session.add(org)
    db_session.flush()
    user = AppUser(organization_id=org.id, display_name="Keshan", is_active=True)
    db_session.add(user)
    db_session.flush()
    upsert_account(db_session, {"id": 219891474, "login": "keshan-dev"}, user.id)
    db_session.commit()

    response = _sign_in(client, 219891474, "keshan-dev", "/workspaces/new")

    assert response.headers["location"] == "/workspaces/new"
    assert auth.read_session(response.cookies[auth.SESSION_COOKIE]) == user.id
    assert (
        "headref_oauth_state" not in response.cookies
        or response.cookies["headref_oauth_state"] == ""
    )


@respx.mock
def test_a_sign_in_never_becomes_a_person_of_a_workspace(
    client: TestClient, db_session: Session, oauth_settings: Settings
) -> None:
    """Isiwara's commits were synced into lead-a's workspace; then she signs in.

    Before W10 the callback matched her synced identity link, so her account acted as
    that workspace person, which the team pages then showed to any signed-in user.
    """
    _, owner = _account(db_session, 101, "lead-a")
    db_session.add(Team(organization_id=db_session.scalar(select(Organization.id)), name="Core"))
    workspace = _workspace(db_session, owner)
    org_id = ensure_organization(db_session, workspace)
    db_session.commit()
    person_id = resolve_actor(
        db_session, "github", "202", "isiwara", auto_create=True, organization_id=org_id
    )
    db_session.commit()

    _sign_in(client, 202, "isiwara")

    db_session.expire_all()
    account = db_session.scalar(select(Account).where(Account.github_user_id == 202))
    assert account.app_user_id != person_id
    assert db_session.get(AppUser, person_id).team_id is None


@respx.mock
def test_an_account_bound_to_a_workspace_person_is_moved_to_its_own_user(
    client: TestClient, db_session: Session, oauth_settings: Settings
) -> None:
    _, owner = _account(db_session, 101, "lead-a")
    workspace = _workspace(db_session, owner)
    org_id = ensure_organization(db_session, workspace)
    db_session.commit()
    person_id = resolve_actor(
        db_session, "github", "202", "isiwara", auto_create=True, organization_id=org_id
    )
    upsert_account(db_session, {"id": 202, "login": "isiwara"}, person_id)
    db_session.commit()

    response = _sign_in(client, 202, "isiwara")

    db_session.expire_all()
    account = db_session.scalar(select(Account).where(Account.github_user_id == 202))
    assert account.app_user_id != person_id
    assert auth.read_session(response.cookies[auth.SESSION_COOKIE]) == account.app_user_id


def test_a_stale_next_naming_a_post_only_route_goes_home() -> None:
    """A redirect is a GET; next=<POST-only path> would land on a 405."""
    from app.main import app as real_app
    from app.web.routes import is_openable_page

    assert is_openable_page(real_app, "/workspaces")
    assert is_openable_page(real_app, "/w/1?jira=saved")
    assert not is_openable_page(real_app, "/w/1/connections/jira/start")
    assert not is_openable_page(real_app, "/no/such/page")


@respx.mock
def test_a_workspace_named_like_the_accounts_organization_captures_nobody(
    client: TestClient, db_session: Session, oauth_settings: Settings
) -> None:
    """A workspace's organization takes the workspace's name, which anyone can choose."""
    _, owner = _account(db_session, 101, "lead-a")
    trap = _workspace(db_session, owner, routes.ACCOUNTS_ORGANIZATION)
    trap_org = ensure_organization(db_session, trap)
    db_session.commit()

    _sign_in(client, 555, "newcomer")

    account = db_session.scalar(select(Account).where(Account.github_user_id == 555))
    user = db_session.get(AppUser, account.app_user_id)
    assert user.organization_id != trap_org
    assert user.organization.name == routes.ACCOUNTS_ORGANIZATION
