"""The evidence drawer escapes a hostile excerpt on the real workspace page (P5-003, #35).

tests/web/test_ui_templates.py covers the partial through a bare Jinja environment. These
go through the app instead: GET /w/{id} with a signed-in owner, the app's own template
environment and filters, and the drawer included by the page.

The analysis is replaced, as in tests/test_workspace_analysis.py. Running the real
analysis here fails on SQLite, which drops the time zone from stored datetimes, so the
findings code compares naive and aware values. PostgreSQL keeps the time zone.
"""

from datetime import UTC, datetime

import pytest

from app.schemas.insight import EvidenceItem, Insight
from app.web import workspace_pages
from app.workspace_analysis import MemberAnalysis, WorkspaceAnalysis
from tests.test_workspace_analysis import _owned, client, db_session  # noqa: F401

NOW = datetime.now(UTC)
HOSTILE = "<img src=x onerror=alert(1)> **bold** <script>alert(2)</script>"


def _hostile_analysis(person_id: int) -> WorkspaceAnalysis:
    evidence = EvidenceItem(
        id="ev_1",
        source="github",
        entity_type="pull_request",
        entity_key="o/r#5",
        source_url="https://github.com/o/r/pull/5",
        summary="Pull request 5 open",
        excerpt=HOSTILE,
        observed_at=NOW,
        retrieved_at=NOW,
        source_state="fresh",
    )
    claim = Insight(
        claim="Pull request #5 has failing CI checks.",
        classification="fact",
        confidence="HIGH",
        evidence=[evidence],
    )
    return WorkspaceAnalysis(
        members=[
            MemberAnalysis(
                person_id=person_id,
                display_name="zara",
                current_work=claim,
                blockers=[claim],
            )
        ],
        health=[],
    )


@pytest.mark.parametrize("member", [False, True], ids=["team view", "member view"])
def test_a_hostile_excerpt_is_escaped_in_the_drawer(
    client, db_session, monkeypatch, member  # noqa: F811
):
    workspace, person_id = _owned(db_session, client)

    async def fake(db, ws, only_person_id=None):
        return _hostile_analysis(person_id)

    monkeypatch.setattr(workspace_pages, "analyze_workspace", fake)
    url = f"/w/{workspace.id}" + (f"?member={person_id}" if member else "")

    html = client.get(url).text

    assert "Excerpt (source text, not verified)" in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "<script>alert(2)</script>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html
    # Untrusted text is plain text: no Markdown rendering.
    assert "**bold**" in html
    assert "<strong>bold</strong>" not in html
