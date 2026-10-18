"""Connecting a workspace to GitHub: install, verify, pick, disconnect (W3, DEC-022)."""

import time
from collections.abc import Generator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.integrations.errors import NotFoundError, UpstreamError
from app.integrations.github_app import Installation
from app.main import app
from app.models.connections import GitHubInstallation, WorkspaceRepository
from app.web import auth, connections
from tests.conftest import log_in
from tests.test_workspaces import _account, _add_member, _workspace

REPOS = [
    {"id": 9001, "full_name": "lead-a/headref-sandbox", "private": True},
    {"id": 9002, "full_name": "lead-a/other", "private": False},
]


class FakeAppClient:
    """Stands in for GitHubAppClient. installations maps id to Installation."""

    def __init__(self) -> None:
        self.installations: dict[int, Installation] = {}
        self.repos: dict[int, list[dict[str, Any]]] = {}
        self.fail_repos = False

    def get_installation(self, installation_id: int) -> Installation:
        if installation_id not in self.installations:
            raise NotFoundError("not this app's installation", status_code=404)
        return self.installations[installation_id]

    def list_installation_repositories(self, installation_id: int) -> list[dict[str, Any]]:
        if self.fail_repos:
            raise UpstreamError("GitHub 502", status_code=502)
        return self.repos.get(installation_id, [])


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
def fake_app(monkeypatch: pytest.MonkeyPatch) -> FakeAppClient:
    fake = FakeAppClient()
    monkeypatch.setattr(connections, "get_app_client", lambda: fake)
    cfg = Settings(
        database_url="sqlite:///:memory:",
        github_app_id="123",
        github_app_slug="headref-test",
        github_app_private_key_b64="eA==",
        _env_file=None,
    )
    monkeypatch.setattr(connections, "get_settings", lambda: cfg)
    return fake


@pytest.fixture
def client(db_session: Session, fake_app: FakeAppClient) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[auth.get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _user_installation(account_id: int = 101, installation_id: int = 55) -> Installation:
    return Installation(
        installation_id=installation_id,
        account_id=account_id,
        account_login="lead-a",
        account_type="User",
        repository_selection="selected",
        suspended=False,
    )


def _start(client: TestClient, workspace_id: int) -> str:
    """Press Connect GitHub; return the state GitHub would send back."""
    response = client.post(f"/w/{workspace_id}/connections/github/start", follow_redirects=False)
    assert response.status_code == 303
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def _setup(client: TestClient, state: str, installation_id: int = 55) -> Any:
    client.cookies.set(connections.USER_TOKEN_COOKIE, "gho_user", domain="testserver.local")
    return client.get(
        f"/connections/github/setup?installation_id={installation_id}"
        f"&setup_action=install&state={state}",
        follow_redirects=False,
    )


def _owner_with_workspace(db: Session, client: TestClient) -> tuple[Any, Any]:
    user, account = _account(db, 101, "lead-a")
    workspace = _workspace(db, account, "Payments")
    log_in(client, user.id)
    return account, workspace


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------


def test_connect_sends_the_owner_to_the_app_install_page(client, db_session):
    _, workspace = _owner_with_workspace(db_session, client)

    response = client.post(f"/w/{workspace.id}/connections/github/start", follow_redirects=False)

    location = urlparse(response.headers["location"])
    assert (location.netloc, location.path) == (
        "github.com",
        "/apps/headref-test/installations/new",
    )
    assert connections.INSTALL_STATE_COOKIE in response.cookies


def test_a_manager_cannot_start_a_connection(client, db_session):
    _, owner = _account(db_session, 101, "lead-a")
    manager_user, manager = _account(db_session, 202, "manager-b")
    workspace = _workspace(db_session, owner)
    _add_member(db_session, workspace, manager, "manager")
    log_in(client, manager_user.id)

    response = client.post(f"/w/{workspace.id}/connections/github/start", follow_redirects=False)

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Setup callback
# ---------------------------------------------------------------------------


def test_a_verified_installation_is_attached(client, db_session, fake_app):
    account, workspace = _owner_with_workspace(db_session, client)
    fake_app.installations[55] = _user_installation(account_id=101)

    response = _setup(client, _start(client, workspace.id))

    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/w/{workspace.id}/connections/github")
    row = db_session.scalar(select(GitHubInstallation))
    assert (row.workspace_id, row.installation_id, row.connected_by_account_id) == (
        workspace.id,
        55,
        account.id,
    )


def test_someone_elses_installation_is_refused(client, db_session, fake_app):
    """Passing another user's installation_id must not expose their repositories."""
    _, workspace = _owner_with_workspace(db_session, client)
    fake_app.installations[77] = _user_installation(account_id=999, installation_id=77)

    response = _setup(client, _start(client, workspace.id), installation_id=77)

    assert "github=not_allowed" in response.headers["location"]
    assert db_session.scalar(select(GitHubInstallation)) is None


def test_an_org_installation_needs_an_org_admin(client, db_session, fake_app, monkeypatch):
    _, workspace = _owner_with_workspace(db_session, client)
    fake_app.installations[55] = Installation(55, 5000, "acme", "Organization", "all", False)
    seen: dict[str, Any] = {}

    def fake_attach(installation, user_token, github_user_id):
        seen.update(token=user_token, user=github_user_id)
        return False

    monkeypatch.setattr(connections, "user_can_attach", fake_attach)

    response = _setup(client, _start(client, workspace.id))

    assert seen == {"token": "gho_user", "user": 101}
    assert "github=not_allowed" in response.headers["location"]
    assert db_session.scalar(select(GitHubInstallation)) is None


@pytest.mark.parametrize("tamper", ["no_cookie", "other_value", "forged", "expired"])
def test_a_bad_state_attaches_nothing(client, db_session, fake_app, tamper):
    account, workspace = _owner_with_workspace(db_session, client)
    fake_app.installations[55] = _user_installation(account_id=101)
    state = _start(client, workspace.id)
    if tamper == "no_cookie":
        client.cookies.delete(connections.INSTALL_STATE_COOKIE)
    elif tamper == "other_value":
        state = auth.sign_value(f"{workspace.id}.{account.id}.{int(time.time())}.other")
    elif tamper == "forged":
        state = f"{workspace.id}.{account.id}.{int(time.time())}.x~deadbeef"
        client.cookies.set(connections.INSTALL_STATE_COOKIE, state, domain="testserver.local")
    elif tamper == "expired":
        old = int(time.time()) - connections.INSTALL_STATE_MAX_AGE_SECONDS - 1
        state = auth.sign_value(f"{workspace.id}.{account.id}.{old}.n")
        client.cookies.set(connections.INSTALL_STATE_COOKIE, state, domain="testserver.local")

    response = _setup(client, state)

    assert "github=bad_state" in response.headers["location"]
    assert db_session.scalar(select(GitHubInstallation)) is None


def test_a_state_made_for_another_account_is_refused(client, db_session, fake_app):
    _, workspace = _owner_with_workspace(db_session, client)
    fake_app.installations[55] = _user_installation(account_id=101)
    state = auth.sign_value(f"{workspace.id}.999.{int(time.time())}.n")
    client.cookies.set(connections.INSTALL_STATE_COOKIE, state, domain="testserver.local")

    response = _setup(client, state)

    assert "github=bad_state" in response.headers["location"]


def test_an_owner_demoted_mid_flow_cannot_finish(client, db_session, fake_app):
    account, workspace = _owner_with_workspace(db_session, client)
    fake_app.installations[55] = _user_installation(account_id=101)
    state = _start(client, workspace.id)
    member = workspace.members[0]
    member.role = "manager"
    db_session.commit()

    response = _setup(client, state)

    assert "github=not_owner" in response.headers["location"]
    assert db_session.scalar(select(GitHubInstallation)) is None


def test_an_installation_github_does_not_know_is_not_found(client, db_session):
    _, workspace = _owner_with_workspace(db_session, client)

    response = _setup(client, _start(client, workspace.id), installation_id=404)

    assert "github=not_found" in response.headers["location"]


def test_without_a_github_sign_in_token_the_user_must_sign_in_again(client, db_session, fake_app):
    _, workspace = _owner_with_workspace(db_session, client)
    fake_app.installations[55] = _user_installation(account_id=101)
    state = _start(client, workspace.id)

    response = client.get(
        f"/connections/github/setup?installation_id=55&setup_action=install&state={state}",
        follow_redirects=False,
    )

    assert "github=sign_in_again" in response.headers["location"]


# ---------------------------------------------------------------------------
# Repository picker
# ---------------------------------------------------------------------------


def _connected(db: Session, client: TestClient, fake: FakeAppClient) -> tuple[Any, Any, Any]:
    account, workspace = _owner_with_workspace(db, client)
    inst = GitHubInstallation(
        workspace_id=workspace.id, installation_id=55, account_login="lead-a", account_type="User"
    )
    db.add(inst)
    db.commit()
    fake.repos[55] = REPOS
    return account, workspace, inst


def test_the_picker_lists_the_installation_repositories(client, db_session, fake_app):
    _, workspace, inst = _connected(db_session, client, fake_app)

    html = client.get(f"/w/{workspace.id}/connections/github").text

    assert "lead-a/headref-sandbox" in html
    assert f'value="{inst.id}:9001"' in html


def test_a_github_failure_is_shown_not_an_empty_list(client, db_session, fake_app):
    _, workspace, _ = _connected(db_session, client, fake_app)
    fake_app.fail_repos = True

    html = client.get(f"/w/{workspace.id}/connections/github").text

    assert "could not be read from GitHub" in html
    assert "can read no repositories" not in html


def test_saving_takes_full_names_from_github_not_the_form(client, db_session, fake_app):
    _, workspace, inst = _connected(db_session, client, fake_app)

    response = client.post(
        f"/w/{workspace.id}/connections/github/repos",
        data={"repo": [f"{inst.id}:9001"]},
        follow_redirects=False,
    )

    assert "github=saved" in response.headers["location"]
    rows = db_session.scalars(select(WorkspaceRepository)).all()
    assert [(r.github_repo_id, r.full_name) for r in rows] == [(9001, "lead-a/headref-sandbox")]


@pytest.mark.parametrize("value", ["{inst}:1234", "999:9001", "abc", "{inst}:"])
def test_a_tampered_selection_saves_nothing(client, db_session, fake_app, value):
    _, workspace, inst = _connected(db_session, client, fake_app)
    db_session.add(
        WorkspaceRepository(
            workspace_id=workspace.id,
            github_installation_id=inst.id,
            github_repo_id=9002,
            full_name="lead-a/other",
        )
    )
    db_session.commit()

    response = client.post(
        f"/w/{workspace.id}/connections/github/repos",
        data={"repo": [value.format(inst=inst.id)]},
        follow_redirects=False,
    )

    assert "github=invalid_selection" in response.headers["location"]
    rows = db_session.scalars(select(WorkspaceRepository)).all()
    assert [r.github_repo_id for r in rows] == [9002]


def test_the_workspace_page_shows_the_connection(client, db_session, fake_app):
    _, workspace, inst = _connected(db_session, client, fake_app)
    client.post(f"/w/{workspace.id}/connections/github/repos", data={"repo": [f"{inst.id}:9001"]})

    html = client.get(f"/w/{workspace.id}").text

    assert "Connected" in html
    assert "lead-a: 1 repository selected" in html
    assert "Manage repositories" in html


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


def test_disconnect_removes_the_installation_and_its_repositories(client, db_session, fake_app):
    _, workspace, inst = _connected(db_session, client, fake_app)
    client.post(f"/w/{workspace.id}/connections/github/repos", data={"repo": [f"{inst.id}:9001"]})

    response = client.post(
        f"/w/{workspace.id}/connections/github/{inst.id}/disconnect", follow_redirects=False
    )

    assert "github=disconnected" in response.headers["location"]
    assert db_session.scalar(select(GitHubInstallation)) is None
    assert db_session.scalar(select(WorkspaceRepository)) is None


def test_an_owner_cannot_disconnect_another_workspaces_installation(client, db_session, fake_app):
    """Using my workspace id with their installation row id must not touch theirs."""
    _, mine = _owner_with_workspace(db_session, client)
    _, other_owner = _account(db_session, 202, "lead-b")
    theirs = _workspace(db_session, other_owner, "Theirs")
    their_inst = GitHubInstallation(
        workspace_id=theirs.id, installation_id=66, account_login="lead-b", account_type="User"
    )
    db_session.add(their_inst)
    db_session.commit()

    response = client.post(
        f"/w/{mine.id}/connections/github/{their_inst.id}/disconnect", follow_redirects=False
    )

    assert "github=not_found" in response.headers["location"]
    assert db_session.get(GitHubInstallation, their_inst.id) is not None
