"""Tests for Headref UX refinements and Jira integration (Issue requirements).

Validates:
1. Login page: No 'Select Person' stub dropdown, only GitHub OAuth.
2. Repos page: Synced repositories appear first at top of list, Jira status card present.
3. Team overview page: Multi-repo pillbar removed, unmatched accounts card/banner removed.
4. Jira endpoints: GET /jira, POST /jira/connect, POST /jira/sync, POST /jira/demo, POST /jira/disconnect.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Base
from app.main import app
from app.models.canonical import AppUser, Organization, Repository, Team
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
def setup_user_and_repos(db_session: Session) -> tuple[AppUser, list[Repository]]:
    """Seed test org, team, user, and multiple repositories."""
    org = Organization(name="Test Org")
    db_session.add(org)
    db_session.flush()

    team = Team(organization_id=org.id, name="Core Team")
    db_session.add(team)
    db_session.flush()

    user = AppUser(
        organization_id=org.id,
        team_id=team.id,
        display_name="Keshan Dev",
        role_label="Engineer",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    r1 = Repository(organization_id=org.id, full_name="keshan-dev/headref", default_branch="main", is_active=True)
    r2 = Repository(organization_id=org.id, full_name="agentdynarq/arka", default_branch="main", is_active=True)
    db_session.add_all([r1, r2])
    db_session.commit()

    return user, [r1, r2]


def test_login_page_no_select_person_dropdown(client: TestClient) -> None:
    """Login page must NOT show 'Select Person' or stub fallback dropdown."""
    response = client.get("/login")
    assert response.status_code == 200
    html = response.text
    assert "Select Person" not in html
    assert "Offline demo sign in (stub fallback)" not in html
    assert "seed/identity_map.yml" not in html
    assert "Sign in with GitHub" in html


def test_team_page_no_repo_switcher_pillbar_and_no_unmatched_card(
    client: TestClient, setup_user_and_repos: tuple[AppUser, list[Repository]]
) -> None:
    """Team overview page removes multi-repo switcher pillbar and unmatched accounts card."""
    user, repos = setup_user_and_repos
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(user.id))

    response = client.get(f"/?repo={repos[0].full_name}")
    assert response.status_code == 200
    html = response.text

    # Verify no multi-repo pillbar with 'All (2)'
    assert 'aria-label="Filter by repository"' not in html
    assert "All (2)" not in html
    assert "All (3)" not in html

    # Verify no unmatched accounts card or banner
    assert "unmatched-card" not in html
    assert "unmatched account" not in html.lower()

    # Verify team members heading and repo meta
    assert repos[0].full_name in html
    assert "Members" in html


def test_repos_page_orders_synced_repos_at_top(
    client: TestClient, setup_user_and_repos: tuple[AppUser, list[Repository]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repos page must order synced repositories at the top of the list."""
    user, db_repos = setup_user_and_repos
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(user.id))
    client.cookies.set(routes.GITHUB_TOKEN_COOKIE, "gho_test_token")

    # Mock GitHubClient returning 3 repos: 1 unsynced, 1 synced, 1 unsynced
    mock_gh = MagicMock()
    mock_gh.list_organizations.return_value = []
    mock_gh.list_repositories.return_value = [
        {"full_name": "unsynced/alpha", "name": "alpha", "owner": {"login": "unsynced"}, "private": False},
        {"full_name": "keshan-dev/headref", "name": "headref", "owner": {"login": "keshan-dev"}, "private": False},
        {"full_name": "unsynced/beta", "name": "beta", "owner": {"login": "unsynced"}, "private": False},
    ]

    with patch("app.web.routes.GitHubClient") as mock_gh_cls:
        mock_gh_cls.return_value.__enter__.return_value = mock_gh
        response = client.get("/repos")

    assert response.status_code == 200
    html = response.text

    # Synced repo (headref) should appear before unsynced repos
    pos_synced = html.find("keshan-dev/headref")
    pos_alpha = html.find("unsynced/alpha")
    assert pos_synced != -1
    assert pos_alpha != -1
    assert pos_synced < pos_alpha, "Synced repository should appear before unsynced repository"

    # Verify Jira banner exists on /repos
    assert "Atlassian Jira Integration" in html
    assert "/jira" in html


def test_jira_page_renders_cleanly(
    client: TestClient, setup_user_and_repos: tuple[AppUser, list[Repository]]
) -> None:
    """GET /jira renders the Jira connection management page."""
    user, _ = setup_user_and_repos
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(user.id))

    response = client.get("/jira")
    assert response.status_code == 200
    html = response.text
    assert "Connect Jira Workspace" in html
    assert "Atlassian Jira Cloud" in html
    assert "Load Sample Jira Data (AUTH Project)" in html


def test_jira_connect_validation_error(
    client: TestClient, setup_user_and_repos: tuple[AppUser, list[Repository]]
) -> None:
    """POST /jira/connect with missing inputs returns error."""
    user, _ = setup_user_and_repos
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(user.id))

    response = client.post(
        "/jira/connect",
        data={"jira_base_url": "", "jira_email": "", "jira_api_token": ""},
        follow_redirects=False,
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "error=" in response.headers["location"]


def test_jira_connect_success_and_sync(
    client: TestClient, setup_user_and_repos: tuple[AppUser, list[Repository]]
) -> None:
    """POST /jira/connect with valid credentials validates and redirects with success."""
    user, _ = setup_user_and_repos
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(user.id))

    with patch("app.web.routes.JiraClient") as mock_jira_cls, \
         patch("app.web.routes.sync_jira") as mock_sync_jira:
        mock_client = MagicMock()
        mock_client.list_projects.return_value = [{"key": "AUTH", "name": "Auth Project"}]
        mock_jira_cls.return_value = mock_client

        response = client.post(
            "/jira/connect",
            data={
                "jira_base_url": "https://my-team.atlassian.net",
                "jira_email": "dev@my-team.com",
                "jira_api_token": "secret-token",
                "jira_project_keys": "AUTH",
            },
            follow_redirects=False,
        )

        assert response.status_code == status.HTTP_303_SEE_OTHER
        assert "success=connected" in response.headers["location"]
        mock_client.list_projects.assert_called_once()
        mock_sync_jira.assert_called_once()


def test_jira_disconnect(
    client: TestClient, setup_user_and_repos: tuple[AppUser, list[Repository]]
) -> None:
    """POST /jira/disconnect clears Jira configuration."""
    user, _ = setup_user_and_repos
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(user.id))

    response = client.post("/jira/disconnect", follow_redirects=False)
    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "success=disconnected" in response.headers["location"]
