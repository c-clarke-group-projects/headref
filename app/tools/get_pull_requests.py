"""Deterministic read tool T-003: get_pull_requests (P3-002, Issue #22).

Returns GitHub pull requests authored by a person, with state, draft flag,
review state, branch name and check status from PostgreSQL.

When no pull requests fall within the requested window the tool returns up to 5
of the user's most recently updated pull requests without a date filter. This
allows the agent to infer likely current work even for repositories that have
been quiet for longer than the configured window (DEC-020).
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.canonical import AppUser, Repository
from app.models.work import PullRequest
from app.schemas.errors import ToolFailure
from app.schemas.tools import (
    GetPullRequestsInput,
    GetPullRequestsOutput,
    PullRequestOut,
)
from app.tools.base import execute_tool_query

# Maximum PRs to show when falling back to all-time most-recent.
_FALLBACK_LIMIT = 5


def get_pull_requests(
    session: Session,
    input_data: GetPullRequestsInput,
) -> GetPullRequestsOutput | ToolFailure:
    """Retrieve pull requests authored by a subject within a window.

    Primary query honours the requested window.  When that returns nothing, a
    fallback query fetches the _FALLBACK_LIMIT most recently updated PRs for
    the user across all time so the agent always has real data to reason from.
    """

    def _query() -> GetPullRequestsOutput | ToolFailure:
        user = session.get(AppUser, input_data.subject_user_id)
        if not user:
            return ToolFailure(
                tool_id="T-003",
                error_type="NOT_FOUND",
                detail=f"User with ID {input_data.subject_user_id} does not exist",
            )

        # Primary: within the requested window
        stmt = (
            select(PullRequest, Repository)
            .join(Repository, PullRequest.repository_id == Repository.id)
            .where(
                PullRequest.author_app_user_id == input_data.subject_user_id,
                or_(
                    PullRequest.created_at_source.between(
                        input_data.window_start, input_data.window_end
                    ),
                    PullRequest.source_updated_at.between(
                        input_data.window_start, input_data.window_end
                    ),
                ),
            )
        )

        if input_data.states:
            stmt = stmt.where(PullRequest.state.in_(input_data.states))

        if not input_data.include_drafts:
            stmt = stmt.where(PullRequest.is_draft.is_(False))

        stmt = stmt.order_by(PullRequest.source_updated_at.desc())
        results = session.execute(stmt).all()

        # Fallback: the window returned nothing, so show the latest pull requests.
        if not results:
            fallback_stmt = (
                select(PullRequest, Repository)
                .join(Repository, PullRequest.repository_id == Repository.id)
                .where(PullRequest.author_app_user_id == input_data.subject_user_id)
                .order_by(PullRequest.source_updated_at.desc())
                .limit(_FALLBACK_LIMIT)
            )
            if input_data.states:
                fallback_stmt = fallback_stmt.where(PullRequest.state.in_(input_data.states))
            if not input_data.include_drafts:
                fallback_stmt = fallback_stmt.where(PullRequest.is_draft.is_(False))
            results = session.execute(fallback_stmt).all()

        pull_requests: list[PullRequestOut] = []
        for pr, repo in results:
            pull_requests.append(
                PullRequestOut(
                    pull_request_id=pr.id,
                    number=pr.number,
                    repo_full_name=repo.full_name,
                    title=pr.title,
                    body_excerpt=pr.body_excerpt,
                    state=pr.state,  # type: ignore[arg-type]
                    is_draft=pr.is_draft,
                    branch_name=pr.branch_name,
                    author_user_id=pr.author_app_user_id,
                    review_state=pr.review_state or "none",  # type: ignore[arg-type]
                    last_review_at=pr.last_review_at,
                    checks_state=pr.checks_state or "unknown",  # type: ignore[arg-type]
                    created_at=pr.created_at_source,
                    last_commit_at=pr.last_commit_at,
                    merged_at=pr.merged_at,
                    source_url=pr.source_url,
                    source_updated_at=pr.source_updated_at,
                    retrieved_at=pr.retrieved_at,
                )
            )

        return GetPullRequestsOutput(pull_requests=pull_requests)

    return execute_tool_query("T-003", _query)
