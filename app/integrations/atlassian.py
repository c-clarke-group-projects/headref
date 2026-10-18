"""Atlassian OAuth 2.0 (3LO) and the Jira Cloud API through api.atlassian.com (W4, DEC-023).

Scopes are read only: read:jira-work, read:jira-user, plus offline_access for a refresh
token. Atlassian rotates the refresh token on every refresh, so token-endpoint calls are
made exactly once (no retry): a retried refresh would present a token that was already
replaced and lock the connection out.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import HTTP_MAX_ATTEMPTS, HTTP_TIMEOUT_SECONDS, get_settings
from app.integrations.http import HttpClient

logger = logging.getLogger("headref.integrations.atlassian")

AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"  # noqa: S105  a URL, not a secret
API_URL = "https://api.atlassian.com"
SCOPES = "read:jira-work read:jira-user offline_access"


@dataclass(frozen=True)
class TokenSet:
    """Tokens from the Atlassian token endpoint. Never logged."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    scope: str


@dataclass(frozen=True)
class JiraSite:
    """An Atlassian site the token can reach, from accessible-resources."""

    cloud_id: str
    url: str
    name: str
    scopes: tuple[str, ...]


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """The Atlassian consent URL for the read-only Jira scopes."""
    return f"{AUTHORIZE_URL}?" + urlencode(
        {
            "audience": "api.atlassian.com",
            "client_id": client_id,
            "scope": SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "prompt": "consent",
        }
    )


def _token_set(data: dict[str, Any], now: datetime) -> TokenSet:
    return TokenSet(
        access_token=str(data["access_token"]),
        refresh_token=str(data["refresh_token"]),
        expires_at=now + timedelta(seconds=int(data.get("expires_in") or 3600)),
        scope=str(data.get("scope") or ""),
    )


class AtlassianClient:
    """Token exchange and read-only Jira calls for the configured Atlassian app."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        settings = get_settings()
        if not settings.atlassian_enabled:
            raise RuntimeError("The Atlassian app is not configured (see .env.example).")
        self._client_id = settings.atlassian_client_id
        self._client_secret = settings.atlassian_client_secret
        # 1 attempt: the token endpoint rotates the refresh token (module docstring).
        self._token_http = HttpClient(
            timeout=HTTP_TIMEOUT_SECONDS, max_attempts=1, transport=transport
        )
        self._api_http = HttpClient(
            base_url=API_URL,
            timeout=HTTP_TIMEOUT_SECONDS,
            max_attempts=HTTP_MAX_ATTEMPTS,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        """Close the underlying HTTP clients."""
        self._token_http.close()
        self._api_http.close()

    def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        """Trade the authorization code from the callback for tokens."""
        now = datetime.now(UTC)
        data = self._token_http.request(
            "POST",
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        ).json()
        return _token_set(data, now)

    def refresh(self, refresh_token: str) -> TokenSet:
        """Use the refresh token once. The returned refresh token replaces it."""
        now = datetime.now(UTC)
        data = self._token_http.request(
            "POST",
            TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
            },
        ).json()
        return _token_set(data, now)

    def accessible_sites(self, access_token: str) -> list[JiraSite]:
        """Atlassian sites the token can reach that have the Jira read scopes."""
        data = self._api_http.get(
            "/oauth/token/accessible-resources",
            headers={"Authorization": f"Bearer {access_token}"},
        ).json()
        sites = [
            JiraSite(
                cloud_id=str(r["id"]),
                url=str(r.get("url") or ""),
                name=str(r.get("name") or ""),
                scopes=tuple(r.get("scopes") or ()),
            )
            for r in data
        ]
        return [s for s in sites if "read:jira-work" in s.scopes]

    def list_projects(self, cloud_id: str, access_token: str) -> list[dict[str, str]]:
        """Jira projects on a site, as {id, key, name}, following isLast pages."""
        projects: list[dict[str, str]] = []
        start_at = 0
        while True:
            data = self._api_http.get(
                f"/ex/jira/{cloud_id}/rest/api/3/project/search",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"startAt": start_at, "maxResults": 50, "orderBy": "key"},
            ).json()
            values = data.get("values") or []
            projects.extend(
                {"id": str(p["id"]), "key": str(p["key"]), "name": str(p.get("name") or "")}
                for p in values
            )
            if data.get("isLast", True) or not values:
                return projects
            start_at += len(values)
