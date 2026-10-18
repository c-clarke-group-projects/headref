"""Workspace access, isolation and the workspace API (W1, DEC-021)."""

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import app
from app.models.canonical import AppUser, Organization
from app.models.workspace import Account, Workspace, WorkspaceMember
from app.web import auth
from app.web.workspaces import (
    ManagerAccess,
    OwnerAccess,
    WorkspaceContext,
    upsert_account,
)
from tests.conftest import iter_api_routes, log_in


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _override_db(target: FastAPI, session: Session) -> None:
    def override() -> Generator[Session, None, None]:
        yield session

    target.dependency_overrides[auth.get_db] = override


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    _override_db(app, db_session)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _account(db: Session, gh_id: int, login: str) -> tuple[AppUser, Account]:
    """An app_user and its account, as the GitHub callback leaves them."""
    org = db.scalar(select(Organization)) or Organization(name="Org")
    db.add(org)
    db.flush()
    user = AppUser(organization_id=org.id, display_name=login, is_active=True)
    db.add(user)
    db.flush()
    account = upsert_account(db, {"id": gh_id, "login": login, "name": login}, user.id)
    db.commit()
    return user, account


def _workspace(db: Session, owner: Account, name: str = "Payments") -> Workspace:
    workspace = Workspace(name=name, created_by_account_id=owner.id)
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, account_id=owner.id, role="owner"))
    db.commit()
    return workspace


def _add_member(db: Session, workspace: Workspace, account: Account, role: str) -> None:
    db.add(WorkspaceMember(workspace_id=workspace.id, account_id=account.id, role=role))
    db.commit()


# ---------------------------------------------------------------------------
# Create and list
# ---------------------------------------------------------------------------


def test_creating_a_workspace_makes_the_caller_its_owner(client: TestClient, db_session):
    user, account = _account(db_session, 101, "lead-a")
    log_in(client, user.id)

    response = client.post(
        "/api/workspaces", json={"name": "  Payments  ", "description": " Card flows "}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Payments"
    assert body["description"] == "Card flows"
    assert body["role"] == "owner"
    member = db_session.scalar(select(WorkspaceMember))
    assert (member.account_id, member.role) == (account.id, "owner")


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_a_workspace_name_must_be_1_to_100_characters(client: TestClient, db_session, name):
    user, _ = _account(db_session, 101, "lead-a")
    log_in(client, user.id)

    assert client.post("/api/workspaces", json={"name": name}).status_code == 422


def test_the_list_holds_only_my_workspaces(client: TestClient, db_session):
    user_a, account_a = _account(db_session, 101, "lead-a")
    _, account_b = _account(db_session, 202, "lead-b")
    _workspace(db_session, account_a, "Alpha")
    _workspace(db_session, account_b, "Bravo")
    log_in(client, user_a.id)

    names = [w["name"] for w in client.get("/api/workspaces").json()]

    assert names == ["Alpha"]


def test_an_archived_workspace_is_not_listed(client: TestClient, db_session):
    user, account = _account(db_session, 101, "lead-a")
    workspace = _workspace(db_session, account)
    workspace.archived_at = datetime.now(UTC)
    db_session.commit()
    log_in(client, user.id)

    assert client.get("/api/workspaces").json() == []


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def test_another_accounts_workspace_is_404_not_403(client: TestClient, db_session):
    """A non-member cannot even learn that the workspace exists (EV-10)."""
    _, account_a = _account(db_session, 101, "lead-a")
    user_b, _ = _account(db_session, 202, "lead-b")
    workspace = _workspace(db_session, account_a)
    log_in(client, user_b.id)

    existing = client.get(f"/api/workspaces/{workspace.id}")
    missing = client.get("/api/workspaces/99999")

    assert existing.status_code == missing.status_code == 404
    assert existing.json() == missing.json()


def test_an_archived_workspace_is_404_even_for_its_owner(client: TestClient, db_session):
    user, account = _account(db_session, 101, "lead-a")
    workspace = _workspace(db_session, account)
    workspace.archived_at = datetime.now(UTC)
    db_session.commit()
    log_in(client, user.id)

    assert client.get(f"/api/workspaces/{workspace.id}").status_code == 404


def test_a_viewer_can_open_the_workspace(client: TestClient, db_session):
    _, owner = _account(db_session, 101, "lead-a")
    viewer_user, viewer = _account(db_session, 202, "viewer-b")
    workspace = _workspace(db_session, owner)
    _add_member(db_session, workspace, viewer, "viewer")
    log_in(client, viewer_user.id)

    response = client.get(f"/api/workspaces/{workspace.id}")

    assert response.status_code == 200
    assert response.json()["role"] == "viewer"


def test_no_session_is_401(client: TestClient):
    assert client.get("/api/workspaces").status_code == 401


def test_a_session_without_an_account_is_401(client: TestClient, db_session):
    """An app_user that never signed in through GitHub has no account."""
    org = Organization(name="Org")
    db_session.add(org)
    db_session.flush()
    user = AppUser(organization_id=org.id, display_name="Seeded", is_active=True)
    db_session.add(user)
    db_session.commit()
    log_in(client, user.id)

    assert client.get("/api/workspaces").status_code == 401


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


@pytest.fixture
def role_client(db_session: Session) -> Generator[TestClient, None, None]:
    """A small app with 1 manager route and 1 owner route."""
    role_app = FastAPI()

    @role_app.post("/w/{workspace_id}/manage")
    def manage(access: ManagerAccess) -> dict[str, Any]:
        return {"role": access.role}

    @role_app.post("/w/{workspace_id}/own")
    def own(access: OwnerAccess) -> dict[str, Any]:
        return {"role": access.role}

    _override_db(role_app, db_session)
    return TestClient(role_app)


@pytest.mark.parametrize(
    ("role", "manage_status", "own_status"),
    [("viewer", 403, 403), ("manager", 200, 403), ("owner", 200, 200)],
)
def test_roles_include_everything_below_them(
    role_client: TestClient, db_session, role, manage_status, own_status
):
    _, owner = _account(db_session, 101, "lead-a")
    member_user, member = _account(db_session, 202, "member-b")
    workspace = _workspace(db_session, owner)
    if role != "owner":
        _add_member(db_session, workspace, member, role)
    else:
        member_user, member = _account(db_session, 101, "lead-a")
    log_in(role_client, member_user.id)

    assert role_client.post(f"/w/{workspace.id}/manage").status_code == manage_status
    assert role_client.post(f"/w/{workspace.id}/own").status_code == own_status


# ---------------------------------------------------------------------------
# Every workspace route is guarded
# ---------------------------------------------------------------------------


def _uses_workspace_access(dependant: Any) -> bool:
    for dependency in dependant.dependencies:
        if hasattr(dependency.call, "min_role") or _uses_workspace_access(dependency):
            return True
    return False


def test_every_workspace_route_checks_membership():
    """A route with {workspace_id} that skips workspace_access would leak data."""
    routes = [(p, d) for p, d in iter_api_routes(app) if "{workspace_id}" in p]
    assert routes, "expected at least 1 workspace route; the scan may be seeing none"
    unguarded = [p for p, d in routes if not _uses_workspace_access(d)]
    assert unguarded == []


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def test_an_account_is_matched_on_the_numeric_github_id(db_session):
    """A renamed GitHub login updates the same account instead of creating a second."""
    user, first = _account(db_session, 101, "old-login")

    again = upsert_account(db_session, {"id": 101, "login": "new-login"}, user.id)
    db_session.commit()

    assert again.id == first.id
    assert again.github_login == "new-login"
    assert len(db_session.scalars(select(Account)).all()) == 1


def test_workspace_context_is_frozen():
    assert WorkspaceContext.__dataclass_params__.frozen  # type: ignore[attr-defined]
