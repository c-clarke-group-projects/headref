"""Sync 1 workspace's connected GitHub repositories and Jira projects (W5, DEC-021).

Order: Jira first, then GitHub, so branch names and pull request titles can link to the
Jira items already stored. Each source that cannot be reached is recorded as a failed
sync_run with a typed error, so the workspace shows it as unavailable, never as empty
(rule 9). The read path is unchanged: tools still only read PostgreSQL.

Background runs go through start_workspace_sync, which refuses a second run for a
workspace that is already syncing, in this process or according to sync_run.
"""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.atlassian import API_URL, AtlassianClient
from app.integrations.crypto import CredentialError
from app.integrations.errors import IntegrationError
from app.integrations.github import GitHubClient
from app.integrations.github_app import GitHubAppClient
from app.integrations.jira import JiraClient
from app.integrations.jira_tokens import valid_access_token
from app.models.canonical import Organization
from app.models.connections import (
    GitHubInstallation,
    JiraConnection,
    WorkspaceJiraProject,
    WorkspaceRepository,
)
from app.models.operations import SyncRun
from app.models.workspace import Workspace
from app.sync import sync_github, sync_jira
from app.workspace_feed import record_changes, record_feed_failure

logger = logging.getLogger("headref.workspace_sync")

# A run still "running" after this long is treated as dead (process restarted mid-run).
STUCK_AFTER = timedelta(minutes=15)

_running: set[int] = set()
_running_lock = threading.Lock()


def ensure_organization(db: Session, workspace: Workspace) -> int:
    """The workspace's own organization id, created on first use. The caller commits."""
    if workspace.organization_id is None:
        org = Organization(name=workspace.name, created_at=datetime.now(UTC))
        db.add(org)
        db.flush()
        workspace.organization_id = org.id
    return workspace.organization_id


def has_sources(db: Session, workspace_id: int) -> bool:
    """True if the workspace has at least 1 selected repository or Jira project."""
    repo = db.scalar(
        select(WorkspaceRepository.id).where(WorkspaceRepository.workspace_id == workspace_id)
    )
    project = db.scalar(
        select(WorkspaceJiraProject.id).where(WorkspaceJiraProject.workspace_id == workspace_id)
    )
    return repo is not None or project is not None


def is_running(db: Session, workspace_id: int, now: datetime | None = None) -> bool:
    """True if a sync for this workspace is in progress here or recorded as running."""
    with _running_lock:
        if workspace_id in _running:
            return True
    cutoff = (now or datetime.now(UTC)) - STUCK_AFTER
    return (
        db.scalar(
            select(SyncRun.id).where(
                SyncRun.workspace_id == workspace_id,
                SyncRun.status == "running",
                SyncRun.started_at >= cutoff,
            )
        )
        is not None
    )


def _failed_run(
    db: Session, workspace_id: int, source: str, scope: str, error_type: str
) -> SyncRun:
    """Record that a source could not be synced at all, before any fetch started."""
    now = datetime.now(UTC)
    run = SyncRun(
        source=source,
        scope=scope[:255],
        status="failed",
        started_at=now,
        finished_at=now,
        error_type=error_type,
        workspace_id=workspace_id,
    )
    db.add(run)
    db.commit()
    logger.warning(
        "workspace sync source failed: workspace_id=%s source=%s scope=%s error=%s",
        workspace_id,
        source,
        scope,
        error_type,
    )
    return run


def sync_workspace(
    db: Session,
    workspace_id: int,
    app_client: Any = None,
    atlassian_client: Any = None,
) -> list[SyncRun]:
    """Sync every selected Jira project and repository of 1 workspace. Returns the runs."""
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.archived_at is not None:
        return []
    org_id = ensure_organization(db, workspace)
    db.commit()
    runs: list[SyncRun] = []

    # 1. Jira: 1 run for all selected projects of the connected site.
    connection = db.scalar(
        select(JiraConnection).where(JiraConnection.workspace_id == workspace_id)
    )
    keys = list(
        db.scalars(
            select(WorkspaceJiraProject.key)
            .where(WorkspaceJiraProject.workspace_id == workspace_id)
            .order_by(WorkspaceJiraProject.key)
        ).all()
    )
    if connection is not None and keys:
        scope = ",".join(keys)
        if connection.status != "active" or not connection.cloud_id:
            runs.append(_failed_run(db, workspace_id, "jira", scope, "AUTH_FAILED"))
        else:
            try:
                atlassian = atlassian_client or AtlassianClient()
                token = valid_access_token(db, connection.id, atlassian)
                jira = JiraClient(
                    base_url=f"{API_URL}/ex/jira/{connection.cloud_id}", bearer_token=token
                )
                runs.append(
                    sync_jira(
                        db,
                        scope=scope,
                        jira_client=jira,
                        organization_id=org_id,
                        workspace_id=workspace_id,
                        auto_create_people=True,
                    )
                )
            except IntegrationError as exc:
                runs.append(_failed_run(db, workspace_id, "jira", scope, exc.error_type))
            except CredentialError:
                runs.append(_failed_run(db, workspace_id, "jira", scope, "AUTH_FAILED"))

    # 2. GitHub: 1 run per selected repository, with its installation's token.
    rows = db.execute(
        select(WorkspaceRepository, GitHubInstallation)
        .join(
            GitHubInstallation, GitHubInstallation.id == WorkspaceRepository.github_installation_id
        )
        .where(WorkspaceRepository.workspace_id == workspace_id)
        .order_by(WorkspaceRepository.full_name)
    ).all()
    if rows:
        app = app_client or GitHubAppClient()
        for repo, installation in rows:
            try:
                token = app.installation_token(installation.installation_id)
            except IntegrationError as exc:
                runs.append(_failed_run(db, workspace_id, "github", repo.full_name, exc.error_type))
                continue
            client = GitHubClient(token=token)
            try:
                runs.append(
                    sync_github(
                        db,
                        scope=repo.full_name,
                        gh_client=client,
                        auto_create_contributors=True,
                        organization_id=org_id,
                        workspace_id=workspace_id,
                    )
                )
            finally:
                client.close()

    # 3. The feed: what changed since the previous sync. The synced data is already
    # committed; if working out the changes fails, the feed says so (rule 9).
    try:
        record_changes(db, workspace_id, runs)
    except Exception:
        logger.exception("feed not recorded: workspace_id=%s", workspace_id)
        record_feed_failure(db, workspace_id)
    logger.info(
        "workspace sync finished: workspace_id=%s runs=%s statuses=%s",
        workspace_id,
        len(runs),
        sorted({r.status for r in runs}),
    )
    return runs


def _claim(workspace_id: int) -> bool:
    with _running_lock:
        if workspace_id in _running:
            return False
        _running.add(workspace_id)
        return True


def _release(workspace_id: int) -> None:
    with _running_lock:
        _running.discard(workspace_id)


def _guarded_run(
    workspace_id: int, session_factory: Callable[[], Session], runner: Callable[..., Any]
) -> None:
    db = session_factory()
    try:
        runner(db, workspace_id)
    except Exception:
        logger.exception("workspace sync crashed: workspace_id=%s", workspace_id)
    finally:
        db.close()
        _release(workspace_id)


def start_workspace_sync(
    workspace_id: int,
    session_factory: Callable[[], Session],
    runner: Callable[..., Any] = sync_workspace,
) -> bool:
    """Start a background sync for the workspace. False if 1 is already running here.

    The caller checks is_running against the database first; this guards the window
    before the first sync_run row exists.
    """
    if not _claim(workspace_id):
        return False
    threading.Thread(
        target=_guarded_run,
        args=(workspace_id, session_factory, runner),
        name=f"workspace-sync-{workspace_id}",
        daemon=True,
    ).start()
    logger.info("workspace sync started: workspace_id=%s", workspace_id)
    return True


def sync_all_workspaces(
    session_factory: Callable[[], Session], runner: Callable[..., Any] = sync_workspace
) -> list[int]:
    """The scheduler's pass: sync each workspace with sources, skipping running ones."""
    db = session_factory()
    try:
        candidates = [w for w in workspaces_to_sync(db) if not is_running(db, w)]
    finally:
        db.close()
    synced: list[int] = []
    for workspace_id in candidates:
        if _claim(workspace_id):
            _guarded_run(workspace_id, session_factory, runner)
            synced.append(workspace_id)
    return synced


def workspaces_to_sync(db: Session) -> list[int]:
    """Workspaces with at least 1 selected source, for the scheduler."""
    with_repos = select(WorkspaceRepository.workspace_id)
    with_projects = select(WorkspaceJiraProject.workspace_id)
    return list(
        db.scalars(
            select(Workspace.id)
            .where(
                Workspace.archived_at.is_(None),
                Workspace.id.in_(with_repos) | Workspace.id.in_(with_projects),
            )
            .order_by(Workspace.id)
        ).all()
    )


def latest_runs(db: Session, workspace_id: int, limit: int = 200) -> list[SyncRun]:
    """The newest run per (source, scope) of a workspace, Jira first, then by scope."""
    seen: set[tuple[str, str]] = set()
    latest: list[SyncRun] = []
    for run in db.scalars(
        select(SyncRun)
        .where(SyncRun.workspace_id == workspace_id)
        .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
        .limit(limit)
    ).all():
        key = (run.source, run.scope)
        if key not in seen:
            seen.add(key)
            latest.append(run)
    return sorted(latest, key=lambda r: (r.source != "jira", r.scope))


def sync_history(db: Session, workspace_id: int, limit: int = 50) -> list[SyncRun]:
    """The workspace's most recent runs, newest first."""
    return list(
        db.scalars(
            select(SyncRun)
            .where(SyncRun.workspace_id == workspace_id)
            .order_by(SyncRun.started_at.desc(), SyncRun.id.desc())
            .limit(limit)
        ).all()
    )
