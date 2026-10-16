"""Integration tests for demo seeding through the real ingestion path (P2-009, Issue #20)."""

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models.canonical import AppUser, Organization, Project, Repository, Team
from app.models.identity import IdentityLink, UnmatchedEntity
from app.models.work import PullRequest, WorkItem, WorkItemLink
from seed.seed_demo import seed_demo_database


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_seed_demo_runs_offline_and_populates_data(db_session: Session) -> None:
    """seed_demo_database seeds demo data with 0 live network calls (DEC-012, FR-032)."""
    fixtures_dir = Path("seed/fixtures")
    identity_map_path = Path("seed/identity_map.yml")

    summary = seed_demo_database(
        session=db_session,
        fixtures_dir=fixtures_dir,
        identity_map_path=identity_map_path,
    )
    assert summary["gh_written"] > 0
    assert summary["jira_written"] > 0

    # 1. Complete demo team exists
    org = db_session.scalar(select(Organization))
    assert org is not None
    assert org.name == "headref Demo Corp"

    team = db_session.scalar(select(Team).where(Team.organization_id == org.id))
    assert team is not None
    assert team.name == "Core Engineering"

    users = db_session.scalars(select(AppUser)).all()
    assert len(users) >= 2
    user_names = {u.display_name for u in users}
    assert "Keshan" in user_names
    assert "Isiwara" in user_names

    # 2. Verified identity links exist
    links = db_session.scalars(select(IdentityLink)).all()
    assert len(links) >= 3
    for lnk in links:
        assert lnk.match_method == "manual"
        assert lnk.confidence == "HIGH"
        assert lnk.verified_at is not None

    # 3. Canonical repositories and projects exist
    repo = db_session.scalar(select(Repository).where(Repository.full_name == "keshan-dev/headref"))
    assert repo is not None

    projects = db_session.scalars(select(Project)).all()
    project_keys = {p.key for p in projects}
    assert "AUTH" in project_keys
    assert "PAY" in project_keys


def test_seed_demo_contains_special_evaluation_scenarios(db_session: Session) -> None:
    """Seeded database contains S-6 (unmatched), S-8 (unlinked PR), and CF-1 (state conflict)."""
    seed_demo_database(db_session)

    # Scenario S-6: At least 1 unmatched entity exists
    unmatched = db_session.scalars(select(UnmatchedEntity)).all()
    assert len(unmatched) >= 1
    unmatched_ids = {u.external_id for u in unmatched}
    assert "999999" in unmatched_ids  # external-contributor

    # Scenario S-8: At least 1 unlinked pull request exists
    all_prs = db_session.scalars(select(PullRequest)).all()
    pr_links = db_session.scalars(
        select(WorkItemLink).where(WorkItemLink.target_type == "pull_request")
    ).all()
    linked_pr_ids = {lnk.target_id for lnk in pr_links}

    unlinked_prs = [pr for pr in all_prs if str(pr.id) not in linked_pr_ids]
    assert len(unlinked_prs) >= 1
    # PR #3 has no ticket
    pr_3 = next((pr for pr in all_prs if pr.number == 3), None)
    assert pr_3 is not None
    assert str(pr_3.id) not in linked_pr_ids

    # Scenario CF-1: Jira in_progress but linked PR is merged
    auth_245 = db_session.scalar(select(WorkItem).where(WorkItem.external_id == "AUTH-245"))
    assert auth_245 is not None
    assert auth_245.status == "in_progress"

    # Find PR 182 which is linked to AUTH-245
    pr_182 = db_session.scalar(select(PullRequest).where(PullRequest.number == 182))
    assert pr_182 is not None
    assert pr_182.state == "merged"

    # Verify link exists connecting AUTH-245 to PR 182
    link_to_182 = db_session.scalar(
        select(WorkItemLink).where(
            WorkItemLink.work_item_id == auth_245.id,
            WorkItemLink.target_type == "pull_request",
            WorkItemLink.target_id == str(pr_182.id),
        )
    )
    assert link_to_182 is not None


def test_seed_demo_idempotency_running_twice_safe(db_session: Session) -> None:
    """Running seed_demo_database twice produces identical row counts (NFR-003)."""
    seed_demo_database(db_session)

    user_count_1 = len(db_session.scalars(select(AppUser)).all())
    pr_count_1 = len(db_session.scalars(select(PullRequest)).all())
    wi_count_1 = len(db_session.scalars(select(WorkItem)).all())
    link_count_1 = len(db_session.scalars(select(WorkItemLink)).all())

    # Re-run
    seed_demo_database(db_session)

    user_count_2 = len(db_session.scalars(select(AppUser)).all())
    pr_count_2 = len(db_session.scalars(select(PullRequest)).all())
    wi_count_2 = len(db_session.scalars(select(WorkItem)).all())
    link_count_2 = len(db_session.scalars(select(WorkItemLink)).all())

    assert user_count_1 == user_count_2
    assert pr_count_1 == pr_count_2
    assert wi_count_1 == wi_count_2
    assert link_count_1 == link_count_2
