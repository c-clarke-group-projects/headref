"""Deterministic team overview generator (P5-001, Issue #33, FR-025, Gap G6).

Generates the team overview contract listing members, their operational states
(on_track, needs_attention, blocked, unknown), attention items, and source freshness.
Under DEC-018, this module runs pure deterministic Python rules without any model calls.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.blockers import detect_blockers
from app.agent.conflicts import detect_conflicts
from app.agent.evidence_builder import build_evidence
from app.agent.orchestrator import ReadTools, run_s1_s2
from app.agent.risks import detect_risks
from app.models.canonical import AppUser, Repository, Team
from app.models.identity import IdentityLink
from app.models.work import Commit, PullRequest, Review
from app.schemas.errors import ToolFailure
from app.schemas.insight import (
    AttentionItem,
    EvidenceItem,
    TeamMemberOverview,
    TeamOverview,
)
from app.schemas.tools import (
    GetSourceHealthInput,
    GetTeamMembersInput,
    SourceHealthOut,
    TeamMemberOut,
)
from app.tools.get_team_members import get_team_members


def generate_team_overview(
    session: Session,
    team_id: int,
    *,
    now: datetime | None = None,
    tools: ReadTools | None = None,
    team_members_fn: Callable[..., Any] | None = None,
    repository_name: str | None = None,
) -> TeamOverview:
    """Produce the TeamOverview contract for a given team (FR-025).

    1. Verify team exists.
    2. Check source health (T-007) for required sources (jira, github).
    3. Query active team members (T-001) and unmatched account counts.
    4. For each member, retrieve 14-day activity, build evidence, and evaluate findings.
       If a required source is unavailable, member state is forced to 'unknown'.
    5. Aggregate overall team status (Blocked > Needs Attention > Unknown > On Track).
    """
    if session is not None:
        team = session.get(Team, team_id)
        if team is None:
            raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
        team_name = team.name
    else:
        team_name = f"Team {team_id}"

    moment = now if now is not None else datetime.now(UTC)
    read_tools = tools if tools is not None else ReadTools()
    fetch_members = team_members_fn if team_members_fn is not None else get_team_members

    # 1. Health check via T-007
    health_input = GetSourceHealthInput(team_id=team_id, sources=["jira", "github"])
    health_res = read_tools.source_health(session, health_input)

    if isinstance(health_res, ToolFailure) or health_res is None:
        source_health: list[SourceHealthOut] = []
        unavailable_sources = {"jira", "github"}
    else:
        source_health = list(health_res.sources)
        unavailable_sources = {h.source for h in source_health if h.state == "unavailable"}

    last_synced = {h.source: h.last_success_at for h in source_health}

    # 2. Member list via T-001
    members_input = GetTeamMembersInput(team_id=team_id, include_inactive=False)
    members_res = fetch_members(session, members_input)

    if isinstance(members_res, ToolFailure) or members_res is None:
        if isinstance(members_res, ToolFailure) and members_res.error_type == "NOT_FOUND":
            raise HTTPException(status_code=404, detail=f"Team {team_id} not found")
        team_members = []
        unmatched_count = 0
    else:
        team_members = sorted(members_res.members, key=lambda m: m.display_name)
        unmatched_count = members_res.unmatched_count

    # Filter to repository contributors if repository_name is provided
    repo_user_ids: set[int] | None = None
    if repository_name and session is not None:
        repo = session.scalar(
            select(Repository).where(func.lower(Repository.full_name) == repository_name.lower())
        )
        if repo:
            commit_user_ids = session.scalars(
                select(Commit.author_app_user_id).where(
                    Commit.repository_id == repo.id,
                    Commit.author_app_user_id.is_not(None),
                )
            ).all()
            pr_user_ids = session.scalars(
                select(PullRequest.author_app_user_id).where(
                    PullRequest.repository_id == repo.id,
                    PullRequest.author_app_user_id.is_not(None),
                )
            ).all()
            review_user_ids = session.scalars(
                select(Review.reviewer_app_user_id)
                .join(PullRequest, Review.pull_request_id == PullRequest.id)
                .where(
                    PullRequest.repository_id == repo.id,
                    Review.reviewer_app_user_id.is_not(None),
                )
            ).all()

            owner_handle = repo.full_name.split("/")[0] if "/" in repo.full_name else ""
            owner_user_ids = []
            if owner_handle:
                owner_user_ids = session.scalars(
                    select(IdentityLink.app_user_id).where(
                        IdentityLink.integration == "github",
                        func.lower(IdentityLink.external_handle) == owner_handle.lower(),
                    )
                ).all()

            repo_user_ids = (
                set(commit_user_ids)
                | set(pr_user_ids)
                | set(review_user_ids)
                | set(owner_user_ids)
            )
        else:
            repo_user_ids = set()

    if repo_user_ids is not None:
        team_members = [m for m in team_members if m.user_id in repo_user_ids]
        missing_ids = repo_user_ids - {m.user_id for m in team_members}
        if missing_ids:
            extra_users = session.scalars(
                select(AppUser).where(AppUser.id.in_(missing_ids), AppUser.is_active.is_(True))
            ).all()
            for u in extra_users:
                team_members.append(
                    TeamMemberOut(
                        user_id=u.id,
                        display_name=u.display_name,
                        role_label=u.role_label,
                        is_active=u.is_active,
                        linked_accounts=[],
                    )
                )
            team_members.sort(key=lambda m: m.display_name)

    # 3. Member states
    member_overviews: list[TeamMemberOverview] = []

    for m in team_members:
        if unavailable_sources:
            # When required sources are unavailable, state must be unknown (FR-025)
            sources_str = ", ".join(sorted(unavailable_sources))
            member_overviews.append(
                TeamMemberOverview(
                    user_id=m.user_id,
                    display_name=m.display_name,
                    role_label=m.role_label,
                    state="unknown",
                    state_reason=f"Source data is unavailable ({sources_str})",
                    attention_count=0,
                    attention_items=[],
                    backing_evidence=[],
                )
            )
            continue

        # Run retrieval stage S1/S2 for this member
        context = run_s1_s2(
            session,
            subject_user_id=m.user_id,
            team_id=team_id,
            question_type="blockers",
            tools=read_tools,
            now=moment,
        )

        # Scope retrieved activity strictly to the chosen repository
        if repository_name:
            repo_lower = repository_name.lower()
            context.retrieval.pull_requests = [
                pr for pr in context.retrieval.pull_requests if pr.repo_full_name.lower() == repo_lower
            ]
            context.retrieval.commits = [
                c for c in context.retrieval.commits if c.repo_full_name.lower() == repo_lower
            ]
            context.retrieval.reviews = [
                r for r in context.retrieval.reviews if r.repo_full_name.lower() == repo_lower
            ]

        if not context.gate.proceed:
            reason = context.gate.reason or "Required data is unavailable"
            member_overviews.append(
                TeamMemberOverview(
                    user_id=m.user_id,
                    display_name=m.display_name,
                    role_label=m.role_label,
                    state="unknown",
                    state_reason=reason,
                    attention_count=0,
                    attention_items=[],
                    backing_evidence=[],
                )
            )
            continue

        # Build evidence
        evidence_set = build_evidence(context)
        canonical_evidence: list[EvidenceItem] = []
        for ev in evidence_set.items:
            canonical_evidence.append(
                EvidenceItem(
                    id=ev.id,
                    source=ev.source,
                    entity_type=str(ev.entity_type),
                    entity_key=str(ev.entity_key),
                    source_url=ev.source_url or "",
                    summary=ev.summary,
                    excerpt=ev.excerpt,
                    observed_at=ev.observed_at,
                    retrieved_at=ev.retrieved_at,
                    source_state="fresh" if str(ev.source_state) == "fresh" else "stale",
                )
            )

        # Run deterministic findings engine
        conflicts = detect_conflicts(
            context.retrieval.work_items,
            context.retrieval.pull_requests,
            context.retrieval.commits,
            context.retrieval.links,
        )
        blockers = detect_blockers(
            context.retrieval.work_items,
            context.retrieval.pull_requests,
            context.retrieval.commits,
            context.retrieval.links,
            as_of=moment,
        )
        risks = detect_risks(
            context.retrieval.work_items,
            context.retrieval.pull_requests,
            context.retrieval.commits,
            context.retrieval.links,
            conflicts=conflicts,
            as_of=moment,
        )

        attn: list[AttentionItem] = []
        backing: list[EvidenceItem] = []

        if blockers:
            member_state = "blocked"
            state_reason = None
            for b in blockers:
                b_ev = [
                    ev
                    for ev in canonical_evidence
                    if ev.entity_key in b.evidence_keys
                    or any(k in ev.summary for k in b.evidence_keys)
                ]
                attn.append(
                    AttentionItem(
                        kind="blocker",
                        claim=b.description,
                        classification="fact",
                        confidence="HIGH",
                        evidence=b_ev,
                    )
                )
                backing.extend(b_ev)
        elif risks or conflicts:
            member_state = "needs_attention"
            state_reason = None
            for r in risks:
                r_ev = [
                    ev
                    for ev in canonical_evidence
                    if ev.entity_key in r.evidence_keys
                    or any(k in ev.summary for k in r.evidence_keys)
                ]
                attn.append(
                    AttentionItem(
                        kind="risk",
                        claim=r.description,
                        classification="inference",
                        confidence="HIGH",
                        evidence=r_ev,
                    )
                )
                backing.extend(r_ev)
            for c in conflicts:
                c_ev = [
                    ev
                    for ev in canonical_evidence
                    if ev.entity_key == c.work_item_external_id
                    or c.work_item_external_id in ev.summary
                ]
                attn.append(
                    AttentionItem(
                        kind="risk",
                        claim=c.description,
                        classification="fact",
                        confidence="HIGH",
                        evidence=c_ev,
                    )
                )
                backing.extend(c_ev)
        elif canonical_evidence:
            member_state = "on_track"
            state_reason = None
            backing = canonical_evidence[:3]
        else:
            member_state = "unknown"
            state_reason = "No Jira or GitHub records in the last 14 days"

        # Deduplicate backing evidence items by ID while preserving order
        seen_ids: set[str] = set()
        deduped_backing: list[EvidenceItem] = []
        for item in backing:
            if item.id not in seen_ids:
                seen_ids.add(item.id)
                deduped_backing.append(item)

        member_overviews.append(
            TeamMemberOverview(
                user_id=m.user_id,
                display_name=m.display_name,
                role_label=m.role_label,
                state=member_state,
                state_reason=state_reason,
                attention_count=len(attn),
                attention_items=attn,
                backing_evidence=deduped_backing,
            )
        )

    # 4. Overall status calculation
    if any(m.state == "blocked" for m in member_overviews):
        overall_status = "Blocked"
    elif any(m.state == "needs_attention" for m in member_overviews):
        overall_status = "Needs Attention"
    elif member_overviews and all(m.state == "unknown" for m in member_overviews):
        overall_status = "Unknown"
    else:
        overall_status = "On Track"

    display_team_name = team_name
    if repository_name:
        display_team_name = f"{team_name} ({repository_name})"

    return TeamOverview(
        team_id=team_id,
        team_name=display_team_name,
        overall_status=overall_status,
        source_health=source_health,
        last_synced=last_synced,
        unmatched_count=unmatched_count,
        members=member_overviews,
    )
