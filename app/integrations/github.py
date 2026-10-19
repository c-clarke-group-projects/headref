"""GitHub read-only API client (P2-002, Issue #13).

Fetches repositories, pull requests, reviews, commits, and branches from GitHub REST API v3.
Adheres strictly to read-only constraints (AC-17), handles RFC-5988 pagination, and uses
the shared HttpClient for bounded retries and secret redaction (P2-001, NFR-001, NFR-002).
"""

import logging
import os
import re
from datetime import datetime
from typing import Any

import httpx

from app.config import HTTP_MAX_ATTEMPTS, HTTP_TIMEOUT_SECONDS, settings
from app.integrations.http import HttpClient

logger = logging.getLogger("headref.integrations.github")

# Regex to extract URL with rel="next" from RFC-5988 Link header
_NEXT_LINK_REGEX = re.compile(r'<([^>]+)>;\s*rel="next"')

# GitHub logins: letters, digits and hyphens, 1 to 39 characters, no hyphen at
# either end. Checked before the login goes into a URL path.
GITHUB_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
GITHUB_API_URL = "https://api.github.com"


def parse_next_page_url(link_header: str | None) -> str | None:
    """Extract the next page URL from a GitHub RFC-5988 Link response header."""
    if not link_header:
        return None
    match = _NEXT_LINK_REGEX.search(link_header)
    return match.group(1) if match else None


def get_active_github_token() -> str | None:
    """The configured GITHUB_TOKEN, for the legacy configured-scope sync.

    A signed-in user's token is never kept: sign-in asks for no scopes and drops its
    token after reading the profile (DEC-026). Workspaces use GitHub App tokens.
    """
    env_token = os.getenv("GITHUB_TOKEN")
    if env_token:
        return env_token
    if settings is not None and settings.github_token:
        return settings.github_token
    return None


# Check run conclusions that mean the head commit failed CI (BL-7).
_FAILING_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
)


def summarize_checks(
    check_runs: list[dict[str, Any]], combined_status: dict[str, Any] | None
) -> str:
    """Reduce check runs and the combined commit status to passing, failing or unknown.

    Failing wins over everything. Nothing reported, or anything still running, is
    unknown: "passing" would claim a result nobody has seen yet.
    """
    statuses = (combined_status or {}).get("statuses") or []
    combined_state = (combined_status or {}).get("state")
    if any(run.get("conclusion") in _FAILING_CONCLUSIONS for run in check_runs):
        return "failing"
    if statuses and combined_state in ("failure", "error"):
        return "failing"
    if not check_runs and not statuses:
        return "unknown"
    if any(run.get("status") != "completed" for run in check_runs):
        return "unknown"
    if statuses and combined_state != "success":
        return "unknown"
    return "passing"


class GitHubClient:
    """Read-only GitHub REST API client."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        timeout: float = HTTP_TIMEOUT_SECONDS,
        max_attempts: int = HTTP_MAX_ATTEMPTS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if token is None:
            token = get_active_github_token()

        if not token:
            raise RuntimeError("GITHUB_TOKEN must be configured in environment or settings.")

        self.base_url = base_url.rstrip("/")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        self._http = HttpClient(
            base_url=self.base_url,
            timeout=timeout,
            max_attempts=max_attempts,
            transport=transport,
            headers=headers,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _paginate(
        self,
        initial_url: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all pages of a paginated resource following RFC-5988 Link headers."""
        results: list[dict[str, Any]] = []
        current_url: str | None = initial_url
        current_params = params

        while current_url:
            response = self._http.get(current_url, params=current_params)
            data = response.json()

            if isinstance(data, list):
                results.extend(data)
            elif isinstance(data, dict) and "items" in data:
                results.extend(data["items"])
            else:
                break

            # The URL parsed from rel="next" already embeds query parameters
            current_params = None
            link_header = response.headers.get("link")
            current_url = parse_next_page_url(link_header)

        return results

    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        """Fetch details of a single GitHub repository."""
        url = f"/repos/{owner}/{repo}"
        response = self._http.get(url)
        return response.json()

    def list_repositories(self, org: str | None = None) -> list[dict[str, Any]]:
        """List repositories for an organization or authenticated user."""
        if org:
            url = f"/orgs/{org}/repos"
        else:
            url = "/user/repos"
        return self._paginate(url, params={"per_page": 100})

    def list_organizations(self) -> list[dict[str, Any]]:
        """List organizations for the authenticated user."""
        return self._paginate("/user/orgs", params={"per_page": 100})

    def list_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "all",
    ) -> list[dict[str, Any]]:
        """List pull requests for a repository with pagination."""
        url = f"/repos/{owner}/{repo}/pulls"
        params = {"state": state, "per_page": 100}
        return self._paginate(url, params=params)

    def list_reviews(
        self,
        owner: str,
        repo: str,
        pull_number: int,
    ) -> list[dict[str, Any]]:
        """List reviews for a specific pull request with pagination."""
        url = f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews"
        params = {"per_page": 100}
        return self._paginate(url, params=params)

    def list_commits(
        self,
        owner: str,
        repo: str,
        since: datetime | None = None,
        until: datetime | None = None,
        sha: str | None = None,
    ) -> list[dict[str, Any]]:
        """List commits for a repository with optional time range and branch filters."""
        url = f"/repos/{owner}/{repo}/commits"
        params: dict[str, Any] = {"per_page": 100}
        if since:
            params["since"] = since.isoformat()
        if until:
            params["until"] = until.isoformat()
        if sha:
            params["sha"] = sha
        return self._paginate(url, params=params)

    def get_checks_state(self, owner: str, repo: str, ref: str) -> str:
        """passing, failing or unknown for a commit, from check runs and statuses."""
        runs = self._http.get(
            f"/repos/{owner}/{repo}/commits/{ref}/check-runs", params={"per_page": 100}
        ).json()
        status = self._http.get(f"/repos/{owner}/{repo}/commits/{ref}/status").json()
        return summarize_checks(runs.get("check_runs") or [], status)

    def list_branches(
        self,
        owner: str,
        repo: str,
    ) -> list[dict[str, Any]]:
        """List branches for a repository with pagination."""
        url = f"/repos/{owner}/{repo}/branches"
        params = {"per_page": 100}
        return self._paginate(url, params=params)


def lookup_github_user(login: str, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Return {"id", "login", "type"} of a GitHub account, from its public profile.

    Used to turn an invited login into the numeric id an invite is matched on. No token:
    GitHub allows 60 unauthenticated requests an hour per address, plenty for invites.
    Raises ValueError for a malformed login, NotFoundError when there is no such account,
    and the other typed IntegrationErrors when GitHub cannot be reached.
    """
    if not GITHUB_LOGIN_PATTERN.match(login):
        raise ValueError("not a GitHub login")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    with HttpClient(
        base_url=GITHUB_API_URL,
        timeout=HTTP_TIMEOUT_SECONDS,
        max_attempts=HTTP_MAX_ATTEMPTS,
        transport=transport,
        headers=headers,
    ) as http:
        data = http.get(f"/users/{login}").json()
    logger.info("github user lookup: login=%s found_id=%s", login, data.get("id"))
    return {"id": int(data["id"]), "login": str(data["login"]), "type": str(data.get("type"))}
