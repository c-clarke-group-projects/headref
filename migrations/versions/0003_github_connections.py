"""GitHub App installations and selected repositories per workspace (W3, DEC-022).

Additive only. No token column: installation tokens are minted per use.

Revision ID: 0003_github_connections
Revises: 0002_workspaces
Create Date: 2026-10-19 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_github_connections"
down_revision: str | None = "0002_workspaces"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "github_installation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=32), nullable=False),
        sa.Column("connected_by_account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connected_by_account_id"], ["account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "installation_id", name="uq_github_installation"),
    )
    op.create_index("ix_github_installation_workspace_id", "github_installation", ["workspace_id"])

    op.create_table(
        "workspace_repository",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("github_installation_id", sa.Integer(), nullable=False),
        sa.Column("github_repo_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("added_by_account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["github_installation_id"], ["github_installation.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["added_by_account_id"], ["account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "github_repo_id", name="uq_workspace_repository"),
    )
    op.create_index(
        "ix_workspace_repository_workspace_id", "workspace_repository", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_repository_workspace_id", table_name="workspace_repository")
    op.drop_table("workspace_repository")
    op.drop_index("ix_github_installation_workspace_id", table_name="github_installation")
    op.drop_table("github_installation")
