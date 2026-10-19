"""Pending review requests on pull requests (W11, DEC-025).

Additive only.

Revision ID: 0009_review_request
Revises: 0008_workspace_invite
Create Date: 2026-10-20 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_review_request"
down_revision: str | None = "0008_workspace_invite"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "review_request",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pull_request_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_external_id", sa.String(length=255), nullable=False),
        sa.Column("reviewer_app_user_id", sa.Integer(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["pull_request_id"], ["pull_request.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_app_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "pull_request_id", "reviewer_external_id", name="uq_review_request_reviewer"
        ),
    )
    op.create_index(
        "ix_review_request_reviewer_app_user_id",
        "review_request",
        ["reviewer_app_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_review_request_reviewer_app_user_id", table_name="review_request")
    op.drop_table("review_request")
