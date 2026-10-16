"""Conflict detection rules CF-1 through CF-4 (P4-002, Issue #26).

Detects cross-source disagreements between Jira and GitHub per DATA_AND_EVIDENCE.md 6.7.
Headref reports both states and never resolves conflicts or picks a winner (C-1, C-2, DEC-005).
"""

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.tools import CommitOut, PullRequestOut, WorkItemLinkOut, WorkItemOut


class ConflictFinding(BaseModel):
    """Structured record of a detected conflict between Jira and GitHub."""

    conflict_id: Literal["CF-1", "CF-2", "CF-3", "CF-4"] = Field(
        description="Conflict rule identifier"
    )
    description: str = Field(description="Factual conflict statement reporting both states")
    work_item_id: int | None = Field(default=None, description="Internal work item ID")
    work_item_external_id: str = Field(description="External issue key e.g. AUTH-245")
    target_type: Literal["pull_request", "commit", "branch"] = Field(
        description="Conflicting GitHub entity type"
    )
    target_identifier: str = Field(description="Pull request number or commit SHA")
    evidence_keys: list[str] = Field(
        default_factory=list,
        description="Cited natural keys from both sides e.g. ['AUTH-245', '182']",
    )


def _matches_work_item(
    item: WorkItemOut,
    pr: PullRequestOut,
    links: Sequence[WorkItemLinkOut],
) -> bool:
    """Determine if a pull request is correlated to a work item via links or text."""
    # 1. Match via pre-established work_item_link
    for link in links:
        if link.work_item_id == item.work_item_id or link.work_item_external_id == item.external_id:
            if link.target_type == "pull_request":
                if link.target_id == pr.pull_request_id or str(link.target_id) == str(pr.number):
                    return True

    # 2. Match via word-bounded ticket key in branch name or title
    pattern = re.compile(rf"\b{re.escape(item.external_id)}\b", re.IGNORECASE)
    if pr.branch_name and pattern.search(pr.branch_name):
        return True
    if pr.title and pattern.search(pr.title):
        return True

    return False


def _matches_commit(
    item: WorkItemOut,
    commit: CommitOut,
    links: Sequence[WorkItemLinkOut],
) -> bool:
    """Determine if a commit is correlated to a work item via links or branch/message."""
    for link in links:
        if link.work_item_id == item.work_item_id or link.work_item_external_id == item.external_id:
            if link.target_type == "commit" and (
                str(link.target_id) == str(commit.commit_id) or str(link.target_id) == commit.sha
            ):
                return True
            if link.target_type == "branch" and commit.branch_name:
                if str(link.target_id) == commit.branch_name:
                    return True

    pattern = re.compile(rf"\b{re.escape(item.external_id)}\b", re.IGNORECASE)
    if commit.branch_name and pattern.search(commit.branch_name):
        return True
    if commit.message_excerpt and pattern.search(commit.message_excerpt):
        return True

    return False


def detect_conflicts(
    work_items: Sequence[WorkItemOut],
    pull_requests: Sequence[PullRequestOut],
    commits: Sequence[CommitOut] = (),
    links: Sequence[WorkItemLinkOut] = (),
) -> list[ConflictFinding]:
    """Evaluate CF-1 through CF-4 rules over retrieved work items and code activity.

    Per C-1 and C-2, conflicts are strictly reported and never automatically resolved.
    """
    conflicts: list[ConflictFinding] = []

    for item in work_items:
        # Find all pull requests linked to this work item
        linked_prs = [pr for pr in pull_requests if _matches_work_item(item, pr, links)]

        for pr in linked_prs:
            # -----------------------------------------------------------------
            # CF-1: Jira in_progress and linked pull request is merged
            # -----------------------------------------------------------------
            if item.status == "in_progress" and pr.state == "merged":
                conflicts.append(
                    ConflictFinding(
                        conflict_id="CF-1",
                        description=(
                            f"Jira shows {item.external_id} In Progress, but linked "
                            f"pull request #{pr.number} is merged."
                        ),
                        work_item_id=item.work_item_id,
                        work_item_external_id=item.external_id,
                        target_type="pull_request",
                        target_identifier=f"#{pr.number}",
                        evidence_keys=[item.external_id, f"PR-{pr.number}"],
                    )
                )

            # -----------------------------------------------------------------
            # CF-2: Jira done and linked pull request is open
            # -----------------------------------------------------------------
            if item.status == "done" and pr.state == "open":
                conflicts.append(
                    ConflictFinding(
                        conflict_id="CF-2",
                        description=(
                            f"Jira shows {item.external_id} Done, but linked "
                            f"pull request #{pr.number} is still open."
                        ),
                        work_item_id=item.work_item_id,
                        work_item_external_id=item.external_id,
                        target_type="pull_request",
                        target_identifier=f"#{pr.number}",
                        evidence_keys=[item.external_id, f"PR-{pr.number}"],
                    )
                )

            # -----------------------------------------------------------------
            # CF-4: Jira blocked and linked pull request approved with passing checks
            # -----------------------------------------------------------------
            if (
                (item.status == "blocked" or item.is_flagged)
                and pr.review_state == "approved"
                and pr.checks_state == "passing"
            ):
                conflicts.append(
                    ConflictFinding(
                        conflict_id="CF-4",
                        description=(
                            f"{item.external_id} is flagged blocked, but linked "
                            f"pull request #{pr.number} is approved with passing checks."
                        ),
                        work_item_id=item.work_item_id,
                        work_item_external_id=item.external_id,
                        target_type="pull_request",
                        target_identifier=f"#{pr.number}",
                        evidence_keys=[item.external_id, f"PR-{pr.number}"],
                    )
                )

        # ---------------------------------------------------------------------
        # CF-3: Jira done and commits on the linked branch after the transition
        # ---------------------------------------------------------------------
        if item.status == "done":
            linked_commits = [c for c in commits if _matches_commit(item, c, links)]
            post_done_commits = [
                c for c in linked_commits if c.committed_at > item.source_updated_at
            ]
            if post_done_commits:
                latest_post_commit = max(post_done_commits, key=lambda c: c.committed_at)
                done_date = item.source_updated_at.strftime("%Y-%m-%d")
                commit_date = latest_post_commit.committed_at.strftime("%Y-%m-%d")
                conflicts.append(
                    ConflictFinding(
                        conflict_id="CF-3",
                        description=(
                            f"{item.external_id} was marked Done on {done_date}, but commits "
                            f"on its branch continued to {commit_date}."
                        ),
                        work_item_id=item.work_item_id,
                        work_item_external_id=item.external_id,
                        target_type="commit",
                        target_identifier=latest_post_commit.sha[:8],
                        evidence_keys=[item.external_id, latest_post_commit.sha[:8]],
                    )
                )

    return conflicts
