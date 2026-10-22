"""The demo workspace seeded by seed/seed_demo.py --owner (#46, agreed 2026-10-22)."""

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.main import app
from app.models.canonical import AppUser, Organization, Repository
from app.models.connections import WorkspaceRepository
from app.models.operations import SyncRun
from app.models.workspace import Workspace, WorkspaceMember
from app.people import confirmed_people
from app.schemas.tools import GetSourceHealthInput
from app.tools.get_source_health import get_source_health
from app.web import auth, workspace_pages
from app.workspace_analysis import WorkspaceAnalysis, jira_in_use
from app.workspace_sync import has_sources, workspaces_to_sync
from seed.seed_demo import DEMO_ORG_NAME, DemoOwnerError, main, seed_demo_database
from tests.conftest import log_in
from tests.test_workspaces import _account, _workspace


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _demo(db: Session) -> Workspace:
    return db.scalar(select(Workspace).where(Workspace.is_demo.is_(True)))


def test_owner_gets_a_demo_workspace_on_the_demo_organization(db_session: Session) -> None:
    user, owner = _account(db_session, 101, "Demo-Owner")

    summary = seed_demo_database(db_session, owner_login="demo-owner")

    demo = _demo(db_session)
    org = db_session.get(Organization, demo.organization_id)
    assert summary["workspace_id"] == demo.id
    assert org.name == DEMO_ORG_NAME
    assert demo.name == "Demo"
    role = db_session.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == demo.id, WorkspaceMember.account_id == owner.id
        )
    )
    assert role == "owner"
    # Identity-map people are verified mappings, so they are confirmed and analysed.
    assert [p.display_name for p in confirmed_people(db_session, org.id)]


def test_the_demo_workspace_reads_both_sources_as_fresh(db_session: Session) -> None:
    _account(db_session, 101, "demo-owner")
    seed_demo_database(db_session, owner_login="demo-owner")
    demo = _demo(db_session)

    runs = db_session.scalars(select(SyncRun).where(SyncRun.workspace_id == demo.id)).all()
    health = get_source_health(
        db_session, GetSourceHealthInput(workspace_id=demo.id, sources=["github", "jira"])
    )

    assert {r.source for r in runs} == {"github", "jira"}
    assert {h.source: h.state for h in health.sources} == {"github": "fresh", "jira": "fresh"}
    assert jira_in_use(db_session, demo.id) is True


def test_a_demo_workspace_is_never_synced(db_session: Session) -> None:
    _account(db_session, 101, "demo-owner")
    seed_demo_database(db_session, owner_login="demo-owner")
    demo = _demo(db_session)
    # Even with a repository selected, neither Sync now nor the scheduler picks it up.
    repo = db_session.scalar(
        select(Repository).where(Repository.organization_id == demo.organization_id)
    )
    db_session.add(
        WorkspaceRepository(
            workspace_id=demo.id,
            github_installation_id=1,  # SQLite does not enforce the foreign key here
            github_repo_id=1,
            full_name=repo.full_name,
        )
    )
    db_session.commit()

    assert has_sources(db_session, demo.id) is False
    assert demo.id not in workspaces_to_sync(db_session)


def test_seeding_twice_keeps_1_demo_workspace_and_1_owner(db_session: Session) -> None:
    _account(db_session, 101, "demo-owner")
    seed_demo_database(db_session, owner_login="demo-owner")
    seed_demo_database(db_session, owner_login="demo-owner")

    assert db_session.scalar(select(func.count(Workspace.id)).where(Workspace.is_demo)) == 1
    assert db_session.scalar(select(func.count(WorkspaceMember.id))) == 1


def test_an_owner_who_never_signed_in_is_refused_before_anything_is_seeded(
    db_session: Session,
) -> None:
    with pytest.raises(DemoOwnerError, match="Sign in to headref once"):
        seed_demo_database(db_session, owner_login="nobody")

    assert _demo(db_session) is None
    assert db_session.scalar(select(func.count(SyncRun.id))) == 0


def test_the_seed_never_writes_into_another_organization(db_session: Session) -> None:
    """The old seed used the first organization, which can be a real workspace's."""
    user, owner = _account(db_session, 101, "demo-owner")
    real = _workspace(db_session, owner, "dev team")
    real_org = Organization(name="dev team")
    db_session.add(real_org)
    db_session.flush()
    real.organization_id = real_org.id
    db_session.commit()

    seed_demo_database(db_session, owner_login="demo-owner")

    assert (
        db_session.scalar(
            select(func.count(Repository.id)).where(Repository.organization_id == real_org.id)
        )
        == 0
    )
    # The account's own person keeps no team: the demo team is for demo people only.
    assert db_session.get(AppUser, user.id).team_id is None


def test_a_real_workspace_on_the_demo_organization_is_refused(db_session: Session) -> None:
    _, owner = _account(db_session, 101, "demo-owner")
    org = Organization(name=DEMO_ORG_NAME)
    db_session.add(org)
    db_session.flush()
    real = _workspace(db_session, owner, "Someone's team")
    real.organization_id = org.id
    db_session.commit()

    with pytest.raises(DemoOwnerError, match="real workspace"):
        seed_demo_database(db_session, owner_login="demo-owner")


def test_the_cli_reports_a_missing_owner_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("seed.seed_demo.get_sync_session", lambda: db_session)

    assert main(["--owner", "nobody"]) == 1
    assert "Sign in to headref once" in capsys.readouterr().err


def test_the_page_labels_demo_data_and_offers_no_connection(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    user, _ = _account(db_session, 101, "demo-owner")
    seed_demo_database(db_session, owner_login="demo-owner")
    demo = _demo(db_session)

    async def no_analysis(*_: Any, **__: Any) -> WorkspaceAnalysis:
        return WorkspaceAnalysis(members=[], health=[])

    def override() -> Generator[Session, None, None]:
        yield db_session

    monkeypatch.setattr(workspace_pages, "analyze_workspace", no_analysis)
    app.dependency_overrides[auth.get_db] = override
    try:
        with TestClient(app) as client:
            log_in(client, user.id)
            page = client.get(f"/w/{demo.id}").text
            home = client.get("/workspaces").text
    finally:
        app.dependency_overrides.clear()

    assert "Demo data" in page and "Never synced" in page
    assert "Connect GitHub" not in page and "Sync now" not in page
    assert "Demo data" in home


def test_demo_links_point_at_their_own_records(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """With JIRA_BASE_URL blank (README 9.2), no link falls back to a placeholder host,
    and every review links to the pull request it belongs to."""
    import app.config
    from app.models.work import PullRequest, Review, WorkItem
    from seed.seed_demo import DEMO_JIRA_SITE

    monkeypatch.setenv("JIRA_BASE_URL", "")
    app.config.settings = None

    seed_demo_database(db_session)

    items = db_session.scalars(select(WorkItem)).all()
    assert items
    for item in items:
        assert item.source_url == f"{DEMO_JIRA_SITE}/browse/{item.external_id}"
    reviews = db_session.execute(select(Review, PullRequest).join(PullRequest)).all()
    assert reviews
    for review, pr in reviews:
        assert review.source_url.startswith(pr.source_url + "#"), review.source_url
