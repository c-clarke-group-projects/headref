"""Tests for the team overview page (P5-004, Issue #36).

Verifies:
1. Unauthenticated request to /teams/{id} redirects to /login.
2. Unauthorized actor (different team) returns 403.
3. Successful team overview renders all 4 member states with distinct badges.
4. Attention items display blocker or risk tag, description, and link to profile.
5. Hero summary reflects attention count across the team.
6. Unmatched accounts banner displays when unmatched_count > 0.
7. Members are displayed alphabetically without ranking or comparison.
8. Empty team displays honest empty state note.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.schemas.insight import AttentionItem, EvidenceItem, TeamMemberOverview, TeamOverview
from app.schemas.tools import SourceHealthOut
from app.web import auth, routes


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
    2: FakeUser(id=2, team_id=10, display_name="Bob"),
    3: FakeUser(id=3, team_id=20, display_name="Carol"),
}

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # routes.py binds load_user at import time, so patching only app.web.auth leaves
    # the form login path calling the real loader against a None session.
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


def _sample_overview(team_id: int = 10, unmatched: int = 2) -> TeamOverview:
    ev = EvidenceItem(
        id="ev_1",
        source="jira",
        entity_type="work_item",
        entity_key="AUTH-1",
        source_url="https://jira.example/AUTH-1",
        summary="Ticket blocked",
        observed_at=NOW - timedelta(days=2),
        retrieved_at=NOW,
        source_state="fresh",
    )
    members = [
        TeamMemberOverview(
            user_id=1,
            display_name="Alice",
            role_label="Backend Lead",
            state="on_track",
            state_reason=None,
            attention_count=0,
            attention_items=[],
        ),
        TeamMemberOverview(
            user_id=2,
            display_name="Bob",
            role_label="Engineer",
            state="blocked",
            state_reason="Work item is blocked",
            attention_count=1,
            attention_items=[
                AttentionItem(
                    claim="Blocked by AUTH-1",
                    evidence=[ev],
                    kind="blocker",
                )
            ],
        ),
        TeamMemberOverview(
            user_id=4,
            display_name="Dana",
            role_label=None,
            state="needs_attention",
            state_reason="Pull request unreviewed",
            attention_count=1,
            attention_items=[
                AttentionItem(
                    claim="PR #10 open without review",
                    evidence=[ev],
                    kind="risk",
                )
            ],
        ),
        TeamMemberOverview(
            user_id=5,
            display_name="Eve",
            role_label=None,
            state="unknown",
            state_reason="No activity in last 14 days",
            attention_count=0,
            attention_items=[],
        ),
    ]
    return TeamOverview(
        team_id=team_id,
        team_name="Core Team",
        # Blocked outranks needs_attention and unknown, which is what this mix holds.
        overall_status="Blocked",
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
        members=members,
        unmatched_count=unmatched,
    )


def test_team_overview_unauthenticated_redirects(client: TestClient):
    response = client.get("/teams/10", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_team_overview_unauthorized_actor_returns_403(client: TestClient):
    _log_in(client, 3)  # Carol is on team 20
    response = client.get("/teams/10")
    assert response.status_code == 403


def test_team_overview_renders_all_states_and_attention(client: TestClient):
    _log_in(client, 1)  # Alice is on team 10
    overview = _sample_overview(team_id=10, unmatched=3)

    with patch("app.web.routes.generate_team_overview", return_value=overview):
        response = client.get("/teams/10")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # Title and team name
    assert "Core Team" in html
    # Hero attention summary
    assert "2 items" in html
    assert "need your attention" in html

    # All 4 member states
    assert "On track" in html
    assert "Blocked" in html
    assert "Needs attention" in html
    assert "Unknown" in html

    # Attention item tags and text
    assert "Blocker" in html
    assert "Blocked by AUTH-1" in html
    assert "Risk" in html
    assert "PR #10 open without review" in html

    # Links to member profiles with correct question tab
    assert "/members/2?question=blockers" in html
    assert "/members/4?question=risks" in html

    # The unmatched accounts card was removed from the team page on 2026-10-17, which
    # leaves P5-004 "the unmatched count is visible" open. Raised in WORKLOG.md under
    # Decisions Needed; test_headref_ux_and_jira.py asserts the current behaviour.

    # No ranking and no comparison (AI_BEHAVIOR 5.11). A substring ban on "rank" is
    # wrong here: the page carries the disclaimer "Headref does not rank or compare
    # people", which is the copy the rule asks for. Assert the structure instead.
    assert "does not rank or compare people" in html
    assert "Member order is alphabetical" in html

    # Members render in alphabetical order, not in any scored order. Match the member
    # card link, not the bare name: "Eve" also occurs inside "Evidence".
    card_names = re.findall(r'class="member-card__name"[^>]*>([^<]+)<', html)
    assert card_names == ["Alice", "Bob", "Dana", "Eve"]

    # No sorting control and no scoring vocabulary anywhere on the page.
    lowered = html.lower()
    for forbidden in ("leaderboard", "top performer", "ranked", "sort by", "data-sort"):
        assert forbidden not in lowered


def test_team_overview_empty_team_renders_clean_state(client: TestClient):
    _log_in(client, 1)
    # "On Track" is what generate_team_overview returns for a team with no members.
    # The value is not asserted here; the fixture matches real behaviour so the test
    # does not drift from it.
    empty = TeamOverview(
        team_id=10,
        team_name="Empty Team",
        overall_status="On Track",
        source_health=[],
        members=[],
        unmatched_count=0,
    )

    with patch("app.web.routes.generate_team_overview", return_value=empty):
        response = client.get("/teams/10")

    assert response.status_code == 200
    assert "No active members found" in response.text


def test_login_page_renders_cleanly(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    cfg = Settings(
        database_url="sqlite:///:memory:",
        github_oauth_client_id="test-client-id",
        github_oauth_client_secret="test-client-secret",
        _env_file=None,
    )
    monkeypatch.setattr(routes, "get_settings", lambda: cfg)
    response = client.get("/login?next=/admin/unmatched")
    assert response.status_code == 200
    html = response.text
    assert "Sign in" in html
    # The demo stub form was removed on 2026-10-17. next now rides on the OAuth link.
    assert 'href="/auth/github/login?next=/admin/unmatched"' in html
    assert "Demo sign in" not in html


def test_login_page_form_submit_invalid_user_renders_401(client: TestClient):
    response = client.post(
        "/login",
        data={"user_id": "999", "next": "/admin/unmatched"},
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 401
    assert "Unknown or inactive user" in response.text
