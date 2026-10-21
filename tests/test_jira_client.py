"""Unit tests for Jira read-only API client (P2-004, Issue #15)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.config import HTTP_MAX_ATTEMPTS
from app.integrations.errors import (
    AuthError,
    IntegrationTimeoutError,
    NotFoundError,
    RateLimitError,
    UpstreamError,
)
from app.integrations.jira import JiraClient

FIXTURES_DIR = Path("seed/fixtures/jira")
BASE_URL = "https://example.atlassian.net"
EMAIL = "test@example.com"
API_TOKEN = "test-jira-token"


@pytest.fixture
def jira_client() -> JiraClient:
    """Provide a configured JiraClient pointing to the test instance."""
    return JiraClient(
        base_url=BASE_URL,
        email=EMAIL,
        api_token=API_TOKEN,
    )


def load_fixture(filename: str) -> Any:
    """Load JSON fixture from seed/fixtures/jira/."""
    path = FIXTURES_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@respx.mock
def test_search_issues_from_fixture(jira_client: JiraClient) -> None:
    """search_issues fetches and returns issues with all required fields."""
    fixture_data = load_fixture("issues.json")
    respx.get(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json=fixture_data)
    )

    issues = jira_client.search_issues(jql="project = AUTH")
    assert len(issues) == 4

    keys = [issue["key"] for issue in issues]
    assert "AUTH-245" in keys
    assert "PAY-101" in keys
    assert "AUTH-246" in keys
    assert "AUTH-240" in keys

    # Verify fields on AUTH-245
    auth_245 = next(i for i in issues if i["key"] == "AUTH-245")
    fields = auth_245["fields"]
    assert fields["summary"] == "Implement session validation seam"
    assert fields["status"]["name"] == "In Progress"
    assert fields["assignee"]["accountId"] == "5f3a1b2c3d4e"
    assert fields["priority"]["name"] == "High"
    assert fields["duedate"] == "2026-09-22"
    assert fields["flagged"] is False

    # Verify flagged impediment on AUTH-246
    auth_246 = next(i for i in issues if i["key"] == "AUTH-246")
    assert auth_246["fields"]["flagged"] is True


@respx.mock
def test_get_issue_links_from_fixture(jira_client: JiraClient) -> None:
    """get_issue_links fetches blocks / is blocked by dependencies."""
    issue_data = {
        "id": "10003",
        "key": "AUTH-246",
        "fields": {
            "issuelinks": [
                {
                    "id": "20001",
                    "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                    "inwardIssue": {"id": "10001", "key": "AUTH-245"},
                }
            ]
        },
    }
    respx.get(f"{BASE_URL}/rest/api/3/issue/AUTH-246").mock(
        return_value=httpx.Response(200, json=issue_data)
    )

    links = jira_client.get_issue_links("AUTH-246")
    assert len(links) == 1
    assert links[0]["type"]["name"] == "Blocks"
    assert links[0]["inwardIssue"]["key"] == "AUTH-245"


@respx.mock
def test_get_remote_links_from_fixture(jira_client: JiraClient) -> None:
    """get_remote_links returns correlated links (e.g. GitHub PR URLs)."""
    remote_data = load_fixture("remote_links.json")
    respx.get(f"{BASE_URL}/rest/api/3/issue/AUTH-245/remotelink").mock(
        return_value=httpx.Response(200, json=remote_data)
    )

    links = jira_client.get_remote_links("AUTH-245")
    assert len(links) == 1
    assert links[0]["object"]["url"] == "https://github.com/keshan-dev/headref/pull/1"


@respx.mock
def test_list_projects_from_fixture(jira_client: JiraClient) -> None:
    """list_projects returns list of projects."""
    projects_data = load_fixture("projects.json")
    respx.get(f"{BASE_URL}/rest/api/3/project").mock(
        return_value=httpx.Response(200, json=projects_data)
    )

    projects = jira_client.list_projects()
    assert len(projects) == 2
    keys = {p["key"] for p in projects}
    assert keys == {"AUTH", "PAY"}


@respx.mock
def test_pagination_follows_next_page_token(jira_client: JiraClient) -> None:
    """search/jql pages with nextPageToken until isLast (the startAt API was removed)."""
    route = respx.get(f"{BASE_URL}/rest/api/3/search/jql")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "issues": [{"key": "ISSUE-1"}, {"key": "ISSUE-2"}],
                "nextPageToken": "page-2",
                "isLast": False,
            },
        ),
        httpx.Response(200, json={"issues": [{"key": "ISSUE-3"}], "isLast": True}),
    ]

    issues = jira_client.search_issues(jql="project = TEST", max_results=2, auto_paginate=True)

    assert [i["key"] for i in issues] == ["ISSUE-1", "ISSUE-2", "ISSUE-3"]
    assert route.call_count == 2
    assert "nextPageToken" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["nextPageToken"] == "page-2"


def test_an_unbounded_search_is_refused_before_any_call(jira_client: JiraClient) -> None:
    """Jira Cloud answers 400 to empty JQL; say so here instead of calling it."""
    with pytest.raises(ValueError, match="bounded"):
        jira_client.search_issues(jql="  ")


@respx.mock
def test_jira_401_unauthorized(jira_client: JiraClient) -> None:
    """401 response raises AuthError without retrying."""
    respx.get(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(401, json={"errorMessages": ["Invalid credentials"]})
    )

    with pytest.raises(AuthError):
        jira_client.search_issues(jql="project = TEST")


@respx.mock
def test_jira_403_rate_limited(jira_client: JiraClient) -> None:
    """403 with rate-limiting header raises RateLimitError."""
    respx.get(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(403, headers={"retry-after": "30"})
    )

    with pytest.raises(RateLimitError):
        jira_client.search_issues(jql="project = TEST")


@respx.mock
def test_jira_429_rate_limited(jira_client: JiraClient) -> None:
    """429 response raises RateLimitError after retries."""
    respx.get(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(429, headers={"retry-after": "1"})
    )

    with pytest.raises(RateLimitError):
        jira_client.search_issues(jql="project = TEST")


SEARCH_URL = f"{BASE_URL}/rest/api/3/search/jql"


@respx.mock
def test_jira_timeout_is_retried_then_typed(jira_client: JiraClient, no_backoff: None) -> None:
    """A timeout through the client retries up to the limit and raises TIMEOUT (P6-001)."""
    route = respx.get(SEARCH_URL).mock(side_effect=httpx.ReadTimeout("Read timed out"))

    with pytest.raises(IntegrationTimeoutError) as exc_info:
        jira_client.search_issues(jql="project = TEST")

    assert route.call_count == HTTP_MAX_ATTEMPTS
    assert exc_info.value.error_type == "TIMEOUT"


@respx.mock
def test_jira_429_is_retried_up_to_the_limit(jira_client: JiraClient, no_backoff: None) -> None:
    """429 through the client is retried up to the limit before RATE_LIMITED."""
    route = respx.get(SEARCH_URL).respond(429, headers={"retry-after": "1"})

    with pytest.raises(RateLimitError) as exc_info:
        jira_client.search_issues(jql="project = TEST")

    assert route.call_count == HTTP_MAX_ATTEMPTS
    assert exc_info.value.status_code == 429


@respx.mock
def test_jira_500_is_retried_then_upstream_error(jira_client: JiraClient, no_backoff: None) -> None:
    """500 through the client retries up to the limit and raises UPSTREAM_ERROR."""
    route = respx.get(SEARCH_URL).respond(500, json={"errorMessages": ["Internal error"]})

    with pytest.raises(UpstreamError) as exc_info:
        jira_client.search_issues(jql="project = TEST")

    assert route.call_count == HTTP_MAX_ATTEMPTS
    assert exc_info.value.status_code == 500
    assert exc_info.value.error_type == "UPSTREAM_ERROR"


@respx.mock
def test_jira_403_without_rate_limit_signals_is_auth_failed(jira_client: JiraClient) -> None:
    """A plain 403 (no permission to browse) is AUTH_FAILED and is not retried."""
    route = respx.get(SEARCH_URL).respond(
        403, json={"errorMessages": ["You do not have permission to browse this project."]}
    )

    with pytest.raises(AuthError) as exc_info:
        jira_client.search_issues(jql="project = TEST")

    assert route.call_count == 1
    assert exc_info.value.status_code == 403
    assert exc_info.value.error_type == "AUTH_FAILED"


@respx.mock
def test_jira_404_not_found(jira_client: JiraClient) -> None:
    """404 response raises NotFoundError."""
    respx.get(f"{BASE_URL}/rest/api/3/issue/MISSING-999").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
    )

    with pytest.raises(NotFoundError):
        jira_client.get_issue("MISSING-999")


def test_missing_config_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing base URL, email or API token raises RuntimeError."""
    # app/integrations/jira.py binds settings at import time, so patching
    # app.config.settings would leave that binding in place.
    monkeypatch.setattr("app.integrations.jira.settings", None)

    with pytest.raises(RuntimeError, match="Jira base URL is required"):
        JiraClient(base_url="", email="a@b.com", api_token="tok")

    with pytest.raises(RuntimeError, match="Jira email and API token"):
        JiraClient(base_url="https://example.com", email="", api_token="tok")
