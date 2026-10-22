"""Deterministic read tool T-002: get_assigned_work_items (P3-002, Issue #22).

Returns normalized Jira work items assigned to a person within a time window,
including raw statuses, flags, and blocking dependencies from PostgreSQL.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.canonical import AppUser
from app.models.work import WorkItem, WorkItemDependency
from app.schemas.errors import ToolFailure
from app.schemas.tools import (
    DependencyOut,
    GetAssignedWorkItemsInput,
    GetAssignedWorkItemsOutput,
    WorkItemOut,
)
from app.tools.base import execute_tool_query


def get_assigned_work_items(
    session: Session,
    input_data: GetAssignedWorkItemsInput,
) -> GetAssignedWorkItemsOutput | ToolFailure:
    """Retrieve Jira work items assigned to a subject within a timestamp window."""

    def _query() -> GetAssignedWorkItemsOutput | ToolFailure:
        user = session.get(AppUser, input_data.subject_user_id)
        if not user:
            return ToolFailure(
                tool_id="T-002",
                error_type="NOT_FOUND",
                detail=f"User with ID {input_data.subject_user_id} does not exist",
            )

        stmt = select(WorkItem).where(
            WorkItem.assignee_app_user_id == input_data.subject_user_id,
            WorkItem.source_updated_at >= input_data.window_start,
            WorkItem.source_updated_at <= input_data.window_end,
        )

        if not input_data.include_done:
            stmt = stmt.where(WorkItem.status != "done")

        if input_data.statuses:
            stmt = stmt.where(WorkItem.status.in_(input_data.statuses))

        stmt = stmt.order_by(WorkItem.source_updated_at.desc())
        work_items = list(session.scalars(stmt).all())

        # Fallback: the window returned nothing, so show the latest active assigned items.
        if not work_items:
            fallback_stmt = select(WorkItem).where(
                WorkItem.assignee_app_user_id == input_data.subject_user_id,
            )
            if not input_data.include_done:
                fallback_stmt = fallback_stmt.where(WorkItem.status != "done")
            if input_data.statuses:
                fallback_stmt = fallback_stmt.where(WorkItem.status.in_(input_data.statuses))
            fallback_stmt = fallback_stmt.order_by(WorkItem.source_updated_at.desc()).limit(5)
            work_items = list(session.scalars(fallback_stmt).all())

        items: list[WorkItemOut] = []
        for wi in work_items:
            # Query blocking dependencies for this work item
            dep_stmt = (
                select(WorkItem)
                .join(WorkItemDependency, WorkItemDependency.blocked_by_work_item_id == WorkItem.id)
                .where(
                    WorkItemDependency.work_item_id == wi.id,
                    WorkItem.status != "done",
                )
                .order_by(WorkItem.external_id)
            )
            blocking = list(session.scalars(dep_stmt).all())

            items.append(
                WorkItemOut(
                    work_item_id=wi.id,
                    external_id=wi.external_id,
                    title=wi.title,
                    status=wi.status,  # type: ignore[arg-type]
                    raw_status=wi.raw_status,
                    assignee_user_id=wi.assignee_app_user_id,
                    priority=wi.priority,
                    due_date=wi.due_date,
                    is_flagged=wi.is_flagged,
                    blocked_by=[dep.external_id for dep in blocking],
                    blocked_by_items=[
                        DependencyOut(
                            external_id=dep.external_id,
                            status=dep.status,  # type: ignore[arg-type]
                            priority=dep.priority,
                            is_flagged=dep.is_flagged,
                            source_url=dep.source_url,
                            source_updated_at=dep.source_updated_at,
                            retrieved_at=dep.retrieved_at,
                        )
                        for dep in blocking
                    ],
                    source_url=wi.source_url,
                    source_updated_at=wi.source_updated_at,
                    retrieved_at=wi.retrieved_at,
                )
            )

        return GetAssignedWorkItemsOutput(items=items)

    return execute_tool_query("T-002", _query)
