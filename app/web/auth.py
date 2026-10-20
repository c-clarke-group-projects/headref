"""Session cookie and who is asking (P3-001, NFR-009).

Signing in happens only through GitHub OAuth (``/auth/github/login`` in routes.py),
which sets the signed session cookie defined here.

Authorization is per workspace: ``workspace_access`` in app/web/workspaces.py checks
membership and role on every route under /w/{workspace_id} (DEC-021). The team rule
``can_view_member`` and its MemberGuard went with the team pages in W10 (DEC-026).
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


def cookie_secure() -> bool:
    """True outside APP_ENV=local: cookies then travel over HTTPS only.

    Local development serves plain http://localhost, where a Secure cookie is not sent.
    """
    from app.config import get_settings

    return get_settings().app_env != "local"


# Minimum length of SESSION_SECRET. 32 characters keeps the HMAC key at 256 bits or
# more for a random hex or base64 value.
MIN_SESSION_SECRET_LENGTH = 32

_signing_key: bytes | None = None


def _get_signing_key() -> bytes:
    """Return the session signing key, from SESSION_SECRET when it is set.

    Without SESSION_SECRET the key is random per process: sessions end on restart and
    do not work across several workers. Fine for local work, wrong for a deployment.
    The key is never logged.
    """
    global _signing_key
    if _signing_key is None:
        from app.config import get_settings

        secret = get_settings().session_secret
        if secret:
            if len(secret) < MIN_SESSION_SECRET_LENGTH:
                raise RuntimeError(
                    f"SESSION_SECRET must be at least {MIN_SESSION_SECRET_LENGTH} characters"
                )
            _signing_key = secret.encode()
        else:
            logger.warning(
                "SESSION_SECRET is not set; using a random per-process session key. "
                "Sessions end when the app restarts."
            )
            _signing_key = secrets.token_bytes(32)
    return _signing_key


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
    return hmac.new(_get_signing_key(), payload.encode(), hashlib.sha256).hexdigest()


def sign_value(payload: str) -> str:
    """Return payload with an HMAC signature, for values that must round-trip unchanged."""
    return f"{payload}~{_signature(payload)}"


def read_signed_value(token: str | None) -> str | None:
    """Return the payload of a sign_value token, or None if missing or forged."""
    if not token:
        return None
    payload, separator, signature = token.rpartition("~")
    if not separator or not payload:
        return None
    if not hmac.compare_digest(signature.encode(), _signature(payload).encode()):
        return None
    return payload


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
# Who is asking
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


# ---------------------------------------------------------------------------
# Session routes. There is no login route here: GitHub OAuth signs people in.
# ---------------------------------------------------------------------------


class ActorInfo(BaseModel):
    """Who a session belongs to."""

    user_id: int
    display_name: str
    team_id: int | None


def _actor_info(user: Member) -> ActorInfo:
    """Build the public description of a user."""
    return ActorInfo(user_id=user.id, display_name=user.display_name, team_id=user.team_id)


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    """End the session by deleting the cookie."""
    response.delete_cookie(SESSION_COOKIE)


@auth_router.get("/me")
def me(actor: ActorDep) -> ActorInfo:
    """Return who the current session belongs to."""
    return _actor_info(actor)
