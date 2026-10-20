"""Isolation between workspaces (W10, DEC-021, EV-10).

2 workspaces sync the same repository, so the same GitHub author exists once in each.
Nothing made in 1 workspace may be read, changed or even confirmed to exist from the
other: every such request answers 404 or "not in this workspace".
"""

import re
from collections.abc import Generator, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import people, workspace_admin
from app.db import Base
from app.main import app
from app.models.canonical import AppUser
from app.models.feed import WorkspaceEvent
from app.models.workspace import Account, Workspace, WorkspaceInvite
from app.web import auth
from app.workspace_analysis import MemberAnalysis, analyze_workspace
from app.workspace_feed import record_changes
from app.workspace_sync import sync_workspace
from tests.conftest import log_in
from tests.test_workspace_sync import FakeApp, _workspace_with_repo, github  # noqa: F401

NOW = datetime.now(UTC)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[auth.get_db] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _owner_user_id(db: Session, workspace: Workspace) -> int:
    account = db.get(Account, workspace.created_by_account_id)
    return account.app_user_id


def _person(db: Session, workspace: Workspace) -> int:
    """The synced GitHub author (id 101) as a person of this workspace."""
    db.refresh(workspace)
    return db.scalar(
        select(AppUser.id).where(
            AppUser.organization_id == workspace.organization_id,
            AppUser.display_name == "lead-a",
        )
    )


@pytest.fixture
def two(db_session: Session, github) -> dict[str, Any]:  # noqa: F811
    """Workspaces A and B, both synced from the same repository; A's author confirmed."""
    a = _workspace_with_repo(db_session, "Payments", 101)
    b = _workspace_with_repo(db_session, "Audit", 202)
    sync_workspace(db_session, a.id, app_client=FakeApp())
    sync_workspace(db_session, b.id, app_client=FakeApp())
    person_a, person_b = _person(db_session, a), _person(db_session, b)
    people.set_status(db_session, a.organization_id, person_a, "confirmed")
    db_session.add(
        WorkspaceEvent(
            workspace_id=a.id,
            person_id=person_a,
            kind="item_moved",
            text="SECRET-A moved from To Do to Done",
            occurred_at=NOW,
        )
    )
    owner_a = db_session.get(Account, a.created_by_account_id)
    invite = WorkspaceInvite(
        workspace_id=a.id,
        github_user_id=303,
        github_login="someone",
        role="viewer",
        invited_by_account_id=owner_a.id,
        created_at=NOW,
        expires_at=NOW.replace(year=NOW.year + 1),
    )
    db_session.add(invite)
    db_session.commit()
    return {"a": a, "b": b, "person_a": person_a, "person_b": person_b, "invite": invite.id}


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------


def test_the_same_author_is_a_separate_person_in_each_workspace(db_session, two):
    assert two["person_a"] != two["person_b"]
    assert db_session.get(AppUser, two["person_b"]).status == "suggested"


@pytest.mark.anyio
async def test_analysis_covers_only_its_own_confirmed_people(db_session, two, monkeypatch):
    """The detection rules need PostgreSQL's aware timestamps, so analysis per person is
    stubbed here; what is checked is who gets analysed, and with which workspace id."""
    seen: list[tuple[int, int]] = []

    async def fake_member(db, workspace_id, person, uses_jira, health, now):
        seen.append((workspace_id, person.id))
        return MemberAnalysis(
            person_id=person.id, display_name=person.display_name, current_work=None
        )

    monkeypatch.setattr("app.workspace_analysis.analyze_member", fake_member)

    await analyze_workspace(db_session, two["a"])
    await analyze_workspace(db_session, two["b"])

    assert seen == [(two["a"].id, two["person_a"])]


def test_a_person_id_from_another_workspace_is_not_found(db_session, two):
    for action in (
        lambda: people.set_status(db_session, two["b"].organization_id, two["person_a"], "ignored"),
        lambda: people.merge(
            db_session, two["b"].organization_id, two["person_a"], two["person_b"]
        ),
    ):
        with pytest.raises(people.PeopleError, match="not_found"):
            action()
    assert db_session.get(AppUser, two["person_a"]).status == "confirmed"


def test_the_feed_snapshot_reads_only_its_own_workspace(db_session, two):
    record_changes(db_session, two["b"].id, [], now=NOW)

    from app.models.feed import FindingSnapshot

    subjects = db_session.scalars(
        select(FindingSnapshot.subject).where(FindingSnapshot.workspace_id == two["b"].id)
    ).all()
    assert subjects == ["work_state"]  # nobody is confirmed in B


def test_an_invite_cannot_be_withdrawn_through_another_workspace(db_session, two):
    with pytest.raises(workspace_admin.AdminError, match="not_found"):
        workspace_admin.revoke_invite(db_session, two["b"].id, two["invite"])
    assert db_session.get(WorkspaceInvite, two["invite"]) is not None


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_bs_owner_sees_nothing_of_a(client, db_session, two):
    log_in(client, _owner_user_id(db_session, two["b"]))
    b_id, person_a = two["b"].id, two["person_a"]

    page = client.get(f"/w/{b_id}?member={person_a}").text
    assert "SECRET-A" not in page
    assert "Payments" not in page
    assert client.get(f"/w/{b_id}/members/{person_a}/summary").status_code == 404

    moved = client.post(
        f"/w/{b_id}/people/{person_a}/status", data={"status": "ignored"}, follow_redirects=False
    )
    assert moved.headers["location"].endswith("people=not_found")
    revoked = client.post(
        f"/w/{b_id}/settings/invites/{two['invite']}/revoke", follow_redirects=False
    )
    assert revoked.headers["location"].endswith("settings=not_found")
    db_session.expire_all()
    assert db_session.get(AppUser, person_a).status == "confirmed"
    assert db_session.get(WorkspaceInvite, two["invite"]) is not None


def _routes(application: FastAPI) -> Iterator[tuple[str, set[str]]]:
    """(path, methods) of every route, including those inside included routers."""
    for route in application.routes:
        candidates = (
            [route]
            if isinstance(route, APIRoute)
            else getattr(route, "effective_candidates", list)()
        )
        for candidate in candidates:
            if hasattr(candidate, "methods") and candidate.methods:
                yield candidate.path, set(candidate.methods)


def test_every_workspace_route_is_404_for_a_non_member(client, db_session, two):
    """Each route under a workspace id, with every method, called by B's owner on A."""
    log_in(client, _owner_user_id(db_session, two["b"]))
    calls = []
    for path, methods in _routes(app):
        if "{workspace_id}" not in path:
            continue
        url = re.sub(r"\{[a-z_]+\}", "1", path.replace("{workspace_id}", str(two["a"].id)))
        for method in methods - {"HEAD"}:
            calls.append((method, url, client.request(method, url, follow_redirects=False)))

    assert len(calls) >= 20, "the route scan saw too few workspace routes"
    leaks = [(m, u, r.status_code) for m, u, r in calls if r.status_code != 404]
    assert leaks == []

    # The 404s come from the membership check, not from paths that match nothing.
    log_in(client, _owner_user_id(db_session, two["a"]))
    assert client.get(f"/w/{two['a'].id}/settings").status_code == 200
    assert client.get(f"/api/workspaces/{two['a'].id}/sync/status").status_code == 200
