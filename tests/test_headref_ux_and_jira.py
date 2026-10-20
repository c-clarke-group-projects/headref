"""Tests for Headref UX refinements and Jira integration (Issue requirements).

Validates:
1. Login page: No 'Select Person' stub dropdown, only GitHub OAuth.
2. Jira credentials cannot be posted (connect and disconnect are removed). The team era
   Repos and Jira pages went in W10 (DEC-026); workspaces connect Jira per workspace.
"""

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.main import app
from app.models.canonical import AppUser, Organization, Repository, Team
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

    r1 = Repository(
        organization_id=org.id,
        full_name="keshan-dev/headref",
        default_branch="main",
        is_active=True,
    )
    r2 = Repository(
        organization_id=org.id, full_name="agentdynarq/arka", default_branch="main", is_active=True
    )
    db_session.add_all([r1, r2])
    db_session.commit()

    return user, [r1, r2]


def test_login_page_no_select_person_dropdown(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Login page must NOT show 'Select Person' or stub fallback dropdown."""
    cfg = Settings(
        database_url="sqlite:///:memory:",
        github_oauth_client_id="test-client-id",
        github_oauth_client_secret="test-client-secret",
        _env_file=None,
    )
    monkeypatch.setattr(routes, "get_settings", lambda: cfg)
    response = client.get("/login")
    assert response.status_code == 200
    html = response.text
    assert "Select Person" not in html
    assert "Offline demo sign in (stub fallback)" not in html
    assert "seed/identity_map.yml" not in html
    assert "Sign in with GitHub" in html


@pytest.mark.parametrize("path", ["/jira/connect", "/jira/disconnect"])
def test_jira_credentials_cannot_be_changed_over_http(
    client: TestClient,
    setup_user_and_repos: tuple[AppUser, list[Repository]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
) -> None:
    """Credentials come from the environment only (CLAUDE.md rule 3)."""
    user, _ = setup_user_and_repos
    client.cookies.set(auth.SESSION_COOKIE, auth.sign_session(user.id))
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("JIRA_API_TOKEN=original\n", encoding="utf-8")
    token_before = os.environ["JIRA_API_TOKEN"]

    response = client.post(
        path,
        data={
            "jira_base_url": "https://attacker.example",
            "jira_email": "x@attacker.example",
            "jira_api_token": "attacker-token",
        },
        follow_redirects=False,
    )

    assert response.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_405_METHOD_NOT_ALLOWED)
    assert os.environ["JIRA_API_TOKEN"] == token_before
    assert env_file.read_text(encoding="utf-8") == "JIRA_API_TOKEN=original\n"
