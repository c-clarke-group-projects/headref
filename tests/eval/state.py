"""Known database states for the evaluation scenarios (P6-003, Developer 1 half).

A scenario writes the rows it needs into a fresh in-memory database, then asks the real
agent: the real read tools T-001 to T-007, findings, confidence, narrative call,
validator and agent_run write. Only Ollama is replaced, at the HTTP layer, with a
narrative that names no entity, so the narrative never decides a result. Results are
read from structured fields only, never wording (DEC-011).

Times are relative to the real clock, because source health (T-007) judges freshness
against it.
"""

import asyncio
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

import httpx
import respx
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.agent.orchestrator import run_agent
from app.config import get_settings
from app.db import Base
from app.models.agent import AgentRun
from app.models.canonical import AppUser, Organization, Project, Repository, Team
from app.models.operations import SyncRun
from app.models.work import Commit, PullRequest, Review, WorkItem, WorkItemLink
from app.schemas.insight import Insight, MemberInsight

REPO = "keshan-dev/headref"
JIRA_SITE = "https://example.atlassian.net"
NEUTRAL_NARRATIVE = {
    "summary": "A summary of the recorded work items and pull requests for this member.",
    "needs_attention": "See the findings below.",
    "attention_needed": False,
}


def _mark_utc(target: Any, _context: Any, _attrs: Any = None) -> None:
    """SQLite drops the zone that PostgreSQL keeps; restore it on load and refresh (P6-005)."""
    for name, value in list(vars(target).items()):
        if isinstance(value, datetime) and value.tzinfo is None:
            set_committed_value(target, name, value.replace(tzinfo=UTC))


class Scenario:
    """1 scenario's database: a team, a Jira project and a repository to fill."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.now = datetime.now(UTC)
        self._ids = count(1)
        org = Organization(name="Eval Org")
        session.add(org)
        session.flush()
        self.org = org
        self.team = Team(organization_id=org.id, name="Eval Team")
        self.project = Project(organization_id=org.id, key="AUTH", name="Authentication")
        self.repo = Repository(organization_id=org.id, full_name=REPO)
        session.add_all([self.team, self.project, self.repo])
        session.flush()

    def ago(self, days: float = 0, hours: float = 0) -> datetime:
        return self.now - timedelta(days=days, hours=hours)

    def member(self, name: str) -> AppUser:
        user = AppUser(organization_id=self.org.id, team_id=self.team.id, display_name=name)
        self.session.add(user)
        self.session.flush()
        return user

    def work_item(
        self,
        key: str,
        assignee: AppUser | None,
        status: str = "in_progress",
        raw_status: str = "In Progress",
        updated: datetime | None = None,
        **fields: Any,
    ) -> WorkItem:
        item = WorkItem(
            project_id=self.project.id,
            external_id=key,
            title=fields.pop("title", f"Work on {key}"),
            status=status,
            raw_status=raw_status,
            assignee_app_user_id=assignee.id if assignee else None,
            is_flagged=fields.pop("is_flagged", False),
            source_url=f"{JIRA_SITE}/browse/{key}",
            source_updated_at=updated or self.ago(hours=2),
            retrieved_at=self.ago(hours=1),
            **fields,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def pull_request(
        self,
        number: int,
        author: AppUser | None,
        branch: str,
        state: str = "open",
        created: datetime | None = None,
        **fields: Any,
    ) -> PullRequest:
        pr = PullRequest(
            repository_id=self.repo.id,
            number=number,
            title=fields.pop("title", f"Pull request {number}"),
            state=state,
            is_draft=fields.pop("is_draft", False),
            branch_name=branch,
            author_app_user_id=author.id if author else None,
            created_at_source=created or self.ago(days=1),
            source_url=f"https://github.com/{REPO}/pull/{number}",
            source_updated_at=fields.pop("updated", self.ago(hours=3)),
            retrieved_at=self.ago(hours=1),
            **fields,
        )
        self.session.add(pr)
        self.session.flush()
        return pr

    def commit(
        self, author: AppUser | None, when: datetime, branch: str | None = None, message: str = ""
    ) -> Commit:
        sha = f"{next(self._ids):040x}"
        row = Commit(
            repository_id=self.repo.id,
            sha=sha,
            message_excerpt=message or f"commit {sha[-6:]}",
            branch_name=branch,
            author_app_user_id=author.id if author else None,
            committed_at=when,
            source_url=f"https://github.com/{REPO}/commit/{sha}",
            retrieved_at=self.ago(hours=1),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def review(
        self, pr: PullRequest, reviewer: AppUser | None, state: str, when: datetime
    ) -> Review:
        row = Review(
            pull_request_id=pr.id,
            external_id=f"review-{next(self._ids)}",
            reviewer_app_user_id=reviewer.id if reviewer else None,
            state=state,
            submitted_at=when,
            source_url=f"{pr.source_url}#pullrequestreview-{pr.id}",
            retrieved_at=self.ago(hours=1),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def link(self, item: WorkItem, pr: PullRequest, method: str = "branch_name") -> WorkItemLink:
        confidence = "HIGH" if method in ("branch_name", "jira_remote_link") else "MEDIUM"
        row = WorkItemLink(
            work_item_id=item.id,
            target_type="pull_request",
            target_id=str(pr.id),
            link_method=method,
            confidence=confidence,
            created_at=self.ago(hours=1),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def synced(
        self,
        source: str,
        finished: datetime,
        status: str = "success",
        error_type: str | None = None,
    ) -> SyncRun:
        run = SyncRun(
            source=source,
            scope=REPO if source == "github" else "ALL",
            workspace_id=None,
            status=status,
            started_at=finished - timedelta(seconds=30),
            finished_at=finished,
            items_fetched=1,
            items_written=1,
            items_skipped=0,
            error_type=error_type,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def both_fresh(self) -> None:
        self.synced("jira", self.ago(hours=1))
        self.synced("github", self.ago(hours=1))

    def ask(
        self, subject: AppUser, question_type: str, narrative: dict[str, Any] | None = None
    ) -> MemberInsight:
        """Run the real agent for 1 question, with Ollama mocked and no network.

        narrative is what the mocked model answers; by default 1 that names no entity.
        """
        self.session.commit()
        url = get_settings().ollama_url.rstrip("/") + "/api/chat"
        reply = httpx.Response(
            200,
            json={
                "message": {"content": json.dumps(narrative or NEUTRAL_NARRATIVE)},
                "eval_count": 30,
                "prompt_eval_count": 90,
            },
        )
        with respx.mock(assert_all_mocked=True) as mock:
            mock.post(url).mock(return_value=reply)
            return asyncio.run(
                run_agent(
                    self.session,
                    subject_user_id=subject.id,
                    team_id=self.team.id,
                    question_type=question_type,
                    now=self.now,
                )
            )

    def agent_runs(self) -> list[AgentRun]:
        return list(self.session.scalars(select(AgentRun)).all())


def scenario_session() -> Generator[Scenario, None, None]:
    """A fresh database per scenario, so the insight cache never carries over."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    # After a commit, rows are refreshed rather than loaded, so both events are needed.
    event.listen(Base, "load", _mark_utc, propagate=True)
    event.listen(Base, "refresh", _mark_utc, propagate=True)
    try:
        with Session(engine) as session:
            yield Scenario(session)
    finally:
        event.remove(Base, "load", _mark_utc)
        event.remove(Base, "refresh", _mark_utc)


def claims(insight: MemberInsight) -> list[Insight]:
    """Every claim in the answer."""
    current = [insight.likely_current_work] if insight.likely_current_work else []
    return current + insight.blockers + insight.risks


def cited_keys(claim: Insight) -> set[str]:
    return {ev.entity_key for ev in claim.evidence}
