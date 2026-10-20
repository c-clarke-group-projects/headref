"""Scope source data to workspaces (W5, DEC-021).

Each workspace gets its own organization row, which the existing source tables already
hang off. Uniqueness moves from global to per organization, so 2 workspaces can sync the
same repository, Jira project or person without colliding. sync_run gains workspace_id.

Backfills existing rows; nothing is dropped. Downgrade restores the global constraints
and fails if per-workspace duplicates now exist, which is the correct outcome.

Revision ID: 0005_workspace_scope
Revises: 0004_jira_connections
Create Date: 2026-10-19 00:00:00.000000+00:00
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_workspace_scope"
down_revision: str | None = "0004_jira_connections"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. A dedicated organization row per workspace.
    op.add_column("workspace", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_workspace_organization", "workspace", "organization", ["organization_id"], ["id"]
    )
    workspaces = bind.execute(
        sa.text("SELECT id, name FROM workspace WHERE organization_id IS NULL")
    ).all()
    for workspace_id, name in workspaces:
        org_id = bind.execute(
            sa.text("INSERT INTO organization (name, created_at) VALUES (:n, :t) RETURNING id"),
            {"n": name, "t": datetime.now(UTC)},
        ).scalar_one()
        bind.execute(
            sa.text("UPDATE workspace SET organization_id = :o WHERE id = :w"),
            {"o": org_id, "w": workspace_id},
        )
    op.create_unique_constraint("uq_workspace_organization", "workspace", ["organization_id"])

    # 2. Repositories and projects are unique per organization.
    op.drop_constraint("repository_full_name_key", "repository", type_="unique")
    op.create_unique_constraint(
        "uq_repository_org_full_name", "repository", ["organization_id", "full_name"]
    )
    op.drop_constraint("project_key_key", "project", type_="unique")
    op.create_unique_constraint("uq_project_org_key", "project", ["organization_id", "key"])

    # 3. Identity links belong to an organization, taken from their app_user. Lookups
    #    always filter on it, so a link without one never attributes anything.
    op.add_column("identity_link", sa.Column("organization_id", sa.Integer(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE identity_link SET organization_id = "
            "(SELECT organization_id FROM app_user WHERE app_user.id = identity_link.app_user_id)"
        )
    )
    op.create_foreign_key(
        "fk_identity_link_organization",
        "identity_link",
        "organization",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_identity_link_integration_external", "identity_link", type_="unique")
    op.create_unique_constraint(
        "uq_identity_link_org_integration_external",
        "identity_link",
        ["organization_id", "integration", "external_id"],
    )

    # 4. Unmatched accounts belong to an organization; existing rows to the oldest one.
    op.add_column("unmatched_entity", sa.Column("organization_id", sa.Integer(), nullable=True))
    bind.execute(
        sa.text("UPDATE unmatched_entity SET organization_id = (SELECT MIN(id) FROM organization)")
    )
    op.create_foreign_key(
        "fk_unmatched_entity_organization",
        "unmatched_entity",
        "organization",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_unmatched_entity_integration_external", "unmatched_entity", type_="unique"
    )
    op.create_unique_constraint(
        "uq_unmatched_entity_org_integration_external",
        "unmatched_entity",
        ["organization_id", "integration", "external_id"],
    )

    # 5. Sync history per workspace. Null for the legacy global syncs.
    op.add_column("sync_run", sa.Column("workspace_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_sync_run_workspace",
        "sync_run",
        "workspace",
        ["workspace_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_sync_run_workspace_started", "sync_run", ["workspace_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_run_workspace_started", table_name="sync_run")
    op.drop_constraint("fk_sync_run_workspace", "sync_run", type_="foreignkey")
    op.drop_column("sync_run", "workspace_id")

    op.drop_constraint(
        "uq_unmatched_entity_org_integration_external", "unmatched_entity", type_="unique"
    )
    op.create_unique_constraint(
        "uq_unmatched_entity_integration_external",
        "unmatched_entity",
        ["integration", "external_id"],
    )
    op.drop_constraint("fk_unmatched_entity_organization", "unmatched_entity", type_="foreignkey")
    op.drop_column("unmatched_entity", "organization_id")

    op.drop_constraint("uq_identity_link_org_integration_external", "identity_link", type_="unique")
    op.create_unique_constraint(
        "uq_identity_link_integration_external", "identity_link", ["integration", "external_id"]
    )
    op.drop_constraint("fk_identity_link_organization", "identity_link", type_="foreignkey")
    op.drop_column("identity_link", "organization_id")

    op.drop_constraint("uq_project_org_key", "project", type_="unique")
    op.create_unique_constraint("project_key_key", "project", ["key"])
    op.drop_constraint("uq_repository_org_full_name", "repository", type_="unique")
    op.create_unique_constraint("repository_full_name_key", "repository", ["full_name"])

    op.drop_constraint("uq_workspace_organization", "workspace", type_="unique")
    op.drop_constraint("fk_workspace_organization", "workspace", type_="foreignkey")
    op.drop_column("workspace", "organization_id")
