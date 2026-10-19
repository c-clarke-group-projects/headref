"""Sign-in, sign-out and the home redirect (DEC-021, DEC-026).

GitHub OAuth only identifies the person: it asks for no scopes, and its token is used once
to read the profile and then dropped. Each account acts as its own app_user, never as a
person found in a workspace's sources. The team pages and team API from the single-team
MVP were removed in W10 (DEC-026); every page now lives under a workspace.
"""

import hmac
import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import exists, select
from sqlalchemy.orm import Session
from starlette.routing import Match

from app.config import get_settings
from app.models.canonical import AppUser, Organization
from app.models.workspace import Account, Workspace
from app.web.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    DbDep,
    cookie_secure,
    sign_session,
)
from app.web.ui_format import templates
from app.web.workspaces import upsert_account

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Headref sign-in"])

# Organization that holds the app_user each signed-in account acts as. Kept apart from
# every workspace's organization, so an account is never 1 of the people it looks at.
ACCOUNTS_ORGANIZATION = "Headref accounts"


@router.get("/")
def home() -> Response:
    """Home is the portfolio of workspaces; it answers 401 to the sign-in page."""
    return RedirectResponse(url="/workspaces", status_code=status.HTTP_303_SEE_OTHER)


def account_user(db: Session, gh_user: dict[str, Any]) -> AppUser | None:
    """The app_user a GitHub sign-in acts as, or None when that user is inactive.

    An existing account keeps its app_user, unless that app_user belongs to a workspace's
    organization (an earlier sign-in could bind to a synced person); then, as for a new
    account, a fresh app_user is made in the accounts organization. The caller commits.
    """
    account = db.scalar(select(Account).where(Account.github_user_id == int(gh_user["id"])))
    if account is not None and account.app_user_id is not None:
        user = db.get(AppUser, account.app_user_id)
        in_workspace = user is not None and (
            db.scalar(select(Workspace.id).where(Workspace.organization_id == user.organization_id))
            is not None
        )
        if user is not None and not in_workspace:
            return user if user.is_active else None
    # A workspace's organization is named after the workspace, so a workspace called
    # "Headref accounts" must not become the accounts organization: only an
    # organization no workspace uses qualifies, the oldest first.
    org = db.scalar(
        select(Organization)
        .where(
            Organization.name == ACCOUNTS_ORGANIZATION,
            ~exists().where(Workspace.organization_id == Organization.id),
        )
        .order_by(Organization.id)
        .limit(1)
    )
    if org is None:
        org = Organization(name=ACCOUNTS_ORGANIZATION)
        db.add(org)
        db.flush()
    user = AppUser(
        organization_id=org.id,
        display_name=str(gh_user.get("name") or gh_user.get("login") or "GitHub user"),
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def safe_next(value: str | None) -> str:
    """Accept only a same site absolute path as a post-login destination.

    ``next`` arrives from the query string and from the login form, so it is
    attacker controlled. Anything that could leave this origin becomes "/".
    Rejected: an absolute URL with a scheme, a protocol relative "//host" or its
    backslash variants, and anything that is not rooted at "/".
    """
    if not value or not value.startswith("/"):
        return "/"
    # Browsers strip tabs and newlines from a Location value, so "/<TAB>//host"
    # would resolve protocol relative. Starlette percent encodes them today, but
    # that is its behaviour to change, not a guarantee this function should lean on.
    normalised = "".join(c for c in value if c not in "\t\r\n").replace("\\", "/")
    if not normalised.startswith("/") or normalised.startswith("//"):
        return "/"
    return value


def is_openable_page(application: Any, path: str) -> bool:
    """True if a GET to path reaches a route, so redirecting there cannot answer 405.

    A stale sign-in link can carry next=<a POST-only path>, for example a Connect
    button's action. Redirects are always GETs, so such a next would land on a 405.
    """
    scope = {"type": "http", "path": path.split("?", 1)[0], "method": "GET", "root_path": ""}
    return any(route.matches(scope)[0] == Match.FULL for route in application.routes)


OAUTH_STATE_COOKIE = "headref_oauth_state"
OAUTH_NEXT_COOKIE = "headref_oauth_next"
# Set by sign-ins before W10, which kept the GitHub token in a cookie. Only deleted now.
LEGACY_GITHUB_TOKEN_COOKIE = "headref_gh_token"  # noqa: S105  a cookie name, not a secret


@router.get("/login", response_class=HTMLResponse)
def get_login_page(request: Request) -> HTMLResponse:
    """Render the sign-in page. GitHub OAuth is the only way to sign in."""
    settings = get_settings()
    next_url = safe_next(request.query_params.get("next"))
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "next": next_url,
            "error": error,
            "oauth_enabled": settings.github_oauth_enabled,
        },
    )


@router.get("/auth/github/login")
def github_login(
    request: Request,
    next: str = "/",
) -> Response:
    """Redirect to GitHub OAuth authorize endpoint with CSRF state token."""
    settings = get_settings()
    if not settings.github_oauth_enabled:
        err = (
            "GitHub OAuth is not configured. "
            "Set GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET in .env."
        )
        return RedirectResponse(
            url=f"/login?error={urlencode({'e': err})[2:]}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    state = secrets.token_urlsafe(32)
    callback_url = str(request.url_for("github_callback"))
    params = {
        "client_id": settings.github_oauth_client_id,
        "redirect_uri": callback_url,
        # No scopes: sign-in needs only who the person is. Data access goes through the
        # GitHub App installed per workspace (DEC-022), never through this token.
        "state": state,
    }
    authorize_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    redirect = RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
    )
    redirect.set_cookie(
        OAUTH_NEXT_COOKIE,
        safe_next(next),
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
    )
    return redirect


@router.get("/auth/github/callback", name="github_callback")
async def github_callback(
    request: Request,
    db: DbDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> Response:
    """Handle OAuth redirect callback from GitHub, exchange code, and map identity."""
    settings = get_settings()

    def _login_err(msg: str) -> Response:
        res = RedirectResponse(
            url=f"/login?error={urlencode({'e': msg})[2:]}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
        res.delete_cookie(OAUTH_STATE_COOKIE)
        res.delete_cookie(OAUTH_NEXT_COOKIE)
        return res

    if error:
        err_msg = error_description or error or "OAuth authorization failed"
        logger.warning("GitHub OAuth callback returned error: %s", err_msg)
        return _login_err(f"GitHub sign-in error: {err_msg}")

    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
        logger.warning("GitHub OAuth CSRF mismatch: state=%s cookie=%s", state, cookie_state)
        return _login_err("Invalid or expired OAuth state (CSRF verification failed).")

    if not code:
        return _login_err("Missing authorization code from GitHub.")

    next_url = safe_next(request.cookies.get(OAUTH_NEXT_COOKIE, "/"))
    if not is_openable_page(request.app, next_url):
        next_url = "/"

    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as http_client:
            token_resp = await http_client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.github_oauth_client_id,
                    "client_secret": settings.github_oauth_client_secret,
                    "code": code,
                    "redirect_uri": str(request.url_for("github_callback")),
                },
            )
            if token_resp.status_code != 200:
                logger.error(
                    "GitHub token exchange status %d: %s",
                    token_resp.status_code,
                    token_resp.text,
                )
                return _login_err(
                    f"GitHub token exchange returned status {token_resp.status_code}."
                )

            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                desc = (
                    token_data.get("error_description")
                    or token_data.get("error")
                    or "Unknown error"
                )
                logger.error("GitHub token exchange error: %s", desc)
                return _login_err(f"GitHub error: {desc}")

            user_resp = await http_client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Headref-Intelligence-Agent",
                },
            )
            if user_resp.status_code != 200:
                logger.error(
                    "GitHub user fetch status %d: %s",
                    user_resp.status_code,
                    user_resp.text,
                )
                return _login_err(
                    f"Failed to fetch user profile from GitHub (HTTP {user_resp.status_code})."
                )

            gh_user = user_resp.json()
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        logger.error("Network error during GitHub OAuth: %s", exc)
        return _login_err("Network error contacting GitHub. Please try again.")

    gh_user_id = gh_user.get("id")
    gh_login = gh_user.get("login") or ""
    if not gh_user_id:
        return _login_err("GitHub profile is missing a numeric user ID.")

    user = account_user(db, gh_user)
    if user is None:
        logger.warning("sign-in refused, inactive account: gh_user_id=%s", gh_user_id)
        return _login_err("This headref account is inactive.")
    account = upsert_account(db, gh_user, user.id)
    db.commit()

    logger.info(
        "GitHub OAuth login successful: user_id=%s account_id=%s gh_login=%s",
        user.id,
        account.id,
        gh_login,
    )
    # Home after sign-in is the portfolio of workspaces (DEC-021).
    dest_url = "/workspaces" if next_url == "/" else next_url
    redirect = RedirectResponse(url=dest_url, status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        SESSION_COOKIE,
        sign_session(user.id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
    )
    redirect.delete_cookie(LEGACY_GITHUB_TOKEN_COOKIE)
    redirect.delete_cookie(OAUTH_STATE_COOKIE)
    redirect.delete_cookie(OAUTH_NEXT_COOKIE)
    return redirect


@router.post("/logout")
def logout_redirect() -> Response:
    """Sign out and go to the sign-in page. POST only, so a link cannot sign anyone out."""
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(SESSION_COOKIE)
    redirect.delete_cookie(LEGACY_GITHUB_TOKEN_COOKIE)
    return redirect
