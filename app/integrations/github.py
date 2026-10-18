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


def parse_next_page_url(link_header: str | None) -> str | None:
    """Extract the next page URL from a GitHub RFC-5988 Link response header."""
    if not link_header:
        return None
    match = _NEXT_LINK_REGEX.search(link_header)
    return match.group(1) if match else None


_ACTIVE_GITHUB_TOKEN: str | None = None


def set_active_github_token(token: str | None) -> None:
    """Store the authenticated user's GitHub token in memory for sync operations."""
    global _ACTIVE_GITHUB_TOKEN
    _ACTIVE_GITHUB_TOKEN = token


def get_active_github_token() -> str | None:
    """Retrieve the active GitHub token from memory, env, or settings."""
    if _ACTIVE_GITHUB_TOKEN:
        return _ACTIVE_GITHUB_TOKEN
    env_token = os.getenv("GITHUB_TOKEN")
    if env_token:
        return env_token
    if settings is not None and settings.github_token:
        return settings.github_token
    return None


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

    def list_branches(
        self,
        owner: str,
        repo: str,
    ) -> list[dict[str, Any]]:
        """List branches for a repository with pagination."""
        url = f"/repos/{owner}/{repo}/branches"
        params = {"per_page": 100}
        return self._paginate(url, params=params)
