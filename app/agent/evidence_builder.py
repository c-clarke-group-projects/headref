"""Build a deterministic, sanitized evidence set from retrieval results (P4-001).

Produces the frozen, ID-labelled evidence set required for stage S3.
Deduplicates, ranks, truncates to MAX_EVIDENCE_ITEMS, sanitizes excerpts,
excludes null actors (DEC-008), and assigns sequential IDs ev_1..ev_n (DEC-004).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.schemas.evidence import (
    EntityType,
    EvidenceItem,
    EvidenceSet,
    SourceState,
)

# orchestrator imports build_evidence from this module, so importing AgentRunContext
# from it at runtime is a cycle. S3 only needs the type, never the class itself.
if TYPE_CHECKING:
    from app.agent.orchestrator import AgentRunContext

MAX_EVIDENCE_ITEMS = 50
MAX_EXCERPT_LENGTH = 500

_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _sanitize_excerpt(value: str | None) -> str | None:
    """Remove URLs and control characters, then cap untrusted text at 500 chars."""
    if not value:
        return None

    cleaned = _URL_PATTERN.sub("", value)
    cleaned = _CONTROL_PATTERN.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned[:MAX_EXCERPT_LENGTH].strip()

    return cleaned or None


def _source_state(
    context: AgentRunContext,
    source: str,
) -> SourceState | None:
    """Return the source state, or None if health is missing or unavailable."""
    for health in context.health:
        if health.source == source:
            if health.state == "unavailable":
                return None
            return SourceState(health.state)

    # Fail closed when health is missing per DEC-010
    return None


def _work_item_summary(item: Any) -> str:
    """Create a summary from normalized structured fields only."""
    prio = item.priority or "unspecified"
    flagged = "yes" if item.is_flagged else "no"
    return (
        f"Jira work item {item.external_id}: "
        f"status={item.status}; priority={prio}; flagged={flagged}"
    )


def _pull_request_summary(item: Any) -> str:
    """Create a summary from normalized structured fields only."""
    draft_str = "yes" if item.is_draft else "no"
    return (
        f"GitHub pull request #{item.number} in {item.repo_full_name}: "
        f"state={item.state}; review={item.review_state}; "
        f"checks={item.checks_state}; draft={draft_str}"
    )


def _commit_summary(item: Any) -> str:
    """Create a summary from normalized commit fields only."""
    sha_short = item.sha[:8] if item.sha else "unknown"
    branch_str = f"on branch {item.branch_name}" if item.branch_name else "direct commit"
    return f"GitHub commit {sha_short} in {item.repo_full_name} {branch_str}"


def _review_summary(item: Any) -> str:
    """Create a summary from normalized review fields only."""
    return (
        f"GitHub code review on PR #{item.pull_request_number} in "
        f"{item.repo_full_name}: state={item.state}"
    )


def build_evidence(context: AgentRunContext) -> EvidenceSet:
    """Build a sanitized, deduplicated, ranked evidence set with stable IDs."""
    retrieval = context.retrieval
    now = datetime.now(UTC)

    candidates: list[tuple[int, float, int, str, EvidenceItem]] = []
    seen: set[tuple[str, str, str]] = set()

    excluded_null_actor = 0
    excluded_unavailable_source = 0
    excluded_duplicate = 0

    jira_state = _source_state(context, "jira")
    github_state = _source_state(context, "github")

    # 1. Jira work items
    for item in retrieval.work_items:
        if jira_state is None:
            excluded_unavailable_source += 1
            continue

        if item.assignee_user_id is None:
            excluded_null_actor += 1
            continue

        entity_key = str(item.external_id)
        dedupe_key = ("jira", "work_item", entity_key)

        if dedupe_key in seen:
            excluded_duplicate += 1
            continue
        seen.add(dedupe_key)

        observed_at = item.source_updated_at or now
        evidence = EvidenceItem(
            id="",
            source="jira",
            entity_type=EntityType.work_item,
            entity_key=entity_key,
            source_url=item.source_url or f"https://jira.example.com/{entity_key}",
            summary=_work_item_summary(item),
            excerpt=None,
            observed_at=observed_at,
            retrieved_at=item.retrieved_at or now,
            source_state=jira_state,
        )

        # Work item directly assigned: rank priority 0, Jira source authority 0
        candidates.append((0, -observed_at.timestamp(), 0, entity_key, evidence))

    # 1b. Blocking work items, often another member's. A dependency blocker names them,
    # so the drawer must be able to show them. The record is cited, not attributed to
    # anyone, so the null actor rule does not apply.
    for item in retrieval.work_items:
        if jira_state is None:
            continue

        for dep in item.blocked_by_items:
            entity_key = str(dep.external_id)
            dedupe_key = ("jira", "work_item", entity_key)

            if dedupe_key in seen:
                excluded_duplicate += 1
                continue
            seen.add(dedupe_key)

            evidence = EvidenceItem(
                id="",
                source="jira",
                entity_type=EntityType.work_item,
                entity_key=entity_key,
                source_url=dep.source_url,
                summary=_work_item_summary(dep),
                excerpt=None,
                observed_at=dep.source_updated_at,
                retrieved_at=dep.retrieved_at,
                source_state=jira_state,
            )

            # Dependency: rank priority 1, after the subject's own work items
            candidates.append((1, -dep.source_updated_at.timestamp(), 0, entity_key, evidence))

    # 2. GitHub pull requests
    for pr in retrieval.pull_requests:
        if github_state is None:
            excluded_unavailable_source += 1
            continue

        if pr.author_user_id is None:
            excluded_null_actor += 1
            continue

        entity_key = str(pr.number)
        dedupe_key = ("github", "pull_request", entity_key)

        if dedupe_key in seen:
            excluded_duplicate += 1
            continue
        seen.add(dedupe_key)

        observed_at = pr.source_updated_at or now
        pr_url = pr.source_url or f"https://github.com/{pr.repo_full_name}/pull/{pr.number}"
        evidence = EvidenceItem(
            id="",
            source="github",
            entity_type=EntityType.pull_request,
            entity_key=entity_key,
            source_url=pr_url,
            summary=_pull_request_summary(pr),
            excerpt=_sanitize_excerpt(pr.body_excerpt),
            observed_at=observed_at,
            retrieved_at=pr.retrieved_at or now,
            source_state=github_state,
        )

        # Pull request authored by subject: rank priority 0, GitHub source authority 1
        candidates.append((0, -observed_at.timestamp(), 1, entity_key, evidence))

    # 3. GitHub commits
    for commit in retrieval.commits:
        if github_state is None:
            excluded_unavailable_source += 1
            continue

        if commit.author_user_id is None:
            excluded_null_actor += 1
            continue

        entity_key = commit.sha[:8] if commit.sha else "unknown"
        dedupe_key = ("github", "commit", entity_key)

        if dedupe_key in seen:
            excluded_duplicate += 1
            continue
        seen.add(dedupe_key)

        observed_at = commit.committed_at or now
        evidence = EvidenceItem(
            id="",
            source="github",
            entity_type=EntityType.commit,
            entity_key=entity_key,
            source_url=commit.source_url or f"https://github.com/{commit.repo_full_name}",
            summary=_commit_summary(commit),
            excerpt=_sanitize_excerpt(commit.message_excerpt),
            observed_at=observed_at,
            retrieved_at=commit.retrieved_at or now,
            source_state=github_state,
        )

        # Commit: rank priority 1
        candidates.append((1, -observed_at.timestamp(), 1, entity_key, evidence))

    # 4. GitHub reviews
    for review in retrieval.reviews:
        if github_state is None:
            excluded_unavailable_source += 1
            continue

        if review.reviewer_user_id is None:
            excluded_null_actor += 1
            continue

        entity_key = str(review.review_id)
        dedupe_key = ("github", "review", entity_key)

        if dedupe_key in seen:
            excluded_duplicate += 1
            continue
        seen.add(dedupe_key)

        observed_at = review.submitted_at or now
        evidence = EvidenceItem(
            id="",
            source="github",
            entity_type=EntityType.review,
            entity_key=entity_key,
            source_url=review.source_url or f"https://github.com/{review.repo_full_name}",
            summary=_review_summary(review),
            excerpt=_sanitize_excerpt(review.body_excerpt),
            observed_at=observed_at,
            retrieved_at=review.retrieved_at or now,
            source_state=github_state,
        )

        # Review: rank priority 2
        candidates.append((2, -observed_at.timestamp(), 1, entity_key, evidence))

    # Deterministic ranking: priority -> recency -> source authority -> entity_key
    candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]))

    truncated = len(candidates) > MAX_EVIDENCE_ITEMS
    selected = candidates[:MAX_EVIDENCE_ITEMS]

    # Assign contiguous sequential IDs ev_1 .. ev_n after ranking and truncation
    final_items = tuple(
        item.model_copy(update={"id": f"ev_{idx}"})
        for idx, (_, _, _, _, item) in enumerate(selected, start=1)
    )

    return EvidenceSet(
        items=final_items,
        truncated=truncated,
        excluded_null_actor=excluded_null_actor,
        excluded_unavailable_source=excluded_unavailable_source,
        excluded_duplicate=excluded_duplicate,
    )
