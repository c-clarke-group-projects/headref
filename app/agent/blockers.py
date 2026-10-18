"""Deterministic blocker detection rules BL-1 through BL-8 (P4-003, Issue #27).

Evaluates the 8 deterministic blocker signals required by FR-020 and DEC-014.
Since Slack is out of scope, blockers are derived exclusively from Jira and GitHub structure.
All thresholds are loaded from app.config.
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_settings
from app.schemas.tools import CommitOut, PullRequestOut, WorkItemLinkOut, WorkItemOut


class BlockerFinding(BaseModel):
    """Structured record of a detected blocker signal."""

    signal_id: Literal["BL-1", "BL-2", "BL-3", "BL-4", "BL-5", "BL-6", "BL-7", "BL-8"] = Field(
        description="Blocker rule identifier"
    )
    blocker_type: Literal["explicit", "dependency", "review", "progress", "environment"] = Field(
        description="Classification of blocker per DATA_AND_EVIDENCE.md 6.9"
    )
    description: str = Field(description="Factual blocker statement describing the entity")
    entity_key: str = Field(description="Natural identifier of affected ticket or pull request")
    work_item_id: int | None = Field(
        default=None, description="Internal work item ID if applicable"
    )
    pull_request_number: int | None = Field(
        default=None, description="Pull request number if applicable"
    )
    evidence_keys: list[str] = Field(
        default_factory=list, description="Cited natural entity keys backing this blocker"
    )


def _has_linked_code(
    item: WorkItemOut,
    pull_requests: Sequence[PullRequestOut],
    links: Sequence[WorkItemLinkOut],
) -> bool:
    """Check whether a work item has any linked pull request or branch."""
    for link in links:
        if link.work_item_id == item.work_item_id or link.work_item_external_id == item.external_id:
            return True

    pattern = re.compile(rf"\b{re.escape(item.external_id)}\b", re.IGNORECASE)
    for pr in pull_requests:
        if pr.branch_name and pattern.search(pr.branch_name):
            return True
        if pr.title and pattern.search(pr.title):
            return True

    return False


def detect_blockers(
    work_items: Sequence[WorkItemOut],
    pull_requests: Sequence[PullRequestOut],
    commits: Sequence[CommitOut] = (),
    links: Sequence[WorkItemLinkOut] = (),
    as_of: datetime | None = None,
) -> list[BlockerFinding]:
    """Detect all deterministic blockers BL-1 to BL-8 across Jira and GitHub."""
    settings = get_settings()
    now = as_of or datetime.now(UTC)
    blockers: list[BlockerFinding] = []

    # -------------------------------------------------------------------------
    # Jira Work Item Blockers (BL-1, BL-2, BL-3, BL-8)
    # -------------------------------------------------------------------------
    for item in work_items:
        # BL-1 (explicit): Work item status is blocked
        if item.status == "blocked":
            blockers.append(
                BlockerFinding(
                    signal_id="BL-1",
                    blocker_type="explicit",
                    description=f"{item.external_id} has status Blocked in Jira.",
                    entity_key=item.external_id,
                    work_item_id=item.work_item_id,
                    evidence_keys=[item.external_id],
                )
            )

        # BL-2 (explicit): Impediment flag is set
        if item.is_flagged:
            blockers.append(
                BlockerFinding(
                    signal_id="BL-2",
                    blocker_type="explicit",
                    description=f"{item.external_id} has impediment flag set in Jira.",
                    entity_key=item.external_id,
                    work_item_id=item.work_item_id,
                    evidence_keys=[item.external_id],
                )
            )

        # BL-3 (dependency): Open is blocked by dependency exists
        if item.blocked_by:
            deps_str = ", ".join(item.blocked_by)
            blockers.append(
                BlockerFinding(
                    signal_id="BL-3",
                    blocker_type="dependency",
                    description=f"{item.external_id} is blocked by dependency: {deps_str}.",
                    entity_key=item.external_id,
                    work_item_id=item.work_item_id,
                    evidence_keys=[item.external_id] + list(item.blocked_by),
                )
            )

        # BL-8 (progress): Work item in_progress with no linked branch/PR after ISSUE_NO_CODE_DAYS
        if item.status == "in_progress":
            elapsed = now - item.source_updated_at
            if elapsed > timedelta(days=settings.issue_no_code_days):
                if not _has_linked_code(item, pull_requests, links):
                    days = int(elapsed.total_seconds() // 86400)
                    blockers.append(
                        BlockerFinding(
                            signal_id="BL-8",
                            blocker_type="progress",
                            description=(
                                f"{item.external_id} has been In Progress for {days} "
                                f"days with no linked branch or pull request."
                            ),
                            entity_key=item.external_id,
                            work_item_id=item.work_item_id,
                            evidence_keys=[item.external_id],
                        )
                    )

    # -------------------------------------------------------------------------
    # GitHub Pull Request Blockers (BL-4, BL-5, BL-6, BL-7)
    # -------------------------------------------------------------------------
    for pr in pull_requests:
        if pr.state != "open":
            continue

        pr_key = f"PR-{pr.number}"
        pr_evidence_keys = [str(pr.number), f"#{pr.number}", pr_key]

        # BL-4 (review): changes_requested and no subsequent commit
        if pr.review_state == "changes_requested":
            # Check commits matching this PR
            has_commit_after_review = False
            if pr.last_review_at:
                for c in commits:
                    if pr.branch_name and c.branch_name == pr.branch_name:
                        if c.committed_at > pr.last_review_at:
                            has_commit_after_review = True
                            break

            if not has_commit_after_review:
                blockers.append(
                    BlockerFinding(
                        signal_id="BL-4",
                        blocker_type="review",
                        description=(
                            f"Pull request #{pr.number} ({pr.repo_full_name}) has "
                            f"changes requested with no subsequent commits."
                        ),
                        entity_key=pr_key,
                        pull_request_number=pr.number,
                        evidence_keys=pr_evidence_keys,
                    )
                )

        # BL-5 (review): Open with no review for more than PR_REVIEW_WAIT_DAYS
        if not pr.is_draft and pr.review_state == "none":
            elapsed = now - pr.created_at
            if elapsed > timedelta(days=settings.pr_review_wait_days):
                days = int(elapsed.total_seconds() // 86400)
                blockers.append(
                    BlockerFinding(
                        signal_id="BL-5",
                        blocker_type="review",
                        description=(
                            f"Pull request #{pr.number} ({pr.repo_full_name}) has "
                            f"been open for {days} days with no review."
                        ),
                        entity_key=pr_key,
                        pull_request_number=pr.number,
                        evidence_keys=pr_evidence_keys,
                    )
                )

        # BL-6 (progress): Draft pull request older than DRAFT_PR_STALE_DAYS
        if pr.is_draft:
            elapsed = now - pr.created_at
            if elapsed > timedelta(days=settings.draft_pr_stale_days):
                days = int(elapsed.total_seconds() // 86400)
                blockers.append(
                    BlockerFinding(
                        signal_id="BL-6",
                        blocker_type="progress",
                        description=(
                            f"Pull request #{pr.number} ({pr.repo_full_name}) has "
                            f"been in draft state for {days} days."
                        ),
                        entity_key=pr_key,
                        pull_request_number=pr.number,
                        evidence_keys=pr_evidence_keys,
                    )
                )

        # BL-7 (environment): Checks failing on head commit
        if pr.checks_state == "failing":
            blockers.append(
                BlockerFinding(
                    signal_id="BL-7",
                    blocker_type="environment",
                    description=(
                        f"Pull request #{pr.number} ({pr.repo_full_name}) has "
                        f"failing CI checks."
                    ),
                    entity_key=pr_key,
                    pull_request_number=pr.number,
                    evidence_keys=pr_evidence_keys,
                )
            )

    return blockers
