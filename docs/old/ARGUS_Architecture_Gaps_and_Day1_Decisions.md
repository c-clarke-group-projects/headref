# headref - Architecture Gaps, Holes and Day 1 Decisions

Companion to `headref_MVP_Plan.html` and `TeamPulse_Analysis_and_Recommendations.md`.
Written for the start of development. Covers 4 things:

1. Contradictions in the current plan that must be resolved before any code is written.
2. Architecture that is missing and is needed for the MVP to work at all.
3. Architecture that is missing and is needed before the demo.
4. The Day 1 order of work.

Severity: **P0** blocks the MVP. **P1** needed before the demo. **P2** later stage.

---

## 0. Verdict

The plan is good. Scope is correctly cut, the lane split is sensible, the
security spine (read only, no write tools in the LLM path) is right, and the
2 person workload is realistic.

The holes are not in the breadth. They are in 3 places:

1. **The plan never says how data gets into Postgres or when.** Ingestion has no
   trigger, no scheduler, no state table. This is the single largest gap.
2. **The correlation between a Jira issue and a GitHub PR is the actual product,
   and it is not a modelled entity.** It exists only as a string parse inside the
   evidence builder.
3. **The anti hallucination mechanism does not hold as designed.** The current
   `Insight` schema lets the LLM emit its own evidence objects. That is the exact
   shape that produces fabricated evidence, and the validation stage cannot catch
   it because there is nothing to check against.

Everything else below is smaller.

---

## 1. Contradictions to resolve before writing code

These are places where the plan says 2 different things. Each needs one decision
in Sprint 0. They are cheap now and expensive in week 3.

### C1 (P0) Do tools call APIs or read the database?

The file layout puts `tools/` next to `integrations/` and gives both to Lane A,
which implies tools call GitHub and Jira. The architecture diagram shows the tool
layer reading from Postgres. `PullRequestOut` carries `retrieved_at`, which is an
ingestion field, not a query field.

Both paths cannot exist. If tools call APIs, a lead's question blocks on 2
external APIs, latency is 5 to 60 seconds, rate limits are hit during the demo,
and nothing is reproducible in tests.

**Decision: tools read Postgres only. No tool ever makes a network call.**

Consequences, all good:

- Answering a question is fast and offline.
- Tests need no HTTP mocking at the tool layer at all.
- The demo cannot be broken by GitHub being slow.
- `retrieved_at` becomes a column on the row, set by the ingester, and the tool
  just returns it.

Rename for clarity: `integrations/` is the write path (network to database),
`tools/` is the read path (database to agent).

### C2 (P0) Is validation an LLM call or code?

Section 3 diagram says "4 validate (LLM/rules)". The stage table says
"Code + rules". The tech stack section says one LLM call.

**Decision: validation is 100 percent deterministic code. One LLM call total.**

This is both cheaper and stronger. See section 2.3 for the mechanism that makes
it work.

### C3 (P1) Is there a free text question box?

The agent has a "planner" stage that "decides which tools and time window the
question needs", but the UI is 2 fixed pages. If the UI only has pages, the
planner is dead code and there is no natural language input at all.

**Decision for MVP: no free text box.** Offer 3 fixed question types on the
member page:

- What is this person working on?
- Is anything blocking this person?
- What is at risk this week?

The planner becomes a small dictionary mapping question type to time window and
required sources. It stays deterministic and testable, and there is no intent
classification, no out of scope handling, and no jailbreak surface through the
question field.

Add a free text box in a later stage if it is wanted.

### C4 (P1) `temperature 0` is specified 3 times and is no longer a valid parameter

The plan uses temperature 0 as the determinism strategy (risk register, testing
section, issue B4). On the current Claude models the `temperature` parameter has
been removed and sending it returns a 400 error.

**Determinism has to come from somewhere else.** Use all 3 of:

- Recorded fixtures, so the evidence going into the model is byte identical.
- Structured outputs (`output_config.format`), so the shape is fixed.
- Deterministic validation, so tests assert on structured fields, never on prose.

Tests must assert on `classification`, `confidence`, `conflicts` and the set of
cited evidence ids. Never assert on the wording of a sentence.

---

## 2. Missing architecture, P0 for the MVP

### 2.1 (P0) Ingestion has no trigger and no state

The plan has `ingest.py` and says data is cached in Postgres. It never says what
runs it. Redis, a queue and a scheduler are all deferred, correctly, but nothing
replaced them.

Required for the MVP, and it is small:

- A CLI entry point: `python -m app.sync --source github --team 1`. One command,
  idempotent, safe to re run.
- A `sync_run` table recording every attempt.
- A `sync_cursor` table so a re run resumes instead of refetching everything.
- The web request path never triggers ingestion. It reads whatever is in the
  database and shows how old it is.

Suggested tables:

```text
sync_run
  id
  source              -> github | jira
  scope               -> repo full name, or jira project key
  status              -> running | success | partial | failed
  started_at
  finished_at
  items_fetched
  items_written
  items_skipped
  error_type          -> null | TIMEOUT | RATE_LIMITED | AUTH_FAILED | UPSTREAM_ERROR
  error_detail

sync_cursor
  source
  scope
  cursor_value        -> e.g. last commit sha, or jira updated timestamp
  updated_at
  PRIMARY KEY (source, scope)
```

### 2.2 (P0) Graded scenario 5 cannot be answered without source health

Scenario 5 is "Jira source down, report unavailable, lower confidence, never say
no blockers found". With tools reading from Postgres, the database will happily
return yesterday's Jira rows and the agent has no way to know Jira is down.

The answer comes straight out of `sync_run`. Add one function:

```text
source_health(source, team) -> {
  last_success_at,
  last_attempt_at,
  last_error_type,
  state: fresh | stale | unavailable
}
```

Rules:

- `fresh` if the last successful sync is within the freshness window.
- `stale` if the last successful sync is older than the window but there is data.
- `unavailable` if the last attempt failed or there has never been a success.

Then: any insight whose evidence depends on a `stale` source drops one confidence
level. Any insight that depends on an `unavailable` source returns `UNKNOWN` with
the reason, never a negative claim. This is a deterministic rule, it is about 20
lines of code, and it is the single most convincing behaviour in the whole demo.

### 2.3 (P0) The evidence model lets the LLM invent evidence

Current contract:

```python
class Insight(BaseModel):
    claim: str
    classification: str
    confidence: str
    evidence: list[EvidenceItem]   # <- the LLM generates these
    conflicts: list[str]
```

If the model produces the `EvidenceItem` objects, it can produce evidence that
does not exist, and `validation.py` has nothing authoritative to compare against.
`detail` is a human readable string, so the validator cannot check it in code
either.

**Fix, and this is the most important change in the document:**

Stage 2 builds the evidence set and assigns every item a stable id. That set is
the only evidence that exists. The LLM is given the set and is required to cite
by id. It cannot mint new ids.

```python
class EvidenceItem(BaseModel):
    id: str                       # "ev_1", stable within one agent run
    source: str                   # jira | github
    entity_type: str              # issue | pull_request | commit | review | issue_link
    entity_key: str               # "AUTH-245", "acme/api#182", commit sha
    url: str                      # deep link, shown in the evidence drawer
    summary: str                  # short factual line, built by code not the LLM
    excerpt: str | None           # untrusted source text, capped length
    observed_at: datetime         # when the event happened at the source
    retrieved_at: datetime        # when we fetched it

class Claim(BaseModel):
    claim: str
    classification: str           # fact | inference | unknown
    evidence_ids: list[str]       # MUST reference ids from the provided set

class Insight(BaseModel):
    claim: str
    classification: str
    confidence: str               # assigned by code, not by the model
    evidence: list[EvidenceItem]  # resolved by code from evidence_ids
    conflicts: list[str]          # produced by code, not by the model
```

The validator then becomes 4 hard rules in ordinary Python:

1. Every `evidence_id` in a claim exists in the run's evidence set. If not, the
   claim is dropped and the drop is logged.
2. A claim classified `fact` must cite at least 1 evidence item from the
   authoritative source for that kind of fact (see the source authority table in
   the checklist, section 13).
3. A claim classified `inference` must cite 2 or more evidence items.
4. Confidence is computed by code from the resolved evidence, never taken from
   the model. The model does not get to state its own confidence.

This is what makes hallucination structurally difficult rather than discouraged
by a prompt. It is also easy to unit test and it demos well.

### 2.4 (P0) The issue to PR link is not a modelled entity

The whole product rests on connecting `AUTH-245` to `feature/AUTH-245-refresh`
to PR 182 to 4 commits. In the current plan that connection is derived by string
parsing inside `evidence_builder.py` on every request. It is not stored, not
auditable, not testable in isolation, and it cannot carry a confidence.

Model it:

```text
work_item_link
  id
  work_item_id        -> internal WorkItem
  target_type         -> pull_request | branch | commit | review
  target_id           -> internal id of the target
  link_method         -> branch_name | pr_title | pr_body | commit_message | jira_remote_link
  confidence          -> HIGH | MEDIUM | LOW
  created_at
  UNIQUE(work_item_id, target_type, target_id)
```

Link method ranks by reliability:

- `jira_remote_link`, a real Jira to GitHub link object: HIGH.
- `branch_name`, the ticket id parsed out of the branch: HIGH.
- `pr_title`: MEDIUM.
- `pr_body` or `commit_message`: MEDIUM.
- Anything inferred from timing or authorship alone: LOW, and it should probably
  not be used at MVP.

Written by the ingester (Lane A), read by the evidence builder (Lane B). This
also gives the 2 lanes a clean seam: Lane A produces links, Lane B consumes them.

### 2.5 (P0) Blockers and risks have no defined source

`MemberInsight` has `blockers: list[Insight]` and `risks: list[Insight]`. Slack is
out of scope. Nothing in the plan says where a blocker comes from with only
GitHub and Jira available. Without this list, Lane B cannot build the feature and
the demo has no blockers to show.

Deterministic blocker signals available from Jira and GitHub only:

| Signal | Source | Type |
|---|---|---|
| Issue status is Blocked or On Hold | Jira | explicit |
| Issue has an "is blocked by" link to an open issue | Jira | dependency |
| Issue flagged, impediment flag set | Jira | explicit |
| PR has changes requested and no push since | GitHub | review |
| PR open with no review for more than N days | GitHub | review |
| PR is draft and older than N days | GitHub | progress |
| Required checks failing on the head commit | GitHub | environment |
| Assigned issue in progress with no linked branch or PR after N days | both | progress |

Deterministic risk signals:

| Signal | Source |
|---|---|
| Due date within N days and status not done | Jira |
| Time in current status exceeds N days | Jira |
| High priority issue with no activity in N days | Jira + GitHub |
| Jira status conflicts with PR state | both |

All of these are rules, not model output. Put the thresholds in `config.py` as
named constants so they are tunable and testable.

This raises an honest design point worth stating plainly: **once facts, blockers,
risks and conflicts are all deterministic, the LLM's only real jobs are the
current work narrative and the phrasing.** That is fine. It is the safe and
correct architecture. The value of the product is the evidence graph, not the
model. Say this in the README, it is a strength and not a weakness.

One place a model genuinely beats rules, if it is wanted later: classifying free
text Jira comments and PR review comments into blocker candidates. That is a
small classification job over untrusted text. Cache the result by content hash so
the same comment is never classified twice.

### 2.6 (P0) Identity has no bootstrap path

Lane A task A1 is "identity graph + resolver + unmatched queue", and the analysis
document sets the rule "never attribute from display name alone, require at least
one verified link". With personal access tokens and no OAuth in the MVP, there is
no verified email anywhere in the system, so that rule as written cannot be
satisfied and Lane A is blocked on a question the plan does not answer.

Two real world details that make the automatic path fail more often than expected:

- GitHub commit author emails are frequently the privacy address
  `NNNN+username@users.noreply.github.com`, not a company email.
- Jira Cloud hides user email addresses by default under its privacy settings, so
  `emailAddress` is often absent from the API response.

Both should be confirmed against the real accounts on Day 1 rather than assumed.

**MVP answer:**

- The verified link comes from an admin supplied mapping, committed as
  `seed/identity_map.yml` and loaded by the seeder. `match_method = manual`.
- The resolver may propose links from an email match or an exact handle match.
  Those are written with `match_method = inferred` and are **not used for
  attribution** until confirmed.
- Anything unmatched goes to `unmatched_entity` with the raw external id, the
  handle seen, a count, and the last time it was seen. The UI shows the count.
  Nothing is ever silently dropped or guessed.

```text
unmatched_entity
  id
  integration        -> github | jira
  external_id
  external_handle
  first_seen_at
  last_seen_at
  occurrence_count
  resolved_user_id   -> null until a human resolves it
```

### 2.7 (P0) No agent run record

Nothing persists what the agent did. The plan has no audit table, and the risk
register lists "LLM cost creep" with no mechanism that measures cost.

One table solves 4 problems at once: debugging, cost tracking, eval replay, and
the explainability story for the demo.

```text
agent_run
  id
  actor_user_id            -> who asked
  subject_user_id          -> who it was about
  question_type
  time_window_start / end
  evidence_set             -> JSON snapshot of the evidence given to the model
  prompt_version           -> "reasoning_v1"
  model                    -> "claude-opus-5"
  raw_model_output         -> JSON as returned
  validated_output         -> JSON after the validator ran
  dropped_claims           -> claims the validator rejected, with the reason
  input_tokens / output_tokens
  latency_ms
  error_type
  created_at
```

`dropped_claims` is worth having on its own. It is direct evidence that the
hallucination defence is working, and it is a good thing to show during the demo.

---

## 3. Missing architecture, P1 before the demo

### 3.1 (P1) Seed the demo by running the ingester, not by inserting rows

`seed_demo.py` inserting rows directly creates a second write path into the same
tables. The seeded data will drift from what the real ingester produces, and the
ingester will never be exercised end to end in the demo.

Instead: put recorded GitHub and Jira API payloads in `seed/fixtures/`, and have
`seed_demo.py` feed them through the real `ingest.py`. Same code path, fully
deterministic, no network, and it tests the ingester every time it runs.

Those same fixtures are then the eval fixtures. One artefact, 3 uses: demo, tests
and evals.

### 3.2 (P1) Insight caching, and the actual cost number

The plan says "one reasoning call per request" but nothing stops a page refresh
from being a new billed call.

Cache the validated insight keyed by `hash(subject_user_id, question_type,
evidence_set)`. If the evidence has not changed, the answer does not change and
nothing is billed. Sync invalidates it naturally because the evidence hash moves.

Concrete cost model, using current Claude Opus 5 pricing (5 dollars per million
input tokens, 25 dollars per million output tokens):

```text
per insight call:
  input  ~4,000 tokens of structured evidence  = $0.020
  output   ~800 tokens of JSON                 = $0.020
  total                                        ~ $0.04

8 person team, 3 refreshes per person per day  = 24 calls = ~$1.00 per day
whole build plus demo, a few hundred calls     = ~$10 to $20
```

That is the number to agree a spend limit against. Prompt caching on the stable
system prompt and instructions lowers the input side further. Put a hard monthly
cap on the API key in the Anthropic console on Day 1, before the first call.

### 3.3 (P1) Put the authorization check in one place now, even as a stub

The MVP uses a login stub, which is fine. But if every route reads the database
directly, adding real authorization later means touching every route.

Add one function today:

```python
def can_view_member(actor: User, subject: User) -> bool:
    """MVP: same team only. Later: real RBAC."""
```

Call it in every route that returns member data. It costs 10 minutes now and it
means the seam exists. Broken object level authorization is the top item on the
OWASP API list and a reviewer will ask about it.

### 3.4 (P1) Untrusted text reaching the browser

The plan covers prompt injection into the model well. It does not cover injected
content reaching the UI. The evidence drawer shows `excerpt` text taken from PR
bodies and Jira comments.

3 rules:

- Jinja2 autoescapes by default. Never use the `|safe` filter on any field that
  originated from GitHub or Jira.
- Do not render markdown or HTML from source text. Show it as plain text.
- Cap `excerpt` length, 500 characters is plenty, and strip URLs from it. An
  image URL rendered inside the drawer is a beacon that leaks who viewed what to
  whoever wrote the PR description.

### 3.5 (P1) Timestamps

3 systems, 3 time formats. Decide once, on Day 1:

- Every timestamp column is `timestamptz`, stored in UTC.
- Convert at the integration boundary, never anywhere else.
- The UI renders local time and also shows the absolute UTC value on hover.
- "Recent" is one named constant in `config.py`, not a number typed into 5
  different files.

### 3.6 (P1) CI is missing 3 cheap checks

The plan's CI runs lint and tests. Add:

- A secret scanner. The coding rules already require one, the CI section does not
  run one.
- `alembic upgrade head` against an empty database. Catches a broken migration
  before it reaches the other person's machine.
- `alembic heads` returning exactly 1 head. With 2 people and one migration
  directory, a second head is the most common Postgres conflict in a 2 person
  team and it is silent until someone tries to migrate.

### 3.7 (P1) Real data on Day 1, before any code

The Quick Start checklist covers the repo, labels, milestones and the board. It
does not cover getting a real data source. Without one, Lane A is writing
integrations against an imagined API shape and the graded scenarios are fiction.

On Day 1, before writing code:

- Create a real Jira Cloud site (the free tier covers a small team), create a
  project, create 10 to 15 issues with realistic statuses, assignees, due dates,
  a blocked issue and a blocking link.
- Create a real GitHub repo with branches named after the ticket ids, several
  PRs in different states (open, merged, draft, changes requested) and real
  commits.
- Generate the read only tokens and confirm both APIs return what you expect with
  one `curl` each.
- Save those real responses as the fixtures in `seed/fixtures/`.

Half a day of work that removes the largest correctness risk in the project.

### 3.8 (P2) Deletion is silently absent

Right to erasure and employee data rules make a purge path mandatory for a real
deployment. It is correctly out of scope for the MVP, but it is currently not
mentioned at all, which reads as an oversight rather than a decision.

Add it to the out of scope list explicitly, and write the stub:

```python
def purge_user(user_id: int) -> None:
    """Remove evidence, identity links, cached activity and insights for a user.
    MVP: not implemented, raises NotImplementedError. Phase 2."""
```

---

## 4. Reconsider the lane boundary

The current split gives `tools/` to Lane A. Once C1 is decided (tools read
Postgres), the tools are database queries, which is closer to Lane B's work, and
it leaves Lane A owning code that Lane B is the only consumer of.

Cleaner boundary:

| Lane A (Keshan) | Lane B (Isiwara) |
|---|---|
| `integrations/` GitHub and Jira clients | `tools/` read queries over canonical tables |
| `ingest.py` normalize and write | `agent/` planner, evidence builder, reasoning, validation, confidence |
| `identity_resolver.py`, unmatched queue | `web/` routes, templates, UI |
| `work_item_link` creation | `work_item_link` consumption |
| `sync_run`, `sync_cursor`, `source_health` | `agent_run` |
| `seed/fixtures` recording | `seed/fixtures` consumption |

The handoff shrinks to exactly one artefact: **the database schema**. That is the
cleanest possible seam for 2 people, and it removes the need to freeze the tool
function signatures separately.

If you prefer to keep tools with Lane A, that works too. The rule that matters
either way is the one from C1: **tools do no network I/O.**

---

## 5. Complete table list for the Sprint 0 migration

The plan lists 8 tables. This is the full set the MVP actually needs. It is still
small.

```text
Core
  organization            -- 1 row for MVP, but the column exists
  team
  app_user                -- internal person
  repository
  project                 -- jira project

Identity
  identity_link
  unmatched_entity

Work
  work_item               -- jira issue
  pull_request
  commit
  review
  work_item_link          -- the correlation, section 2.4

Agent
  evidence                -- optional: persist per run, or rebuild each time
  insight
  agent_run

Operations
  sync_run
  sync_cursor
```

Every table that holds external data carries `source_updated_at`, `retrieved_at`
and the external id it came from. That is the freshness contract Lane A task A6
already asks for, applied consistently.

Note: do not name a table `user` in Postgres. It is a reserved word and it will
cause quoting pain in every raw query. Use `app_user`.

---

## 6. Revised gap register

| ID | Gap | Severity | Where | When |
|---|---|---|---|---|
| C1 | Tools: API or database. Decide database | P0 | Sprint 0 | Day 1 |
| C2 | Validation: LLM or code. Decide code | P0 | Sprint 0 | Day 1 |
| C3 | Free text question box: decide no, fixed types | P1 | Sprint 0 | Day 1 |
| C4 | `temperature 0` is no longer valid, replace the determinism strategy | P1 | Sprint 0 | Day 1 |
| A1 | Ingestion trigger, `sync_run`, `sync_cursor` | P0 | Lane A | Sprint 1 |
| A2 | `source_health`, required by graded scenario 5 | P0 | Lane A | Sprint 1 |
| A3 | Evidence ids, LLM cites by id, code assigns confidence | P0 | Shared contract | Sprint 0 |
| A4 | `work_item_link` table | P0 | Lane A writes, B reads | Sprint 1 |
| A5 | Blocker and risk signal definitions without Slack | P0 | Lane B | Sprint 1 |
| A6 | Identity bootstrap via `seed/identity_map.yml` | P0 | Lane A | Sprint 1 |
| A7 | `agent_run` table | P0 | Lane B | Sprint 2 |
| B1 | Seed through the ingester, not direct inserts | P1 | Shared | Sprint 1 |
| B2 | Insight cache keyed by evidence hash, spend cap on the key | P1 | Lane B | Sprint 2 |
| B3 | `can_view_member` seam | P1 | Lane B | Sprint 1 |
| B4 | Escape untrusted text in the UI, cap and strip excerpts | P1 | Lane B | Sprint 2 |
| B5 | UTC everywhere, one "recent" constant | P1 | Shared | Sprint 0 |
| B6 | CI: secret scan, migration smoke test, single alembic head | P1 | Shared | Sprint 0 |
| B7 | Real Jira and GitHub data plus recorded fixtures | P1 | Both | Day 1 |
| B8 | `purge_user` stub, deletion moved to the explicit out of scope list | P2 | Lane A | Sprint 3 |

---

## 7. What the plan already gets right, keep it

- The product boundary: evidence, not surveillance, not a productivity score.
- Read only for the whole MVP. This is what makes prompt injection survivable.
- No write tools in the LLM path, ever.
- Cutting Slack, calendar, vector search, SSO, multi tenancy and human in the
  loop from the MVP. All correct.
- Rule based HIGH / MEDIUM / LOW instead of a numeric score with no calibration
  data behind it.
- Postgres only, no Redis, no queue, no vector database at this stage.
- Typed tool failures so "Jira denied access" is never rendered as "no blockers".
- The WORKLOG append at top rule. It genuinely prevents merge conflicts and it is
  good contribution evidence.
- Folder ownership, small PRs, contracts frozen in Sprint 0.
- Fixtures so Lane B is never blocked waiting for Lane A.

---

## 8. Day 1 order

Do these in order, both people together, before any feature code.

1. Create the real Jira project and the real GitHub repo with realistic data
   (section 3.7). Generate read only tokens. Confirm both APIs with one `curl`
   each. Save the raw responses into `seed/fixtures/`.
2. Set a hard spend cap on the Anthropic API key.
3. Make the 4 decisions in section 1. Write them into the repo as
   `docs/decisions.md`, 1 paragraph each. They are the ones that are expensive to
   reverse.
4. Create the repo, protect `main`, add labels, milestones, the board, the issue
   and PR templates, `WORKLOG.md`.
5. Write the full table list from section 5 as SQLAlchemy models and the first
   Alembic migration. One person drives, the other reviews live.
6. Freeze the Pydantic contracts with the evidence id change from section 2.3
   applied. This is the contract that matters most.
7. CI: lint, tests, secret scan, `alembic upgrade head`, single alembic head.
8. `docker compose up` reaches a health endpoint on both machines.

Then open the Sprint 1 issues and split.

---

## 9. One line summary

The plan is sound and correctly scoped. Fix 5 things before writing feature code:
decide that tools read the database and never the network, give ingestion a
trigger and a `sync_run` state table so source outages are detectable, make the
LLM cite evidence by id so the validator has something real to check, model the
issue to PR link as a table instead of a string parse, and define where blockers
come from now that Slack is out of scope.
