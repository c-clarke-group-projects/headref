"""Shared error handling and execution infrastructure for read tools.

Every tool in app/tools/ executes offline queries over PostgreSQL per DEC-002,
returning frozen Pydantic contracts or typed ToolFailure instances (FR-033).
No HTTP client may ever be imported in this package (AC-1).
"""

import logging
import re
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import SQLAlchemyError

from app.db import is_timeout_error
from app.schemas.errors import ToolFailure

logger = logging.getLogger("headref.tools")

SECRET_REDACT_REGEX = re.compile(
    r"(token|bearer|basic|key|password|secret|authorization)"
    r"\s*[:=]\s*"
    r"(?:(?:bearer|basic|token)\s+)?"
    r"\S+",
    re.IGNORECASE,
)

T = TypeVar("T")


def sanitize_tool_detail(detail: str | None) -> str:
    """Mask credentials or sensitive data in error strings (NFR-011)."""
    if not detail:
        return "Unknown error"
    return SECRET_REDACT_REGEX.sub(r"\1=***", detail)


def execute_tool_query(
    tool_id: str,
    query_fn: Callable[[], T],
) -> T | ToolFailure:
    """Execute a database read operation and convert failures to typed ToolFailure models.

    Detects query timeouts, converts unexpected database exceptions to UPSTREAM_ERROR,
    and guarantees that raw exceptions never escape into the agent orchestrator.
    """
    try:
        return query_fn()
    except Exception as exc:
        safe_detail = sanitize_tool_detail(str(exc))
        if is_timeout_error(exc):
            logger.error("Tool %s timed out during database execution: %s", tool_id, safe_detail)
            return ToolFailure(
                tool_id=tool_id,
                error_type="TIMEOUT",
                detail=f"Query exceeded database statement timeout: {safe_detail}",
            )

        if isinstance(exc, SQLAlchemyError):
            logger.error("Tool %s database error: %s", tool_id, safe_detail)
            return ToolFailure(
                tool_id=tool_id,
                error_type="UPSTREAM_ERROR",
                detail=f"Database error during {tool_id} execution: {safe_detail}",
            )

        logger.error("Tool %s unexpected error: %s", tool_id, safe_detail)
        return ToolFailure(
            tool_id=tool_id,
            error_type="UPSTREAM_ERROR",
            detail=f"Unexpected error in {tool_id}: {safe_detail}",
        )
