"""Workspace settings, invites, roles and archive (W9, DEC-021)."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import workspace_admin
from app.db import Base
from app.integrations.errors import IntegrationTimeoutError, NotFoundError
from app.integrations.github import lookup_github_user
from app.main import app
from app.models.workspace import WorkspaceInvite, WorkspaceMember
from app.web import auth
from app.web.settings_pages import settings_message
from app.workspace_admin import AdminError
from tests.conftest import log_in
from tests.test_workspaces import _account, _add_member, _workspace

NOW = datetime.now(UTC)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _github(users: dict[str, int]):
    """A lookup that knows the given login to id pairs, as GitHub would."""

    def lookup(login: str) -> dict:
        if login not in users:
            raise NotFoundError("no such user")
        return {"id": users[login], "login": login, "type": "User"}

    return lookup


def _owned(db: Session):
    owner_user, owner = _account(db, 101, "lead-a")
    workspace = _workspace(db, owner)
    return owner_user, owner, workspace


# ---------------------------------------------------------------------------
# GitHub lookup
# ---------------------------------------------------------------------------


def test_a_malformed_login_never_reaches_github():
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"called {request.url}")

    for login in ("../repos/x", "a b", "-lead", "x" * 40, ""):
        with pytest.raises(ValueError):
            lookup_github_user(login, transport=httpx.MockTransport(fail))


def test_lookup_returns_the_numeric_id_and_maps_404():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        if request.url.path == "/users/isiwara":
            return httpx.Response(200, json={"id": 202, "login": "Isiwara", "type": "User"})
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)

    assert lookup_github_user("isiwara", transport=transport) == {
        "id": 202,
        "login": "Isiwara",
        "type": "User",
    }
    with pytest.raises(NotFoundError):
        lookup_github_user("nobody", transport=transport)


# ---------------------------------------------------------------------------
# Inviting
# ---------------------------------------------------------------------------


def test_an_invite_is_stored_by_github_id_and_renewed_not_duplicated(db_session):
    _, owner, workspace = _owned(db_session)
    lookup = _github({"isiwara": 202})

    workspace_admin.invite(db_session, workspace, owner, "@isiwara", "viewer", lookup, NOW)
    workspace_admin.invite(db_session, workspace, owner, "isiwara", "manager", lookup, NOW)

    invites = db_session.scalars(select(WorkspaceInvite)).all()
    assert [(i.github_user_id, i.role) for i in invites] == [(202, "manager")]


@pytest.mark.parametrize(
    ("login", "role", "lookup", "code"),
    [
        ("isiwara", "owner", _github({"isiwara": 202}), "invalid_role"),
        ("nobody", "viewer", _github({}), "no_such_user"),
        ("lead-a", "viewer", _github({"lead-a": 101}), "already_member"),
    ],
)
def test_invites_that_are_refused(db_session, login, role, lookup, code):
    _, owner, workspace = _owned(db_session)

    with pytest.raises(AdminError) as exc:
        workspace_admin.invite(db_session, workspace, owner, login, role, lookup, NOW)

    assert exc.value.code == code
    assert db_session.scalar(select(WorkspaceInvite)) is None


def test_an_organization_cannot_be_invited(db_session):
    _, owner, workspace = _owned(db_session)

    def org(login: str) -> dict:
        return {"id": 9, "login": login, "type": "Organization"}

    with pytest.raises(AdminError, match="not_a_user"):
        workspace_admin.invite(db_session, workspace, owner, "acme", "viewer", org, NOW)


def test_github_unreachable_is_a_typed_failure_and_saves_nothing(db_session):
    _, owner, workspace = _owned(db_session)

    def down(login: str) -> dict:
        raise IntegrationTimeoutError("timed out")

    with pytest.raises(AdminError) as exc:
        workspace_admin.invite(db_session, workspace, owner, "isiwara", "viewer", down, NOW)

    assert (exc.value.code, exc.value.detail) == ("github_unavailable", "TIMEOUT")
    assert db_session.scalar(select(WorkspaceInvite)) is None


def test_the_settings_message_never_echoes_the_query_string():
    assert "TIMEOUT" in settings_message("github_unavailable", "TIMEOUT")
    assert "<script>" not in settings_message("github_unavailable", "<script>")
    assert settings_message("<script>", "") is None


# ---------------------------------------------------------------------------
# Accepting
# ---------------------------------------------------------------------------


def test_accepting_joins_with_the_invited_role(db_session):
    _, owner, workspace = _owned(db_session)
    _, invitee = _account(db_session, 202, "isiwara")
    made = workspace_admin.invite(
        db_session, workspace, owner, "isiwara", "manager", _github({"isiwara": 202}), NOW
    )

    assert workspace_admin.accept_invite(db_session, invitee, made.id) == workspace.id

    member = db_session.scalar(
        select(WorkspaceMember).where(WorkspaceMember.account_id == invitee.id)
    )
    assert (member.role, member.invited_by_account_id) == ("manager", owner.id)
    assert db_session.scalar(select(WorkspaceInvite)) is None


def test_a_login_taken_over_by_another_account_does_not_carry_the_invite(db_session):
    """Invite isiwara (id 202); a different GitHub account later signs in as isiwara."""
    _, owner, workspace = _owned(db_session)
    made = workspace_admin.invite(
        db_session, workspace, owner, "isiwara", "viewer", _github({"isiwara": 202}), NOW
    )
    _, impostor = _account(db_session, 303, "isiwara")

    assert workspace_admin.invites_for(db_session, impostor) == []
    with pytest.raises(AdminError, match="not_found"):
        workspace_admin.accept_invite(db_session, impostor, made.id)


def test_an_expired_or_archived_invite_cannot_be_accepted(db_session):
    _, owner, workspace = _owned(db_session)
    _, invitee = _account(db_session, 202, "isiwara")
    lookup = _github({"isiwara": 202})
    old = workspace_admin.invite(
        db_session, workspace, owner, "isiwara", "viewer", lookup, NOW - timedelta(days=15)
    )

    assert workspace_admin.invites_for(db_session, invitee, NOW) == []
    with pytest.raises(AdminError, match="not_found"):
        workspace_admin.accept_invite(db_session, invitee, old.id, NOW)

    fresh = workspace_admin.invite(db_session, workspace, owner, "isiwara", "viewer", lookup, NOW)
    workspace_admin.archive(db_session, workspace, workspace.name, owner.id)
    with pytest.raises(AdminError, match="not_found"):
        workspace_admin.accept_invite(db_session, invitee, fresh.id, NOW)


def test_declining_removes_the_invite(db_session):
    _, owner, workspace = _owned(db_session)
    _, invitee = _account(db_session, 202, "isiwara")
    made = workspace_admin.invite(
        db_session, workspace, owner, "isiwara", "viewer", _github({"isiwara": 202}), NOW
    )

    workspace_admin.decline_invite(db_session, invitee, made.id)

    assert db_session.scalar(select(WorkspaceInvite)) is None
    assert (
        db_session.scalar(select(WorkspaceMember).where(WorkspaceMember.account_id == invitee.id))
        is None
    )


# ---------------------------------------------------------------------------
# Members and archive
# ---------------------------------------------------------------------------


def test_the_owner_cannot_be_demoted_or_removed(db_session):
    _, owner, workspace = _owned(db_session)
    owner_member = db_session.scalar(select(WorkspaceMember))

    for action in (
        lambda: workspace_admin.change_role(db_session, workspace.id, owner_member.id, "viewer"),
        lambda: workspace_admin.remove_member(db_session, workspace.id, owner_member.id),
    ):
        with pytest.raises(AdminError, match="owner_fixed"):
            action()


def test_a_member_id_from_another_workspace_is_not_found(db_session):
    _, owner, workspace = _owned(db_session)
    _, other_owner = _account(db_session, 404, "lead-b")
    other = _workspace(db_session, other_owner, "Other")
    _, viewer = _account(db_session, 202, "isiwara")
    _add_member(db_session, other, viewer, "viewer")
    foreign = db_session.scalar(
        select(WorkspaceMember).where(WorkspaceMember.account_id == viewer.id)
    )

    with pytest.raises(AdminError, match="not_found"):
        workspace_admin.remove_member(db_session, workspace.id, foreign.id)
    assert db_session.get(WorkspaceMember, foreign.id) is not None


def test_archive_needs_the_exact_name(db_session):
    _, owner, workspace = _owned(db_session)

    with pytest.raises(AdminError, match="confirm_mismatch"):
        workspace_admin.archive(db_session, workspace, "payments", owner.id)
    assert workspace.archived_at is None

    workspace_admin.archive(db_session, workspace, " Payments ", owner.id)
    assert workspace.archived_at is not None


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


def test_a_manager_and_a_viewer_cannot_change_settings(client, db_session):
    _, owner, workspace = _owned(db_session)
    for gh_id, login, role in ((202, "mgr", "manager"), (303, "view", "viewer")):
        user, account = _account(db_session, gh_id, login)
        _add_member(db_session, workspace, account, role)
        log_in(client, user.id)

        page = client.get(f"/w/{workspace.id}/settings")
        assert page.status_code == 200
        assert "lead-a" in page.text
        assert "Invite by GitHub username" not in page.text
        for path, data in (
            ("settings/invites", {"login": "x", "role": "viewer"}),
            ("settings/details", {"name": "Renamed"}),
            ("settings/archive", {"confirm_name": workspace.name}),
        ):
            response = client.post(f"/w/{workspace.id}/{path}", data=data, follow_redirects=False)
            assert response.status_code == 403
    db_session.refresh(workspace)
    assert (workspace.name, workspace.archived_at) == ("Payments", None)


def test_invite_accept_and_open_end_to_end(client, db_session, monkeypatch):
    owner_user, _, workspace = _owned(db_session)
    invitee_user, _ = _account(db_session, 202, "isiwara")
    monkeypatch.setattr(workspace_admin, "lookup_github_user", _github({"isiwara": 202}))

    log_in(client, owner_user.id)
    sent = client.post(
        f"/w/{workspace.id}/settings/invites",
        data={"login": "isiwara", "role": "viewer"},
        follow_redirects=False,
    )
    assert sent.headers["location"].endswith("settings=invited")
    assert "isiwara" in client.get(f"/w/{workspace.id}/settings").text

    client.cookies.clear()
    log_in(client, invitee_user.id)
    assert client.get(f"/w/{workspace.id}").status_code == 404
    home = client.get("/workspaces").text
    assert "Invitations (1)" in home
    invite_id = db_session.scalar(select(WorkspaceInvite.id))

    accepted = client.post(f"/invites/{invite_id}/accept", follow_redirects=False)

    assert accepted.headers["location"] == f"/w/{workspace.id}"
    assert client.get(f"/w/{workspace.id}/settings").status_code == 200


def test_someone_elses_invite_id_is_refused(client, db_session):
    _, owner, workspace = _owned(db_session)
    workspace_admin.invite(
        db_session, workspace, owner, "isiwara", "viewer", _github({"isiwara": 202}), NOW
    )
    stranger_user, _ = _account(db_session, 303, "stranger")
    log_in(client, stranger_user.id)
    invite_id = db_session.scalar(select(WorkspaceInvite.id))

    response = client.post(f"/invites/{invite_id}/accept", follow_redirects=False)

    assert response.headers["location"] == "/workspaces?invite=not_found"
    assert client.get(f"/w/{workspace.id}").status_code == 404


def test_archiving_closes_the_workspace(client, db_session):
    owner_user, _, workspace = _owned(db_session)
    log_in(client, owner_user.id)

    response = client.post(
        f"/w/{workspace.id}/settings/archive",
        data={"confirm_name": "Payments"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/workspaces?invite=archived"
    assert client.get(f"/w/{workspace.id}").status_code == 404
    home = client.get("/workspaces?invite=archived").text
    assert "Workspace archived" in home
    assert "No workspaces yet" in home
