"""Person status for the People screen (W6, DEC-024).

app_user.status: suggested (discovered, holds exactly 1 external account), confirmed (a
manager confirmed it, possibly merging accounts), ignored, bot. Existing rows become
confirmed, so the legacy pages are unchanged.

Revision ID: 0006_person_status
Revises: 0005_workspace_scope
Create Date: 2026-10-19 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_person_status"
down_revision: str | None = "0005_workspace_scope"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="confirmed"),
    )
    op.create_check_constraint(
        "ck_app_user_status",
        "app_user",
        "status IN ('suggested', 'confirmed', 'ignored', 'bot')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_app_user_status", "app_user", type_="check")
    op.drop_column("app_user", "status")
