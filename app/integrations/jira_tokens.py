"""A valid Atlassian access token for a workspace's Jira connection (W4, DEC-023).

The row is locked while a refresh runs (SELECT ... FOR UPDATE on PostgreSQL), and the new
access and refresh tokens are written in 1 commit, because Atlassian invalidates the old
refresh token as soon as it issues the new one. A rejected refresh marks the connection
auth_failed and raises; it is never turned into empty data (rule 9).
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.atlassian import AtlassianClient, TokenSet
from app.integrations.crypto import decrypt_secret, encrypt_secret
from app.integrations.errors import AuthError, IntegrationError
from app.models.connections import JiraConnection

logger = logging.getLogger("headref.integrations.jira_tokens")

# Refresh when the access token has less than this left.
REFRESH_MARGIN = timedelta(seconds=120)


def store_tokens(connection: JiraConnection, tokens: TokenSet) -> None:
    """Encrypt and set both tokens. The caller commits."""
    connection.access_token_enc = encrypt_secret(tokens.access_token)
    connection.refresh_token_enc = encrypt_secret(tokens.refresh_token)
    connection.expires_at = tokens.expires_at
    connection.scopes = tokens.scope[:255]
    connection.updated_at = datetime.now(UTC)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def valid_access_token(
    db: Session, connection_id: int, client: AtlassianClient, now: datetime | None = None
) -> str:
    """Return a usable access token for the connection, refreshing it if needed."""
    now = now or datetime.now(UTC)
    connection = db.scalar(
        select(JiraConnection).where(JiraConnection.id == connection_id).with_for_update()
    )
    if connection is None:
        raise LookupError(f"Jira connection {connection_id} does not exist")
    if connection.status == "auth_failed":
        db.rollback()
        raise AuthError("The Jira connection must be reconnected", status_code=401)

    if _aware(connection.expires_at) - now > REFRESH_MARGIN:
        token = decrypt_secret(connection.access_token_enc)
        db.rollback()  # release the row lock; nothing changed
        return token

    try:
        tokens = client.refresh(decrypt_secret(connection.refresh_token_enc))
    except AuthError:
        connection.status = "auth_failed"
        connection.updated_at = datetime.now(UTC)
        db.commit()
        logger.warning("jira refresh rejected: connection_id=%s", connection_id)
        raise
    except IntegrationError:
        db.rollback()
        raise
    store_tokens(connection, tokens)
    db.commit()
    logger.info("jira token refreshed: connection_id=%s", connection_id)
    return tokens.access_token
