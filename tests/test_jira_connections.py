"""Connecting a workspace to Jira: consent, verify, site, projects, disconnect (W4)."""

import time
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.integrations import crypto
from app.integrations.atlassian import JiraSite, TokenSet
from app.integrations.errors import UpstreamError
from app.main import app
from app.models.connections import JiraConnection, WorkspaceJiraProject
from app.web import auth, jira_connections
from tests.conftest import log_in
from tests.test_workspaces import _account, _add_member, _workspace

KEY = Fernet.generate_key().decode()
SITE_A = JiraSite("cloud-a", "https://keshanuni.atlassian.net", "keshanuni", ("read:jira-work",))
SITE_B = JiraSite("cloud-b", "https://other.atlassian.net", "other", ("read:jira-work",))
PROJECTS = [
    {"id": "10001", "key": "HRS", "name": "headref sandbox"},
    {"id": "10000", "key": "MP", "name": "My Project"},
]


class FakeAtlassian:
    def __init__(self) -> None:
        self.sites = [SITE_A]
        self.fail = False
        self.codes: list[str] = []

    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        if self.fail:
            raise UpstreamError("Atlassian 502", status_code=502)
        self.codes.append(code)
        return TokenSet("access-1", "refresh-1", datetime.now(UTC) + timedelta(hours=1), "")

    def accessible_sites(self, access_token: str) -> list[JiraSite]:
        return self.sites

    def list_projects(self, cloud_id: str, access_token: str) -> list[dict[str, str]]:
        if self.fail:
            raise UpstreamError("Atlassian 502", status_code=502)
        return PROJECTS

    def refresh(self, refresh_token: str) -> TokenSet:
        raise AssertionError("not expected in these tests")


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeAtlassian:
    fake = FakeAtlassian()
    cfg = Settings(
        database_url="sqlite:///:memory:",
        atlassian_client_id="client-id",
        atlassian_client_secret="client-secret",
        credential_encryption_key=KEY,
        _env_file=None,
    )
    monkeypatch.setattr(jira_connections, "get_atlassian_client", lambda: fake)
    monkeypatch.setattr(jira_connections, "get_settings", lambda: cfg)
    monkeypatch.setattr(crypto, "get_settings", lambda: cfg)
    return fake


@pytest.fixture
def client(db_session: Session, fake: FakeAtlassian) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[auth.get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _owner(db: Session, client: TestClient) -> tuple[Any, Any]:
    user, account = _account(db, 101, "lead-a")
    workspace = _workspace(db, account, "Payments")
    log_in(client, user.id)
    return account, workspace


def _start(client: TestClient, workspace_id: int) -> str:
    response = client.post(f"/w/{workspace_id}/connections/jira/start", follow_redirects=False)
    assert response.status_code == 303
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


def _callback(client: TestClient, state: str, extra: str = "&code=code-1") -> Any:
    return client.get(f"/connections/jira/callback?state={state}{extra}", follow_redirects=False)


def test_connect_sends_the_owner_to_atlassian(client, db_session):
    _, workspace = _owner(db_session, client)

    response = client.post(f"/w/{workspace.id}/connections/jira/start", follow_redirects=False)

    location = urlparse(response.headers["location"])
    assert location.netloc == "auth.atlassian.com"
    redirect_uri = parse_qs(location.query)["redirect_uri"][0]
    assert redirect_uri.endswith("/connections/jira/callback")
    assert jira_connections.JIRA_STATE_COOKIE in response.cookies


def test_a_manager_cannot_connect_jira(client, db_session):
    _, owner = _account(db_session, 101, "lead-a")
    manager_user, manager = _account(db_session, 202, "manager-b")
    workspace = _workspace(db_session, owner)
    _add_member(db_session, workspace, manager, "manager")
    log_in(client, manager_user.id)

    response = client.post(f"/w/{workspace.id}/connections/jira/start", follow_redirects=False)

    assert response.status_code == 403


def test_1_site_connects_and_the_tokens_are_stored_encrypted(client, db_session, fake):
    account, workspace = _owner(db_session, client)

    response = _callback(client, _start(client, workspace.id))

    assert "jira=connected" in response.headers["location"]
    connection = db_session.scalar(select(JiraConnection))
    assert (connection.status, connection.cloud_id, connection.site_name) == (
        "active",
        "cloud-a",
        "keshanuni",
    )
    assert "refresh-1" not in connection.refresh_token_enc
    assert crypto.decrypt_secret(connection.refresh_token_enc) == "refresh-1"
    assert connection.connected_by_account_id == account.id


@pytest.mark.parametrize("tamper", ["no_cookie", "forged", "expired", "other_account"])
def test_a_bad_state_connects_nothing(client, db_session, fake, tamper):
    account, workspace = _owner(db_session, client)
    state = _start(client, workspace.id)
    cookie = jira_connections.JIRA_STATE_COOKIE
    if tamper == "no_cookie":
        client.cookies.delete(cookie)
    elif tamper == "forged":
        state = f"{workspace.id}.{account.id}.{int(time.time())}.x~deadbeef"
        client.cookies.set(cookie, state, domain="testserver.local")
    elif tamper == "expired":
        old = int(time.time()) - jira_connections.JIRA_STATE_MAX_AGE_SECONDS - 1
        state = auth.sign_value(f"{workspace.id}.{account.id}.{old}.n")
        client.cookies.set(cookie, state, domain="testserver.local")
    elif tamper == "other_account":
        state = auth.sign_value(f"{workspace.id}.999.{int(time.time())}.n")
        client.cookies.set(cookie, state, domain="testserver.local")

    response = _callback(client, state)

    assert "jira=bad_state" in response.headers["location"]
    assert db_session.scalar(select(JiraConnection)) is None
    assert fake.codes == []


def test_a_declined_consent_connects_nothing(client, db_session, fake):
    _, workspace = _owner(db_session, client)

    response = _callback(client, _start(client, workspace.id), extra="&error=access_denied")

    assert "jira=denied" in response.headers["location"]
    assert db_session.scalar(select(JiraConnection)) is None


def test_an_upstream_failure_changes_nothing(client, db_session, fake):
    _, workspace = _owner(db_session, client)
    fake.fail = True

    response = _callback(client, _start(client, workspace.id))

    assert "jira=upstream" in response.headers["location"]
    assert db_session.scalar(select(JiraConnection)) is None


def test_several_sites_wait_for_a_choice_among_them(client, db_session, fake):
    _, workspace = _owner(db_session, client)
    fake.sites = [SITE_A, SITE_B]

    response = _callback(client, _start(client, workspace.id))
    assert "jira=choose_site" in response.headers["location"]
    assert db_session.scalar(select(JiraConnection)).status == "pending_site"

    bad = client.post(
        f"/w/{workspace.id}/connections/jira/site",
        data={"cloud_id": "cloud-not-reachable"},
        follow_redirects=False,
    )
    assert "jira=invalid_selection" in bad.headers["location"]

    client.post(f"/w/{workspace.id}/connections/jira/site", data={"cloud_id": "cloud-b"})
    connection = db_session.scalar(select(JiraConnection))
    db_session.refresh(connection)
    assert (connection.status, connection.cloud_id) == ("active", "cloud-b")


def _connected(db: Session, client: TestClient) -> Any:
    _, workspace = _owner(db, client)
    _callback(client, _start(client, workspace.id))
    return workspace


def test_projects_are_saved_with_keys_from_atlassian(client, db_session, fake):
    workspace = _connected(db_session, client)

    response = client.post(
        f"/w/{workspace.id}/connections/jira/projects",
        data={"project": ["10001"]},
        follow_redirects=False,
    )

    assert "jira=saved" in response.headers["location"]
    rows = db_session.scalars(select(WorkspaceJiraProject)).all()
    assert [(r.project_id, r.key) for r in rows] == [("10001", "HRS")]


def test_a_tampered_project_id_saves_nothing(client, db_session, fake):
    workspace = _connected(db_session, client)

    response = client.post(
        f"/w/{workspace.id}/connections/jira/projects",
        data={"project": ["10001", "99999"]},
        follow_redirects=False,
    )

    assert "jira=invalid_selection" in response.headers["location"]
    assert db_session.scalar(select(WorkspaceJiraProject)) is None


def test_an_atlassian_failure_is_shown_not_an_empty_list(client, db_session, fake):
    workspace = _connected(db_session, client)
    fake.fail = True

    html = client.get(f"/w/{workspace.id}/connections/jira").text

    assert "could not be read from Atlassian" in html
    assert "no Jira projects you" not in html


def test_an_auth_failed_connection_asks_for_a_reconnect(client, db_session, fake):
    workspace = _connected(db_session, client)
    connection = db_session.scalar(select(JiraConnection))
    connection.status = "auth_failed"
    db_session.commit()

    picker = client.get(f"/w/{workspace.id}/connections/jira").text
    page = client.get(f"/w/{workspace.id}").text

    assert "Reconnect Jira" in picker
    assert "unavailable, not empty" in picker
    assert "Reconnect needed" in page


def test_disconnect_deletes_the_tokens_and_projects(client, db_session, fake):
    workspace = _connected(db_session, client)
    client.post(f"/w/{workspace.id}/connections/jira/projects", data={"project": ["10001"]})

    response = client.post(f"/w/{workspace.id}/connections/jira/disconnect", follow_redirects=False)

    assert "jira=disconnected" in response.headers["location"]
    assert db_session.scalar(select(JiraConnection)) is None
    assert db_session.scalar(select(WorkspaceJiraProject)) is None


def test_the_workspace_page_shows_the_jira_connection(client, db_session, fake):
    workspace = _connected(db_session, client)
    client.post(f"/w/{workspace.id}/connections/jira/projects", data={"project": ["10001"]})

    html = client.get(f"/w/{workspace.id}").text

    assert "keshanuni: 1 project selected" in html
    assert "Manage projects" in html


@pytest.mark.parametrize("outcome", ["bad_state", "denied", "upstream"])
def test_the_state_cookie_is_cleared_whatever_the_outcome(client, db_session, fake, outcome):
    _, workspace = _owner(db_session, client)
    state = _start(client, workspace.id)
    extra = "&code=code-1"
    if outcome == "bad_state":
        state = "x" + state
    elif outcome == "denied":
        extra = "&error=access_denied"
    else:
        fake.fail = True

    response = _callback(client, state, extra=extra)

    assert f"jira={outcome}" in response.headers["location"]
    cleared = response.headers.get("set-cookie", "")
    assert jira_connections.JIRA_STATE_COOKIE in cleared
    assert "Max-Age=0" in cleared or "expires=Thu, 01 Jan 1970" in cleared
