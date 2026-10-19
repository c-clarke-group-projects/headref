"""People: discovery, confirm, ignore, bot, merge, split, pages (W6, DEC-024)."""

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import people
from app.db import Base
from app.integrations.identity_resolver import resolve_actor
from app.main import app
from app.models.canonical import AppUser, Organization, Project, Repository
from app.models.identity import IdentityLink
from app.models.work import PullRequest, WorkItem
from app.web import auth
from app.workspace_sync import ensure_organization
from tests.conftest import log_in
from tests.test_workspaces import _account, _add_member, _workspace

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _org(db: Session, name: str = "W") -> int:
    org = Organization(name=name)
    db.add(org)
    db.flush()
    return org.id


def _found(db: Session, org_id: int, integration: str, external_id: str, handle: str) -> int:
    """A person as a workspace sync discovers them."""
    person_id = resolve_actor(
        db, integration, external_id, handle, auto_create=True, organization_id=org_id
    )
    db.commit()
    return person_id


def _pr(db: Session, org_id: int, author_id: int, number: int = 1) -> PullRequest:
    repo = db.scalar(select(Repository).where(Repository.organization_id == org_id))
    if repo is None:
        repo = Repository(organization_id=org_id, full_name=f"o/r{org_id}")
        db.add(repo)
        db.flush()
    pr = PullRequest(
        repository_id=repo.id,
        number=number,
        title="t",
        state="open",
        branch_name="feature/x",
        created_at_source=NOW,
        source_url=f"u{number}",
        source_updated_at=NOW,
        retrieved_at=NOW,
        author_app_user_id=author_id,
    )
    db.add(pr)
    db.commit()
    return pr


def _item(db: Session, org_id: int, assignee_id: int, key: str = "HRS-1") -> WorkItem:
    project = db.scalar(select(Project).where(Project.organization_id == org_id))
    if project is None:
        project = Project(organization_id=org_id, key="HRS", name="sandbox")
        db.add(project)
        db.flush()
    item = WorkItem(
        project_id=project.id,
        external_id=key,
        title="t",
        status="in_progress",
        raw_status="In Progress",
        source_url=key,
        source_updated_at=NOW,
        retrieved_at=NOW,
        assignee_app_user_id=assignee_id,
    )
    db.add(item)
    db.commit()
    return item


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_a_workspace_sync_suggests_a_person_per_account(db_session):
    org_id = _org(db_session)

    github_id = _found(db_session, org_id, "github", "101", "keshan-dev")
    jira_id = _found(db_session, org_id, "jira", "acc-1", "Keshan")

    assert github_id != jira_id
    statuses = {u.id: u.status for u in db_session.scalars(select(AppUser)).all()}
    assert statuses == {github_id: "suggested", jira_id: "suggested"}


def test_a_bot_account_is_marked_as_a_bot(db_session):
    org_id = _org(db_session)

    bot_id = _found(db_session, org_id, "github", "49699333", "dependabot[bot]")

    assert db_session.get(AppUser, bot_id).status == "bot"


def test_the_legacy_path_still_confirms_at_once(db_session):
    _org(db_session)

    person_id = resolve_actor(db_session, "github", "101", "keshan-dev", auto_create=True)

    assert db_session.get(AppUser, person_id).status == "confirmed"


def test_list_people_groups_by_status_with_accounts_and_counts(db_session):
    org_id = _org(db_session)
    github_id = _found(db_session, org_id, "github", "101", "keshan-dev")
    _pr(db_session, org_id, github_id)

    grouped = people.list_people(db_session, org_id)

    (person,) = grouped["suggested"]
    assert (person.display_name, person.pull_requests) == ("keshan-dev", 1)
    assert [(a.integration, a.handle) for a in person.accounts] == [("github", "keshan-dev")]


# ---------------------------------------------------------------------------
# Merge and split
# ---------------------------------------------------------------------------


def test_merging_a_jira_person_into_a_github_person_moves_accounts_and_work(db_session):
    org_id = _org(db_session)
    github_id = _found(db_session, org_id, "github", "101", "keshan-dev")
    jira_id = _found(db_session, org_id, "jira", "acc-1", "Keshan")
    item = _item(db_session, org_id, jira_id)

    people.merge(db_session, org_id, source_id=jira_id, target_id=github_id)

    db_session.refresh(item)
    assert item.assignee_app_user_id == github_id
    assert db_session.get(AppUser, jira_id) is None
    target = db_session.get(AppUser, github_id)
    assert target.status == "confirmed"
    links = db_session.scalars(select(IdentityLink).where(IdentityLink.app_user_id == github_id))
    assert sorted(link.integration for link in links) == ["github", "jira"]


def test_a_merge_across_workspaces_changes_nothing(db_session):
    mine, theirs = _org(db_session, "mine"), _org(db_session, "theirs")
    my_person = _found(db_session, mine, "github", "101", "keshan-dev")
    their_person = _found(db_session, theirs, "jira", "acc-9", "Someone")

    with pytest.raises(people.PeopleError) as exc:
        people.merge(db_session, mine, source_id=their_person, target_id=my_person)

    assert exc.value.code == "not_found"
    assert db_session.get(AppUser, their_person) is not None


def test_a_person_cannot_be_merged_into_themselves(db_session):
    org_id = _org(db_session)
    person_id = _found(db_session, org_id, "github", "101", "keshan-dev")

    with pytest.raises(people.PeopleError, match="same_person"):
        people.merge(db_session, org_id, person_id, person_id)


def test_split_moves_the_account_and_its_work_back_out(db_session):
    org_id = _org(db_session)
    github_id = _found(db_session, org_id, "github", "101", "keshan-dev")
    jira_id = _found(db_session, org_id, "jira", "acc-1", "Keshan")
    item = _item(db_session, org_id, jira_id)
    pr = _pr(db_session, org_id, github_id)
    people.merge(db_session, org_id, jira_id, github_id)
    jira_link = db_session.scalar(select(IdentityLink).where(IdentityLink.integration == "jira"))

    new_id = people.split(db_session, org_id, github_id, jira_link.id)

    db_session.refresh(item)
    db_session.refresh(pr)
    assert item.assignee_app_user_id == new_id
    assert pr.author_app_user_id == github_id
    assert db_session.get(AppUser, new_id).status == "suggested"


def test_split_refuses_when_activity_could_not_be_told_apart(db_session):
    org_id = _org(db_session)
    first = _found(db_session, org_id, "github", "101", "keshan-dev")
    second = _found(db_session, org_id, "github", "202", "keshan-alt")
    people.merge(db_session, org_id, second, first)
    link = db_session.scalar(select(IdentityLink).where(IdentityLink.external_id == "202"))

    with pytest.raises(people.PeopleError, match="ambiguous_split"):
        people.split(db_session, org_id, first, link.id)


def test_split_refuses_the_only_account(db_session):
    org_id = _org(db_session)
    person_id = _found(db_session, org_id, "github", "101", "keshan-dev")
    link = db_session.scalar(select(IdentityLink))

    with pytest.raises(people.PeopleError, match="only_account"):
        people.split(db_session, org_id, person_id, link.id)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[auth.get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _owned_workspace(db: Session, client: TestClient):
    user, account = _account(db, 101, "lead-a")
    workspace = _workspace(db, account, "Payments")
    org_id = ensure_organization(db, workspace)
    db.commit()
    log_in(client, user.id)
    return workspace, org_id


def test_a_manager_confirms_a_person(client, db_session):
    workspace, org_id = _owned_workspace(db_session, client)
    person_id = _found(db_session, org_id, "github", "555", "dev-x")

    response = client.post(
        f"/w/{workspace.id}/people/{person_id}/status",
        data={"status": "confirmed"},
        follow_redirects=False,
    )

    assert "people=confirmed" in response.headers["location"]
    assert db_session.get(AppUser, person_id).status == "confirmed"


def test_a_person_of_another_workspace_cannot_be_touched(client, db_session):
    workspace, _ = _owned_workspace(db_session, client)
    their_person = _found(db_session, _org(db_session, "theirs"), "github", "777", "victim")

    response = client.post(
        f"/w/{workspace.id}/people/{their_person}/status",
        data={"status": "ignored"},
        follow_redirects=False,
    )

    assert "people=not_found" in response.headers["location"]
    assert db_session.get(AppUser, their_person).status == "suggested"


def test_a_viewer_sees_people_but_cannot_change_them(client, db_session):
    _, owner = _account(db_session, 101, "lead-a")
    workspace = _workspace(db_session, owner, "Payments")
    org_id = ensure_organization(db_session, workspace)
    db_session.commit()
    viewer_user, viewer = _account(db_session, 303, "viewer-c")
    _add_member(db_session, workspace, viewer, "viewer")
    person_id = _found(db_session, org_id, "github", "555", "dev-x")
    log_in(client, viewer_user.id)

    page = client.get(f"/w/{workspace.id}/people")
    post = client.post(
        f"/w/{workspace.id}/people/{person_id}/status",
        data={"status": "confirmed"},
        follow_redirects=False,
    )

    assert page.status_code == 200 and "dev-x" in page.text
    assert "Confirm</button>" not in page.text
    assert post.status_code == 403


def test_merge_through_the_page(client, db_session):
    workspace, org_id = _owned_workspace(db_session, client)
    github_id = _found(db_session, org_id, "github", "555", "dev-x")
    jira_id = _found(db_session, org_id, "jira", "acc-5", "Dev X")

    response = client.post(
        f"/w/{workspace.id}/people/merge",
        data={"source": str(jira_id), "target": str(github_id)},
        follow_redirects=False,
    )

    assert "people=merged" in response.headers["location"]
    assert db_session.get(AppUser, jira_id) is None


def test_a_hostile_account_handle_is_escaped(client, db_session):
    workspace, org_id = _owned_workspace(db_session, client)
    _found(db_session, org_id, "jira", "acc-6", "<img src=x onerror=alert(1)>")

    html = client.get(f"/w/{workspace.id}/people").text

    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_the_workspace_page_lists_confirmed_people_and_the_waiting_count(client, db_session):
    workspace, org_id = _owned_workspace(db_session, client)
    confirmed = _found(db_session, org_id, "github", "555", "zara")
    people.set_status(db_session, org_id, confirmed, "confirmed")
    _found(db_session, org_id, "jira", "acc-7", "Waiting Person")

    html = client.get(f"/w/{workspace.id}").text

    assert "zara" in html
    assert "1 person is waiting for confirmation." in html
    assert "Waiting Person" not in html


def test_people_shows_accounts_nobody_holds_and_hides_linked_ones(client, db_session):
    from app.integrations.identity_resolver import record_unmatched

    workspace, org_id = _owned_workspace(db_session, client)
    now = datetime.now(UTC)
    record_unmatched(
        db_session, "github", "commit-author:abc", "Outside (commit author)", now, org_id
    )
    # Seen as unmatched once, later linked to a person: no longer excluded.
    record_unmatched(db_session, "jira", "acc-1", "Keshan", now, org_id)
    _found(db_session, org_id, "jira", "acc-1", "Keshan")
    db_session.commit()

    html = client.get(f"/w/{workspace.id}/people").text

    assert "Not matched to anyone (1)" in html
    assert "Outside (commit author)" in html
