"""Workspace feed: what changed between 2 syncs (W8, DEC-025).

After every workspace sync, record_changes takes a snapshot and compares it with the
previous 1:

- per confirmed person, the code-built findings (blockers, risks, conflicts), keyed by
  rule and entity, so a day counter in the text ("open for 4 days") is not a change;
- for the workspace, each Jira issue's status, each pull request's state, and whether
  each source's last sync failed.

Each difference becomes 1 short, code-written line (DEC-018: no model text). A subject
seen for the first time is a baseline and produces no lines. A person whose sources are
unavailable is skipped, so missing data never turns a finding into "no longer detected"
(rule 9); the same holds for issues and pull requests when their source failed to sync.
Feed lines describe work, never people (rule 15).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agent.blockers import detect_blockers
from app.agent.conflicts import detect_conflicts
from app.agent.orchestrator import ReadTools, run_s1_s2
from app.agent.risks import detect_risks
from app.models.canonical import Project, Repository
from app.models.feed import FindingSnapshot, WorkspaceEvent
from app.models.operations import SyncRun
from app.models.work import PullRequest, WorkItem
from app.models.workspace import Workspace, WorkspaceMember
from app.people import confirmed_people
from app.workspace_analysis import jira_in_use

logger = logging.getLogger("headref.workspace_feed")

WORK_STATE = "work_state"
FEED_RETENTION = timedelta(days=90)
FEED_PAGE_SIZE = 50
TEXT_LIMIT = 500

# RK-4 restates a conflict as a risk; the conflict line already reports it.
_SKIPPED_SIGNALS = {"RK-4"}
_FINDING_LABELS = {"blocker": "blocker", "risk": "risk", "conflict": "Jira and GitHub conflict"}
_SOURCE_NAMES = {"github": "GitHub", "jira": "Jira"}


@dataclass
class FeedItem:
    """1 feed line as the page shows it."""

    kind: str
    text: str
    source_url: str | None
    occurred_at: datetime
    is_new: bool


def _person_subject(person_id: int) -> str:
    return f"person:{person_id}"


def _clip(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 3] + "..."


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; every stored time is UTC."""
    if moment is None or moment.tzinfo is not None:
        return moment
    return moment.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def person_findings(
    db: Session,
    workspace_id: int,
    person_id: int,
    uses_jira: bool,
    now: datetime,
    tools: ReadTools | None = None,
) -> dict[str, dict[str, Any]] | None:
    """Findings for 1 person keyed by rule and entity, or None when a source is missing.

    Retrieval matches the workspace page: blockers from the blockers question, risks from
    the risks question, conflicts from both.
    """
    findings: dict[str, dict[str, Any]] = {}
    for question in ("blockers", "risks"):
        context = run_s1_s2(
            db,
            subject_user_id=person_id,
            team_id=None,
            question_type=question,
            tools=tools,
            now=now,
            workspace_id=workspace_id,
            jira_in_use=uses_jira,
        )
        if not context.gate.proceed:
            return None
        found = context.retrieval
        item_urls = {w.external_id: w.source_url for w in found.work_items}
        conflicts = detect_conflicts(
            found.work_items, found.pull_requests, found.commits, found.links
        )
        for c in conflicts:
            key = f"conflict:{c.conflict_id}:{c.work_item_external_id}:{c.target_identifier}"
            findings[key] = {
                "kind": "conflict",
                "text": c.description,
                "url": item_urls.get(c.work_item_external_id),
            }
        if question == "blockers":
            for b in detect_blockers(
                found.work_items, found.pull_requests, found.commits, found.links, as_of=now
            ):
                url = item_urls.get(b.entity_key)
                entity = b.entity_key
                if b.pull_request_number is not None:
                    pr = next(
                        (p for p in found.pull_requests if p.number == b.pull_request_number),
                        None,
                    )
                    if pr is not None:
                        url = pr.source_url
                        entity = f"{pr.repo_full_name}#{pr.number}"
                findings[f"blocker:{b.signal_id}:{entity}"] = {
                    "kind": "blocker",
                    "text": b.description,
                    "url": url,
                }
        else:
            for r in detect_risks(
                found.work_items,
                found.pull_requests,
                found.commits,
                found.links,
                conflicts=conflicts,
                as_of=now,
            ):
                if r.signal_id in _SKIPPED_SIGNALS:
                    continue
                findings[f"risk:{r.signal_id}:{r.entity_key}"] = {
                    "kind": "risk",
                    "text": r.description,
                    "url": item_urls.get(r.entity_key),
                }
    return findings


def _failed_sources(runs: Iterable[SyncRun]) -> set[str]:
    return {r.source for r in runs if r.status == "failed"}


def work_state(
    db: Session, organization_id: int, runs: list[SyncRun], previous: dict[str, Any]
) -> dict[str, Any]:
    """Issue statuses, pull request states and sync outcomes for 1 workspace.

    A source that failed in this sync keeps its previous section: its stored rows were not
    refreshed, and what is missing must not read as a change.
    """
    failed = _failed_sources(runs)
    if "jira" in failed:
        items = previous.get("items", {})
    else:
        items = {
            w.external_id: {
                "status": w.raw_status,
                "title": w.title,
                "url": w.source_url,
                "person": w.assignee_app_user_id,
            }
            for w in db.scalars(
                select(WorkItem)
                .join(Project, Project.id == WorkItem.project_id)
                .where(Project.organization_id == organization_id)
            )
        }
    if "github" in failed:
        prs = previous.get("prs", {})
    else:
        prs = {}
        for pr, repo_name in db.execute(
            select(PullRequest, Repository.full_name)
            .join(Repository, Repository.id == PullRequest.repository_id)
            .where(Repository.organization_id == organization_id)
        ):
            state = "draft" if pr.state == "open" and pr.is_draft else pr.state
            prs[f"{repo_name}#{pr.number}"] = {
                "state": state,
                "title": pr.title,
                "url": pr.source_url,
                "person": pr.author_app_user_id,
            }
    syncs = dict(previous.get("syncs", {}))
    for run in runs:
        syncs[f"{run.source}:{run.scope}"] = {
            "failed": run.status == "failed",
            "error": run.error_type,
        }
    return {"items": items, "prs": prs, "syncs": syncs}


# ---------------------------------------------------------------------------
# Differences
# ---------------------------------------------------------------------------


@dataclass
class _Change:
    kind: str
    text: str
    url: str | None
    entity_key: str | None
    person_id: int | None


def diff_findings(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]], person_id: int
) -> list[_Change]:
    """New and no longer detected findings of 1 person."""
    changes: list[_Change] = []
    for key in sorted(after.keys() - before.keys()):
        finding = after[key]
        label = _FINDING_LABELS[finding["kind"]]
        changes.append(
            _Change(
                kind=f"{finding['kind']}_new",
                text=f"New {label}: {finding['text']}",
                url=finding.get("url"),
                entity_key=key,
                person_id=person_id,
            )
        )
    for key in sorted(before.keys() - after.keys()):
        finding = before[key]
        label = _FINDING_LABELS[finding["kind"]]
        changes.append(
            _Change(
                kind=f"{finding['kind']}_cleared",
                text=f"No longer detected, {label}: {finding['text']}",
                url=finding.get("url"),
                entity_key=key,
                person_id=person_id,
            )
        )
    return changes


_PR_TRANSITIONS = {
    ("draft", "open"): "marked ready for review",
    ("open", "draft"): "converted to draft",
    ("open", "merged"): "merged",
    ("draft", "merged"): "merged",
    ("open", "closed"): "closed without merging",
    ("draft", "closed"): "closed without merging",
    ("closed", "open"): "reopened",
    ("closed", "draft"): "reopened",
}
_PR_FIRST_SEEN = {
    "open": "opened",
    "draft": "opened as a draft",
    "merged": "merged",
    "closed": "closed",
}


def diff_work_state(before: dict[str, Any], after: dict[str, Any]) -> list[_Change]:
    """Issue moves, pull request changes, and sync failures and recoveries."""
    changes: list[_Change] = []
    old_items = before.get("items", {})
    for key, item in sorted(after.get("items", {}).items()):
        old = old_items.get(key)
        if old is None:
            text = f"{key} added in Jira, status {item['status']}: {item['title']}"
            kind = "item_added"
        elif old["status"] != item["status"]:
            text = f"{key} moved from {old['status']} to {item['status']}: {item['title']}"
            kind = "item_moved"
        else:
            continue
        changes.append(_Change(kind, _clip(text), item.get("url"), key, item.get("person")))

    old_prs = before.get("prs", {})
    for key, pr in sorted(after.get("prs", {}).items()):
        old = old_prs.get(key)
        if old is None:
            verb = _PR_FIRST_SEEN.get(pr["state"], pr["state"])
        elif old["state"] != pr["state"]:
            verb = _PR_TRANSITIONS.get(
                (old["state"], pr["state"]), f"changed from {old['state']} to {pr['state']}"
            )
        else:
            continue
        text = f"Pull request {key} {verb}: {pr['title']}"
        changes.append(
            _Change(f"pr_{pr['state']}", _clip(text), pr.get("url"), key, pr.get("person"))
        )

    old_syncs = before.get("syncs", {})
    for key, sync in sorted(after.get("syncs", {}).items()):
        was_failed = old_syncs.get(key, {}).get("failed", False)
        source, _, scope = key.partition(":")
        name = _SOURCE_NAMES.get(source, source)
        if sync["failed"] and not was_failed:
            text = f"{name} sync failed for {scope}: {sync['error'] or 'UPSTREAM_ERROR'}."
            changes.append(_Change("sync_failed", _clip(text), None, key, None))
        elif was_failed and not sync["failed"]:
            text = f"{name} sync for {scope} works again."
            changes.append(_Change("sync_recovered", _clip(text), None, key, None))
    return changes


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def _load(db: Session, workspace_id: int, subject: str) -> FindingSnapshot | None:
    return db.scalar(
        select(FindingSnapshot).where(
            FindingSnapshot.workspace_id == workspace_id, FindingSnapshot.subject == subject
        )
    )


def _store(
    db: Session,
    workspace_id: int,
    subject: str,
    snapshot: FindingSnapshot | None,
    data: dict[str, Any],
    now: datetime,
) -> None:
    if snapshot is None:
        db.add(
            FindingSnapshot(workspace_id=workspace_id, subject=subject, data=data, created_at=now)
        )
    else:
        snapshot.data = data
        snapshot.created_at = now


def record_changes(
    db: Session, workspace_id: int, runs: list[SyncRun], now: datetime | None = None
) -> int:
    """Snapshot the workspace after a sync, write the differences as feed lines.

    Returns how many lines were written. A first snapshot is a baseline: 0 lines.
    """
    moment = now or datetime.now(UTC)
    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.organization_id is None:
        return 0
    changes: list[_Change] = []

    previous_work = _load(db, workspace_id, WORK_STATE)
    work = work_state(
        db, workspace.organization_id, runs, previous_work.data if previous_work else {}
    )
    if previous_work is not None:
        changes.extend(diff_work_state(previous_work.data, work))
    _store(db, workspace_id, WORK_STATE, previous_work, work, moment)

    uses_jira = jira_in_use(db, workspace_id)
    skipped = 0
    for person in confirmed_people(db, workspace.organization_id):
        findings = person_findings(db, workspace_id, person.id, uses_jira, moment)
        if findings is None:
            skipped += 1
            continue
        subject = _person_subject(person.id)
        previous = _load(db, workspace_id, subject)
        if previous is not None:
            changes.extend(diff_findings(previous.data.get("findings", {}), findings, person.id))
        _store(db, workspace_id, subject, previous, {"findings": findings}, moment)

    for change in changes:
        db.add(
            WorkspaceEvent(
                workspace_id=workspace_id,
                person_id=change.person_id,
                kind=change.kind,
                text=_clip(change.text),
                entity_key=change.entity_key[:255] if change.entity_key else None,
                source_url=change.url,
                occurred_at=moment,
            )
        )
    db.execute(
        delete(WorkspaceEvent).where(
            WorkspaceEvent.workspace_id == workspace_id,
            WorkspaceEvent.occurred_at < moment - FEED_RETENTION,
        )
    )
    db.commit()
    logger.info(
        "feed recorded: workspace_id=%s lines=%s people_skipped=%s baseline=%s",
        workspace_id,
        len(changes),
        skipped,
        previous_work is None,
    )
    return len(changes)


def record_feed_failure(db: Session, workspace_id: int) -> None:
    """Say on the feed that this sync's changes could not be worked out (rule 9)."""
    db.rollback()
    db.add(
        WorkspaceEvent(
            workspace_id=workspace_id,
            kind="feed_failed",
            text="Changes from this sync could not be worked out. The next sync tries again.",
            occurred_at=datetime.now(UTC),
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def feed(
    db: Session,
    workspace_id: int,
    last_seen_at: datetime | None,
    person_id: int | None = None,
    limit: int = FEED_PAGE_SIZE,
) -> list[FeedItem]:
    """Newest feed lines first; for 1 person, only lines about their work."""
    query = select(WorkspaceEvent).where(WorkspaceEvent.workspace_id == workspace_id)
    if person_id is not None:
        query = query.where(WorkspaceEvent.person_id == person_id)
    rows = db.scalars(
        query.order_by(WorkspaceEvent.occurred_at.desc(), WorkspaceEvent.id.desc()).limit(limit)
    ).all()
    seen = _aware(last_seen_at)
    return [
        FeedItem(
            kind=row.kind,
            text=row.text,
            source_url=row.source_url,
            occurred_at=_aware(row.occurred_at),
            is_new=seen is None or _aware(row.occurred_at) > seen,
        )
        for row in rows
    ]


def mark_seen(
    db: Session, workspace_id: int, account_id: int, now: datetime | None = None
) -> datetime | None:
    """Record this visit; return the previous visit time, used to mark newer lines."""
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.account_id == account_id
        )
    )
    if member is None:
        return None
    previous = member.last_seen_at
    member.last_seen_at = now or datetime.now(UTC)
    db.commit()
    return previous


# ---------------------------------------------------------------------------
# Portfolio cards
# ---------------------------------------------------------------------------

ATTENTION_LIMIT = 3
_ATTENTION_ORDER = {"blocker": 0, "conflict": 1, "risk": 2}


@dataclass
class PortfolioCard:
    """1 workspace on the portfolio home, from its latest snapshots (W10).

    Counts are for the workspace as a whole, never per person (rule 15): a finding that
    concerns 2 people counts once. health is "Blocked", "Needs Attention", "On Track" or
    "Unknown"; unknown_reason says why when it is "Unknown".
    """

    health: str
    blockers: int = 0
    conflicts: int = 0
    risks: int = 0
    attention: list[str] | None = None
    last_sync: dict[str, datetime] | None = None
    failed_sources: list[str] | None = None
    unknown_reason: str | None = None


def portfolio_card(db: Session, workspace: Workspace, latest: list[SyncRun]) -> PortfolioCard:
    """Health, counts, top attention items and last sync per source for 1 workspace.

    latest is the newest sync_run per scope (workspace_sync.latest_runs). Only people
    who are confirmed now count. A failed source makes the health Unknown, because its
    findings may be missing; the counts are still shown, from the last good sync.
    """
    last_sync: dict[str, datetime] = {}
    for run in latest:
        if run.status != "failed" and run.finished_at is not None:
            finished = _aware(run.finished_at)
            if run.source not in last_sync or finished > last_sync[run.source]:
                last_sync[run.source] = finished
    failed = sorted({_SOURCE_NAMES.get(r.source, r.source) for r in latest if r.status == "failed"})

    if workspace.organization_id is None or _load(db, workspace.id, WORK_STATE) is None:
        return PortfolioCard(
            health="Unknown",
            last_sync=last_sync,
            failed_sources=failed,
            unknown_reason="Not synced yet.",
        )
    confirmed = [p.id for p in confirmed_people(db, workspace.organization_id)]
    if not confirmed:
        return PortfolioCard(
            health="Unknown",
            last_sync=last_sync,
            failed_sources=failed,
            unknown_reason="Nobody is confirmed yet.",
        )

    findings: dict[str, dict[str, Any]] = {}
    for snapshot in db.scalars(
        select(FindingSnapshot).where(
            FindingSnapshot.workspace_id == workspace.id,
            FindingSnapshot.subject.in_([_person_subject(pid) for pid in confirmed]),
        )
    ):
        findings.update(snapshot.data.get("findings", {}))
    counts = {kind: 0 for kind in _ATTENTION_ORDER}
    for finding in findings.values():
        counts[finding["kind"]] += 1
    ordered = sorted(findings.items(), key=lambda kv: (_ATTENTION_ORDER[kv[1]["kind"]], kv[0]))

    if failed:
        health, reason = "Unknown", f"{' and '.join(failed)} could not be synced."
    elif counts["blocker"]:
        health, reason = "Blocked", None
    elif counts["conflict"] or counts["risk"]:
        health, reason = "Needs Attention", None
    else:
        health, reason = "On Track", None
    return PortfolioCard(
        health=health,
        blockers=counts["blocker"],
        conflicts=counts["conflict"],
        risks=counts["risk"],
        attention=[_clip(f["text"]) for _, f in ordered[:ATTENTION_LIMIT]],
        last_sync=last_sync,
        failed_sources=failed,
        unknown_reason=reason,
    )
