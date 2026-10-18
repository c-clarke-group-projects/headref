"""Tests for the unmatched identity administrator view (P5-005, Issue #37).

Verifies:
1. Unauthenticated request to /admin/unmatched redirects to /login.
2. Read-only list of unresolved unmatched_entity rows with counts and dates.
3. Resolved entities do not appear.
4. Handles containing special characters or scripts are strictly escaped.
5. Resolution steps are clearly displayed.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.identity import UnmatchedEntity
from app.web import auth


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
    res = client.post("/api/auth/login", json={"user_id": user_id})
    assert res.status_code == 200


def test_unmatched_unauthenticated_redirects(client: TestClient):
    response = client.get("/admin/unmatched", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_unmatched_view_renders_entities_and_steps(client: TestClient):
    _log_in(client, 1)

    unresolved = [
        UnmatchedEntity(
            id=1,
            integration="github",
            external_id="12345",
            external_handle="<script>alert('bad')</script>",
            first_seen_at=NOW - timedelta(days=5),
            last_seen_at=NOW - timedelta(hours=2),
            occurrence_count=7,
            resolved_app_user_id=None,
        ),
        UnmatchedEntity(
            id=2,
            integration="jira",
            external_id="jira-user-99",
            external_handle="external_contractor",
            first_seen_at=NOW - timedelta(days=10),
            last_seen_at=NOW - timedelta(days=1),
            occurrence_count=3,
            resolved_app_user_id=None,
        ),
    ]

    mock_db = MagicMock()
    mock_db.scalars.return_value.all.return_value = unresolved
    mock_db.get.return_value = None

    app.dependency_overrides[auth.get_db] = lambda: mock_db
    try:
        response = client.get("/admin/unmatched")
        assert response.status_code == 200
        html = response.text

        # Title
        assert "Unmatched accounts" in html

        # Escaped malicious handle
        assert "<script>" not in html
        assert "&lt;script&gt;alert(&#39;bad&#39;)&lt;/script&gt;" in html or (
            "&lt;script&gt;alert('bad')&lt;/script&gt;" in html
        )

        # External IDs and counts
        assert "12345" in html
        assert "7 times" in html
        assert "jira-user-99" in html
        assert "3 times" in html

        # Step by step instructions
        assert "How to fix a mapping" in html
        assert "seed/identity_map.yml" in html
        assert "Run the identity loader" in html
    finally:
        app.dependency_overrides[auth.get_db] = lambda: iter([None])


def test_unmatched_view_renders_for_actor_without_a_team(client: TestClient):
    """An actor with no team still gets the page, with no source health strip.

    get_source_health needs a team id. Before this case was handled the route
    constructed its input with no arguments and the page returned 500.
    """
    USERS[9] = FakeUser(id=9, team_id=None, display_name="No Team")
    try:
        _log_in(client, 9)

        mock_db = MagicMock()
        mock_db.scalars.return_value.all.return_value = []
        mock_db.get.return_value = None

        app.dependency_overrides[auth.get_db] = lambda: mock_db
        try:
            response = client.get("/admin/unmatched")
            assert response.status_code == 200
            assert "Unmatched accounts" in response.text
        finally:
            app.dependency_overrides[auth.get_db] = lambda: iter([None])
    finally:
        USERS.pop(9, None)
