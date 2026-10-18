"""Login stub and the authorization seam (P3-001, FR-028, NFR-009).

The login is a stub. It trusts the user id it is given and sets a signed session
cookie. It is not authentication, and real login replaces it in Stage 2.

The authorization seam is real. ``can_view_member`` is the only place the rule
"a person may view members of their own team" is written. Every route that returns
member data MUST take the ``MemberGuard`` dependency, which calls it, answers 403
and logs on denial. A test fails if a member route does not.
"""

import hashlib
import hmac
import logging
import secrets
from collections.abc import Generator
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SESSION_COOKIE = "headref_session"
SESSION_MAX_AGE_SECONDS = 8 * 60 * 60

# Signing key for the session cookie. It is random per process and never stored or
# logged, so no secret is added to configuration (README rule 7). Restarting the app
# logs everyone out, which is acceptable for a stub.
_SIGNING_KEY = secrets.token_bytes(32)


class Member(Protocol):
    """The fields of an app_user that login and authorization need."""

    id: int
    team_id: int | None
    display_name: str
    is_active: bool


# ---------------------------------------------------------------------------
# Session cookie
# ---------------------------------------------------------------------------


def _signature(payload: str) -> str:
    """Return the HMAC signature of a payload."""
    return hmac.new(_SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()


def sign_session(user_id: int) -> str:
    """Return a cookie value carrying user_id that cannot be edited undetected."""
    payload = str(user_id)
    return f"{payload}.{_signature(payload)}"


def read_session(token: str | None) -> int | None:
    """Return the user id in a session cookie value, or None if missing or forged."""
    if not token:
        return None
    payload, separator, signature = token.partition(".")
    if not separator:
        return None
    if not hmac.compare_digest(signature.encode(), _signature(payload).encode()):
        return None
    if not (payload.isascii() and payload.isdigit()):
        return None
    return int(payload)


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------


def get_db() -> Generator[Session, None, None]:
    """Yield a read-path database session.

    app.db is imported here, not at the top of the module, so that importing app.main
    and serving /health never depends on DATABASE_URL being set.
    """
    from app.db import get_session

    yield from get_session()


def load_user(db: Session, user_id: int) -> Member | None:
    """Fetch an app_user by id, or None if there is no such user."""
    from app.models.canonical import AppUser

    return db.get(AppUser, user_id)


DbDep = Annotated[Session, Depends(get_db)]


# ---------------------------------------------------------------------------
# Who is asking, and are they allowed to see this person
# ---------------------------------------------------------------------------


def current_actor(request: Request, db: DbDep) -> Member:
    """Identify who is asking from the session cookie, or answer 401."""
    user_id = read_session(request.cookies.get(SESSION_COOKIE))
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not logged in")
    user = load_user(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not logged in")
    return user


ActorDep = Annotated[Member, Depends(current_actor)]


def can_view_member(actor: Member, subject: Member) -> bool:
    """Return True if actor may view subject's data.

    MVP rule: same team. A person with no team can view nobody, not even another
    person with no team, because "no team" is not a team.
    """
    if actor.team_id is None or subject.team_id is None:
        return False
    return actor.team_id == subject.team_id


def require_member_access(member_id: int, actor: ActorDep, db: DbDep) -> Member:
    """Load the member named in the URL and check the actor may view them.

    Answers 404 if there is no such member, and 403 (logged) if the actor may not
    view them. Routes should use MemberGuard rather than calling this directly.
    """
    subject = load_user(db, member_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="Member not found")
    if not can_view_member(actor, subject):
        logger.warning("access denied: actor_id=%s subject_id=%s", actor.id, subject.id)
        raise HTTPException(status_code=403, detail="Not allowed to view this member")
    return subject


# Every route with {member_id} in its path takes this as a parameter.
MemberGuard = Annotated[Member, Depends(require_member_access)]


# ---------------------------------------------------------------------------
# Login stub routes
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Body of the login stub."""

    user_id: int


class ActorInfo(BaseModel):
    """Who a session belongs to."""

    user_id: int
    display_name: str
    team_id: int | None


def _actor_info(user: Member) -> ActorInfo:
    """Build the public description of a user."""
    return ActorInfo(user_id=user.id, display_name=user.display_name, team_id=user.team_id)


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.post("/login")
def login(body: LoginRequest, response: Response, db: DbDep) -> ActorInfo:
    """Stub login: trust the given user id and set a signed session cookie."""
    user = load_user(db, body.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Unknown or inactive user")
    response.set_cookie(
        SESSION_COOKIE,
        sign_session(user.id),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )
    logger.info("login: user_id=%s", user.id)
    return _actor_info(user)


@auth_router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    """End the session by deleting the cookie."""
    response.delete_cookie(SESSION_COOKIE)


@auth_router.get("/me")
def me(actor: ActorDep) -> ActorInfo:
    """Return who the current session belongs to."""
    return _actor_info(actor)
