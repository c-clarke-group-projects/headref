"""Unit tests for work item link builder (P2-007, Issue #18)."""

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.integrations.link_builder import (
    extract_ticket_keys,
    link_commit,
    link_jira_remote_links,
    link_pull_request,
    upsert_work_item_link,
)
from app.models.canonical import Organization, Project, Repository
from app.models.work import Commit, PullRequest, WorkItem, WorkItemLink


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_extract_ticket_keys_word_boundaries_and_case() -> None:
    """extract_ticket_keys matches configured project keys and obeys word boundaries."""
    project_keys = ["AUTH", "PAY"]

    # The word boundary stops AUTH-2450 being truncated to AUTH-245. Both are real
    # references in this text, so both are extracted, and a lowercase key is matched.
    text = "Work on AUTH-245 and not AUTH-2450. Also fixes pay-101."
    assert extract_ticket_keys(text, project_keys) == {"AUTH-245", "AUTH-2450", "PAY-101"}

    # A project key inside a longer token is not a reference.
    assert extract_ticket_keys("XAUTH-245 and AUTHX-245", project_keys) == set()

    # None or empty text
    assert extract_ticket_keys(None, project_keys) == set()
    assert extract_ticket_keys("", project_keys) == set()
    assert extract_ticket_keys("No tickets here", project_keys) == set()


def test_link_branch_name_creates_high_link(db_session: Session) -> None:
    """A ticket ID in a branch name creates a HIGH confidence link."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    proj = Project(organization_id=org.id, key="AUTH", name="Auth")
    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add_all([proj, repo])
    db_session.flush()

    now = datetime.now(UTC)
    wi = WorkItem(
        project_id=proj.id,
        external_id="AUTH-245",
        title="Session seam",
        status="in_progress",
        raw_status="In Progress",
        source_url="url1",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(wi)
    db_session.flush()

    pr = PullRequest(
        repository_id=repo.id,
        number=1,
        title="Session validation seam",
        branch_name="feature/AUTH-245-login",
        state="open",
        created_at_source=now,
        source_url="https://github.com/keshan-dev/headref/pull/1",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(pr)
    db_session.flush()

    links = link_pull_request(
        db_session,
        pr=pr,
        work_items_by_key={"AUTH-245": wi},
        project_keys=["AUTH"],
    )
    assert len(links) >= 1

    pr_links = [lnk for lnk in links if lnk.target_type == "pull_request"]
    assert len(pr_links) == 1
    assert pr_links[0].confidence == "HIGH"
    assert pr_links[0].link_method == "branch_name"
    assert pr_links[0].target_id == str(pr.id)


def test_link_jira_remote_link_creates_high_link(db_session: Session) -> None:
    """A Jira remote link matching a PR URL creates a HIGH confidence link."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    proj = Project(organization_id=org.id, key="AUTH", name="Auth")
    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add_all([proj, repo])
    db_session.flush()

    now = datetime.now(UTC)
    wi = WorkItem(
        project_id=proj.id,
        external_id="AUTH-245",
        title="Session seam",
        status="in_progress",
        raw_status="In Progress",
        source_url="url1",
        source_updated_at=now,
        retrieved_at=now,
    )
    pr = PullRequest(
        repository_id=repo.id,
        number=1,
        title="Seam PR",
        branch_name="main",
        state="open",
        created_at_source=now,
        source_url="https://github.com/keshan-dev/headref/pull/1",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add_all([wi, pr])
    db_session.flush()

    remote_links = [
        {
            "object": {
                "url": "https://github.com/keshan-dev/headref/pull/1",
                "title": "GitHub PR #1",
            }
        }
    ]

    links = link_jira_remote_links(
        session=db_session,
        work_item=wi,
        remote_links=remote_links,
        prs_by_url={pr.source_url: pr},
    )
    assert len(links) == 1
    assert links[0].target_type == "pull_request"
    assert links[0].target_id == str(pr.id)
    assert links[0].link_method == "jira_remote_link"
    assert links[0].confidence == "HIGH"


def test_link_pr_title_and_body_creates_medium_link(db_session: Session) -> None:
    """Ticket IDs in PR titles or bodies create MEDIUM confidence links."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    proj = Project(organization_id=org.id, key="AUTH", name="Auth")
    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add_all([proj, repo])
    db_session.flush()

    now = datetime.now(UTC)
    wi = WorkItem(
        project_id=proj.id,
        external_id="AUTH-245",
        title="Session seam",
        status="in_progress",
        raw_status="In Progress",
        source_url="url1",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(wi)
    db_session.flush()

    # PR with ticket only in title (generic branch)
    pr = PullRequest(
        repository_id=repo.id,
        number=1,
        title="AUTH-245: add session auth seam",
        body_excerpt="Refactoring auth package",
        branch_name="patch-1",
        state="open",
        created_at_source=now,
        source_url="https://github.com/keshan-dev/headref/pull/1",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(pr)
    db_session.flush()

    links = link_pull_request(
        db_session,
        pr=pr,
        work_items_by_key={"AUTH-245": wi},
        project_keys=["AUTH"],
    )
    assert len(links) == 1
    assert links[0].confidence == "MEDIUM"
    assert links[0].link_method == "pr_title"


def test_link_pr_body_alone_creates_medium_link(db_session: Session) -> None:
    """A ticket ID only in the PR body creates a MEDIUM pr_body link (P6-001)."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    proj = Project(organization_id=org.id, key="AUTH", name="Auth")
    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add_all([proj, repo])
    db_session.flush()

    now = datetime.now(UTC)
    wi = WorkItem(
        project_id=proj.id,
        external_id="AUTH-245",
        title="Session seam",
        status="in_progress",
        raw_status="In Progress",
        source_url="url1",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(wi)
    db_session.flush()

    # Generic branch and title: the body is the only place the key appears.
    pr = PullRequest(
        repository_id=repo.id,
        number=2,
        title="Refactor session handling",
        body_excerpt="Part of AUTH-245. Moves the seam behind an interface.",
        branch_name="patch-2",
        state="open",
        created_at_source=now,
        source_url="https://github.com/keshan-dev/headref/pull/2",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(pr)
    db_session.flush()

    links = link_pull_request(
        db_session,
        pr=pr,
        work_items_by_key={"AUTH-245": wi},
        project_keys=["AUTH"],
    )
    assert len(links) == 1
    assert links[0].confidence == "MEDIUM"
    assert links[0].link_method == "pr_body"
    assert links[0].target_type == "pull_request"
    assert links[0].target_id == str(pr.id)


def test_link_commit_message_creates_medium_link(db_session: Session) -> None:
    """Ticket ID in a commit message creates a MEDIUM confidence link."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    proj = Project(organization_id=org.id, key="AUTH", name="Auth")
    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add_all([proj, repo])
    db_session.flush()

    now = datetime.now(UTC)
    wi = WorkItem(
        project_id=proj.id,
        external_id="AUTH-245",
        title="Session seam",
        status="in_progress",
        raw_status="In Progress",
        source_url="url1",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(wi)
    db_session.flush()

    commit = Commit(
        repository_id=repo.id,
        sha="a1b2c3d4e5f67890123456789abcdef012345678",
        message_excerpt="feat: implement AUTH-245 auth seam",
        branch_name="main",
        committed_at=now,
        source_url="url_commit",
        retrieved_at=now,
    )
    db_session.add(commit)
    db_session.flush()

    links = link_commit(
        db_session,
        commit=commit,
        work_items_by_key={"AUTH-245": wi},
        project_keys=["AUTH"],
    )
    assert len(links) == 1
    assert links[0].target_type == "commit"
    assert links[0].target_id == str(commit.id)
    assert links[0].confidence == "MEDIUM"
    assert links[0].link_method == "commit_message"


def test_no_ticket_reference_creates_no_link(db_session: Session) -> None:
    """A pull request with no ticket reference anywhere produces no link."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    proj = Project(organization_id=org.id, key="AUTH", name="Auth")
    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add_all([proj, repo])
    db_session.flush()

    now = datetime.now(UTC)
    wi = WorkItem(
        project_id=proj.id,
        external_id="AUTH-245",
        title="Session seam",
        status="in_progress",
        raw_status="In Progress",
        source_url="url1",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(wi)
    db_session.flush()

    pr = PullRequest(
        repository_id=repo.id,
        number=3,
        title="fix(deps): bump dependency versions for security patch",
        body_excerpt="Routine security bump. No Jira ticket associated.",
        branch_name="chore/dep-bump",
        state="closed",
        created_at_source=now,
        source_url="https://github.com/keshan-dev/headref/pull/3",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(pr)
    db_session.flush()

    links = link_pull_request(
        db_session,
        pr=pr,
        work_items_by_key={"AUTH-245": wi},
        project_keys=["AUTH"],
    )
    assert len(links) == 0

    all_links = db_session.scalars(select(WorkItemLink)).all()
    assert len(all_links) == 0


def test_duplicate_link_preserves_highest_confidence(db_session: Session) -> None:
    """A duplicate (work_item, target_type, target_id) keeps the highest confidence."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    proj = Project(organization_id=org.id, key="AUTH", name="Auth")
    db_session.add(proj)
    db_session.flush()

    now = datetime.now(UTC)
    wi = WorkItem(
        project_id=proj.id,
        external_id="AUTH-245",
        title="Session seam",
        status="in_progress",
        raw_status="In Progress",
        source_url="url1",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add(wi)
    db_session.flush()

    # First insert: MEDIUM from pr_title
    l1 = upsert_work_item_link(
        session=db_session,
        work_item_id=wi.id,
        target_type="pull_request",
        target_id="101",
        link_method="pr_title",
        confidence="MEDIUM",
    )
    assert l1.confidence == "MEDIUM"
    assert l1.link_method == "pr_title"
    assert len(db_session.scalars(select(WorkItemLink)).all()) == 1

    # Second insert: upgrade to HIGH from branch_name
    l2 = upsert_work_item_link(
        session=db_session,
        work_item_id=wi.id,
        target_type="pull_request",
        target_id="101",
        link_method="branch_name",
        confidence="HIGH",
    )
    assert l2.id == l1.id
    assert l2.confidence == "HIGH"
    assert l2.link_method == "branch_name"
    assert len(db_session.scalars(select(WorkItemLink)).all()) == 1

    # Third insert: try lower confidence MEDIUM from pr_body -> stays HIGH
    l3 = upsert_work_item_link(
        session=db_session,
        work_item_id=wi.id,
        target_type="pull_request",
        target_id="101",
        link_method="pr_body",
        confidence="MEDIUM",
    )
    assert l3.id == l1.id
    assert l3.confidence == "HIGH"
    assert l3.link_method == "branch_name"
    assert len(db_session.scalars(select(WorkItemLink)).all()) == 1


def test_no_link_created_from_timing_or_authorship(db_session: Session) -> None:
    """No link is ever created from timing or authorship alone."""
    org = Organization(name="Keshan Org")
    db_session.add(org)
    db_session.flush()

    proj = Project(organization_id=org.id, key="AUTH", name="Auth")
    repo = Repository(organization_id=org.id, full_name="keshan-dev/headref")
    db_session.add_all([proj, repo])
    db_session.flush()

    # Same timestamp, same author, but totally unrelated branch and title
    now = datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)
    wi = WorkItem(
        project_id=proj.id,
        external_id="AUTH-245",
        title="Session seam",
        status="in_progress",
        raw_status="In Progress",
        assignee_app_user_id=1,
        source_url="url1",
        source_updated_at=now,
        retrieved_at=now,
    )
    pr = PullRequest(
        repository_id=repo.id,
        number=99,
        title="Unrelated documentation cleanup",
        branch_name="chore/docs-update",
        author_app_user_id=1,
        state="open",
        created_at_source=now,
        source_url="url2",
        source_updated_at=now,
        retrieved_at=now,
    )
    db_session.add_all([wi, pr])
    db_session.flush()

    links = link_pull_request(
        db_session,
        pr=pr,
        work_items_by_key={"AUTH-245": wi},
        project_keys=["AUTH"],
    )
    assert len(links) == 0
    assert len(db_session.scalars(select(WorkItemLink)).all()) == 0
