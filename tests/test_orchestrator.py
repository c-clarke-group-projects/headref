"""Tests for the orchestrator, stages S1 and S2 (P3-004).

No test touches a database or Ollama. The read tools are replaced by fakes that record
every call, so the tests can say exactly which tools ran, in which order, with what input.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.agent.orchestrator import (
    AgentRunContext,
    ReadTools,
    RetrievalResult,
    build_activity,
    build_truncated,
    github_current_work,
    run_agent,
    run_s1_s2,
    unknown_response,
)
from app.agent.planner import PLANS, InvalidQuestionTypeError
from app.schemas.errors import ToolFailure
from app.schemas.insight import EvidenceItem
from app.schemas.tools import (
    CommitOut,
    GetAssignedWorkItemsOutput,
    GetCommitsOutput,
    GetPullRequestsOutput,
    GetReviewsOutput,
    GetSourceHealthOutput,
    GetWorkItemLinksOutput,
    PullRequestOut,
    ReviewOut,
    SourceHealthOut,
    WorkItemLinkOut,
    WorkItemOut,
)

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
NO_DB: Any = None  # the fake tools never use the session

FULL_TOOL_CALLS = ["T-007", "T-002", "T-003", "T-004", "T-005", "T-006"]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _health(source: str, state: str = "fresh", error: str | None = None) -> SourceHealthOut:
    return SourceHealthOut(
        source=source,  # type: ignore[arg-type]
        state=state,  # type: ignore[arg-type]
        last_success_at=NOW - timedelta(hours=1),
        last_error_type=error,
    )


def _work_item(work_item_id: int = 1, due_date: datetime | None = None) -> WorkItemOut:
    return WorkItemOut(
        work_item_id=work_item_id,
        external_id=f"AUTH-{work_item_id}",
        title="Refresh token rotation",
        status="in_progress",
        raw_status="In Progress",
        due_date=due_date,
        source_url="https://example.atlassian.net/browse/AUTH-1",
        source_updated_at=NOW,
        retrieved_at=NOW,
    )


def _pull_request(pull_request_id: int = 7) -> PullRequestOut:
    return PullRequestOut(
        pull_request_id=pull_request_id,
        number=182,
        repo_full_name="acme/api",
        title="AUTH-1 rotate refresh tokens",
        state="open",
        is_draft=False,
        review_state="none",
        checks_state="unknown",
        created_at=NOW,
        source_url="https://github.com/acme/api/pull/182",
        source_updated_at=NOW,
        retrieved_at=NOW,
    )


def _commit() -> CommitOut:
    return CommitOut(
        commit_id=1,
        sha="abc123",
        repo_full_name="acme/api",
        message_excerpt="AUTH-1 add rotation",
        committed_at=NOW,
        source_url="https://github.com/acme/api/commit/abc123",
        retrieved_at=NOW,
    )


def _review() -> ReviewOut:
    return ReviewOut(
        review_id=1,
        pull_request_id=7,
        pull_request_number=182,
        repo_full_name="acme/api",
        state="changes_requested",
        submitted_at=NOW,
        source_url="https://github.com/acme/api/pull/182#review-1",
        retrieved_at=NOW,
    )


def _link() -> WorkItemLinkOut:
    return WorkItemLinkOut(
        link_id=1,
        work_item_id=1,
        work_item_external_id="AUTH-1",
        target_type="pull_request",
        target_id=7,
        link_method="branch_name",
        confidence="HIGH",
        created_at=NOW,
    )


def _failure(tool_id: str, error_type: str = "TIMEOUT") -> ToolFailure:
    return ToolFailure(
        tool_id=tool_id,
        error_type=error_type,  # type: ignore[arg-type]
        detail="Query exceeded database statement timeout",
    )


def make_tools(
    *,
    health: list[SourceHealthOut] | None = None,
    work_items: list[WorkItemOut] | None = None,
    open_items: list[WorkItemOut] | None = None,
    pull_requests: list[PullRequestOut] | None = None,
    commits: list[CommitOut] | None = None,
    truncated: bool = False,
    reviews: list[ReviewOut] | None = None,
    links: list[WorkItemLinkOut] | None = None,
    failures: dict[str, ToolFailure] | None = None,
) -> tuple[ReadTools, list[str], dict[str, list[Any]]]:
    """Build fake read tools. Returns the tools, the ordered call log and the inputs."""
    calls: list[str] = []
    inputs: dict[str, list[Any]] = {}
    failing = failures or {}

    def record(tool_id: str, data: Any) -> ToolFailure | None:
        calls.append(tool_id)
        inputs.setdefault(tool_id, []).append(data)
        return failing.get(tool_id)

    def source_health(session: Any, data: Any) -> Any:
        failure = record("T-007", data)
        if failure:
            return failure
        default = [_health("jira"), _health("github")]
        return GetSourceHealthOutput(sources=health if health is not None else default)

    def assigned(session: Any, data: Any) -> Any:
        failure = record("T-002", data)
        if failure:
            return failure
        chosen = work_items if data.include_done else open_items
        return GetAssignedWorkItemsOutput(items=chosen or [])

    def pull(session: Any, data: Any) -> Any:
        failure = record("T-003", data)
        return failure or GetPullRequestsOutput(pull_requests=pull_requests or [])

    def commit(session: Any, data: Any) -> Any:
        failure = record("T-004", data)
        return failure or GetCommitsOutput(commits=commits or [], truncated=truncated)

    def review(session: Any, data: Any) -> Any:
        failure = record("T-005", data)
        return failure or GetReviewsOutput(reviews=reviews or [])

    def link(session: Any, data: Any) -> Any:
        failure = record("T-006", data)
        return failure or GetWorkItemLinksOutput(links=links or [])

    tools = ReadTools(
        source_health=source_health,
        assigned_work_items=assigned,
        pull_requests=pull,
        commits=commit,
        reviews=review,
        work_item_links=link,
    )
    return tools, calls, inputs


def _run(question: str, tools: ReadTools) -> AgentRunContext:
    return run_s1_s2(
        NO_DB, subject_user_id=5, team_id=10, question_type=question, tools=tools, now=NOW
    )


# ---------------------------------------------------------------------------
# S1 and S2 on the happy path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("question", ["current_work", "blockers"])
def test_current_work_and_blockers_call_every_data_tool_in_order(question: str) -> None:
    """S2 calls T-007 first, then T-002 to T-006, for the two 14 day questions."""
    tools, calls, inputs = make_tools(
        work_items=[_work_item(1)], pull_requests=[_pull_request(7)], links=[_link()]
    )

    context = _run(question, tools)

    assert calls == FULL_TOOL_CALLS
    assert context.gate.proceed is True
    assert inputs["T-002"][0].window_start == NOW - timedelta(days=14)
    assert inputs["T-002"][0].window_end == NOW
    assert inputs["T-002"][0].subject_user_id == 5


def test_risks_call_the_risk_tools_and_the_open_items_query() -> None:
    """The risks plan uses a 30 day window, adds the open-items query, and skips T-005."""
    due_soon = _work_item(2, due_date=NOW + timedelta(days=2))
    undated = _work_item(3, due_date=None)
    tools, calls, inputs = make_tools(
        work_items=[_work_item(1)],
        open_items=[due_soon, undated],
        pull_requests=[_pull_request(7)],
    )

    context = _run("risks", tools)

    assert calls == ["T-007", "T-002", "T-002", "T-003", "T-004", "T-006"]
    window_call, open_call = inputs["T-002"]
    assert window_call.window_start == NOW - timedelta(days=30)
    assert open_call.include_done is False
    assert [item.work_item_id for item in context.retrieval.open_due_items] == [2]
    assert inputs["T-006"][0].work_item_ids == [1, 2]


@pytest.mark.parametrize("question", list(PLANS))
def test_source_health_is_called_first_on_every_run(question: str) -> None:
    """T-007 runs on every run, before any data tool, for the sources in the plan."""
    tools, calls, inputs = make_tools()

    _run(question, tools)

    assert calls[0] == "T-007"
    assert inputs["T-007"][0].team_id == 10
    assert set(inputs["T-007"][0].sources) == {"jira", "github"}


def test_the_context_carries_plan_retrieval_health_and_failures() -> None:
    """AgentRunContext holds everything later stages need."""
    tools, _, _ = make_tools(
        work_items=[_work_item(1)],
        pull_requests=[_pull_request(7)],
        commits=[_commit()],
        reviews=[_review()],
        links=[_link()],
    )

    context = _run("current_work", tools)

    assert context.plan is PLANS["current_work"]
    assert context.question_type == "current_work"
    assert context.subject_user_id == 5
    assert [health.source for health in context.health] == ["jira", "github"]
    assert len(context.retrieval.work_items) == 1
    assert len(context.retrieval.pull_requests) == 1
    assert len(context.retrieval.commits) == 1
    assert len(context.retrieval.reviews) == 1
    assert len(context.retrieval.links) == 1
    assert context.retrieval.tools_called == FULL_TOOL_CALLS
    assert context.failures == []


def test_a_truncated_commit_result_is_passed_on() -> None:
    """Confidence rules later need to know when T-004 hit its limit."""
    tools, _, _ = make_tools(commits=[_commit()], truncated=True)

    context = _run("current_work", tools)

    assert context.retrieval.commits_truncated is True
    assert build_truncated(context) == {"commits": 100}


def test_an_untruncated_result_reports_no_truncation() -> None:
    tools, _, _ = make_tools(commits=[_commit()])
    assert build_truncated(_run("current_work", tools)) is None


def test_activity_counts_only_this_members_rows_inside_the_window() -> None:
    old_commit = _commit()
    old_commit.committed_at = NOW - timedelta(days=90)  # an empty-window fallback row
    review_given = _review()
    review_given.reviewer_user_id = 5
    review_received = _review()
    review_received.reviewer_user_id = 9
    unlinked = _pull_request(8)
    tools, _, _ = make_tools(
        pull_requests=[_pull_request(7), unlinked],
        commits=[_commit(), old_commit],
        reviews=[review_given, review_received],
        links=[_link()],  # links pull request 7 only
    )

    activity = build_activity(_run("current_work", tools), 5)

    assert activity is not None
    assert activity.window_days == PLANS["current_work"].window_days
    assert activity.open_pull_requests == 2
    assert activity.commits == 1
    assert activity.reviews_given == 1
    assert [pr.pull_request_id for pr in activity.unlinked_pull_requests] == [8]


def test_activity_is_withheld_when_a_tool_it_needs_failed() -> None:
    """Short counts would read as less activity, so none are shown (rule 9)."""
    tools, _, _ = make_tools(commits=[_commit()], failures={"T-005": _failure("T-005")})
    assert build_activity(_run("current_work", tools), 5) is None


def test_links_are_looked_up_from_the_retrieved_ids() -> None:
    """T-006 receives the work item and pull request ids that S2 just found."""
    tools, _, inputs = make_tools(work_items=[_work_item(1)], pull_requests=[_pull_request(7)])

    _run("current_work", tools)

    assert inputs["T-006"][0].work_item_ids == [1]
    assert inputs["T-006"][0].pull_request_ids == [7]


def test_links_are_not_looked_up_when_there_is_nothing_to_link() -> None:
    """T-006 rejects an empty id list, so it is not called at all."""
    tools, calls, _ = make_tools()

    context = _run("current_work", tools)

    assert calls == ["T-007", "T-002", "T-003", "T-004", "T-005"]
    assert context.failures == []


def test_an_invalid_question_type_is_rejected_before_any_tool_runs() -> None:
    """422 comes first: no tool, not even T-007, is called."""
    tools, calls, _ = make_tools()

    with pytest.raises(InvalidQuestionTypeError):
        _run("everything", tools)

    assert calls == []


# ---------------------------------------------------------------------------
# The required-source gate
# ---------------------------------------------------------------------------


def test_an_unavailable_required_source_closes_the_gate() -> None:
    """GitHub is required for current_work. Unavailable means UNKNOWN, with the reason."""
    tools, calls, _ = make_tools(
        health=[_health("jira"), _health("github", "unavailable", "TIMEOUT")],
        work_items=[_work_item(1)],
    )

    context = _run("current_work", tools)

    assert context.gate.proceed is False
    assert context.gate.reason == "github is unavailable (last error: TIMEOUT)"
    # Nothing is read from the unavailable source, and links need both sources.
    assert calls == ["T-007", "T-002"]


@pytest.mark.parametrize(
    ("question", "slot", "required"),
    [
        ("current_work", "likely_current_work", "github"),
        ("blockers", "blockers", "github"),
        ("risks", "risks", "jira"),
    ],
)
def test_the_unknown_answer_puts_the_reason_in_the_asked_slot(
    question: str, slot: str, required: str
) -> None:
    """A closed gate answers UNKNOWN, never an empty list that reads as "nothing found"."""
    other = "jira" if required == "github" else "github"
    tools, _, _ = make_tools(
        health=[_health(required, "unavailable", "AUTH_FAILED"), _health(other)]
    )
    context = _run(question, tools)
    assert context.gate.proceed is False

    answer = unknown_response(context)

    value = getattr(answer, slot)
    insight = value[0] if isinstance(value, list) else value
    assert insight.classification == "unknown"
    assert insight.confidence == "UNKNOWN"
    assert f"{required} is unavailable (last error: AUTH_FAILED)" in insight.claim
    assert answer.unknowns == [context.gate.reason]
    assert answer.user_id == 5
    assert answer.source_health == context.health
    assert set(answer.last_synced) == {"jira", "github"}


def test_an_unavailable_optional_source_continues_with_a_note() -> None:
    """GitHub is optional for risks: the run goes on without it and says so."""
    tools, calls, _ = make_tools(
        health=[_health("jira"), _health("github", "unavailable", "RATE_LIMITED")],
        work_items=[_work_item(1)],
    )

    context = _run("risks", tools)

    assert context.gate.proceed is True
    assert calls == ["T-007", "T-002", "T-002"]
    assert any("github is unavailable and optional" in note for note in context.notes)


def test_a_stale_required_source_does_not_close_the_gate() -> None:
    """Stale data is still usable. Only confidence drops, later."""
    tools, calls, _ = make_tools(health=[_health("jira", "stale"), _health("github")])

    context = _run("current_work", tools)

    assert context.gate.proceed is True
    assert calls[:3] == ["T-007", "T-002", "T-003"]


def test_a_required_source_missing_from_the_health_answer_counts_as_unavailable() -> None:
    """Never assume healthy: no health record for a required source closes the gate."""
    tools, calls, _ = make_tools(health=[_health("jira")])

    context = _run("current_work", tools)

    assert context.gate.proceed is False
    assert context.gate.reason == "github has no health record"
    assert "T-003" not in calls


def test_a_failing_health_tool_fails_closed() -> None:
    """If T-007 itself fails, nothing is read and the answer is UNKNOWN."""
    tools, calls, _ = make_tools(failures={"T-007": _failure("T-007")})

    context = _run("current_work", tools)

    assert calls == ["T-007"]
    assert context.gate.proceed is False
    assert context.gate.reason == "source health could not be determined (TIMEOUT)"
    assert context.health == []
    assert [failure.tool_id for failure in context.failures] == ["T-007"]


def test_a_failing_tool_on_a_required_source_closes_the_gate() -> None:
    """A failed T-003 is recorded, the other tools still run, and the gate closes."""
    tools, calls, _ = make_tools(work_items=[_work_item(1)], failures={"T-003": _failure("T-003")})

    context = _run("blockers", tools)

    assert context.gate.proceed is False
    assert context.gate.reason == "T-003 failed (TIMEOUT)"
    assert "T-004" in calls
    assert [failure.tool_id for failure in context.failures] == ["T-003"]


@pytest.mark.parametrize("question", ["current_work", "blockers"])
def test_unavailable_jira_does_not_stop_a_github_answer(question: str) -> None:
    """Jira is optional here (DEC-020): the run goes on and says Jira was not used."""
    tools, calls, _ = make_tools(
        health=[_health("jira", "unavailable"), _health("github")],
        pull_requests=[_pull_request(7)],
    )

    context = _run(question, tools)

    assert context.gate.proceed is True
    assert "T-002" not in calls
    assert "T-003" in calls
    assert context.gate.skipped == [
        "jira is unavailable (no successful sync recorded), so this answer does not use jira data"
    ]


def test_a_failing_tool_on_an_optional_source_is_recorded_but_does_not_close_the_gate() -> None:
    """GitHub is optional for risks, so a failed T-003 is a note, not a stop."""
    tools, _, _ = make_tools(
        work_items=[_work_item(1)], failures={"T-003": _failure("T-003", "RATE_LIMITED")}
    )

    context = _run("risks", tools)

    assert context.gate.proceed is True
    assert [failure.tool_id for failure in context.failures] == ["T-003"]
    assert any("T-003 failed (RATE_LIMITED)" in note for note in context.notes)


# ---------------------------------------------------------------------------
# S1-S6 Full Pipeline run_agent Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_agent_gate_closed() -> None:
    """When a required source is unavailable, run_agent returns UNKNOWN and persists run."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as OrmSession
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models.canonical import AppUser, Organization

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session: OrmSession = sessionmaker(bind=engine)()
    try:
        org = Organization(name="Test Org")
        session.add(org)
        session.flush()
        user = AppUser(organization_id=org.id, display_name="Keshan")
        session.add(user)
        session.flush()

        tools, _, _ = make_tools(health=[_health("jira"), _health("github", "unavailable")])
        insight = await run_agent(
            session=session,
            subject_user_id=user.id,
            team_id=1,
            question_type="current_work",
            tools=tools,
            now=NOW,
            skip_llm=True,
        )
        assert insight.user_id == user.id
        assert insight.likely_current_work is not None
        assert "Cannot be established" in insight.likely_current_work.claim
        assert insight.likely_current_work.confidence == "UNKNOWN"
    finally:
        session.close()


@pytest.mark.anyio
async def test_run_agent_full_pipeline_success(caplog: pytest.LogCaptureFixture) -> None:
    """Full execution S1-S6 returns MemberInsight with findings, summary, and cache saved."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as OrmSession
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models.canonical import AppUser, Organization

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session: OrmSession = sessionmaker(bind=engine)()
    try:
        org = Organization(name="Test Org")
        session.add(org)
        session.flush()
        user = AppUser(organization_id=org.id, display_name="Keshan")
        session.add(user)
        session.flush()

        wi = _work_item(1)
        wi.assignee_user_id = user.id
        pr = _pull_request(7)
        pr.author_user_id = user.id

        tools, _, _ = make_tools(
            health=[_health("jira"), _health("github")],
            work_items=[wi],
            pull_requests=[pr],
        )

        with caplog.at_level("INFO", logger="headref.agent"):
            insight = await run_agent(
                session=session,
                subject_user_id=user.id,
                team_id=1,
                question_type="current_work",
                tools=tools,
                now=NOW,
                skip_llm=True,
            )

        # The tool-call trace: which tools ran, then whether the model was called.
        assert "s1_s2 done: question=current_work proceed=True tools=['T-007'" in caplog.text
        assert "s4_s5 done: question=current_work model_called=False" in caplog.text
        assert insight.user_id == user.id
        assert insight.summary is not None
        assert "AUTH-1" in insight.summary or "Assigned" in insight.summary
        assert insight.likely_current_work is not None
        assert "AUTH-1" in insight.likely_current_work.claim

        # Running a second time hits the cache
        insight_cached = await run_agent(
            session=session,
            subject_user_id=user.id,
            team_id=1,
            question_type="current_work",
            tools=tools,
            now=NOW,
            skip_llm=True,
        )
        assert insight_cached.summary == insight.summary
    finally:
        session.close()


@pytest.mark.anyio
async def test_a_model_timeout_is_reported_as_a_fallback() -> None:
    """A timed out model call returns the labelled fallback and records why (EV-12 B)."""
    from unittest.mock import AsyncMock

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session as OrmSession
    from sqlalchemy.orm import sessionmaker

    from app.config import get_settings
    from app.db import Base
    from app.models.agent import AgentRun
    from app.models.canonical import AppUser, Organization

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session: OrmSession = sessionmaker(bind=engine)()
    try:
        org = Organization(name="Test Org")
        session.add(org)
        session.flush()
        user = AppUser(organization_id=org.id, display_name="Keshan")
        session.add(user)
        session.flush()

        pr = _pull_request(7)
        pr.author_user_id = user.id
        tools, _, _ = make_tools(health=[_health("jira"), _health("github")], pull_requests=[pr])
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post.side_effect = httpx.ReadTimeout("model too slow")

        insight = await run_agent(
            session=session,
            subject_user_id=user.id,
            team_id=1,
            question_type="current_work",
            tools=tools,
            now=NOW,
            client=client,
        )

        assert insight.fallback_used is True
        run = session.scalars(select(AgentRun)).one()
        assert run.error_type == "TIMEOUT"
        assert run.model == get_settings().model_id
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Likely current work from GitHub alone (DEC-020)
# ---------------------------------------------------------------------------


def _evidence(index: int, entity_type: str, key: str, summary: str) -> EvidenceItem:
    return EvidenceItem(
        id=f"ev_{index}",
        source="github",
        entity_type=entity_type,  # type: ignore[arg-type]
        entity_key=key,
        source_url="https://github.com/acme/api",
        summary=summary,
        observed_at=NOW,
        retrieved_at=NOW,
        source_state="fresh",
    )


def _branch_commit(sha: str, branch: str | None, hours_ago: int) -> CommitOut:
    return _commit().model_copy(
        update={"sha": sha, "branch_name": branch, "committed_at": NOW - timedelta(hours=hours_ago)}
    )


def test_github_current_work_picks_the_most_recently_active_open_pull_request() -> None:
    """Open PR with the latest commit wins; merged PRs are history, not current work."""
    older = _pull_request(1).model_copy(
        update={"number": 10, "last_commit_at": NOW - timedelta(days=3), "branch_name": "old"}
    )
    newer = _pull_request(2).model_copy(
        update={"number": 11, "last_commit_at": NOW - timedelta(hours=2), "branch_name": "feat/x"}
    )
    merged = _pull_request(3).model_copy(
        update={"number": 12, "state": "merged", "last_commit_at": NOW}
    )
    evidence = [
        _evidence(1, "pull_request", "11", "GitHub pull request #11 in acme/api: state=open"),
        _evidence(2, "commit", "aaaaaaaa", "GitHub commit aaaaaaaa in acme/api on branch feat/x"),
        _evidence(3, "commit", "bbbbbbbb", "GitHub commit bbbbbbbb in acme/api on branch feat/x2"),
        _evidence(4, "pull_request", "10", "GitHub pull request #10 in acme/api: state=open"),
    ]
    retrieval = RetrievalResult(pull_requests=[older, newer, merged])

    insight = github_current_work(retrieval, evidence, is_truncated=False, as_of=NOW)

    assert insight is not None
    assert insight.claim.startswith("Likely working on pull request #11 in acme/api")
    assert insight.classification == "inference"
    assert [ev.id for ev in insight.evidence] == ["ev_1", "ev_2"]
    assert insight.confidence == "MEDIUM"


def test_github_current_work_falls_back_to_the_latest_commit_branch() -> None:
    """With no open PR, the branch of the newest commit is the likely work."""
    retrieval = RetrievalResult(
        commits=[_branch_commit("c1", "fix/old", 30), _branch_commit("c2", "feat/new", 1)]
    )
    evidence = [_evidence(1, "commit", "c2", "GitHub commit c2 in acme/api on branch feat/new")]

    insight = github_current_work(retrieval, evidence, is_truncated=False, as_of=NOW)

    assert insight is not None
    assert "branch feat/new in acme/api" in insight.claim
    assert [ev.id for ev in insight.evidence] == ["ev_1"]


def test_github_current_work_is_none_without_github_activity() -> None:
    """No activity means no claim; the page then says no activity is recorded."""
    insight = github_current_work(RetrievalResult(), [], is_truncated=False, as_of=NOW)

    assert insight is None
