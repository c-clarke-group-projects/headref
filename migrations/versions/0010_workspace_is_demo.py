"""Demo workspaces seeded from recorded fixtures (#46, agreed 2026-10-22).

Additive only: 1 column, false for every existing workspace.

Revision ID: 0010_workspace_is_demo
Revises: 0009_review_request
Create Date: 2026-10-22 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_workspace_is_demo"
down_revision: str | None = "0009_review_request"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column(
        "workspace",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("workspace", "is_demo")
