# headref Agent Architecture

How the AI part of headref works. For the wider system see `ARCHITECTURE.md`. For behaviour
rules see `AI_BEHAVIOR.md`. For tool contracts see `AGENT_TOOLS.md`.

---

## 3.1 Agent purpose

headref turns a set of correlated engineering records into a small number of labelled,
evidence-cited claims about 1 team member, plus a short readable summary.

**Deterministic code produces the claims. The model writes the summary.** See DEC-018.

The model does not decide what data to fetch, does not choose which evidence supports a
claim, does not classify anything, does not compute confidence, does not detect conflicts,
and does not take actions. All of that is application code.

## 3.2 Agent responsibilities

**The LLM call (stage S4b) is responsible for exactly 2 things:**

- Writing `summary`: 2 sentences on what the person is working on.
- Writing `needs_attention`: 1 sentence naming the blocker or risk, or stating there is none.

That is the complete list. It receives findings that are already decided.

**The LLM is NOT responsible for:**

| Not the model's job | Who does it |
|---|---|
| Deciding which data to retrieve | S1 Plan, deterministic code |
| Fetching data | S2 Retrieve, read tools over PostgreSQL |
| Correlating Jira issues to pull requests | Ingestion (DEC-009), read at S3 |
| Deduplicating evidence | S3, deterministic code |
| Deciding likely current work | S4a, deterministic code |
| Producing claims | S4a, deterministic code (DEC-018) |
| Classifying fact / inference / unknown | S4a, deterministic code |
| Selecting supporting evidence IDs | S4a, deterministic code |
| Assigning confidence | S4a, deterministic code (DEC-006) |
| Detecting conflicts, blockers and risks | S4a, deterministic rules |
| Deciding what the user is allowed to see | Authorization, before S1 |
| Any write or action | Nothing. There are no write tools (DEC-001) |

## 3.3 Agent architecture diagram

```text
                    HTTP request
                    (subject_user_id, question_type)
                            |
                            v
                 +----------------------+
                 | Authorization        |   can_view_member()
                 | deterministic        |   403 if denied
                 +----------+-----------+
                            |
                            v
                 +----------------------+
                 | Insight cache lookup |   hash(subject, question, evidence)
                 | deterministic        |   hit -> jump to S6
                 +----------+-----------+
                            |
   =========================|====================== AGENT ORCHESTRATOR
                            v
        S1  +----------------------------+
   PLAN     | question_type -> plan      |  CODE
            | sources, time window       |
            +-------------+--------------+
                          v
        S2  +----------------------------+
  RETRIEVE  | read tools (PostgreSQL)    |  CODE, no network
            | + source_health()          |
            +-------------+--------------+
                          v
        S3  +----------------------------+
  EVIDENCE  | correlate, dedupe,         |  CODE
   BUILDER  | assign ev_1..ev_n          |
            +-------------+--------------+
                          |
                 evidence set (frozen)
                          |
                          v
       S4a  +----------------------------+
  FINDINGS  | current work, blockers,    |  CODE
            | risks, conflicts,          |
            | classification, confidence |
            | -> claims with evidence    |
            +-------------+--------------+
                          |
                 findings (frozen)
                          |
            +-------------+--------------+
            |                            |
            v                            v
       S4b  +----------------+     (kept aside, used in S5)
 NARRATIVE  |  1 LLM CALL    |  LLM  <- Ollama, localhost
            |  no tools      |
            |  no evidence   |
            |  ids emitted   |
            +-------+--------+
                    | summary + needs_attention
                    v
        S5  +----------------------------+
  VALIDATE  | forbidden language?        |  CODE
            | invented ticket/PR names?  |
            | -> else use deterministic  |
            +-------------+--------------+
                          v
        S6  +----------------------------+
  RESPOND   | format, persist agent_run, |  CODE
            | write insight cache        |
            +-------------+--------------+
                          v
                    MemberInsight
```

Only S4b touches the LLM, and it only writes prose. Everything else is ordinary Python.

## 3.4 Agent stages

### S1 Plan

| Field | Value |
|---|---|
| **Purpose** | Decide which sources and which time window this question needs |
| **Input** | `question_type`, `subject_user_id`, current time |
| **Processing** | Look up a fixed mapping from question type to required sources, optional sources and time window. No model call, no branching on content |
| **Output** | `RetrievalPlan(required_sources, optional_sources, window_start, window_end, tool_calls)` |
| **Uses LLM** | No |
| **Deterministic** | Yes |
| **Failure behavior** | An unknown `question_type` is rejected with 422 before this stage. There is no other failure mode |

The mapping, per DEC-007:

| question_type | Required sources | Optional | Window |
|---|---|---|---|
| `current_work` | jira, github | none | last 14 days |
| `blockers` | jira, github | none | last 14 days |
| `risks` | jira | github | last 30 days, plus all open items with a due date |

"Required" matters: if a required source is `unavailable`, the answer is UNKNOWN (FR-023).
If an optional source is unavailable, the answer proceeds with reduced confidence.

### S2 Retrieve

| Field | Value |
|---|---|
| **Purpose** | Load every record the plan asks for, and the health of each source |
| **Input** | `RetrievalPlan` |
| **Processing** | Call the read tools in `AGENT_TOOLS.md`. Call `get_source_health` for each source in the plan. All reads hit PostgreSQL |
| **Output** | `RetrievalResult(work_items, pull_requests, commits, reviews, links, source_health, failures)` |
| **Uses LLM** | No |
| **Deterministic** | Yes |
| **Failure behavior** | A tool returns a typed failure, never an exception and never an empty success. Failures are collected into `failures` and carried forward. A database outage fails the request with 503; there is no partial answer without a database |

**No network calls happen in this stage.** See DEC-002.

### S3 Build Evidence

| Field | Value |
|---|---|
| **Purpose** | Turn raw records into the frozen, ID-labelled evidence set |
| **Input** | `RetrievalResult` |
| **Processing** | 1. Join work items to pull requests, commits and reviews using stored `work_item_link` rows. 2. Drop records whose actor is not the subject. 3. Deduplicate: the same underlying event reached through 2 paths appears once. 4. Rank by relevance (same work item, recency, directness). 5. Truncate to `MAX_EVIDENCE_ITEMS`. 6. Build a code-generated `summary` for each item. 7. Cap and sanitize `excerpt`. 8. Assign `ev_1` .. `ev_n` |
| **Output** | `EvidenceSet`, frozen. This is the only evidence that exists for this run |
| **Uses LLM** | No |
| **Deterministic** | Yes |
| **Failure behavior** | An empty evidence set is a valid outcome and is passed forward. S4a produces an `unknown` claim. If a required source is `unavailable`, S4b is skipped entirely and the run returns UNKNOWN with the reason |

The `summary` field is written by application code, never by the model. See DEC-004.

### S4a Findings

| Field | Value |
|---|---|
| **Purpose** | Decide everything that is true, before the model is involved |
| **Input** | `EvidenceSet`, `source_health`, `failures` |
| **Processing** | Apply the deterministic rules: likely current work from `work_item_link` plus the Jira assignment; blockers BL-1 to BL-8; risks RK-1 to RK-4; conflicts CF-1 to CF-4; classification per `DATA_AND_EVIDENCE.md` 6.6; confidence per `AI_BEHAVIOR.md` 5.4 |
| **Output** | `Findings(claims, blockers, risks, conflicts, unknowns)`, each claim already carrying its classification, confidence and evidence IDs |
| **Uses LLM** | No |
| **Deterministic** | Yes |
| **Failure behavior** | Cannot fail. No evidence produces an `unknown` claim with a stated reason |

This stage is the product. See DEC-018.

### S4b Narrative

| Field | Value |
|---|---|
| **Purpose** | Turn findings that code has already produced into 2 readable sentences |
| **Input** | `question_type`, the `Findings` from S4a, the versioned prompt file |
| **Processing** | Exactly 1 call to Ollama at `http://localhost:11434`. Model from `MODEL_ID`, default `llama3.2`. `temperature: 0`, fixed `seed`, `format` set to the `NarrativeOutput` JSON schema, `num_predict` capped. **No tools are passed.** Untrusted retrieved text appears only inside clearly delimited blocks |
| **Output** | `NarrativeOutput(summary, needs_attention, attention_needed)`. **No claims, no classifications, no evidence IDs** (DEC-018) |
| **Uses LLM** | **Yes. This is the only stage that does** |
| **Deterministic** | No |
| **Failure behavior** | See the table below |

Failure handling in S4:

| Failure | Response |
|---|---|
| Response fails schema validation | Retry once with the same input |
| Second attempt also fails | Fall back to the deterministic summary (FR-012, FR-022), labelled as a fallback |
| Ollama not running, connection refused | No retry. Deterministic fallback. Clear message in logs naming the service |
| Timeout (model cold, or machine under memory pressure) | 1 retry, then the deterministic fallback |
| Model not pulled | No retry. Deterministic fallback. Log the exact `ollama pull` command needed |
| Narrative names a ticket or pull request not in the findings | Discard the narrative, use the deterministic summary, record it in `agent_run.dropped_claims` |

The fallback never fails the page. A user always gets the deterministic facts.

### S5 Validate

| Field | Value |
|---|---|
| **Purpose** | Check the narrative is grounded and safe, then assemble the response |
| **Input** | `NarrativeOutput` from S4b, `Findings` from S4a, `source_health`, `failures` |
| **Processing** | See the ordered rules below |
| **Output** | `ValidatedInsight(claims, conflicts, blockers, risks, unknowns, dropped_claims)` |
| **Uses LLM** | No |
| **Deterministic** | Yes |
| **Failure behavior** | Cannot fail. A rejected narrative falls back to the deterministic summary; the findings are unaffected |

Validation rules, applied in order. Under DEC-018 the claims come from S4a and are already
correct by construction, so validation now checks **the narrative only**:

1. **Invented entity check.** Every ticket key (`AUTH-245`) and pull request number
   mentioned in `summary` or `needs_attention` must appear in the findings passed to S4b.
   If any does not, the narrative is discarded, the deterministic summary is used instead,
   and the event is recorded in `dropped_claims` with reason `INVENTED_ENTITY`.
2. **Forbidden language check.** The narrative must not contain person-judgment language
   (`AI_BEHAVIOR.md` 5.11). Matching triggers the same fallback, reason
   `FORBIDDEN_LANGUAGE`.
3. **Length and shape check.** Both fields must be non-empty and within the schema bounds.
   Otherwise 1 retry, then the deterministic summary.
4. **Refusal check.** A refusal written as valid JSON ("I am sorry, but I cannot help")
   fits the schema but is not a summary. First-person refusal phrasing triggers the same
   fallback, with no retry, reason `REFUSAL` (EV-12 C2, agreed 2026-10-22).
5. **Assembly.** The claims, blockers, risks, conflicts and confidence from S4a are
   attached unchanged. Evidence is resolved from IDs by code.

Steps 1 to 4 are the 4 validator checks; step 5 rejects nothing.

The important property: **a failed narrative never produces a wrong fact.** The worst case
is that the user sees the deterministic summary instead of a generated one.

### S6 Respond

| Field | Value |
|---|---|
| **Purpose** | Produce the API response and record what happened |
| **Input** | `ValidatedInsight`, timings, token counts |
| **Processing** | Assemble `MemberInsight`. Attach per-source `last_synced` and health state. Write the `agent_run` row including the evidence set, raw model output, validated output and dropped claims. Write the insight cache entry |
| **Output** | `MemberInsight` |
| **Uses LLM** | No |
| **Deterministic** | Yes |
| **Failure behavior** | If persisting `agent_run` fails, the response is still returned and the persistence failure is logged at error level. Observability must not break the product |

## 3.5 Agent execution flow

```text
1.  Request arrives: subject_user_id, question_type
2.  Authorization: can_view_member(actor, subject). Denied -> 403, logged
3.  Validate question_type against the 3 allowed values. Invalid -> 422
4.  S1 Plan: question_type -> required sources, optional sources, time window
5.  S2 Retrieve: read tools over PostgreSQL + source_health per source
6.  Gate: any REQUIRED source unavailable?
       yes -> skip S4, return UNKNOWN with the reason and the last successful sync time
       no  -> continue
7.  S3 Build Evidence: correlate, dedupe, rank, truncate, assign ev_1..ev_n
8.  Cache lookup: hash(subject_user_id, question_type, evidence_set)
       hit  -> skip S4 and S5, go to step 11 with the cached validated insight
       miss -> continue
9.  S4a Findings: claims, blockers, risks, conflicts, confidence, all in code
10. S4b Narrative: 1 Ollama call, structured output, no tools, prose only
11. S5 Validate: narrative grounded and safe, else deterministic fallback
11. S6 Respond: assemble MemberInsight, persist agent_run, write cache
12. Return to the UI
```

The cache lookup sits after evidence construction, not before, because the cache key is
the evidence hash. Building the evidence is cheap; the model call is not.

## 3.6 Agent state

State exists for the duration of 1 request and is held in a single `AgentRunContext`
object passed between stages.

```text
AgentRunContext
  run_id                  uuid, generated at the start
  actor_user_id           who asked
  subject_user_id         who it is about
  question_type           current_work | blockers | risks
  plan                    RetrievalPlan from S1
  retrieval               RetrievalResult from S2
  evidence_set            EvidenceSet from S3, frozen after creation
  source_health           per source: state, last_success_at, last_error_type
  tool_failures           typed failures collected during S2
  raw_model_output        the S4 response, stored verbatim
  validated               ValidatedInsight from S5
  dropped_claims          what the validator rejected and why
  prompt_version          e.g. "reasoning_v1"
  model                   e.g. "llama3.2:latest"
  input_tokens            from the API response
  output_tokens           from the API response
  started_at, finished_at
  error_type              null unless something failed
```

At the end of the run this context is written to the `agent_run` table and discarded.

## 3.7 Agent memory

**The MVP agent has no long-term memory. This is deliberate.**

| Memory type | Present in MVP? | Notes |
|---|---|---|
| Short-term execution state | Yes | `AgentRunContext`, 1 request, discarded after |
| Conversation history | No | There is no conversation. Each request is independent |
| Long-term learned memory | No | The agent does not remember previous answers or adapt |
| Persistent project context | No | Nothing is injected from past runs into a new prompt |
| Audit history | Yes, but not memory | `agent_run` rows are written for audit, cost and replay. **They are never read back into a prompt** |
| Result cache | Yes, but not memory | The insight cache returns a previously computed answer when the evidence hash is identical. It does not influence reasoning |

The distinction matters: `agent_run` and the insight cache are storage, not memory. No
past run ever enters a future prompt. Every run reasons only over the evidence set built
for it.

Do not add memory features that are not listed here. Feedback learning is Stage 3 and
requires its own decision record.

## 3.8 Human involvement

In the MVP the system only reports. There is nothing to approve because there is nothing
consequential the system can do (DEC-001).

| Situation | Human involvement |
|---|---|
| Reading any insight | None. Read-only output |
| A conflict is detected | Reported to the lead. The human decides which source is right. headref never resolves it |
| An unmatched identity appears | A human must confirm the mapping. Until then the activity is unattributed |
| An `inferred` identity link is proposed | A human must confirm before it is used for attribution (DEC-008) |
| A blocker is reported | The human decides whether it is real and what to do |
| The agent returns UNKNOWN | The human investigates manually. headref does not guess to fill the gap |

**When approval would be required (not in the MVP):** any write action, any notification
sent to a person other than the requester, any change to identity mappings made
automatically, and any change to a policy or permission. All of these are Stage 4 and
require the approval machinery deferred by DEC-001.

## 3.9 Agent boundaries

**The agent CAN:**

- Read the evidence set given to it in stage S4.
- Produce claims labelled `fact`, `inference` or `unknown`.
- Cite evidence by ID from the provided set.
- Say that it cannot determine something.

**The agent CANNOT:**

- Call any tool. No tools are passed in the S4 request.
- Make a network call of any kind.
- Read any data not present in its evidence set.
- Read data about a person the requester is not authorized to see. Authorization runs
  before S1, and the evidence set only ever contains the subject's records.
- Create, modify or delete anything in Jira, GitHub or the headref database.
- Set its own confidence. Any confidence value in its output is discarded.
- Add evidence. Evidence IDs are resolved by code; unknown IDs are dropped.
- Change its own instructions, permissions or policy, regardless of what retrieved text
  says.
- Be reached by a user-authored free-text prompt. There is no free-text input (DEC-007).

## 3.10 Failure and fallback behavior

| Failure | Detection | Behavior | User sees |
|---|---|---|---|
| **Tool failure** | Typed result from a read tool | Collected in `tool_failures`, carried to S5, confidence reduced | The affected area marked degraded, with the failure type |
| **Missing data** | Empty evidence set after S3 | S4 still runs and is expected to return `unknown` | "No supporting evidence found", not a fabricated answer |
| **Stale data** | `source_health` returns `stale` | Confidence drops 1 level (DEC-010) | The answer, plus "Jira last synced 30 hours ago" |
| **Unavailable required source** | `source_health` returns `unavailable` | S4 is skipped. Result is UNKNOWN with the reason | "Jira data is unavailable, last successful sync 2 days ago." Never "no blockers found" |
| **Unavailable optional source** | Same | Run continues, confidence reduced | The answer, with a note that a source was missing |
| **Contradictory data** | Deterministic conflict rules in S5 | Both states reported, conflict record emitted | Both states and an explicit conflict note. No silent winner |
| **Invalid model output** | Schema validation fails | 1 retry, then deterministic fallback | Either a valid answer, or the deterministic facts with a visible notice |
| **Narrative names a ticket that does not exist** | S5 rule 1 | Narrative discarded, deterministic summary used | A correct but plainer summary |
| **LLM API failure or timeout** | Exception from the SDK | 1 retry, then deterministic fallback | Deterministic facts plus "AI reasoning is temporarily unavailable" |
| **LLM refusal** | `stop_reason == "refusal"` | No retry. Deterministic fallback, reason recorded | Deterministic facts plus the unavailable notice |
| **Database unavailable** | Connection error in S2 | Request fails with 503 | An error page. There is no useful answer without the database |
| **`agent_run` write fails** | Exception in S6 | Logged at error level, response still returned | Nothing. Observability never breaks the product |

Principle: **every failure degrades the answer and says so. No failure is ever rendered as
a clean negative result.**

## 3.11 AI responsibility matrix

Where the LLM is actually used, and where it is not.

| Responsibility | Deterministic Code | LLM | Both |
|---|---|---|---|
| Authorization | X | | |
| Choosing which sources to query | X | | |
| Choosing the time window | X | | |
| Fetching records | X | | |
| Correlating a Jira issue to a pull request | X | | |
| Deduplicating evidence | X | | |
| Ranking evidence relevance | X | | |
| Assigning evidence IDs | X | | |
| Writing the evidence `summary` text | X | | |
| Detecting a Jira / GitHub state conflict | X | | |
| Detecting blockers | X | | |
| Detecting delivery risks | X | | |
| Computing confidence | X | | |
| Determining source freshness | X | | |
| Enforcing schema validity | X | | |
| Rejecting unsupported claims | X | | |
| Recording the run, cost and latency | X | | |
| **Writing the summary sentence** | | **X** | |
| Labelling a claim fact / inference / unknown | X | | |
| Selecting which evidence supports a claim | X | | |
| **Phrasing the needs-attention sentence** | | **X** | |

**Read this table honestly.** 19 of 21 responsibilities are deterministic code. The model
does exactly 2 things: it writes the summary sentence and the needs-attention sentence.
It shares nothing, because under DEC-018 it is never asked to decide anything.

This was not a stylistic choice. It was measured: on the 3B local model this project must
use, the claim-producing design missed a blocker entirely in testing. Correctness moved
into rules; language stayed with the model.

That is the correct shape for this product. The value is in the evidence graph, not in the
model. A model that is unavailable degrades headref to a still-useful deterministic report.
A model that misbehaves is caught by validation. This should be stated plainly in the
README rather than hidden, because it is the reason the system can be trusted.
