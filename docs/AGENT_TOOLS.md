# headref Agent Tools

Every tool available to the headref agent, with its full contract.

---

## Important: what "tool" means in headref

In many agent systems a tool is something the model chooses to call. **That is not the
case here.**

Per DEC-001 and DEC-005, the reasoning call in stage S4 is passed **no tools at all**. The
model never selects, invokes or sees a tool. It receives a finished evidence set and
returns claims.

In headref a tool is a **typed read function invoked by the deterministic orchestrator in
stage S2**, according to the fixed plan produced in stage S1. Tools are called by code, in
a fixed order, with parameters derived from the plan and never from model output.

The tool contracts below still matter, and they are still strict, because:

- They define the boundary between the read path and the database.
- They define the typed failures the agent must reason about (FR-033).
- They are the contract Developer 2 builds against.
- They are what a future free-text version of headref would expose if a model ever were
  allowed to choose (Stage 2, requires a new decision record).

### The 3 rules that apply to every tool

1. **A tool MUST NOT make an external network call.** Tools read PostgreSQL only.
   No module under `app/tools/` may import `httpx` or any HTTP client. A test enforces
   this. See DEC-002.
2. **A tool MUST return a Pydantic model or a typed failure.** Never a raw dict, never
   raw upstream JSON, never a bare exception, never an empty list standing in for an error.
3. **A tool MUST NOT widen access.** Every tool takes a `subject_user_id` or `team_id`
   already checked by `can_view_member` before stage S1. A tool never re-derives who the
   caller may see, and never returns records for a different subject.

### Standard typed failures

Every tool returns either its success model or a `ToolFailure`:

```python
class ToolFailure(BaseModel):
    tool_id: str
    error_type: Literal[
        "TIMEOUT",          # database query exceeded the statement timeout
        "NOT_FOUND",        # the requested subject or team does not exist
        "UPSTREAM_ERROR",   # database error
        "SCHEMA_INVALID",   # a stored row failed to validate into the output model
    ]
    detail: str             # human readable, MUST NOT contain secrets
    occurred_at: datetime
```

`RATE_LIMITED` and `AUTH_FAILED` appear in the write path (`app/integrations/`), not here.
Tools cannot be rate limited or denied because they do not call anyone.

**An empty result is not a failure.** A tool that finds no rows returns an empty list with
success. The difference between "no rows" and "the source that fills this table is down"
comes from T-007, never from an empty list.

---

## Tool index

| ID | Name | Category | Access | Status |
|---|---|---|---|---|
| T-001 | `get_team_members` | Directory | Read | MVP |
| T-002 | `get_assigned_work_items` | Jira-derived | Read | MVP |
| T-003 | `get_pull_requests` | GitHub-derived | Read | MVP |
| T-004 | `get_commits` | GitHub-derived | Read | MVP |
| T-005 | `get_reviews` | GitHub-derived | Read | MVP |
| T-006 | `get_work_item_links` | Correlation | Read | MVP |
| T-007 | `get_source_health` | Operational | Read | MVP |
| T-101 | `search_slack_messages` | Slack | Read | FUTURE |
| T-102 | `get_calendar_events` | Calendar | Read | FUTURE |
| T-103 | `post_slack_summary` | Slack | **Write** | FUTURE, blocked |
| T-104 | `add_jira_comment` | Jira | **Write** | FUTURE, blocked |

Which tools each question type uses:

| question_type | Tools called in S2 |
|---|---|
| `current_work` | T-001, T-002, T-003, T-004, T-006, T-007 |
| `blockers` | T-001, T-002, T-003, T-005, T-006, T-007 |
| `risks` | T-001, T-002, T-003, T-006, T-007 |

---

# Category: Directory

## T-001 `get_team_members`

| Field | Value |
|---|---|
| **Tool ID** | T-001 |
| **Name** | `get_team_members` |
| **Purpose** | Return the members of a team with their internal IDs and mapped external accounts |
| **Category** | Directory |
| **Access** | Read |
| **Data source** | PostgreSQL: `app_user`, `team`, `identity_link` |
| **Timeout** | 2 seconds (statement timeout) |
| **Retry policy** | None. A database read either succeeds or the request fails |
| **Rate limits** | None |
| **Requirements** | FR-001, FR-002 |

**Inputs**

```python
class GetTeamMembersInput(BaseModel):
    team_id: int
    include_inactive: bool = False
```

**Outputs**

```python
class TeamMemberOut(BaseModel):
    user_id: int
    display_name: str
    role_label: str | None
    is_active: bool
    linked_accounts: list[LinkedAccount]   # verified links only

class LinkedAccount(BaseModel):
    integration: Literal["github", "jira"]
    external_handle: str
    match_method: Literal["manual", "inferred"]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]

class GetTeamMembersOutput(BaseModel):
    members: list[TeamMemberOut]
    unmatched_count: int    # from unmatched_entity, for the UI banner
```

**Required permissions:** the caller must be authorized for `team_id`.

**When the agent MAY use it:** in stage S2 for every question type, to establish the
subject and their external handles.

**When it MUST NOT be used:** never to enumerate members of a team the actor cannot see.
Never as an autocomplete or people-search endpoint exposed to the browser.

**Failure cases:** `NOT_FOUND` if the team does not exist. `UPSTREAM_ERROR` on a database
error.

**Security considerations:** `linked_accounts` includes `inferred` links, which MUST be
displayed as unconfirmed and MUST NOT be used for attribution (DEC-008). Consumers must
check `match_method`.

**Audit requirements:** none beyond the `agent_run` record. This tool reads no sensitive
content.

**Example input**

```json
{"team_id": 1, "include_inactive": false}
```

**Example output**

```json
{
  "members": [
    {
      "user_id": 7,
      "display_name": "Keshan",
      "role_label": "Backend Engineer",
      "is_active": true,
      "linked_accounts": [
        {"integration": "github", "external_handle": "keshan-dev",
         "match_method": "manual", "confidence": "HIGH"},
        {"integration": "jira", "external_handle": "Keshan P.",
         "match_method": "manual", "confidence": "HIGH"}
      ]
    }
  ],
  "unmatched_count": 1
}
```

---

# Category: Jira-derived

## T-002 `get_assigned_work_items`

| Field | Value |
|---|---|
| **Tool ID** | T-002 |
| **Name** | `get_assigned_work_items` |
| **Purpose** | Return Jira work items assigned to a person, with normalized status, priority, due date, flags and issue links |
| **Category** | Jira-derived |
| **Access** | Read |
| **Data source** | PostgreSQL: `work_item`, `project` |
| **Timeout** | 2 seconds |
| **Retry policy** | None |
| **Rate limits** | None |
| **Requirements** | FR-005, FR-007, FR-012, FR-020, FR-021 |

**Inputs**

```python
class GetAssignedWorkItemsInput(BaseModel):
    subject_user_id: int
    window_start: datetime          # UTC
    window_end: datetime            # UTC
    include_done: bool = True       # done items are needed for conflict detection
    statuses: list[str] | None = None
```

**Outputs**

```python
class WorkItemOut(BaseModel):
    work_item_id: int
    external_id: str                # "AUTH-245"
    title: str
    status: Literal["todo", "in_progress", "in_review", "done", "blocked"]
    raw_status: str                 # the upstream string, preserved
    assignee_user_id: int | None    # null when the assignee is unmatched
    priority: str | None
    due_date: datetime | None
    is_flagged: bool                # Jira impediment flag
    blocked_by: list[str]           # external IDs of open blocking issues
    source_url: str
    source_updated_at: datetime
    retrieved_at: datetime

class GetAssignedWorkItemsOutput(BaseModel):
    items: list[WorkItemOut]
```

**Required permissions:** the caller must be authorized to view `subject_user_id`.

**When the agent MAY use it:** in S2 for all 3 question types. Jira is the authoritative
source for assignment, status, priority and due date.

**When it MUST NOT be used:**
- MUST NOT be called with a `subject_user_id` the actor is not authorized for.
- MUST NOT be used to produce any aggregate that compares people, such as issue counts
  per person ranked against each other. See `AI_BEHAVIOR.md` 5.11.
- MUST NOT be used as a general Jira search. It filters by assignee only.

**Failure cases:** `NOT_FOUND` if the user does not exist. `SCHEMA_INVALID` if a stored
row has an unmappable status, which indicates an ingestion bug and MUST be logged.

**Security considerations:** `title` originates from Jira and is untrusted text. It MUST
be escaped before rendering (FR-030) and MUST be treated as data, never instructions,
where it reaches a prompt (FR-029).

**Audit requirements:** covered by `agent_run.evidence_set`.

**Example output**

```json
{
  "items": [
    {
      "work_item_id": 41, "external_id": "AUTH-245",
      "title": "Refresh token rotation", "status": "in_progress",
      "raw_status": "In Progress", "assignee_user_id": 7,
      "priority": "High", "due_date": "2026-09-16T00:00:00Z",
      "is_flagged": false, "blocked_by": [],
      "source_url": "https://example.atlassian.net/browse/AUTH-245",
      "source_updated_at": "2026-09-12T09:14:00Z",
      "retrieved_at": "2026-09-13T06:00:00Z"
    }
  ]
}
```

---

# Category: GitHub-derived

## T-003 `get_pull_requests`

| Field | Value |
|---|---|
| **Tool ID** | T-003 |
| **Name** | `get_pull_requests` |
| **Purpose** | Return pull requests authored by a person, with state, draft flag, review state, branch name and check status |
| **Category** | GitHub-derived |
| **Access** | Read |
| **Data source** | PostgreSQL: `pull_request`, `repository` |
| **Timeout** | 2 seconds |
| **Retry policy** | None |
| **Rate limits** | None |
| **Requirements** | FR-004, FR-007, FR-012, FR-019, FR-020 |

**Inputs**

```python
class GetPullRequestsInput(BaseModel):
    subject_user_id: int
    window_start: datetime
    window_end: datetime
    states: list[Literal["open", "merged", "closed"]] | None = None
    include_drafts: bool = True
```

**Outputs**

```python
class PullRequestOut(BaseModel):
    pull_request_id: int
    number: int
    repo_full_name: str             # "acme/api"
    title: str
    body_excerpt: str | None        # capped, URL-stripped, untrusted
    state: Literal["open", "merged", "closed"]
    is_draft: bool
    branch_name: str | None
    author_user_id: int | None      # null when the author is unmatched
    review_state: Literal["none", "pending", "approved", "changes_requested"]
    last_review_at: datetime | None
    checks_state: Literal["unknown", "passing", "failing"]
    created_at: datetime
    last_commit_at: datetime | None
    merged_at: datetime | None
    source_url: str
    source_updated_at: datetime
    retrieved_at: datetime

class GetPullRequestsOutput(BaseModel):
    pull_requests: list[PullRequestOut]
```

**Required permissions:** authorized for `subject_user_id`.

**When the agent MAY use it:** in S2 for all 3 question types. GitHub is the authoritative
source for pull request state, draft status and review state.

**When it MUST NOT be used:**
- MUST NOT be used to count pull requests per person as a performance measure.
- MUST NOT be used to infer working hours from `created_at` or `last_commit_at`. Deriving
  activity times for a person is forbidden (`AI_BEHAVIOR.md` 5.9).

**Failure cases:** `NOT_FOUND`, `UPSTREAM_ERROR`, `SCHEMA_INVALID`.

**Security considerations:** `title` and `body_excerpt` are attacker-controllable by
anyone who can open a pull request. `body_excerpt` MUST already be capped at
`EXCERPT_MAX_CHARS` and stripped of URLs by the ingester. This is the primary prompt
injection vector in the product (FR-029).

**Audit requirements:** covered by `agent_run.evidence_set`.

---

## T-004 `get_commits`

| Field | Value |
|---|---|
| **Tool ID** | T-004 |
| **Name** | `get_commits` |
| **Purpose** | Return commits authored by a person within a window, with branch and message excerpt |
| **Category** | GitHub-derived |
| **Access** | Read |
| **Data source** | PostgreSQL: `commit`, `repository` |
| **Timeout** | 2 seconds |
| **Retry policy** | None |
| **Requirements** | FR-004, FR-007, FR-012 |

**Inputs**

```python
class GetCommitsInput(BaseModel):
    subject_user_id: int
    window_start: datetime
    window_end: datetime
    limit: int = 100
```

**Outputs**

```python
class CommitOut(BaseModel):
    commit_id: int
    sha: str
    repo_full_name: str
    message_excerpt: str            # capped, URL-stripped, untrusted
    branch_name: str | None
    author_user_id: int | None
    committed_at: datetime
    source_url: str
    retrieved_at: datetime

class GetCommitsOutput(BaseModel):
    commits: list[CommitOut]
    truncated: bool                 # true when more rows exist than `limit`
```

**When the agent MAY use it:** as supporting evidence for current work, and to determine
whether a pull request with changes requested has seen a push since the review.

**When it MUST NOT be used:**
- MUST NOT be used to produce commit counts presented as productivity.
- MUST NOT be used to derive a person's working pattern, active hours or days worked.
- MUST NOT be used to attribute a commit whose `author_user_id` is null. Unmatched commits
  are excluded from the evidence set entirely (DEC-008).

**Failure cases:** `NOT_FOUND`, `UPSTREAM_ERROR`.

**Security considerations:** commit messages are untrusted text. `truncated` MUST be
surfaced so an answer is not silently based on a partial view.

---

## T-005 `get_reviews`

| Field | Value |
|---|---|
| **Tool ID** | T-005 |
| **Name** | `get_reviews` |
| **Purpose** | Return code reviews, both those given by the person and those received on their pull requests |
| **Category** | GitHub-derived |
| **Access** | Read |
| **Data source** | PostgreSQL: `review`, `pull_request` |
| **Timeout** | 2 seconds |
| **Retry policy** | None |
| **Requirements** | FR-004, FR-020 |

**Inputs**

```python
class GetReviewsInput(BaseModel):
    subject_user_id: int
    window_start: datetime
    window_end: datetime
    direction: Literal["given", "received", "both"] = "both"
```

**Outputs**

```python
class ReviewOut(BaseModel):
    review_id: int
    pull_request_id: int
    pull_request_number: int
    repo_full_name: str
    reviewer_user_id: int | None
    state: Literal["approved", "changes_requested", "commented", "dismissed"]
    body_excerpt: str | None        # capped, URL-stripped, untrusted
    submitted_at: datetime
    source_url: str
    retrieved_at: datetime

class GetReviewsOutput(BaseModel):
    reviews: list[ReviewOut]
```

**When the agent MAY use it:** primarily for the `blockers` question. A `changes_requested`
review with no subsequent commit is 1 of the 8 blocker signals.

**When it MUST NOT be used:**
- MUST NOT be used to evaluate review quality or to count reviews as a performance measure.
- MUST NOT be used to characterize the reviewer's behaviour. Review records are evidence
  about the state of the work, not about the reviewer.

**Failure cases:** `NOT_FOUND`, `UPSTREAM_ERROR`.

**Security considerations:** review bodies are untrusted text written by other people, and
are a realistic injection vector.

---

# Category: Correlation

## T-006 `get_work_item_links`

| Field | Value |
|---|---|
| **Tool ID** | T-006 |
| **Name** | `get_work_item_links` |
| **Purpose** | Return the stored links between Jira work items and branches, pull requests, commits and reviews |
| **Category** | Correlation |
| **Access** | Read |
| **Data source** | PostgreSQL: `work_item_link` |
| **Timeout** | 2 seconds |
| **Retry policy** | None |
| **Requirements** | FR-011 |

**Inputs**

```python
class GetWorkItemLinksInput(BaseModel):
    work_item_ids: list[int] | None = None
    pull_request_ids: list[int] | None = None
    min_confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
```

At least 1 of `work_item_ids` or `pull_request_ids` MUST be provided.

**Outputs**

```python
class WorkItemLinkOut(BaseModel):
    link_id: int
    work_item_id: int
    work_item_external_id: str
    target_type: Literal["pull_request", "branch", "commit", "review"]
    target_id: int
    link_method: Literal[
        "jira_remote_link", "branch_name", "pr_title", "pr_body", "commit_message"
    ]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    created_at: datetime

class GetWorkItemLinksOutput(BaseModel):
    links: list[WorkItemLinkOut]
```

**When the agent MAY use it:** in S3, to join work items to code activity. This is the tool
that makes the product work.

**When it MUST NOT be used:**
- MUST NOT be used to create a link. This tool is read-only. Links are created during
  ingestion (DEC-009).
- MUST NOT be used to infer a link that is not stored. If no link exists, the work is
  unlinked and MUST be reported as unlinked (scenario S-8).

**Failure cases:** `UPSTREAM_ERROR`. An empty list is a valid, meaningful result.

**Security considerations:** none directly. Links carry no free text.

**Note on confidence:** `link_method` determines the link confidence, and that feeds the
claim confidence in S5. A chain of MEDIUM links cannot produce a HIGH claim.

---

# Category: Operational

## T-007 `get_source_health`

| Field | Value |
|---|---|
| **Tool ID** | T-007 |
| **Name** | `get_source_health` |
| **Purpose** | Report whether each data source is fresh, stale or unavailable, derived from sync history |
| **Category** | Operational |
| **Access** | Read |
| **Data source** | PostgreSQL: `sync_run` |
| **Timeout** | 2 seconds |
| **Retry policy** | None |
| **Requirements** | FR-008, FR-010, FR-023 |

**This is the most important tool in the system.** Without it the agent cannot tell the
difference between "Jira shows no blockers" and "Jira has been unreachable for 2 days".

**Inputs**

```python
class GetSourceHealthInput(BaseModel):
    team_id: int
    sources: list[Literal["github", "jira"]]
```

**Outputs**

```python
class SourceHealthOut(BaseModel):
    source: Literal["github", "jira"]
    state: Literal["fresh", "stale", "unavailable"]
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_error_type: str | None     # TIMEOUT | RATE_LIMITED | AUTH_FAILED | UPSTREAM_ERROR
    age_hours: float | None

class GetSourceHealthOutput(BaseModel):
    sources: list[SourceHealthOut]
```

State rules, per DEC-010:

| State | Condition |
|---|---|
| `fresh` | `last_success_at` within `FRESHNESS_WINDOW_HOURS` (24) |
| `stale` | `last_success_at` older than 24 hours, but data exists |
| `unavailable` | The most recent attempt failed, or `last_success_at` is null |

**When the agent MAY use it:** in stage S2, for every question type, always. It is never
optional.

**When it MUST NOT be used:**
- MUST NOT be skipped as an optimization. An answer produced without checking source
  health is not a valid headref answer.
- MUST NOT be used to trigger a sync. It reports; it does not act.

**Failure cases:** `UPSTREAM_ERROR`. If this tool itself fails, the run MUST return
UNKNOWN rather than assume the sources are healthy. Fail closed, never open.

**Security considerations:** `last_error_type` is a typed enum, never a raw upstream error
string, so no credential material can leak through it.

**Audit requirements:** the resolved health for every source is recorded in `agent_run`.

**Example output**

```json
{
  "sources": [
    {"source": "github", "state": "fresh",
     "last_success_at": "2026-09-13T06:00:00Z",
     "last_attempt_at": "2026-09-13T06:00:00Z",
     "last_error_type": null, "age_hours": 2.5},
    {"source": "jira", "state": "unavailable",
     "last_success_at": "2026-09-11T06:00:00Z",
     "last_attempt_at": "2026-09-13T06:00:00Z",
     "last_error_type": "AUTH_FAILED", "age_hours": 50.5}
  ]
}
```

Given this output and `question_type = blockers`, the run MUST return UNKNOWN with the
reason "Jira data is unavailable, last successful sync 2026-09-11". It MUST NOT report
"no blockers found".

---

# FUTURE tools

Not implemented. Listed so that nobody re-invents them ad hoc, and so the constraints are
recorded now.

## T-101 `search_slack_messages` (FUTURE)

**Status:** FUTURE, Stage 2 at the earliest. Blocked by DEC-014 and DR-1.
**Purpose:** find messages mentioning a blocker or a work item.
**Constraints when built:**
- MUST be opt-in per channel. Never workspace-wide.
- MUST be read-only.
- Requires the works-council and labour-law review (DR-1) to be closed first.
- Messages are untrusted text and the highest-risk injection source in the product.

## T-102 `get_calendar_events` (FUTURE)

**Status:** FUTURE, Stage 3.
**Purpose:** meeting load as context for reduced code activity.
**Constraints when built:**
- MUST NOT read private event details, only free/busy and titles where permitted.
- MUST NOT be used to infer working hours or presence (`AI_BEHAVIOR.md` 5.9).

## T-103 `post_slack_summary` (FUTURE, WRITE) and T-104 `add_jira_comment` (FUTURE, WRITE)

**Status:** BLOCKED. Stage 4 at the earliest.

These require reversing DEC-001, which is the decision that makes prompt injection
survivable in this product. Before either can exist the system needs:

1. A new decision record superseding DEC-001.
2. An approval gate with a human confirmation step.
3. Risk classification per action.
4. A full audit trail of attempted and performed actions.
5. Idempotency keys, so a retried action does not post twice.
6. A separate red-team evaluation suite for the write path.

**Until all 6 exist, no write tool may be added to this codebase, and the reasoning call
MUST continue to be made with no tools.**

---

## Adding a new tool

1. Confirm it reads PostgreSQL only. If it needs a network call, it belongs in
   `app/integrations/`, not here.
2. Add a section to this document using the same fields, with the next free ID.
3. Define the Pydantic input and output models in `app/schemas/tools.py`.
4. Define the typed failures it can return.
5. State explicitly when it MUST NOT be used.
6. Add it to the tool index and to the question-type table.
7. Add unit tests covering success, empty result and each failure type.
8. If it changes what the agent can see, add an evaluation scenario in
   `TESTING_AND_EVALUATION.md`.
