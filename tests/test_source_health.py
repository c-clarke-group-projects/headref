"""Unit tests for operational read tool T-007: get_source_health (P3-003, Issue #23)."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models.canonical import Organization, Team
from app.models.operations import SyncRun
from app.schemas.errors import ToolFailure
from app.schemas.tools import (
    GetSourceHealthInput,
    GetSourceHealthOutput,
)
from app.tools.get_source_health import get_source_health


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _seed_team(session: Session) -> Team:
    """Seed minimal organization and team."""
    org = Organization(name="Health Org")
    session.add(org)
    session.flush()

    team = Team(organization_id=org.id, name="Observability Team")
    session.add(team)
    session.flush()
    return team


def test_t007_source_health_fresh_and_stale(db_session: Session) -> None:
    """T-007 correctly identifies fresh (<24h) and stale (>24h) sync states."""
    team = _seed_team(db_session)
    now = datetime.now(UTC)

    # GitHub: fresh (synced 2 hours ago)
    gh_run = SyncRun(
        source="github",
        scope="keshan-dev/headref",
        status="success",
        items_fetched=10,
        items_written=10,
        items_skipped=0,
        started_at=now - timedelta(hours=2),
        finished_at=now - timedelta(hours=2),
    )

    # Jira: stale (synced 30 hours ago)
    jira_run = SyncRun(
        source="jira",
        scope="ALL",
        status="success",
        items_fetched=15,
        items_written=15,
        items_skipped=0,
        started_at=now - timedelta(hours=30),
        finished_at=now - timedelta(hours=30),
    )
    db_session.add_all([gh_run, jira_run])
    db_session.flush()

    inp = GetSourceHealthInput(team_id=team.id, sources=["github", "jira"])
    res = get_source_health(db_session, inp)

    assert isinstance(res, GetSourceHealthOutput)
    assert len(res.sources) == 2

    gh_health = next(s for s in res.sources if s.source == "github")
    assert gh_health.state == "fresh"
    assert gh_health.last_error_type is None
    assert gh_health.age_hours is not None
    assert 1.9 <= gh_health.age_hours <= 2.1

    jira_health = next(s for s in res.sources if s.source == "jira")
    assert jira_health.state == "stale"
    assert jira_health.last_error_type is None
    assert jira_health.age_hours is not None
    assert 29.9 <= jira_health.age_hours <= 30.1


def test_t007_source_health_unavailable_on_failed_attempt(db_session: Session) -> None:
    """T-007 marks a source unavailable if the most recent attempt failed."""
    team = _seed_team(db_session)
    now = datetime.now(UTC)

    # Earlier success 5 hours ago
    success_run = SyncRun(
        source="jira",
        scope="ALL",
        status="success",
        started_at=now - timedelta(hours=5),
        finished_at=now - timedelta(hours=5),
    )
    # Recent failure 10 minutes ago
    failed_run = SyncRun(
        source="jira",
        scope="ALL",
        status="failed",
        error_type="RATE_LIMITED",
        error_detail="API rate limit exceeded",
        started_at=now - timedelta(minutes=10),
        finished_at=now - timedelta(minutes=10),
    )
    db_session.add_all([success_run, failed_run])
    db_session.flush()

    inp = GetSourceHealthInput(team_id=team.id, sources=["jira"])
    res = get_source_health(db_session, inp)

    assert isinstance(res, GetSourceHealthOutput)
    assert len(res.sources) == 1
    assert res.sources[0].state == "unavailable"
    assert res.sources[0].last_error_type == "RATE_LIMITED"


def test_t007_source_health_unavailable_when_no_history(db_session: Session) -> None:
    """T-007 marks a source unavailable if no sync has ever run."""
    team = _seed_team(db_session)

    inp = GetSourceHealthInput(team_id=team.id, sources=["github"])
    res = get_source_health(db_session, inp)

    assert isinstance(res, GetSourceHealthOutput)
    assert len(res.sources) == 1
    assert res.sources[0].state == "unavailable"
    assert res.sources[0].last_success_at is None
    assert res.sources[0].last_attempt_at is None
    assert res.sources[0].last_error_type is None


def test_t007_source_health_missing_team(db_session: Session) -> None:
    """T-007 returns NOT_FOUND if the team does not exist."""
    inp = GetSourceHealthInput(team_id=999, sources=["github"])
    res = get_source_health(db_session, inp)

    assert isinstance(res, ToolFailure)
    assert res.tool_id == "T-007"
    assert res.error_type == "NOT_FOUND"


def test_t007_source_health_fail_closed_on_db_error(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-007 fails closed (returns ToolFailure) on unexpected query errors."""
    team = _seed_team(db_session)

    def _broken_query(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(db_session, "scalar", _broken_query)

    inp = GetSourceHealthInput(team_id=team.id, sources=["github"])
    res = get_source_health(db_session, inp)

    assert isinstance(res, ToolFailure)
    assert res.tool_id == "T-007"
    assert res.error_type == "UPSTREAM_ERROR"
