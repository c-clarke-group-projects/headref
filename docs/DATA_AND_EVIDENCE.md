# headref Data and Evidence

The information foundation: where data comes from, what it becomes, and how it turns into
evidence the agent can cite.

Read with `ARCHITECTURE.md` (the system) and `AGENT_TOOLS.md` (how the agent reads it).

---

## 6.1 Data sources

### GitHub (MVP)

| Property | Value |
|---|---|
| **Purpose** | Code activity: what was written, reviewed and merged |
| **Access** | Fine-grained personal access token, read-only, only the repositories configured |
| **Data collected** | Pull requests (state, draft, branch, title, body), reviews (state, body, timestamp), commits (sha, message, branch, timestamp), branch names, check status |
| **Not collected** | File contents, diffs, repository settings, org membership, anything from a repository not explicitly configured |
| **Authority for** | Pull request state, review state, commit activity, branch existence |
| **Freshness requirement** | 24 hours. Beyond that, `stale` |
| **Rate limit** | Approximately 5000 requests per hour for a token-authenticated caller. Search endpoints are much lower. To be confirmed in P0-002 |
| **Typical failures** | 401 expired token, 403 with a rate-limit header, 404 repository not visible to the token, 5xx |

### Jira (MVP)

| Property | Value |
|---|---|
| **Purpose** | Planned work: what is assigned, its state, and what depends on what |
| **Access** | Email plus API token, read-only, only the projects configured |
| **Data collected** | Issues (key, title, status, assignee, priority, due date), issue links (blocks / is blocked by), impediment flag, remote links to GitHub |
| **Not collected** | Attachments, worklogs, time tracking, custom fields not explicitly mapped, comments from projects not configured |
| **Authority for** | Assignment, ticket status, priority, due date, declared blockers, issue dependencies |
| **Freshness requirement** | 24 hours. Beyond that, `stale` |
| **Rate limit** | Jira Cloud applies cost-based throttling and returns 429 with `Retry-After`. There is no fixed published number to design against. Budget for 429 and honour `Retry-After` |
| **Typical failures** | 401 expired token, 403 project not permitted, 429 throttled, 5xx |

### Not data sources in the MVP

| Source | Status | Reason |
|---|---|---|
| Slack, Teams | FUTURE, Stage 2 | DEC-014, and DR-1 legal review |
| Calendar | FUTURE, Stage 3 | DEC-014 |
| CI/CD systems | Not planned | Check status comes from GitHub |
| Time tracking | Permanently excluded | Would make this a surveillance tool |
| IDE or editor telemetry | Permanently excluded | Same |

---

## 6.2 Data flow

```text
  EXTERNAL SOURCES                    WRITE PATH (network)          Developer 1
  ================                    ====================
                                                                   Triggered by:
   GitHub API  ----------+                                         python -m app.sync
                         |                                         (sched 5min / on demand)
   Jira API  ------------+
                         |
                         v
              +---------------------------+
              | app/integrations/         |  httpx + tenacity
              |   github_client.py        |  timeout 10s, max 3 attempts
              |   jira_client.py          |  typed failures
              +------------+--------------+
                           |
                           v
              +---------------------------+
              | app/integrations/ingest.py|  normalize to canonical
              |   identity_resolver.py    |  map external account -> app_user
              |   link_builder.py         |  build work_item_link
              +------------+--------------+
                           |
                           v
              +---------------------------+
              |      PostgreSQL           |  single source of truth
              |  canonical tables         |  every row carries
              |  + identity + links       |  source_updated_at, retrieved_at
              |  + sync_run, sync_cursor  |
              +------------+--------------+
                           ^
  ========================= | ============================================
                           |
              +------------+--------------+          READ PATH (no network)
              |  app/tools/  (T-001..007) |          Developer 2
              |  PostgreSQL reads only    |
              +------------+--------------+
                           |
                           v
              +---------------------------+
              | app/agent/                |
              |  evidence_builder.py      |  correlate, dedupe, rank
              |   -> assigns ev_1..ev_n   |  truncate, sanitize
              +------------+--------------+
                           |
                           v
              +---------------------------+
              |  reasoning.py  (1 LLM call)|  cites evidence by ID
              |  validation.py (code)      |  drops unsupported claims
              |  confidence.py (code)      |  assigns HIGH/MED/LOW
              +------------+--------------+
                           |
                           v
                    MemberInsight -> UI
```

**The 2 paths never cross.** The write path makes network calls. A web request may
*start* one in the background (FR-035) but never waits for it. The read path makes no
network calls. See DEC-002, DEC-003 and DEC-016.

---

## 6.3 Canonical entities

Only entities the MVP actually uses. Every table holding external data carries
`source_updated_at`, `retrieved_at` and the external ID it came from.

### Core

**`organization`** One row in the MVP. Exists so the column is there when multi-tenancy
arrives.
`id`, `name`, `created_at`

**`team`** A group of people a lead is responsible for.
`id`, `organization_id`, `name`, `created_at`

**`app_user`** An internal person. **Named `app_user`, not `user`, because `user` is a
reserved word in PostgreSQL.**
`id`, `organization_id`, `team_id`, `display_name`, `role_label`, `is_active`,
`created_at`

**`repository`** A configured GitHub repository.
`id`, `organization_id`, `full_name` ("acme/api"), `default_branch`, `is_active`

**`project`** A configured Jira project.
`id`, `organization_id`, `key` ("AUTH"), `name`, `is_active`

### Identity

**`identity_link`** Maps an external account to 1 internal person. See DEC-008.
`id`, `app_user_id`, `integration` (github|jira), `external_id`, `external_handle`,
`match_method` (manual|inferred), `confidence`, `verified_at`, `created_at`
Unique on `(integration, external_id)`.

**`unmatched_entity`** External accounts seen during ingestion that could not be mapped.
`id`, `integration`, `external_id`, `external_handle`, `first_seen_at`, `last_seen_at`,
`occurrence_count`, `resolved_app_user_id` (null until a human resolves it)
Unique on `(integration, external_id)`.

### Work

**`work_item`** A Jira issue.
`id`, `project_id`, `external_id` ("AUTH-245"), `title`, `status` (normalized),
`raw_status`, `assignee_app_user_id` (nullable), `priority`, `due_date`, `is_flagged`,
`source_url`, `source_updated_at`, `retrieved_at`

**`work_item_dependency`** Jira issue links of the blocking kind.
`id`, `work_item_id`, `blocked_by_work_item_id`, `link_type`, `retrieved_at`

**`pull_request`** A GitHub pull request.
`id`, `repository_id`, `number`, `title`, `body_excerpt`, `state` (open|merged|closed),
`is_draft`, `branch_name`, `author_app_user_id` (nullable), `review_state`,
`last_review_at`, `checks_state`, `created_at_source`, `last_commit_at`, `merged_at`,
`source_url`, `source_updated_at`, `retrieved_at`

**`commit`** A GitHub commit.
`id`, `repository_id`, `sha`, `message_excerpt`, `branch_name`,
`author_app_user_id` (nullable), `committed_at`, `source_url`, `retrieved_at`
Unique on `(repository_id, sha)`.

**`review`** A GitHub pull request review.
`id`, `pull_request_id`, `external_id`, `reviewer_app_user_id` (nullable), `state`,
`body_excerpt`, `submitted_at`, `source_url`, `retrieved_at`

**`work_item_link`** The correlation between planned work and code. See DEC-009.
`id`, `work_item_id`, `target_type` (pull_request|branch|commit|review), `target_id`,
`link_method`, `confidence`, `created_at`
Unique on `(work_item_id, target_type, target_id)`.

### Agent

**`insight`** A cached validated answer. Keyed by the evidence hash. See FR-031.
`id`, `subject_app_user_id`, `question_type`, `evidence_hash`, `payload` (JSON),
`created_at`, `expires_at`

**`agent_run`** A record of every run. See FR-024.
`id`, `actor_app_user_id`, `subject_app_user_id`, `question_type`, `window_start`,
`window_end`, `evidence_set` (JSON), `source_health` (JSON), `prompt_version`, `model`,
`raw_model_output` (JSON), `validated_output` (JSON), `dropped_claims` (JSON),
`input_tokens`, `output_tokens`, `latency_ms`, `error_type`, `created_at`

**Note:** `evidence` is not a table in the MVP. The evidence set is built per run and
stored as JSON inside `agent_run.evidence_set`. Promoting it to its own table is a Stage 2
change and is only worth doing if evidence needs to be queried across runs.

### Operations

**`sync_run`** Every ingestion attempt. See FR-008.
`id`, `source`, `scope`, `status` (running|success|partial|failed), `started_at`,
`finished_at`, `items_fetched`, `items_written`, `items_skipped`, `error_type`,
`error_detail`

**`sync_cursor`** Resume position per source and scope. See FR-009.
`source`, `scope`, `cursor_value`, `updated_at`
Primary key `(source, scope)`.

### Table count

17 tables. Small enough for 2 people, complete enough for every MVP requirement.

---

## 6.4 Entity relationships

```text
organization
   |
   +-- team --------- app_user ------+-------------------+
   |                     |  ^        |                   |
   |                     |  |        |                   |
   +-- repository        |  |   identity_link      unmatched_entity
   |                     |  |   (external -> person)  (no person yet)
   +-- project           |  |
                         |  |
                         |  +-- assignee_app_user_id
                         |  +-- author_app_user_id
                         |  +-- reviewer_app_user_id
                         |
   THE CORRELATION CHAIN, which is the core of the product:

   app_user
      |
      | assigned to
      v
   work_item  (AUTH-245, Jira)
      |
      | work_item_link (link_method = branch_name, confidence HIGH)
      v
   pull_request  (acme/api #182, branch feature/AUTH-245-refresh-token)
      |                    |
      | contains           | received
      v                    v
   commit               review
   (sha, message)       (changes_requested, 2026-09-09)
```

### How the chain is built

The chain is built **during ingestion**, not at request time (DEC-009).

1. Jira ingestion writes `work_item` rows with external IDs like `AUTH-245`.
2. GitHub ingestion writes `pull_request`, `commit` and `review` rows.
3. `identity_resolver` maps each external actor to an `app_user_id`, or records it in
   `unmatched_entity` and leaves the actor null.
4. `link_builder` scans branch names, pull request titles and bodies, commit messages, and
   Jira remote links for work item external IDs, and writes `work_item_link` rows with the
   method and confidence used.

### Why a link may be missing

- The branch does not contain a ticket ID and neither does the title, body or any commit
  message. The pull request is genuinely unlinked. It appears as unlinked work. headref does
  **not** guess (scenario S-8).
- The work item belongs to a Jira project that is not configured.
- The pull request is in a repository that is not configured.

Missing links reduce coverage, and coverage is reported honestly rather than filled in.

---

## 6.5 Evidence model

Evidence is what a claim points at. It is built by application code in stage S3, before
any model call, and frozen for the run. See DEC-004.

```python
class EvidenceItem(BaseModel):
    id: str                    # "ev_1", stable within 1 agent run only
    source: Literal["jira", "github"]
    entity_type: Literal[
        "work_item", "pull_request", "commit", "review", "work_item_link"
    ]
    entity_key: str            # "AUTH-245", "acme/api#182", a commit sha
    source_url: str            # deep link to the original record
    summary: str               # 1 factual line, GENERATED BY CODE
    excerpt: str | None        # untrusted source text, capped and URL-stripped
    observed_at: datetime      # when the event happened at the source
    retrieved_at: datetime     # when headref fetched it
    source_state: Literal["fresh", "stale"]   # from T-007 at build time
```

### Field rules

| Field | Rule |
|---|---|
| `id` | Assigned sequentially by code in S3. Unique within 1 run. Not stable across runs |
| `source_url` | MUST resolve to the real record. Shown in the evidence drawer (FR-027) |
| `summary` | MUST be generated by application code from the record's structured fields. **Never written by the model** |
| `excerpt` | Untrusted. Capped at `EXCERPT_MAX_CHARS` (500). URLs stripped. Escaped before render |
| `observed_at` | From the source record, not from the fetch |
| `retrieved_at` | From ingestion. Together with `observed_at` this is the freshness contract |
| `source_state` | Copied from `get_source_health` at evidence build time, so confidence rules can see it |

### Example

```json
{
  "id": "ev_1",
  "source": "jira",
  "entity_type": "work_item",
  "entity_key": "AUTH-245",
  "source_url": "https://example.atlassian.net/browse/AUTH-245",
  "summary": "AUTH-245 'Refresh token rotation' assigned to Keshan, status In Progress, priority High, due 2026-09-16",
  "excerpt": null,
  "observed_at": "2026-09-12T09:14:00Z",
  "retrieved_at": "2026-09-13T06:00:00Z",
  "source_state": "fresh"
}
```

### The lifecycle of an evidence ID

```text
S3  code builds the set        ev_1, ev_2, ev_3 assigned
S4  model receives the set     model returns claim citing ["ev_1", "ev_3"]
S5  code resolves the IDs      ev_1 and ev_3 found -> claim kept
                               a cited "ev_9" would not be found -> claim dropped
S6  code attaches evidence     the response carries the resolved ev_1 and ev_3 objects,
                               never anything the model wrote
```

---

## 6.6 Source authority

Which source is trusted for which kind of fact. This table is enforced by validation rule
2: a claim classified `fact` must cite at least 1 evidence item from the authoritative
source for that fact type.

| Fact type | Authoritative source | Notes |
|---|---|---|
| Work item assignment | **Jira** | GitHub cannot know who a ticket is assigned to |
| Work item status | **Jira** | The declared state of the work |
| Priority | **Jira** | |
| Due date | **Jira** | |
| Declared blocker or impediment flag | **Jira** | An explicitly recorded blocker |
| Issue dependency (blocks / is blocked by) | **Jira** | |
| Pull request state (open, merged, closed) | **GitHub** | Jira remote links lag |
| Draft status | **GitHub** | |
| Review state | **GitHub** | |
| Commit activity | **GitHub** | |
| Branch existence and name | **GitHub** | |
| Check status | **GitHub** | |
| Work item to code correlation | **`work_item_link`** | Derived, carries its own confidence |
| Likely current work | **Neither alone** | Always an inference across both |
| Whether work is complete | **Neither alone** | Disagreement is a conflict, not a fact |
| Source availability | **`sync_run`** | Never inferred from an empty query result |

### Consequences

- A claim about assignment that cites only GitHub evidence is downgraded from `fact` to
  `inference`.
- A claim about pull request state that cites only Jira evidence is downgraded.
- "Current work" can never be a `fact`. It is always an `inference` requiring 2 or more
  evidence items.
- "This work is complete" can never be a `fact` when the sources disagree. It is a conflict.

---

## 6.7 Conflict handling

A conflict is disagreement between sources about the same underlying thing. Conflicts are
detected by deterministic code in stage S5, never by the model.

### Detected conflicts (MVP)

| ID | Condition | Reported as |
|---|---|---|
| **CF-1** | Jira status `in_progress` and the linked pull request is `merged` | "Jira shows AUTH-245 In Progress, but linked pull request 182 is merged." |
| **CF-2** | Jira status `done` and the linked pull request is `open` | "Jira shows AUTH-245 Done, but linked pull request 182 is still open." |
| **CF-3** | Jira status `done` and commits on the linked branch after the Jira transition | "AUTH-245 was marked Done on 2026-09-10, but commits on its branch continued to 2026-09-12." |
| **CF-4** | Jira status `blocked` and the linked pull request is approved with passing checks | "AUTH-245 is flagged blocked, but linked pull request 182 is approved with passing checks." |

### Rules

- **C-1** A conflict MUST be reported. It MUST NOT be silently resolved.
- **C-2** headref MUST NOT choose a winner. It reports both states and lets the human decide.
- **C-3** An unresolved conflict affecting a claim drops that claim's confidence 1 level.
- **C-4** Conflicts are produced by code. The model does not detect or report them.
- **C-5** A conflict is not an error. It is often the most useful thing on the page,
  because it usually means a ticket needs updating.

### Why not resolve automatically

The cases are genuinely ambiguous. A merged pull request with an In Progress ticket might
mean the ticket was not updated, or that the merge was only part of the work, or that a
follow-up is needed. Picking one would be a guess presented as a fact, which violates 5.5
H-4.

---

## 6.8 Freshness

Every answer depends on data that was fetched at some point in the past. The age of that
data is part of the answer.

### The 3 timestamps

| Timestamp | Meaning | Set by |
|---|---|---|
| `source_updated_at` | When the source system last changed this record | Ingestion, from the payload |
| `retrieved_at` | When headref fetched it | Ingestion, at fetch time |
| `sync_run.finished_at` | When the sync that fetched it completed | The sync runner |

All 3 are `timestamptz` in UTC. Conversion happens at the integration boundary and nowhere
else. The UI renders local time and shows the UTC value on hover.

### The 3 states

Computed by `get_source_health` (T-007) from `sync_run`. See DEC-010.

| State | Condition | Effect on the answer |
|---|---|---|
| **`fresh`** | Last successful sync within `FRESHNESS_WINDOW_HOURS` (24) | Normal confidence |
| **`stale`** | Last successful sync older than 24 hours, but data exists | Confidence drops 1 level. The age is shown |
| **`unavailable`** | The most recent attempt failed, or no successful sync has ever occurred | If the source is **required** for the question: answer is UNKNOWN with the reason. If **optional**: continue with reduced confidence |

### The rule this exists to enforce

**An empty query result MUST NOT be read as "nothing is wrong".**

`SELECT ... WHERE status = 'blocked'` returning 0 rows means either that nothing is
blocked, or that Jira has not been synced since the blocker was created. Only `sync_run`
can tell the difference, and checking it is mandatory on every run (T-8 in
`AI_BEHAVIOR.md` 5.7).

### Configuration

All thresholds are named constants in `app/config.py`, never literals in logic:

```text
SYNC_INTERVAL_MINUTES    = 5     # scheduler cadence (DEC-016)
FRESHNESS_WINDOW_HOURS   = 24    # fresh -> stale boundary
RECENT_ACTIVITY_DAYS     = 14    # default question window
PR_REVIEW_WAIT_DAYS      = 3     # blocker: unreviewed pull request
DRAFT_PR_STALE_DAYS      = 5     # blocker: long-lived draft
ISSUE_NO_CODE_DAYS       = 3     # blocker: in progress with no code
DUE_SOON_DAYS            = 3     # risk: approaching due date
STATUS_STUCK_DAYS        = 5     # risk: time in the same status
NO_ACTIVITY_DAYS         = 7     # risk: no activity on a high priority item
EXCERPT_MAX_CHARS        = 500   # untrusted text cap
MAX_EVIDENCE_ITEMS       = 40    # evidence set truncation
```

---

## 6.9 Data quality

### Blocker detection signals

The 8 deterministic signals required by FR-020. Slack is out of scope (DEC-014), so every
blocker comes from Jira or GitHub structure.

| ID | Signal | Source | Blocker type |
|---|---|---|---|
| **BL-1** | Work item status is `blocked` | Jira | explicit |
| **BL-2** | Impediment flag is set | Jira | explicit |
| **BL-3** | An open `is blocked by` dependency exists | Jira | dependency |
| **BL-4** | Pull request has `changes_requested` and no commit since the review | GitHub | review |
| **BL-5** | Pull request open with no review for more than `PR_REVIEW_WAIT_DAYS` | GitHub | review |
| **BL-6** | Pull request is draft and older than `DRAFT_PR_STALE_DAYS` | GitHub | progress |
| **BL-7** | Checks failing on the head commit | GitHub | environment |
| **BL-8** | Work item `in_progress` with no linked branch or pull request after `ISSUE_NO_CODE_DAYS` | Both | progress |

### Risk detection signals

The 4 deterministic signals required by FR-021.

| ID | Signal | Source |
|---|---|---|
| **RK-1** | Due date within `DUE_SOON_DAYS` and status is not `done` | Jira |
| **RK-2** | Time in the current status exceeds `STATUS_STUCK_DAYS` | Jira |
| **RK-3** | High priority item with no activity for `NO_ACTIVITY_DAYS` | Jira + GitHub |
| **RK-4** | An unresolved Jira / GitHub conflict (CF-1 to CF-4) | Both |

Every risk statement describes the work item and the dates. It never describes the person.

### Missing data

| Case | Handling |
|---|---|
| A field is absent from the payload | Store null. Never substitute a default that could be mistaken for a real value |
| Jira does not expose the assignee email | Expected. Identity comes from the manual map (DEC-008) |
| A work item has no due date | Not a risk signal. RK-1 simply does not fire |
| A pull request has no linked work item | Reported as unlinked. Never guessed (S-8) |
| A commit has no matching author identity | Excluded from the evidence set. Recorded in `unmatched_entity` |
| A whole source has never been synced | `unavailable`. Answers depending on it are UNKNOWN |

### Duplicates

| Case | Handling |
|---|---|
| Re-running a sync | Idempotent upsert on the natural key. `(repository_id, sha)` for commits, `(integration, external_id)` for identity, and so on. NFR-003 |
| The same event reachable by 2 paths | Deduplicated in S3 by `(entity_type, entity_key)` before evidence IDs are assigned |
| The same work item linked to a pull request by 2 methods | 1 `work_item_link` row wins, the highest-confidence method. Unique constraint enforces this |
| The same person mapped to 2 accounts on 1 integration | Allowed. A person may have 2 GitHub accounts. The unique constraint is on the external side, not the person side |

### Unmatched identities

| Case | Handling |
|---|---|
| An external account is not in `identity_map.yml` | The record is ingested with a null actor. `unmatched_entity` is created or its count incremented |
| An inferred match is found | Stored with `match_method = inferred`. **Not used for attribution** until confirmed (DEC-008) |
| A human confirms a mapping | `resolved_app_user_id` is set. The next sync attributes the activity. Existing rows are backfilled by a re-sync |
| 2 people have similar display names | Irrelevant. Display names are never used for matching. There is no code path for it (H-7) |

### Malformed data

| Case | Handling |
|---|---|
| A payload fails schema validation at ingestion | Skip the record, increment `items_skipped`, log what was skipped and why, continue. 1 bad record MUST NOT fail a whole sync |
| A stored row fails to validate into a tool output model | Return `SCHEMA_INVALID` from the tool, exclude the row, log at error level. Indicates an ingestion bug |
| A Jira status string does not map to a normalized value | Store `raw_status`, map `status` to the closest known value, log the unmapped string. An unmapped status appearing repeatedly is a signal to extend the mapping |
| A timestamp has no timezone | Treat as UTC and log it. Never guess a local timezone |
| Text contains control characters or is not valid UTF-8 | Sanitize at ingestion. Never store raw bytes that will break rendering |

### Data quality visibility

These counts MUST be visible, not buried in logs:

- Unresolved `unmatched_entity` count, shown in the UI (FR-003).
- `items_skipped` per `sync_run`.
- Pull requests with no `work_item_link`, as a coverage measure.
- Per-source `state` from T-007 on every page showing member data (NFR-020).

Coverage is a real metric for this product. If half the pull requests are unlinked, headref
is only half as useful, and the honest thing is to show that rather than to infer links
more aggressively.
