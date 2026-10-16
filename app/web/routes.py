"""Web and API routes for Headref (Phase 5, Issues #33-#38, #49).

Every member route takes the MemberGuard dependency from app.web.auth (FR-028).
Team routes enforce actor team membership. All question types are validated
strictly against DEC-007 (current_work, blockers, risks).
"""

import asyncio
import hmac
import logging
import re
import secrets
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import parse_qs, urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, or_, select

from app.agent.orchestrator import run_agent
from app.agent.planner import build_plan
from app.agent.team_overview import generate_team_overview
from app.config import get_settings
from app.db import get_sync_session
from app.integrations.github import GitHubClient
from app.models.canonical import AppUser, Organization, Repository, Team
from app.models.identity import IdentityLink, UnmatchedEntity
from app.models.operations import SyncRun
from app.scheduler import resolve_team_scopes, run_scheduled_tick
from app.schemas.errors import ToolFailure
from app.sync import sync_github
from app.schemas.insight import MemberInsight, TeamOverview
from app.schemas.sync import SyncStatus, SyncStatusResponse, SyncTriggerResponse
from app.schemas.tools import (
    GetSourceHealthInput,
    LinkedAccount,
    SourceHealthOut,
    TeamMemberOut,
)
from app.tools.get_source_health import get_source_health
from app.web.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
    ActorDep,
    DbDep,
    MemberGuard,
    load_user,
    sign_session,
)
from app.web.ui_format import signal_rows, templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Headref UI & APIs"])


# ---------------------------------------------------------------------------
# API Endpoints (P5-001, P5-007)
# ---------------------------------------------------------------------------


@router.get("/api/members/{member_id}/insight", response_model=MemberInsight)
async def get_member_insight(
    member_id: int,
    question: str = Query(..., description="Question type: current_work, blockers, or risks"),
    member: MemberGuard = None,
    actor: ActorDep = None,
    db: DbDep = None,
) -> MemberInsight:
    """Retrieve member insight for a specific question type (FR-014, FR-028, DEC-007)."""
    plan = build_plan(question)
    return await run_agent(
        session=db,
        subject_user_id=member.id,
        team_id=member.team_id,
        question_type=plan.question_type,
        actor_user_id=actor.id,
    )


@router.get("/api/teams/{team_id}/overview", response_model=TeamOverview)
def get_team_overview(
    team_id: int,
    actor: ActorDep,
    db: DbDep,
) -> TeamOverview:
    """Retrieve team overview status and member breakdowns (FR-025, Gap G6)."""
    if actor.team_id != team_id or actor.team_id is None:
        logger.warning(
            "team access denied: actor_id=%s actor_team=%s requested_team=%s",
            actor.id,
            actor.team_id,
            team_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to view overview for this team",
        )
    return generate_team_overview(session=db, team_id=team_id)


# Keeping a reference stops the event loop garbage collecting a running background
# sync before it finishes. asyncio only holds a weak reference to a task.
_BACKGROUND_SYNCS: set[asyncio.Task[None]] = set()


def _do_background_sync(team_id: int) -> None:
    """Run a sync tick off the request thread, logging what happened either way."""
    session = get_sync_session()
    try:
        results = run_scheduled_tick(session, team_id=team_id)
        logger.info(
            "Background sync finished for team=%d github=%s jira=%s",
            team_id,
            results.get("github"),
            results.get("jira"),
        )
    except Exception as exc:
        logger.error("Background sync failed for team=%d: %s", team_id, exc)
    finally:
        session.close()


def _scope_filter(team_id: int) -> Any:
    """SQL predicate matching only the sync_run rows belonging to a team's scopes."""
    gh_scopes, jira_scope_list = resolve_team_scopes(team_id)
    return or_(
        and_(SyncRun.source == "github", SyncRun.scope.in_(gh_scopes)),
        and_(SyncRun.source == "jira", SyncRun.scope.in_(jira_scope_list)),
    )


def _to_status(run: SyncRun) -> SyncStatus:
    return SyncStatus(
        run_id=run.id,
        source=run.source,
        scope=run.scope,
        status=run.status,
        error_type=run.error_type,
        items_skipped=run.items_skipped or 0,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _running_runs(db: Any, team_id: int) -> list[SyncRun]:
    """Rows still marked running for this team's scopes."""
    if db is None:
        return []
    return list(
        db.scalars(select(SyncRun).where(SyncRun.status == "running", _scope_filter(team_id))).all()
    )


@router.post(
    "/api/teams/{team_id}/sync",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SyncTriggerResponse,
)
async def trigger_team_sync(
    team_id: int,
    actor: ActorDep,
    db: DbDep,
) -> SyncTriggerResponse:
    """Trigger an on-demand sync for a team (Issue #49, P5-007, DEC-016).

    Returns 202 in under 500 ms and never waits for the sync. If a run is already in
    flight for this team's scopes, that run is returned and no second run starts.
    """
    if actor.team_id != team_id or actor.team_id is None:
        logger.warning(
            "refresh denied: actor_id=%s actor_team=%s requested_team=%s",
            actor.id,
            actor.team_id,
            team_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only refresh your own team.",
        )

    in_flight = _running_runs(db, team_id)
    if in_flight:
        logger.info(
            "refresh skipped for team=%d, %d run(s) already in flight", team_id, len(in_flight)
        )
        return SyncTriggerResponse(
            started=False,
            all_finished=False,
            runs=[_to_status(r) for r in in_flight],
        )

    task = asyncio.create_task(asyncio.to_thread(_do_background_sync, team_id))
    _BACKGROUND_SYNCS.add(task)
    task.add_done_callback(_BACKGROUND_SYNCS.discard)
    logger.info("refresh started for team=%d", team_id)

    # No sync_run row exists yet. Reporting one here would state a fact the database
    # does not hold (rule 9), so the list is empty and the UI says "starting".
    return SyncTriggerResponse(started=True, all_finished=False, runs=[])


@router.get("/api/teams/{team_id}/sync/status", response_model=SyncStatusResponse)
def get_team_sync_status(
    team_id: int,
    actor: ActorDep,
    db: DbDep,
) -> SyncStatusResponse:
    """Poll sync status for team refresh (Issue #49, P5-007).

    Reports the most recent run per source and scope. A scope with no recorded run is
    simply absent. An empty list means nothing has been recorded, not that a sync is
    running.
    """
    if actor.team_id != team_id or actor.team_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only refresh your own team.",
        )

    latest: list[SyncRun] = []
    if db:
        gh_scopes, jira_scope_list = resolve_team_scopes(team_id)
        for source, scopes in (("github", gh_scopes), ("jira", jira_scope_list)):
            for scope in scopes:
                run = db.scalar(
                    select(SyncRun)
                    .where(SyncRun.source == source, SyncRun.scope == scope)
                    .order_by(SyncRun.started_at.desc())
                    .limit(1)
                )
                if run:
                    latest.append(run)

    all_finished = bool(latest and all(r.status != "running" for r in latest))
    return SyncStatusResponse(all_finished=all_finished, runs=[_to_status(r) for r in latest])


# ---------------------------------------------------------------------------
# UI Page Routes (P5-002, P5-003, P5-004, P5-005, P5-006)
# ---------------------------------------------------------------------------


def _render_team_page(
    request: Request,
    actor: Any,
    db: Any,
    team_id: int | None,
) -> HTMLResponse:
    """Team overview dashboard (P5-004, Issue #36)."""
    target_team_id = actor.team_id if team_id is None else team_id
    if target_team_id is None or actor.team_id != target_team_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not allowed to view overview for this team",
        )

    overview = generate_team_overview(session=db, team_id=target_team_id)
    return templates.TemplateResponse(
        request=request,
        name="team.html",
        context={
            "request": request,
            "actor": actor,
            "overview": overview,
            "team_name": overview.team_name,
            "team_id": target_team_id,
            "unmatched_count": overview.unmatched_count,
            "nav_current": "team",
            "now": datetime.now(UTC),
        },
    )


@router.get("/", response_class=HTMLResponse)
def get_home_page(request: Request, actor: ActorDep, db: DbDep) -> HTMLResponse:
    """The signed in actor's own team overview.

    Separate from the /teams/{team_id} handler so that "/" takes no team parameter
    at all. Stacked on 1 handler, team_id was accepted from the query string here.
    """
    return _render_team_page(request, actor, db, None)


@router.get("/teams/{team_id}", response_class=HTMLResponse)
def get_team_page(
    request: Request,
    team_id: int,
    actor: ActorDep,
    db: DbDep,
) -> HTMLResponse:
    """Team overview dashboard for a named team (P5-004, Issue #36)."""
    return _render_team_page(request, actor, db, team_id)


def _build_team_member_out(db: DbDep, member: Any) -> TeamMemberOut:
    links = []
    if db:
        links = db.scalars(
            select(IdentityLink)
            .where(IdentityLink.app_user_id == member.id)
            .order_by(IdentityLink.id)
        ).all()
    linked_accounts = [
        LinkedAccount(
            integration=link.integration,  # type: ignore[arg-type]
            external_handle=link.external_handle,
            match_method=link.match_method,  # type: ignore[arg-type]
            confidence=link.confidence,  # type: ignore[arg-type]
        )
        for link in links
    ]
    return TeamMemberOut(
        user_id=member.id,
        display_name=member.display_name,
        role_label=getattr(member, "role_label", None),
        is_active=getattr(member, "is_active", True),
        linked_accounts=linked_accounts,
    )


@router.get("/members/{member_id}", response_class=HTMLResponse)
async def get_member_page(
    request: Request,
    member_id: int,
    question: str = "current_work",
    member: MemberGuard = None,
    actor: ActorDep = None,
    db: DbDep = None,
) -> HTMLResponse:
    """Member profile page (P5-002, Issue #34)."""
    if question not in ("current_work", "blockers", "risks"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown question type",
        )

    member_out = _build_team_member_out(db, member)
    insight = await run_agent(
        session=db,
        subject_user_id=member.id,
        team_id=member.team_id,
        question_type=question,
        actor_user_id=actor.id,
    )

    signals = signal_rows(question, get_settings(), insight.source_health)
    unmatched_count = 0
    team_name = f"Team {member.team_id}"
    if db:
        unmatched_count = (
            db.scalar(
                select(func.count(UnmatchedEntity.id)).where(
                    UnmatchedEntity.resolved_app_user_id.is_(None)
                )
            )
            or 0
        )
        if member.team_id:
            team_obj = db.get(Team, member.team_id)
            if team_obj:
                team_name = team_obj.name

    return templates.TemplateResponse(
        request=request,
        name="member.html",
        context={
            "request": request,
            "actor": actor,
            "member": member_out,
            "insight": insight,
            "question": question,
            "is_own": actor.id == member.id,
            "signals": signals,
            "unmatched_count": unmatched_count,
            "team_name": team_name,
            "team_id": member.team_id,
            "nav_current": "member",
            "now": datetime.now(UTC),
        },
    )


@router.get("/members/{member_id}/panel", response_class=HTMLResponse)
async def get_member_panel(
    request: Request,
    member_id: int,
    question: str = "current_work",
    member: MemberGuard = None,
    actor: ActorDep = None,
    db: DbDep = None,
) -> HTMLResponse:
    """Question panel HTML fragment for progressive enhancement tab swap."""
    if question not in ("current_work", "blockers", "risks"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown question type",
        )

    member_out = _build_team_member_out(db, member)
    insight = await run_agent(
        session=db,
        subject_user_id=member.id,
        team_id=member.team_id,
        question_type=question,
        actor_user_id=actor.id,
    )
    signals = signal_rows(question, get_settings(), insight.source_health)

    return templates.TemplateResponse(
        request=request,
        name="partials/question_panel.html",
        context={
            "request": request,
            "member": member_out,
            "insight": insight,
            "question": question,
            "signals": signals,
            "now": datetime.now(UTC),
        },
    )


def _unmatched_source_health(db: Any, team_id: int | None) -> list[SourceHealthOut]:
    """Per-source health for the unmatched page, with a typed failure never hidden.

    A tool failure is not an empty result (rule 9). When the health tool cannot answer,
    every source is reported unavailable with the error type, so the page says "I could
    not look" rather than showing nothing.
    """
    sources: list[Literal["github", "jira"]] = ["github", "jira"]
    if db is None or team_id is None:
        return []

    result = get_source_health(
        session=db,
        input_data=GetSourceHealthInput(team_id=team_id, sources=sources),
    )
    if isinstance(result, ToolFailure):
        logger.warning(
            "Source health unavailable for the unmatched page, team=%s error_type=%s",
            team_id,
            result.error_type,
        )
        return [
            SourceHealthOut(source=src, state="unavailable", last_error_type=result.error_type)
            for src in sources
        ]
    return list(result.sources)


@router.get("/admin/unmatched", response_class=HTMLResponse)
def get_unmatched_page(
    request: Request,
    actor: ActorDep,
    db: DbDep,
) -> HTMLResponse:
    """Unmatched identity queue administrator view (P5-005, Issue #37)."""
    rows = []
    if db:
        rows = db.scalars(
            select(UnmatchedEntity)
            .where(UnmatchedEntity.resolved_app_user_id.is_(None))
            .order_by(UnmatchedEntity.last_seen_at.desc())
        ).all()

    source_health = _unmatched_source_health(db, actor.team_id)
    team_name = "Team"
    if db and actor.team_id:
        team_obj = db.get(Team, actor.team_id)
        if team_obj:
            team_name = team_obj.name

    return templates.TemplateResponse(
        request=request,
        name="unmatched.html",
        context={
            "request": request,
            "actor": actor,
            "rows": rows,
            "source_health": source_health,
            "team_id": actor.team_id,
            "team_name": team_name,
            "unmatched_count": len(rows),
            "nav_current": "unmatched",
            "now": datetime.now(UTC),
        },
    )


# ---------------------------------------------------------------------------
# Login / Logout Page Routes (Login stub)
# ---------------------------------------------------------------------------


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


OAUTH_STATE_COOKIE = "headref_oauth_state"
OAUTH_NEXT_COOKIE = "headref_oauth_next"
GITHUB_TOKEN_COOKIE = "headref_gh_token"
REPO_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")



@router.get("/login", response_class=HTMLResponse)
def get_login_page(
    request: Request,
    db: DbDep,
) -> HTMLResponse:
    """Render login page with GitHub authentication and fallback demo option."""
    settings = get_settings()
    users = []
    if db:
        users = db.scalars(
            select(AppUser).where(AppUser.is_active.is_(True)).order_by(AppUser.display_name)
        ).all()
    next_url = safe_next(request.query_params.get("next"))
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "request": request,
            "users": users,
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
        err = "GitHub OAuth is not configured. Set GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET in .env."
        return RedirectResponse(
            url=f"/login?error={urlencode({'e': err})[2:]}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    state = secrets.token_urlsafe(32)
    callback_url = str(request.url_for("github_callback"))
    params = {
        "client_id": settings.github_oauth_client_id,
        "redirect_uri": callback_url,
        "scope": "read:user,repo,read:org",
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
    )
    redirect.set_cookie(
        OAUTH_NEXT_COOKIE,
        safe_next(next),
        max_age=600,
        httponly=True,
        samesite="lax",
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
                return _login_err(f"GitHub token exchange returned status {token_resp.status_code}.")

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

    link = db.scalar(
        select(IdentityLink).where(
            IdentityLink.integration == "github",
            IdentityLink.external_id == str(gh_user_id),
        )
    )
    if not link and gh_login:
        link = db.scalar(
            select(IdentityLink).where(
                IdentityLink.integration == "github",
                func.lower(IdentityLink.external_handle) == gh_login.lower(),
            )
        )

    if not link:
        team = db.scalar(select(Team))
        org = db.scalar(select(Organization))
        if not org:
            org = Organization(name="Default Organization")
            db.add(org)
            db.flush()
        if not team:
            team = Team(organization_id=org.id, name="Core Team")
            db.add(team)
            db.flush()

        user = AppUser(
            organization_id=org.id,
            team_id=team.id,
            display_name=gh_user.get("name") or gh_login,
            role_label="Engineer",
            is_active=True,
        )
        db.add(user)
        db.flush()

        link = IdentityLink(
            app_user_id=user.id,
            integration="github",
            external_id=str(gh_user_id),
            external_handle=gh_login,
            match_method="oauth",
            confidence="HIGH",
            verified_at=datetime.now(UTC),
        )
        db.add(link)
        db.commit()
    else:
        user = db.get(AppUser, link.app_user_id)
        if not user or not user.is_active:
            logger.warning("Mapped user %s is inactive or missing", link.app_user_id)
            return _login_err("Mapped team member account is inactive or not found.")
        if user.team_id is None:
            team = db.scalar(select(Team))
            if team:
                user.team_id = team.id
                db.commit()

    logger.info("GitHub OAuth login successful: user_id=%s gh_login=%s", user.id, gh_login)
    dest_url = "/repos" if next_url == "/" else next_url
    redirect = RedirectResponse(url=dest_url, status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        SESSION_COOKIE,
        sign_session(user.id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    redirect.set_cookie(
        GITHUB_TOKEN_COOKIE,
        access_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    redirect.delete_cookie(OAUTH_STATE_COOKIE)
    redirect.delete_cookie(OAUTH_NEXT_COOKIE)
    return redirect



@router.post("/login")
async def post_login(
    request: Request,
    response: Response,
    db: DbDep,
) -> Response:
    """Submit login stub form and set signed session cookie."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
        user_id = int(data.get("user_id", 0))
        next_url = safe_next(str(data.get("next", "/")))
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        user_id = int(parsed.get("user_id", ["0"])[0])
        next_url = safe_next(parsed.get("next", ["/"])[0])

    user = load_user(db, user_id)
    if user is None or not user.is_active:
        users = []
        if db:
            users = db.scalars(
                select(AppUser).where(AppUser.is_active.is_(True)).order_by(AppUser.display_name)
            ).all()
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "request": request,
                "users": users,
                "next": next_url,
                "error": "Unknown or inactive user",
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    redirect = RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        SESSION_COOKIE,
        sign_session(user.id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return redirect


@router.post("/logout")
@router.get("/logout")
def logout_redirect() -> Response:
    """Log out and redirect to login page."""
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(SESSION_COOKIE)
    redirect.delete_cookie(GITHUB_TOKEN_COOKIE)
    return redirect


@router.get("/repos", response_class=HTMLResponse)
def get_repos_page(
    request: Request,
    actor: ActorDep,
    db: DbDep,
) -> HTMLResponse:
    """Repository and organization selection page for Headref."""
    settings = get_settings()
    token = request.cookies.get(GITHUB_TOKEN_COOKIE) or (settings.github_token if settings else None)

    orgs: list[dict[str, Any]] = []
    repos: list[dict[str, Any]] = []
    fetch_error: str | None = None

    if token:
        try:
            with GitHubClient(token=token) as gh:
                orgs = gh.list_organizations()
                repos = gh.list_repositories()
        except Exception as exc:
            logger.warning("Failed to fetch GitHub organizations or repos: %s", exc)
            fetch_error = "Could not fetch fresh repositories from GitHub. You may still manually specify a repository."
    else:
        fetch_error = "No GitHub access token available. Please sign in with GitHub to view your repositories."

    db_repos = db.scalars(select(Repository)).all() if db else []
    synced_names = {r.full_name.lower(): r for r in db_repos}

    for r in repos:
        fn = (r.get("full_name") or "").lower()
        r["is_synced"] = fn in synced_names

    known_fetched = {(r.get("full_name") or "").lower() for r in repos}
    for dbr in db_repos:
        if dbr.full_name.lower() not in known_fetched:
            repos.append({
                "full_name": dbr.full_name,
                "name": dbr.full_name.split("/")[-1],
                "owner": {"login": dbr.full_name.split("/")[0]},
                "description": "Configured repository in database",
                "private": False,
                "is_synced": True,
            })

    unmatched_count = 0
    team_name = f"Team {actor.team_id}" if actor.team_id else "Headref"
    if db:
        unmatched_count = db.scalar(
            select(func.count(UnmatchedEntity.id)).where(UnmatchedEntity.status == "pending")
        ) or 0
        if actor.team_id:
            team_obj = db.get(Team, actor.team_id)
            if team_obj:
                team_name = team_obj.name

    return templates.TemplateResponse(
        request=request,
        name="repos.html",
        context={
            "request": request,
            "actor": actor,
            "team_name": team_name,
            "unmatched_count": unmatched_count,
            "nav_current": "repos",
            "orgs": orgs,
            "repos": repos,
            "synced_repos": [r.full_name for r in db_repos],
            "fetch_error": fetch_error,
            "error": request.query_params.get("error"),
            "now": datetime.now(UTC),
        },
    )


@router.post("/repos/select")
async def select_repository(
    request: Request,
    actor: ActorDep,
    db: DbDep,
) -> Response:
    """Select a GitHub repository to ingest and track with team updates."""
    content_type = request.headers.get("content-type", "")
    is_json = "application/json" in content_type
    if is_json:
        data = await request.json()
        repo_full_name = (data.get("repo_full_name") or data.get("repo") or "").strip()
    else:
        body = (await request.body()).decode("utf-8")
        parsed = parse_qs(body)
        repo_full_name = (parsed.get("repo_full_name", [""])[0] or parsed.get("repo", [""])[0]).strip()

    if not repo_full_name or not REPO_NAME_REGEX.match(repo_full_name):
        err = "Invalid repository format. Please use 'owner/repo' (e.g. 'octocat/Hello-World')."
        if is_json:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err)
        return RedirectResponse(
            url=f"/repos?error={urlencode({'e': err})[2:]}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    settings = get_settings()
    token = request.cookies.get(GITHUB_TOKEN_COOKIE) or (settings.github_token if settings else None)

    try:
        gh_client = GitHubClient(token=token) if token else None
        team_id = actor.team_id or 1
        sync_github(
            session=db,
            team_id=team_id,
            scope=repo_full_name,
            gh_client=gh_client,
            auto_create_contributors=True,
        )
        logger.info("Successfully synced repository %s for actor %s", repo_full_name, actor.id)
    except Exception as exc:
        logger.error("Failed to sync repository %s: %s", repo_full_name, exc)
        err = f"Failed to sync {repo_full_name}: {str(exc)}"
        if is_json:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
        return RedirectResponse(
            url=f"/repos?error={urlencode({'e': err})[2:]}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    dest_url = f"/?synced=true&repo={urlencode({'r': repo_full_name})[2:]}"
    if is_json:
        return Response(
            content='{"success": true, "redirect": "' + dest_url + '"}',
            media_type="application/json",
            status_code=status.HTTP_200_OK,
        )
    return RedirectResponse(url=dest_url, status_code=status.HTTP_303_SEE_OTHER)
