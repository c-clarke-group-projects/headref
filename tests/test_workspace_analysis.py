"""Workspace analysis, likely next work, member panel and summary (W7)."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import people
from app.agent.orchestrator import run_agent
from app.db import Base
from app.integrations.identity_resolver import resolve_actor
from app.main import app
from app.models.canonical import AppUser, Organization, Project, Repository
from app.models.operations import SyncRun
from app.models.work import PullRequest, WorkItem
from app.schemas.insight import EvidenceItem, Insight, MemberInsight
from app.schemas.tools import GetSourceHealthInput
from app.tools.get_source_health import get_source_health
from app.web import auth, workspace_pages
from app.workspace_analysis import (
    MemberAnalysis,
    WorkspaceAnalysis,
    analyze_workspace,
    next_up,
)
from app.workspace_sync import ensure_organization
from tests.conftest import log_in
from tests.test_orchestrator import _health, _work_item, make_tools
from tests.test_workspaces import _account, _workspace

NOW = datetime.now(UTC)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _person(db: Session, org_id: int, gh_id: str = "101", handle: str = "keshan-dev") -> int:
    person_id = resolve_actor(db, "github", gh_id, handle, auto_create=True, organization_id=org_id)
    db.commit()
    return person_id


def _todo(db: Session, org_id: int, person_id: int, key: str, priority: str, due_days: int | None):
    project = db.query(Project).filter_by(organization_id=org_id).first()
    if project is None:
        project = Project(organization_id=org_id, key="HRS", name="sandbox")
        db.add(project)
        db.flush()
    db.add(
        WorkItem(
            project_id=project.id,
            external_id=key,
            title=f"Task {key}",
            status="todo",
            raw_status="To Do",
            priority=priority,
            due_date=NOW + timedelta(days=due_days) if due_days is not None else None,
            assignee_app_user_id=person_id,
            source_url=f"https://x.atlassian.net/browse/{key}",
            source_updated_at=NOW,
            retrieved_at=NOW,
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# Likely next
# ---------------------------------------------------------------------------


def test_next_up_orders_by_priority_then_due_date_and_adds_drafts(db_session):
    org = Organization(name="W")
    db_session.add(org)
    db_session.flush()
    person_id = _person(db_session, org.id)
    _todo(db_session, org.id, person_id, "HRS-10", "Medium", 1)
    _todo(db_session, org.id, person_id, "HRS-11", "Highest", 9)
    _todo(db_session, org.id, person_id, "HRS-12", "Highest", 2)
    repo = Repository(organization_id=org.id, full_name="o/r")
    db_session.add(repo)
    db_session.flush()
    db_session.add(
        PullRequest(
            repository_id=repo.id,
            number=7,
            title="Start a changelog",
            state="open",
            is_draft=True,
            branch_name="wip",
            author_app_user_id=person_id,
            created_at_source=NOW,
            source_url="https://github.com/o/r/pull/7",
            source_updated_at=NOW,
            retrieved_at=NOW,
        )
    )
    db_session.commit()

    items = next_up(db_session, person_id, [], NOW)

    assert [i.evidence.entity_key for i in items] == ["HRS-12", "HRS-11", "HRS-10", "o/r#7"]
    assert all(i.evidence.source_url.startswith("https://") for i in items)


def _open_pr(db: Session, repo: Repository, number: int, author_id: int | None) -> PullRequest:
    pr = PullRequest(
        repository_id=repo.id,
        number=number,
        title=f"Change {number}",
        state="open",
        is_draft=False,
        branch_name=f"b{number}",
        author_app_user_id=author_id,
        created_at_source=NOW,
        source_url=f"https://github.com/o/r/pull/{number}",
        source_updated_at=NOW,
        retrieved_at=NOW,
    )
    db.add(pr)
    db.flush()
    return pr


def test_a_pending_review_request_is_in_likely_next_until_given(db_session):
    from app.integrations.identity_resolver import record_review_requests
    from app.models.work import ReviewRequest

    org = Organization(name="W")
    db_session.add(org)
    db_session.flush()
    me = _person(db_session, org.id, "101", "keshan-dev")
    other = _person(db_session, org.id, "202", "isiwara")
    repo = Repository(organization_id=org.id, full_name="o/r")
    db_session.add(repo)
    db_session.flush()
    pr = _open_pr(db_session, repo, 9, other)
    raw = {"state": "open", "requested_reviewers": [{"id": 101, "login": "keshan-dev"}]}

    record_review_requests(db_session, [pr], [raw], organization_id=org.id)
    db_session.commit()

    assert [i.text for i in next_up(db_session, me, [], NOW)] == [
        "Review requested on pull request #9 Change 9"
    ]
    assert next_up(db_session, other, [], NOW) == []

    # GitHub drops the reviewer once they review; the request goes with it.
    record_review_requests(
        db_session, [pr], [{"state": "open", "requested_reviewers": []}], organization_id=org.id
    )
    db_session.commit()
    assert db_session.scalar(select(ReviewRequest)) is None
    assert next_up(db_session, me, [], NOW) == []


def test_a_closed_pull_request_keeps_no_review_request(db_session):
    from app.integrations.identity_resolver import record_review_requests
    from app.models.work import ReviewRequest

    org = Organization(name="W")
    db_session.add(org)
    db_session.flush()
    repo = Repository(organization_id=org.id, full_name="o/r")
    db_session.add(repo)
    db_session.flush()
    pr = _open_pr(db_session, repo, 3, None)
    raw = {"state": "closed", "requested_reviewers": [{"id": 101, "login": "keshan-dev"}]}

    record_review_requests(db_session, [pr], [raw], organization_id=org.id)

    assert db_session.scalar(select(ReviewRequest)) is None


def test_next_up_is_empty_when_nothing_waits(db_session):
    org = Organization(name="W")
    db_session.add(org)
    db_session.flush()

    assert next_up(db_session, _person(db_session, org.id), [], NOW) == []


# ---------------------------------------------------------------------------
# Agent changes
# ---------------------------------------------------------------------------


def test_workspace_health_reads_only_that_workspaces_runs(db_session):
    _, account = _account(db_session, 101, "lead-a")
    workspace = _workspace(db_session, account)
    db_session.add_all(
        [
            SyncRun(
                source="github",
                scope="a",
                status="failed",
                error_type="AUTH_FAILED",
                started_at=NOW,
            ),
            SyncRun(
                source="github",
                scope="b",
                status="success",
                started_at=NOW,
                finished_at=NOW,
                workspace_id=workspace.id,
            ),
        ]
    )
    db_session.commit()

    out = get_source_health(
        db_session, GetSourceHealthInput(workspace_id=workspace.id, sources=["github"])
    )

    assert out.sources[0].state == "fresh"


@pytest.mark.anyio
async def test_current_work_with_1_evidence_item_is_worded_as_a_fact():
    """6.6 forbids presenting current work as certain; 11.6 needs 2 items for inference."""
    tools, _, _ = make_tools(
        health=[_health("jira"), _health("github")], work_items=[_work_item(1)]
    )

    from unittest.mock import MagicMock

    insight = await run_agent(
        MagicMock(),
        subject_user_id=5,
        team_id=10,
        question_type="current_work",
        tools=tools,
        skip_llm=True,
    )

    claim = insight.likely_current_work
    assert claim.classification == "fact"
    assert claim.claim.startswith("AUTH-1 is assigned and In Progress")
    assert "Likely" not in claim.claim


@pytest.mark.anyio
async def test_a_linked_pull_request_makes_current_work_an_inference():
    """Found on real data (W11): HRS-7 with its open PR #8 still read as 1 record, because
    pull request evidence names no issue key. The stored link ties them together."""
    from unittest.mock import MagicMock

    from tests.test_orchestrator import _link, _pull_request

    tools, _, _ = make_tools(
        health=[_health("jira"), _health("github")],
        work_items=[_work_item(1).model_copy(update={"assignee_user_id": 5})],
        pull_requests=[_pull_request(7).model_copy(update={"author_user_id": 5})],
        links=[_link()],
    )

    insight = await run_agent(
        MagicMock(),
        subject_user_id=5,
        team_id=10,
        question_type="current_work",
        tools=tools,
        skip_llm=True,
    )

    claim = insight.likely_current_work
    assert claim.classification == "inference"
    assert claim.claim.startswith("Likely working on AUTH-1")
    assert {e.entity_type for e in claim.evidence} == {"work_item", "pull_request"}


# ---------------------------------------------------------------------------
# Analysis scope
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_only_confirmed_people_are_analysed(db_session, monkeypatch):
    _, account = _account(db_session, 101, "lead-a")
    workspace = _workspace(db_session, account)
    org_id = ensure_organization(db_session, workspace)
    db_session.commit()
    confirmed = _person(db_session, org_id, "1", "zara")
    people.set_status(db_session, org_id, confirmed, "confirmed")
    _person(db_session, org_id, "2", "waiting")
    seen: list[int] = []

    async def fake_member(db, workspace_id, person, uses_jira, health, now):
        seen.append(person.id)
        return MemberAnalysis(
            person_id=person.id, display_name=person.display_name, current_work=None
        )

    monkeypatch.setattr("app.workspace_analysis.analyze_member", fake_member)

    analysis = await analyze_workspace(db_session, workspace)

    assert seen == [confirmed]
    assert [m.display_name for m in analysis.members] == ["zara"]


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[auth.get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _evidence() -> EvidenceItem:
    return EvidenceItem(
        id="ev_1",
        source="github",
        entity_type="pull_request",
        entity_key="5",
        source_url="https://github.com/o/r/pull/5",
        summary="PR #5 checks failing",
        observed_at=NOW,
        retrieved_at=NOW,
        source_state="fresh",
    )


def _analysis(person_id: int, name: str) -> WorkspaceAnalysis:
    blocker = Insight(
        claim="Pull request #5 has failing CI checks.",
        classification="fact",
        confidence="HIGH",
        evidence=[_evidence()],
    )
    return WorkspaceAnalysis(
        members=[
            MemberAnalysis(
                person_id=person_id,
                display_name=name,
                current_work=None,
                blockers=[blocker],
                conflicts=["Jira shows HRS-2 Done, but linked pull request #2 is still open."],
            )
        ],
        health=[],
    )


def _owned(db: Session, client: TestClient, person_name: str = "zara"):
    user, account = _account(db, 101, "lead-a")
    workspace = _workspace(db, account, "Payments")
    org_id = ensure_organization(db, workspace)
    db.commit()
    person_id = _person(db, org_id, "777", person_name)
    people.set_status(db, org_id, person_id, "confirmed")
    log_in(client, user.id)
    return workspace, person_id


def test_the_team_view_lists_blockers_and_conflicts_with_names(client, db_session, monkeypatch):
    workspace, person_id = _owned(db_session, client)

    async def fake(db, ws, only_person_id=None):
        return _analysis(person_id, "zara")

    monkeypatch.setattr(workspace_pages, "analyze_workspace", fake)

    html = client.get(f"/w/{workspace.id}").text

    assert "Blockers (1)" in html
    assert "Blocker: zara" in html
    assert "Jira and GitHub disagree (1)" in html
    assert "Headref does not choose" in html


def test_selecting_a_member_shows_their_panel_and_loads_the_summary_later(
    client, db_session, monkeypatch
):
    workspace, person_id = _owned(db_session, client)
    asked: list[int | None] = []

    async def fake(db, ws, only_person_id=None):
        asked.append(only_person_id)
        return _analysis(person_id, "zara")

    monkeypatch.setattr(workspace_pages, "analyze_workspace", fake)

    html = client.get(f"/w/{workspace.id}?member={person_id}").text

    assert asked == [person_id]
    assert f'data-summary-src="/w/{workspace.id}/members/{person_id}/summary"' in html
    assert "Likely next" in html
    assert "cannot know what someone will pick up next" in html


def test_a_member_id_from_elsewhere_shows_the_whole_team(client, db_session, monkeypatch):
    workspace, person_id = _owned(db_session, client)
    asked: list[int | None] = []

    async def fake(db, ws, only_person_id=None):
        asked.append(only_person_id)
        return _analysis(person_id, "zara")

    monkeypatch.setattr(workspace_pages, "analyze_workspace", fake)

    client.get(f"/w/{workspace.id}?member=99999")

    assert asked == [None]


def test_the_summary_labels_a_fallback(client, db_session, monkeypatch):
    workspace, person_id = _owned(db_session, client)

    async def fake_summary(db, ws, pid):
        return MemberInsight(user_id=pid, summary="Built from findings.", fallback_used=True)

    monkeypatch.setattr(workspace_pages, "member_summary", fake_summary)

    html = client.get(f"/w/{workspace.id}/members/{person_id}/summary").text

    assert "Built from findings." in html
    assert "AI reasoning is temporarily unavailable" in html
    assert "Written by the local model" not in html


def test_no_summary_for_an_unconfirmed_or_foreign_person(client, db_session, monkeypatch):
    workspace, _ = _owned(db_session, client)
    org_id = workspace.organization_id
    waiting = _person(db_session, org_id, "888", "waiting")
    other = Organization(name="other")
    db_session.add(other)
    db_session.flush()
    foreign = _person(db_session, other.id, "999", "foreign")
    monkeypatch.setattr(workspace_pages, "member_summary", lambda *a: pytest.fail("model called"))

    for pid in (waiting, foreign):
        assert client.get(f"/w/{workspace.id}/members/{pid}/summary").status_code == 404


def test_a_hostile_claim_is_escaped(client, db_session, monkeypatch):
    workspace, person_id = _owned(db_session, client, "<img src=x onerror=alert(1)>")

    async def fake(db, ws, only_person_id=None):
        return _analysis(person_id, "<img src=x onerror=alert(1)>")

    monkeypatch.setattr(workspace_pages, "analyze_workspace", fake)

    html = client.get(f"/w/{workspace.id}").text

    assert "<img src=x onerror=alert(1)>" not in html


def test_appuser_import_is_used():
    assert AppUser.__tablename__ == "app_user"
