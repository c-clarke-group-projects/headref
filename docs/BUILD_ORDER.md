# headref Build Order

A layer by layer guide for building headref by hand, in the order the layers depend on
each other, with what to learn before writing each one.

**Who this is for:** a developer who is building this project to learn the stack, not
only to ship it. It answers the question "what are the sections of this application, and
which one do I write first".

**How this differs from the other planning files:**

| File | Answers |
|---|---|
| `TASKS.md` | What are the 49 units of work, who owns each, what are the acceptance criteria |
| `BUILD_ORDER.md` (this file) | What are the architectural layers, what does each do, in what order do I write them, what do I need to understand first |
| `ARCHITECTURE.md` | What is the finished shape of the system |

This file adds no new scope. Every section below maps to tasks that already exist in
`TASKS.md`. If the 2 files disagree on ordering, `TASKS.md` wins, because it also carries
the 2-developer parallelism and the 3-week triage.

---

## 1. The vocabulary problem

Terms like "controller" and "service" come from Spring and ASP.NET MVC. Python and
FastAPI use different names for the same jobs. This table is the translation.

| Term you know | headref name | Location | What it actually does |
|---|---|---|---|
| Entity, Model | Model | `app/models/` | SQLAlchemy classes that map to PostgreSQL tables |
| DTO, ViewModel | Schema, Contract | `app/schemas/` | Pydantic classes that define what crosses a boundary |
| Repository, DAO | Tool | `app/tools/` | Reads PostgreSQL, returns Pydantic objects, no business rules |
| Service, business logic | Findings engine, ingestion | `app/agent/` rules, `app/integrations/ingest.py` | Decides what is true. The actual product |
| Controller | Route | `app/web/routes.py` | Maps an HTTP request to a service call and a response |
| Middleware, filter | Dependency | FastAPI `Depends()` | Auth, session, request context |
| View | Template | `app/web/templates/` | Jinja2 HTML |
| Config | Settings | `app/config.py` | Environment variables turned into typed values |
| Scheduled job | Sync CLI | `app/sync.py` | The ingestion entry point |

2 extra layers exist here that a standard CRUD application does not have:

- **The integration layer** (`app/integrations/`), the only code allowed to make an
  external network call.
- **The agent layer** (`app/agent/`), which turns database rows into an evidence-backed
  answer, with exactly 1 LLM call in it.

---

## 2. The sections, in dependency order

| # | Section | Folder | Tasks | Cannot start until |
|---|---|---|---|---|
| 0 | Environment and skeleton | repo root | P0-001, P0-003, P0-005 | Nothing |
| 1 | Configuration | `app/config.py` | P1-003 | 0 |
| 2 | Database layer | `app/models/`, `app/db.py`, `migrations/` | P1-001, P1-004 | 1 |
| 3 | Contracts | `app/schemas/` | P1-002 | 2 |
| 4 | Identity map and fixtures | `seed/` | P0-002, P1-005 | 3 |
| 5 | HTTP client and API clients | `app/integrations/http.py`, `*_client.py` | P2-001, P2-002, P2-004 | 3 |
| 6 | Ingestion and normalization | `app/integrations/ingest.py` | P2-003, P2-005 | 5 |
| 7 | Identity resolution and linking | `identity_resolver.py`, `link_builder.py` | P2-006, P2-007 | 6 |
| 8 | Sync CLI and run state | `app/sync.py` | P2-008, P2-009, P2-010 | 7 |
| 9 | Read tools | `app/tools/` | P3-002, P3-003 | 3, real rows from 8 |
| 10 | Findings engine | `app/agent/` rule modules | P4-002, P4-003, P4-004, P4-008 | 9 |
| 11 | Agent orchestration | `app/agent/` | P3-004, P4-001, P4-005, P4-006, P4-007 | 10 |
| 12 | Authentication | `app/web/auth.py` | P3-001 | 2 |
| 13 | Authorization | `app/web/auth.py` | P3-001 | 12 |
| 14 | API layer | `app/main.py`, `app/web/routes.py` | P5-001, P5-007 | 11, 13 |
| 15 | Web UI | `app/web/templates/`, `static/` | P5-002 to P5-006 | 14 |
| 16 | Cross cutting | `tests/`, CI, logging | P0-004, P6-001 to P6-005, P7-001 | Runs alongside everything |

Sections 5 to 8 are the **write path**. Sections 9 to 11 are the **read path**. They meet
only at PostgreSQL. That boundary is the most important rule in the codebase and it is
why 2 people can work at the same time without colliding.

---

## Section 0. Environment and skeleton

**What it is.** The repository, the Python project definition, Docker Compose, and a
working local Ollama. No application logic yet.

**Why first.** Every later section needs a place to run.

**Files you write by hand**

```text
pyproject.toml          dependencies, ruff and black settings
Dockerfile              Python 3.11 image for the api container
docker-compose.yml      2 services: api, postgres
.env.example            every variable name, no real values
.gitignore              .env must be in it
app/__init__.py
```

**Concepts to learn first**

1. Virtual environments, and why `pip install` goes into 1 per project.
2. What `pyproject.toml` replaced (`setup.py`, `requirements.txt`).
3. Docker image versus container versus volume.
4. Why PostgreSQL runs in Docker but Ollama runs on the host here (RAM, DEC-017).
5. Environment variables, and why a secret never goes in a file that git tracks.

**Build in this order**

1. `pyproject.toml` with fastapi, uvicorn, sqlalchemy, alembic, pydantic,
   pydantic-settings, httpx, tenacity, jinja2, psycopg, pytest, respx, ruff, black.
2. `Dockerfile`.
3. `docker-compose.yml` with a named volume for PostgreSQL data.
4. `.env.example`, then your own `.env`, then confirm git ignores it.
5. `scripts/check_ollama.py` already exists. Run it and record your numbers in
   `WORKLOG.md`.

**Done when**

- `docker compose up --build` starts both containers and neither restarts in a loop.
- `docker compose exec api python -c "print('ok')"` prints ok.
- `git status` never shows `.env`.

**Traps**

- Committing `.env`. Check before your first commit, not after.
- Hard-coding `localhost` for PostgreSQL inside the api container. The hostname is the
  compose service name.
- Reaching Ollama from inside a container needs `host.docker.internal:11434`, not
  `localhost`. This is the 1 outstanding item on P0-005.

---

## Section 1. Configuration

**What it is.** 1 module that reads environment variables, validates them, and exposes
them as typed values. Every constant in the system lives here, not scattered in the code.

**Why here.** The database layer needs a connection URL before it can do anything.

**Files**

```text
app/config.py
```

**Concepts to learn first**

1. Pydantic `BaseSettings`, how it reads environment variables and casts types.
2. Fail fast: a missing required variable should crash at startup, not at first use.
3. Why a constant like `SYNC_INTERVAL_MINUTES` is named in config and not typed as `5`
   in 3 places.

**What goes in it**

Database URL, GitHub token, Jira email and token, Jira base URL, Ollama URL and model
name, HTTP timeout, retry limit, sync interval, freshness thresholds, statement timeout,
evidence window in days, log level.

**Done when**

- Importing `app.config` with a missing required variable raises a clear error naming the
  variable.
- No secret is ever printed in `__repr__` or a log line.
- Every number used later in the project can be traced back to this file.

**Traps**

- Reading `os.environ` directly somewhere else later. If you do that once, the config
  module stops being the single source and the value drifts.
- Logging the settings object at startup. It contains tokens.

---

## Section 2. Database layer

**What it is.** The 17 SQLAlchemy models, the engine and session factory, and the first
Alembic migration.

**Why here.** Ingestion writes canonical rows, so the shape of those rows has to exist
first. This is why `TASKS.md` swapped the original phase order.

**Files**

```text
app/db.py                  engine, SessionLocal, get_session dependency, statement timeout
app/models/__init__.py
app/models/canonical.py    organization, team, app_user, repository, project
app/models/identity.py     identity_link, unmatched_entity
app/models/work.py         work_item, work_item_dependency, pull_request, commit, review,
                           work_item_link
app/models/agent.py        insight, agent_run
app/models/operations.py   sync_run, sync_cursor
alembic.ini
migrations/versions/0001_initial.py
```

**Concepts to learn first**

1. ORM basics: a class is a table, an attribute is a column, an instance is a row.
2. SQLAlchemy 2.0 declarative style with `Mapped` and `mapped_column`. Do not follow 1.x
   tutorials, the syntax changed.
3. Session, transaction, commit, rollback. What a session actually holds.
4. Foreign keys and relationships, and lazy loading versus the N+1 problem.
5. Unique constraints as the mechanism for idempotency. `(repository_id, sha)` is what
   makes running sync twice safe.
6. Alembic: autogenerate, review, upgrade, downgrade, and what a migration head is.
7. `timestamptz`, and why every timestamp in this system is UTC.

**Build in this order**

1. `app/db.py` first, with the engine and a `get_session` generator.
2. `canonical.py`, the 5 tables with no external data.
3. `identity.py`.
4. `work.py`, the largest file.
5. `agent.py` and `operations.py`.
6. `alembic init`, point `env.py` at your metadata, autogenerate, then **read the
   generated migration line by line** before applying it.
7. The indexes listed in `ARCHITECTURE.md` section 6.

**Done when**

- `alembic upgrade head` creates 17 tables on an empty database.
- `alembic downgrade base` then `upgrade head` works twice in a row.
- Every table holding external data has `source_updated_at`, `retrieved_at` and an
  external ID column.

**Traps**

- Naming the person table `user`. It is a reserved word in PostgreSQL. It is `app_user`.
- 2 people generating a migration at the same time. Announce it first. CI enforces 1 head.
- Trusting autogenerate. It misses index and constraint changes regularly.

**Read first:** `DATA_AND_EVIDENCE.md` sections 6.3 and 6.4.

---

## Section 3. Contracts

**What it is.** The Pydantic models that define what crosses a boundary: tool inputs and
outputs, the insight and evidence shapes, and typed errors.

**Why here, and why it matters more than it looks.** This is the handoff between the 2
developers. Once these are frozen, Developer 1 can build ingestion and Developer 2 can
build the agent against the same agreed shapes without waiting for each other.

**Files**

```text
app/schemas/__init__.py
app/schemas/tools.py      input and output model per tool T-001 to T-007
app/schemas/insight.py    Claim, EvidenceItem, Insight, MemberInsight
app/schemas/errors.py     ToolFailure, the error type enum
```

**Concepts to learn first**

1. The difference between a SQLAlchemy model and a Pydantic schema, and why you need
   both. The model is storage. The schema is a contract.
2. Pydantic v2 validation, `model_validate`, `model_config`, `from_attributes`.
3. Enums for closed sets: question type, confidence level, error type, claim class.
4. Why a function that can fail returns a typed failure object instead of `None` or an
   empty list. An empty list means "nothing found". A failure means "I could not look".
   Confusing those 2 is how a system starts lying.

**Done when**

- Every tool in `AGENT_TOOLS.md` has an input model and an output model here.
- `ToolFailure` covers all 6 error types: `TIMEOUT`, `RATE_LIMITED`, `AUTH_FAILED`,
  `NOT_FOUND`, `UPSTREAM_ERROR`, `SCHEMA_INVALID`.
- Both developers have reviewed the file and agreed it is frozen.

**Traps**

- Changing a schema quietly after the freeze. It breaks the other developer's code
  silently. Change it by pull request with both names on it.
- Letting raw upstream JSON leak through as a `dict`. Every boundary returns a model.

**Read first:** `AGENT_TOOLS.md` in full.

---

## Section 4. Identity map and fixtures

**What it is.** A YAML file that maps a GitHub login and a Jira account to 1 internal
person, a loader for it, and recorded API payloads captured from real Jira and GitHub.

**Why here.** The fixtures are the single most valuable artifact in the project. They make
the demo work offline, they make tests deterministic, and they make both machines behave
identically. `TASKS.md` says to protect P0-002 above everything else, and that is correct.

**Files**

```text
seed/identity_map.yml
seed/fixtures/github/*.json
seed/fixtures/jira/*.json
seed/seed_demo.py
app/integrations/identity_loader.py
```

**Concepts to learn first**

1. What a fixture is, and why a recorded payload beats a hand-written one. Real APIs
   return nulls, missing keys and shapes you did not predict.
2. Redaction. Capture real structure, remove real email addresses and tokens before
   committing.
3. YAML parsing, and validating the loaded structure with Pydantic rather than trusting
   it.

**Done when**

- Fixtures exist for at least 1 realistic scenario per evaluation case.
- No token, no personal email address and no customer data is in any committed fixture.
- `identity_map.yml` loads and validates, and a bad entry produces a readable error.

**Traps**

- Capturing fixtures late. Everything downstream waits on them.
- Hand-writing fixtures to match your code. Then your tests only prove your code agrees
  with itself.

---

## Section 5. HTTP client and API clients

**What it is.** The write path starts here. 1 shared httpx wrapper with timeout, retry and
logging, and on top of it a GitHub client and a Jira client that only read.

**Why here.** Everything it needs exists, and it is the first code that touches a network.

**Files**

```text
app/integrations/http.py           the shared client
app/integrations/errors.py         typed integration failures
app/integrations/github_client.py
app/integrations/jira_client.py
```

**Concepts to learn first**

1. `httpx`, sync versus async, and connection reuse.
2. Timeouts: connect timeout and read timeout are different things, set both.
3. `tenacity` retry with exponential backoff, and which status codes are worth retrying.
   4xx is not, except 429.
4. Rate limiting, the `Retry-After` header, and why honouring it is not optional.
5. Pagination. Both APIs page, and a client that reads only page 1 is quietly wrong.
6. Dependency injection of the transport, so a fixture can replace the network in tests.
7. Authentication headers: GitHub bearer token, Jira basic auth with email plus token.

**Build in this order**

1. `http.py` with timeout, retry limit, and a log line for every attempt recording what
   was tried, what came back, and what was skipped.
2. `errors.py`, mapping a status code or an exception to 1 of the 6 typed failures.
3. `github_client.py`, 1 method per endpoint, returning parsed models.
4. `jira_client.py`, the same shape.

**Done when**

- Every network call has a timeout and a maximum of 3 attempts.
- A 401 produces `AUTH_FAILED`, not a crash, and does not log the token.
- A 429 produces `RATE_LIMITED` and respects `Retry-After`.
- Tests with `respx` cover timeout, 429, 500 and a malformed body, with no real network.

**Traps**

- Logging the full request headers. That prints your token.
- Retrying a 404 forever.
- Letting a timeout surface as an empty result. An empty result means "nothing there",
  which is a different and much more damaging claim.

---

## Section 6. Ingestion and normalization

**What it is.** The layer that turns an upstream JSON payload into canonical rows. This is
the real service layer of the write path.

**Why here.** It needs both the clients above it and the models below it.

**Files**

```text
app/integrations/ingest.py
```

**Concepts to learn first**

1. Normalization: many upstream statuses collapse into 1 small canonical set, and you
   keep `raw_status` so nothing is lost.
2. Upsert. In PostgreSQL, `INSERT ... ON CONFLICT DO UPDATE`. This is what makes a re-run
   idempotent.
3. Partial failure handling. 1 malformed record is skipped and counted, it does not abort
   the whole sync.
4. Excerpting and capping text at the boundary, and stripping URLs, before it ever reaches
   the database.
5. Timezone conversion at the boundary, so everything stored is UTC.

**Done when**

- Running ingestion twice over the same fixture produces the same database state.
- A record with a missing field is skipped, counted and logged, and the run continues.
- No raw upstream JSON is stored except where a table explicitly holds it.

**Traps**

- Delete then insert instead of upsert. You lose IDs that other tables point at.
- Trusting upstream text. It is untrusted data and it is never an instruction. See
  `AI_BEHAVIOR.md` 5.6.

---

## Section 7. Identity resolution and link building

**What it is.** 2 pieces of logic that together are the product's core correctness risk.
Resolution maps an external account to an internal person. Link building connects a Jira
issue to the branches, pull requests and commits that implement it.

**Why here.** Both need normalized rows to work on.

**Files**

```text
app/integrations/identity_resolver.py
app/integrations/link_builder.py
```

**Concepts to learn first**

1. Deterministic matching first: the identity map file is authoritative, inference is a
   fallback, and every link records which method produced it.
2. The unmatched queue. An account you cannot map is recorded, not guessed and not
   dropped.
3. Pattern extraction: an issue key like `AUTH-245` appears in a branch name, a pull
   request title and a commit message, in that order of reliability.
4. Recording confidence and `link_method` per link, because a later reviewer needs to know
   why the system believes a link exists.

**Done when**

- An unmappable account creates an `unmatched_entity` row with an occurrence count, and
  never a wrong mapping.
- A link built from a branch name and a link built from a commit message are
  distinguishable after the fact.
- The system never claims a link it cannot show a reason for.

**Traps**

- Fuzzy name matching that looks clever and silently assigns 1 person's work to another.
  A missing link is honest. A wrong link destroys trust in the whole product.

---

## Section 8. Sync CLI and run state

**What it is.** The entry point that runs ingestion, records every attempt in `sync_run`,
and stores a resume position in `sync_cursor`.

**Files**

```text
app/sync.py
seed/seed_demo.py         loads fixtures through this same ingestion path
```

**Concepts to learn first**

1. `python -m package.module` and `argparse`.
2. Cursor based incremental sync, and why `--reset-cursor` has to exist.
3. Run state as data. If a sync fails at 3am, the row is the only thing that tells you.
4. Locking: the same (source, scope) must never sync twice at once.

**Done when**

- `python -m app.sync --source github --team 1` writes a `sync_run` row with counts.
- A failed run records `error_type` and leaves the cursor unchanged.
- `seed_demo.py` loads fixtures through the real ingestion code with no network call.

---

## Section 9. Read tools

**What it is.** The repository layer. 7 functions, each reading PostgreSQL and returning
Pydantic models. The read path begins here.

**The hard rule.** A tool never makes an external API call. It reads the database only.
Everything it sees was fetched earlier by the write path and stamped with a time.

**Files**

```text
app/tools/get_team_members.py         T-001
app/tools/get_assigned_work_items.py  T-002
app/tools/get_pull_requests.py        T-003
app/tools/get_commits.py              T-004
app/tools/get_reviews.py              T-005
app/tools/get_work_item_links.py      T-006
app/tools/get_source_health.py        T-007
```

**Concepts to learn first**

1. Query construction with SQLAlchemy `select()`, joins, and filtering by a time window.
2. Why each tool takes a typed input model instead of loose arguments.
3. Statement timeouts on the read path, so a slow query fails fast instead of hanging a
   page.
4. Source health as a first-class concept: a tool has to be able to say "Jira data is 3
   hours old" rather than implying it is current.

**Done when**

- Every tool returns the Pydantic model from `app/schemas/tools.py`, never a raw row.
- No file under `app/tools/` imports httpx or any client module. Add a test that asserts
  this.
- Each tool runs inside the read-path statement timeout on seeded data.

---

## Section 10. Findings engine

**What it is.** The domain logic. Given evidence, code decides what is a blocker, what is
a risk, what conflicts, how confident the system is, and what the member is working on.

**Why this is the product.** Under DEC-018 the LLM does not produce findings. It writes 2
sentences over findings that this code produced. If this section is weak, no model output
can rescue it.

**Files**

```text
app/agent/conflicts.py    CF-1 to CF-4
app/agent/blockers.py     BL-1 to BL-8
app/agent/risks.py        RK-1 to RK-4
app/agent/confidence.py   HIGH, MEDIUM, LOW, UNKNOWN
app/agent/summary.py      the deterministic member summary
```

**Concepts to learn first**

1. Pure functions. A rule takes evidence in and returns findings out, with no database
   access and no side effects. This is what makes them testable.
2. Rule identifiers. Every rule has an ID so a finding can name the rule that produced it.
3. The 3 claim classes: fact, inference, unknown, and the discipline of using unknown.
4. Confidence as a deterministic function of evidence, never a number the model chose.

**Done when**

- Every rule has a unit test with a case that fires and a case that does not.
- Confidence is computed only here, and no other module assigns it.
- The deterministic summary alone, with no LLM at all, already answers the 3 questions.
  Prove this before Section 11 starts.

**Traps**

- Adding a rule that reads the database. Keep them pure.
- Inventing a finding the evidence does not support. Never invent a figure, a source or a
  quote.

---

## Section 11. Agent orchestration

**What it is.** The 6-stage pipeline: plan, retrieve, build evidence, reason, validate,
respond. Exactly 1 of those stages calls the LLM.

**Files**

```text
app/agent/planner.py            S1
app/agent/orchestrator.py       runs S1 to S6
app/agent/evidence_builder.py   S3, stable evidence IDs
app/agent/reasoning.py          S4b, the single Ollama call
app/agent/validation.py         S5
app/agent/cache.py              keyed by evidence hash
app/agent/prompts/reasoning_v1.txt
```

**Concepts to learn first**

1. Pipeline orchestration: stages with explicit inputs and outputs, so any stage can be
   tested alone.
2. Stable IDs. The same evidence produces the same ID on every run, which is what makes
   citation and caching work.
3. Calling a local model over HTTP, with `temperature: 0` for reproducibility, a timeout,
   and 1 retry.
4. Prompt versioning as a file changed by pull request, not a string in code.
5. Output validation. Whatever comes back is checked against the evidence, and anything
   unsupported is dropped and recorded in `dropped_claims`.
6. Graceful degradation. If Ollama is down, the answer falls back to the deterministic
   summary. The page still works. It never shows an error page.
7. Caching by content hash, so identical evidence does not pay for a second generation.

**Done when**

- 1 run produces exactly 1 LLM call, asserted by a test.
- The LLM is passed no tools and no write capability.
- Killing Ollama mid-demo degrades the answer instead of breaking the page.
- `agent_run` records the prompt version, raw output, validated output and dropped claims.

**Read first:** `AGENT_ARCHITECTURE.md` and `AI_BEHAVIOR.md`, in full, before writing any
of this.

---

## Section 12. Authentication

**What it is.** Establishing who is making the request. In the MVP this is a login stub: a
session cookie that identifies an `app_user`.

**Why it is section 12 and not section 1.** This is the answer to the natural instinct to
build login first. Real authentication is explicitly out of scope for the MVP, and when it
arrives it must come from a managed OIDC provider rather than being hand-written. Password
hashing, session rotation, MFA and account recovery are a large surface with a large
failure cost, and none of it teaches you anything about this product. The stub takes about
30 minutes.

If you want to learn authentication properly, do it as a separate exercise after the demo,
not inside the deadline.

**Files**

```text
app/web/auth.py     the session stub and the current-actor dependency
```

**Concepts to learn first**

1. What a session cookie is, and the `HttpOnly`, `Secure` and `SameSite` flags.
2. FastAPI `Depends()`, and why the current actor arrives as a dependency rather than
   being looked up inside each route.
3. The difference between authentication (who you are) and authorization (what you may
   see). They are section 12 and section 13 for a reason.

**Done when**

- Every request that needs an actor gets it from 1 dependency function.
- The stub is clearly marked as a stub in the code and in the README.

---

## Section 13. Authorization

**What it is.** 1 function, called before any member data is returned.

```python
def can_view_member(actor: AppUser, subject: AppUser) -> bool:
    """MVP: same team only. Later: real RBAC."""
```

**Why it gets its own section despite being 3 lines.** Broken object-level authorization
is the most common serious API security failure. Any route that returns data about person
B to person A without a check is the bug. Building the seam now means the later change is
1 file. Retrofitting it means touching every route and missing 1.

**Concepts to learn first**

1. Object-level authorization versus role-level authorization. Checking "is a team lead"
   is not the same as checking "may view this specific person".
2. Where the check goes: before stage S1, before any tool is called.
3. Denial behaviour: return 403, log it, and do not leak whether the subject exists.

**Done when**

- No member-data route reaches a tool before the check passes.
- A test enumerates the routes and asserts none bypasses it. This is FR-028.

---

## Section 14. API layer

**What it is.** The controllers. Each route maps an HTTP request to a service call and a
response model, and does nothing else.

**Files**

```text
app/main.py           app creation, router registration, startup checks
app/web/routes.py     the routes
```

**Concepts to learn first**

1. FastAPI routing, path and query parameters, and response models.
2. Thin controllers. If a route contains a business rule, that rule is in the wrong place.
3. Status codes that mean something: 200, 202 for the accepted background sync, 403, 404,
   422, and 503 for a degraded source.
4. Exception handlers, so an unhandled error becomes a clean response and a log line, not
   a stack trace in the browser.
5. Startup checks: fail loudly at boot if the database or the model is unreachable.

**Routes in the MVP**

| Method | Path | Purpose |
|---|---|---|
| GET | `/teams/{id}` | Team overview page (removed by DEC-026, now `/w/{workspace_id}`) |
| GET | `/members/{id}` | Member profile page |
| GET | `/api/members/{id}/insight` | Member insight JSON |
| POST | `/api/teams/{id}/sync` | Start a sync, return 202 (removed by DEC-026, now `POST /w/{workspace_id}/sync`) |
| GET | `/admin/unmatched` | The unmatched identity queue |

**Done when**

- Every route that returns member data calls `can_view_member` first.
- No route builds a SQL query or applies a business rule inline.

---

## Section 15. Web UI

**What it is.** Server-rendered Jinja2 templates with a small amount of vanilla
JavaScript. No framework, no build step.

**Files**

```text
app/web/templates/base.html
app/web/templates/team.html
app/web/templates/member.html
app/web/templates/partials/evidence_drawer.html
app/web/static/app.js
app/web/static/style.css
```

**Concepts to learn first**

1. Jinja2: template inheritance, blocks, loops, filters.
2. Autoescaping, and why `|safe` is never applied to any field that came from GitHub or
   Jira. That is how untrusted upstream text becomes an XSS hole.
3. Progressive enhancement. The page works with JavaScript doing only 3 jobs: expand the
   evidence drawer, switch question type, poll a running sync.
4. Showing freshness and degraded state honestly. If Jira data is 3 hours old, the page
   says so rather than implying it is live.

**Done when**

- Every claim on screen can be expanded to the evidence that produced it, and every
  evidence item links back to its Jira or GitHub page.
- A source being unavailable is visible on the page, not hidden.
- No template applies `|safe` to upstream text.

---

## Section 16. Cross cutting

These are not a phase at the end. They run alongside every section above.

| Concern | What to do | Where |
|---|---|---|
| Logging | Structured logs recording what was attempted, what came back, what was skipped. Never a token | Every section |
| Errors | 6 typed failures, never a bare exception crossing a boundary | Sections 5, 9 |
| Tests | At least 1 unit test per task, per the Definition of Done | Every section |
| Fixtures and respx | No test makes a real network call | Sections 5 to 8 |
| Evaluation | 12 scenarios asserting agent behaviour | Sections 10, 11 |
| CI | Lint, tests, secret scan, single migration head | Section 0 onward |
| Security pass | Tokens, authorization coverage, template escaping, prompt injection | Before the demo |

---

## 3. The dependency chain in 1 picture

```text
Section 0  skeleton
    |
Section 1  config
    |
Section 2  models + db + migrations
    |
Section 3  schemas  <---- the freeze point, both developers agree here
    |
    +-------------------------------+
    |                               |
  WRITE PATH                      READ PATH
    |                               |
Section 4  fixtures               (waits for rows from Section 8)
Section 5  http + clients           |
Section 6  ingest                   |
Section 7  identity + links         |
Section 8  sync CLI --------> PostgreSQL <-------- Section 9  read tools
                                                        |
                                               Section 10  findings engine
                                                        |
                                               Section 11  agent pipeline
                                                        |
                              Section 12 auth --> Section 13 authz
                                                        |
                                               Section 14  routes
                                                        |
                                               Section 15  templates
```

---

## 4. Building by hand without missing the deadline

There are 15 working days and 49 tasks. Writing every line by hand is not compatible with
that, so decide per section which of 3 modes you are in.

| Mode | Meaning | Use it for |
|---|---|---|
| By hand | You type it, you look things up, you accept being slow | Sections 2, 3, 7, 9, 10, 11, 13 |
| With reference | You write it with documentation or an example open beside you | Sections 1, 5, 6, 8, 14 |
| Generated and reviewed | Boilerplate you read line by line before committing | Sections 0, 15, repetitive templates and test scaffolding |

The sections worth your hand-written time are the ones that carry the product: the schema,
the contracts, identity and linking, the read tools, the findings engine, the agent
pipeline, and the authorization check. Docker files and HTML scaffolding teach you much
less per hour.

**A rule that protects the learning either way:** you may not commit a line you cannot
explain. If a generated block does something you do not understand, either understand it
or replace it.

---

## 5. The vertical slice checkpoint

After Section 3, before going deep into the write path, build 1 thin slice through every
layer with fake data. It takes about half a day and it prevents the most common failure in
a layered build, which is discovering at Section 14 that the layers do not fit.

The slice:

1. 1 route, `GET /members/{id}`.
2. It calls `can_view_member`, which returns True.
3. It calls 1 read tool, which returns 1 `app_user` row you inserted by hand.
4. It returns a Pydantic response model.
5. It renders a template with 1 line on it.
6. 1 test hits the route and asserts the response.

No LLM, no ingestion, no evidence. When that runs end to end, you have seen how all 7
layers connect, and every later section is filling in a box you have already touched.

---

## 6. What to learn, per stack item

Learn each of these when the section that needs it arrives, not before.

| Stack item | Needed at | The 20 percent that matters |
|---|---|---|
| Python typing | Everywhere | Type hints, `Optional`, `list[X]`, protocols |
| Pydantic v2 | Sections 1, 3 | BaseModel, BaseSettings, validators, enums |
| SQLAlchemy 2.0 | Section 2 | Declarative models, session, select, joins, upsert |
| Alembic | Section 2 | Autogenerate, review, upgrade, downgrade, 1 head |
| PostgreSQL | Section 2 | Constraints, indexes, `ON CONFLICT`, `timestamptz` |
| httpx | Section 5 | Timeouts, headers, pagination, injectable transport |
| tenacity | Section 5 | Retry, backoff, which errors to retry |
| FastAPI | Sections 12 to 14 | Routing, `Depends()`, response models, exception handlers |
| Jinja2 | Section 15 | Inheritance, loops, autoescaping |
| pytest | Every section | Fixtures, parametrize, assertions |
| respx | Sections 5 to 8 | Mocking httpx without touching a network |
| Docker Compose | Section 0 | Services, volumes, networks, exec |
| Ollama | Section 11 | Local HTTP API, temperature 0, timeouts |

---

## 7. Glossary

| Term | Meaning in this project |
|---|---|
| Canonical model | The internal shape data takes after normalization, independent of GitHub or Jira |
| Write path | Code that fetches from external APIs and writes rows. The only place with network calls |
| Read path | Code that answers a request. Reads PostgreSQL only, never the network |
| Tool | A read-path function the agent may call. A repository method with a typed contract |
| Evidence item | 1 source-backed record with an ID, cited by a claim |
| Claim | 1 statement, classed as fact, inference or unknown |
| Finding | A blocker, risk or conflict, produced by a rule in code, never by the model |
| Confidence | HIGH, MEDIUM, LOW or UNKNOWN, assigned by code from the evidence |
| Freshness | How old the underlying data is, shown to the user rather than hidden |
| Idempotent | Running it twice leaves the same state as running it once |
| Degraded | A source is unavailable, the system says so and still answers what it can |
| Seam | A small function placed early so a later change is 1 edit instead of 40 |

---

## 8. Before you start each section

1. Read the section above.
2. Read the linked document for that section.
3. Read the matching tasks in `TASKS.md` for the acceptance criteria.
4. Check `DECISIONS.md` for a constraint that applies.
5. Write the test first where you can, so "done" is something you can run rather than a
   feeling.
6. Update `WORKLOG.md` in the same pull request.
