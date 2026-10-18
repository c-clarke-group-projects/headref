"""Formatting helpers and Jinja filters for the Headref templates.

Register once where the Jinja2Templates object is created:

    from app.web import ui_format
    templates = Jinja2Templates(directory="app/web/templates")
    ui_format.register(templates.env)

Everything here is pure and has no I/O, so it is easy to unit test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

SOURCE_LABELS = {"github": "GitHub", "jira": "Jira"}

# DESIGN.md 6.3, typed error to plain label. Use everywhere an error_type is shown.
ERROR_LABELS = {
    "TIMEOUT": "the request timed out",
    "RATE_LIMITED": "rate limited by the source",
    "AUTH_FAILED": "access token rejected",
    "NOT_FOUND": "the project or repository was not found",
    "UPSTREAM_ERROR": "the source returned an error",
    "SCHEMA_INVALID": "some records could not be read and were left out",
}

STATUS_LABELS = {
    "todo": "To do",
    "in_progress": "In progress",
    "in_review": "In review",
    "done": "Done",
    "blocked": "Blocked",
}

# Assigned work sort order (DESIGN.md S2 section 4).
STATUS_ORDER = {"blocked": 0, "in_progress": 1, "in_review": 2, "todo": 3, "done": 4}

# Tooltip text for confidence badges, 1 line each.
# TODO: copy the exact rules from docs/AI_BEHAVIOR.md 5.4. Only MEDIUM is
# quoted in DESIGN.md; do not invent the others.
CONFIDENCE_RULES = {
    "HIGH": "High: see docs/AI_BEHAVIOR.md 5.4",
    "MEDIUM": "Medium: 2 or more evidence items, or 1 item from the authoritative source",
    "LOW": "Low: see docs/AI_BEHAVIOR.md 5.4",
    "UNKNOWN": "Unknown: the sources cannot establish this",
}


# Time -----------------------------------------------------------------------

def as_utc(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC, convert aware ones to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt: datetime | None) -> str:
    """2026-09-12T09:14:00Z, for the datetime attribute."""
    if dt is None:
        return ""
    return as_utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_title(dt: datetime | None) -> str:
    """2026-09-12 09:14 UTC, for the title tooltip."""
    if dt is None:
        return ""
    return as_utc(dt).strftime("%Y-%m-%d %H:%M UTC")


def fmt_utc(dt: datetime | None) -> str:
    """12 Sep 2026, 09:14 UTC. app.js rewrites this to local time."""
    if dt is None:
        return ""
    d = as_utc(dt)
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}, {d:%H:%M} UTC"


def fmt_short(dt: datetime | None) -> str:
    """24 Sep, 08:32 UTC. Used for last successful sync."""
    if dt is None:
        return ""
    d = as_utc(dt)
    return f"{d.day} {MONTHS[d.month - 1]}, {d:%H:%M} UTC"


def fmt_date(value: datetime | date | None) -> str:
    """28 Sep 2026. Due dates carry no time."""
    if value is None:
        return ""
    d = as_utc(value) if isinstance(value, datetime) else value
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def is_past(value: datetime | date | None, now: datetime | None = None) -> bool:
    if value is None:
        return False
    now = as_utc(now or datetime.now(timezone.utc))
    if isinstance(value, datetime):
        return as_utc(value) < now
    return value < now.date()


def reltime(dt: datetime | None, now: datetime | None = None) -> str:
    """just now, N min ago, N hours ago, N days ago (DESIGN.md 6.3)."""
    if dt is None:
        return "never"
    now = as_utc(now or datetime.now(timezone.utc))
    seconds = max(0, int((now - as_utc(dt)).total_seconds()))
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"
    days = hours // 24
    return f"{days} days ago"


# Labels ----------------------------------------------------------------------

def source_label(source: str | None) -> str:
    return SOURCE_LABELS.get((source or "").lower(), source or "Unknown source")


def error_label(code: str | None) -> str:
    return ERROR_LABELS.get(code or "", "the source returned an error")


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "", status or "Unknown")


def initials(name: str | None) -> str:
    """KE for Keshan, IS for Isiwara, JD for Jane Doe."""
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def avatar_tint(user_id: int | None) -> str:
    """Stable, meaningless tint class (DESIGN.md 6.7)."""
    return f"avatar--tint-{(user_id or 0) % 6}"


def external_url(url: str | None) -> str:
    """Allow only http and https links to sources; anything else becomes #.

    Autoescape already escapes quotes. This blocks javascript: and data: URLs
    in case a bad value ever reaches source_url.
    """
    if not url:
        return "#"
    parsed = urlparse(url.strip())
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return url.strip()
    return "#"


def sort_assigned(items: Iterable[Any]) -> list[Any]:
    """Blocked, in progress, in review, to do, done; then due date, none last."""
    far = date.max

    def key(w: Any) -> tuple[int, date]:
        due = getattr(w, "due_date", None)
        due_d = due.date() if isinstance(due, datetime) else (due or far)
        return (STATUS_ORDER.get(getattr(w, "status", ""), 9), due_d)

    return sorted(items, key=key)


# Signals checked disclosure -----------------------------------------------------

@dataclass(frozen=True)
class Signal:
    id: str
    text: str
    needs: tuple[str, ...]
    checked: bool
    found: bool
    needs_label: str


# Plain word versions from DESIGN.md section 10. Thresholds render from
# config, never hardcoded. The tuple is (text template, sources needed,
# config attribute holding the threshold or None).
# TODO: confirm the "needs" sources and the config attribute names against
# docs/AI_BEHAVIOR.md and app/config.py. The names below are placeholders.
BLOCKER_SIGNALS: dict[str, tuple[str, tuple[str, ...], str | None]] = {
    "BL-1": ("Ticket status is Blocked in Jira", ("jira",), None),
    "BL-2": ("Ticket is flagged as impeded in Jira", ("jira",), None),
    "BL-3": ('Ticket has an open "is blocked by" dependency', ("jira",), None),
    "BL-4": ("Pull request has changes requested and no commits since", ("github",), None),
    "BL-5": ("Pull request open with no review for more than {n} days", ("github",), "BL5_NO_REVIEW_DAYS"),
    "BL-6": ("Draft pull request older than {n} days", ("github",), "BL6_DRAFT_DAYS"),
    "BL-7": ("Checks failing on the latest commit", ("github",), None),
    "BL-8": ("Ticket in progress with no linked branch or pull request after {n} days", ("jira", "github"), "BL8_NO_LINK_DAYS"),
}
RISK_SIGNALS: dict[str, tuple[str, tuple[str, ...], str | None]] = {
    "RK-1": ("Due within {n} days and not done", ("jira",), "RK1_DUE_DAYS"),
    "RK-2": ("In the same status for more than {n} days", ("jira",), "RK2_STATUS_DAYS"),
    "RK-3": ("High priority with no activity for {n} days", ("jira", "github"), "RK3_IDLE_DAYS"),
    "RK-4": ("Jira and GitHub disagree about the ticket", ("jira", "github"), None),
}


def signal_rows(
    question: str,
    settings: Any,
    source_health: Sequence[Any],
    found_ids: Iterable[str] = (),
) -> list[Signal]:
    """Rows for the Signals checked card on the Blockers and Risks tabs.

    A signal counts as checked only when every source it needs is not
    unavailable. found_ids is optional: pass it only if the backend reports
    which signal produced each finding. Without it, show checked rows only
    when the answer list is empty, so "Checked, not found" is true.
    """
    table = BLOCKER_SIGNALS if question == "blockers" else RISK_SIGNALS if question == "risks" else {}
    down = {getattr(s, "source", "") for s in source_health if getattr(s, "state", "") == "unavailable"}
    found = set(found_ids)
    rows: list[Signal] = []
    for sid, (template, needs, attr) in table.items():
        n = getattr(settings, attr, None) if attr else None
        text = template.format(n=n if n is not None else "?")
        missing = [src for src in needs if src in down]
        rows.append(
            Signal(
                id=sid,
                text=text,
                needs=needs,
                checked=not missing,
                found=sid in found,
                needs_label=" and ".join(source_label(m) for m in missing),
            )
        )
    return rows


# Registration --------------------------------------------------------------------

def register(env: Any) -> None:
    """Add filters and globals to a Jinja2 Environment. Autoescape stays on."""
    env.filters.update(
        {
            "iso_utc": iso_utc,
            "utc_title": utc_title,
            "fmt_utc": fmt_utc,
            "fmt_short": fmt_short,
            "fmt_date": fmt_date,
            "is_past": is_past,
            "reltime": reltime,
            "source_label": source_label,
            "error_label": error_label,
            "status_label": status_label,
            "initials": initials,
            "avatar_tint": avatar_tint,
            "external_url": external_url,
            "sort_assigned": sort_assigned,
        }
    )
    env.globals["confidence_rules"] = CONFIDENCE_RULES
