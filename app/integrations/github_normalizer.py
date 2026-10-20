"""GitHub data normalizer and canonical database loader (P2-003, Issue #14).

Transforms raw GitHub REST API payloads into canonical Repository, PullRequest,
Commit, and Review records. Enforces URL stripping and length caps on excerpts (FR-030),
UTC freshness timestamps (AC-7), and idempotent upsert without duplicates (NFR-003).
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import EXCERPT_MAX_CHARS
from app.models.canonical import Repository
from app.models.work import Commit, PullRequest, Review

logger = logging.getLogger("headref.integrations.github_normalizer")

# Regex to strip URLs from excerpts per FR-030
URL_REGEX = re.compile(r"https?://\S+")


@dataclass
class NormalizationCounts:
    """Tracking counts of fetched, written, and skipped records during normalization."""

    fetched: int = 0
    written: int = 0
    skipped: int = 0


def clean_excerpt(text: str | None, max_chars: int = EXCERPT_MAX_CHARS) -> str | None:
    """Remove URLs and truncate text to max_chars characters per FR-030."""
    if not text:
        return None
    cleaned = URL_REGEX.sub("", text).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
    return cleaned if cleaned else None


def parse_datetime(val: Any) -> datetime | None:
    """Parse an ISO-8601 string or timestamp into a UTC datetime."""
    if not val:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=UTC)
        return val.astimezone(UTC)
    if isinstance(val, str):
        try:
            # Handle standard UTC ISO-8601 strings ending in Z
            clean_str = val.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            return None
    return None


def normalize_repository(
    payload: dict[str, Any],
    organization_id: int,
) -> dict[str, Any]:
    """Normalize a GitHub repository payload into a repository dictionary."""
    full_name = payload.get("full_name") or payload.get("name")
    if not full_name:
        raise ValueError("Repository payload missing name / full_name")

    default_branch = payload.get("default_branch") or "main"
    return {
        "organization_id": organization_id,
        "full_name": full_name,
        "default_branch": default_branch,
        "is_active": True,
    }


def normalize_pull_request(
    payload: dict[str, Any],
    repository_id: int,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    """Normalize a GitHub pull request payload into canonical dictionary attributes."""
    if "number" not in payload or "title" not in payload:
        raise ValueError("PullRequest payload missing number or title")

    if retrieved_at is None:
        now = datetime.now(UTC)
    else:
        now = parse_datetime(retrieved_at) or datetime.now(UTC)

    # Derive state: open, merged, or closed
    merged_at = parse_datetime(payload.get("merged_at"))
    if merged_at is not None:
        state = "merged"
    elif payload.get("state") == "closed":
        state = "closed"
    else:
        state = "open"

    head_ref = payload.get("head", {}).get("ref") or "unknown"
    created_at_source = parse_datetime(payload.get("created_at")) or now
    source_updated_at = parse_datetime(payload.get("updated_at")) or created_at_source
    source_url = payload.get("html_url") or f"https://github.com/pulls/{payload['number']}"

    return {
        "repository_id": repository_id,
        "number": payload["number"],
        "title": payload["title"][:500],
        "body_excerpt": clean_excerpt(payload.get("body")),
        "state": state,
        "is_draft": bool(payload.get("draft", False)),
        "branch_name": head_ref[:255],
        "author_app_user_id": None,  # Left None, resolved by P2-006
        "created_at_source": created_at_source,
        "merged_at": merged_at,
        "source_url": source_url[:1024],
        "source_updated_at": source_updated_at,
        "retrieved_at": now,
    }


def normalize_commit(
    payload: dict[str, Any],
    repository_id: int,
    retrieved_at: datetime | None = None,
    branch_name: str | None = None,
) -> dict[str, Any]:
    """Normalize a GitHub commit payload into canonical dictionary attributes."""
    sha = payload.get("sha")
    if not sha:
        raise ValueError("Commit payload missing sha")

    if retrieved_at is None:
        now = datetime.now(UTC)
    else:
        now = parse_datetime(retrieved_at) or datetime.now(UTC)

    commit_data = payload.get("commit", {})
    author_data = commit_data.get("author", {})

    message = commit_data.get("message")
    committed_at = parse_datetime(author_data.get("date")) or now
    source_url = payload.get("html_url") or f"https://github.com/commits/{sha}"

    return {
        "repository_id": repository_id,
        "sha": sha[:64],
        "message_excerpt": clean_excerpt(message),
        "branch_name": branch_name[:255] if branch_name else None,
        "author_app_user_id": None,  # Left None, resolved by P2-006
        "committed_at": committed_at,
        "source_url": source_url[:1024],
        "retrieved_at": now,
    }


def normalize_review(
    payload: dict[str, Any],
    pull_request_id: int,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    """Normalize a GitHub pull request review payload into canonical attributes."""
    if "id" not in payload:
        raise ValueError("Review payload missing id")

    if retrieved_at is None:
        now = datetime.now(UTC)
    else:
        now = parse_datetime(retrieved_at) or datetime.now(UTC)

    external_id = str(payload["id"])
    raw_state = payload.get("state", "commented")
    state = raw_state.lower()
    submitted_at = parse_datetime(payload.get("submitted_at")) or now
    source_url = payload.get("html_url") or f"https://github.com/reviews/{external_id}"

    return {
        "pull_request_id": pull_request_id,
        "external_id": external_id[:255],
        "reviewer_app_user_id": None,  # Left None, resolved by P2-006
        "state": state[:64],
        "body_excerpt": clean_excerpt(payload.get("body")),
        "submitted_at": submitted_at,
        "source_url": source_url[:1024],
        "retrieved_at": now,
    }


def upsert_repository(session: Session, repo_dict: dict[str, Any]) -> Repository:
    """Idempotently insert or update a repository record, per organization (W5)."""
    stmt = select(Repository).where(
        Repository.organization_id == repo_dict["organization_id"],
        Repository.full_name == repo_dict["full_name"],
    )
    existing = session.scalar(stmt)
    if existing:
        existing.default_branch = repo_dict.get("default_branch", existing.default_branch)
        existing.is_active = repo_dict.get("is_active", existing.is_active)
        return existing

    repo = Repository(**repo_dict)
    session.add(repo)
    session.flush()
    return repo


def upsert_pull_request(session: Session, pr_dict: dict[str, Any]) -> PullRequest:
    """Idempotently insert or update a pull request record."""
    stmt = select(PullRequest).where(
        PullRequest.repository_id == pr_dict["repository_id"],
        PullRequest.number == pr_dict["number"],
    )
    existing = session.scalar(stmt)
    if existing:
        for key, val in pr_dict.items():
            if key not in ("id", "repository_id", "number", "author_app_user_id"):
                setattr(existing, key, val)
        return existing

    pr = PullRequest(**pr_dict)
    session.add(pr)
    session.flush()
    return pr


def upsert_commit(session: Session, commit_dict: dict[str, Any]) -> Commit:
    """Idempotently insert or update a commit record on (repository_id, sha)."""
    stmt = select(Commit).where(
        Commit.repository_id == commit_dict["repository_id"],
        Commit.sha == commit_dict["sha"],
    )
    existing = session.scalar(stmt)
    if existing:
        for key, val in commit_dict.items():
            if key not in ("id", "repository_id", "sha", "author_app_user_id"):
                setattr(existing, key, val)
        return existing

    commit = Commit(**commit_dict)
    session.add(commit)
    session.flush()
    return commit


def upsert_review(session: Session, review_dict: dict[str, Any]) -> Review:
    """Idempotently insert or update a pull request review record."""
    stmt = select(Review).where(
        Review.pull_request_id == review_dict["pull_request_id"],
        Review.external_id == review_dict["external_id"],
    )
    existing = session.scalar(stmt)
    if existing:
        for key, val in review_dict.items():
            if key not in ("id", "pull_request_id", "external_id", "reviewer_app_user_id"):
                setattr(existing, key, val)
        return existing

    review = Review(**review_dict)
    session.add(review)
    session.flush()
    return review


def ingest_repositories(
    session: Session,
    organization_id: int,
    payloads: list[dict[str, Any]],
) -> tuple[list[Repository], NormalizationCounts]:
    """Normalize and upsert a batch of repository payloads, skipping invalid records."""
    counts = NormalizationCounts(fetched=len(payloads))
    results: list[Repository] = []
    for raw in payloads:
        try:
            norm = normalize_repository(raw, organization_id=organization_id)
            repo = upsert_repository(session, norm)
            results.append(repo)
            counts.written += 1
        except Exception as exc:
            logger.warning("Skipping malformed repository record: %s", exc)
            counts.skipped += 1
    return results, counts


def ingest_pull_requests(
    session: Session,
    repository_id: int,
    payloads: list[dict[str, Any]],
    retrieved_at: datetime | None = None,
) -> tuple[list[PullRequest], NormalizationCounts]:
    """Normalize and upsert a batch of PR payloads, skipping invalid records."""
    counts = NormalizationCounts(fetched=len(payloads))
    results: list[PullRequest] = []
    for raw in payloads:
        try:
            norm = normalize_pull_request(
                raw, repository_id=repository_id, retrieved_at=retrieved_at
            )
            pr = upsert_pull_request(session, norm)
            results.append(pr)
            counts.written += 1
        except Exception as exc:
            logger.warning("Skipping malformed pull request record: %s", exc)
            counts.skipped += 1
    return results, counts


def ingest_commits(
    session: Session,
    repository_id: int,
    payloads: list[dict[str, Any]],
    retrieved_at: datetime | None = None,
    branch_name: str | None = None,
) -> tuple[list[Commit], NormalizationCounts]:
    """Normalize and upsert a batch of commit payloads, skipping invalid records."""
    counts = NormalizationCounts(fetched=len(payloads))
    results: list[Commit] = []
    for raw in payloads:
        try:
            norm = normalize_commit(
                raw,
                repository_id=repository_id,
                retrieved_at=retrieved_at,
                branch_name=branch_name,
            )
            commit = upsert_commit(session, norm)
            results.append(commit)
            counts.written += 1
        except Exception as exc:
            logger.warning("Skipping malformed commit record: %s", exc)
            counts.skipped += 1
    return results, counts


def ingest_reviews(
    session: Session,
    pull_request_id: int,
    payloads: list[dict[str, Any]],
    retrieved_at: datetime | None = None,
) -> tuple[list[Review], NormalizationCounts]:
    """Normalize and upsert a batch of review payloads, skipping invalid records."""
    counts = NormalizationCounts(fetched=len(payloads))
    results: list[Review] = []
    for raw in payloads:
        try:
            norm = normalize_review(raw, pull_request_id=pull_request_id, retrieved_at=retrieved_at)
            review = upsert_review(session, norm)
            results.append(review)
            counts.written += 1
        except Exception as exc:
            logger.warning("Skipping malformed review record: %s", exc)
            counts.skipped += 1
    return results, counts
