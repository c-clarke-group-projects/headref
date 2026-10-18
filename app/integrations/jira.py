"""Jira read-only API client (P2-004, Issue #15).

Provides read-only access to Jira Cloud REST API v3 for issues, projects,
issue links, and remote links. Conforms to AC-17 (zero write endpoints).
"""

import base64
import logging
from typing import Any

import httpx

from app.config import settings
from app.integrations.http import HttpClient

logger = logging.getLogger("headref.integrations.jira")


class JiraClient:
    """Read-only Jira Cloud API client conforming to AC-17."""

    def __init__(
        self,
        base_url: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Initialize JiraClient with basic authentication and base URL."""
        if settings is not None:
            self._base_url = (base_url or settings.jira_base_url or "").rstrip("/")
            self._email = email or settings.jira_email or ""
            self._api_token = api_token or settings.jira_api_token or ""
        else:
            self._base_url = (base_url or "").rstrip("/")
            self._email = email or ""
            self._api_token = api_token or ""

        if not self._base_url:
            raise RuntimeError("Jira base URL is required but not configured.")
        if not self._email or not self._api_token:
            raise RuntimeError("Jira email and API token are required but not configured.")

        # Jira Cloud uses HTTP Basic auth: base64(email:api_token)
        raw_auth = f"{self._email}:{self._api_token}".encode()
        b64_auth = base64.b64encode(raw_auth).decode("ascii")

        self._headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {b64_auth}",
        }
        self._client = HttpClient(transport=transport)

    def search_issues(
        self,
        jql: str,
        max_results: int = 50,
        fields: list[str] | None = None,
        auto_paginate: bool = False,
    ) -> list[dict[str, Any]]:
        """Search issues with JQL via read-only GET /rest/api/3/search/jql.

        Jira Cloud removed GET /rest/api/3/search (HTTP 410, verified 2026-10-19) and
        rejects unbounded JQL on the new endpoint, so jql must restrict the search, for
        example by project. Pages are followed with nextPageToken, not startAt.
        """
        if not jql.strip():
            raise ValueError("Jira search needs a bounded JQL query, for example by project")
        url = f"{self._base_url}/rest/api/3/search/jql"
        params: dict[str, Any] = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ",".join(fields) if fields else "*navigable",
        }

        all_issues: list[dict[str, Any]] = []
        while True:
            response = self._client.get(url, headers=self._headers, params=params)
            data = response.json()
            all_issues.extend(data.get("issues", []))
            next_token = data.get("nextPageToken")
            if not auto_paginate or data.get("isLast", True) or not next_token:
                return all_issues
            params["nextPageToken"] = next_token

    def get_issue(
        self,
        issue_id_or_key: str,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a single issue by ID or key via GET /rest/api/3/issue/{issue_id_or_key}."""
        url = f"{self._base_url}/rest/api/3/issue/{issue_id_or_key}"
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = ",".join(fields)

        response = self._client.get(url, headers=self._headers, params=params)
        return response.json()

    def get_remote_links(self, issue_id_or_key: str) -> list[dict[str, Any]]:
        """Fetch remote links for an issue via GET /rest/api/3/issue/{key}/remotelink."""
        url = f"{self._base_url}/rest/api/3/issue/{issue_id_or_key}/remotelink"
        response = self._client.get(url, headers=self._headers)
        data = response.json()
        if isinstance(data, list):
            return data
        return []

    def get_issue_links(self, issue_id_or_key: str) -> list[dict[str, Any]]:
        """Fetch issue links (blocks / is blocked by) for an issue."""
        issue = self.get_issue(issue_id_or_key, fields=["issuelinks"])
        fields = issue.get("fields", {})
        return fields.get("issuelinks", [])

    def list_projects(self) -> list[dict[str, Any]]:
        """Fetch projects accessible to the caller via GET /rest/api/3/project."""
        url = f"{self._base_url}/rest/api/3/project"
        response = self._client.get(url, headers=self._headers)
        data = response.json()
        if isinstance(data, list):
            return data
        return []
