"""Demo seeding through the real ingestion path (P2-009, Issue #20).

Seeds a complete demo database strictly through real ingestion functions using
an offline FixtureTransport. Zero live network calls are made. Produces a
realistic dataset featuring verified members, unmatched entities (S-6),
unlinked PRs (S-8), and Jira/GitHub state conflicts (CF-1).
"""

import json
import logging
import sys
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import get_sync_session
from app.integrations.github import GitHubClient
from app.integrations.identity_loader import load_identity_map
from app.integrations.jira import JiraClient
from app.models.canonical import AppUser, Organization, Team
from app.sync import sync_github, sync_jira

logger = logging.getLogger("headref.seed")

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class FixtureTransport(httpx.BaseTransport):
    """Offline HTTP transport replaying local JSON fixtures without network calls."""

    def __init__(self, fixtures_dir: Path) -> None:
        self.fixtures_dir = fixtures_dir

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url_path = request.url.path

        # 1. GitHub endpoint routing
        if "github" in str(request.url.host) or "/repos/" in url_path:
            if "/pulls/" in url_path and "/reviews" in url_path:
                content = (self.fixtures_dir / "github" / "reviews.json").read_bytes()
                return httpx.Response(200, content=content, request=request)
            if url_path.endswith("/pulls"):
                content = (self.fixtures_dir / "github" / "pull_requests.json").read_bytes()
                return httpx.Response(200, content=content, request=request)
            if url_path.endswith("/commits"):
                content = (self.fixtures_dir / "github" / "commits.json").read_bytes()
                return httpx.Response(200, content=content, request=request)
            if url_path.endswith("/branches"):
                content = (self.fixtures_dir / "github" / "branches.json").read_bytes()
                return httpx.Response(200, content=content, request=request)
            if "/repos/" in url_path:
                repos_text = (self.fixtures_dir / "github" / "repositories.json").read_text()
                repos = json.loads(repos_text)
                repo = repos[0] if repos else {}
                return httpx.Response(200, json=repo, request=request)

        # 2. Jira endpoint routing
        if "atlassian.net" in str(request.url.host) or "/rest/api/" in url_path:
            if "/remotelink" in url_path:
                content = (self.fixtures_dir / "jira" / "remote_links.json").read_bytes()
                return httpx.Response(200, content=content, request=request)
            if "/project" in url_path:
                content = (self.fixtures_dir / "jira" / "projects.json").read_bytes()
                return httpx.Response(200, content=content, request=request)
            if "/search" in url_path:
                content = (self.fixtures_dir / "jira" / "issues.json").read_bytes()
                return httpx.Response(200, content=content, request=request)
            if "/issue/" in url_path:
                issues_text = (self.fixtures_dir / "jira" / "issues.json").read_text()
                issues_data = json.loads(issues_text)
                issues = issues_data.get("issues", [])
                issue_key = url_path.split("/")[-1]
                matched = next((i for i in issues if i.get("key") == issue_key), None)
                if matched:
                    return httpx.Response(200, json=matched, request=request)
                if issues:
                    return httpx.Response(200, json=issues[0], request=request)

        return httpx.Response(404, json={"error": "Not found in fixtures"}, request=request)


def seed_demo_database(
    session: Session,
    fixtures_dir: Path | None = None,
    identity_map_path: Path | None = None,
) -> dict[str, int]:
    """Seed the database end-to-end using real ingestion with offline fixture transport."""
    fdir = fixtures_dir or FIXTURES_DIR
    imap = identity_map_path or (Path(__file__).resolve().parent / "identity_map.yml")

    logger.info("Initializing demo organization and team...")

    # 1. Organization and Team
    org = session.scalar(select(Organization))
    if not org:
        org = Organization(name="headref Demo Corp")
        session.add(org)
        session.flush()

    team = session.scalar(select(Team).where(Team.organization_id == org.id))
    if not team:
        team = Team(organization_id=org.id, name="Core Engineering")
        session.add(team)
        session.flush()

    session.commit()

    # 2. Identity Map
    logger.info("Loading identity map from %s...", imap)
    load_identity_map(session, imap)

    # Assign team to any unassigned users
    users = session.scalars(select(AppUser).where(AppUser.team_id.is_(None))).all()
    for u in users:
        u.team_id = team.id
    session.commit()

    # 3. Create offline clients using FixtureTransport
    transport = FixtureTransport(fixtures_dir=fdir)
    gh_client = GitHubClient(
        token="demo-offline-token",  # noqa: S106 offline fixture transport, no network call
        transport=transport,
    )
    jira_client = JiraClient(
        base_url="https://example.atlassian.net",
        email="demo@example.com",
        api_token="demo-offline-token",  # noqa: S106 offline fixture transport, no network call
        transport=transport,
    )

    # 4. Ingest via real sync path.
    # Linking crosses the 2 sources in both directions: sync_github matches branch
    # names, titles and bodies against work items that must already exist, and
    # sync_jira matches remote links against pull requests that must already exist.
    # No single pass can satisfy both, so Jira runs, GitHub runs, then Jira runs once
    # more to settle the remote links. A later seeding adds no rows, which is the
    # re-run safety P2-009 requires.
    logger.info("Running Jira demo sync...")
    sync_jira(
        session=session,
        team_id=team.id,
        scope="ALL",
        jira_client=jira_client,
    )

    logger.info("Running GitHub demo sync...")
    gh_run = sync_github(
        session=session,
        team_id=team.id,
        scope="keshan-dev/headref",
        gh_client=gh_client,
    )

    logger.info("Running Jira demo sync again to settle cross-source links...")
    jira_run = sync_jira(
        session=session,
        team_id=team.id,
        scope="ALL",
        jira_client=jira_client,
    )

    summary = {
        "gh_fetched": gh_run.items_fetched,
        "gh_written": gh_run.items_written,
        "jira_fetched": jira_run.items_fetched,
        "jira_written": jira_run.items_written,
    }
    logger.info("Demo seeding complete: %s", summary)
    return summary


def main() -> int:
    """CLI entrypoint for seeding demo database."""
    session = get_sync_session()
    try:
        summary = seed_demo_database(session)
        print("Demo database seeded successfully through the real ingestion path:")
        print(f"  GitHub: {summary['gh_written']} written ({summary['gh_fetched']} fetched)")
        print(f"  Jira:   {summary['jira_written']} written ({summary['jira_fetched']} fetched)")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
