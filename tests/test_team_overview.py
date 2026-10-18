"""Unit tests for the deterministic team overview generator (P5-001, FR-025, Gap G6).

Verifies that:
1. Nonexistent team raises 404.
2. Unavailable sources force member states to 'unknown'.
3. Blockers produce 'blocked' state with 'blocker' attention items.
4. Risks or conflicts produce 'needs_attention' state with 'risk' attention items.
5. Active unblocked members produce 'on_track' state.
6. Members with no activity produce 'unknown' state with explanatory reason.
7. Every non-empty state is backed by evidence items (FR-025).
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.agent.orchestrator import ReadTools
from app.agent.team_overview import generate_team_overview
from app.models.canonical import Repository, Team
from app.schemas.tools import (
    CommitOut,
    GetAssignedWorkItemsOutput,
    GetCommitsOutput,
    GetPullRequestsOutput,
    GetReviewsOutput,
    GetSourceHealthOutput,
    GetTeamMembersOutput,
    GetWorkItemLinksOutput,
    PullRequestOut,
    SourceHealthOut,
    TeamMemberOut,
    WorkItemOut,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def _health(source: str, state: str = "fresh") -> SourceHealthOut:
    return SourceHealthOut(
        source=source,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        last_success_at=NOW - timedelta(hours=1),
    )


def _team_member(user_id: int = 1, name: str = "Alice") -> TeamMemberOut:
    return TeamMemberOut(
        user_id=user_id,
        display_name=name,
        role_label="Backend Engineer",
        is_active=True,
        linked_accounts=[],
    )


# The actor id matters. build_evidence drops any row with a null actor (AC-14), so a
# fixture that leaves it unset produces an empty evidence set and every member falls
# through to "unknown".
SUBJECT_USER_ID = 1


def _work_item(
    work_item_id: int = 1,
    status: str = "in_progress",
    raw_status: str = "In Progress",
    due_date: datetime | None = None,
    assignee_user_id: int | None = SUBJECT_USER_ID,
) -> WorkItemOut:
    return WorkItemOut(
        work_item_id=work_item_id,
        external_id=f"AUTH-{work_item_id}",
        title=f"Task {work_item_id}",
        status=status,
        raw_status=raw_status,
        assignee_user_id=assignee_user_id,
        due_date=due_date,
        source_url=f"https://example.atlassian.net/browse/AUTH-{work_item_id}",
        source_updated_at=NOW - timedelta(days=1),
        retrieved_at=NOW,
    )


def _pull_request(
    pr_id: int = 10,
    state: str = "open",
    author_user_id: int | None = SUBJECT_USER_ID,
) -> PullRequestOut:
    return PullRequestOut(
        pull_request_id=pr_id,
        number=pr_id,
        repo_full_name="acme/api",
        title=f"PR #{pr_id}",
        state=state,
        is_draft=False,
        review_state="approved",
        checks_state="passing",
        author_user_id=author_user_id,
        created_at=NOW - timedelta(days=2),
        source_url=f"https://github.com/acme/api/pull/{pr_id}",
        source_updated_at=NOW - timedelta(days=1),
        retrieved_at=NOW,
    )


def _commit(commit_id: int = 100, author_user_id: int | None = SUBJECT_USER_ID) -> CommitOut:
    return CommitOut(
        commit_id=commit_id,
        sha=f"sha{commit_id}",
        repo_full_name="acme/api",
        message_excerpt=f"Commit {commit_id}",
        author_user_id=author_user_id,
        committed_at=NOW - timedelta(days=1),
        source_url=f"https://github.com/acme/api/commit/sha{commit_id}",
        retrieved_at=NOW,
    )


def test_generate_team_overview_team_not_found() -> None:
    session = MagicMock()
    session.get.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        generate_team_overview(session, 999)

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


def test_generate_team_overview_unavailable_source_forces_unknown() -> None:
    session = MagicMock()
    session.get.return_value = Team(id=10, organization_id=1, name="Core Team")

    tools = ReadTools(
        source_health=lambda s, d: GetSourceHealthOutput(
            sources=[_health("jira", "fresh"), _health("github", "unavailable")]
        )
    )

    def fake_members(s: Any, d: Any) -> GetTeamMembersOutput:
        return GetTeamMembersOutput(
            members=[_team_member(1, "Alice"), _team_member(2, "Bob")],
            unmatched_count=1,
        )

    overview = generate_team_overview(
        session,
        10,
        now=NOW,
        tools=tools,
        team_members_fn=fake_members,
    )

    assert overview.team_id == 10
    assert overview.overall_status == "Unknown"
    assert overview.unmatched_count == 1
    assert len(overview.members) == 2

    for member in overview.members:
        assert member.state == "unknown"
        assert "unavailable" in (member.state_reason or "").lower()
        assert member.attention_count == 0


def test_generate_team_overview_blocked_member() -> None:
    session = MagicMock()
    session.get.return_value = Team(id=10, organization_id=1, name="Core Team")

    tools = ReadTools(
        source_health=lambda s, d: GetSourceHealthOutput(
            sources=[_health("jira", "fresh"), _health("github", "fresh")]
        ),
        assigned_work_items=lambda s, d: GetAssignedWorkItemsOutput(
            items=[_work_item(1, status="blocked", raw_status="Blocked")]
        ),
        pull_requests=lambda s, d: GetPullRequestsOutput(pull_requests=[]),
        commits=lambda s, d: GetCommitsOutput(commits=[], truncated=False),
        reviews=lambda s, d: GetReviewsOutput(reviews=[]),
        work_item_links=lambda s, d: GetWorkItemLinksOutput(links=[]),
    )

    def fake_members(s: Any, d: Any) -> GetTeamMembersOutput:
        return GetTeamMembersOutput(
            members=[_team_member(1, "Alice")],
            unmatched_count=0,
        )

    overview = generate_team_overview(
        session,
        10,
        now=NOW,
        tools=tools,
        team_members_fn=fake_members,
    )

    assert overview.overall_status == "Blocked"
    assert len(overview.members) == 1
    member = overview.members[0]
    assert member.state == "blocked"
    assert member.attention_count == 1
    assert member.attention_items[0].kind == "blocker"
    assert len(member.backing_evidence) > 0


def test_generate_team_overview_needs_attention_member() -> None:
    session = MagicMock()
    session.get.return_value = Team(id=10, organization_id=1, name="Core Team")

    # Work item due tomorrow -> triggers RK-1
    due_tomorrow = NOW + timedelta(days=1)
    tools = ReadTools(
        source_health=lambda s, d: GetSourceHealthOutput(
            sources=[_health("jira", "fresh"), _health("github", "fresh")]
        ),
        assigned_work_items=lambda s, d: GetAssignedWorkItemsOutput(
            items=[_work_item(1, status="in_progress", due_date=due_tomorrow)]
        ),
        pull_requests=lambda s, d: GetPullRequestsOutput(pull_requests=[]),
        commits=lambda s, d: GetCommitsOutput(commits=[], truncated=False),
        reviews=lambda s, d: GetReviewsOutput(reviews=[]),
        work_item_links=lambda s, d: GetWorkItemLinksOutput(links=[]),
    )

    def fake_members(s: Any, d: Any) -> GetTeamMembersOutput:
        return GetTeamMembersOutput(
            members=[_team_member(1, "Alice")],
            unmatched_count=0,
        )

    overview = generate_team_overview(
        session,
        10,
        now=NOW,
        tools=tools,
        team_members_fn=fake_members,
    )

    assert overview.overall_status == "Needs Attention"
    assert len(overview.members) == 1
    member = overview.members[0]
    assert member.state == "needs_attention"
    assert member.attention_count >= 1
    assert member.attention_items[0].kind == "risk"
    assert len(member.backing_evidence) > 0


def test_generate_team_overview_on_track_member() -> None:
    session = MagicMock()
    session.get.return_value = Team(id=10, organization_id=1, name="Core Team")

    # Normal in-progress work item without risks or blockers
    due_next_month = NOW + timedelta(days=25)
    tools = ReadTools(
        source_health=lambda s, d: GetSourceHealthOutput(
            sources=[_health("jira", "fresh"), _health("github", "fresh")]
        ),
        assigned_work_items=lambda s, d: GetAssignedWorkItemsOutput(
            items=[_work_item(1, status="in_progress", due_date=due_next_month)]
        ),
        pull_requests=lambda s, d: GetPullRequestsOutput(
            pull_requests=[_pull_request(10, state="open")]
        ),
        commits=lambda s, d: GetCommitsOutput(commits=[_commit(100)], truncated=False),
        reviews=lambda s, d: GetReviewsOutput(reviews=[]),
        work_item_links=lambda s, d: GetWorkItemLinksOutput(links=[]),
    )

    def fake_members(s: Any, d: Any) -> GetTeamMembersOutput:
        return GetTeamMembersOutput(
            members=[_team_member(1, "Alice")],
            unmatched_count=0,
        )

    overview = generate_team_overview(
        session,
        10,
        now=NOW,
        tools=tools,
        team_members_fn=fake_members,
    )

    assert overview.overall_status == "On Track"
    assert len(overview.members) == 1
    member = overview.members[0]
    assert member.state == "on_track"
    assert member.attention_count == 0
    assert len(member.backing_evidence) >= 1


def test_generate_team_overview_no_activity_unknown() -> None:
    session = MagicMock()
    session.get.return_value = Team(id=10, organization_id=1, name="Core Team")

    tools = ReadTools(
        source_health=lambda s, d: GetSourceHealthOutput(
            sources=[_health("jira", "fresh"), _health("github", "fresh")]
        ),
        assigned_work_items=lambda s, d: GetAssignedWorkItemsOutput(items=[]),
        pull_requests=lambda s, d: GetPullRequestsOutput(pull_requests=[]),
        commits=lambda s, d: GetCommitsOutput(commits=[], truncated=False),
        reviews=lambda s, d: GetReviewsOutput(reviews=[]),
        work_item_links=lambda s, d: GetWorkItemLinksOutput(links=[]),
    )

    def fake_members(s: Any, d: Any) -> GetTeamMembersOutput:
        return GetTeamMembersOutput(
            members=[_team_member(1, "Alice")],
            unmatched_count=0,
        )

    overview = generate_team_overview(
        session,
        10,
        now=NOW,
        tools=tools,
        team_members_fn=fake_members,
    )

    assert overview.overall_status == "Unknown"
    assert len(overview.members) == 1
    member = overview.members[0]
    assert member.state == "unknown"
    assert "no jira or github records" in (member.state_reason or "").lower()
    assert member.attention_count == 0


def test_generate_team_overview_filters_by_repository_name() -> None:
    session = MagicMock()
    session.get.return_value = Team(id=10, organization_id=1, name="Core Team")

    mock_repo = Repository(id=42, organization_id=1, full_name="acme/special-repo")
    session.scalar.return_value = mock_repo

    # User 1 has commits in repo 42, User 2 has no commits in repo 42
    def _rows(values: list[int]) -> MagicMock:
        result = MagicMock()
        result.all.return_value = values
        return result

    session.scalars.side_effect = [
        _rows([1]),  # commit_user_ids
        _rows([]),  # pr_user_ids
        _rows([]),  # review_user_ids
        _rows([]),  # owner_user_ids
    ]

    tools = ReadTools(
        source_health=lambda s, d: GetSourceHealthOutput(
            sources=[_health("jira", "fresh"), _health("github", "fresh")]
        ),
        assigned_work_items=lambda s, d: GetAssignedWorkItemsOutput(items=[]),
        pull_requests=lambda s, d: GetPullRequestsOutput(pull_requests=[]),
        commits=lambda s, d: GetCommitsOutput(commits=[], truncated=False),
        reviews=lambda s, d: GetReviewsOutput(reviews=[]),
        work_item_links=lambda s, d: GetWorkItemLinksOutput(links=[]),
    )

    def fake_members(s: Any, d: Any) -> GetTeamMembersOutput:
        return GetTeamMembersOutput(
            members=[_team_member(1, "Alice"), _team_member(2, "Bob")],
            unmatched_count=0,
        )

    overview = generate_team_overview(
        session,
        10,
        now=NOW,
        tools=tools,
        team_members_fn=fake_members,
        repository_name="acme/special-repo",
    )

    assert "acme/special-repo" in overview.team_name
    assert len(overview.members) == 1
    assert overview.members[0].display_name == "Alice"
