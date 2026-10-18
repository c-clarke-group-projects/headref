"""Unit and integration tests for the sync CLI and orchestration pipeline (P2-008, Issue #19)."""

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.integrations.errors import AuthError
from app.models.canonical import Organization
from app.models.operations import SyncCursor, SyncRun
from app.models.work import Commit, PullRequest
from app.sync import main, sanitize_error_detail, sync_github, sync_jira


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_sanitize_error_detail_redacts_tokens() -> None:
    """sanitize_error_detail masks passwords, tokens and secrets per NFR-011."""
    err = "Invalid credentials: token=secret-gh-token-12345 in header Authorization: Bearer abcde"
    cleaned = sanitize_error_detail(err)
    assert cleaned is not None
    assert "secret-gh-token-12345" not in cleaned
    assert "abcde" not in cleaned
    assert "token=***" in cleaned


def test_sync_github_success(db_session: Session) -> None:
    """sync_github successfully ingests repos, PRs, commits, reviews and sets cursor."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    mock_client = MagicMock()
    mock_client.get_repository.return_value = {
        "id": 1,
        "name": "headref",
        "full_name": "keshan-dev/headref",
        "default_branch": "main",
    }
    mock_client.list_pull_requests.return_value = [
        {
            "id": 101,
            "number": 1,
            "title": "AUTH-245: session seam",
            "body": "Fixes #1",
            "state": "open",
            "draft": False,
            "head": {"ref": "feature/AUTH-245-login"},
            "created_at": "2026-09-20T10:00:00Z",
            "updated_at": "2026-09-20T11:00:00Z",
            "user": {"id": 219891474, "login": "keshan-dev"},
        }
    ]
    mock_client.list_commits.return_value = [
        {
            "sha": "a1b2c3d4e5f67890123456789abcdef012345678",
            "commit": {
                "message": "feat: session seam AUTH-245",
                "author": {"date": "2026-09-20T09:00:00Z"},
            },
            "author": {"id": 219891474, "login": "keshan-dev"},
        }
    ]
    mock_client.list_reviews.return_value = [
        {
            "id": 9991,
            "state": "APPROVED",
            "body": "Looks good",
            "submitted_at": "2026-09-20T12:00:00Z",
            "user": {"id": 221026807, "login": "IsiwaraKumarage8"},
        }
    ]

    run = sync_github(
        session=db_session,
        team_id=1,
        scope="keshan-dev/headref",
        gh_client=mock_client,
    )
    assert run.status == "success"
    assert run.items_fetched == 3  # 1 PR + 1 commit + 1 review
    assert run.items_written == 3
    assert run.items_skipped == 0
    assert run.finished_at is not None

    # Verify cursor exists
    cursor = db_session.scalar(
        select(SyncCursor).where(
            SyncCursor.source == "github",
            SyncCursor.scope == "keshan-dev/headref",
        )
    )
    assert cursor is not None
    assert cursor.cursor_value is not None


def test_sync_jira_success(db_session: Session) -> None:
    """sync_jira successfully ingests projects, issues, dependencies and sets cursor."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    mock_client = MagicMock()
    mock_client.list_projects.return_value = [
        {"id": "10000", "key": "AUTH", "name": "Authentication Service"}
    ]
    mock_client.search_issues.return_value = [
        {
            "id": "10001",
            "key": "AUTH-245",
            "fields": {
                "summary": "Session seam",
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "duedate": "2026-09-22",
                "flagged": False,
                "project": {"id": "10000", "key": "AUTH"},
                "assignee": {"accountId": "5f3a1b2c3d4e", "displayName": "Keshan P."},
                "issuelinks": [],
            },
        }
    ]
    mock_client.get_remote_links.return_value = []

    run = sync_jira(
        session=db_session,
        team_id=1,
        scope="ALL",
        jira_client=mock_client,
    )
    assert run.status == "success"
    assert run.items_fetched >= 2
    assert run.items_written >= 2
    assert run.items_skipped == 0

    cursor = db_session.scalar(
        select(SyncCursor).where(
            SyncCursor.source == "jira",
            SyncCursor.scope == "ALL",
        )
    )
    assert cursor is not None


def test_sync_failure_records_typed_error_and_no_secret_in_detail(
    db_session: Session,
) -> None:
    """A failed sync records typed error_type and redacts any secret from detail."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    mock_client = MagicMock()
    mock_client.get_repository.side_effect = AuthError(
        "Invalid authorization: token=secret-gh-token-12345"
    )

    run = sync_github(
        session=db_session,
        team_id=1,
        scope="keshan-dev/headref",
        gh_client=mock_client,
    )
    assert run.status == "failed"
    assert run.error_type == "AUTH_FAILED"
    assert run.error_detail is not None
    assert "secret-gh-token-12345" not in run.error_detail
    # The whole "authorization: token=<secret>" span is one match, so the marker
    # carries the label that opened it rather than the inner key.
    assert "***" in run.error_detail

    # Failed run must NOT create or advance cursor
    cursor = db_session.scalar(
        select(SyncCursor).where(
            SyncCursor.source == "github",
            SyncCursor.scope == "keshan-dev/headref",
        )
    )
    assert cursor is None


def test_reset_cursor_clears_existing_cursor(db_session: Session) -> None:
    """--reset-cursor deletes pre-existing cursor before syncing."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    # Pre-seed cursor
    existing_cursor = SyncCursor(
        source="github",
        scope="keshan-dev/headref",
        cursor_value="2026-09-01T00:00:00Z",
    )
    db_session.add(existing_cursor)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.get_repository.return_value = {
        "id": 1,
        "name": "headref",
        "full_name": "keshan-dev/headref",
        "default_branch": "main",
    }
    mock_client.list_pull_requests.return_value = []
    mock_client.list_commits.return_value = []

    # Run with reset_cursor
    run = sync_github(
        session=db_session,
        team_id=1,
        scope="keshan-dev/headref",
        reset_cursor=True,
        gh_client=mock_client,
    )
    assert run.status == "success"

    # New cursor has updated timestamp
    new_cursor = db_session.scalar(
        select(SyncCursor).where(
            SyncCursor.source == "github",
            SyncCursor.scope == "keshan-dev/headref",
        )
    )
    assert new_cursor is not None
    assert new_cursor.cursor_value != "2026-09-01T00:00:00Z"


def test_idempotency_running_twice_produces_identical_state(db_session: Session) -> None:
    """Running sync multiple times produces the exact same row counts (NFR-003)."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    mock_client = MagicMock()
    mock_client.get_repository.return_value = {
        "id": 1,
        "name": "headref",
        "full_name": "keshan-dev/headref",
        "default_branch": "main",
    }
    mock_client.list_pull_requests.return_value = [
        {
            "id": 101,
            "number": 1,
            "title": "AUTH-245: session seam",
            "body": "Fixes #1",
            "state": "open",
            "draft": False,
            "head": {"ref": "feature/AUTH-245-login"},
            "created_at": "2026-09-20T10:00:00Z",
            "updated_at": "2026-09-20T11:00:00Z",
            "user": {"id": 219891474, "login": "keshan-dev"},
        }
    ]
    mock_client.list_commits.return_value = [
        {
            "sha": "a1b2c3d4e5f67890123456789abcdef012345678",
            "commit": {
                "message": "feat: session seam AUTH-245",
                "author": {"date": "2026-09-20T09:00:00Z"},
            },
            "author": {"id": 219891474, "login": "keshan-dev"},
        }
    ]
    mock_client.list_reviews.return_value = []

    # Run 1
    run1 = sync_github(
        session=db_session, team_id=1, scope="keshan-dev/headref", gh_client=mock_client
    )
    assert run1.status == "success"

    pr_count_1 = len(db_session.scalars(select(PullRequest)).all())
    commit_count_1 = len(db_session.scalars(select(Commit)).all())

    # Run 2
    run2 = sync_github(
        session=db_session, team_id=1, scope="keshan-dev/headref", gh_client=mock_client
    )
    assert run2.status == "success"

    pr_count_2 = len(db_session.scalars(select(PullRequest)).all())
    commit_count_2 = len(db_session.scalars(select(Commit)).all())

    assert pr_count_1 == pr_count_2 == 1
    assert commit_count_1 == commit_count_2 == 1


def test_cli_main_entrypoint(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    """main() parses CLI flags, executes requested sync, and returns 0."""
    monkeypatch.setattr("app.sync.get_sync_session", lambda: db_session)

    mock_run = SyncRun(
        source="github",
        scope="keshan-dev/headref",
        status="success",
        items_fetched=10,
        items_written=10,
        items_skipped=0,
    )
    monkeypatch.setattr("app.sync.sync_github", lambda *args, **kwargs: mock_run)

    exit_code = main(["--source", "github", "--team", "1"])
    assert exit_code == 0


def test_open_pull_requests_record_their_checks_state() -> None:
    """BL-7 needs checks_state from GitHub; a read failure is unknown, never passing."""
    from app.integrations.errors import UpstreamError
    from app.sync import record_checks_state

    class PR:
        def __init__(self, number: int, state: str) -> None:
            self.number, self.state, self.checks_state = number, state, None

    prs = [PR(1, "open"), PR(2, "open"), PR(3, "merged"), PR(4, "open")]
    raw = [
        {"number": 1, "head": {"sha": "s1"}},
        {"number": 2, "head": {"sha": "s2"}},
        {"number": 3, "head": {"sha": "s3"}},
        {"number": 4, "head": {}},
    ]
    client = MagicMock()

    def checks(owner: str, repo: str, sha: str) -> str:
        if sha == "s2":
            raise UpstreamError("GitHub 502", status_code=502)
        return "failing"

    client.get_checks_state.side_effect = checks

    record_checks_state(client, "o", "r", prs, raw)

    assert [p.checks_state for p in prs] == ["failing", "unknown", None, "unknown"]
    called = [c.args[2] for c in client.get_checks_state.call_args_list]
    assert called == ["s1", "s2"]


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (["HRS", "AUTH"], 'project in ("AUTH", "HRS") ORDER BY key'),
        (["HRS", 'X" OR project is not EMPTY OR "', "lower"], 'project in ("HRS") ORDER BY key'),
        ([], ""),
    ],
)
def test_project_jql_is_bounded_and_quoted(keys, expected):
    from app.sync import project_jql

    assert project_jql(keys) == expected
