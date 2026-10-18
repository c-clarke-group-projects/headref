"""Unit tests for the canonical SQLAlchemy models (P1-001, Issue #7)."""

from app.models import Base


def test_table_count_and_names() -> None:
    """20 tables must be defined, and none named 'user' (DATA_AND_EVIDENCE.md 6.3).

    account, workspace and workspace_member added by migration 0002 (DEC-021).
    """
    table_names = set(Base.metadata.tables.keys())

    expected_tables = {
        "organization",
        "team",
        "app_user",
        "repository",
        "project",
        "identity_link",
        "unmatched_entity",
        "work_item",
        "work_item_dependency",
        "pull_request",
        "commit",
        "review",
        "work_item_link",
        "insight",
        "agent_run",
        "sync_run",
        "sync_cursor",
        "account",
        "workspace",
        "workspace_member",
    }

    assert len(table_names) == 20
    assert table_names == expected_tables
    assert "user" not in table_names
    assert "app_user" in table_names


def test_external_data_freshness_columns() -> None:
    """Every external-data table must carry source_updated_at and retrieved_at non-null."""
    external_tables = ["work_item", "pull_request", "commit", "review"]

    for table_name in external_tables:
        table = Base.metadata.tables[table_name]
        assert "retrieved_at" in table.c, f"Missing retrieved_at on {table_name}"
        msg_retrieved = f"retrieved_at must be non-null on {table_name}"
        assert not table.c["retrieved_at"].nullable, msg_retrieved

        # work_item and pull_request carry source_updated_at
        if table_name in ("work_item", "pull_request"):
            assert "source_updated_at" in table.c, f"Missing source_updated_at on {table_name}"
            msg_source = f"source_updated_at must be non-null on {table_name}"
            assert not table.c["source_updated_at"].nullable, msg_source


def test_unique_constraints_exist() -> None:
    """Key natural uniqueness constraints must exist for idempotency (ARCHITECTURE.md 6)."""
    # 1. identity_link(integration, external_id)
    id_link = Base.metadata.tables["identity_link"]
    unique_cols = [
        {c.name for c in uq.columns} for uq in id_link.constraints if getattr(uq, "columns", None)
    ]
    assert {"integration", "external_id"} in unique_cols

    # 2. unmatched_entity(integration, external_id)
    unmatched = Base.metadata.tables["unmatched_entity"]
    unique_cols = [
        {c.name for c in uq.columns} for uq in unmatched.constraints if getattr(uq, "columns", None)
    ]
    assert {"integration", "external_id"} in unique_cols

    # 3. commit(repository_id, sha)
    commit = Base.metadata.tables["commit"]
    unique_cols = [
        {c.name for c in uq.columns} for uq in commit.constraints if getattr(uq, "columns", None)
    ]
    assert {"repository_id", "sha"} in unique_cols

    # 4. work_item_link(work_item_id, target_type, target_id)
    link = Base.metadata.tables["work_item_link"]
    unique_cols = [
        {c.name for c in uq.columns} for uq in link.constraints if getattr(uq, "columns", None)
    ]
    assert {"work_item_id", "target_type", "target_id"} in unique_cols

    # 5. sync_cursor primary key is (source, scope)
    cursor = Base.metadata.tables["sync_cursor"]
    pk_cols = {c.name for c in cursor.primary_key.columns}
    assert pk_cols == {"source", "scope"}
