"""Unit tests for GitHub read-only client (P2-002, Issue #13)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from app.integrations.errors import AuthError, NotFoundError, RateLimitError
from app.integrations.github import GitHubClient, parse_next_page_url

FIXTURE_DIR = Path("seed/fixtures/github")


def load_fixture(filename: str) -> Any:
    """Load and parse a JSON fixture from seed/fixtures/github/."""
    file_path = FIXTURE_DIR / filename
    with open(file_path, encoding="utf-8") as f:
        return json.load(f)


def test_parse_next_page_url() -> None:
    """Verify next page URL extraction from RFC-5988 Link header."""
    header = (
        '<https://api.github.com/repos/org/repo/pulls?page=2>; rel="next", '
        '<https://api.github.com/repos/org/repo/pulls?page=5>; rel="last"'
    )
    assert parse_next_page_url(header) == "https://api.github.com/repos/org/repo/pulls?page=2"
    assert parse_next_page_url(None) is None
    assert (
        parse_next_page_url('<https://api.github.com/repos/org/repo/pulls?page=1>; rel="prev"')
        is None
    )


@respx.mock
def test_list_pull_requests_from_fixture() -> None:
    """GitHub client lists pull requests matching expected fields and state."""
    prs_data = load_fixture("pull_requests.json")
    route = respx.get("https://api.github.com/repos/keshan-dev/headref/pulls").respond(
        200,
        json=prs_data,
    )

    client = GitHubClient(token="ghp_test_token_123")
    prs = client.list_pull_requests("keshan-dev", "headref")

    assert route.called
    assert len(prs) == 4

    pr1 = prs[0]
    assert pr1["number"] == 1
    assert pr1["state"] == "open"
    assert pr1["draft"] is False
    assert pr1["head"]["ref"] == "feature/AUTH-245-login"
    assert pr1["user"]["login"] == "keshan-dev"
    assert pr1["user"]["id"] == 219891474

    pr2 = prs[1]
    assert pr2["draft"] is True
    assert pr2["head"]["ref"] == "feature/PAY-101-profile"
    assert pr2["user"]["login"] == "IsiwaraKumarage8"

    pr3 = prs[2]
    assert pr3["state"] == "closed"
    assert pr3["user"]["login"] == "external-contributor"

    # Pull request 182 carries the CF-1 scenario: merged while AUTH-245 is in progress.
    pr182 = prs[3]
    assert pr182["number"] == 182
    assert pr182["state"] == "closed"
    assert pr182["merged_at"] is not None
    assert pr182["head"]["ref"] == "feature/AUTH-245-refresh-token"


@respx.mock
def test_list_reviews_from_fixture() -> None:
    """GitHub client lists reviews with state, body, and submitted_at timestamp."""
    reviews_data = load_fixture("reviews.json")
    route = respx.get("https://api.github.com/repos/keshan-dev/headref/pulls/1/reviews").respond(
        200, json=reviews_data
    )

    client = GitHubClient(token="ghp_test_token_123")
    reviews = client.list_reviews("keshan-dev", "headref", pull_number=1)

    assert route.called
    assert len(reviews) == 2
    assert reviews[0]["state"] == "APPROVED"
    assert reviews[0]["user"]["login"] == "IsiwaraKumarage8"
    assert reviews[1]["state"] == "CHANGES_REQUESTED"
    assert reviews[1]["user"]["login"] == "keshan-dev"


@respx.mock
def test_list_commits_from_fixture() -> None:
    """GitHub client lists commits with sha, author, message, and date."""
    commits_data = load_fixture("commits.json")
    route = respx.get("https://api.github.com/repos/keshan-dev/headref/commits").respond(
        200, json=commits_data
    )

    client = GitHubClient(token="ghp_test_token_123")
    commits = client.list_commits("keshan-dev", "headref")

    assert route.called
    assert len(commits) == 3
    assert commits[0]["sha"] == "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"
    assert "AUTH-245" in commits[0]["commit"]["message"]
    assert commits[0]["author"]["login"] == "keshan-dev"
    assert commits[0]["author"]["id"] == 219891474


@respx.mock
def test_list_branches_from_fixture() -> None:
    """GitHub client lists branches with name and commit sha."""
    branches_data = load_fixture("branches.json")
    route = respx.get("https://api.github.com/repos/keshan-dev/headref/branches").respond(
        200, json=branches_data
    )

    client = GitHubClient(token="ghp_test_token_123")
    branches = client.list_branches("keshan-dev", "headref")

    assert route.called
    assert len(branches) == 3
    names = [b["name"] for b in branches]
    assert "main" in names
    assert "feature/AUTH-245-login" in names
    assert "feature/PAY-101-profile" in names


@respx.mock
def test_list_repositories_from_fixture() -> None:
    """GitHub client lists repositories for the authenticated user or org."""
    repos_data = load_fixture("repositories.json")
    route = respx.get("https://api.github.com/user/repos").respond(200, json=repos_data)

    client = GitHubClient(token="ghp_test_token_123")
    repos = client.list_repositories()

    assert route.called
    assert len(repos) == 1
    assert repos[0]["name"] == "headref"
    assert repos[0]["full_name"] == "keshan-dev/headref"


@respx.mock
def test_pagination_follows_rfc5988_link_header() -> None:
    """Client automatically paginates through multiple pages using Link header."""
    base = "https://api.github.com/repos/keshan-dev/headref/pulls?state=all&per_page=100"
    page1_url = base
    page2_url = f"{base}&page=2"

    respx.get(page1_url).respond(
        200,
        json=[{"id": 1, "title": "Page 1 Item"}],
        headers={"link": f'<{page2_url}>; rel="next"'},
    )
    respx.get(page2_url).respond(
        200,
        json=[{"id": 2, "title": "Page 2 Item"}],
    )

    client = GitHubClient(token="ghp_test_token_123")
    results = client.list_pull_requests("keshan-dev", "headref")

    assert len(results) == 2
    assert results[0]["title"] == "Page 1 Item"
    assert results[1]["title"] == "Page 2 Item"


@respx.mock
def test_github_403_rate_limited() -> None:
    """GitHub rate-limiting signals on 403 raise RateLimitError."""
    respx.get("https://api.github.com/repos/keshan-dev/headref/pulls").respond(
        403,
        headers={"x-ratelimit-remaining": "0", "retry-after": "60"},
        text="API rate limit exceeded",
    )

    client = GitHubClient(token="ghp_test_token_123", max_attempts=1)
    with pytest.raises(RateLimitError) as exc_info:
        client.list_pull_requests("keshan-dev", "headref")

    assert exc_info.value.status_code == 403
    assert exc_info.value.error_type == "RATE_LIMITED"


@respx.mock
def test_github_401_unauthorized() -> None:
    """GitHub 401 raises AuthError."""
    respx.get("https://api.github.com/repos/keshan-dev/headref/pulls").respond(
        401,
        text="Bad credentials",
    )

    client = GitHubClient(token="invalid_token", max_attempts=1)
    with pytest.raises(AuthError) as exc_info:
        client.list_pull_requests("keshan-dev", "headref")

    assert exc_info.value.status_code == 401
    assert exc_info.value.error_type == "AUTH_FAILED"


@respx.mock
def test_github_404_not_found() -> None:
    """GitHub 404 raises NotFoundError."""
    respx.get("https://api.github.com/repos/keshan-dev/nonexistent/pulls").respond(
        404,
        text="Not Found",
    )

    client = GitHubClient(token="ghp_test_token_123", max_attempts=1)
    with pytest.raises(NotFoundError) as exc_info:
        client.list_pull_requests("keshan-dev", "nonexistent")

    assert exc_info.value.status_code == 404
    assert exc_info.value.error_type == "NOT_FOUND"


def test_missing_github_token_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHubClient raises RuntimeError if token is missing."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    # app/integrations/github.py binds settings at import time, so patching
    # app.config.settings would leave that binding in place.
    monkeypatch.setattr("app.integrations.github.settings", None)

    with pytest.raises(RuntimeError) as exc_info:
        GitHubClient(token=None)

    assert "GITHUB_TOKEN must be configured" in str(exc_info.value)


# ---------------------------------------------------------------------------
# CI checks for BL-7 (W3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("runs", "status", "expected"),
    [
        ([], {"state": "pending", "statuses": []}, "unknown"),
        ([{"status": "completed", "conclusion": "success"}], None, "passing"),
        ([{"status": "completed", "conclusion": "failure"}], None, "failing"),
        ([{"status": "completed", "conclusion": "timed_out"}], None, "failing"),
        ([{"status": "in_progress", "conclusion": None}], None, "unknown"),
        (
            [{"status": "completed", "conclusion": "success"}],
            {"state": "failure", "statuses": [{"state": "failure"}]},
            "failing",
        ),
        ([], {"state": "success", "statuses": [{"state": "success"}]}, "passing"),
        ([], {"state": "pending", "statuses": [{"state": "pending"}]}, "unknown"),
        (
            [{"status": "completed", "conclusion": "skipped"}],
            {"state": "pending", "statuses": []},
            "passing",
        ),
    ],
)
def test_summarize_checks(runs, status, expected):
    from app.integrations.github import summarize_checks

    assert summarize_checks(runs, status) == expected


@respx.mock
def test_get_checks_state_reads_check_runs_and_statuses():
    respx.get("https://api.github.com/repos/o/r/commits/abc/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 1,
                "check_runs": [{"status": "completed", "conclusion": "failure"}],
            },
        )
    )
    respx.get("https://api.github.com/repos/o/r/commits/abc/status").mock(
        return_value=httpx.Response(200, json={"state": "pending", "statuses": []})
    )

    assert GitHubClient(token="t").get_checks_state("o", "r", "abc") == "failing"
