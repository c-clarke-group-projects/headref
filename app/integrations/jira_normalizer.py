"""Jira data normalizer and canonical database loader (P2-005, Issue #16).

Transforms raw Jira Cloud REST API payloads into canonical Project, WorkItem,
and WorkItemDependency records. Normalizes statuses into canonical taxonomy,
captures impediment flags, and performs idempotent upserts without duplicates.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.canonical import Project
from app.models.work import WorkItem, WorkItemDependency

logger = logging.getLogger("headref.integrations.jira_normalizer")

# Values a Jira impediment field carries, whatever the field is named.
_IMPEDIMENT_VALUES = frozenset({"impediment", "flagged"})

# Canonical status taxonomy: todo | in_progress | in_review | done | blocked
STATUS_MAP: dict[str, str] = {
    # To Do / Backlog
    "to do": "todo",
    "todo": "todo",
    "backlog": "todo",
    "open": "todo",
    "created": "todo",
    "ready for dev": "todo",
    "selected for development": "todo",
    # In Progress
    "in progress": "in_progress",
    "in development": "in_progress",
    "active": "in_progress",
    "started": "in_progress",
    "wip": "in_progress",
    # In Review / Testing / QA
    "in review": "in_review",
    "under review": "in_review",
    "review": "in_review",
    "code review": "in_review",
    "qa": "in_review",
    "in qa": "in_review",
    "testing": "in_review",
    "in test": "in_review",
    "ready for review": "in_review",
    # Done / Resolved / Closed
    "done": "done",
    "closed": "done",
    "resolved": "done",
    "completed": "done",
    # Blocked / Impediment
    "blocked": "blocked",
    "impediment": "blocked",
    "on hold": "blocked",
    "waiting": "blocked",
}

# Fallback mapping from Jira statusCategory.key
CATEGORY_MAP: dict[str, str] = {
    "new": "todo",
    "indeterminate": "in_progress",
    "done": "done",
}


@dataclass
class NormalizationCounts:
    """Tracking counts of fetched, written, and skipped records during normalization."""

    fetched: int = 0
    written: int = 0
    skipped: int = 0


def parse_datetime(val: Any) -> datetime | None:
    """Parse an ISO-8601 string or date into a UTC datetime."""
    if not val:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=UTC)
        return val.astimezone(UTC)
    if isinstance(val, str):
        try:
            clean_str = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            return None
    return None


def map_status(raw_status: str, status_category_key: str | None = None) -> str:
    """Normalize a Jira status name to canonical status taxonomy.

    Preserves the raw status for raw_status column. If status is unknown,
    falls back to statusCategory or 'todo' and logs a warning.
    """
    cleaned = raw_status.strip().lower()
    if cleaned in STATUS_MAP:
        return STATUS_MAP[cleaned]

    # Try matching category key
    cat_key = (status_category_key or "").strip().lower()
    if cat_key in CATEGORY_MAP:
        fallback = CATEGORY_MAP[cat_key]
        logger.warning(
            "Unmapped Jira status '%s'; mapped via category '%s' to '%s'",
            raw_status,
            status_category_key,
            fallback,
        )
        return fallback

    logger.warning("Unmapped Jira status '%s'; defaulting to 'todo'", raw_status)
    return "todo"


def _holds_impediment_value(value: Any) -> bool:
    """Report whether a custom field value carries Jira's impediment vocabulary."""
    if isinstance(value, str):
        return value.lower() in _IMPEDIMENT_VALUES
    if isinstance(value, list):
        return any(
            isinstance(item, dict) and str(item.get("value", "")).lower() in _IMPEDIMENT_VALUES
            for item in value
        )
    return False


def is_issue_flagged(fields: dict[str, Any]) -> bool:
    """Detect if an issue has the Jira impediment / flagged indicator set."""
    if bool(fields.get("flagged")):
        return True

    # Check custom fields where flagged is an array of objects
    for key, value in fields.items():
        if "flag" in key.lower():
            if isinstance(value, list) and len(value) > 0:
                return True
            if isinstance(value, bool) and value:
                return True
            if isinstance(value, str) and value.lower() in ("impediment", "flagged", "true"):
                return True
        # A Jira instance exposes the impediment field under an opaque id such as
        # customfield_10015, so the name carries no signal. Match on the value.
        elif key.startswith("customfield_") and _holds_impediment_value(value):
            return True
    return False


def normalize_project(payload: dict[str, Any], organization_id: int) -> dict[str, Any]:
    """Normalize a Jira project payload into canonical dictionary attributes."""
    key = payload.get("key")
    if not key:
        raise ValueError("Project payload missing key")

    name = payload.get("name") or key
    return {
        "organization_id": organization_id,
        "key": key[:32],
        "name": name[:255],
        "is_active": True,
    }


def normalize_work_item(
    payload: dict[str, Any],
    project_id: int,
    retrieved_at: datetime | None = None,
    jira_base_url: str = "",
) -> dict[str, Any]:
    """Normalize a Jira issue payload into canonical WorkItem dictionary attributes."""
    key = payload.get("key")
    if not key:
        raise ValueError("Issue payload missing key")

    fields = payload.get("fields", {})
    summary = fields.get("summary")
    if not summary:
        raise ValueError(f"Issue {key} missing summary")

    if retrieved_at is None:
        now = datetime.now(UTC)
    else:
        now = parse_datetime(retrieved_at) or datetime.now(UTC)

    status_obj = fields.get("status", {})
    raw_status = status_obj.get("name") or "To Do"
    category_key = status_obj.get("statusCategory", {}).get("key")
    normalized_status = map_status(raw_status, status_category_key=category_key)

    priority_obj = fields.get("priority")
    priority = priority_obj.get("name") if isinstance(priority_obj, dict) else None

    due_date = parse_datetime(fields.get("duedate"))
    flagged = is_issue_flagged(fields)

    created_at = parse_datetime(fields.get("created")) or now
    source_updated_at = parse_datetime(fields.get("updated")) or created_at

    base_url = jira_base_url.rstrip("/")
    source_url = f"{base_url}/browse/{key}" if base_url else f"https://jira/browse/{key}"

    return {
        "project_id": project_id,
        "external_id": key[:64],
        "title": summary[:500],
        "status": normalized_status[:64],
        "raw_status": raw_status[:64],
        "assignee_app_user_id": None,  # Left None, resolved by P2-006
        "priority": priority[:32] if priority else None,
        "due_date": due_date,
        "is_flagged": flagged,
        "source_url": source_url[:1024],
        "source_updated_at": source_updated_at,
        "retrieved_at": now,
    }


def extract_blocking_dependencies(
    issue_payload: dict[str, Any],
    work_items_by_key: dict[str, WorkItem],
    retrieved_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Extract blocking issue link relationships from a Jira issue payload.

    Identifies links where:
    1. outward 'blocks': current issue blocks outwardIssue (outwardIssue depends on current).
    2. inward 'is blocked by': current issue is blocked by inwardIssue (current depends on inward).
    """
    key = issue_payload.get("key")
    if not key or key not in work_items_by_key:
        return []

    current_item = work_items_by_key[key]
    fields = issue_payload.get("fields", {})
    issue_links = fields.get("issuelinks", [])

    now = parse_datetime(retrieved_at) or datetime.now(UTC)
    dependencies: list[dict[str, Any]] = []

    for link in issue_links:
        link_type_obj = link.get("type", {})
        link_name = (link_type_obj.get("name") or "").lower()

        # Check outward 'blocks'
        if "outwardIssue" in link:
            outward_key = link["outwardIssue"].get("key")
            outward_desc = (link_type_obj.get("outward") or "").lower()
            if outward_key in work_items_by_key and (
                "block" in link_name or "block" in outward_desc
            ):
                blocked_item = work_items_by_key[outward_key]
                dependencies.append(
                    {
                        "work_item_id": blocked_item.id,
                        "blocked_by_work_item_id": current_item.id,
                        "link_type": "blocks",
                        "retrieved_at": now,
                    }
                )

        # Check inward 'is blocked by'
        if "inwardIssue" in link:
            inward_key = link["inwardIssue"].get("key")
            inward_desc = (link_type_obj.get("inward") or "").lower()
            if inward_key in work_items_by_key and ("block" in link_name or "block" in inward_desc):
                blocking_item = work_items_by_key[inward_key]
                dependencies.append(
                    {
                        "work_item_id": current_item.id,
                        "blocked_by_work_item_id": blocking_item.id,
                        "link_type": "blocks",
                        "retrieved_at": now,
                    }
                )

    return dependencies


def upsert_project(session: Session, project_dict: dict[str, Any]) -> Project:
    """Idempotently insert or update a Project record on (organization, key) (W5)."""
    stmt = select(Project).where(
        Project.organization_id == project_dict["organization_id"],
        Project.key == project_dict["key"],
    )
    existing = session.scalar(stmt)
    if existing:
        existing.name = project_dict.get("name", existing.name)
        existing.is_active = project_dict.get("is_active", existing.is_active)
        return existing

    project = Project(**project_dict)
    session.add(project)
    session.flush()
    return project


def upsert_work_item(session: Session, item_dict: dict[str, Any]) -> WorkItem:
    """Idempotently insert or update a WorkItem record on (project_id, external_id)."""
    stmt = select(WorkItem).where(
        WorkItem.project_id == item_dict["project_id"],
        WorkItem.external_id == item_dict["external_id"],
    )
    existing = session.scalar(stmt)
    if existing:
        for key, val in item_dict.items():
            if key not in ("id", "project_id", "external_id", "assignee_app_user_id"):
                setattr(existing, key, val)
        return existing

    item = WorkItem(**item_dict)
    session.add(item)
    session.flush()
    return item


def upsert_work_item_dependency(session: Session, dep_dict: dict[str, Any]) -> WorkItemDependency:
    """Idempotently insert or update a WorkItemDependency record."""
    stmt = select(WorkItemDependency).where(
        WorkItemDependency.work_item_id == dep_dict["work_item_id"],
        WorkItemDependency.blocked_by_work_item_id == dep_dict["blocked_by_work_item_id"],
    )
    existing = session.scalar(stmt)
    if existing:
        existing.link_type = dep_dict.get("link_type", existing.link_type)
        existing.retrieved_at = dep_dict.get("retrieved_at", existing.retrieved_at)
        return existing

    dep = WorkItemDependency(**dep_dict)
    session.add(dep)
    session.flush()
    return dep


def ingest_projects(
    session: Session,
    organization_id: int,
    payloads: list[dict[str, Any]],
) -> tuple[list[Project], NormalizationCounts]:
    """Normalize and upsert a batch of project payloads, skipping invalid records."""
    counts = NormalizationCounts(fetched=len(payloads))
    results: list[Project] = []
    for raw in payloads:
        try:
            norm = normalize_project(raw, organization_id=organization_id)
            proj = upsert_project(session, norm)
            results.append(proj)
            counts.written += 1
        except Exception as exc:
            logger.warning("Skipping malformed project record: %s", exc)
            counts.skipped += 1
    return results, counts


def ingest_work_items(
    session: Session,
    project_id: int,
    payloads: list[dict[str, Any]],
    retrieved_at: datetime | None = None,
    jira_base_url: str = "",
) -> tuple[list[WorkItem], NormalizationCounts]:
    """Normalize and upsert a batch of issue payloads, skipping invalid records."""
    counts = NormalizationCounts(fetched=len(payloads))
    results: list[WorkItem] = []
    for raw in payloads:
        try:
            norm = normalize_work_item(
                raw,
                project_id=project_id,
                retrieved_at=retrieved_at,
                jira_base_url=jira_base_url,
            )
            item = upsert_work_item(session, norm)
            results.append(item)
            counts.written += 1
        except Exception as exc:
            logger.warning("Skipping malformed work item record: %s", exc)
            counts.skipped += 1
    return results, counts


def ingest_dependencies(
    session: Session,
    work_items_by_key: dict[str, WorkItem],
    payloads: list[dict[str, Any]],
    retrieved_at: datetime | None = None,
) -> tuple[list[WorkItemDependency], NormalizationCounts]:
    """Extract and upsert all blocking dependencies across a batch of issues."""
    counts = NormalizationCounts()
    results: list[WorkItemDependency] = []

    for raw in payloads:
        dep_dicts = extract_blocking_dependencies(
            raw,
            work_items_by_key=work_items_by_key,
            retrieved_at=retrieved_at,
        )
        counts.fetched += len(dep_dicts)
        for d in dep_dicts:
            try:
                dep = upsert_work_item_dependency(session, d)
                results.append(dep)
                counts.written += 1
            except Exception as exc:
                logger.warning("Skipping dependency record: %s", exc)
                counts.skipped += 1

    return results, counts
