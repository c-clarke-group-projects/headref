"""Scheduled synchronization runner and concurrency controller (P2-010, Issue #48).

Runs ingestion automatically on an interval defined by SYNC_INTERVAL_MINUTES.
Enforces concurrency control so identical (source, scope) syncs never overlap,
and recovers from stuck running states after STUCK_SYNC_TIMEOUT_MINUTES (DEC-016).
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (
    SCHEDULER_ENABLED,
    STUCK_SYNC_TIMEOUT_MINUTES,
    SYNC_INTERVAL_MINUTES,
    github_scopes,
    jira_scopes,
    settings,
)
from app.db import get_sync_session
from app.models.operations import SyncRun
from app.sync import sync_github, sync_jira

logger = logging.getLogger("headref.scheduler")


def recover_stuck_syncs(
    session: Session,
    source: str,
    scope: str,
    timeout_minutes: int = STUCK_SYNC_TIMEOUT_MINUTES,
    now: datetime | None = None,
) -> bool:
    """Check if a sync is currently running.

    If a sync has been running longer than timeout_minutes, it is marked as failed
    due to timeout so subsequent syncs are not blocked indefinitely. Returns True
    if a sync is still legitimately running and should not be started again.
    """
    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(minutes=timeout_minutes)

    stmt = select(SyncRun).where(
        SyncRun.source == source,
        SyncRun.scope == scope,
        SyncRun.status == "running",
    )
    running_runs = session.scalars(stmt).all()

    has_active_run = False
    for r in running_runs:
        # Check if stuck
        started_at = r.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)

        if started_at < cutoff:
            logger.warning(
                "SyncRun %d for (%s, %s) stuck since %s; marking failed (timeout)",
                r.id,
                source,
                scope,
                started_at,
            )
            r.status = "failed"
            r.error_type = "TIMEOUT"
            r.error_detail = "Sync marked as timed out / stuck after timeout period"
            r.finished_at = current_time
            session.commit()
        else:
            has_active_run = True

    return has_active_run


def resolve_team_scopes(team_id: int) -> tuple[list[str], list[str]]:
    """GitHub and Jira scopes for a team, as (github, jira).

    The MVP ingests 1 team, so the scopes come from GITHUB_REPOS and JIRA_PROJECT_KEYS
    and are the same whatever team id is asked for. Per team scope is a column on the
    team table, not a configuration value, and is deferred until a second team exists.
    The parameter is here so call sites already pass it when that day comes.
    """
    return github_scopes(), jira_scopes()


def _aggregate(statuses: list[str]) -> str | None:
    """Collapse per scope statuses into 1 value for the tick result.

    Failure wins, because a tick that failed anywhere has not fully succeeded. If every
    scope was skipped the tick did nothing. Otherwise report the first real status.
    """
    if not statuses:
        return None
    if "failed" in statuses:
        return "failed"
    if all(s == "skipped_running" for s in statuses):
        return "skipped_running"
    return next(s for s in statuses if s != "skipped_running")


def _run_source_tick(
    session: Session,
    source: str,
    scopes: list[str],
    sync_fn: Any,
    team_id: int,
    stuck_timeout_minutes: int,
) -> tuple[str | None, dict[str, str]]:
    """Run 1 source across every configured scope, guarding each scope separately."""
    per_scope: dict[str, str] = {}

    for scope in scopes:
        if recover_stuck_syncs(
            session=session,
            source=source,
            scope=scope,
            timeout_minutes=stuck_timeout_minutes,
        ):
            logger.warning("%s sync for %s already running; skipping tick", source, scope)
            per_scope[scope] = "skipped_running"
            continue

        try:
            run = sync_fn(session, team_id=team_id, scope=scope)
            per_scope[scope] = run.status
            logger.info(
                "%s sync finished for scope=%s team=%d status=%s",
                source,
                scope,
                team_id,
                run.status,
            )
        except Exception as exc:
            logger.error("Error during scheduled %s sync for %s: %s", source, scope, exc)
            per_scope[scope] = "failed"

    return _aggregate(list(per_scope.values())), per_scope


def run_scheduled_tick(
    session: Session,
    team_id: int = 1,
    gh_scope: str | None = None,
    jira_scope: str | None = None,
    stuck_timeout_minutes: int = STUCK_SYNC_TIMEOUT_MINUTES,
) -> dict[str, Any]:
    """Execute one scheduled sync tick across GitHub and Jira with concurrency guards.

    Scopes default to the deployment configuration. Pass gh_scope or jira_scope to run
    a single named scope instead, which is what the tests and manual runs do.
    """
    configured_gh, configured_jira = resolve_team_scopes(team_id)
    gh_scopes = [gh_scope] if gh_scope else configured_gh
    jira_scope_list = [jira_scope] if jira_scope else configured_jira

    gh_status, gh_detail = _run_source_tick(
        session, "github", gh_scopes, sync_github, team_id, stuck_timeout_minutes
    )
    jira_status, jira_detail = _run_source_tick(
        session, "jira", jira_scope_list, sync_jira, team_id, stuck_timeout_minutes
    )

    return {
        "github": gh_status,
        "jira": jira_status,
        "details": {"github": gh_detail, "jira": jira_detail},
    }


async def scheduler_loop(
    interval_seconds: float,
    stop_event: asyncio.Event,
    team_id: int = 1,
) -> None:
    """Asynchronous background loop that executes ticks until stop_event is set."""
    logger.info("Scheduler loop started with interval of %.1f seconds", interval_seconds)
    while not stop_event.is_set():
        try:
            session = get_sync_session()
            try:
                run_scheduled_tick(session, team_id=team_id)
            finally:
                session.close()
        except Exception as exc:
            logger.error("Unexpected error in scheduler loop: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            # Expected timeout when stop_event is not set: proceed to next tick
            pass

    logger.info("Scheduler loop stopped.")


def start_scheduler_task(
    interval_minutes: int | None = None,
    enabled: bool | None = None,
    team_id: int = 1,
) -> tuple[asyncio.Task[None] | None, asyncio.Event | None]:
    """Start the scheduler background task if enabled."""
    is_enabled = (
        enabled
        if enabled is not None
        else (settings.scheduler_enabled if settings else SCHEDULER_ENABLED)
    )
    if not is_enabled:
        logger.info("Scheduler is disabled via configuration.")
        return None, None

    interval = (
        interval_minutes
        if interval_minutes is not None
        else (settings.sync_interval_minutes if settings else SYNC_INTERVAL_MINUTES)
    )
    interval_sec = max(1.0, interval * 60.0)

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        scheduler_loop(
            interval_seconds=interval_sec,
            stop_event=stop_event,
            team_id=team_id,
        )
    )
    return task, stop_event


async def stop_scheduler_task(
    task: asyncio.Task[None] | None,
    stop_event: asyncio.Event | None,
) -> None:
    """Signal stop_event and cleanly cancel and await scheduler task."""
    if stop_event:
        stop_event.set()
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
