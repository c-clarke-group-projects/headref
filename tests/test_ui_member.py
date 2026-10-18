"""Tests for the member profile page and drawer integration (P5-002, P5-003, Issues #34, #35).

Verifies:
1. Unauthenticated request to /members/{id} redirects to /login.
2. Unauthorized actor returns 403.
3. Invalid question type returns 422.
4. Member page renders 3 question tabs with active tab aria-selected.
5. Claims display class and categorical confidence badges; no percentage scores.
6. Panel fragment endpoint /members/{id}/panel renders cleanly for tab swap.
7. Fallback notice renders when fallback_used is True (FR-022).

The activity card (FR-026) is not covered here: MemberInsight has no activity field
yet, gap G3. Add the coverage with the field.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.insight import (
    ActivitySummary,
    EvidenceItem,
    Insight,
    MemberInsight,
)
from app.schemas.tools import PullRequestOut, SourceHealthOut, WorkItemOut
from app.web import auth, routes


@dataclass
class FakeUser:
    id: int
    team_id: int | None
    display_name: str = "Test"
    is_active: bool = True
    role_label: str | None = "Backend Engineer"

    @property
    def user_id(self) -> int:
        return self.id


USERS = {
    1: FakeUser(id=1, team_id=10, display_name="Alice"),
    2: FakeUser(id=2, team_id=10, display_name="Bob"),
    3: FakeUser(id=3, team_id=20, display_name="Carol"),
}

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(auth, "load_user", lambda db, user_id: USERS.get(user_id))
    monkeypatch.setattr(routes, "load_user", lambda db, user_id: USERS.get(user_id))

    def fake_db() -> Iterator[None]:
        yield None

    app.dependency_overrides[auth.get_db] = fake_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _log_in(client: TestClient, user_id: int) -> None:
    res = client.post("/api/auth/login", json={"user_id": user_id})
    assert res.status_code == 200


def _sample_insight(question: str = "current_work", fallback: bool = False) -> MemberInsight:
    ev = EvidenceItem(
        id="ev_1",
        source="jira",
        entity_type="work_item",
        entity_key="AUTH-245",
        source_url="https://jira.example/AUTH-245",
        summary="Ticket in progress",
        observed_at=NOW - timedelta(days=1),
        retrieved_at=NOW,
        source_state="fresh",
    )
    claim = Insight(
        claim="Alice is working on session authentication validation.",
        classification="fact",
        confidence="HIGH",
        evidence=[ev],
        conflicts=[],
    )
    assigned = [
        WorkItemOut(
            work_item_id=245,
            external_id="AUTH-245",
            title="Session auth validation seam",
            status="in_progress",
            raw_status="In Progress",
            assignee_user_id=2,
            priority="High",
            due_date=NOW + timedelta(days=2),
            blocked_by=[],
            is_flagged=False,
            source_url="https://jira.example/AUTH-245",
            source_updated_at=NOW - timedelta(days=1),
            retrieved_at=NOW,
        )
    ]
    return MemberInsight(
        user_id=2,
        summary="Alice is actively working on AUTH-245 with 12 recent commits.",
        source_health=[
            SourceHealthOut(
                source="github",  # type: ignore[arg-type]
                state="fresh",  # type: ignore[arg-type]
                last_success_at=NOW,
            ),
            SourceHealthOut(
                source="jira",  # type: ignore[arg-type]
                state="fresh",  # type: ignore[arg-type]
                last_success_at=NOW,
            ),
        ],
        likely_current_work=claim if question == "current_work" else None,
        blockers=[claim] if question == "blockers" else [],
        risks=[claim] if question == "risks" else [],
        assigned=assigned,
        unknowns=[],
        fallback_used=fallback,
    )


def test_member_page_unauthenticated_redirects(client: TestClient):
    response = client.get("/members/1", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_member_page_unauthorized_actor_returns_403(client: TestClient):
    _log_in(client, 3)  # Carol is on team 20; Alice is on team 10
    response = client.get("/members/1")
    assert response.status_code == 403


def test_member_page_invalid_question_returns_422(client: TestClient):
    _log_in(client, 1)
    response = client.get("/members/2?question=invalid_type")
    assert response.status_code == 422


def test_member_page_renders_claims_and_confidence(client: TestClient):
    _log_in(client, 1)
    insight = _sample_insight("current_work")

    with patch("app.web.routes.run_agent", new_callable=AsyncMock) as mock_agent:
        mock_agent.return_value = insight
        response = client.get("/members/2?question=current_work")

    assert response.status_code == 200
    html = response.text

    # Tab selection
    assert 'aria-selected="true"' in html
    assert "Current work" in html

    # Claim class and confidence badges
    assert "Fact" in html
    assert "High confidence" in html
    assert re.search(r"\d+\s*%", html) is None, "Numeric confidence must never appear"

    # No activity on the insight, so no activity card and no counts.
    assert "Recent activity" not in html

    # Assigned work table
    assert "AUTH-245" in html
    assert "Session auth validation seam" in html

    # Drawer open trigger button and noscript fallback details
    assert "data-ev-open" in html
    assert "evidence-details" in html


def test_member_panel_fragment_returns_partial_html(client: TestClient):
    _log_in(client, 1)
    insight = _sample_insight("blockers")

    with patch("app.web.routes.run_agent", new_callable=AsyncMock) as mock_agent:
        mock_agent.return_value = insight
        response = client.get("/members/2/panel?question=blockers")

    assert response.status_code == 200
    html = response.text

    # Must be fragment: no <!doctype html> or <head>
    assert "<!doctype html>" not in html.lower()
    assert "Is anything blocking" in html
    assert "Signals checked" in html


def test_member_page_fallback_notice_rendered(client: TestClient):
    _log_in(client, 1)
    insight = _sample_insight("current_work", fallback=True)

    with patch("app.web.routes.run_agent", new_callable=AsyncMock) as mock_agent:
        mock_agent.return_value = insight
        response = client.get("/members/2?question=current_work")

    assert response.status_code == 200
    assert "AI reasoning is temporarily unavailable, showing recorded facts only." in response.text
    assert "Written by the local model" not in response.text


def _unlinked_pr() -> PullRequestOut:
    return PullRequestOut(
        pull_request_id=90,
        number=190,
        repo_full_name="keshan-dev/headref",
        title="chore: tidy",
        state="open",
        is_draft=False,
        branch_name="chore/tidy",
        review_state="none",
        checks_state="passing",
        created_at=NOW - timedelta(days=2),
        source_updated_at=NOW - timedelta(days=1),
        retrieved_at=NOW,
        source_url="https://github.com/keshan-dev/headref/pull/190",
    )


def test_member_page_activity_counts_carry_the_context_note(client: TestClient):
    _log_in(client, 1)
    insight = _sample_insight("current_work")
    insight.activity = ActivitySummary(
        window_days=14,
        open_pull_requests=2,
        commits=7,
        reviews_given=3,
        unlinked_pull_requests=[_unlinked_pr()],
    )

    with patch("app.web.routes.run_agent", new_callable=AsyncMock) as mock_agent:
        mock_agent.return_value = insight
        html = client.get("/members/2?question=current_work").text

    assert "Recent activity" in html
    assert "Last 14 days" in html
    assert '<span class="activity__num">7</span>commits' in html
    assert '<span class="activity__num">3</span>reviews given' in html
    assert "These counts are context, not a measure of productivity." in html
    assert "https://github.com/keshan-dev/headref/pull/190" in html
    assert re.search(r"\d+\s*%", html) is None


def test_member_page_discloses_truncation(client: TestClient):
    _log_in(client, 1)
    insight = _sample_insight("current_work")
    insight.activity = ActivitySummary(window_days=14, commits=100)
    insight.truncated = {"commits": 100}

    with patch("app.web.routes.run_agent", new_callable=AsyncMock) as mock_agent:
        mock_agent.return_value = insight
        html = client.get("/members/2?question=current_work").text

    assert "Showing the most recent 100 commits." in html
