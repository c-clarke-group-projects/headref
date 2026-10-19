"""Synchronization CLI and orchestration pipeline (P2-008, Issue #19).

Orchestrates upstream clients, canonical normalizers, identity resolution,
and work item link builders. Tracks run state in sync_run (FR-008), cursor
positions in sync_cursor (FR-009), and enforces secret redaction in error
logs (NFR-011).
"""

import argparse
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import github_scopes, jira_scopes
from app.db import get_sync_session
from app.integrations.errors import IntegrationError
from app.integrations.github import GitHubClient
from app.integrations.github_normalizer import (
    ingest_commits,
    ingest_pull_requests,
    ingest_repositories,
    ingest_reviews,
    parse_datetime,
)
from app.integrations.identity_resolver import (
    attribute_commits,
    attribute_pull_requests,
    attribute_reviews,
    attribute_work_items,
)
from app.integrations.jira import JiraClient
from app.integrations.jira_normalizer import (
    ingest_dependencies,
    ingest_projects,
    ingest_work_items,
)
from app.integrations.link_builder import (
    link_commit,
    link_jira_remote_links,
    link_pull_request,
)
from app.models.canonical import Organization, Project, Repository
from app.models.operations import SyncCursor, SyncRun
from app.models.work import PullRequest, WorkItem

logger = logging.getLogger("headref.sync")

# An Authorization header renders as "Authorization: Bearer <credential>", so the
# scheme word has to be consumed too or the credential after it survives redaction.
SECRET_REDACT_REGEX = re.compile(
    r"(token|bearer|basic|key|password|secret|authorization)"
    r"\s*[:=]\s*"
    r"(?:(?:bearer|basic|token)\s+)?"
    r"\S+",
    re.IGNORECASE,
)


def sanitize_error_detail(detail: str | None) -> str | None:
    """Mask any tokens, bearer credentials, or passwords in error messages (NFR-011)."""
    if not detail:
        return None
    return SECRET_REDACT_REGEX.sub(r"\1=***", detail)


def get_or_create_cursor(
    session: Session,
    source: str,
    scope: str,
) -> SyncCursor | None:
    """Fetch the active SyncCursor for source and scope."""
    stmt = select(SyncCursor).where(SyncCursor.source == source, SyncCursor.scope == scope)
    return session.scalar(stmt)


def advance_cursor(
    session: Session,
    source: str,
    scope: str,
    cursor_value: str,
) -> SyncCursor:
    """Update or insert a resume position cursor for incremental sync."""
    cursor = get_or_create_cursor(session, source, scope)
    if cursor:
        cursor.cursor_value = cursor_value
        cursor.updated_at = datetime.now(UTC)
        return cursor

    new_cursor = SyncCursor(
        source=source,
        scope=scope,
        cursor_value=cursor_value,
        updated_at=datetime.now(UTC),
    )
    session.add(new_cursor)
    session.flush()
    return new_cursor


def _only_configured_scope(scopes: list[str], source: str) -> str:
    """Return the single configured scope, or fail loudly rather than guess.

    A deployment that ingests several scopes must say which one this run is for.
    Picking the first silently would sync 1 repository and report success for all.
    """
    if not scopes:
        raise ValueError(f"No {source} scope is configured. Set it in the environment.")
    if len(scopes) > 1:
        raise ValueError(
            f"{len(scopes)} {source} scopes are configured, so scope cannot be inferred. "
            f"Pass scope explicitly."
        )
    return scopes[0]


CHECKS_STATES = frozenset({"passing", "failing", "unknown"})

# Jira project keys: an uppercase letter, then letters, digits or underscores.
_JIRA_KEY = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


def project_jql(keys: list[str]) -> str:
    """A bounded JQL query over the given project keys, or "" if there are none.

    Keys are checked against Jira's key format and quoted, so a key from the API
    cannot change the query. Invalid keys are logged and left out.
    """
    valid = sorted({k for k in keys if _JIRA_KEY.fullmatch(k or "")})
    skipped = sorted(set(keys) - set(valid))
    if skipped:
        logger.warning("Jira project keys left out of the search: %s", skipped)
    if not valid:
        return ""
    quoted = ", ".join(f'"{k}"' for k in valid)
    return f"project in ({quoted}) ORDER BY key"


def record_checks_state(
    client: Any,
    owner: str,
    repo_name: str,
    prs: list[PullRequest],
    raw_prs: list[dict[str, Any]],
) -> None:
    """Set checks_state on open pull requests from their head commit's CI results.

    BL-7 reads this, so it must reflect GitHub. A pull request whose checks cannot be
    read is set to unknown and logged; it is never recorded as passing.
    """
    head_sha = {raw.get("number"): (raw.get("head") or {}).get("sha") for raw in raw_prs}
    for pr in prs:
        if pr.state != "open":
            continue
        sha = head_sha.get(pr.number)
        if not sha:
            pr.checks_state = "unknown"
            continue
        try:
            state = client.get_checks_state(owner, repo_name, sha)
        except IntegrationError as exc:
            logger.warning(
                "checks not read for %s/%s#%s: %s", owner, repo_name, pr.number, exc.error_type
            )
            state = "unknown"
        pr.checks_state = state if state in CHECKS_STATES else "unknown"


def cursor_key(scope: str, workspace_id: int | None) -> str:
    """The sync_cursor scope. A workspace sync keeps its own cursor (w<id>:<scope>), so 2
    workspaces syncing the same repository cannot skip each other's commits."""
    return f"w{workspace_id}:{scope}" if workspace_id else scope


def resolve_org_id(session: Session, organization_id: int | None) -> int:
    """The organization a sync writes to: the workspace's, or the legacy first one."""
    if organization_id is not None:
        return organization_id
    org = session.scalar(select(Organization).order_by(Organization.id))
    return org.id if org else 1


def sync_github(
    session: Session,
    team_id: int = 1,
    scope: str | None = None,
    reset_cursor: bool = False,
    gh_client: GitHubClient | None = None,
    auto_create_contributors: bool = False,
    organization_id: int | None = None,
    workspace_id: int | None = None,
) -> SyncRun:
    """Run end-to-end GitHub ingestion, normalization, identity attribution and links.

    With organization_id and workspace_id (W5), everything is written to and read from
    the workspace's organization, and the run and cursor belong to the workspace.
    """
    scope = scope or _only_configured_scope(github_scopes(), "GitHub")
    logger.info(
        "Starting GitHub sync for scope=%s, team_id=%d, workspace_id=%s",
        scope,
        team_id,
        workspace_id,
    )
    cursor_scope = cursor_key(scope, workspace_id)

    # 1. Reset cursor if requested
    if reset_cursor:
        stmt = delete(SyncCursor).where(
            SyncCursor.source == "github",
            SyncCursor.scope == cursor_scope,
        )
        session.execute(stmt)
        session.flush()

    # 2. Start running audit row
    start_time = datetime.now(UTC)
    sync_run = SyncRun(
        source="github",
        scope=scope,
        status="running",
        started_at=start_time,
        workspace_id=workspace_id,
    )
    session.add(sync_run)
    session.commit()

    try:
        client = gh_client or GitHubClient()

        # Find or create repository, in this organization
        org_id = resolve_org_id(session, organization_id)
        repo_stmt = select(Repository).where(
            Repository.full_name == scope, Repository.organization_id == org_id
        )
        repo = session.scalar(repo_stmt)
        if not repo:
            raw_repo = client.get_repository(scope.split("/")[0], scope.split("/")[1])
            repos, _ = ingest_repositories(session, org_id, [raw_repo])
            repo = repos[0]
            session.commit()

        # Check cursor
        cursor = get_or_create_cursor(session, "github", cursor_scope)
        since_dt = parse_datetime(cursor.cursor_value) if cursor else None

        owner, repo_name = scope.split("/")

        # Fetch data
        raw_prs = client.list_pull_requests(owner, repo_name)
        raw_commits = client.list_commits(owner, repo_name, since=since_dt)

        # Ingest and normalize
        prs, pr_counts = ingest_pull_requests(session, repo.id, raw_prs)
        commits, commit_counts = ingest_commits(session, repo.id, raw_commits)

        # Fetch & ingest reviews
        total_rev_fetched = 0
        total_rev_written = 0
        total_rev_skipped = 0
        all_raw_reviews: list[dict[str, Any]] = []
        all_persisted_reviews = []

        for pr in prs:
            raw_revs = client.list_reviews(owner, repo_name, pr.number)
            total_rev_fetched += len(raw_revs)
            all_raw_reviews.extend(raw_revs)
            revs, rev_counts = ingest_reviews(session, pr.id, raw_revs)
            total_rev_written += rev_counts.written
            total_rev_skipped += rev_counts.skipped
            all_persisted_reviews.extend(revs)

        record_checks_state(client, owner, repo_name, prs, raw_prs)

        # Identity resolution
        attribute_pull_requests(
            session,
            prs,
            raw_prs,
            auto_create=auto_create_contributors,
            team_id=team_id,
            organization_id=organization_id,
        )
        attribute_commits(
            session,
            commits,
            raw_commits,
            auto_create=auto_create_contributors,
            team_id=team_id,
            organization_id=organization_id,
        )
        attribute_reviews(
            session,
            all_persisted_reviews,
            all_raw_reviews,
            auto_create=auto_create_contributors,
            team_id=team_id,
            organization_id=organization_id,
        )

        # Link building, within this organization only
        projects = session.scalars(select(Project).where(Project.organization_id == org_id)).all()
        project_keys = [p.key for p in projects]
        work_items = session.scalars(
            select(WorkItem).join(Project).where(Project.organization_id == org_id)
        ).all()
        wi_by_key = {wi.external_id: wi for wi in work_items}

        for pr in prs:
            link_pull_request(session, pr, wi_by_key, project_keys)
        for commit in commits:
            link_commit(session, commit, wi_by_key, project_keys)

        # Totals
        fetched = pr_counts.fetched + commit_counts.fetched + total_rev_fetched
        written = pr_counts.written + commit_counts.written + total_rev_written
        skipped = pr_counts.skipped + commit_counts.skipped + total_rev_skipped

        status = "partial" if skipped > 0 else "success"

        # Advance cursor only on success/partial
        advance_cursor(session, "github", cursor_scope, datetime.now(UTC).isoformat())

        sync_run.status = status
        sync_run.items_fetched = fetched
        sync_run.items_written = written
        sync_run.items_skipped = skipped
        sync_run.finished_at = datetime.now(UTC)
        session.commit()
        return sync_run

    except Exception as exc:
        session.rollback()
        err_type = exc.error_type if isinstance(exc, IntegrationError) else type(exc).__name__
        safe_detail = sanitize_error_detail(str(exc))
        sync_run.status = "failed"
        sync_run.error_type = err_type
        sync_run.error_detail = safe_detail
        sync_run.finished_at = datetime.now(UTC)
        session.commit()
        logger.error("GitHub sync failed: %s (%s)", err_type, safe_detail)
        return sync_run


def sync_jira(
    session: Session,
    team_id: int = 1,
    scope: str | None = None,
    reset_cursor: bool = False,
    jira_client: JiraClient | None = None,
    organization_id: int | None = None,
    workspace_id: int | None = None,
) -> SyncRun:
    """Run end-to-end Jira ingestion, normalization, dependencies and remote links.

    With organization_id and workspace_id (W5), projects, issues, people and links are
    the workspace's, and only the projects named in scope are ingested.
    """
    scope = scope or _only_configured_scope(jira_scopes(), "Jira")
    logger.info(
        "Starting Jira sync for scope=%s, team_id=%d, workspace_id=%s",
        scope,
        team_id,
        workspace_id,
    )
    cursor_scope = cursor_key(scope, workspace_id)

    # 1. Reset cursor if requested
    if reset_cursor:
        stmt = delete(SyncCursor).where(
            SyncCursor.source == "jira",
            SyncCursor.scope == cursor_scope,
        )
        session.execute(stmt)
        session.flush()

    # 2. Start running audit row
    start_time = datetime.now(UTC)
    sync_run = SyncRun(
        source="jira",
        scope=scope,
        status="running",
        started_at=start_time,
        workspace_id=workspace_id,
    )
    session.add(sync_run)
    session.commit()

    try:
        client = jira_client or JiraClient()

        # Ingest projects
        raw_projects = client.list_projects()
        org_id = resolve_org_id(session, organization_id)
        scope_keys = [k.strip() for k in scope.split(",") if k.strip()]
        if workspace_id is not None and scope.upper() != "ALL":
            raw_projects = [p for p in raw_projects if p.get("key") in scope_keys]
        projects, proj_counts = ingest_projects(session, org_id, raw_projects)
        session.commit()

        # Search issues. The JQL must be bounded (Jira Cloud rejects an empty query).
        keys = [p.key for p in projects] if scope.upper() == "ALL" else scope_keys
        jql = project_jql(keys)
        raw_issues = client.search_issues(jql=jql, auto_paginate=True) if jql else []

        # Ingest issues for each project
        total_wi_fetched = len(raw_issues)
        total_wi_written = 0
        total_wi_skipped = 0
        all_persisted_items: list[WorkItem] = []

        proj_by_key = {p.key: p for p in projects}

        # Group raw issues by project
        issues_by_proj: dict[str, list[dict[str, Any]]] = {}
        for raw in raw_issues:
            pkey = raw.get("fields", {}).get("project", {}).get("key") or "UNKNOWN"
            issues_by_proj.setdefault(pkey, []).append(raw)

        for pkey, raw_group in issues_by_proj.items():
            proj = proj_by_key.get(pkey)
            if not proj:
                continue
            items, counts = ingest_work_items(session, proj.id, raw_group)
            total_wi_written += counts.written
            total_wi_skipped += counts.skipped
            all_persisted_items.extend(items)

        # Attribute work items
        attribute_work_items(
            session, all_persisted_items, raw_issues, organization_id=organization_id
        )

        # Ingest blocking dependencies
        wi_by_key = {w.external_id: w for w in all_persisted_items}
        deps, dep_counts = ingest_dependencies(session, wi_by_key, raw_issues)

        # Fetch remote links and build WorkItemLink
        all_prs = session.scalars(
            select(PullRequest).join(Repository).where(Repository.organization_id == org_id)
        ).all()
        prs_by_url = {pr.source_url: pr for pr in all_prs}

        for raw_issue in raw_issues:
            key = raw_issue.get("key")
            if key and key in wi_by_key:
                wi = wi_by_key[key]
                try:
                    remote_links = client.get_remote_links(key)
                    link_jira_remote_links(session, wi, remote_links, prs_by_url)
                except Exception as rlink_exc:
                    logger.warning("Could not fetch remote links for %s: %s", key, rlink_exc)

        fetched = proj_counts.fetched + total_wi_fetched + dep_counts.fetched
        written = proj_counts.written + total_wi_written + dep_counts.written
        skipped = proj_counts.skipped + total_wi_skipped + dep_counts.skipped

        status = "partial" if skipped > 0 else "success"

        # Advance cursor only on success/partial
        advance_cursor(session, "jira", cursor_scope, datetime.now(UTC).isoformat())

        sync_run.status = status
        sync_run.items_fetched = fetched
        sync_run.items_written = written
        sync_run.items_skipped = skipped
        sync_run.finished_at = datetime.now(UTC)
        session.commit()
        return sync_run

    except Exception as exc:
        session.rollback()
        err_type = exc.error_type if isinstance(exc, IntegrationError) else type(exc).__name__
        safe_detail = sanitize_error_detail(str(exc))
        sync_run.status = "failed"
        sync_run.error_type = err_type
        sync_run.error_detail = safe_detail
        sync_run.finished_at = datetime.now(UTC)
        session.commit()
        logger.error("Jira sync failed: %s (%s)", err_type, safe_detail)
        return sync_run


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for running integration synchronizations."""
    parser = argparse.ArgumentParser(description="Headref Ingestion Sync CLI")
    parser.add_argument(
        "--source",
        choices=["github", "jira", "all"],
        required=True,
        help="Integration source to sync",
    )
    parser.add_argument(
        "--team",
        type=int,
        default=1,
        help="Target team ID",
    )
    parser.add_argument(
        "--reset-cursor",
        action="store_true",
        help="Reset sync cursor to force full resync",
    )

    args = parser.parse_args(argv)

    session = get_sync_session()
    try:
        if args.source in ("github", "all"):
            gh_run = sync_github(session, team_id=args.team, reset_cursor=args.reset_cursor)
            print(
                f"GitHub Sync finished: status={gh_run.status} "
                f"fetched={gh_run.items_fetched} written={gh_run.items_written} "
                f"skipped={gh_run.items_skipped}"
            )
            if gh_run.status == "failed":
                return 1

        if args.source in ("jira", "all"):
            jira_run = sync_jira(session, team_id=args.team, reset_cursor=args.reset_cursor)
            print(
                f"Jira Sync finished: status={jira_run.status} "
                f"fetched={jira_run.items_fetched} written={jira_run.items_written} "
                f"skipped={jira_run.items_skipped}"
            )
            if jira_run.status == "failed":
                return 1

        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
