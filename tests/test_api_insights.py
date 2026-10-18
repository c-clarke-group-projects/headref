"""Integration tests for member insight and team overview endpoints (P5-001, Issue #33).

Verifies:
1. Member insight 200 returns MemberInsight contract.
2. Member insight 422 for invalid question type.
3. Member insight 401 when unauthenticated.
4. Member insight 403 when actor does not share subject's team.
5. Member insight 404 when subject does not exist.
6. Team overview 200 returns TeamOverview contract.
7. Team overview 401 when unauthenticated.
8. Team overview 403 when actor does not belong to the requested team.
9. Team overview 404 when team does not exist.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.insight import MemberInsight, TeamMemberOverview, TeamOverview
from app.web import auth


@dataclass
class FakeUser:
    """In-memory stand-in for an app_user row."""

    id: int
    team_id: int | None
    display_name: str = "Test"
    is_active: bool = True


USERS = {
    1: FakeUser(id=1, team_id=10, display_name="Alice"),
    2: FakeUser(id=2, team_id=10, display_name="Bob"),
    3: FakeUser(id=3, team_id=20, display_name="Carol"),
    4: FakeUser(id=4, team_id=None, display_name="Dana"),
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Test client configured with in-memory users and a stubbed DB dependency."""
    monkeypatch.setattr(auth, "load_user", lambda db, user_id: USERS.get(user_id))

    def fake_db() -> Iterator[None]:
        yield None

    app.dependency_overrides[auth.get_db] = fake_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _log_in(client: TestClient, user_id: int) -> None:
    """Log in through the stub and verify success."""
    response = client.post("/api/auth/login", json={"user_id": user_id})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Member insight endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", ["current_work", "blockers", "risks"])
def test_get_member_insight_success(client: TestClient, question: str) -> None:
    """Returns 200 and MemberInsight schema for valid questions and authorized actor."""
    _log_in(client, 1)

    fake_insight = MemberInsight(
        user_id=2,
        likely_current_work=None,
        assigned=[],
        blockers=[],
        risks=[],
        unknowns=[],
        last_synced={"jira": None, "github": None},
        source_health=[],
        summary="Member 2 insight summary.",
        fallback_used=False,
    )

    with patch("app.web.routes.run_agent", new_callable=AsyncMock) as mock_agent:
        mock_agent.return_value = fake_insight
        response = client.get(f"/api/members/2/insight?question={question}")

    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == 2
    assert "last_synced" in data
    assert "source_health" in data
    assert "blockers" in data
    assert "risks" in data
    assert data["fallback_used"] is False


def test_get_member_insight_invalid_question_422(client: TestClient) -> None:
    """Invalid question types (e.g. progress, invalid) return 422 Unprocessable Entity."""
    _log_in(client, 1)

    response = client.get("/api/members/2/insight?question=progress")
    assert response.status_code == 422

    response = client.get("/api/members/2/insight?question=invalid_type")
    assert response.status_code == 422


def test_get_member_insight_unauthenticated_401(client: TestClient) -> None:
    """Unauthenticated calls return 401."""
    response = client.get("/api/members/1/insight?question=current_work")
    assert response.status_code == 401


def test_get_member_insight_unauthorized_403(client: TestClient) -> None:
    """Actor attempting to view member of another team receives 403 Forbidden."""
    # User 1 is on team 10; User 3 is on team 20
    _log_in(client, 1)
    response = client.get("/api/members/3/insight?question=current_work")
    assert response.status_code == 403


def test_get_member_insight_not_found_404(client: TestClient) -> None:
    """Requesting an unknown member ID returns 404 Not Found."""
    _log_in(client, 1)
    response = client.get("/api/members/999/insight?question=current_work")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Team overview endpoint tests
# ---------------------------------------------------------------------------


def test_get_team_overview_success(client: TestClient) -> None:
    """Authorized team member receives 200 and TeamOverview schema."""
    _log_in(client, 1)

    fake_overview = TeamOverview(
        team_id=10,
        team_name="Core Team",
        overall_status="On Track",
        source_health=[],
        last_synced={},
        unmatched_count=0,
        members=[
            TeamMemberOverview(
                user_id=1,
                display_name="Alice",
                state="on_track",
                attention_count=0,
                attention_items=[],
                backing_evidence=[],
            )
        ],
    )

    with patch("app.web.routes.generate_team_overview") as mock_overview:
        mock_overview.return_value = fake_overview
        response = client.get("/api/teams/10/overview")

    assert response.status_code == 200
    data = response.json()
    assert data["team_id"] == 10
    assert data["overall_status"] == "On Track"
    assert "source_health" in data
    assert "last_synced" in data
    assert "unmatched_count" in data
    assert len(data["members"]) == 1


def test_get_team_overview_unauthenticated_401(client: TestClient) -> None:
    """Unauthenticated calls to team overview return 401."""
    response = client.get("/api/teams/10/overview")
    assert response.status_code == 401


def test_get_team_overview_unauthorized_403(client: TestClient) -> None:
    """Actor requesting overview for another team receives 403 Forbidden."""
    # User 1 is on team 10; requesting team 20 overview
    _log_in(client, 1)
    response = client.get("/api/teams/20/overview")
    assert response.status_code == 403


def test_get_team_overview_not_found_404(client: TestClient) -> None:
    """Requesting an overview for a nonexistent team raises 404."""
    _log_in(client, 1)

    with patch("app.web.routes.generate_team_overview") as mock_overview:
        mock_overview.side_effect = HTTPException(status_code=404, detail="Team 10 not found")
        response = client.get("/api/teams/10/overview")

    assert response.status_code == 404
