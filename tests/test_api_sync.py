"""Tests for the on-demand refresh endpoint and polling (P5-007, Issue #49, DEC-016).

Verifies:
1. POST /api/teams/{team_id}/sync returns 202 in under 500ms (timing test, AC-2).
2. Endpoint never awaits sync completion (non-blocking execution).
3. Concurrency guard: does not start duplicate sync if one is already running.
4. Unauthorized actor returns 403 and starts nothing.
5. GET /api/teams/{team_id}/sync/status returns execution progress.
6. A source with no sync_run row is never reported as running.
7. The in-flight query is filtered by the team's configured scopes.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models.operations import SyncRun
from app.web import auth
from tests.conftest import log_in


@dataclass
class FakeUser:
    id: int
    team_id: int | None
    display_name: str = "Test"
    is_active: bool = True

    @property
    def user_id(self) -> int:
        return self.id


USERS = {
    1: FakeUser(id=1, team_id=10, display_name="Alice"),
    2: FakeUser(id=2, team_id=20, display_name="Bob"),
}

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(auth, "load_user", lambda db, user_id: USERS.get(user_id))

    def fake_db() -> Iterator[None]:
        yield None

    app.dependency_overrides[auth.get_db] = fake_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _log_in(client: TestClient, user_id: int) -> None:
    log_in(client, user_id)


def test_sync_trigger_returns_202_under_500ms(client: TestClient):
    """POST /api/teams/{id}/sync returns HTTP 202 in under 500ms (AC-2)."""
    _log_in(client, 1)

    with patch("app.web.routes.asyncio.create_task") as mock_task:
        start_time = time.perf_counter()
        response = client.post("/api/teams/10/sync")
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        assert response.status_code == 202
        assert elapsed_ms < 500, f"Sync endpoint took {elapsed_ms:.1f}ms, must be < 500ms"
        data = response.json()
        assert "all_finished" in data
        assert "runs" in data
        mock_task.assert_called_once()


def test_sync_trigger_unauthorized_returns_403_and_starts_nothing(client: TestClient):
    _log_in(client, 2)  # Bob is on team 20

    with patch("app.web.routes.asyncio.create_task") as mock_task:
        response = client.post("/api/teams/10/sync")

    assert response.status_code == 403
    mock_task.assert_not_called()


def test_sync_trigger_concurrency_deduplication(client: TestClient):
    """If a sync is already running, return the in-flight status and do not spawn a new task."""
    _log_in(client, 1)

    running_run = SyncRun(
        id=42,
        source="github",
        scope="keshan-dev/headref",
        status="running",
        started_at=NOW,
    )
    mock_db = MagicMock()
    mock_db.scalars.return_value.all.return_value = [running_run]

    app.dependency_overrides[auth.get_db] = lambda: mock_db
    try:
        with patch("app.web.routes.asyncio.create_task") as mock_task:
            response = client.post("/api/teams/10/sync")
            assert response.status_code == 202
            data = response.json()
            assert data["all_finished"] is False
            assert len(data["runs"]) == 1
            assert data["runs"][0]["status"] == "running"
            # Did not launch a new task because one is already running
            mock_task.assert_not_called()
    finally:
        app.dependency_overrides[auth.get_db] = lambda: iter([None])


def test_sync_status_polling(client: TestClient):
    _log_in(client, 1)

    finished_run = SyncRun(
        id=43,
        source="github",
        scope="keshan-dev/headref",
        status="success",
        started_at=NOW,
        items_skipped=0,
    )
    mock_db = MagicMock()
    mock_db.scalar.return_value = finished_run

    app.dependency_overrides[auth.get_db] = lambda: mock_db
    try:
        response = client.get("/api/teams/10/sync/status")
        assert response.status_code == 200
        data = response.json()
        assert data["all_finished"] is True
        assert len(data["runs"]) > 0
    finally:
        app.dependency_overrides[auth.get_db] = lambda: iter([None])


def test_sync_trigger_does_not_invent_a_running_source(client: TestClient):
    """With no sync_run row, the trigger reports no runs rather than claiming one.

    The earlier implementation returned a hand written payload saying GitHub and Jira
    were running before anything had been written to sync_run. Stating a fact the
    database does not hold is exactly what rule 9 forbids.
    """
    _log_in(client, 1)

    mock_db = MagicMock()
    mock_db.scalars.return_value.all.return_value = []

    app.dependency_overrides[auth.get_db] = lambda: mock_db
    try:
        with patch("app.web.routes.asyncio.create_task") as mock_task:
            response = client.post("/api/teams/10/sync")
    finally:
        app.dependency_overrides[auth.get_db] = lambda: iter([None])

    assert response.status_code == 202
    data = response.json()
    assert data["started"] is True
    assert data["runs"] == []
    assert data["all_finished"] is False
    mock_task.assert_called_once()


def test_in_flight_query_is_scoped_to_the_team():
    """The guard filters sync_run by this team's scopes, not by every running row.

    Asserted on the compiled SQL because the scope values come from configuration.
    """
    from app.config import github_scopes, jira_scopes
    from app.web.routes import _scope_filter

    sql = str(
        select(SyncRun)
        .where(SyncRun.status == "running", _scope_filter(10))
        .compile(compile_kwargs={"literal_binds": True})
    )

    assert "sync_run.source = 'github'" in sql
    assert "sync_run.source = 'jira'" in sql
    for scope in github_scopes() + jira_scopes():
        assert f"'{scope}'" in sql
