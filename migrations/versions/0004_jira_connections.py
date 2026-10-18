"""Atlassian connection and selected Jira projects per workspace (W4, DEC-023).

Additive only. Tokens are stored encrypted (Fernet, CREDENTIAL_ENCRYPTION_KEY).

Revision ID: 0004_jira_connections
Revises: 0003_github_connections
Create Date: 2026-10-19 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_jira_connections"
down_revision: str | None = "0003_github_connections"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "jira_connection",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("cloud_id", sa.String(length=64), nullable=True),
        sa.Column("site_url", sa.String(length=255), nullable=True),
        sa.Column("site_name", sa.String(length=255), nullable=True),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scopes", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("connected_by_account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connected_by_account_id"], ["account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id"),
    )

    op.create_table(
        "workspace_jira_project",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("jira_connection_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("added_by_account_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["jira_connection_id"], ["jira_connection.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by_account_id"], ["account.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "project_id", name="uq_workspace_jira_project"),
    )
    op.create_index(
        "ix_workspace_jira_project_workspace_id", "workspace_jira_project", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_jira_project_workspace_id", table_name="workspace_jira_project")
    op.drop_table("workspace_jira_project")
    op.drop_table("jira_connection")
