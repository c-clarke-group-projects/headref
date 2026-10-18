"""Deterministic read tool T-005: get_reviews (P3-002, Issue #22).

Returns GitHub pull request reviews given or received by a subject person
within a window, including decision states and body excerpts from PostgreSQL.
"""

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.canonical import AppUser, Repository
from app.models.work import PullRequest, Review
from app.schemas.errors import ToolFailure
from app.schemas.tools import (
    GetReviewsInput,
    GetReviewsOutput,
    ReviewOut,
)
from app.tools.base import execute_tool_query


def get_reviews(
    session: Session,
    input_data: GetReviewsInput,
) -> GetReviewsOutput | ToolFailure:
    """Retrieve pull request reviews given or received by a subject."""

    def _query() -> GetReviewsOutput | ToolFailure:
        user = session.get(AppUser, input_data.subject_user_id)
        if not user:
            return ToolFailure(
                tool_id="T-005",
                error_type="NOT_FOUND",
                detail=f"User with ID {input_data.subject_user_id} does not exist",
            )

        stmt = (
            select(Review, PullRequest, Repository)
            .join(PullRequest, Review.pull_request_id == PullRequest.id)
            .join(Repository, PullRequest.repository_id == Repository.id)
            .where(
                Review.submitted_at.between(input_data.window_start, input_data.window_end),
            )
        )

        if input_data.direction == "given":
            stmt = stmt.where(Review.reviewer_app_user_id == input_data.subject_user_id)
        elif input_data.direction == "received":
            stmt = stmt.where(PullRequest.author_app_user_id == input_data.subject_user_id)
        else:  # "both"
            stmt = stmt.where(
                or_(
                    Review.reviewer_app_user_id == input_data.subject_user_id,
                    PullRequest.author_app_user_id == input_data.subject_user_id,
                )
            )

        stmt = stmt.order_by(Review.submitted_at.desc())
        results = session.execute(stmt).all()

        # Fallback: the window returned nothing, so show the latest reviews.
        if not results:
            fallback_stmt = (
                select(Review, PullRequest, Repository)
                .join(PullRequest, Review.pull_request_id == PullRequest.id)
                .join(Repository, PullRequest.repository_id == Repository.id)
            )
            if input_data.direction == "given":
                fallback_stmt = fallback_stmt.where(
                    Review.reviewer_app_user_id == input_data.subject_user_id
                )
            elif input_data.direction == "received":
                fallback_stmt = fallback_stmt.where(
                    PullRequest.author_app_user_id == input_data.subject_user_id
                )
            else:
                fallback_stmt = fallback_stmt.where(
                    or_(
                        Review.reviewer_app_user_id == input_data.subject_user_id,
                        PullRequest.author_app_user_id == input_data.subject_user_id,
                    )
                )
            fallback_stmt = fallback_stmt.order_by(Review.submitted_at.desc()).limit(5)
            results = session.execute(fallback_stmt).all()

        reviews: list[ReviewOut] = []
        for review, pr, repo in results:
            reviews.append(
                ReviewOut(
                    review_id=review.id,
                    pull_request_id=pr.id,
                    pull_request_number=pr.number,
                    repo_full_name=repo.full_name,
                    reviewer_user_id=review.reviewer_app_user_id,
                    state=review.state,  # type: ignore[arg-type]
                    body_excerpt=review.body_excerpt,
                    submitted_at=review.submitted_at,
                    source_url=review.source_url,
                    retrieved_at=review.retrieved_at,
                )
            )

        return GetReviewsOutput(reviews=reviews)

    return execute_tool_query("T-005", _query)
