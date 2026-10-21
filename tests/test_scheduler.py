"""Unit tests for scheduled synchronization and concurrency controls (P2-010, Issue #48)."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models.canonical import Organization, Repository, Team
from app.models.operations import SyncRun
from app.scheduler import (
    recover_stuck_syncs,
    resolve_team_scopes,
    run_scheduled_tick,
    start_scheduler_task,
    stop_scheduler_task,
)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_scheduler_disabled_flag_does_not_start() -> None:
    """Setting enabled=False prevents the scheduler from starting."""
    task, stop_event = start_scheduler_task(enabled=False)
    assert task is None
    assert stop_event is None


def test_recover_stuck_sync_marks_failed(db_session: Session) -> None:
    """A running sync older than timeout is marked failed with error_type TIMEOUT."""
    now = datetime.now(UTC)
    stuck_time = now - timedelta(minutes=25)

    stuck_run = SyncRun(
        source="github",
        scope="keshan-dev/headref",
        status="running",
        started_at=stuck_time,
    )
    db_session.add(stuck_run)
    db_session.commit()

    is_running = recover_stuck_syncs(
        session=db_session,
        source="github",
        scope="keshan-dev/headref",
        timeout_minutes=15,
        now=now,
    )
    assert is_running is False

    db_session.refresh(stuck_run)
    assert stuck_run.status == "failed"
    assert stuck_run.error_type == "TIMEOUT"
    assert stuck_run.finished_at is not None


def test_active_sync_prevents_duplicate_run(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """A currently active sync within timeout threshold blocks new concurrent syncs."""
    # The tick also runs the Jira path. Without this stub it called the real Jira site
    # from .env (caught by the conftest network guard, P6-001).
    monkeypatch.setattr(
        "app.scheduler.sync_jira", lambda *args, **kwargs: MagicMock(status="success")
    )
    now = datetime.now(UTC)
    recent_time = now - timedelta(minutes=2)

    active_run = SyncRun(
        source="github",
        scope="keshan-dev/headref",
        status="running",
        started_at=recent_time,
    )
    db_session.add(active_run)
    db_session.commit()

    is_running = recover_stuck_syncs(
        session=db_session,
        source="github",
        scope="keshan-dev/headref",
        timeout_minutes=15,
        now=now,
    )
    assert is_running is True

    # When run_scheduled_tick runs, GitHub is skipped
    results = run_scheduled_tick(
        session=db_session,
        gh_scope="keshan-dev/headref",
        jira_scope="ALL",
        stuck_timeout_minutes=15,
    )
    assert results["github"] == "skipped_running"


def test_scheduled_tick_executes_sync_paths(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    """run_scheduled_tick invokes sync_github and sync_jira."""
    mock_gh_run = MagicMock(status="success")
    mock_jira_run = MagicMock(status="success")

    monkeypatch.setattr("app.scheduler.sync_github", lambda *args, **kwargs: mock_gh_run)
    monkeypatch.setattr("app.scheduler.sync_jira", lambda *args, **kwargs: mock_jira_run)

    results = run_scheduled_tick(
        session=db_session,
        gh_scope="keshan-dev/headref",
        jira_scope="ALL",
    )
    assert results["github"] == "success"
    assert results["jira"] == "success"


def test_failed_tick_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
) -> None:
    """A failed tick logs the error and does not raise an unhandled exception."""
    monkeypatch.setattr(
        "app.scheduler.sync_github",
        MagicMock(side_effect=RuntimeError("GitHub connection failed")),
    )
    monkeypatch.setattr(
        "app.scheduler.sync_jira",
        lambda *args, **kwargs: MagicMock(status="success"),
    )

    results = run_scheduled_tick(session=db_session)
    assert results["github"] == "failed"
    assert results["jira"] == "success"


def test_scheduler_lifecycle_start_and_stop() -> None:
    """start_scheduler_task starts asyncio task and stop_scheduler_task cleanly cancels it."""

    # Driven with asyncio.run rather than an async test, so the suite needs no
    # async pytest plugin.
    async def lifecycle() -> None:
        task, stop_event = start_scheduler_task(
            interval_minutes=1,
            enabled=True,
        )
        assert task is not None
        assert stop_event is not None
        assert not task.done()

        await stop_scheduler_task(task, stop_event)
        assert task.done()

    asyncio.run(lifecycle())


def test_the_legacy_tick_reads_only_its_own_teams_repositories(db_session: Session) -> None:
    """Workspace repositories sync through their GitHub App, never this GITHUB_TOKEN path."""
    legacy, workspace_org = Organization(name="Legacy"), Organization(name="Payments")
    db_session.add_all([legacy, workspace_org])
    db_session.flush()
    team = Team(organization_id=legacy.id, name="Core")
    db_session.add(team)
    db_session.add_all(
        [
            Repository(organization_id=legacy.id, full_name="team/api"),
            Repository(organization_id=workspace_org.id, full_name="tenant/private"),
        ]
    )
    db_session.commit()

    github, _ = resolve_team_scopes(team.id, session=db_session)

    assert github == ["team/api"]
