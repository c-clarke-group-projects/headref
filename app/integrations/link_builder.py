"""Work item link builder (P2-007, Issue #18).

Populates canonical WorkItemLink records connecting Jira work items to GitHub pull
requests, branches, and commits based on the correlation confidence matrix in DEC-009:
- jira_remote_link: HIGH
- branch_name: HIGH
- pr_title: MEDIUM
- pr_body: MEDIUM
- commit_message: MEDIUM

Enforces that no links are ever created from timing or authorship alone (AC-13).
"""

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.work import Commit, PullRequest, WorkItem, WorkItemLink

logger = logging.getLogger("headref.integrations.link_builder")

CONFIDENCE_RANKS: dict[str, int] = {
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}


def build_ticket_pattern(project_keys: list[str]) -> re.Pattern[str] | None:
    """Build a regex pattern with word boundaries matching any configured project key."""
    valid_keys = [re.escape(k.strip().upper()) for k in project_keys if k.strip()]
    if not valid_keys:
        return None
    joined = "|".join(valid_keys)
    # \b ensures AUTH-245 matches but AUTH-2450 does not match as AUTH-245
    return re.compile(rf"\b({joined})-(\d+)\b", re.IGNORECASE)


def extract_ticket_keys(text: str | None, project_keys: list[str]) -> set[str]:
    """Extract all matching Jira ticket keys from text using project keys with word boundaries."""
    if not text or not project_keys:
        return set()
    pattern = build_ticket_pattern(project_keys)
    if pattern is None:
        return set()

    matches = pattern.finditer(text)
    return {m.group(0).upper() for m in matches}


def upsert_work_item_link(
    session: Session,
    work_item_id: int,
    target_type: str,
    target_id: str,
    link_method: str,
    confidence: str,
    now: datetime | None = None,
) -> WorkItemLink:
    """Idempotently insert or update a WorkItemLink, preserving the highest confidence."""
    stmt = select(WorkItemLink).where(
        WorkItemLink.work_item_id == work_item_id,
        WorkItemLink.target_type == target_type,
        WorkItemLink.target_id == target_id,
    )
    existing = session.scalar(stmt)

    new_rank = CONFIDENCE_RANKS.get(confidence.upper(), 1)

    if existing:
        current_rank = CONFIDENCE_RANKS.get(existing.confidence.upper(), 1)
        if new_rank > current_rank:
            existing.confidence = confidence.upper()
            existing.link_method = link_method
        return existing

    created_time = now or datetime.now(UTC)
    link = WorkItemLink(
        work_item_id=work_item_id,
        target_type=target_type,
        target_id=target_id,
        link_method=link_method,
        confidence=confidence.upper(),
        created_at=created_time,
    )
    session.add(link)
    session.flush()
    return link


def link_pull_request(
    session: Session,
    pr: PullRequest,
    work_items_by_key: dict[str, WorkItem],
    project_keys: list[str],
) -> list[WorkItemLink]:
    """Connect a PullRequest to WorkItems via branch_name (HIGH), title (MED), body (MED)."""
    links: list[WorkItemLink] = []
    pr_target_id = str(pr.id)

    # 1. Branch name: HIGH confidence
    if pr.branch_name:
        branch_tickets = extract_ticket_keys(pr.branch_name, project_keys)
        for key in branch_tickets:
            if key in work_items_by_key:
                wi = work_items_by_key[key]
                link = upsert_work_item_link(
                    session=session,
                    work_item_id=wi.id,
                    target_type="pull_request",
                    target_id=pr_target_id,
                    link_method="branch_name",
                    confidence="HIGH",
                )
                links.append(link)
                # Also link the branch itself
                branch_link = upsert_work_item_link(
                    session=session,
                    work_item_id=wi.id,
                    target_type="branch",
                    target_id=pr.branch_name,
                    link_method="branch_name",
                    confidence="HIGH",
                )
                links.append(branch_link)

    # 2. PR title: MEDIUM confidence
    if pr.title:
        title_tickets = extract_ticket_keys(pr.title, project_keys)
        for key in title_tickets:
            if key in work_items_by_key:
                wi = work_items_by_key[key]
                link = upsert_work_item_link(
                    session=session,
                    work_item_id=wi.id,
                    target_type="pull_request",
                    target_id=pr_target_id,
                    link_method="pr_title",
                    confidence="MEDIUM",
                )
                links.append(link)

    # 3. PR body excerpt: MEDIUM confidence
    if pr.body_excerpt:
        body_tickets = extract_ticket_keys(pr.body_excerpt, project_keys)
        for key in body_tickets:
            if key in work_items_by_key:
                wi = work_items_by_key[key]
                link = upsert_work_item_link(
                    session=session,
                    work_item_id=wi.id,
                    target_type="pull_request",
                    target_id=pr_target_id,
                    link_method="pr_body",
                    confidence="MEDIUM",
                )
                links.append(link)

    return links


def link_commit(
    session: Session,
    commit: Commit,
    work_items_by_key: dict[str, WorkItem],
    project_keys: list[str],
) -> list[WorkItemLink]:
    """Connect a Commit to WorkItems via branch_name (HIGH) and message_excerpt (MED)."""
    links: list[WorkItemLink] = []
    commit_target_id = str(commit.id)

    # 1. Branch name on commit: HIGH confidence
    if commit.branch_name:
        branch_tickets = extract_ticket_keys(commit.branch_name, project_keys)
        for key in branch_tickets:
            if key in work_items_by_key:
                wi = work_items_by_key[key]
                link = upsert_work_item_link(
                    session=session,
                    work_item_id=wi.id,
                    target_type="commit",
                    target_id=commit_target_id,
                    link_method="branch_name",
                    confidence="HIGH",
                )
                links.append(link)

    # 2. Commit message: MEDIUM confidence
    if commit.message_excerpt:
        message_tickets = extract_ticket_keys(commit.message_excerpt, project_keys)
        for key in message_tickets:
            if key in work_items_by_key:
                wi = work_items_by_key[key]
                link = upsert_work_item_link(
                    session=session,
                    work_item_id=wi.id,
                    target_type="commit",
                    target_id=commit_target_id,
                    link_method="commit_message",
                    confidence="MEDIUM",
                )
                links.append(link)

    return links


def link_jira_remote_links(
    session: Session,
    work_item: WorkItem,
    remote_links: list[dict[str, Any]],
    prs_by_url: dict[str, PullRequest],
) -> list[WorkItemLink]:
    """Connect a WorkItem to PullRequests referenced in Jira remote links (HIGH confidence)."""
    links: list[WorkItemLink] = []
    for rlink in remote_links:
        obj = rlink.get("object", {})
        url = obj.get("url") or ""
        clean_url = url.strip().rstrip("/")
        for pr_url, pr in prs_by_url.items():
            if clean_url == pr_url.strip().rstrip("/"):
                link = upsert_work_item_link(
                    session=session,
                    work_item_id=work_item.id,
                    target_type="pull_request",
                    target_id=str(pr.id),
                    link_method="jira_remote_link",
                    confidence="HIGH",
                )
                links.append(link)
    return links
