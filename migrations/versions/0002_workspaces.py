"""Accounts, workspaces and workspace membership (W1, DEC-021).

Additive only: no existing table changes. Per-workspace columns on the source tables
come in later migrations.

Revision ID: 0002_workspaces
Revises: 0001_initial
Create Date: 2026-10-19 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_workspaces"
down_revision: str | None = "0001_initial"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("github_login", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("app_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["app_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("github_user_id"),
        sa.UniqueConstraint("app_user_id"),
    )

    op.create_table(
        "workspace",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("created_by_account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_account_id"], ["account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "workspace_member",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("invited_by_account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["account_id"], ["account.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_account_id"], ["account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "account_id", name="uq_workspace_member_account"),
        sa.CheckConstraint(
            "role IN ('owner', 'manager', 'viewer')", name="ck_workspace_member_role"
        ),
    )
    op.create_index("ix_workspace_member_workspace_id", "workspace_member", ["workspace_id"])
    op.create_index("ix_workspace_member_account_id", "workspace_member", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_workspace_member_account_id", table_name="workspace_member")
    op.drop_index("ix_workspace_member_workspace_id", table_name="workspace_member")
    op.drop_table("workspace_member")
    op.drop_table("workspace")
    op.drop_table("account")
