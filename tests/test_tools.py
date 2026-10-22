"""Unit tests for deterministic read tools T-001 through T-006 (P3-002, Issue #22)."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models.canonical import AppUser, Organization, Project, Repository, Team
from app.models.identity import IdentityLink, UnmatchedEntity
from app.models.work import Commit, PullRequest, Review, WorkItem, WorkItemDependency, WorkItemLink
from app.schemas.errors import ToolFailure
from app.schemas.tools import (
    GetAssignedWorkItemsInput,
    GetAssignedWorkItemsOutput,
    GetCommitsInput,
    GetCommitsOutput,
    GetPullRequestsInput,
    GetPullRequestsOutput,
    GetReviewsInput,
    GetReviewsOutput,
    GetTeamMembersInput,
    GetTeamMembersOutput,
    GetWorkItemLinksInput,
    GetWorkItemLinksOutput,
)
from app.tools.base import execute_tool_query
from app.tools.get_assigned_work_items import get_assigned_work_items
from app.tools.get_commits import get_commits
from app.tools.get_pull_requests import get_pull_requests
from app.tools.get_reviews import get_reviews
from app.tools.get_team_members import get_team_members
from app.tools.get_work_item_links import get_work_item_links


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _seed_base_org_and_team(session: Session) -> tuple[Organization, Team, AppUser]:
    """Seed minimal base records: organization, team, and an active user."""
    org = Organization(name="Test Org")
    session.add(org)
    session.flush()

    team = Team(organization_id=org.id, name="Core Team")
    session.add(team)
    session.flush()

    user = AppUser(
        organization_id=org.id,
        team_id=team.id,
        display_name="Keshan",
        role_label="Lead",
        is_active=True,
    )
    session.add(user)
    session.flush()

    return org, team, user


# ---------------------------------------------------------------------------
# T-001: get_team_members
# ---------------------------------------------------------------------------


def test_t001_get_team_members_success(db_session: Session) -> None:
    """T-001 returns active team members, linked accounts, and unmatched count."""
    org, team, user1 = _seed_base_org_and_team(db_session)

    # Inactive member
    user2 = AppUser(
        organization_id=org.id,
        team_id=team.id,
        display_name="Inactive Dev",
        is_active=False,
    )
    db_session.add(user2)

    # Linked accounts for user1
    now = datetime.now(UTC)
    link = IdentityLink(
        app_user_id=user1.id,
        integration="github",
        external_id="219891474",
        external_handle="keshan-dev",
        match_method="manual",
        confidence="HIGH",
        verified_at=now,
    )
    db_session.add(link)

    # Unmatched entity in queue
    unmatched = UnmatchedEntity(
        integration="github",
        external_id="external-contributor-1",
        external_handle="external-contrib",
    )
    db_session.add(unmatched)
    db_session.flush()

    # Query with include_inactive=False
    inp = GetTeamMembersInput(team_id=team.id, include_inactive=False)
    res = get_team_members(db_session, inp)

    assert isinstance(res, GetTeamMembersOutput)
    assert len(res.members) == 1
    assert res.members[0].user_id == user1.id
    assert res.members[0].display_name == "Keshan"
    assert len(res.members[0].linked_accounts) == 1
    assert res.members[0].linked_accounts[0].external_handle == "keshan-dev"
    assert res.unmatched_count == 1

    # Query with include_inactive=True
    inp_all = GetTeamMembersInput(team_id=team.id, include_inactive=True)
    res_all = get_team_members(db_session, inp_all)
    assert isinstance(res_all, GetTeamMembersOutput)
    assert len(res_all.members) == 2


def test_t001_get_team_members_missing_team(db_session: Session) -> None:
    """T-001 returns NOT_FOUND failure if the team ID does not exist."""
    inp = GetTeamMembersInput(team_id=999)
    res = get_team_members(db_session, inp)
    assert isinstance(res, ToolFailure)
    assert res.tool_id == "T-001"
    assert res.error_type == "NOT_FOUND"


# ---------------------------------------------------------------------------
# T-002: get_assigned_work_items
# ---------------------------------------------------------------------------


def test_t002_get_assigned_work_items_success(db_session: Session) -> None:
    """T-002 returns assigned work items, filters by status, and resolves blockers."""
    org, team, user = _seed_base_org_and_team(db_session)
    now = datetime.now(UTC)

    project = Project(organization_id=org.id, key="AUTH", name="Authentication")
    db_session.add(project)
    db_session.flush()

    # Blocked item
    wi_in_progress = WorkItem(
        project_id=project.id,
        external_id="AUTH-245",
        title="Refresh token rotation",
        status="in_progress",
        raw_status="In Progress",
        assignee_app_user_id=user.id,
        priority="High",
        is_flagged=False,
        source_url="https://jira.example.com/AUTH-245",
        source_updated_at=now,
    )
    # Blocker item
    wi_blocker = WorkItem(
        project_id=project.id,
        external_id="AUTH-246",
        title="Cryptographic library upgrade",
        status="blocked",
        raw_status="Blocked",
        assignee_app_user_id=user.id,
        priority="Highest",
        is_flagged=True,
        source_url="https://jira.example.com/AUTH-246",
        source_updated_at=now,
    )
    # Done item
    wi_done = WorkItem(
        project_id=project.id,
        external_id="AUTH-240",
        title="Session cookie spec",
        status="done",
        raw_status="Done",
        assignee_app_user_id=user.id,
        source_url="https://jira.example.com/AUTH-240",
        source_updated_at=now,
    )
    db_session.add_all([wi_in_progress, wi_blocker, wi_done])
    db_session.flush()

    # Add dependency: AUTH-245 is blocked by AUTH-246
    dep = WorkItemDependency(
        work_item_id=wi_in_progress.id,
        blocked_by_work_item_id=wi_blocker.id,
        link_type="is blocked by",
    )
    db_session.add(dep)
    db_session.flush()

    # Query items including done
    inp = GetAssignedWorkItemsInput(
        subject_user_id=user.id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        include_done=True,
    )
    res = get_assigned_work_items(db_session, inp)
    assert isinstance(res, GetAssignedWorkItemsOutput)
    assert len(res.items) == 3

    # Check dependency resolution on AUTH-245
    auth_245 = next(i for i in res.items if i.external_id == "AUTH-245")
    assert auth_245.blocked_by == ["AUTH-246"]
    assert [d.external_id for d in auth_245.blocked_by_items] == ["AUTH-246"]
    assert auth_245.blocked_by_items[0].source_url == "https://jira.example.com/AUTH-246"

    # Check include_done=False excludes done item
    inp_active = GetAssignedWorkItemsInput(
        subject_user_id=user.id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        include_done=False,
    )
    res_active = get_assigned_work_items(db_session, inp_active)
    assert isinstance(res_active, GetAssignedWorkItemsOutput)
    assert len(res_active.items) == 2
    assert not any(i.status == "done" for i in res_active.items)

    # Check statuses filter
    inp_filtered = GetAssignedWorkItemsInput(
        subject_user_id=user.id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        statuses=["blocked"],
    )
    res_filtered = get_assigned_work_items(db_session, inp_filtered)
    assert isinstance(res_filtered, GetAssignedWorkItemsOutput)
    assert len(res_filtered.items) == 1
    assert res_filtered.items[0].external_id == "AUTH-246"
    assert res_filtered.items[0].is_flagged is True


def test_t002_get_assigned_work_items_missing_user(db_session: Session) -> None:
    """T-002 returns NOT_FOUND failure if the subject user does not exist."""
    now = datetime.now(UTC)
    inp = GetAssignedWorkItemsInput(
        subject_user_id=999,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
    )
    res = get_assigned_work_items(db_session, inp)
    assert isinstance(res, ToolFailure)
    assert res.tool_id == "T-002"
    assert res.error_type == "NOT_FOUND"


# ---------------------------------------------------------------------------
# T-003: get_pull_requests
# ---------------------------------------------------------------------------


def test_t003_get_pull_requests_success(db_session: Session) -> None:
    """T-003 returns authored pull requests and respects state/draft filters."""
    org, team, user = _seed_base_org_and_team(db_session)
    now = datetime.now(UTC)

    repo = Repository(
        organization_id=org.id,
        full_name="keshan-dev/headref",
        default_branch="main",
    )
    db_session.add(repo)
    db_session.flush()

    pr_open = PullRequest(
        repository_id=repo.id,
        number=182,
        title="feat: refresh token rotation",
        body_excerpt="Implements token rotation.",
        state="open",
        is_draft=False,
        branch_name="feature/AUTH-245-token",
        author_app_user_id=user.id,
        review_state="approved",
        checks_state="passing",
        created_at_source=now - timedelta(hours=2),
        source_updated_at=now,
        source_url="https://github.com/keshan-dev/headref/pull/182",
    )
    pr_draft = PullRequest(
        repository_id=repo.id,
        number=183,
        title="draft: experiment",
        state="open",
        is_draft=True,
        branch_name="exp",
        author_app_user_id=user.id,
        created_at_source=now - timedelta(hours=1),
        source_updated_at=now,
        source_url="https://github.com/keshan-dev/headref/pull/183",
    )
    db_session.add_all([pr_open, pr_draft])
    db_session.flush()

    # Query with drafts
    inp = GetPullRequestsInput(
        subject_user_id=user.id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        include_drafts=True,
    )
    res = get_pull_requests(db_session, inp)
    assert isinstance(res, GetPullRequestsOutput)
    assert len(res.pull_requests) == 2

    # Query excluding drafts
    inp_nodrafts = GetPullRequestsInput(
        subject_user_id=user.id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        include_drafts=False,
    )
    res_nodrafts = get_pull_requests(db_session, inp_nodrafts)
    assert isinstance(res_nodrafts, GetPullRequestsOutput)
    assert len(res_nodrafts.pull_requests) == 1
    assert res_nodrafts.pull_requests[0].number == 182
    assert res_nodrafts.pull_requests[0].repo_full_name == "keshan-dev/headref"
    assert res_nodrafts.pull_requests[0].review_state == "approved"


def test_t003_get_pull_requests_missing_user(db_session: Session) -> None:
    """T-003 returns NOT_FOUND if subject does not exist."""
    now = datetime.now(UTC)
    inp = GetPullRequestsInput(
        subject_user_id=999,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
    )
    res = get_pull_requests(db_session, inp)
    assert isinstance(res, ToolFailure)
    assert res.error_type == "NOT_FOUND"


# ---------------------------------------------------------------------------
# T-004: get_commits
# ---------------------------------------------------------------------------


def test_t004_get_commits_truncation_detection(db_session: Session) -> None:
    """T-004 sets truncated=True when row count exceeds limit, and caps output."""
    org, team, user = _seed_base_org_and_team(db_session)
    now = datetime.now(UTC)

    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add(repo)
    db_session.flush()

    for idx in range(5):
        c = Commit(
            repository_id=repo.id,
            sha=f"sha_{idx}",
            message_excerpt=f"Commit message {idx}",
            author_app_user_id=user.id,
            committed_at=now - timedelta(minutes=idx * 10),
            source_url=f"https://github.com/keshan-dev/headref/commit/sha_{idx}",
        )
        db_session.add(c)
    db_session.flush()

    # Query with limit=3 (truncation expected)
    inp_trunc = GetCommitsInput(
        subject_user_id=user.id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        limit=3,
    )
    res_trunc = get_commits(db_session, inp_trunc)
    assert isinstance(res_trunc, GetCommitsOutput)
    assert res_trunc.truncated is True
    assert len(res_trunc.commits) == 3

    # Query with limit=10 (no truncation)
    inp_notrunc = GetCommitsInput(
        subject_user_id=user.id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        limit=10,
    )
    res_notrunc = get_commits(db_session, inp_notrunc)
    assert isinstance(res_notrunc, GetCommitsOutput)
    assert res_notrunc.truncated is False
    assert len(res_notrunc.commits) == 5


def test_t004_get_commits_empty_window_falls_back_to_latest(db_session: Session) -> None:
    """With nothing in the window, T-004 returns the 5 most recent commits of any age."""
    org, team, user = _seed_base_org_and_team(db_session)
    now = datetime.now(UTC)

    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add(repo)
    db_session.flush()

    for idx in range(7):
        db_session.add(
            Commit(
                repository_id=repo.id,
                sha=f"old_{idx}",
                message_excerpt=f"Old commit {idx}",
                author_app_user_id=user.id,
                committed_at=now - timedelta(days=90 + idx),
                source_url=f"https://github.com/keshan-dev/headref/commit/old_{idx}",
            )
        )
    db_session.flush()

    res = get_commits(
        db_session,
        GetCommitsInput(
            subject_user_id=user.id,
            window_start=now - timedelta(days=14),
            window_end=now,
        ),
    )
    assert isinstance(res, GetCommitsOutput)
    assert [c.sha for c in res.commits] == [f"old_{idx}" for idx in range(5)]


# ---------------------------------------------------------------------------
# T-005: get_reviews
# ---------------------------------------------------------------------------


def test_t005_get_reviews_direction_filter(db_session: Session) -> None:
    """T-005 filters reviews by given, received, or both directions."""
    org, team, user1 = _seed_base_org_and_team(db_session)
    now = datetime.now(UTC)

    user2 = AppUser(organization_id=org.id, team_id=team.id, display_name="Reviewer Dev")
    db_session.add(user2)
    db_session.flush()

    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add(repo)
    db_session.flush()

    # PR authored by user1
    pr = PullRequest(
        repository_id=repo.id,
        number=10,
        title="PR 10",
        state="open",
        branch_name="feat",
        author_app_user_id=user1.id,
        created_at_source=now,
        source_updated_at=now,
        source_url="https://github.com/keshan-dev/headref/pull/10",
    )
    db_session.add(pr)
    db_session.flush()

    # Review given by user2, received by user1
    review = Review(
        pull_request_id=pr.id,
        external_id="rev_1",
        reviewer_app_user_id=user2.id,
        state="changes_requested",
        body_excerpt="Please fix unit tests.",
        submitted_at=now,
        source_url="https://github.com/keshan-dev/headref/pull/10#rev1",
    )
    db_session.add(review)
    db_session.flush()

    # For reviewer user2: given direction finds the review
    inp_given = GetReviewsInput(
        subject_user_id=user2.id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        direction="given",
    )
    res_given = get_reviews(db_session, inp_given)
    assert isinstance(res_given, GetReviewsOutput)
    assert len(res_given.reviews) == 1
    assert res_given.reviews[0].state == "changes_requested"

    # For author user1: received direction finds the review
    inp_received = GetReviewsInput(
        subject_user_id=user1.id,
        window_start=now - timedelta(days=1),
        window_end=now + timedelta(days=1),
        direction="received",
    )
    res_received = get_reviews(db_session, inp_received)
    assert isinstance(res_received, GetReviewsOutput)
    assert len(res_received.reviews) == 1
    assert res_received.reviews[0].reviewer_user_id == user2.id


# ---------------------------------------------------------------------------
# T-006: get_work_item_links
# ---------------------------------------------------------------------------


def test_t006_get_work_item_links_filtering(db_session: Session) -> None:
    """T-006 returns correlation links filtered by IDs and minimum confidence."""
    org, team, user = _seed_base_org_and_team(db_session)
    now = datetime.now(UTC)

    project = Project(organization_id=org.id, key="AUTH", name="Auth")
    db_session.add(project)
    db_session.flush()

    wi = WorkItem(
        project_id=project.id,
        external_id="AUTH-245",
        title="Token rotation",
        status="in_progress",
        raw_status="In Progress",
        source_url="https://jira.example.com/AUTH-245",
        source_updated_at=now,
    )
    db_session.add(wi)
    db_session.flush()

    # Link 1: HIGH confidence
    link_high = WorkItemLink(
        work_item_id=wi.id,
        target_type="pull_request",
        target_id="182",
        link_method="branch_name",
        confidence="HIGH",
    )
    # Link 2: MEDIUM confidence
    link_med = WorkItemLink(
        work_item_id=wi.id,
        target_type="pull_request",
        target_id="185",
        link_method="pr_title",
        confidence="MEDIUM",
    )
    # Link 3: Non-integer target (raw branch name string)
    link_branch = WorkItemLink(
        work_item_id=wi.id,
        target_type="branch",
        target_id="feature/AUTH-245-branch",
        link_method="branch_name",
        confidence="HIGH",
    )
    db_session.add_all([link_high, link_med, link_branch])
    db_session.flush()

    # Query by work_item_ids with min_confidence=MEDIUM (both PR links returned)
    inp_med = GetWorkItemLinksInput(work_item_ids=[wi.id], min_confidence="MEDIUM")
    res_med = get_work_item_links(db_session, inp_med)
    assert isinstance(res_med, GetWorkItemLinksOutput)
    assert len(res_med.links) == 2
    assert {link.target_id for link in res_med.links} == {182, 185}

    # Query with min_confidence=HIGH (only link 1 returned)
    inp_high = GetWorkItemLinksInput(work_item_ids=[wi.id], min_confidence="HIGH")
    res_high = get_work_item_links(db_session, inp_high)
    assert isinstance(res_high, GetWorkItemLinksOutput)
    assert len(res_high.links) == 1
    assert res_high.links[0].target_id == 182

    # Query by pull_request_ids
    inp_pr = GetWorkItemLinksInput(pull_request_ids=[185], min_confidence="MEDIUM")
    res_pr = get_work_item_links(db_session, inp_pr)
    assert isinstance(res_pr, GetWorkItemLinksOutput)
    assert len(res_pr.links) == 1
    assert res_pr.links[0].work_item_external_id == "AUTH-245"


# ---------------------------------------------------------------------------
# General Tool Behavior & Error Handling
# ---------------------------------------------------------------------------


def test_tools_empty_results_return_success(db_session: Session) -> None:
    """Tools return empty lists on no matches, never failures (AGENT_TOOLS.md)."""
    org, team, user = _seed_base_org_and_team(db_session)
    now = datetime.now(UTC)

    # Empty work items
    res_wi = get_assigned_work_items(
        db_session,
        GetAssignedWorkItemsInput(
            subject_user_id=user.id,
            window_start=now - timedelta(days=1),
            window_end=now + timedelta(days=1),
        ),
    )
    assert isinstance(res_wi, GetAssignedWorkItemsOutput)
    assert res_wi.items == []

    # Empty pull requests
    res_pr = get_pull_requests(
        db_session,
        GetPullRequestsInput(
            subject_user_id=user.id,
            window_start=now - timedelta(days=1),
            window_end=now + timedelta(days=1),
        ),
    )
    assert isinstance(res_pr, GetPullRequestsOutput)
    assert res_pr.pull_requests == []


def test_execute_tool_query_maps_timeout_and_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """execute_tool_query converts timeouts to TIMEOUT and db exceptions to UPSTREAM_ERROR."""

    # 1. Simulated timeout
    def _timeout_op() -> None:
        raise RuntimeError("canceling statement due to statement timeout")

    monkeypatch.setattr("app.tools.base.is_timeout_error", lambda exc: True)
    res_timeout = execute_tool_query("T-002", _timeout_op)
    assert isinstance(res_timeout, ToolFailure)
    assert res_timeout.error_type == "TIMEOUT"

    # 2. General database error
    def _db_err_op() -> None:
        raise RuntimeError("OperationalError: connection closed token: secret-12345")

    monkeypatch.setattr("app.tools.base.is_timeout_error", lambda exc: False)
    res_err = execute_tool_query("T-002", _db_err_op)
    assert isinstance(res_err, ToolFailure)
    assert res_err.error_type == "UPSTREAM_ERROR"
    # Verify token redaction
    assert "secret-12345" not in res_err.detail
    assert "token=***" in res_err.detail


# ---------------------------------------------------------------------------
# Empty and failure cases (P6-002, Issue #40)
# ---------------------------------------------------------------------------


def test_t001_get_team_members_empty_team(db_session: Session) -> None:
    """T-001 returns an empty member list, not a failure, for a team with no active members."""
    org, team, user = _seed_base_org_and_team(db_session)
    user.is_active = False
    empty_team = Team(organization_id=org.id, name="Empty Team")
    db_session.add(empty_team)
    db_session.flush()

    res_empty = get_team_members(db_session, GetTeamMembersInput(team_id=empty_team.id))
    assert isinstance(res_empty, GetTeamMembersOutput)
    assert res_empty.members == []
    assert res_empty.unmatched_count == 0

    res_inactive = get_team_members(db_session, GetTeamMembersInput(team_id=team.id))
    assert isinstance(res_inactive, GetTeamMembersOutput)
    assert res_inactive.members == []


def test_t004_get_commits_empty(db_session: Session) -> None:
    """T-004 returns no commits and truncated=False for a user with no commits at all."""
    _, _, user = _seed_base_org_and_team(db_session)
    now = datetime.now(UTC)

    res = get_commits(
        db_session,
        GetCommitsInput(
            subject_user_id=user.id,
            window_start=now - timedelta(days=14),
            window_end=now,
        ),
    )
    assert isinstance(res, GetCommitsOutput)
    assert res.commits == []
    assert res.truncated is False


def test_t004_get_commits_missing_user(db_session: Session) -> None:
    """T-004 returns NOT_FOUND failure if the subject user does not exist."""
    now = datetime.now(UTC)
    res = get_commits(
        db_session,
        GetCommitsInput(subject_user_id=999, window_start=now - timedelta(days=14), window_end=now),
    )
    assert isinstance(res, ToolFailure)
    assert res.tool_id == "T-004"
    assert res.error_type == "NOT_FOUND"


def test_t005_get_reviews_empty(db_session: Session) -> None:
    """T-005 returns an empty list, not a failure, when the user has no reviews at all."""
    _, _, user = _seed_base_org_and_team(db_session)
    now = datetime.now(UTC)

    res = get_reviews(
        db_session,
        GetReviewsInput(
            subject_user_id=user.id,
            window_start=now - timedelta(days=14),
            window_end=now,
        ),
    )
    assert isinstance(res, GetReviewsOutput)
    assert res.reviews == []


def test_t005_get_reviews_missing_user(db_session: Session) -> None:
    """T-005 returns NOT_FOUND failure if the subject user does not exist."""
    now = datetime.now(UTC)
    res = get_reviews(
        db_session,
        GetReviewsInput(subject_user_id=999, window_start=now - timedelta(days=14), window_end=now),
    )
    assert isinstance(res, ToolFailure)
    assert res.tool_id == "T-005"
    assert res.error_type == "NOT_FOUND"


def test_t006_get_work_item_links_empty(db_session: Session) -> None:
    """T-006 returns an empty list, a valid result, when no stored link matches."""
    res = get_work_item_links(
        db_session, GetWorkItemLinksInput(work_item_ids=[999], pull_request_ids=[888])
    )
    assert isinstance(res, GetWorkItemLinksOutput)
    assert res.links == []


@pytest.fixture
def broken_session() -> Generator[Session, None, None]:
    """A session on a database with no tables, so every query raises a database error."""
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        yield session


def _failing_tool_calls() -> list[tuple[str, object]]:
    now = datetime.now(UTC)
    window = {"window_start": now - timedelta(days=14), "window_end": now}
    return [
        ("T-001", lambda s: get_team_members(s, GetTeamMembersInput(team_id=1))),
        ("T-004", lambda s: get_commits(s, GetCommitsInput(subject_user_id=1, **window))),
        ("T-005", lambda s: get_reviews(s, GetReviewsInput(subject_user_id=1, **window))),
        ("T-006", lambda s: get_work_item_links(s, GetWorkItemLinksInput(work_item_ids=[1]))),
    ]


@pytest.mark.parametrize(("tool_id", "call"), _failing_tool_calls())
def test_tool_database_error_is_a_typed_failure(
    broken_session: Session, tool_id: str, call
) -> None:
    """A database error is UPSTREAM_ERROR from the real tool, never an empty result."""
    res = call(broken_session)
    assert isinstance(res, ToolFailure)
    assert res.tool_id == tool_id
    assert res.error_type == "UPSTREAM_ERROR"
