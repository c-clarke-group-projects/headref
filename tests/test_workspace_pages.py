"""Portfolio home, create workspace and the workspace shell pages (W2, DEC-021)."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import app
from app.models.workspace import Workspace, WorkspaceMember
from app.web import auth
from tests.conftest import log_in
from tests.test_workspaces import _account, _add_member, _workspace


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
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
    def override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[auth.get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


HTML = {"accept": "text/html"}


def test_the_home_page_needs_a_session(client):
    response = client.get("/workspaces", headers=HTML, follow_redirects=False)

    assert response.status_code in (302, 303, 307)
    assert "/login" in response.headers["location"]


def test_a_new_account_sees_the_empty_state(client, db_session):
    user, _ = _account(db_session, 101, "lead-a")
    log_in(client, user.id)

    html = client.get("/workspaces").text

    assert "No workspaces yet" in html
    assert 'href="/workspaces/new"' in html


def test_the_home_page_lists_only_my_workspaces(client, db_session):
    user_a, account_a = _account(db_session, 101, "lead-a")
    _, account_b = _account(db_session, 202, "lead-b")
    _workspace(db_session, account_a, "Alpha team")
    _workspace(db_session, account_b, "Bravo team")
    log_in(client, user_a.id)

    html = client.get("/workspaces").text

    assert "Alpha team" in html
    assert "Bravo team" not in html
    assert "Not synced yet" in html


def test_a_hostile_workspace_name_is_escaped(client, db_session):
    user, account = _account(db_session, 101, "lead-a")
    _workspace(db_session, account, "<img src=x onerror=alert(1)>")
    log_in(client, user.id)

    html = client.get("/workspaces").text

    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_the_form_creates_a_workspace_and_opens_it(client, db_session):
    user, account = _account(db_session, 101, "lead-a")
    log_in(client, user.id)

    response = client.post(
        "/workspaces",
        data={"name": "Payments", "description": "Card flows"},
        follow_redirects=False,
    )

    workspace = db_session.scalar(select(Workspace))
    assert response.status_code == 303
    assert response.headers["location"] == f"/w/{workspace.id}"
    member = db_session.scalar(select(WorkspaceMember))
    assert (member.account_id, member.role) == (account.id, "owner")


def test_a_blank_name_shows_the_error_and_creates_nothing(client, db_session):
    user, _ = _account(db_session, 101, "lead-a")
    log_in(client, user.id)

    response = client.post("/workspaces", data={"name": "   ", "description": "x"})

    assert response.status_code == 422
    assert "Give the workspace a name" in response.text
    assert db_session.scalar(select(Workspace)) is None


def test_another_accounts_workspace_page_is_404(client, db_session):
    _, account_a = _account(db_session, 101, "lead-a")
    user_b, _ = _account(db_session, 202, "lead-b")
    workspace = _workspace(db_session, account_a, "Secret project")
    log_in(client, user_b.id)

    response = client.get(f"/w/{workspace.id}", headers=HTML)

    assert response.status_code == 404
    assert "Secret project" not in response.text


def test_the_owner_can_connect_both_sources_and_sync_is_disabled(client, db_session):
    user, account = _account(db_session, 101, "lead-a")
    workspace = _workspace(db_session, account, "Payments")
    log_in(client, user.id)

    html = client.get(f"/w/{workspace.id}").text

    assert f'action="/w/{workspace.id}/connections/github/start"' in html
    assert f'action="/w/{workspace.id}/connections/jira/start"' in html
    assert "Sync now" in html
    assert "connect a repository or a Jira project first" in html
    # Sync is not built yet (W5): shown disabled, with the reason.
    assert "disabled" in html


def test_a_viewer_sees_no_connect_or_sync_buttons(client, db_session):
    _, owner = _account(db_session, 101, "lead-a")
    viewer_user, viewer = _account(db_session, 202, "viewer-b")
    workspace = _workspace(db_session, owner, "Payments")
    _add_member(db_session, workspace, viewer, "viewer")
    log_in(client, viewer_user.id)

    html = client.get(f"/w/{workspace.id}").text

    assert "Connect GitHub" not in html
    assert "Sync now" not in html
    assert "The workspace owner connects" in html


def test_the_switcher_lists_only_my_workspaces(client, db_session):
    user, account = _account(db_session, 101, "lead-a")
    _, other = _account(db_session, 202, "lead-b")
    first = _workspace(db_session, account, "Alpha team")
    _workspace(db_session, account, "Charlie team")
    _workspace(db_session, other, "Bravo team")
    log_in(client, user.id)

    html = client.get(f"/w/{first.id}").text

    assert "Switch workspace" in html
    assert "Charlie team" in html
    assert "Bravo team" not in html


def test_a_signed_out_form_post_returns_to_the_page_not_the_post_route(client, db_session):
    """After signing in, a redirect is a GET; a POST-only path would answer 405."""
    _, account = _account(db_session, 101, "lead-a")
    workspace = _workspace(db_session, account, "Payments")

    response = client.post(
        f"/w/{workspace.id}/connections/jira/start",
        headers={"referer": f"http://testserver/w/{workspace.id}"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next=/w/{workspace.id}"


def test_a_signed_out_post_from_another_site_returns_home(client):
    response = client.post(
        "/w/1/connections/jira/start",
        headers={"referer": "https://evil.example/w/1"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/login?next=/"


def test_a_signed_out_page_load_still_returns_to_that_page(client):
    response = client.get("/w/1?jira=saved", headers=HTML, follow_redirects=False)

    assert response.headers["location"] == "/login?next=/w/1?jira=saved"
