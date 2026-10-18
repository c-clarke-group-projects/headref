# headref System Architecture

The whole software system. For the AI reasoning design see `AGENT_ARCHITECTURE.md`, which
this document references rather than repeats.

---

## 1. System overview

headref is a single Python application with 2 entry points and 1 database.

| Entry point | Trigger | Makes network calls | Owner |
|---|---|---|---|
| **Web application** (`app/main.py`) | HTTP request | No | Developer 2 |
| **Sync / write path** (`app/sync.py`) | Scheduler every 5 min, on-demand refresh, or manual CLI | Yes, to GitHub and Jira | Developer 1 |

Both run in the same container image and share the same models and configuration. They are
separated by function, not by deployment.

The defining constraint: **the web request path never calls an external API, and never
waits for one.** Everything the web path serves was fetched earlier by the sync path and
written to PostgreSQL with a timestamp. A request may *start* a background sync
(FR-035); it never blocks on it. See DEC-002 and DEC-016.

---

## 2. Architecture diagram

```text
                            BROWSER
                               |
                               | HTTP
                               v
  +--------------------------------------------------------------+
  |  FastAPI application  (app/main.py)                           |
  |                                                               |
  |  +---------------------+    +------------------------------+  |
  |  | Web routes          |    | API routes                   |  |
  |  | app/web/routes.py   |    | /api/members/{id}/insight    |  |
  |  | Jinja2 templates    |    | /api/teams/{id}/overview     |  |
  |  +----------+----------+    +--------------+---------------+  |
  |             |                              |                  |
  |             +--------------+---------------+                  |
  |                            v                                  |
  |             +------------------------------+                  |
  |             | Auth: login stub             |                  |
  |             | can_view_member(actor, subj) |                  |
  |             +--------------+---------------+                  |
  |                            v                                  |
  |             +------------------------------+                  |
  |             | Agent Orchestrator           |                  |
  |             | app/agent/orchestrator.py    |                  |
  |             | S1..S6, see                  |                  |
  |             | AGENT_ARCHITECTURE.md 3.4    |                  |
  |             +------+----------------+------+                  |
  |                    |                |                         |
  |                    v                v                         |
  |         +----------------+   +---------------------+          |
  |         | Read tools     |   | Ollama (LOCAL)      |          |
  |         | app/tools/     |   | 1 call, stage S4    |----------+---> localhost:11434
  |         | T-001..T-007   |   | narrative only      |          |
  |         +-------+--------+   +---------------------+          |
  |                 |                                             |
  +-----------------|---------------------------------------------+
                    | SQL (read only)
                    v
  +--------------------------------------------------------------+
  |                      PostgreSQL 15                            |
  |   17 canonical tables, see DATA_AND_EVIDENCE.md 6.3           |
  +--------------------------------------------------------------+
                    ^
                    | SQL (write)
  +-----------------|---------------------------------------------+
  |  WRITE PATH  (python -m app.sync)                              |
  |  triggers: scheduler 5min | POST /teams/{id}/sync | CLI        |
  |                                                                |
  |  +----------------+   +-----------------+   +--------------+  |
  |  | github_client  |   | jira_client     |   | ingest       |  |
  |  | httpx+tenacity |   | httpx+tenacity  |   | identity     |  |
  |  | timeout 10s    |   | timeout 10s     |   | link_builder |  |
  |  | max 3 attempts |   | max 3 attempts  |   +--------------+  |
  |  +-------+--------+   +--------+--------+                     |
  +----------|---------------------|------------------------------+
             |                     |
             v                     v
        api.github.com    your-domain.atlassian.net
```

The only **external** network connections are GitHub and Jira from the sync path. Stage
S4 calls Ollama on `localhost`, which never leaves the machine. Nothing in `app/tools/`
calls anything at all (AC-1).

---

## 3. Frontend

Server-rendered Jinja2 templates with a small amount of vanilla JavaScript. No build step,
no framework, no bundler.

| Page | Route | Purpose | Requirement |
|---|---|---|---|
| Team overview | `GET /teams/{id}` | Member list with state and attention items | FR-025 |
| Member profile | `GET /members/{id}` | 1 member, 3 question types, evidence | FR-026 |
| Evidence drawer | Client-side panel | Expanded evidence with source links | FR-027 |
| Unmatched identities | `GET /admin/unmatched` | The unmatched queue | FR-003 |

There is also 1 action, not a page: **Refresh**, which calls `POST /api/teams/{id}/sync`,
gets a 202 back immediately, then polls `sync_run.status` until it leaves `running` and
reloads (FR-035, DEC-016).

JavaScript is used only to expand the evidence drawer, switch question type without a
full page reload, and poll a running sync. No client-side state management, no API
tokens in the browser.

**Rendering rules (FR-030):**
- Jinja2 autoescaping stays on. No template applies `|safe` to any field originating from
  GitHub or Jira.
- Markdown and HTML from source text are not rendered.
- Excerpts arrive already capped and URL-stripped from ingestion.

**Why server-rendered:** it is the lowest-workload choice for 2 people, there is no second
language or build pipeline, and it removes an entire class of client-side security issue.
A React frontend is a later-stage option, not an MVP need.

---

## 4. Backend

FastAPI with Uvicorn, async where the work is I/O-bound.

```text
app/
  main.py              FastAPI app, router registration, startup checks   [DEV 2]
  config.py            Settings from environment, all named constants     [SHARED]
  db.py                Engine, session factory, statement timeout         [SHARED]
  models/              SQLAlchemy table definitions                       [SHARED]
    canonical.py         organization, team, app_user, repository, project
    identity.py          identity_link, unmatched_entity
    work.py              work_item, pull_request, commit, review,
                         work_item_link, work_item_dependency
    agent.py             insight, agent_run
    operations.py        sync_run, sync_cursor
  schemas/             Pydantic contracts, frozen in Phase 1              [SHARED]
    tools.py             tool input and output models
    insight.py           Claim, EvidenceItem, Insight, MemberInsight
    errors.py            ToolFailure and the typed error enum
  integrations/        WRITE PATH, makes network calls                    [DEV 1]
    http.py              shared httpx client, timeout, retry, logging
    github_client.py
    jira_client.py
    ingest.py            normalize payloads into canonical rows
    identity_resolver.py
    link_builder.py      builds work_item_link
    errors.py            typed integration failures
  sync.py              CLI entry point, writes sync_run and sync_cursor   [DEV 1]
  tools/               READ PATH, PostgreSQL only, no HTTP client         [DEV 2]
    get_team_members.py          T-001
    get_assigned_work_items.py   T-002
    get_pull_requests.py         T-003
    get_commits.py               T-004
    get_reviews.py               T-005
    get_work_item_links.py       T-006
    get_source_health.py         T-007
  agent/                                                                  [DEV 2]
    orchestrator.py      runs S1 to S6
    planner.py           S1
    evidence_builder.py  S3
    reasoning.py         S4, the single LLM call
    validation.py        S5, claim checks
    confidence.py        S5, confidence rules
    conflicts.py         S5, CF-1 to CF-4
    blockers.py          S5, BL-1 to BL-8
    risks.py             S5, RK-1 to RK-4
    cache.py             insight cache by evidence hash
    prompts/
      reasoning_v1.txt   versioned prompt, changed by pull request
  web/                                                                    [DEV 2]
    routes.py
    auth.py              login stub, can_view_member
    templates/
    static/
```

---

## 5. Agent layer

Summarized here; the full design is in `AGENT_ARCHITECTURE.md`.

6 stages, 1 of which uses the LLM:

```text
S1 Plan -> S2 Retrieve -> S3 Build Evidence -> S4 Reason -> S5 Validate -> S6 Respond
  code       code            code                LLM          code          code
```

Key constraints that the rest of the system depends on:

- Exactly 1 LLM call per run (DEC-005, NFR-030).
- The LLM call passes no tools (DEC-001).
- The LLM cites evidence by ID; code resolves and validates (DEC-004).
- Confidence, conflicts, blockers and risks are computed by code (DEC-005, DEC-006).
- An LLM failure degrades to the deterministic summary, never to an error page (FR-022).

---

## 6. Database

PostgreSQL 15, single source of truth. 17 tables, listed in `DATA_AND_EVIDENCE.md` 6.3.

| Concern | Approach |
|---|---|
| Schema changes | Alembic. 1 person creates a migration at a time. CI enforces a single head |
| Timestamps | `timestamptz`, UTC everywhere. Converted only at the integration boundary |
| Reserved words | The person table is `app_user`. `user` is reserved in PostgreSQL |
| Idempotency | Upsert on natural keys. `(repository_id, sha)`, `(integration, external_id)`, and so on |
| Statement timeout | 2 seconds for read-path sessions, higher for the sync path |
| Connection pooling | SQLAlchemy default pool. No pgBouncer at this scale |
| Indexes | Foreign keys, `(source, external_id)` on every external table, `work_item_link(work_item_id)`, `sync_run(source, started_at DESC)`, `insight(evidence_hash)` |
| Backups | `pg_dump` on a volume for the MVP. Managed backups when hosted |

**The database is the boundary between the 2 developers.** See DEC-015.

---

## 7. Integration layer

The write path. The only place in the codebase that calls an external API.

| Rule | Detail |
|---|---|
| Timeout | Connect and read timeout on every call. Default 10 seconds (NFR-001) |
| Retry | Maximum 3 attempts, exponential backoff via tenacity. No retry on 4xx except 429 (NFR-002) |
| Rate limits | Honour `Retry-After`. A rate-limit response produces `RATE_LIMITED`, never a crash and never an empty result |
| Logging | Every call logs what was attempted, what came back, and what was skipped. Never the token |
| Credentials | Read from environment variables at runtime. Read-only scope only (NFR-007) |
| Typed failures | `TIMEOUT`, `RATE_LIMITED`, `AUTH_FAILED`, `NOT_FOUND`, `UPSTREAM_ERROR`, `SCHEMA_INVALID` |
| Transport injection | The HTTP layer is injectable so fixtures can replace it for seeding and tests (DEC-012) |
| Idempotency | A re-run upserts. Running sync twice produces the same database state (NFR-003) |
| Partial failure | 1 bad record is skipped and counted, it does not fail the whole sync |

### Sync CLI

```bash
python -m app.sync --source github --team 1
python -m app.sync --source jira   --team 1
python -m app.sync --source jira   --team 1 --reset-cursor   # full refetch
```

Every run writes a `sync_run` row and updates `sync_cursor` on success.

Sync is triggered 3 ways, all through this same entry point (DEC-016):

| Trigger | Cadence | Notes |
|---|---|---|
| Scheduler | Every `SYNC_INTERVAL_MINUTES`, default 5 | Background task in the API container, or cron. Disableable by config |
| On-demand refresh | User action | `POST /api/teams/{id}/sync` starts it in the background and returns 202 immediately |
| Manual CLI | Any time | For backfill, debugging and `--reset-cursor` |

A sync already running for the same (source, scope) is never started twice.

---

## 8. Authentication

MVP: a login stub. A session cookie identifying an `app_user`. No password policy, no SSO,
no MFA.

This is acceptable **only** because the MVP runs locally for a demo with seeded data. It is
listed in `PROJECT_REQUIREMENTS.md` 2.8 as out of scope for the MVP and required before any
real deployment.

When real authentication arrives it MUST come from a managed identity provider with
OIDC. Do not build authentication.

---

## 9. Authorization

Every route returning member data calls 1 function:

```python
def can_view_member(actor: AppUser, subject: AppUser) -> bool:
    """MVP: same team only. Later: real RBAC."""
```

| Rule | Detail |
|---|---|
| Placement | Before stage S1. No tool is called until it passes |
| Failure | 403, and the denial is logged |
| Enforcement | A test asserts that no member-data route bypasses it (FR-028) |
| Scope | The MVP checks team membership only |

**Why a stub still needs the seam:** broken object-level authorization is the most common
API security failure. Retrofitting the check later means touching every route. Adding the
function now costs 10 minutes and makes the later change a 1-file edit.

---

## 10. External services

| Service | Direction | Called from | Credentials | Failure impact |
|---|---|---|---|---|
| GitHub REST API | Outbound | Sync CLI only | `GITHUB_TOKEN`, fine-grained read-only | Sync fails, `sync_run` records it, source becomes `unavailable`, answers degrade |
| Jira Cloud REST API | Outbound | Sync CLI only | `JIRA_EMAIL` + `JIRA_API_TOKEN` | Same |
| Ollama | **Local**, `http://localhost:11434` | Stage S4 only | None | 1 retry, then the deterministic fallback. The page still works |

No inbound webhooks in the MVP. Webhook ingestion is a Stage 2 change.

**Cost: zero.** Inference is local (DEC-017). The constraint is RAM, not money. On the
7.7 GB target machine, Ollama runs on the host with `llama3.2` (3B, about 2 GB) while
PostgreSQL runs in Docker. Measured generation speed is about 13 tokens/sec warm and about
4 tokens/sec when RAM is starved, so other applications MUST be closed before a demo
(NFR-031, NFR-034, NFR-035).

---

## 11. Deployment overview

Local Docker Compose. 2 containers.

```yaml
services:
  db:
    image: postgres:15
    environment:
      POSTGRES_USER: headref
      POSTGRES_PASSWORD: headref
      POSTGRES_DB: headref
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
  api:
    build: .
    env_file: .env
    depends_on: [db]
    ports: ["8000:8000"]
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
volumes:
  pgdata:
```

```bash
docker compose up --build
docker compose exec api alembic upgrade head
docker compose exec api python seed/seed_demo.py
```

No Redis, no queue, no vector database (DEC-013). A hosted deployment is optional, costs
money, and requires approval before it is set up.

---

## 12. Environment separation

| Environment | Purpose | Data | Credentials |
|---|---|---|---|
| **Local development** | Day-to-day work | Fixture-seeded demo data | Each developer's own read-only tokens in their own `.env` |
| **CI** | Lint, tests, migration check | Ephemeral PostgreSQL, fixtures only | No real credentials. The LLM is never called in CI |
| **Demo** | The graded demonstration | Fixture-seeded, identical on both machines | Same as local |
| **Production** | Not in scope for the MVP | N/A | N/A |

Rules:
- `.env` is git-ignored. `.env.example` is committed with empty values.
- CI MUST NOT hold a real GitHub or Jira credential. Inference is local, so there is no
  LLM credential to hold (DEC-017).
- Evaluation tests that need model output run locally, not in CI, so CI stays free and
  deterministic (`TESTING_AND_EVALUATION.md` 11.5).

---

## 13. Request flow

```text
1.  GET /members/7?question=blockers
2.  Session -> actor
3.  can_view_member(actor, subject=7)         403 if denied
4.  question_type validated against 3 values  422 if invalid
5.  S1 Plan       -> required [jira, github], window 14 days
6.  S2 Retrieve   -> T-001..T-007 over PostgreSQL
7.  Gate          -> required source unavailable? -> UNKNOWN, skip to 11
8.  S3 Evidence   -> correlate, dedupe, ev_1..ev_n
9.  Cache lookup  -> hash(subject, question, evidence). Hit -> skip to 11
10. S4a Findings  -> claims, blockers, risks, conflicts, confidence (code)
11. S4b Narrative -> 1 Ollama call on localhost, no tools, prose only
12. S5 Validate   -> narrative grounded and safe, else deterministic fallback
12. S6 Respond    -> MemberInsight, persist agent_run, write cache
13. Render member.html with the evidence drawer
```

Expected timing: steps 1 to 9 under 500 ms on seeded data. Step 10 is 2 to 10 seconds.
A cache hit skips step 10 entirely (NFR-012, NFR-013).

---

## 14. Data flow

See `DATA_AND_EVIDENCE.md` 6.2 for the full diagram. In short:

```text
GitHub API + Jira API
   -> integration clients (timeout, retry, typed failures)
   -> normalize to canonical entities
   -> identity resolution (external account -> app_user, or unmatched_entity)
   -> link_builder (work_item_link)
   -> PostgreSQL
   -> read tools (T-001..T-007)
   -> evidence builder (ev_1..ev_n)
   -> reasoning (1 LLM call)
   -> validation and confidence (code)
   -> MemberInsight
```

---

## 15. Important architectural constraints

These are enforced constraints, not guidelines. Each traces to a decision record.

| ID | Constraint | Source |
|---|---|---|
| **AC-1** | Agent-facing tools MUST NOT make external network calls. No HTTP client may be imported under `app/tools/` | DEC-002 |
| **AC-2** | The web request path MUST NOT **block on** ingestion. Starting a background sync is allowed; waiting for one is not | DEC-003, DEC-016, NFR-015 |
| **AC-3** | The LLM MUST be given no tools, in any call | DEC-001 |
| **AC-4** | Exactly 1 LLM call per agent run | DEC-005 |
| **AC-5** | The LLM MUST cite evidence by ID. Code resolves; unknown IDs are dropped | DEC-004 |
| **AC-6** | Confidence, conflicts, blockers and risks MUST be computed by code | DEC-005, DEC-006 |
| **AC-7** | Every external record MUST carry `source_updated_at` and `retrieved_at` | FR-007 |
| **AC-8** | `get_source_health` MUST be called on every agent run | DEC-010 |
| **AC-9** | An unavailable required source MUST produce UNKNOWN, never a negative conclusion | FR-023 |
| **AC-10** | Every network call MUST have a timeout and a retry limit | NFR-001, NFR-002 |
| **AC-11** | Secrets MUST come from environment variables only | NFR-006 |
| **AC-12** | Ingestion MUST be idempotent | NFR-003 |
| **AC-13** | Attribution MUST require a confirmed identity link. Display-name matching MUST NOT exist as a code path | DEC-008 |
| **AC-14** | `work_item_link` MUST be written during ingestion, never derived at request time | DEC-009 |
| **AC-15** | Untrusted text MUST be escaped before rendering. No `|safe` on source-derived fields | FR-030 |
| **AC-16** | The Alembic history MUST have exactly 1 head | NFR-029 |
| **AC-17** | No write endpoint of any external API may appear in the codebase | DEC-001, NFR-007 |

**If a task appears to require breaking one of these, stop.** Raise a Decision Required
entry in `WORKLOG.md` and add a new record to `DECISIONS.md`. Do not work around it.

---

## 16. The write path / read path boundary

This is the most important boundary in the system and the one most likely to be eroded by
a convenient shortcut.

```text
  WRITE PATH                              READ PATH
  ==========                              =========
  app/integrations/                       app/tools/
  app/sync.py                             app/agent/
  seed/                                   app/web/

  MAY make network calls                  MUST NOT make network calls
  Triggered by CLI or cron                Triggered by an HTTP request
  Writes canonical tables                 Reads canonical tables
  Writes sync_run, sync_cursor            Reads sync_run via T-007
  Writes work_item_link                   Reads work_item_link via T-006
  Resolves identity                       Consumes resolved identity
  Owned by Developer 1                    Owned by Developer 2

  ------------------ PostgreSQL schema ------------------
                    the only handoff
```

### Why the boundary exists

1. **Speed.** A request that never waits on GitHub answers in milliseconds.
2. **Reproducibility.** Evaluation runs against fixed database state, so a failure means a
   real behaviour change (DEC-011).
3. **Demo safety.** A slow or rate-limited external API cannot break a live demonstration.
4. **Testability.** Read-path tests need no HTTP mocking at all.
5. **Parallel work.** Each developer owns 1 side and neither waits on the other.

### The cost, stated honestly

Data is only as fresh as the last sync. That cost is not hidden: it is measured by T-007
and displayed on every page showing member data (NFR-020). The alternative, a fast-moving
request path coupled to 2 external APIs, was rejected in DEC-002.

### How the boundary is enforced

- A test asserts that no module under `app/tools/` imports an HTTP client.
- Code review: a pull request that adds a network call to the read path is rejected.
- No web route imports anything from `app/integrations/`.

---

## 17. Developer boundary

Per DEC-015, the shared technical boundary is the database schema.

| Area | Developer 1 (Keshan) | Developer 2 (Isiwara) |
|---|---|---|
| `app/integrations/` | Owns | Does not edit |
| `app/sync.py` | Owns | Does not edit |
| `seed/` and fixtures | Owns, records fixtures | Consumes fixtures |
| `app/tools/` | Does not edit | Owns |
| `app/agent/` | Does not edit | Owns |
| `app/web/` | Does not edit | Owns |
| `app/models/` | Shared, coordinate | Shared, coordinate |
| `app/schemas/` | Shared, frozen in Phase 1 | Shared, frozen in Phase 1 |
| `app/config.py`, `app/db.py` | Shared, coordinate | Shared, coordinate |
| `migrations/` | Shared, 1 at a time, announced | Shared, 1 at a time, announced |
| CI, Docker | Shared | Shared |

**Working rules:**
- Changes to a `[SHARED]` file need a message to the other developer first, plus a
  `WORKLOG.md` note.
- Only 1 person creates an Alembic migration at a time. Announce it before starting.
- Developer 2 builds against fixture-seeded data from Phase 1 onward and never waits for
  Developer 1's integrations to be finished.
- Developer 2's pull requests require Developer 1's approval. Developer 1 may merge their
  own work.
