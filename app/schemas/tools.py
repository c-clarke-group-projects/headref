"""Deterministic read tool contracts T-001 through T-007 (P1-002, Issue #8)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ---------------------------------------------------------------------------
# T-001: get_team_members
# ---------------------------------------------------------------------------


class GetTeamMembersInput(BaseModel):
    """Input parameters for get_team_members tool."""

    team_id: int = Field(description="Internal ID of the team")
    include_inactive: bool = Field(
        default=False, description="Whether to include inactive team members"
    )


class LinkedAccount(BaseModel):
    """External account mapping verified for a team member."""

    integration: Literal["github", "jira"] = Field(description="Upstream platform name")
    external_handle: str = Field(description="Handle or username on the upstream platform")
    match_method: Literal["manual", "inferred"] = Field(description="How identity link was matched")
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="Confidence level of identity link"
    )


class TeamMemberOut(BaseModel):
    """Team member details with verified account mappings."""

    user_id: int = Field(description="Internal user ID")
    display_name: str = Field(description="Canonical display name of team member")
    role_label: str | None = Field(default=None, description="Role label if configured")
    is_active: bool = Field(description="Whether team member is currently active")
    linked_accounts: list[LinkedAccount] = Field(
        default_factory=list, description="Verified external account links"
    )


class GetTeamMembersOutput(BaseModel):
    """Output for get_team_members tool."""

    members: list[TeamMemberOut] = Field(default_factory=list, description="List of team members")
    unmatched_count: int = Field(
        default=0, description="Count of unmatched entities for UI warning banner"
    )


# ---------------------------------------------------------------------------
# T-002: get_assigned_work_items
# ---------------------------------------------------------------------------


class GetAssignedWorkItemsInput(BaseModel):
    """Input parameters for get_assigned_work_items tool."""

    subject_user_id: int = Field(description="Internal user ID of subject")
    window_start: datetime = Field(description="Start timestamp of time window in UTC")
    window_end: datetime = Field(description="End timestamp of time window in UTC")
    include_done: bool = Field(
        default=True, description="Whether to include done items for conflict detection"
    )
    statuses: list[str] | None = Field(
        default=None, description="Optional filter for specific normalized statuses"
    )


class DependencyOut(BaseModel):
    """A blocking work item, which may be assigned to someone else."""

    external_id: str = Field(description="External issue key e.g. AUTH-245")
    status: Literal["todo", "in_progress", "in_review", "done", "blocked"] = Field(
        description="Normalized work item status"
    )
    priority: str | None = Field(default=None, description="Normalized priority label")
    is_flagged: bool = Field(default=False, description="Jira impediment flag")
    source_url: str = Field(description="Deep link to Jira record")
    source_updated_at: datetime = Field(description="When updated at source")
    retrieved_at: datetime = Field(description="When ingested by Headref")


class WorkItemOut(BaseModel):
    """Normalized Jira work item output representation."""

    work_item_id: int = Field(description="Internal work item ID")
    external_id: str = Field(description="External issue key e.g. AUTH-245")
    title: str = Field(description="Work item title (untrusted source text)")
    status: Literal["todo", "in_progress", "in_review", "done", "blocked"] = Field(
        description="Normalized work item status"
    )
    raw_status: str = Field(description="Upstream raw status preserved")
    assignee_user_id: int | None = Field(
        default=None, description="Internal user ID of assignee or None if unmatched"
    )
    priority: str | None = Field(default=None, description="Normalized priority label")
    due_date: datetime | None = Field(default=None, description="Target due date if set")
    is_flagged: bool = Field(default=False, description="Jira impediment flag")
    blocked_by: list[str] = Field(
        default_factory=list, description="External keys of blocking work items"
    )
    blocked_by_items: list[DependencyOut] = Field(
        default_factory=list, description="The blocking work item records, for evidence"
    )
    source_url: str = Field(description="Deep link to Jira record")
    source_updated_at: datetime = Field(description="When updated at source")
    retrieved_at: datetime = Field(description="When ingested by Headref")


class GetAssignedWorkItemsOutput(BaseModel):
    """Output for get_assigned_work_items tool."""

    items: list[WorkItemOut] = Field(default_factory=list, description="Assigned work items")


# ---------------------------------------------------------------------------
# T-003: get_pull_requests
# ---------------------------------------------------------------------------


class GetPullRequestsInput(BaseModel):
    """Input parameters for get_pull_requests tool."""

    subject_user_id: int = Field(description="Internal user ID of subject")
    window_start: datetime = Field(description="Start timestamp of time window in UTC")
    window_end: datetime = Field(description="End timestamp of time window in UTC")
    states: list[Literal["open", "merged", "closed"]] | None = Field(
        default=None, description="Filter for pull request states"
    )
    include_drafts: bool = Field(default=True, description="Whether to include draft pull requests")


class PullRequestOut(BaseModel):
    """Normalized GitHub pull request output representation."""

    pull_request_id: int = Field(description="Internal pull request ID")
    number: int = Field(description="Pull request number in repository")
    repo_full_name: str = Field(description="Full repository name e.g. acme/api")
    title: str = Field(description="Pull request title (untrusted source text)")
    body_excerpt: str | None = Field(
        default=None, description="Capped and sanitized body excerpt (untrusted)"
    )
    state: Literal["open", "merged", "closed"] = Field(description="Pull request state")
    is_draft: bool = Field(description="Whether pull request is a draft")
    branch_name: str | None = Field(default=None, description="Source branch name")
    author_user_id: int | None = Field(
        default=None, description="Internal user ID of author or None if unmatched"
    )
    review_state: Literal["none", "pending", "approved", "changes_requested"] = Field(
        description="Overall review state"
    )
    last_review_at: datetime | None = Field(default=None, description="Timestamp of latest review")
    checks_state: Literal["unknown", "passing", "failing"] = Field(description="CI checks status")
    created_at: datetime = Field(description="When opened at source")
    last_commit_at: datetime | None = Field(
        default=None, description="Timestamp of latest commit on PR"
    )
    merged_at: datetime | None = Field(default=None, description="Timestamp when merged")
    source_url: str = Field(description="Deep link to GitHub PR")
    source_updated_at: datetime = Field(description="When updated at source")
    retrieved_at: datetime = Field(description="When ingested by Headref")


class GetPullRequestsOutput(BaseModel):
    """Output for get_pull_requests tool."""

    pull_requests: list[PullRequestOut] = Field(
        default_factory=list, description="Retrieved pull requests"
    )


# ---------------------------------------------------------------------------
# T-004: get_commits
# ---------------------------------------------------------------------------


class GetCommitsInput(BaseModel):
    """Input parameters for get_commits tool."""

    subject_user_id: int = Field(description="Internal user ID of subject")
    window_start: datetime = Field(description="Start timestamp of time window in UTC")
    window_end: datetime = Field(description="End timestamp of time window in UTC")
    limit: int = Field(default=100, description="Maximum commits to return")


class CommitOut(BaseModel):
    """Normalized GitHub commit output representation."""

    commit_id: int = Field(description="Internal commit ID")
    sha: str = Field(description="Git commit SHA hash")
    repo_full_name: str = Field(description="Full repository name")
    message_excerpt: str = Field(
        description="Capped and sanitized commit message excerpt (untrusted)"
    )
    branch_name: str | None = Field(default=None, description="Branch name where commit appeared")
    author_user_id: int | None = Field(
        default=None, description="Internal user ID of author or None if unmatched"
    )
    committed_at: datetime = Field(description="Timestamp when commit was authored")
    source_url: str = Field(description="Deep link to commit")
    retrieved_at: datetime = Field(description="When ingested by Headref")


class GetCommitsOutput(BaseModel):
    """Output for get_commits tool."""

    commits: list[CommitOut] = Field(default_factory=list, description="List of commits")
    truncated: bool = Field(description="True when more rows exist than limit")


# ---------------------------------------------------------------------------
# T-005: get_reviews
# ---------------------------------------------------------------------------


class GetReviewsInput(BaseModel):
    """Input parameters for get_reviews tool."""

    subject_user_id: int = Field(description="Internal user ID of subject")
    window_start: datetime = Field(description="Start timestamp of time window in UTC")
    window_end: datetime = Field(description="End timestamp of time window in UTC")
    direction: Literal["given", "received", "both"] = Field(
        default="both", description="Filter reviews given, received, or both"
    )


class ReviewOut(BaseModel):
    """Normalized GitHub code review output representation."""

    review_id: int = Field(description="Internal review ID")
    pull_request_id: int = Field(description="Internal pull request ID")
    pull_request_number: int = Field(description="Pull request number")
    repo_full_name: str = Field(description="Full repository name")
    reviewer_user_id: int | None = Field(
        default=None, description="Internal user ID of reviewer or None if unmatched"
    )
    state: Literal["approved", "changes_requested", "commented", "dismissed"] = Field(
        description="Review decision state"
    )
    body_excerpt: str | None = Field(
        default=None, description="Capped and sanitized review excerpt (untrusted)"
    )
    submitted_at: datetime = Field(description="Timestamp when review was submitted")
    source_url: str = Field(description="Deep link to review")
    retrieved_at: datetime = Field(description="When ingested by Headref")


class GetReviewsOutput(BaseModel):
    """Output for get_reviews tool."""

    reviews: list[ReviewOut] = Field(default_factory=list, description="List of reviews")


# ---------------------------------------------------------------------------
# T-006: get_work_item_links
# ---------------------------------------------------------------------------


class GetWorkItemLinksInput(BaseModel):
    """Input parameters for get_work_item_links tool."""

    work_item_ids: list[int] | None = Field(
        default=None, description="Filter links by internal work item IDs"
    )
    pull_request_ids: list[int] | None = Field(
        default=None, description="Filter links by internal pull request IDs"
    )
    min_confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        default="MEDIUM", description="Minimum confidence threshold for returned links"
    )

    @model_validator(mode="after")
    def validate_ids_provided(self) -> "GetWorkItemLinksInput":
        """Validate that at least one of work_item_ids or pull_request_ids is given."""
        if not self.work_item_ids and not self.pull_request_ids:
            raise ValueError("At least one of work_item_ids or pull_request_ids must be provided")
        return self


class WorkItemLinkOut(BaseModel):
    """Stored correlation link between Jira work item and code activity."""

    link_id: int = Field(description="Internal link ID")
    work_item_id: int = Field(description="Internal work item ID")
    work_item_external_id: str = Field(description="External issue key e.g. AUTH-245")
    target_type: Literal["pull_request", "branch", "commit", "review"] = Field(
        description="Entity type being linked to"
    )
    target_id: int = Field(description="Internal ID of target entity")
    link_method: Literal[
        "jira_remote_link",
        "branch_name",
        "pr_title",
        "pr_body",
        "commit_message",
    ] = Field(description="Method used to establish link")
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="Confidence level of link")
    created_at: datetime = Field(description="When link was established")


class GetWorkItemLinksOutput(BaseModel):
    """Output for get_work_item_links tool."""

    links: list[WorkItemLinkOut] = Field(default_factory=list, description="Discovered links")


# ---------------------------------------------------------------------------
# T-007: get_source_health
# ---------------------------------------------------------------------------


class GetSourceHealthInput(BaseModel):
    """Input parameters for get_source_health tool."""

    team_id: int | None = Field(default=None, description="Internal team ID (legacy path)")
    workspace_id: int | None = Field(
        default=None, description="Workspace whose sync history to read (W7)"
    )
    sources: list[Literal["github", "jira"]] = Field(description="Sources to evaluate health for")


class SourceHealthOut(BaseModel):
    """Operational freshness and availability state for an integration source."""

    source: Literal["github", "jira"] = Field(description="Data source name")
    state: Literal["fresh", "stale", "unavailable"] = Field(description="Evaluated freshness state")
    last_success_at: datetime | None = Field(
        default=None, description="Timestamp of most recent successful sync"
    )
    last_attempt_at: datetime | None = Field(
        default=None, description="Timestamp of most recent sync attempt"
    )
    last_error_type: str | None = Field(
        default=None,
        description="Typed error category if last attempt failed (TIMEOUT, RATE_LIMITED, etc.)",
    )
    age_hours: float | None = Field(default=None, description="Hours since last successful sync")


class GetSourceHealthOutput(BaseModel):
    """Output for get_source_health tool."""

    sources: list[SourceHealthOut] = Field(
        default_factory=list, description="Per-source health status"
    )
