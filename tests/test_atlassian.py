"""Atlassian OAuth client, token encryption and token refresh (W4, DEC-023). No live calls."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from cryptography.fernet import Fernet
from httpx import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.config
from app.config import Settings
from app.db import Base
from app.integrations import atlassian, crypto, jira_tokens
from app.integrations.atlassian import AtlassianClient, TokenSet, authorize_url
from app.integrations.errors import AuthError, UpstreamError
from app.models.connections import JiraConnection

KEY = Fernet.generate_key().decode()
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def configured(monkeypatch: pytest.MonkeyPatch) -> Settings:
    cfg = Settings(
        database_url="sqlite:///:memory:",
        atlassian_client_id="client-id",
        atlassian_client_secret="client-secret",
        credential_encryption_key=KEY,
        _env_file=None,
    )
    monkeypatch.setattr(app.config, "settings", cfg)
    for module in (atlassian, crypto):
        monkeypatch.setattr(module, "get_settings", lambda: cfg)
    return cfg


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------


def test_a_token_round_trips_and_is_not_stored_in_plain_text():
    stored = crypto.encrypt_secret("refresh-abc")

    assert "refresh-abc" not in stored
    assert crypto.decrypt_secret(stored) == "refresh-abc"


def test_a_token_from_another_key_cannot_be_read(configured, monkeypatch):
    stored = crypto.encrypt_secret("refresh-abc")
    other = Settings(
        database_url="sqlite:///:memory:",
        credential_encryption_key=Fernet.generate_key().decode(),
        _env_file=None,
    )
    monkeypatch.setattr(crypto, "get_settings", lambda: other)

    with pytest.raises(crypto.CredentialError):
        crypto.decrypt_secret(stored)


def test_no_key_means_no_encryption(monkeypatch):
    blank = Settings(database_url="sqlite:///:memory:", _env_file=None)
    monkeypatch.setattr(crypto, "get_settings", lambda: blank)

    with pytest.raises(crypto.CredentialError):
        crypto.encrypt_secret("x")


# ---------------------------------------------------------------------------
# OAuth client
# ---------------------------------------------------------------------------


def test_the_consent_url_asks_only_for_read_scopes():
    url = urlparse(authorize_url("client-id", "http://localhost:8108/cb", "state-1"))
    query = parse_qs(url.query)

    assert (url.netloc, url.path) == ("auth.atlassian.com", "/authorize")
    assert query["scope"] == ["read:jira-work read:jira-user offline_access"]
    assert "write" not in query["scope"][0]
    assert query["state"] == ["state-1"]
    assert query["audience"] == ["api.atlassian.com"]


@respx.mock
def test_a_refresh_is_sent_exactly_once_even_on_a_server_error():
    """A retried refresh would present a token Atlassian already replaced."""
    route = respx.post(atlassian.TOKEN_URL).mock(return_value=Response(500))

    with pytest.raises(UpstreamError):
        AtlassianClient().refresh("refresh-abc")

    assert route.call_count == 1


@respx.mock
def test_exchange_code_returns_both_tokens():
    respx.post(atlassian.TOKEN_URL).mock(
        return_value=Response(
            200,
            json={
                "access_token": "a1",
                "refresh_token": "r1",
                "expires_in": 3600,
                "scope": "read:jira-work read:jira-user offline_access",
            },
        )
    )

    tokens = AtlassianClient().exchange_code("code-1", "http://localhost:8108/cb")

    assert (tokens.access_token, tokens.refresh_token) == ("a1", "r1")


@respx.mock
def test_only_sites_with_the_jira_read_scope_are_offered():
    respx.get(f"{atlassian.API_URL}/oauth/token/accessible-resources").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": "c1",
                    "url": "https://a.atlassian.net",
                    "name": "A",
                    "scopes": ["read:jira-work", "read:jira-user"],
                },
                {
                    "id": "c2",
                    "url": "https://b.atlassian.net",
                    "name": "B",
                    "scopes": ["read:confluence-content.all"],
                },
            ],
        )
    )

    sites = AtlassianClient().accessible_sites("a1")

    assert [s.cloud_id for s in sites] == ["c1"]


@respx.mock
def test_projects_follow_pages_until_is_last():
    route = respx.get(f"{atlassian.API_URL}/ex/jira/c1/rest/api/3/project/search")
    route.side_effect = [
        Response(
            200, json={"values": [{"id": "1", "key": "HRS", "name": "Sandbox"}], "isLast": False}
        ),
        Response(200, json={"values": [{"id": "2", "key": "OPS", "name": "Ops"}], "isLast": True}),
    ]

    projects = AtlassianClient().list_projects("c1", "a1")

    assert [p["key"] for p in projects] == ["HRS", "OPS"]
    assert route.calls[1].request.url.params["startAt"] == "1"


# ---------------------------------------------------------------------------
# Refresh and rotation
# ---------------------------------------------------------------------------


class FakeRefresh:
    def __init__(self, result: TokenSet | Exception) -> None:
        self.result = result
        self.calls: list[str] = []

    def refresh(self, refresh_token: str) -> TokenSet:
        self.calls.append(refresh_token)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _connection(db: Session, expires_at: datetime, status: str = "active") -> JiraConnection:
    from app.models.workspace import Workspace

    workspace = Workspace(name="W")
    db.add(workspace)
    db.flush()
    connection = JiraConnection(
        workspace_id=workspace.id,
        cloud_id="c1",
        access_token_enc=crypto.encrypt_secret("old-access"),
        refresh_token_enc=crypto.encrypt_secret("old-refresh"),
        expires_at=expires_at,
        scopes="",
        status=status,
    )
    db.add(connection)
    db.commit()
    return connection


def test_a_fresh_access_token_is_used_without_refreshing(db_session):
    connection = _connection(db_session, NOW + timedelta(minutes=30))
    client = FakeRefresh(UpstreamError("should not be called"))

    token = jira_tokens.valid_access_token(db_session, connection.id, client, now=NOW)

    assert token == "old-access"
    assert client.calls == []


def test_an_expiring_token_is_refreshed_and_both_tokens_are_replaced(db_session):
    connection = _connection(db_session, NOW + timedelta(seconds=30))
    new = TokenSet("new-access", "new-refresh", NOW + timedelta(hours=1), "read:jira-work")
    client = FakeRefresh(new)

    token = jira_tokens.valid_access_token(db_session, connection.id, client, now=NOW)

    db_session.refresh(connection)
    assert token == "new-access"
    assert client.calls == ["old-refresh"]
    assert crypto.decrypt_secret(connection.refresh_token_enc) == "new-refresh"
    assert "new-refresh" not in connection.refresh_token_enc


def test_a_rejected_refresh_marks_the_connection_and_raises(db_session):
    """Rule 9: a dead connection is AUTH_FAILED, never quietly empty."""
    connection = _connection(db_session, NOW - timedelta(minutes=1))
    client = FakeRefresh(AuthError("invalid_grant", status_code=403))

    with pytest.raises(AuthError):
        jira_tokens.valid_access_token(db_session, connection.id, client, now=NOW)

    db_session.refresh(connection)
    assert connection.status == "auth_failed"


def test_an_auth_failed_connection_is_not_refreshed_again(db_session):
    connection = _connection(db_session, NOW - timedelta(minutes=1), status="auth_failed")
    client = FakeRefresh(UpstreamError("should not be called"))

    with pytest.raises(AuthError):
        jira_tokens.valid_access_token(db_session, connection.id, client, now=NOW)

    assert client.calls == []


@respx.mock
def test_a_token_response_without_tokens_is_a_typed_failure():
    respx.post(atlassian.TOKEN_URL).mock(return_value=Response(200, json={"error": "x"}))

    with pytest.raises(UpstreamError):
        AtlassianClient().exchange_code("code-1", "http://localhost:8108/cb")
