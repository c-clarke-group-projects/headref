"""Workspace feed: snapshots, differences and the What changed section (W8, DEC-025)."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import people, workspace_feed, workspace_sync
from app.db import Base
from app.integrations.identity_resolver import resolve_actor
from app.main import app
from app.models.canonical import Project
from app.models.feed import FindingSnapshot, WorkspaceEvent
from app.models.operations import SyncRun
from app.models.work import WorkItem
from app.web import auth, workspace_pages
from app.workspace_analysis import WorkspaceAnalysis
from app.workspace_feed import (
    WORK_STATE,
    diff_findings,
    diff_work_state,
    feed,
    mark_seen,
    person_findings,
    record_changes,
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


def _setup(db: Session):
    """A workspace with its organization, 1 confirmed person and 1 Jira project."""
    user, account = _account(db, 101, "lead-a")
    workspace = _workspace(db, account)
    org_id = ensure_organization(db, workspace)
    db.commit()
    person_id = resolve_actor(db, "github", "777", "zara", auto_create=True, organization_id=org_id)
    db.commit()
    people.set_status(db, org_id, person_id, "confirmed")
    project = Project(organization_id=org_id, key="HRS", name="sandbox")
    db.add(project)
    db.commit()
    return user, account, workspace, person_id, project


def _item(db: Session, project: Project, person_id: int, key: str, status: str, raw: str):
    db.add(
        WorkItem(
            project_id=project.id,
            external_id=key,
            title=f"Task {key}",
            status=status,
            raw_status=raw,
            assignee_app_user_id=person_id,
            source_url=f"https://x.atlassian.net/browse/{key}",
            source_updated_at=NOW,
            retrieved_at=NOW,
        )
    )
    db.commit()


def _run(source: str, status: str, workspace_id: int, error: str | None = None) -> SyncRun:
    return SyncRun(
        source=source,
        scope="HRS" if source == "jira" else "o/r",
        status=status,
        error_type=error,
        started_at=NOW,
        finished_at=NOW,
        workspace_id=workspace_id,
    )


# ---------------------------------------------------------------------------
# Differences
# ---------------------------------------------------------------------------


def test_a_day_counter_in_the_text_is_not_a_change():
    before = {"blocker:BL-5:o/r#5": {"kind": "blocker", "text": "open for 3 days", "url": None}}
    after = {"blocker:BL-5:o/r#5": {"kind": "blocker", "text": "open for 4 days", "url": None}}

    assert diff_findings(before, after, person_id=1) == []


def test_new_and_no_longer_detected_findings():
    before = {"blocker:BL-1:HRS-4": {"kind": "blocker", "text": "HRS-4 is Blocked.", "url": "u"}}
    after = {
        "conflict:CF-2:HRS-2:pr": {"kind": "conflict", "text": "HRS-2 Done, PR open.", "url": None}
    }

    changes = diff_findings(before, after, person_id=7)

    assert [(c.kind, c.text, c.person_id) for c in changes] == [
        ("conflict_new", "New Jira and GitHub conflict: HRS-2 Done, PR open.", 7),
        ("blocker_cleared", "No longer detected, blocker: HRS-4 is Blocked.", 7),
    ]


def test_issue_moves_pull_request_changes_and_sync_failures():
    before = {
        "items": {"HRS-1": {"status": "To Do", "title": "A", "url": "u1", "person": 3}},
        "prs": {
            "o/r#5": {"state": "open", "title": "Fix", "url": "p5", "person": 3},
            "o/r#7": {"state": "draft", "title": "WIP", "url": "p7", "person": 3},
        },
        "syncs": {"github:o/r": {"failed": False, "error": None}},
    }
    after = {
        "items": {
            "HRS-1": {"status": "In Progress", "title": "A", "url": "u1", "person": 3},
            "HRS-9": {"status": "To Do", "title": "B", "url": "u9", "person": None},
        },
        "prs": {
            "o/r#5": {"state": "merged", "title": "Fix", "url": "p5", "person": 3},
            "o/r#7": {"state": "open", "title": "WIP", "url": "p7", "person": 3},
            "o/r#8": {"state": "open", "title": "New", "url": "p8", "person": 3},
        },
        "syncs": {"github:o/r": {"failed": True, "error": "AUTH_FAILED"}},
    }

    texts = [c.text for c in diff_work_state(before, after)]

    assert texts == [
        "HRS-1 moved from To Do to In Progress: A",
        "HRS-9 added in Jira, status To Do: B",
        "Pull request o/r#5 merged: Fix",
        "Pull request o/r#7 marked ready for review: WIP",
        "Pull request o/r#8 opened: New",
        "GitHub sync failed for o/r: AUTH_FAILED.",
    ]


def test_a_sync_that_keeps_failing_is_reported_once_and_its_recovery_once():
    failed = {"syncs": {"jira:HRS": {"failed": True, "error": "TIMEOUT"}}}
    ok = {"syncs": {"jira:HRS": {"failed": False, "error": None}}}

    assert diff_work_state(failed, failed) == []
    assert [c.text for c in diff_work_state(failed, ok)] == ["Jira sync for HRS works again."]


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def test_the_first_sync_is_a_baseline_and_the_next_records_the_move(db_session):
    _, _, workspace, person_id, project = _setup(db_session)
    _item(db_session, project, person_id, "HRS-1", "todo", "To Do")

    assert record_changes(db_session, workspace.id, [], now=NOW) == 0

    item = db_session.scalar(select(WorkItem))
    item.status, item.raw_status = "in_progress", "In Progress"
    db_session.commit()
    assert record_changes(db_session, workspace.id, [], now=NOW + timedelta(minutes=15)) == 1

    event = db_session.scalar(select(WorkspaceEvent))
    assert event.text == "HRS-1 moved from To Do to In Progress: Task HRS-1"
    assert event.person_id == person_id
    assert event.source_url == "https://x.atlassian.net/browse/HRS-1"


def test_a_failed_jira_sync_never_reads_as_issue_changes(db_session):
    _, _, workspace, person_id, project = _setup(db_session)
    _item(db_session, project, person_id, "HRS-1", "todo", "To Do")
    record_changes(db_session, workspace.id, [], now=NOW)
    db_session.query(WorkItem).delete()
    db_session.commit()

    record_changes(db_session, workspace.id, [_run("jira", "failed", workspace.id, "TIMEOUT")])

    kinds = [e.kind for e in db_session.scalars(select(WorkspaceEvent))]
    assert kinds == ["sync_failed"]
    snapshot = db_session.scalar(
        select(FindingSnapshot).where(FindingSnapshot.subject == WORK_STATE)
    )
    assert "HRS-1" in snapshot.data["items"]


def test_a_person_whose_sources_are_unavailable_keeps_their_findings(db_session, monkeypatch):
    _, _, workspace, person_id, _ = _setup(db_session)
    answers = iter(
        [
            {"blocker:BL-1:HRS-4": {"kind": "blocker", "text": "HRS-4 is Blocked.", "url": None}},
            None,
        ]
    )
    monkeypatch.setattr(workspace_feed, "person_findings", lambda *a: next(answers))

    record_changes(db_session, workspace.id, [], now=NOW)
    record_changes(db_session, workspace.id, [], now=NOW + timedelta(minutes=15))

    assert db_session.scalar(select(WorkspaceEvent)) is None
    snapshot = db_session.scalar(
        select(FindingSnapshot).where(FindingSnapshot.subject == f"person:{person_id}")
    )
    assert "blocker:BL-1:HRS-4" in snapshot.data["findings"]


def test_findings_are_keyed_by_rule_and_entity():
    blocked = _work_item(4).model_copy(update={"status": "blocked", "raw_status": "Blocked"})
    tools, _, _ = make_tools(
        health=[_health("jira"), _health("github")], work_items=[blocked], open_items=[blocked]
    )

    findings = person_findings(MagicMock(), 1, 5, True, NOW, tools=tools)

    assert findings["blocker:BL-1:AUTH-4"] == {
        "kind": "blocker",
        "text": "AUTH-4 has status Blocked in Jira.",
        "url": "https://example.atlassian.net/browse/AUTH-1",
    }


def test_an_unavailable_source_gives_no_findings_rather_than_none_found():
    tools, _, _ = make_tools(
        health=[_health("jira", "unavailable", "AUTH_FAILED"), _health("github")],
        work_items=[_work_item(4)],
    )

    assert person_findings(MagicMock(), 1, 5, True, NOW, tools=tools) is None


def test_a_feed_failure_is_shown_not_hidden(db_session, monkeypatch):
    _, _, workspace, _, _ = _setup(db_session)

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(workspace_sync, "record_changes", broken)

    workspace_sync.sync_workspace(db_session, workspace.id)

    event = db_session.scalar(select(WorkspaceEvent))
    assert event.kind == "feed_failed"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _event(db: Session, workspace_id: int, person_id: int | None, text: str, at: datetime):
    db.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            person_id=person_id,
            kind="item_moved",
            text=text,
            occurred_at=at,
        )
    )
    db.commit()


def test_feed_is_newest_first_filters_by_person_and_marks_new(db_session):
    _, _, workspace, person_id, _ = _setup(db_session)
    _event(db_session, workspace.id, person_id, "old", NOW - timedelta(hours=2))
    _event(db_session, workspace.id, None, "sync", NOW - timedelta(minutes=30))
    _event(db_session, workspace.id, person_id, "fresh", NOW)

    everything = feed(db_session, workspace.id, last_seen_at=NOW - timedelta(hours=1))
    theirs = feed(db_session, workspace.id, last_seen_at=None, person_id=person_id)

    assert [(f.text, f.is_new) for f in everything] == [
        ("fresh", True),
        ("sync", True),
        ("old", False),
    ]
    assert [f.text for f in theirs] == ["fresh", "old"]


def test_mark_seen_returns_the_previous_visit(db_session):
    _, account, workspace, _, _ = _setup(db_session)

    assert mark_seen(db_session, workspace.id, account.id, now=NOW) is None
    later = mark_seen(db_session, workspace.id, account.id, now=NOW + timedelta(hours=1))
    assert later.replace(tzinfo=UTC) == NOW


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[auth.get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_the_page_shows_the_feed_and_new_only_until_seen(client, db_session, monkeypatch):
    user, _, workspace, person_id, _ = _setup(db_session)
    _event(db_session, workspace.id, person_id, "HRS-1 moved from To Do to Done: <b>x</b>", NOW)

    async def empty(db, ws, only_person_id=None):
        return WorkspaceAnalysis(members=[], health=[])

    monkeypatch.setattr(workspace_pages, "analyze_workspace", empty)
    log_in(client, user.id)

    first = client.get(f"/w/{workspace.id}").text
    second = client.get(f"/w/{workspace.id}").text

    assert "HRS-1 moved from To Do to Done: &lt;b&gt;x&lt;/b&gt;" in first
    assert ">New</span>" in first
    assert ">New</span>" not in second


def test_the_page_says_when_nothing_changed_yet(client, db_session, monkeypatch):
    user, _, workspace, _, _ = _setup(db_session)

    async def empty(db, ws, only_person_id=None):
        return WorkspaceAnalysis(members=[], health=[])

    monkeypatch.setattr(workspace_pages, "analyze_workspace", empty)
    log_in(client, user.id)

    assert "No changes recorded yet" in client.get(f"/w/{workspace.id}").text
