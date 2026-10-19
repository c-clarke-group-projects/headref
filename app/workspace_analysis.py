"""Workspace analysis: findings for confirmed people, likely next work, summaries (W7).

Code produces every finding (DEC-018): likely current work, blockers, risks, conflicts and
confidence come from the agent's deterministic stages, run with skip_llm. The model is
called only for a member's 2 sentence summary, and only when that member is opened.

Likely next work is shown as facts with a stated ordering, not as an inference: an
inference needs at least 2 evidence items (TESTING_AND_EVALUATION 11.6), and "assigned
and not started" is 1 record per item. Headref cannot know what someone picks up next.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.orchestrator import run_agent
from app.models.canonical import AppUser
from app.models.connections import JiraConnection
from app.models.workspace import Workspace
from app.people import confirmed_people
from app.schemas.errors import ToolFailure
from app.schemas.insight import EvidenceItem, Insight, MemberInsight
from app.schemas.tools import (
    GetAssignedWorkItemsInput,
    GetPullRequestsInput,
    GetSourceHealthInput,
    SourceHealthOut,
)
from app.tools.get_assigned_work_items import get_assigned_work_items
from app.tools.get_pull_requests import get_pull_requests
from app.tools.get_source_health import get_source_health

logger = logging.getLogger("headref.workspace_analysis")

# Assigned work can sit untouched for a long time; look back far enough to find it.
NEXT_UP_LOOKBACK = timedelta(days=3650)
NEXT_UP_LIMIT = 5
_PRIORITY_RANK = {"highest": 0, "high": 1, "medium": 2, "low": 3, "lowest": 4}


@dataclass
class NextUpItem:
    """1 assigned, not started work item or draft pull request, with its evidence."""

    text: str
    evidence: EvidenceItem


@dataclass
class MemberAnalysis:
    """Code-built findings for 1 confirmed person."""

    person_id: int
    display_name: str
    current_work: Insight | None
    blockers: list[Insight] = field(default_factory=list)
    risks: list[Insight] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    next_up: list[NextUpItem] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    activity: object | None = None


@dataclass
class WorkspaceAnalysis:
    """Findings for every confirmed person of a workspace, and source health."""

    members: list[MemberAnalysis]
    health: list[SourceHealthOut]

    def blockers(self) -> list[tuple[MemberAnalysis, Insight]]:
        return [(m, b) for m in self.members for b in m.blockers]

    def risks(self) -> list[tuple[MemberAnalysis, Insight]]:
        return [(m, r) for m in self.members for r in m.risks]

    def conflicts(self) -> list[tuple[MemberAnalysis, str]]:
        return [(m, c) for m in self.members for c in m.conflicts]


def jira_in_use(db: Session, workspace_id: int) -> bool:
    """True if the workspace has an active Jira connection."""
    return (
        db.scalar(
            select(JiraConnection.id).where(
                JiraConnection.workspace_id == workspace_id, JiraConnection.status == "active"
            )
        )
        is not None
    )


def workspace_health(db: Session, workspace_id: int, uses_jira: bool) -> list[SourceHealthOut]:
    """Freshness per source from this workspace's own sync runs (T-007)."""
    sources: list = ["github", "jira"] if uses_jira else ["github"]
    output = get_source_health(db, GetSourceHealthInput(workspace_id=workspace_id, sources=sources))
    return [] if isinstance(output, ToolFailure) else list(output.sources)


def _source_state(health: list[SourceHealthOut], source: str) -> str:
    state = next((h.state for h in health if h.source == source), "unavailable")
    return "fresh" if state == "fresh" else "stale"


def next_up(
    db: Session, person_id: int, health: list[SourceHealthOut], now: datetime
) -> list[NextUpItem]:
    """Assigned work not started (by priority, then due date) and draft pull requests."""
    items: list[NextUpItem] = []
    work = get_assigned_work_items(
        db,
        GetAssignedWorkItemsInput(
            subject_user_id=person_id,
            window_start=now - NEXT_UP_LOOKBACK,
            window_end=now,
            statuses=["todo"],
        ),
    )
    if not isinstance(work, ToolFailure):
        ordered = sorted(
            work.items,
            key=lambda w: (
                _PRIORITY_RANK.get((w.priority or "").lower(), 5),
                w.due_date or datetime.max.replace(tzinfo=UTC),
                w.external_id,
            ),
        )
        for w in ordered:
            due = f", due {w.due_date:%Y-%m-%d}" if w.due_date else ""
            priority = f", {w.priority}" if w.priority else ""
            items.append(
                NextUpItem(
                    text=f"{w.external_id} {w.title} ({w.raw_status}{priority}{due})",
                    evidence=EvidenceItem(
                        id=f"next_{len(items) + 1}",
                        source="jira",
                        entity_type="work_item",
                        entity_key=w.external_id,
                        source_url=w.source_url,
                        summary=f"{w.external_id} assigned, status {w.raw_status}",
                        observed_at=w.source_updated_at,
                        retrieved_at=w.retrieved_at,
                        source_state=_source_state(health, "jira"),
                    ),
                )
            )
    prs = get_pull_requests(
        db,
        GetPullRequestsInput(
            subject_user_id=person_id,
            window_start=now - NEXT_UP_LOOKBACK,
            window_end=now,
            states=["open"],
            include_drafts=True,
        ),
    )
    if not isinstance(prs, ToolFailure):
        for pr in prs.pull_requests:
            if not pr.is_draft:
                continue
            items.append(
                NextUpItem(
                    text=f"Draft pull request #{pr.number} {pr.title}",
                    evidence=EvidenceItem(
                        id=f"next_{len(items) + 1}",
                        source="github",
                        entity_type="pull_request",
                        entity_key=f"{pr.repo_full_name}#{pr.number}",
                        source_url=pr.source_url,
                        summary=f"Draft pull request #{pr.number}, not ready for review",
                        observed_at=pr.source_updated_at,
                        retrieved_at=pr.retrieved_at,
                        source_state=_source_state(health, "github"),
                    ),
                )
            )
    return items[:NEXT_UP_LIMIT]


async def analyze_member(
    db: Session, workspace_id: int, person: AppUser, uses_jira: bool, health: list, now: datetime
) -> MemberAnalysis:
    """Run the 3 questions for 1 person with code only, and collect the findings."""
    answers: dict[str, MemberInsight] = {}
    for question in ("current_work", "blockers", "risks"):
        answers[question] = await run_agent(
            db,
            subject_user_id=person.id,
            team_id=None,
            question_type=question,
            skip_llm=True,
            now=now,
            workspace_id=workspace_id,
            jira_in_use=uses_jira,
        )
    current = answers["current_work"]
    conflicts: list[str] = []
    for answer in answers.values():
        for conflict in answer.conflicts:
            if conflict not in conflicts:
                conflicts.append(conflict)
    unknowns: list[str] = []
    for answer in answers.values():
        for unknown in answer.unknowns:
            if unknown not in unknowns:
                unknowns.append(unknown)
    return MemberAnalysis(
        person_id=person.id,
        display_name=person.display_name,
        current_work=current.likely_current_work,
        blockers=list(answers["blockers"].blockers),
        risks=list(answers["risks"].risks),
        conflicts=conflicts,
        next_up=next_up(db, person.id, health, now),
        unknowns=unknowns,
        activity=current.activity,
    )


async def analyze_workspace(
    db: Session, workspace: Workspace, only_person_id: int | None = None
) -> WorkspaceAnalysis:
    """Findings for every confirmed person, or for 1 of them. Alphabetical, never ranked."""
    now = datetime.now(UTC)
    uses_jira = jira_in_use(db, workspace.id)
    health = workspace_health(db, workspace.id, uses_jira)
    people = confirmed_people(db, workspace.organization_id) if workspace.organization_id else []
    if only_person_id is not None:
        people = [p for p in people if p.id == only_person_id]
    members = [
        await analyze_member(db, workspace.id, person, uses_jira, health, now) for person in people
    ]
    return WorkspaceAnalysis(members=members, health=health)


async def member_summary(db: Session, workspace: Workspace, person_id: int) -> MemberInsight:
    """The 2 sentence summary for 1 person: the only model call (DEC-017, DEC-018)."""
    return await run_agent(
        db,
        subject_user_id=person_id,
        team_id=None,
        question_type="current_work",
        workspace_id=workspace.id,
        jira_in_use=jira_in_use(db, workspace.id),
    )
