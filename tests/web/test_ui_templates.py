"""UI guard tests (DESIGN.md section 14).

Covers: no |safe in templates, no en or em dashes in app/web, no remote
asset URLs, hostile excerpt escaping in the evidence list, URL filter,
relative time labels. Render tests use plain Jinja so they do not need
the app or a database.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.web import ui_format

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "app" / "web"
TEMPLATES = WEB / "templates"

HOSTILE = "<img src=x onerror=alert(1)> **bold** [link](https://evil.example)"
NOW = datetime(2026, 9, 26, 9, 40, tzinfo=UTC)


def make_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    ui_format.register(env)
    env.globals["url_for"] = lambda name, path="": f"/static/{path}"
    return env


def evidence(**overrides):
    base = dict(
        id="ev_3",
        source="github",
        entity_type="pull_request",
        entity_key="keshan-dev/headref#1",
        source_url="https://github.com/keshan-dev/headref/pull/1",
        summary="Pull request 1 open, linked to AUTH-245",
        excerpt=HOSTILE,
        observed_at=NOW - timedelta(days=14),
        retrieved_at=NOW,
        source_state="fresh",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_no_safe_filter_in_templates():
    pattern = re.compile(r"\|\s*safe\b")
    offenders = [
        p.name for p in TEMPLATES.rglob("*.html") if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"|safe found in {offenders}"


def test_no_en_or_em_dashes_in_web_files():
    offenders = []
    for p in WEB.rglob("*"):
        if p.is_file() and p.suffix in {".html", ".css", ".js", ".py"}:
            text = p.read_text(encoding="utf-8")
            if "\u2013" in text or "\u2014" in text:
                offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []


def test_no_remote_assets_in_templates():
    pattern = re.compile(r"""(?:src|href)=["']https?://""", re.I)
    for p in TEMPLATES.rglob("*.html"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if pattern.search(line) and "source_url" not in line:
                pytest.fail(f"remote asset in {p.name}: {line.strip()}")


def test_hostile_excerpt_is_escaped_in_evidence_list():
    html = (
        make_env()
        .get_template("partials/evidence_list.html")
        .render(evidence=[evidence()], claim_id="current-0")
    )
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "**bold**" in html
    assert '<a href="https://evil.example"' not in html
    assert "Excerpt (source text, not verified)" in html
    assert 'rel="noopener noreferrer"' in html


def test_claim_card_shows_class_confidence_and_stale_marker():
    insight = SimpleNamespace(
        claim="Keshan is likely working on AUTH-245, session validation seam.",
        classification="inference",
        confidence="MEDIUM",
        evidence=[
            evidence(id="ev_1", source="jira", source_state="stale", excerpt=None),
            evidence(),
        ],
        conflicts=[],
    )
    html = (
        make_env()
        .get_template("partials/claim.html")
        .render(insight=insight, claim_id="current-0", label="Likely current work")
    )
    assert "Inference" in html
    assert "Medium confidence" in html
    assert "Uses stale Jira data" in html
    assert re.search(r"\d+\s*%", html) is None, "confidence must never be a number"


def test_external_url_blocks_script_urls():
    assert ui_format.external_url("javascript:alert(1)") == "#"
    assert ui_format.external_url("data:text/html,x") == "#"
    assert ui_format.external_url("https://github.com/a/b") == "https://github.com/a/b"


@pytest.mark.parametrize(
    "delta,label",
    [
        (timedelta(seconds=30), "just now"),
        (timedelta(minutes=4), "4 min ago"),
        (timedelta(hours=1), "1 hour ago"),
        (timedelta(hours=30), "30 hours ago"),
        (timedelta(days=3), "3 days ago"),
    ],
)
def test_reltime(delta, label):
    assert ui_format.reltime(NOW - delta, NOW) == label


def test_error_labels_cover_typed_errors():
    for code in [
        "TIMEOUT",
        "RATE_LIMITED",
        "AUTH_FAILED",
        "NOT_FOUND",
        "UPSTREAM_ERROR",
        "SCHEMA_INVALID",
    ]:
        assert (
            ui_format.error_label(code) != "the source returned an error"
            or code == "UPSTREAM_ERROR"
        )
