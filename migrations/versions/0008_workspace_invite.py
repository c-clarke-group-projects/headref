"""Workspace invites by GitHub account (W9, DEC-021).

Additive only.

Revision ID: 0008_workspace_invite
Revises: 0007_workspace_feed
Create Date: 2026-10-19 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_workspace_invite"
down_revision: str | None = "0007_workspace_feed"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_invite",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("github_login", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("invited_by_account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('manager', 'viewer')", name="ck_workspace_invite_role"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_account_id"], ["account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "github_user_id", name="uq_workspace_invite_user"),
    )
    op.create_index(
        "ix_workspace_invite_workspace_id", "workspace_invite", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_workspace_invite_github_user_id", "workspace_invite", ["github_user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_invite_github_user_id", table_name="workspace_invite")
    op.drop_index("ix_workspace_invite_workspace_id", table_name="workspace_invite")
    op.drop_table("workspace_invite")
