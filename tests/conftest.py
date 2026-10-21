"""Pytest configuration and environment fixtures for headref."""

import ipaddress
import os
import socket
from collections.abc import Generator, Iterator
from typing import Any
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

# Ensure dummy configuration secrets exist so app.config can load in test environments.
# An empty value counts as unset: docker compose passes the blank lines of .env through
# as empty variables, which would otherwise make a local run differ from CI.
_TEST_ENV = {
    "DATABASE_URL": "postgresql+psycopg://headref:headref@localhost:5432/headref",
    "GITHUB_TOKEN": "test-github-token",
    "JIRA_BASE_URL": "https://example.atlassian.net",
    "JIRA_EMAIL": "test@example.com",
    "JIRA_API_TOKEN": "test-jira-token",
    "SCHEDULER_ENABLED": "false",
}
for _key, _value in _TEST_ENV.items():
    if not os.environ.get(_key):
        os.environ[_key] = _value


@pytest.fixture(autouse=True)
def _restore_configuration() -> Generator[None, None, None]:
    """Undo any change a test makes to the environment or the settings singleton."""
    import app.config

    saved_env = dict(os.environ)
    saved_settings = app.config.settings
    yield
    os.environ.clear()
    os.environ.update(saved_env)
    app.config.settings = saved_settings


def _database_addresses() -> set[str]:
    """IP addresses of the test database host, the 1 network peer a test may reach.

    tests/test_db.py checks a real statement timeout against PostgreSQL when one is
    reachable: localhost in CI, the compose service "db" inside the api container.
    """
    host = urlparse(os.environ.get("DATABASE_URL", "")).hostname
    if not host:
        return set()
    try:
        return {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return set()


_ALLOWED_ADDRESSES = _database_addresses()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Refuse any connection off this machine, and fail the test that tried (P6-001).

    HTTP is mocked with respx or fake transports, so a real connection means a test
    escaped its mock. The connection is refused with an OSError and the test is failed
    at teardown, so a test that expects a failure cannot pass by accident. Loopback and
    the test database stay allowed; Unix sockets are untouched.
    """
    attempted: list[str] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _allowed(sock: socket.socket, address: Any) -> bool:
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return True
        host = str(address[0])
        if host in _ALLOWED_ADDRESSES:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _guarded_connect(sock: socket.socket, address: Any) -> None:
        if not _allowed(sock, address):
            attempted.append(str(address))
            raise ConnectionRefusedError(f"Network disabled in tests: {address}")
        real_connect(sock, address)

    def _guarded_connect_ex(sock: socket.socket, address: Any) -> int:
        if not _allowed(sock, address):
            attempted.append(str(address))
            raise ConnectionRefusedError(f"Network disabled in tests: {address}")
        return real_connect_ex(sock, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    yield
    if attempted:
        pytest.fail(f"Test tried to reach the network: {', '.join(attempted)}")


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry without sleeping, so a test checks the attempt count, not the wall clock."""
    monkeypatch.setattr(
        "app.integrations.http._WaitRetryAfterOrExponential.__call__", lambda *_: 0.0
    )


def log_in(client: TestClient, user_id: int) -> None:
    """Sign a test client in as user_id, as GitHub OAuth does after a real sign-in.

    Sets the signed session cookie directly. There is no login route to call: the
    P3-001 stub that trusted any user id was removed.
    """
    from app.web.auth import SESSION_COOKIE, sign_session

    # TestClient serves http://testserver, which the cookie jar files under
    # "testserver.local". Using that domain lets the app's logout delete this cookie.
    client.cookies.set(SESSION_COOKIE, sign_session(user_id), domain="testserver.local")


def iter_api_routes(application: FastAPI) -> Iterator[tuple[str, Any]]:
    """Yield (full path, dependant) for every API route, including included routers.

    FastAPI 0.143 keeps an included router as 1 ``_IncludedRouter`` entry in
    ``app.routes``, so a plain ``isinstance(route, APIRoute)`` scan sees none of the
    routes inside it and a coverage check passes without checking anything. Older
    versions flatten them into APIRoute entries. Both layouts are handled.
    """
    for route in application.routes:
        if isinstance(route, APIRoute):
            yield route.path, route.dependant
        elif hasattr(route, "effective_candidates"):
            for candidate in route.effective_candidates():
                if hasattr(candidate, "dependant"):
                    yield candidate.path, candidate.dependant
