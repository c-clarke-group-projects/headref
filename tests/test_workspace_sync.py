"""Per-workspace sync: scoping, failures, the running guard and the pages (W5, DEC-021)."""

import threading
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import workspace_sync
from app.db import Base
from app.integrations.errors import AuthError
from app.main import app
from app.models.canonical import AppUser, Repository
from app.models.connections import (
    GitHubInstallation,
    JiraConnection,
    WorkspaceJiraProject,
    WorkspaceRepository,
)
from app.models.operations import SyncCursor, SyncRun
from app.models.work import WorkItem
from app.models.workspace import Workspace
from app.web import auth, workspace_pages
from tests.conftest import log_in
from tests.test_workspaces import _account, _add_member, _workspace

REPO = "lead-a/headref-sandbox"


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _github_client() -> MagicMock:
    """A GitHub client returning 1 repository, 1 PR by a contributor, 1 commit."""
    client = MagicMock()
    client.get_repository.return_value = {
        "id": 9001,
        "name": "headref-sandbox",
        "full_name": REPO,
        "default_branch": "main",
    }
    client.list_pull_requests.return_value = [
        {
            "id": 1,
            "number": 1,
            "title": "HRS-1 Rotate refresh tokens",
            "body": "",
            "state": "open",
            "draft": False,
            "head": {"ref": "feature/HRS-1-tokens", "sha": "abc"},
            "created_at": "2026-10-18T10:00:00Z",
            "updated_at": "2026-10-18T11:00:00Z",
            "user": {"id": 101, "login": "lead-a"},
        }
    ]
    client.list_commits.return_value = []
    client.list_reviews.return_value = []
    client.get_checks_state.return_value = "passing"
    return client


class FakeApp:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def installation_token(self, installation_id: int) -> str:
        if self.fail:
            raise AuthError("installation suspended", status_code=401)
        return f"ghs_{installation_id}"


def _workspace_with_repo(db: Session, name: str, owner_gh_id: int) -> Workspace:
    _, account = _account(db, owner_gh_id, f"lead-{owner_gh_id}")
    workspace = _workspace(db, account, name)
    inst = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=55 + owner_gh_id,
        account_login="lead-a",
        account_type="User",
    )
    db.add(inst)
    db.flush()
    db.add(
        WorkspaceRepository(
            workspace_id=workspace.id,
            github_installation_id=inst.id,
            github_repo_id=9001,
            full_name=REPO,
        )
    )
    db.commit()
    return workspace


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    client = _github_client()
    seen_tokens: list[str] = []

    def factory(token: str) -> MagicMock:
        seen_tokens.append(token)
        return client

    monkeypatch.setattr(workspace_sync, "GitHubClient", factory)
    client.seen_tokens = seen_tokens
    return client


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def test_a_workspace_sync_writes_into_its_own_organization(db_session, github):
    workspace = _workspace_with_repo(db_session, "Payments", 101)

    runs = workspace_sync.sync_workspace(db_session, workspace.id, app_client=FakeApp())

    db_session.refresh(workspace)
    assert [r.status for r in runs] == ["success"]
    assert runs[0].workspace_id == workspace.id
    assert github.seen_tokens == [f"ghs_{55 + 101}"]
    repo = db_session.scalar(select(Repository))
    assert repo.organization_id == workspace.organization_id
    cursor = db_session.scalar(select(SyncCursor))
    assert cursor.scope == f"w{workspace.id}:{REPO}"


def test_2_workspaces_can_sync_the_same_repository_without_sharing_data(db_session, github):
    first = _workspace_with_repo(db_session, "Payments", 101)
    second = _workspace_with_repo(db_session, "Audit", 202)

    workspace_sync.sync_workspace(db_session, first.id, app_client=FakeApp())
    workspace_sync.sync_workspace(db_session, second.id, app_client=FakeApp())

    db_session.refresh(first)
    db_session.refresh(second)
    repos = db_session.scalars(select(Repository)).all()
    assert sorted(r.organization_id for r in repos) == sorted(
        [first.organization_id, second.organization_id]
    )
    contributors = db_session.scalars(
        select(AppUser).where(
            AppUser.organization_id.in_([first.organization_id, second.organization_id])
        )
    ).all()
    assert len(contributors) == 2  # the same GitHub author, once per workspace


def test_an_unreachable_installation_is_a_failed_run_not_silence(db_session, github):
    workspace = _workspace_with_repo(db_session, "Payments", 101)

    runs = workspace_sync.sync_workspace(db_session, workspace.id, app_client=FakeApp(fail=True))

    assert [(r.status, r.error_type, r.scope) for r in runs] == [("failed", "AUTH_FAILED", REPO)]
    assert db_session.scalar(select(Repository)) is None


def test_a_jira_connection_needing_reconnect_is_a_failed_run(db_session, github):
    workspace = _workspace_with_repo(db_session, "Payments", 101)
    connection = JiraConnection(
        workspace_id=workspace.id,
        cloud_id="c1",
        access_token_enc="x",
        refresh_token_enc="y",
        expires_at=datetime.now(UTC),
        scopes="",
        status="auth_failed",
    )
    db_session.add(connection)
    db_session.flush()
    db_session.add(
        WorkspaceJiraProject(
            workspace_id=workspace.id,
            jira_connection_id=connection.id,
            project_id="10001",
            key="HRS",
            name="sandbox",
        )
    )
    db_session.commit()

    runs = workspace_sync.sync_workspace(db_session, workspace.id, app_client=FakeApp())

    assert (runs[0].source, runs[0].status, runs[0].error_type) == ("jira", "failed", "AUTH_FAILED")
    assert runs[1].source == "github"


def test_jira_uses_the_bearer_token_on_the_atlassian_gateway(db_session, github, monkeypatch):
    workspace = _workspace(db_session, _account(db_session, 101, "lead-a")[1], "Payments")
    connection = JiraConnection(
        workspace_id=workspace.id,
        cloud_id="cloud-1",
        site_url="https://keshanuni.atlassian.net",
        access_token_enc="x",
        refresh_token_enc="y",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes="",
        status="active",
    )
    db_session.add(connection)
    db_session.flush()
    db_session.add(
        WorkspaceJiraProject(
            workspace_id=workspace.id,
            jira_connection_id=connection.id,
            project_id="10001",
            key="HRS",
            name="sandbox",
        )
    )
    db_session.commit()
    monkeypatch.setattr(workspace_sync, "valid_access_token", lambda db, cid, client: "at-1")
    built: dict[str, Any] = {}

    def jira_factory(base_url: str, bearer_token: str) -> MagicMock:
        built.update(base_url=base_url, token=bearer_token)
        jira = MagicMock()
        jira.list_projects.return_value = [
            {"id": "10001", "key": "HRS", "name": "sandbox"},
            {"id": "10000", "key": "MP", "name": "Other project, not selected"},
        ]
        jira.search_issues.return_value = [
            {
                "id": "1",
                "key": "HRS-1",
                "fields": {
                    "summary": "Rotate refresh tokens",
                    "status": {"name": "To Do", "statusCategory": {"key": "new"}},
                    "project": {"key": "HRS"},
                    "created": "2026-10-18T10:00:00.000+0000",
                    "updated": "2026-10-18T10:00:00.000+0000",
                },
            }
        ]
        jira.get_remote_links.return_value = []
        built["client"] = jira
        return jira

    monkeypatch.setattr(workspace_sync, "JiraClient", jira_factory)

    runs = workspace_sync.sync_workspace(db_session, workspace.id, atlassian_client=object())

    assert built["base_url"] == "https://api.atlassian.com/ex/jira/cloud-1"
    assert built["token"] == "at-1"
    assert runs[0].scope == "HRS" and runs[0].status == "success"
    jql = built["client"].search_issues.call_args.kwargs["jql"]
    assert jql == 'project in ("HRS") ORDER BY key'
    # Links open the site people use, never the API gateway or a placeholder (W11).
    item = db_session.scalar(select(WorkItem))
    assert item.source_url == "https://keshanuni.atlassian.net/browse/HRS-1"


# ---------------------------------------------------------------------------
# Running guard and scheduler
# ---------------------------------------------------------------------------


def test_a_recent_running_row_counts_as_running_and_an_old_one_does_not(db_session):
    workspace = _workspace(db_session, _account(db_session, 101, "lead-a")[1], "Payments")
    now = datetime.now(UTC)
    db_session.add(
        SyncRun(
            source="github", scope=REPO, status="running", started_at=now, workspace_id=workspace.id
        )
    )
    db_session.commit()

    assert workspace_sync.is_running(db_session, workspace.id, now=now)
    assert not workspace_sync.is_running(db_session, workspace.id, now=now + timedelta(hours=1))


def test_a_second_start_is_refused_while_the_first_runs(db_session):
    release = threading.Event()
    calls: list[int] = []

    def runner(db: Any, workspace_id: int) -> None:
        calls.append(workspace_id)
        release.wait(5)

    assert workspace_sync.start_workspace_sync(7, lambda: MagicMock(), runner)
    assert not workspace_sync.start_workspace_sync(7, lambda: MagicMock(), runner)
    release.set()
    for _ in range(50):
        if 7 not in workspace_sync._running:
            break
        threading.Event().wait(0.05)
    assert calls == [7]
    assert 7 not in workspace_sync._running


def test_the_scheduler_syncs_only_workspaces_with_sources(db_session):
    with_repo_id = _workspace_with_repo(db_session, "Payments", 101).id
    _workspace(db_session, _account(db_session, 202, "lead-b")[1], "Empty")
    synced: list[int] = []

    result = workspace_sync.sync_all_workspaces(
        lambda: db_session, runner=lambda db, wid: synced.append(wid)
    )

    assert result == synced == [with_repo_id]


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


def test_sync_now_with_nothing_selected_says_so(client, db_session):
    user, account = _account(db_session, 101, "lead-a")
    workspace = _workspace(db_session, account, "Payments")
    log_in(client, user.id)

    response = client.post(f"/w/{workspace.id}/sync", follow_redirects=False)

    assert response.headers["location"] == f"/w/{workspace.id}?sync=nothing_to_sync"


def test_sync_now_starts_in_the_background(client, db_session, monkeypatch):
    workspace = _workspace_with_repo(db_session, "Payments", 101)
    user = db_session.scalar(select(AppUser).order_by(AppUser.id))
    log_in(client, user.id)
    started: list[int] = []
    monkeypatch.setattr(
        workspace_pages, "start_workspace_sync", lambda wid, factory: started.append(wid) or True
    )

    response = client.post(f"/w/{workspace.id}/sync", follow_redirects=False)

    assert response.headers["location"] == f"/w/{workspace.id}?sync=started"
    assert started == [workspace.id]


def test_sync_now_while_running_starts_nothing(client, db_session, monkeypatch):
    workspace = _workspace_with_repo(db_session, "Payments", 101)
    user = db_session.scalar(select(AppUser).order_by(AppUser.id))
    log_in(client, user.id)
    db_session.add(
        SyncRun(
            source="github",
            scope=REPO,
            status="running",
            started_at=datetime.now(UTC),
            workspace_id=workspace.id,
        )
    )
    db_session.commit()
    monkeypatch.setattr(
        workspace_pages, "start_workspace_sync", lambda wid, factory: pytest.fail("started")
    )

    response = client.post(f"/w/{workspace.id}/sync", follow_redirects=False)

    assert response.headers["location"] == f"/w/{workspace.id}?sync=already_running"


def test_a_viewer_cannot_start_a_sync(client, db_session):
    workspace = _workspace_with_repo(db_session, "Payments", 101)
    viewer_user, viewer = _account(db_session, 303, "viewer-c")
    _add_member(db_session, workspace, viewer, "viewer")
    log_in(client, viewer_user.id)

    assert client.post(f"/w/{workspace.id}/sync", follow_redirects=False).status_code == 403


def test_status_reports_the_latest_run_per_source(client, db_session):
    workspace = _workspace_with_repo(db_session, "Payments", 101)
    user = db_session.scalar(select(AppUser).order_by(AppUser.id))
    log_in(client, user.id)
    older = datetime.now(UTC) - timedelta(hours=2)
    db_session.add_all(
        [
            SyncRun(
                source="github",
                scope=REPO,
                status="success",
                started_at=older,
                finished_at=older,
                workspace_id=workspace.id,
            ),
            SyncRun(
                source="github",
                scope=REPO,
                status="failed",
                error_type="RATE_LIMITED",
                started_at=older + timedelta(hours=1),
                workspace_id=workspace.id,
            ),
        ]
    )
    db_session.commit()

    body = client.get(f"/api/workspaces/{workspace.id}/sync/status").json()

    assert body["running"] is False
    assert [(r["status"], r["error_type"]) for r in body["runs"]] == [("failed", "RATE_LIMITED")]


def test_another_workspaces_status_is_404(client, db_session):
    workspace = _workspace_with_repo(db_session, "Payments", 101)
    outsider, _ = _account(db_session, 909, "outsider")
    log_in(client, outsider.id)

    assert client.get(f"/api/workspaces/{workspace.id}/sync/status").status_code == 404
    assert client.get(f"/w/{workspace.id}/syncs").status_code == 404


def test_the_workspace_page_shows_a_failure_and_polls_while_running(client, db_session):
    workspace = _workspace_with_repo(db_session, "Payments", 101)
    user = db_session.scalar(select(AppUser).order_by(AppUser.id))
    log_in(client, user.id)
    db_session.add(
        SyncRun(
            source="jira",
            scope="HRS",
            status="failed",
            error_type="AUTH_FAILED",
            started_at=datetime.now(UTC),
            workspace_id=workspace.id,
        )
    )
    db_session.add(
        SyncRun(
            source="github",
            scope=REPO,
            status="running",
            started_at=datetime.now(UTC),
            workspace_id=workspace.id,
        )
    )
    db_session.commit()

    html = client.get(f"/w/{workspace.id}").text

    assert f'data-ws-sync-poll="/api/workspaces/{workspace.id}/sync/status"' in html
    assert "Failed" in html and "not shown as empty" in html
    assert "Sync history" in html


def test_a_sync_running_in_another_process_is_skipped(db_session, github, monkeypatch):
    """The advisory lock is PostgreSQL only; here it reports another holder."""
    from contextlib import contextmanager

    workspace = _workspace_with_repo(db_session, "Payments", 101)

    @contextmanager
    def held_elsewhere(db, workspace_id):
        yield False

    monkeypatch.setattr(workspace_sync, "_workspace_lock", held_elsewhere)

    assert workspace_sync.sync_workspace(db_session, workspace.id, app_client=FakeApp()) == []
    assert db_session.scalar(select(SyncRun)) is None
