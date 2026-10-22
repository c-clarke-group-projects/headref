"""Demo seeding through the real ingestion path (P2-009, Issue #20).

Seeds a complete demo database strictly through real ingestion functions using
an offline FixtureTransport. Zero live network calls are made. Produces a
realistic dataset featuring verified members, unmatched entities (S-6),
unlinked PRs (S-8), and Jira/GitHub state conflicts (CF-1).
"""

import argparse
import json
import logging
import sys
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db import get_sync_session
from app.integrations.github import GitHubClient
from app.integrations.identity_loader import load_identity_map
from app.integrations.jira import JiraClient
from app.models.canonical import AppUser, Organization, Team
from app.models.workspace import Account, Workspace, WorkspaceMember
from app.sync import sync_github, sync_jira

logger = logging.getLogger("headref.seed")

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
# The demo data has its own organization, found by name. It is never "the first
# organization": in a database with workspaces, that is a real workspace's data.
DEMO_ORG_NAME = "headref Demo Corp"
DEMO_WORKSPACE_NAME = "Demo"
# The fixtures' own Jira site. Passed as the browse site, so issue links do not fall back
# to a placeholder host when JIRA_BASE_URL is blank, as README 9.2 allows.
DEMO_JIRA_SITE = "https://example.atlassian.net"
DEMO_WORKSPACE_DESCRIPTION = "Recorded fixture data, not a real team. Never synced."


class DemoOwnerError(Exception):
    """The --owner login cannot own the demo workspace."""


def ensure_demo_workspace(session: Session, org: Organization, owner_login: str) -> Workspace:
    """Return the demo workspace on the demo organization, owned by owner_login (#46).

    The owner must have signed in to headref once, which creates their account.
    """
    account = session.scalar(
        select(Account).where(func.lower(Account.github_login) == owner_login.lower())
    )
    if account is None:
        raise DemoOwnerError(
            f"No headref account for GitHub login {owner_login!r}. Sign in to headref once "
            "with that account, then run this again."
        )

    workspace = session.scalar(select(Workspace).where(Workspace.organization_id == org.id))
    if workspace is None:
        workspace = Workspace(
            name=DEMO_WORKSPACE_NAME,
            description=DEMO_WORKSPACE_DESCRIPTION,
            organization_id=org.id,
            created_by_account_id=account.id,
            is_demo=True,
        )
        session.add(workspace)
        session.flush()
    elif not workspace.is_demo:
        raise DemoOwnerError(
            f"Organization {org.name!r} belongs to the real workspace {workspace.name!r}."
        )

    member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace.id,
            WorkspaceMember.account_id == account.id,
        )
    )
    if member is None:
        session.add(WorkspaceMember(workspace_id=workspace.id, account_id=account.id, role="owner"))
    session.commit()
    return workspace


class FixtureTransport(httpx.BaseTransport):
    """Offline HTTP transport replaying local JSON fixtures without network calls."""

    def __init__(self, fixtures_dir: Path) -> None:
        self.fixtures_dir = fixtures_dir

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url_path = request.url.path

        # 1. GitHub endpoint routing
        if "github" in str(request.url.host) or "/repos/" in url_path:
            if "/pulls/" in url_path and "/reviews" in url_path:
                # Only this pull request's reviews. Returning the whole file gave every
                # pull request every review, with links to other pull requests.
                number = url_path.split("/pulls/")[1].split("/")[0]
                reviews_text = (self.fixtures_dir / "github" / "reviews.json").read_text()
                reviews = [
                    r
                    for r in json.loads(reviews_text)
                    if f"/pull/{number}#" in (r.get("html_url") or "")
                ]
                return httpx.Response(200, json=reviews, request=request)
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
    owner_login: str | None = None,
) -> dict[str, int]:
    """Seed the database end-to-end using real ingestion with offline fixture transport.

    With owner_login, the data also gets a demo workspace owned by that account, so it
    shows in the UI (#46). Without it, the data is seeded but no workspace shows it.
    """
    fdir = fixtures_dir or FIXTURES_DIR
    imap = identity_map_path or (Path(__file__).resolve().parent / "identity_map.yml")

    logger.info("Initializing demo organization and team...")

    # 1. Organization and Team
    org = session.scalar(select(Organization).where(Organization.name == DEMO_ORG_NAME))
    if not org:
        org = Organization(name=DEMO_ORG_NAME)
        session.add(org)
        session.flush()

    team = session.scalar(select(Team).where(Team.organization_id == org.id))
    if not team:
        team = Team(organization_id=org.id, name="Core Engineering")
        session.add(team)
        session.flush()

    session.commit()

    workspace_id = None
    if owner_login:
        workspace_id = ensure_demo_workspace(session, org, owner_login).id

    # 2. Identity Map, into the demo organization only
    logger.info("Loading identity map from %s...", imap)
    load_identity_map(session, imap, organization_id=org.id)

    # Assign the demo team to the demo organization's unassigned users
    users = session.scalars(
        select(AppUser).where(AppUser.organization_id == org.id, AppUser.team_id.is_(None))
    ).all()
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
        base_url=DEMO_JIRA_SITE,
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
        organization_id=org.id,
        workspace_id=workspace_id,
        site_url=DEMO_JIRA_SITE,
    )

    logger.info("Running GitHub demo sync...")
    gh_run = sync_github(
        session=session,
        team_id=team.id,
        scope="keshan-dev/headref",
        gh_client=gh_client,
        organization_id=org.id,
        workspace_id=workspace_id,
    )

    logger.info("Running Jira demo sync again to settle cross-source links...")
    jira_run = sync_jira(
        session=session,
        team_id=team.id,
        scope="ALL",
        jira_client=jira_client,
        organization_id=org.id,
        workspace_id=workspace_id,
        site_url=DEMO_JIRA_SITE,
    )

    summary = {
        "gh_fetched": gh_run.items_fetched,
        "gh_written": gh_run.items_written,
        "jira_fetched": jira_run.items_fetched,
        "jira_written": jira_run.items_written,
        "workspace_id": workspace_id or 0,
    }
    logger.info("Demo seeding complete: %s", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for seeding demo database."""
    parser = argparse.ArgumentParser(description="Seed the headref demo data offline.")
    parser.add_argument(
        "--owner",
        metavar="GITHUB_LOGIN",
        help="Also create the Demo workspace, owned by this account, so the data shows in "
        "the UI. The account must have signed in to headref once.",
    )
    args = parser.parse_args(argv)

    session = get_sync_session()
    try:
        summary = seed_demo_database(session, owner_login=args.owner)
    except DemoOwnerError as err:
        print(f"Not seeded: {err}", file=sys.stderr)
        return 1
    finally:
        session.close()
    print("Demo database seeded successfully through the real ingestion path:")
    print(f"  GitHub: {summary['gh_written']} written ({summary['gh_fetched']} fetched)")
    print(f"  Jira:   {summary['jira_written']} written ({summary['jira_fetched']} fetched)")
    if summary.get("workspace_id"):
        print(f"  Demo workspace: /w/{summary['workspace_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
