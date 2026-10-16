"""Unit tests for the shared HTTP client (P2-001, Issue #12)."""

import logging

import httpx
import pytest
import respx

from app.integrations.errors import (
    AuthError,
    IntegrationTimeoutError,
    NotFoundError,
    RateLimitError,
    UpstreamError,
)
from app.integrations.http import HttpClient


@respx.mock
def test_get_success_200() -> None:
    """Standard HTTP 200 returns valid response on the first attempt."""
    route = respx.get("https://api.github.com/user").respond(
        200,
        json={"login": "keshan-dev", "id": 219891474},
    )

    with HttpClient() as client:
        response = client.get("https://api.github.com/user")

    assert response.status_code == 200
    assert response.json() == {"login": "keshan-dev", "id": 219891474}
    assert route.call_count == 1


@respx.mock
def test_timeout_raises_typed_timeout() -> None:
    """Timeout exceptions retry up to max_attempts and raise IntegrationTimeoutError."""
    route = respx.get("https://api.github.com/timeout").mock(
        side_effect=httpx.ReadTimeout("Read timed out"),
    )

    client = HttpClient(max_attempts=3, timeout=0.1)
    with pytest.raises(IntegrationTimeoutError) as exc_info:
        client.get("https://api.github.com/timeout")

    assert route.call_count == 3
    assert exc_info.value.error_type == "TIMEOUT"
    failure = exc_info.value.to_tool_failure("T-001")
    assert failure.error_type == "TIMEOUT"
    assert failure.tool_id == "T-001"


@respx.mock
def test_401_auth_error_fails_immediately() -> None:
    """HTTP 401 raises AuthError immediately without retrying."""
    route = respx.get("https://api.github.com/unauthorized").respond(
        401,
        text="Bad credentials",
    )

    client = HttpClient(max_attempts=3)
    with pytest.raises(AuthError) as exc_info:
        client.get("https://api.github.com/unauthorized")

    assert route.call_count == 1
    assert exc_info.value.error_type == "AUTH_FAILED"
    assert exc_info.value.status_code == 401


@respx.mock
def test_403_rate_limited_with_header() -> None:
    """HTTP 403 with rate limit headers maps to RateLimitError and extracts retry_after."""
    route = respx.get("https://api.github.com/rate-limit").respond(
        403,
        headers={"x-ratelimit-remaining": "0", "retry-after": "0.01"},
        text="API rate limit exceeded",
    )

    client = HttpClient(max_attempts=3)
    with pytest.raises(RateLimitError) as exc_info:
        client.get("https://api.github.com/rate-limit")

    assert route.call_count == 1
    assert exc_info.value.error_type == "RATE_LIMITED"
    assert exc_info.value.retry_after == 0.01
    assert exc_info.value.status_code == 403


@respx.mock
def test_403_standard_forbidden_fails_immediately() -> None:
    """Standard HTTP 403 without rate limit signals raises AuthError without retrying."""
    route = respx.get("https://api.github.com/forbidden").respond(
        403,
        text="Resource access forbidden",
    )

    client = HttpClient(max_attempts=3)
    with pytest.raises(AuthError) as exc_info:
        client.get("https://api.github.com/forbidden")

    assert route.call_count == 1
    assert exc_info.value.error_type == "AUTH_FAILED"
    assert exc_info.value.status_code == 403


@respx.mock
def test_404_not_found_fails_immediately() -> None:
    """HTTP 404 raises NotFoundError immediately without retrying."""
    route = respx.get("https://api.github.com/repos/unknown").respond(
        404,
        text="Not Found",
    )

    client = HttpClient(max_attempts=3)
    with pytest.raises(NotFoundError) as exc_info:
        client.get("https://api.github.com/repos/unknown")

    assert route.call_count == 1
    assert exc_info.value.error_type == "NOT_FOUND"
    assert exc_info.value.status_code == 404


@respx.mock
def test_429_retry_after_honoured_and_succeeds() -> None:
    """HTTP 429 retries using Retry-After delay and succeeds on subsequent attempt."""
    route = respx.get("https://api.github.com/retry-me").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0.01"}, text="Too many requests"),
            httpx.Response(200, json={"status": "recovered"}),
        ]
    )

    client = HttpClient(max_attempts=3)
    response = client.get("https://api.github.com/retry-me")

    assert response.status_code == 200
    assert response.json() == {"status": "recovered"}
    assert route.call_count == 2


@respx.mock
def test_429_exhausts_retries_raises_rate_limit_error() -> None:
    """HTTP 429 raises RateLimitError when retries are exhausted."""
    route = respx.get("https://api.github.com/exhausted-429").mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0.01"}),
            httpx.Response(429, headers={"retry-after": "0.01"}),
            httpx.Response(429, headers={"retry-after": "0.01"}),
        ]
    )

    client = HttpClient(max_attempts=3)
    with pytest.raises(RateLimitError) as exc_info:
        client.get("https://api.github.com/exhausted-429")

    assert route.call_count == 3
    assert exc_info.value.error_type == "RATE_LIMITED"


@respx.mock
def test_500_upstream_error_retried_up_to_max_attempts() -> None:
    """HTTP 500 server errors retry up to max_attempts and raise UpstreamError."""
    route = respx.get("https://api.github.com/server-error").respond(
        500,
        text="Internal Server Error",
    )

    client = HttpClient(max_attempts=3)
    with pytest.raises(UpstreamError) as exc_info:
        client.get("https://api.github.com/server-error")

    assert route.call_count == 3
    assert exc_info.value.error_type == "UPSTREAM_ERROR"
    assert exc_info.value.status_code == 500


@respx.mock
def test_no_secret_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Sensitive tokens and authorization headers are never logged."""
    respx.get("https://api.github.com/sensitive").respond(200, json={"ok": True})

    token_secret = "ghp_super_secret_personal_access_token_12345"
    query_secret = "top_secret_param_value_67890"

    client = HttpClient()
    with caplog.at_level(logging.DEBUG, logger="headref.integrations.http"):
        client.get(
            f"https://api.github.com/sensitive?token={query_secret}&other=safe",
            headers={
                "Authorization": f"Bearer {token_secret}",
                "X-Api-Key": "secret-api-key-abcde",
            },
        )

    log_output = caplog.text
    assert token_secret not in log_output
    assert "secret-api-key-abcde" not in log_output
    assert query_secret not in log_output
    assert "***" in log_output
    assert "api.github.com/sensitive" in log_output


def test_transport_is_injectable() -> None:
    """Offline fixtures or mock transport can be injected into HttpClient (DEC-012)."""
    mock_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"source": "fixture", "path": str(request.url)})
    )

    client = HttpClient(transport=mock_transport)
    response = client.get("https://api.mock.test/fixtures/issues")

    assert response.status_code == 200
    assert response.json() == {
        "source": "fixture",
        "path": "https://api.mock.test/fixtures/issues",
    }


def test_every_request_carries_explicit_timeout() -> None:
    """Client configures explicit connect and read timeouts (NFR-001)."""
    client = HttpClient(timeout=7.5)
    assert client._client.timeout.connect == 7.5
    assert client._client.timeout.read == 7.5
