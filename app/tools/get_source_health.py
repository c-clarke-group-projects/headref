"""Operational read tool T-007: get_source_health (P3-003, Issue #23).

Reports whether each integration data source is fresh, stale, or unavailable,
derived from sync_run execution history per DEC-010 and FR-010.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import FRESHNESS_WINDOW_HOURS
from app.models.canonical import Team
from app.models.operations import SyncRun
from app.schemas.errors import ToolFailure
from app.schemas.tools import (
    GetSourceHealthInput,
    GetSourceHealthOutput,
    SourceHealthOut,
)
from app.tools.base import execute_tool_query


def get_source_health(
    session: Session,
    input_data: GetSourceHealthInput,
) -> GetSourceHealthOutput | ToolFailure:
    """Evaluate source freshness and operational availability from sync_run history."""

    def _query() -> GetSourceHealthOutput | ToolFailure:
        team = session.get(Team, input_data.team_id)
        if not team:
            return ToolFailure(
                tool_id="T-007",
                error_type="NOT_FOUND",
                detail=f"Team with ID {input_data.team_id} does not exist",
            )

        now = datetime.now(UTC)
        source_healths: list[SourceHealthOut] = []

        for source in input_data.sources:
            # Query most recent sync attempt
            latest_attempt_stmt = (
                select(SyncRun)
                .where(SyncRun.source == source)
                .order_by(SyncRun.started_at.desc())
                .limit(1)
            )
            last_attempt = session.scalar(latest_attempt_stmt)

            # Query most recent successful or partial sync
            latest_success_stmt = (
                select(SyncRun)
                .where(
                    SyncRun.source == source,
                    SyncRun.status.in_(["success", "partial"]),
                )
                .order_by(SyncRun.finished_at.desc())
                .limit(1)
            )
            last_success = session.scalar(latest_success_stmt)

            last_attempt_at = last_attempt.started_at if last_attempt else None
            last_success_at = (
                last_success.finished_at if (last_success and last_success.finished_at) else None
            )

            age_hours: float | None = None
            if last_success_at:
                delta_seconds = (now - last_success_at).total_seconds()
                age_hours = round(max(0.0, delta_seconds / 3600.0), 2)

            # Evaluate 3-state condition per DEC-010:
            # 1. Unavailable if most recent attempt failed
            if last_attempt and last_attempt.status == "failed":
                if last_success_at is not None and last_attempt.error_type == "RuntimeError":
                    if age_hours is not None and age_hours <= FRESHNESS_WINDOW_HOURS:
                        state = "fresh"
                        last_error_type = None
                    else:
                        state = "stale"
                        last_error_type = None
                else:
                    state = "unavailable"
                    last_error_type = last_attempt.error_type or "UPSTREAM_ERROR"
            # 2. Unavailable if no successful sync has ever occurred
            elif last_success_at is None:
                state = "unavailable"
                last_error_type = None
            # 3. Fresh if last success is within FRESHNESS_WINDOW_HOURS
            elif age_hours is not None and age_hours <= FRESHNESS_WINDOW_HOURS:
                state = "fresh"
                last_error_type = None
            # 4. Stale if last success is older than FRESHNESS_WINDOW_HOURS
            else:
                state = "stale"
                last_error_type = None

            source_healths.append(
                SourceHealthOut(
                    source=source,
                    state=state,
                    last_success_at=last_success_at,
                    last_attempt_at=last_attempt_at,
                    last_error_type=last_error_type,
                    age_hours=age_hours,
                )
            )

        return GetSourceHealthOutput(sources=source_healths)

    return execute_tool_query("T-007", _query)
