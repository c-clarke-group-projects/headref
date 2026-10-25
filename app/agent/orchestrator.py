"""Agent orchestrator, stages S1 and S2 (P3-004, FR-014, FR-023, DEC-010).

S1 builds the plan. S2 checks source health (T-007) and then calls the read tools the
plan asks for. Everything is carried in one AgentRunContext, which later stages extend.

The required-source gate: if a required source is unavailable, or a required tool fails,
the run must not reach the model (S4). It returns UNKNOWN with the reason instead, and
the model is never called. A missing health answer counts as unavailable: never assume
a source is healthy.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.blockers import detect_blockers
from app.agent.cache import (
    compute_evidence_hash,
    get_cached_insight,
    persist_agent_run,
    save_cached_insight,
)
from app.agent.confidence import evaluate_confidence
from app.agent.conflicts import detect_conflicts
from app.agent.deterministic_summary import generate_deterministic_summary
from app.agent.evidence_builder import build_evidence
from app.agent.narrative import deterministic_fallback, generate_narrative
from app.agent.planner import QuestionType, RetrievalPlan, Source, build_plan
from app.agent.risks import detect_risks
from app.agent.validation import extract_entities, validate_narrative
from app.config import get_settings
from app.schemas.errors import ToolFailure
from app.schemas.insight import ActivitySummary, EvidenceItem, Insight, MemberInsight
from app.schemas.tools import (
    CommitOut,
    GetAssignedWorkItemsInput,
    GetCommitsInput,
    GetPullRequestsInput,
    GetReviewsInput,
    GetSourceHealthInput,
    GetWorkItemLinksInput,
    PullRequestOut,
    ReviewOut,
    SourceHealthOut,
    WorkItemLinkOut,
    WorkItemOut,
)
from app.tools.get_assigned_work_items import get_assigned_work_items
from app.tools.get_commits import get_commits
from app.tools.get_pull_requests import get_pull_requests
from app.tools.get_reviews import get_reviews
from app.tools.get_source_health import get_source_health
from app.tools.get_work_item_links import get_work_item_links

logger = logging.getLogger("headref.agent")

# "All open items with a due date" (the risks question) reaches back further than any
# activity window, so it uses its own, very long, lookback.
OPEN_ITEMS_LOOKBACK_DAYS = 3650

# The sources each read tool depends on. A tool is only called when every one of its
# sources is usable, meaning fresh or stale but not unavailable.
TOOL_SOURCES: dict[str, tuple[Source, ...]] = {
    "T-002": ("jira",),
    "T-003": ("github",),
    "T-004": ("github",),
    "T-005": ("github",),
    "T-006": ("jira", "github"),
}


@dataclass(frozen=True)
class ReadTools:
    """The read tools stage S2 calls. Tests pass fakes here instead."""

    source_health: Callable[..., Any] = get_source_health
    assigned_work_items: Callable[..., Any] = get_assigned_work_items
    pull_requests: Callable[..., Any] = get_pull_requests
    commits: Callable[..., Any] = get_commits
    reviews: Callable[..., Any] = get_reviews
    work_item_links: Callable[..., Any] = get_work_item_links


class RetrievalResult(BaseModel):
    """Everything stage S2 retrieved for the subject."""

    work_items: list[WorkItemOut] = Field(default_factory=list)
    open_due_items: list[WorkItemOut] = Field(default_factory=list)
    pull_requests: list[PullRequestOut] = Field(default_factory=list)
    commits: list[CommitOut] = Field(default_factory=list)
    commits_truncated: bool = False
    commits_limit: int | None = None
    reviews: list[ReviewOut] = Field(default_factory=list)
    links: list[WorkItemLinkOut] = Field(default_factory=list)
    tools_called: list[str] = Field(default_factory=list)


class GateResult(BaseModel):
    """Whether the run may go on to reasoning, and if not, why."""

    proceed: bool = True
    reasons: list[str] = Field(default_factory=list)
    # Optional sources the run went on without. Shown to the reader, never hidden.
    skipped: list[str] = Field(default_factory=list)

    @property
    def reason(self) -> str | None:
        """All reasons as one line, or None when the gate is open."""
        return "; ".join(self.reasons) or None


class AgentRunContext(BaseModel):
    """Carries everything between stages: plan, retrieval, health and failures."""

    subject_user_id: int
    team_id: int | None
    question_type: QuestionType
    # W7: a workspace run reads that workspace's sync history, and Jira counts as in use
    # when the workspace has a Jira connection. None keeps the legacy behaviour.
    workspace_id: int | None = None
    jira_in_use: bool | None = None
    plan: RetrievalPlan
    started_at: datetime
    window_start: datetime
    window_end: datetime
    health: list[SourceHealthOut] = Field(default_factory=list)
    retrieval: RetrievalResult = Field(default_factory=RetrievalResult)
    failures: list[ToolFailure] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    gate: GateResult = Field(default_factory=GateResult)


def _call(context: AgentRunContext, tool_id: str, call: Callable[[], Any]) -> Any | None:
    """Run one tool. Record the call, and keep a typed failure instead of raising."""
    context.retrieval.tools_called.append(tool_id)
    result = call()
    if isinstance(result, ToolFailure):
        logger.warning("tool failed: tool_id=%s error_type=%s", tool_id, result.error_type)
        context.failures.append(result)
        return None
    return result


def _jira_in_use(context: AgentRunContext) -> bool:
    """Whether Jira is part of this run: the workspace's connection, else the environment."""
    if context.jira_in_use is not None:
        return context.jira_in_use
    settings = get_settings()
    return bool(settings and settings.jira_enabled)


def _check_health(session: Session, context: AgentRunContext, tools: ReadTools) -> set[Source]:
    """Call T-007 and return the sources that are safe to read from.

    If T-007 itself fails, nothing is usable: fail closed, never assume healthy.
    """
    jira_is_active = _jira_in_use(context)

    sources = [
        s
        for s in [*context.plan.required_sources, *context.plan.optional_sources]
        if (s != "jira" or jira_is_active)
    ]
    if not sources:
        sources = ["github"]
    data = GetSourceHealthInput(
        team_id=context.team_id, workspace_id=context.workspace_id, sources=sources
    )
    output = _call(context, "T-007", lambda: tools.source_health(session, data))
    if output is None:
        return set()
    context.health = [h for h in output.sources if (h.source != "jira" or jira_is_active)]
    return {health.source for health in context.health if health.state != "unavailable"}


def _retrieve(
    session: Session, context: AgentRunContext, tools: ReadTools, usable: set[Source]
) -> None:
    """Stage S2: call the tools the plan asks for, skipping any whose source is unusable."""
    plan, retrieval, subject = context.plan, context.retrieval, context.subject_user_id

    def allowed(tool_id: str) -> bool:
        if tool_id not in plan.tools:
            return False
        unusable = [source for source in TOOL_SOURCES[tool_id] if source not in usable]
        if unusable:
            context.notes.append(f"{tool_id} skipped: {', '.join(unusable)} data is not usable")
            return False
        return True

    if allowed("T-002"):
        data = GetAssignedWorkItemsInput(
            subject_user_id=subject,
            window_start=context.window_start,
            window_end=context.window_end,
        )
        output = _call(context, "T-002", lambda: tools.assigned_work_items(session, data))
        if output is not None:
            retrieval.work_items = list(output.items)

        if plan.include_open_due_items:
            open_data = GetAssignedWorkItemsInput(
                subject_user_id=subject,
                window_start=context.window_end - timedelta(days=OPEN_ITEMS_LOOKBACK_DAYS),
                window_end=context.window_end,
                include_done=False,
            )
            output = _call(context, "T-002", lambda: tools.assigned_work_items(session, open_data))
            if output is not None:
                retrieval.open_due_items = [
                    item for item in output.items if item.due_date is not None
                ]

    if allowed("T-003"):
        pr_data = GetPullRequestsInput(
            subject_user_id=subject,
            window_start=context.window_start,
            window_end=context.window_end,
        )
        output = _call(context, "T-003", lambda: tools.pull_requests(session, pr_data))
        if output is not None:
            retrieval.pull_requests = list(output.pull_requests)

    if allowed("T-004"):
        commit_data = GetCommitsInput(
            subject_user_id=subject,
            window_start=context.window_start,
            window_end=context.window_end,
        )
        output = _call(context, "T-004", lambda: tools.commits(session, commit_data))
        if output is not None:
            retrieval.commits = list(output.commits)
            retrieval.commits_truncated = output.truncated
            retrieval.commits_limit = commit_data.limit

    if allowed("T-005"):
        review_data = GetReviewsInput(
            subject_user_id=subject,
            window_start=context.window_start,
            window_end=context.window_end,
        )
        output = _call(context, "T-005", lambda: tools.reviews(session, review_data))
        if output is not None:
            retrieval.reviews = list(output.reviews)

    if allowed("T-006"):
        work_item_ids = sorted(
            {item.work_item_id for item in [*retrieval.work_items, *retrieval.open_due_items]}
        )
        pull_request_ids = sorted({pr.pull_request_id for pr in retrieval.pull_requests})
        # T-006 needs at least one id. With none, there is nothing to link.
        if work_item_ids or pull_request_ids:
            link_data = GetWorkItemLinksInput(
                work_item_ids=work_item_ids or None,
                pull_request_ids=pull_request_ids or None,
            )
            output = _call(context, "T-006", lambda: tools.work_item_links(session, link_data))
            if output is not None:
                retrieval.links = list(output.links)


def _unavailable_reason(source: Source, health: SourceHealthOut | None) -> str:
    """Explain in one phrase why a source is unavailable, from typed fields only."""
    if health is None:
        return f"{source} has no health record"
    if health.last_error_type:
        return f"{source} is unavailable (last error: {health.last_error_type})"
    return f"{source} is unavailable (no successful sync recorded)"


def _evaluate_gate(context: AgentRunContext, usable: set[Source]) -> None:
    """Decide whether the run may go on to reasoning (the required-source gate)."""
    plan = context.plan
    by_source = {health.source: health for health in context.health}
    reasons: list[str] = []
    skipped: list[str] = []

    health_failure = next((f for f in context.failures if f.tool_id == "T-007"), None)
    if health_failure is not None:
        reasons.append(f"source health could not be determined ({health_failure.error_type})")
    else:
        for source in plan.required_sources:
            if source not in usable:
                reasons.append(_unavailable_reason(source, by_source.get(source)))
        for source in plan.optional_sources:
            if source not in usable:
                if source == "jira" and not _jira_in_use(context):
                    continue
                context.notes.append(
                    f"{source} is unavailable and optional for {plan.question_type}; "
                    f"continuing without {source} data"
                )
                skipped.append(
                    f"{_unavailable_reason(source, by_source.get(source))}, "
                    f"so this answer does not use {source} data"
                )

    for failure in context.failures:
        if failure.tool_id == "T-007":
            continue
        sources = TOOL_SOURCES.get(failure.tool_id)
        # An unknown tool id is treated as required, so the gate fails closed.
        if sources is None or any(source in plan.required_sources for source in sources):
            reasons.append(f"{failure.tool_id} failed ({failure.error_type})")
        else:
            context.notes.append(
                f"{failure.tool_id} failed ({failure.error_type}); continuing without it"
            )

    context.gate = GateResult(proceed=not reasons, reasons=reasons, skipped=skipped)


def run_s1_s2(
    session: Session,
    *,
    subject_user_id: int,
    team_id: int | None,
    question_type: str,
    tools: ReadTools | None = None,
    now: datetime | None = None,
    workspace_id: int | None = None,
    jira_in_use: bool | None = None,
) -> AgentRunContext:
    """Run stages S1 (plan) and S2 (retrieve), and return the filled AgentRunContext.

    An invalid question type raises InvalidQuestionTypeError (422) before any tool runs.
    Check context.gate.proceed before going on to S3 and later stages.
    """
    plan = build_plan(question_type)
    tools = tools if tools is not None else ReadTools()
    moment = now if now is not None else datetime.now(UTC)
    context = AgentRunContext(
        subject_user_id=subject_user_id,
        team_id=team_id,
        question_type=plan.question_type,
        plan=plan,
        started_at=moment,
        window_start=moment - timedelta(days=plan.window_days),
        window_end=moment,
        workspace_id=workspace_id,
        jira_in_use=jira_in_use,
    )

    usable = _check_health(session, context, tools)
    _retrieve(session, context, tools, usable)
    _evaluate_gate(context, usable)

    logger.info(
        "s1_s2 done: question=%s proceed=%s tools=%s failures=%d",
        plan.question_type,
        context.gate.proceed,
        context.retrieval.tools_called,
        len(context.failures),
    )
    return context


def unknown_response(context: AgentRunContext) -> MemberInsight:
    """Build the UNKNOWN answer for a run whose gate is closed (FR-023).

    The reason goes into the slot for the question that was asked, so an unavailable
    source can never be mistaken for "no blockers found".
    """
    reason = context.gate.reason or "required data is unavailable"

    def unknown() -> Insight:
        return Insight(
            claim=f"Cannot be established: {reason}",
            classification="unknown",
            confidence="UNKNOWN",
        )

    return MemberInsight(
        user_id=context.subject_user_id,
        likely_current_work=unknown() if context.question_type == "current_work" else None,
        blockers=[unknown()] if context.question_type == "blockers" else [],
        risks=[unknown()] if context.question_type == "risks" else [],
        unknowns=[reason],
        last_synced={health.source: health.last_success_at for health in context.health},
        source_health=list(context.health),
    )


def _commit_on_branch(evidence: EvidenceItem, branch: str) -> bool:
    """True for commit evidence on exactly this branch (see the commit summary format)."""
    return evidence.entity_type == "commit" and evidence.summary.endswith(f"on branch {branch}")


def github_current_work(
    retrieval: RetrievalResult,
    evidence: list[EvidenceItem],
    *,
    is_truncated: bool,
    as_of: datetime,
) -> Insight | None:
    """Infer likely current work from GitHub alone, for a member with no Jira work item.

    The open pull request with the latest activity wins; with none, the branch of the
    latest commit. Always labelled an inference: GitHub shows activity, not intent.
    """
    open_prs = [pr for pr in retrieval.pull_requests if pr.state == "open"]
    if open_prs:
        pr = max(open_prs, key=lambda p: p.last_commit_at or p.source_updated_at)
        cited = [
            ev
            for ev in evidence
            if (ev.entity_type == "pull_request" and ev.entity_key == str(pr.number))
            or (pr.branch_name and _commit_on_branch(ev, pr.branch_name))
        ]
        draft = " (draft)" if pr.is_draft else ""
        claim = (
            f"Likely working on pull request #{pr.number}{draft} "
            f"in {pr.repo_full_name}: {pr.title}."
        )
    elif retrieval.commits:
        commit = max(retrieval.commits, key=lambda c: c.committed_at)
        if commit.branch_name:
            cited = [ev for ev in evidence if _commit_on_branch(ev, commit.branch_name)]
            claim = (
                f"Likely working on branch {commit.branch_name} in {commit.repo_full_name}, "
                "from recent commits with no open pull request."
            )
        else:
            cited = [ev for ev in evidence if ev.entity_key == commit.sha[:8]]
            claim = (
                f"Likely working in {commit.repo_full_name}, "
                f"latest commit {commit.sha[:8]} with no open pull request."
            )
    else:
        return None

    return Insight(
        claim=claim,
        classification="inference",
        confidence=evaluate_confidence(
            cited, claim_type="current_work", is_truncated=is_truncated, as_of=as_of
        ),
        evidence=cited,
    )


# Tools the activity counts are built from. If any failed or did not run, the counts
# would be silently short, so no counts are shown (rule 9). T-006 is only needed when
# there are pull requests to check for links.
def narrative_allowed_entities(retrieval: RetrievalResult, findings_text: str) -> set[str]:
    """Ticket keys and pull request numbers the narrative may name.

    The member's own work items and pull requests, and every entity the findings text
    names: the model summarises that text, so repeating what it says is not inventing.
    A blocker such as "AUTH-246 is blocked by dependency: AUTH-245" names another
    member's issue; without this, a correct narrative was rejected as INVENTED_ENTITY.
    """
    allowed = {w.external_id for w in retrieval.work_items}
    allowed |= {f"#{pr.number}" for pr in retrieval.pull_requests}
    return allowed | extract_entities(findings_text)


NO_ACTIVITY_UNKNOWN = (
    "No recent Jira or GitHub activity is recorded for this member. Work not tracked in "
    "these tools is not visible to headref."
)

ACTIVITY_TOOLS = ("T-003", "T-004", "T-005")
LINKS_TOOL = "T-006"


def _in_window(moment: datetime | None, context: AgentRunContext) -> bool:
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return context.window_start <= moment <= context.window_end


def build_activity(context: AgentRunContext, subject_user_id: int) -> ActivitySummary | None:
    """Count this member's own activity inside the window, by code.

    Rows a tool returned from outside the window (its empty window fallback) are not
    counted, so the card never states more activity for the window than happened.
    """
    retrieval = context.retrieval
    failed = {f.tool_id for f in context.failures}
    needed = [*ACTIVITY_TOOLS, LINKS_TOOL] if retrieval.pull_requests else list(ACTIVITY_TOOLS)
    if any(t in failed or t not in retrieval.tools_called for t in needed):
        return None

    pull_requests = [
        pr for pr in retrieval.pull_requests if _in_window(pr.source_updated_at, context)
    ]
    linked_pr_ids = {
        link.target_id for link in retrieval.links if link.target_type == "pull_request"
    }
    return ActivitySummary(
        window_days=context.plan.window_days,
        open_pull_requests=sum(1 for pr in pull_requests if pr.state == "open"),
        commits=sum(1 for c in retrieval.commits if _in_window(c.committed_at, context)),
        reviews_given=sum(
            1
            for r in retrieval.reviews
            if r.reviewer_user_id == subject_user_id and _in_window(r.submitted_at, context)
        ),
        unlinked_pull_requests=[
            pr for pr in pull_requests if pr.pull_request_id not in linked_pr_ids
        ],
    )


def build_truncated(context: AgentRunContext) -> dict[str, int] | None:
    """Name what a read tool capped and the cap, so the page can say so (FR-023)."""
    retrieval = context.retrieval
    if retrieval.commits_truncated and retrieval.commits_limit:
        return {"commits": retrieval.commits_limit}
    return None


async def run_agent(
    session: Session,
    *,
    subject_user_id: int,
    team_id: int | None,
    question_type: str,
    actor_user_id: int | None = None,
    tools: ReadTools | None = None,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
    skip_llm: bool = False,
    repository_name: str | None = None,
    workspace_id: int | None = None,
    jira_in_use: bool | None = None,
) -> MemberInsight:
    """Execute end-to-end agent pipeline stages S1 through S6 (DEC-018, FR-014, FR-024).

    1. S1 (plan) and S2 (retrieve)
    2. Check gate: if closed, persist run and return UNKNOWN
    3. S3 (build evidence) with deduplication, ranking, and stable IDs
    4. S6 Cache lookup: return cached answer immediately on hash match
    5. S4a (deterministic findings engine): conflicts, blockers, risks, confidence
    6. S4b (narrative generation): Ollama or deterministic fallback
    7. S5 (validation): safe, grounded narrative or fallback
    8. S6 (persistence & cache): persist agent_run, store insight cache, return MemberInsight
    """
    context = run_s1_s2(
        session,
        subject_user_id=subject_user_id,
        team_id=team_id,
        question_type=question_type,
        tools=tools,
        now=now,
        workspace_id=workspace_id,
        jira_in_use=jira_in_use,
    )

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

    # 1. Closed Gate handling (S2 gate)
    if not context.gate.proceed:
        insight = unknown_response(context)
        persist_agent_run(
            session=session,
            subject_user_id=subject_user_id,
            question_type=question_type,
            window_start=context.window_start,
            window_end=context.window_end,
            evidence_set=[],
            source_health=context.health,
            actor_user_id=actor_user_id,
            error_type="GATE_CLOSED",
        )
        return insight

    # 2. Stage S3: Build Evidence
    evidence_set = build_evidence(context)
    evidence_items = list(evidence_set.items)

    canonical_evidence: list[EvidenceItem] = []
    for ev in evidence_items:
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

    # 3. Stage S6 Cache Lookup (before S4)
    ev_hash = compute_evidence_hash(subject_user_id, question_type, canonical_evidence)
    cached = get_cached_insight(session, subject_user_id, question_type, ev_hash)
    if cached is not None:
        logger.info(
            "Cache hit for subject=%d question=%s hash=%s. Skipping model call.",
            subject_user_id,
            question_type,
            ev_hash[:8],
        )
        persist_agent_run(
            session=session,
            subject_user_id=subject_user_id,
            question_type=question_type,
            window_start=context.window_start,
            window_end=context.window_end,
            evidence_set=canonical_evidence,
            source_health=context.health,
            actor_user_id=actor_user_id,
        )
        return cached

    # 4. Stage S4a: Deterministic Findings Engine
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
        as_of=context.started_at,
    )
    risks = detect_risks(
        context.retrieval.work_items,
        context.retrieval.pull_requests,
        context.retrieval.commits,
        context.retrieval.links,
        conflicts=conflicts,
        as_of=context.started_at,
    )

    det_summary = generate_deterministic_summary(
        context.retrieval.work_items,
        context.retrieval.pull_requests,
        context.retrieval.commits,
        context.retrieval.reviews,
        is_fallback=False,
    )
    det_fallback = generate_deterministic_summary(
        context.retrieval.work_items,
        context.retrieval.pull_requests,
        context.retrieval.commits,
        context.retrieval.reviews,
        is_fallback=True,
    )

    # Resolve likely current work
    likely_current_work = None
    active_items = [w for w in context.retrieval.work_items if w.status == "in_progress"]
    target_item = (
        active_items[0]
        if active_items
        else (context.retrieval.work_items[0] if context.retrieval.work_items else None)
    )
    if target_item is not None:
        # Evidence summaries carry structured fields only, never a title (untrusted text),
        # so a pull request or commit is tied to the item through its stored link.
        linked = {
            (link.target_type, link.target_id)
            for link in context.retrieval.links
            if link.work_item_id == target_item.work_item_id
        }
        linked_urls = {
            pr.source_url
            for pr in context.retrieval.pull_requests
            if ("pull_request", pr.pull_request_id) in linked
        } | {
            commit.source_url
            for commit in context.retrieval.commits
            if ("commit", commit.commit_id) in linked
        }
        target_ev = [
            ev
            for ev in canonical_evidence
            if ev.entity_key == target_item.external_id
            or target_item.external_id in ev.summary
            or (ev.entity_type in ("pull_request", "commit") and ev.source_url in linked_urls)
        ]
        has_conf = any(c.work_item_external_id == target_item.external_id for c in conflicts)
        conf = evaluate_confidence(
            target_ev,
            claim_type="work_item_assignment",
            has_unresolved_conflict=has_conf,
            is_truncated=evidence_set.truncated,
            as_of=context.started_at,
        )
        # Current work is an inference only with 2 or more evidence items (11.6). With 1,
        # say only what that record shows, as a fact, not "likely working on" (6.6).
        if len(target_ev) >= 2:
            claim = f"Likely working on {target_item.external_id}: {target_item.title}."
            classification = "inference"
        else:
            claim = (
                f"{target_item.external_id} is assigned and {target_item.raw_status}: "
                f"{target_item.title}."
            )
            classification = "fact"
        # The claim carries its own conflict, as blockers and risks do, so "likely working
        # on" is never read on its own as "still in progress" (EV-05).
        likely_current_work = Insight(
            claim=claim,
            classification=classification,
            confidence=conf,
            evidence=target_ev,
            conflicts=[
                c.description
                for c in conflicts
                if c.work_item_external_id == target_item.external_id
            ],
        )
    else:
        likely_current_work = github_current_work(
            context.retrieval,
            canonical_evidence,
            is_truncated=evidence_set.truncated,
            as_of=context.started_at,
        )

    # Resolve blocker insights
    blocker_insights: list[Insight] = []
    for b in blockers:
        b_ev = [
            ev
            for ev in canonical_evidence
            if ev.entity_key in b.evidence_keys or any(k in ev.summary for k in b.evidence_keys)
        ]
        conf = evaluate_confidence(
            b_ev,
            claim_type="declared_blocker",
            has_unresolved_conflict=any(c.work_item_external_id == b.entity_key for c in conflicts),
            is_truncated=evidence_set.truncated,
            as_of=context.started_at,
        )
        conf_descs = [c.description for c in conflicts if c.work_item_external_id == b.entity_key]
        blocker_insights.append(
            Insight(
                claim=b.description,
                classification="fact",
                confidence=conf,
                evidence=b_ev,
                conflicts=conf_descs,
            )
        )

    # Resolve risk insights
    risk_insights: list[Insight] = []
    for r in risks:
        r_ev = [
            ev
            for ev in canonical_evidence
            if ev.entity_key in r.evidence_keys or any(k in ev.summary for k in r.evidence_keys)
        ]
        conf = evaluate_confidence(
            r_ev,
            claim_type="due_date",
            has_unresolved_conflict=any(c.work_item_external_id == r.entity_key for c in conflicts),
            is_truncated=evidence_set.truncated,
            as_of=context.started_at,
        )
        conf_descs = [c.description for c in conflicts if c.work_item_external_id == r.entity_key]
        risk_insights.append(
            Insight(
                claim=r.description,
                classification="inference",
                confidence=conf,
                evidence=r_ev,
                conflicts=conf_descs,
            )
        )

    findings_sections = [det_summary.text]
    if blockers:
        findings_sections.append("Blockers: " + "; ".join(b.description for b in blockers))
    if risks:
        findings_sections.append("Risks: " + "; ".join(r.description for r in risks))
    findings_prompt_text = "\n\n".join(findings_sections)
    allowed_entities = narrative_allowed_entities(context.retrieval, findings_prompt_text)

    # 5. Stage S4b: Narrative Generation
    if skip_llm:
        narrative_res = deterministic_fallback(det_summary.text)
    else:
        narrative_res = await generate_narrative(
            findings_prompt_text,
            fallback_text=det_fallback.text,
            client=client,
        )

    # 6. Stage S5: Narrative Validation
    validated = validate_narrative(
        summary=narrative_res.summary,
        needs_attention=narrative_res.needs_attention,
        attention_needed=narrative_res.attention_needed,
        allowed_entities=allowed_entities,
        fallback_text=det_fallback.text,
        findings_text=findings_prompt_text,
    )

    # 7. Stage S6: Assembly, Persistence, and Cache
    unknowns = ([context.gate.reason] if context.gate.reason else []) + context.gate.skipped
    if likely_current_work is None and not context.failures:
        # EV-03: no record is not evidence of no work. Say so instead of leaving a gap.
        unknowns.append(NO_ACTIVITY_UNKNOWN)
    final_insight = MemberInsight(
        user_id=subject_user_id,
        likely_current_work=likely_current_work,
        assigned=context.retrieval.work_items,
        blockers=blocker_insights,
        risks=risk_insights,
        unknowns=unknowns,
        last_synced={h.source: h.last_success_at for h in context.health},
        source_health=list(context.health),
        summary=validated.summary,
        fallback_used=narrative_res.fallback_used or validated.fallback_used,
        conflicts=[c.description for c in conflicts],
        activity=build_activity(context, subject_user_id),
        truncated=build_truncated(context),
    )

    # Why the narrative was not used, if it was not: the model call failed, or the
    # validator rejected what it wrote.
    narrative_error_type = narrative_res.error_type
    if narrative_error_type is None and validated.dropped_claims:
        narrative_error_type = validated.dropped_claims[0].get("reason")
    logger.info(
        "s4_s5 done: question=%s model_called=%s latency_ms=%d dropped=%d fallback=%s",
        question_type,
        not skip_llm,
        narrative_res.latency_ms,
        len(validated.dropped_claims),
        final_insight.fallback_used,
    )

    # A code-only run (skip_llm) is not cached: its summary is the deterministic text,
    # and caching it under the same evidence hash would serve that text in place of the
    # model's summary when the member is opened (W7).
    if not skip_llm:
        save_cached_insight(
            session=session,
            subject_user_id=subject_user_id,
            question_type=question_type,
            evidence_hash=ev_hash,
            insight=final_insight,
        )

    persist_agent_run(
        session=session,
        subject_user_id=subject_user_id,
        question_type=question_type,
        window_start=context.window_start,
        window_end=context.window_end,
        evidence_set=canonical_evidence,
        source_health=context.health,
        raw_model_output=narrative_res.raw_response,
        validated_output=validated.model_dump(),
        dropped_claims=validated.dropped_claims,
        input_tokens=narrative_res.prompt_eval_count,
        output_tokens=narrative_res.eval_count,
        latency_ms=narrative_res.latency_ms,
        actor_user_id=actor_user_id,
        model=get_settings().model_id,
        prompt_version=get_settings().prompt_version,
        error_type=narrative_error_type,
    )

    return final_insight
