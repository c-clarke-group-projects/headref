"""GitHub App authentication and installation access (W3, DEC-022).

The App signs a short-lived JWT with its private key, exchanges it for an installation
access token, and reads repositories with that token. Installation tokens live only in
this process's memory, are reused until 5 minutes before they expire, and are never
stored, logged or sent to a template.

Read only: the App is registered with Metadata, Contents, Pull requests, Checks and
Commit statuses at read level, and nothing here calls a write endpoint.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
import jwt

from app.config import HTTP_MAX_ATTEMPTS, HTTP_TIMEOUT_SECONDS, get_settings
from app.integrations.errors import IntegrationError
from app.integrations.http import HttpClient

logger = logging.getLogger("headref.integrations.github_app")

GITHUB_API = "https://api.github.com"
# GitHub accepts an App JWT for at most 10 minutes. Issue it 60 seconds in the past to
# absorb clock drift, and for 9 minutes in total.
JWT_BACKDATE_SECONDS = 60
JWT_LIFETIME_SECONDS = 540
# Reuse an installation token until it is this close to expiring (they last 1 hour).
TOKEN_REFRESH_MARGIN_SECONDS = 300

_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@dataclass(frozen=True)
class Installation:
    """Who an installation belongs to, from GET /app/installations/{id}."""

    installation_id: int
    account_id: int
    account_login: str
    account_type: str  # "User" or "Organization"
    repository_selection: str  # "all" or "selected"
    suspended: bool


def make_app_jwt(app_id: str, private_key_pem: str, now: float | None = None) -> str:
    """Return a JWT that authenticates as the GitHub App itself."""
    issued = int(now if now is not None else time.time()) - JWT_BACKDATE_SECONDS
    payload = {"iat": issued, "exp": issued + JWT_LIFETIME_SECONDS, "iss": str(app_id)}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


class GitHubAppClient:
    """App-level calls and installation tokens for the configured GitHub App."""

    def __init__(
        self,
        app_id: str | None = None,
        private_key_pem: str | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Any = time.time,
    ) -> None:
        settings = get_settings()
        self._app_id = app_id or settings.github_app_id
        self._private_key = private_key_pem or (
            settings.github_app_private_key() if settings.github_app_enabled else ""
        )
        if not self._app_id or not self._private_key:
            raise RuntimeError("The GitHub App is not configured (see .env.example).")
        self._clock = clock
        self._http = HttpClient(
            base_url=GITHUB_API,
            timeout=HTTP_TIMEOUT_SECONDS,
            max_attempts=HTTP_MAX_ATTEMPTS,
            transport=transport,
            headers=_API_HEADERS,
        )
        self._tokens: dict[int, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def _app_headers(self) -> dict[str, str]:
        token = make_app_jwt(self._app_id, self._private_key, now=self._clock())
        return {"Authorization": f"Bearer {token}"}

    def get_app(self) -> dict[str, Any]:
        """GET /app: proves the App id and private key belong together."""
        return self._http.get("/app", headers=self._app_headers()).json()

    def get_installation(self, installation_id: int) -> Installation:
        """Who owns an installation. Raises NotFoundError if it is not this App's."""
        data = self._http.get(
            f"/app/installations/{int(installation_id)}", headers=self._app_headers()
        ).json()
        account = data.get("account") or {}
        return Installation(
            installation_id=int(data["id"]),
            account_id=int(account["id"]),
            account_login=str(account.get("login") or ""),
            account_type=str(account.get("type") or ""),
            repository_selection=str(data.get("repository_selection") or ""),
            suspended=data.get("suspended_at") is not None,
        )

    def installation_token(self, installation_id: int) -> str:
        """Return a valid installation access token, minting a new one when needed."""
        installation_id = int(installation_id)
        with self._lock:
            cached = self._tokens.get(installation_id)
            if cached and cached[1] - self._clock() > TOKEN_REFRESH_MARGIN_SECONDS:
                return cached[0]
            data = self._http.request(
                "POST",
                f"/app/installations/{installation_id}/access_tokens",
                headers=self._app_headers(),
            ).json()
            token = str(data["token"])
            expires_at = datetime.fromisoformat(
                str(data["expires_at"]).replace("Z", "+00:00")
            ).timestamp()
            self._tokens[installation_id] = (token, expires_at)
            logger.info("minted installation token: installation_id=%s", installation_id)
            return token

    def list_installation_repositories(self, installation_id: int) -> list[dict[str, Any]]:
        """Repositories the installation can read, as {id, full_name, private}."""
        headers = {"Authorization": f"Bearer {self.installation_token(installation_id)}"}
        repos: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self._http.get(
                "/installation/repositories",
                headers=headers,
                params={"per_page": 100, "page": page},
            ).json()
            batch = data.get("repositories") or []
            repos.extend(
                {
                    "id": int(r["id"]),
                    "full_name": str(r["full_name"]),
                    "private": bool(r.get("private")),
                }
                for r in batch
            )
            if len(repos) >= int(data.get("total_count") or 0) or not batch:
                return repos
            page += 1


def user_can_attach(installation: Installation, user_token: str, github_user_id: int) -> bool:
    """True if the signed-in GitHub user owns the installation or administers its org.

    Without this check, anyone could pass another person's installation_id to the setup
    URL and attach their repositories to a workspace. user_token is the signed-in user's
    OAuth token, which has the read:org scope.
    """
    if installation.account_type == "User":
        return installation.account_id == github_user_id
    if installation.account_type != "Organization":
        return False
    with HttpClient(
        base_url=GITHUB_API,
        timeout=HTTP_TIMEOUT_SECONDS,
        max_attempts=HTTP_MAX_ATTEMPTS,
        headers={**_API_HEADERS, "Authorization": f"Bearer {user_token}"},
    ) as http:
        try:
            # The cookie token must belong to the signed-in account, or a token taken
            # from an org admin would let someone else attach that org.
            token_user = http.get("/user").json()
            if int(token_user.get("id") or 0) != github_user_id:
                logger.warning("org attach refused: token does not belong to the signed-in account")
                return False
            membership = http.get(f"/user/memberships/orgs/{installation.account_login}").json()
        except IntegrationError as exc:
            # Not a member, or the token lacks read:org: either way, do not attach.
            logger.warning(
                "org membership check failed (%s): org=%s installation_id=%s",
                type(exc).__name__,
                installation.account_login,
                installation.installation_id,
            )
            return False
    return membership.get("state") == "active" and membership.get("role") == "admin"
