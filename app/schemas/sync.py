"""Sync status contracts for the on-demand refresh endpoints (P5-007, Issue #49, G7).

These describe what the database actually records in ``sync_run``. A source with no
row is absent from the list, never reported as running. An empty list means "no run
has been recorded for this scope", which is not the same as "a sync is in progress".
"""

from datetime import datetime

from pydantic import BaseModel, Field


class SyncStatus(BaseModel):
    """One ``sync_run`` row, as the UI needs to read it."""

    run_id: int | None = Field(default=None, description="sync_run primary key")
    source: str = Field(description="Data source name, github or jira")
    scope: str = Field(description="Repository full name, Jira project key, or ALL")
    status: str = Field(description="running, success, partial or failed")
    error_type: str | None = Field(
        default=None, description="Typed failure category when the run failed"
    )
    items_skipped: int = Field(default=0, description="Rows the run could not attribute")
    started_at: datetime | None = Field(default=None, description="When the run started")
    finished_at: datetime | None = Field(
        default=None, description="When the run finished, or None while running"
    )


class SyncTriggerResponse(BaseModel):
    """Answer to POST /api/teams/{team_id}/sync.

    ``started`` is True when this request began a new background sync. It is False when
    a run was already in flight for the team's scopes, in which case ``runs`` carries
    those in flight rows.
    """

    started: bool = Field(description="Whether this request started a new sync")
    all_finished: bool = Field(
        default=False, description="True when no run for these scopes is still running"
    )
    runs: list[SyncStatus] = Field(
        default_factory=list, description="Recorded runs for the team's scopes"
    )


class SyncStatusResponse(BaseModel):
    """Answer to GET /api/teams/{team_id}/sync/status."""

    all_finished: bool = Field(
        description="True when every recorded run for these scopes has left running"
    )
    runs: list[SyncStatus] = Field(
        default_factory=list, description="Latest run per source and scope"
    )
