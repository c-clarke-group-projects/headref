"""Capture real, sanitized API responses from a workspace's sources (P0-002, #2).

Reads 1 workspace's selected repositories (through its GitHub App installation) and its
Jira projects (through its Atlassian connection), exactly as the workspace sync does,
and saves the raw JSON to seed/fixtures/sandbox/. Read only: GET requests only.

Every email address is replaced before anything is written. Tokens live in memory only
and are never printed or saved. The script also reports, as counts only:
- A-3: whether Jira user objects carry an email address, and whether GitHub commit author
  addresses are privacy (noreply) addresses;
- the rate limit headers GitHub and Jira return.

Run inside the api container:
    docker compose exec api python scripts/capture_fixtures.py --workspace-id 1
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "seed" / "fixtures" / "sandbox"
REDACTED_EMAIL = "redacted@example.invalid"
EMAIL_KEYS = {"email", "emailaddress"}
NOREPLY_SUFFIX = "users.noreply.github.com"


def sanitize(value: Any) -> Any:
    """Return a copy with every email address replaced. Nothing else is changed."""
    if isinstance(value, dict):
        return {
            key: (
                REDACTED_EMAIL
                if key.lower() in EMAIL_KEYS and isinstance(item, str) and item
                else sanitize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def a3_counts(issues: list[dict[str, Any]], commits: list[dict[str, Any]]) -> dict[str, int]:
    """Counts for assumption A-3, taken before sanitizing. No address is returned."""
    users = [
        issue.get("fields", {}).get(role) for issue in issues for role in ("assignee", "reporter")
    ]
    users = [u for u in users if isinstance(u, dict)]
    emails = [(c.get("commit", {}).get("author") or {}).get("email", "") for c in commits]
    return {
        "jira_user_objects": len(users),
        "jira_user_objects_with_email": sum(1 for u in users if u.get("emailAddress")),
        "github_commit_author_emails": sum(1 for e in emails if e),
        "github_commit_author_noreply": sum(1 for e in emails if e.endswith(NOREPLY_SUFFIX)),
    }


RATE_HEADERS = (
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-ratelimit-used",
    "x-ratelimit-resource",
    "retry-after",
    "x-ratelimit-nearlimit",
)


def rate_headers(url: str, token: str) -> dict[str, str]:
    """The rate limit headers of 1 GET. Header names and values only, never the token."""
    import httpx

    response = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=15)
    return {k: v for k, v in response.headers.items() if k.lower() in RATE_HEADERS}


def write(rel: str, data: Any) -> None:
    path = OUT_DIR / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize(data), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}")


def capture(workspace_id: int) -> dict[str, Any]:
    """Fetch, sanitize and write the workspace's sources. Returns the report."""
    from sqlalchemy import select

    from app.db import get_sync_session
    from app.integrations.atlassian import API_URL, AtlassianClient
    from app.integrations.github import GitHubClient
    from app.integrations.github_app import GitHubAppClient
    from app.integrations.jira import JiraClient
    from app.integrations.jira_tokens import valid_access_token
    from app.models.connections import (
        GitHubInstallation,
        JiraConnection,
        WorkspaceJiraProject,
        WorkspaceRepository,
    )
    from app.sync import project_jql

    db = get_sync_session()
    report: dict[str, Any] = {"workspace_id": workspace_id}
    issues: list[dict[str, Any]] = []
    commits: list[dict[str, Any]] = []
    try:
        rows = db.execute(
            select(WorkspaceRepository, GitHubInstallation)
            .join(
                GitHubInstallation,
                GitHubInstallation.id == WorkspaceRepository.github_installation_id,
            )
            .where(WorkspaceRepository.workspace_id == workspace_id)
            .order_by(WorkspaceRepository.full_name)
        ).all()
        app_client = GitHubAppClient()
        repositories, pulls, branches, reviews = [], [], [], []
        for repo, installation in rows:
            owner, name = repo.full_name.split("/", 1)
            token = app_client.installation_token(installation.installation_id)
            with GitHubClient(token=token) as gh:
                repositories.append(gh.get_repository(owner, name))
                repo_pulls = gh.list_pull_requests(owner, name)
                pulls.extend(repo_pulls)
                commits.extend(gh.list_commits(owner, name))
                branches.extend(gh.list_branches(owner, name))
                for pr in repo_pulls:
                    reviews.extend(gh.list_reviews(owner, name, pr["number"]))
            report["github_rate_headers"] = rate_headers("https://api.github.com/rate_limit", token)
        if rows:
            write("github/repositories.json", repositories)
            write("github/pull_requests.json", pulls)
            write("github/commits.json", commits)
            write("github/branches.json", branches)
            write("github/reviews.json", reviews)

        connection = db.scalar(
            select(JiraConnection).where(JiraConnection.workspace_id == workspace_id)
        )
        keys = list(
            db.scalars(
                select(WorkspaceJiraProject.key)
                .where(WorkspaceJiraProject.workspace_id == workspace_id)
                .order_by(WorkspaceJiraProject.key)
            ).all()
        )
        if connection is not None and connection.status == "active" and keys:
            token = valid_access_token(db, connection.id, AtlassianClient())
            base = f"{API_URL}/ex/jira/{connection.cloud_id}"
            jira = JiraClient(base_url=base, bearer_token=token)
            projects = jira.list_projects()
            issues = jira.search_issues(jql=project_jql(keys))
            remote_links = {issue["key"]: jira.get_remote_links(issue["key"]) for issue in issues}
            write("jira/projects.json", [p for p in projects if p.get("key") in keys])
            write("jira/issues.json", {"issues": issues})
            write("jira/remote_links_by_issue.json", remote_links)
            report["jira_rate_headers"] = rate_headers(f"{base}/rest/api/3/myself", token)
        db.commit()
    finally:
        db.close()
    report["a3"] = a3_counts(issues, commits)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture sanitized fixtures from 1 workspace.")
    parser.add_argument("--workspace-id", type=int, required=True)
    args = parser.parse_args(argv)
    report = capture(args.workspace_id)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
