"""Shared HTTP client with timeout, retry, structured logging and typed errors.

Every integration uses this layer to enforce strict timeouts (NFR-001), tenacity-driven
bounded retries (NFR-002), secret-safe logging (NFR-011), and typed error mappings (FR-033).
The transport is injectable for offline fixtures and testing (DEC-012).
"""

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

# tenacity 9.1 stopped re-exporting wait_base from the package root. It has always
# lived in tenacity.wait, which is the documented home for custom wait strategies.
from tenacity.wait import wait_base

from app.config import HTTP_MAX_ATTEMPTS, HTTP_TIMEOUT_SECONDS
from app.integrations.errors import (
    AuthError,
    IntegrationError,
    IntegrationTimeoutError,
    NotFoundError,
    RateLimitError,
    UpstreamError,
)

logger = logging.getLogger("headref.integrations.http")

SENSITIVE_HEADER_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-token",
        "private-token",
        "token",
        "cookie",
        "set-cookie",
    }
)

SENSITIVE_QUERY_KEYS = frozenset(
    {
        "token",
        "api_token",
        "key",
        "api_key",
        "secret",
        "password",
    }
)


def redact_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    """Return a dictionary of headers with secrets and tokens redacted."""
    redacted: dict[str, str] = {}
    for key, val in headers.items():
        if key.lower() in SENSITIVE_HEADER_KEYS:
            redacted[key] = "***"
        else:
            redacted[key] = val
    return redacted


def sanitize_url(url: str | httpx.URL) -> str:
    """Mask sensitive query parameter values in an attempted URL."""
    url_str = str(url)
    try:
        parsed = urlparse(url_str)
        if not parsed.query:
            return url_str
        query_params = parse_qsl(parsed.query, keep_blank_values=True)
        sanitized_params: list[tuple[str, str]] = []
        for q_key, q_val in query_params:
            if q_key.lower() in SENSITIVE_QUERY_KEYS:
                sanitized_params.append((q_key, "***"))
            else:
                sanitized_params.append((q_key, q_val))
        sanitized_query = urlencode(sanitized_params)
        return urlunparse(parsed._replace(query=sanitized_query))
    except Exception:
        return url_str


def parse_retry_after(header_value: str | None) -> float | None:
    """Parse a Retry-After header into a duration in seconds."""
    if not header_value:
        return None
    header_value = header_value.strip()
    # Check if header is a numeric representation of seconds
    try:
        return max(0.0, float(header_value))
    except ValueError:
        pass

    # Check if header is an HTTP date string
    try:
        dt = parsedate_to_datetime(header_value)
        if dt.tzinfo is None:
            now = datetime.now()
        else:
            now = datetime.now(UTC)
        delay = (dt - now).total_seconds()
        return max(0.0, delay)
    except Exception:
        return None


class _TransientHttpError(Exception):
    """Internal exception wrapping a transient HTTP response (429, 5xx) to trigger retry."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"Transient HTTP {response.status_code}")
        self.response = response


class _WaitRetryAfterOrExponential(wait_base):
    """Wait strategy honouring Retry-After on 429, falling back to exponential backoff."""

    def __init__(
        self,
        min_backoff: float = 0.5,
        max_backoff: float = 10.0,
        multiplier: float = 1.0,
    ) -> None:
        self._exponential = wait_exponential(
            multiplier=multiplier,
            min=min_backoff,
            max=max_backoff,
        )

    def __call__(self, retry_state: RetryCallState) -> float:
        if retry_state.outcome and retry_state.outcome.failed:
            exc = retry_state.outcome.exception()
            if isinstance(exc, _TransientHttpError) and exc.response.status_code == 429:
                retry_after = parse_retry_after(exc.response.headers.get("retry-after"))
                if retry_after is not None and retry_after > 0:
                    # Cap retry-after at 60 seconds to avoid hanging indefinitely
                    return min(retry_after, 60.0)
        return self._exponential(retry_state)


def _is_retryable_exception(exc: BaseException) -> bool:
    """Determine whether an exception should trigger a retry attempt.

    Retries only on network transport errors and transient HTTP responses (429, 5xx).
    Never retries other 4xx errors (400, 401, 403, 404, etc.).
    """
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, _TransientHttpError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


class HttpClient:
    """Unified HTTP client with strict timeout, tenacity retry, and typed error mapping."""

    def __init__(
        self,
        base_url: str = "",
        timeout: float = HTTP_TIMEOUT_SECONDS,
        max_attempts: int = HTTP_MAX_ATTEMPTS,
        transport: httpx.BaseTransport | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.transport = transport
        self.default_headers = headers or {}

        # Set explicit connect and read timeouts (NFR-001)
        client_timeout = httpx.Timeout(
            timeout,
            connect=timeout,
            read=timeout,
            write=timeout,
            pool=timeout,
        )

        self._client = httpx.Client(
            base_url=base_url,
            timeout=client_timeout,
            transport=transport,
            headers=self.default_headers,
        )

    def close(self) -> None:
        """Close underlying httpx client."""
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Execute HTTP request with bounded retries, logging, and typed error mapping.

        Retries 429, 5xx and connection errors up to max_attempts. Never retries other 4xx.
        """
        clean_url = sanitize_url(url)
        headers = kwargs.get("headers", {})
        redacted_req_headers = redact_headers(headers)

        attempt_counter = 0

        def _execute_single_attempt() -> httpx.Response:
            nonlocal attempt_counter
            attempt_counter += 1
            logger.debug(
                "Attempt %d/%d: %s %s (headers: %s)",
                attempt_counter,
                self.max_attempts,
                method,
                clean_url,
                redacted_req_headers,
            )

            response = self._client.request(method, url, **kwargs)

            # Check if response status warrants retry
            if response.status_code in (429, 500, 502, 503, 504):
                logger.warning(
                    "Attempt %d/%d received HTTP %d for %s %s",
                    attempt_counter,
                    self.max_attempts,
                    response.status_code,
                    method,
                    clean_url,
                )
                raise _TransientHttpError(response)

            return response

        retrying = Retrying(
            stop=stop_after_attempt(self.max_attempts),
            retry=retry_if_exception(_is_retryable_exception),
            wait=_WaitRetryAfterOrExponential(),
            reraise=True,
        )

        try:
            response = retrying(_execute_single_attempt)
            logger.debug(
                "Successful %s %s with status %d after %d attempts",
                method,
                clean_url,
                response.status_code,
                attempt_counter,
            )
        except _TransientHttpError as transient_err:
            # Retries exhausted on 429 or 5xx
            return self._handle_error_response(transient_err.response, clean_url)
        except httpx.TimeoutException as timeout_err:
            logger.error(
                "Request timed out after %d attempts for %s %s: %s",
                attempt_counter,
                method,
                clean_url,
                timeout_err,
            )
            raise IntegrationTimeoutError(
                f"Request to {clean_url} timed out after {self.max_attempts} "
                f"attempts: {timeout_err}"
            ) from timeout_err
        except httpx.TransportError as transport_err:
            logger.error(
                "Transport error after %d attempts for %s %s: %s",
                attempt_counter,
                method,
                clean_url,
                transport_err,
            )
            raise UpstreamError(
                f"Transport failure connecting to {clean_url}: {transport_err}"
            ) from transport_err

        # Handle non-retryable 4xx errors
        if response.status_code >= 400:
            return self._handle_error_response(response, clean_url)

        return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """Perform a GET request."""
        return self.request("GET", url, **kwargs)

    def _handle_error_response(self, response: httpx.Response, clean_url: str) -> httpx.Response:
        """Map HTTP error status codes to typed IntegrationError instances."""
        status = response.status_code
        try:
            body = response.text
        except Exception:
            body = None

        if status == 401:
            logger.error("Authentication failed (HTTP 401) for %s", clean_url)
            raise AuthError(
                f"Authentication failed for {clean_url}",
                status_code=401,
                response_body=body,
            )

        if status == 403:
            # Check for GitHub / Jira rate-limiting indicators on 403
            remaining = response.headers.get("x-ratelimit-remaining")
            has_retry_after = "retry-after" in response.headers
            is_rate_limit_body = body and (
                "rate limit" in body.lower() or "secondary rate limit" in body.lower()
            )

            if remaining == "0" or has_retry_after or is_rate_limit_body:
                retry_after = parse_retry_after(response.headers.get("retry-after"))
                logger.warning("Rate limit reached (HTTP 403) for %s", clean_url)
                raise RateLimitError(
                    f"Rate limit exceeded (HTTP 403) for {clean_url}",
                    status_code=403,
                    response_body=body,
                    retry_after=retry_after,
                )

            logger.error("Authorization forbidden (HTTP 403) for %s", clean_url)
            raise AuthError(
                f"Authorization failed (HTTP 403) for {clean_url}",
                status_code=403,
                response_body=body,
            )

        if status == 404:
            logger.info("Resource not found (HTTP 404) for %s", clean_url)
            raise NotFoundError(
                f"Resource not found at {clean_url}",
                status_code=404,
                response_body=body,
            )

        if status == 429:
            retry_after = parse_retry_after(response.headers.get("retry-after"))
            logger.warning("Rate limit exceeded (HTTP 429) for %s", clean_url)
            raise RateLimitError(
                f"Rate limit exceeded (HTTP 429) for {clean_url}",
                status_code=429,
                response_body=body,
                retry_after=retry_after,
            )

        if status >= 500:
            logger.error("Upstream server error (HTTP %d) for %s", status, clean_url)
            raise UpstreamError(
                f"Upstream service error (HTTP {status}) for {clean_url}",
                status_code=status,
                response_body=body,
            )

        # Fallback for unexpected 4xx
        raise IntegrationError(
            f"HTTP request failed with status {status} for {clean_url}",
            status_code=status,
            response_body=body,
        )
