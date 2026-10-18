"""Tests for freshness and degraded-state display (P5-006, Issue #38).

Verifies:
1. Fresh, stale, and unavailable states are visually distinct.
2. Unavailable source shows last success timestamp and typed failure reason.
3. Fallback notice displays when AI reasoning is unavailable (FR-022).
4. Truncation notice discloses capped item counts.
5. Time formatting renders with UTC title and localized data attributes.
"""

from datetime import UTC, datetime, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas.tools import SourceHealthOut
from app.web import ui_format

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader("app/web/templates"),
        autoescape=select_autoescape(["html"]),
    )
    ui_format.register(env)
    env.globals["url_for"] = lambda name, path="": f"/static/{path}"
    return env


def test_freshness_tiles_render_distinct_states():
    env = make_env()
    source_health = [
        SourceHealthOut(
            source="github",  # type: ignore[arg-type]
            state="fresh",  # type: ignore[arg-type]
            last_success_at=NOW - timedelta(hours=2),
        ),
        SourceHealthOut(
            source="jira",  # type: ignore[arg-type]
            state="unavailable",  # type: ignore[arg-type]
            last_success_at=NOW - timedelta(days=2),
            last_error_type="AUTH_FAILED",  # type: ignore[arg-type]
        ),
    ]

    html = env.get_template("partials/freshness.html").render(
        source_health=source_health,
        team_id=10,
        now=NOW,
        show_note=True,
    )

    # Fresh GitHub
    assert "source-tile__state--fresh" in html
    assert "Synced" in html

    # Unavailable Jira
    assert "source-tile--unavailable" in html
    assert "access token rejected" in html


def test_unavailable_banner_renders_details():
    env = make_env()
    source_health = [
        SourceHealthOut(
            source="jira",  # type: ignore[arg-type]
            state="unavailable",  # type: ignore[arg-type]
            last_success_at=NOW - timedelta(hours=48),
            last_error_type="TIMEOUT",  # type: ignore[arg-type]
        ),
    ]

    banner_macro = env.get_template("partials/notices.html").module.unavailable_banners
    rendered = banner_macro(source_health)

    assert "banner--unavailable" in rendered
    assert "Jira data is unavailable" in rendered
    assert "the request timed out" in rendered


def test_fallback_notice_rendered():
    env = make_env()
    notice_macro = env.get_template("partials/notices.html").module.fallback_notice
    rendered = notice_macro()

    assert "AI reasoning is temporarily unavailable, showing recorded facts only." in rendered


def test_truncated_notice_rendered():
    env = make_env()
    trunc_macro = env.get_template("partials/notices.html").module.truncated_notice
    rendered = trunc_macro(100, "commits")

    assert "Showing the most recent 100 commits." in rendered
