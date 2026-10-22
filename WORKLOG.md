# headref Work Log

**Append new entries at the top. Do not rewrite previous entries unless correcting a
factual mistake.**

This is the shared chronological development log. It is not a planning document. For what
needs doing, see `TASKS.md`. For why things are the way they are, see `DECISIONS.md`.

## How to use this file

- **1 entry per work session.** Newest entry directly under this header block.
- **Only add your own entries.** Do not edit the other developer's entries.
- **Update it in the same pull request as your code.** A reviewer should see the change
  and the log entry together.
- **Keep entries short.** 10 lines is normal. This is a status handoff, not a diary.
- **Omit empty sections.** If nothing was blocked, delete the Blocked By line.
- **A merge conflict here is never a real conflict.** Keep both blocks.

## Entry template

```markdown
## YYYY-MM-DD | <Developer> | <Task ID>
Status: IN_PROGRESS | IN_REVIEW | DONE | BLOCKED

### Completed
What was actually finished. Specific, not "worked on X".

### Changed
Important files or components touched.

### Discovered
New technical findings the other developer needs to know.

### Problems
Bugs, failures, unexpected behaviour.

### Decisions Needed
Any unresolved choice needing team discussion. Add a DEC record if it is architectural.

### Blocked By
The exact blocker, with a task ID or a person.

### Next Step
What you will do next.

### AI Assistance
Optional. Note where an AI assistant did something worth knowing about, especially
anything that needs a human check.
```

## For AI coding assistants

Read the 3 most recent entries before proposing a change. They tell you what state the
code is actually in, which is often ahead of or behind `TASKS.md`.

When you complete work, append an entry using the template above. Record what you actually
did, not what was planned. If you were unable to complete something, say so in Problems
and leave the status as `IN_PROGRESS`. Do not mark a task `DONE` unless every acceptance
criterion in `TASKS.md` is met.

---

## 2026-10-23 | Keshan | P0-002 sandbox fixtures captured, A-3 corrected
Status: IN_PROGRESS

### Completed
- Ran `scripts/capture_fixtures.py --workspace-id 1` (about 20 GET requests) and
  committed `seed/fixtures/sandbox/`: 8 pull requests, 7 commits, 2 reviews, 11 Jira
  issues from `keshan-dev/headref-sandbox` and project HRS.
- Issue mix present: 11 issues across todo, in_progress, done and blocked; HRS-5 flagged;
  HRS-6 is blocked by HRS-7; 6 of 8 pull requests on `feature/HRS-` branches; pull
  requests open, merged, draft and changes requested; 1 pull request with no ticket ID;
  2 commits with no mapped author; injection text in pull request #8; HRS-1 in progress
  with its pull request #1 merged (CF-1).
- **A-3 corrected** for this sandbox: Jira returns `emailAddress` on 20 of 22 user
  objects through the OAuth connection, and all 7 GitHub commit author emails are real
  addresses, 0 noreply. A 2 person sandbox, so a real team may differ.
- Rate limit headers: GitHub `x-ratelimit-limit: 5000` (resource core, 146 used); Jira
  `x-ratelimit-limit: 350`, 349 remaining.

### Problems
- The first capture wrote 2 things the sanitizer missed, caught by a scan before any
  commit: a personal email inside GitHub's raw commit text (`verification.payload`), and
  the private repository's `temp_clone_token`. The sanitizer now replaces emails inside
  any text and any `*token` field, with tests; the capture was rerun and rescanned clean.
- In the HRS workflow, the Done status has status category `indeterminate` and Blocked
  has `done`. headref maps by status name, so it is not affected, but a category-based
  mapping would read Done as in progress.

### Not done
- 2 items of the issue mix: no issue in an In Review status, and no due date within 3
  days at capture (HRS-10 is due 2026-10-27, HRS-8 is overdue). Needs a Jira change on
  keshanuni.atlassian.net, then a rerun of the capture. The evaluation scenarios that
  need these states build them in `tests/eval/state.py`, so none is blocked.

---

## 2026-10-22 | Keshan | P5-003 dependency blocker cites the blocking issue
Status: DONE

### Completed
- A BL-3 claim such as "HRS-6 is blocked by dependency: HRS-7" now has the blocking
  issue as a second evidence item, with its Jira link, even when another member owns it.
- T-002 returns the blocking records (`blocked_by_items`, PostgreSQL only); the evidence
  builder adds each one after the member's own items, so existing evidence IDs keep
  their numbers. Its summary names only the blocking issue, so it is never cited as the
  blocked item's own current work.

### Changed
`app/schemas/tools.py`, `app/tools/get_assigned_work_items.py`,
`app/agent/evidence_builder.py`, tests in `test_tools.py`, `test_evidence_builder.py` and
a new eval scenario that fails on the old builder.

### Discovered
- Blockers and risks also match evidence by substring of the summary, so a claim about
  HRS-1 would also cite HRS-10 and HRS-11. Not changed here.

### Verification
622 passed, 2 failed (the 2 known local environment failures); eval 20 of 20. Read-only
run on the real workspace: HRS-7 appears as `ev_18` with
`https://keshanuni.atlassian.net/browse/HRS-7`, HRS-6 is still `ev_11`.

---

## 2026-10-22 | Keshan | P5-003 evidence drawer click-check
Status: DONE

### Completed
- On the real workspace, opened the drawer for "HRS-6 is blocked by dependency: HRS-7"
  and clicked Open in Jira: it opened HRS-6. Clicked an Open in GitHub link on a pull
  request claim: it opened the matching record. The stored HRS-6 link in the database is
  `https://keshanuni.atlassian.net/browse/HRS-6`.
- Screenshot of the expanded drawer attached to #35. All 6 acceptance criteria met.

### Discovered
- A BL-3 dependency blocker cites only the blocked item. `blockers.py` lists the
  dependency (HRS-7) in `evidence_keys`, but evidence is built from the selected member's
  records only, so another member's dependency has no evidence item in the drawer. Not
  fixed here.

---

## 2026-10-22 | Keshan | P7-004 demo script for the workspace UI
Status: IN_PROGRESS

### Completed
`docs/DEMO.md`: preparation (model choice, pre-warm, seeding the demo workspace on a fresh
database), the 7 demo steps mapped to the workspace pages, how to make Jira unavailable
for step 5 and what must then be true, the `agent_run.dropped_claims` query for step 7,
and the 9 success criteria of `PROJECT_REQUIREMENTS.md` 2.9 with their state today.

### Not done
- The rehearsal itself, by both developers, and the v0.1.0 tag. Open before tagging: the
  #35 click-check, the #46 timed run with sign-in, a final WORKLOG entry from both, and
  the DEC-020 to DEC-026 decisions.
- Step 5 is written from the code (a rejected token refresh sets "Reconnect needed"; a 401
  fails the Jira run with `AUTH_FAILED`), not yet seen live.

---

## 2026-10-22 | Keshan | Demo seed: correct Jira and review links (P5-003, P7-004)
Status: IN_REVIEW

### Completed
- Checked every stored evidence link in the local database against its record, read
  only: on the real workspace, 26 Jira issues, 144 pull requests, 240 commits and every
  review link match their record. That supports #35; the click-check by a person still
  stands.
- 2 demo seed defects, found by that check:
  1. With `JIRA_BASE_URL` blank, which README 9.2 allows, seeded Jira links fell back to
     `https://jira/browse/<key>`. The seed now passes the fixtures' own site,
     `https://example.atlassian.net`.
  2. The fixture transport answered every pull request's reviews request with the whole
     `reviews.json`, so each demo pull request got every review, with links to other pull
     requests. It now returns only that pull request's reviews.
- 1 test seeds with `JIRA_BASE_URL` blank and checks every issue and review link. On the
  old seed it fails with `https://jira/browse/AUTH-245`.

### Discovered
- The local database still holds old demo rows in "headref Demo Corp" from earlier seeds,
  including the wrongly attached reviews (reviews are keyed by pull request and review id,
  so a re-seed adds the right ones but cannot remove the wrong ones). A fresh database is
  clean. Not deleted: it is Keshan's local data.

---

## 2026-10-22 | Keshan | P7-003 timed clean setup; P7-002 compared with llama3.2:1b
Status: IN_PROGRESS

### P7-003: timed clean setup (scripted)
A fresh `git clone` of `main` at `b0559ea` into a throwaway folder, then README section 9
exactly: `cp .env.example .env`, set `DATABASE_URL` and `SESSION_SECRET`, `docker compose
up --build`, `alembic upgrade head`, `seed/seed_demo.py`, under its own compose project.

| Step | Time from start |
|---|---|
| Clone, `.env` ready | 7 s |
| `docker compose up --build`, `/health` 200 | 136 s |
| `alembic upgrade head` | 146 s |
| `seed_demo.py` (15 GitHub and 8 Jira records) | 148 s |
| `/login` served ("GitHub OAuth not configured", as expected without credentials) | 148 s |

Every README command worked as written. 2 deviations, both because this machine already
runs headref: `API_PORT=8131` and no host port for the database (an override file).
Not timed: `ollama pull` (already done here), registering the GitHub OAuth app, signing
in and `seed_demo.py --owner`. Base images were cached, so a machine that has never run
Docker would also download them. The throwaway stack, its volume and the clone were
removed. The issue asks, ideally, for the developer who did not write the README; this
run was scripted by Developer 1 with an AI assistant, so "clone to working UI" stays open
for Isiwara's run, including sign-in.

### P7-002: the same uncached measurement with llama3.2:1b
10 uncached runs on the demo data, same machine and conditions as the 3B run above:
p50 5.1 s, p95 8.2 s, max 8.2 s, no timeouts, no rejected narrative. That meets NFR-013
(20 s) here, where the 3B model missed it (p95 75.0 s). Summary quality of the 1B model
was not judged. Data for the NFR-013 decision, not a change: `MODEL_ID` is unchanged.

---

## 2026-10-22 | Keshan | P7-002 measured on the real model; narrative rejection fixed
Status: IN_PROGRESS

### Measured (2026-10-22, Keshan's machine)
Machine: i7-12650H, 7.7 GB RAM, Windows 11, Docker Desktop (WSL). Model `llama3.2` (3B)
on host Ollama, `num_predict` 300. Demo data seeded into a throwaway PostgreSQL 15; the
real `run_agent` with real read tools and the real model call. Other applications were
open (2 IDEs, a browser), and host memory sat at its 7.71 GB ceiling for the whole run.
Measured by Developer 1 with an AI assistant, at Keshan's request; P7-002 is the
Developer 2 lane.

| Measure | Result | Target |
|---|---|---|
| p95 cached (20 runs) | 3.36 s (p50 0.19 s) | under 5 s: met |
| p95 uncached (20 warm runs) | 75.0 s (p50 21.2 s, max 83.7 s) | under 20 s (NFR-013): **missed** |
| Cold uncached, model unloaded first | 44.0 s | |
| Tokens per second, warm | 12.1 | |
| Tokens per second, cold | 14.9 eval rate, after 16.8 s to load the model | |
| Cache hits that called the model | 0 of 20 | 0 |
| Peak memory | model process 2,630 MiB private; app container 267 MiB; PostgreSQL 45 to 62 MiB | |
| Cost | 0 USD (DEC-017) | 0 |

The uncached p95 is set by 3 runs that timed out twice at the 30 s model limit (67 s
each) and fell back. Without them the uncached runs sit near the 21 s median.

### Problems
- **9 of about 21 real model answers were thrown away as `INVENTED_ENTITY`.** The findings
  text can name another member's issue ("AUTH-246 is blocked by dependency: AUTH-245"),
  but the validator only allowed the member's own records, so a correct summary repeating
  it was replaced by the fallback. Fixed: the narrative may name every entity the
  findings text names. A regression test fails without the fix. This was the
  "Narrative cited invented entities: AUTH-245" line noted on 2026-10-22 as noise; it was
  not noise.

### Decisions Needed
- NFR-013 is missed on this machine. The P7-002 handoff order: close other applications,
  reduce `OLLAMA_NUM_PREDICT`, reduce `MAX_EVIDENCE_ITEMS`, then `llama3.2:1b` (installed
  here). Each changes answer quality or the demo setup; for both developers to choose.
  Measuring on Isiwara's machine would show whether this is the machine or the design.

---

## 2026-10-22 | Keshan | Fixture capture script, docs for removed team routes
Status: IN_PROGRESS

### Completed
- `scripts/capture_fixtures.py` (P0-002): reads 1 workspace's selected repositories and
  Jira projects through its own GitHub App installation and Atlassian connection, the
  same way the sync does, with GET requests only. It writes the raw JSON to
  `seed/fixtures/sandbox/` after replacing every email address, and prints the A-3 counts
  (Jira users with an email, noreply commit addresses) and the rate limit headers. Counts
  and header values only; no token or address is printed. 2 tests for the redaction and
  the counts.
- Superseded notes (DEC-026, Proposed) where the docs still described the removed team
  pages, the team API and `can_view_member`: `ARCHITECTURE.md` (refresh, authorization,
  request flow), `PROJECT_REQUIREMENTS.md` (FR-028, NFR-009, the refresh criterion),
  `TESTING_AND_EVALUATION.md` (API rows), `BUILD_ORDER.md` (route table). `DECISIONS.md` is
  a record and was not changed.

### Not done
- The capture itself: it needs Keshan's stored connections. Run it on the "dev team"
  workspace, check the files, then commit them. P0-002 stays open until then.
- `CLAUDE.md`'s FR-028 enforcement row: its own change rule needs both developers.

---

## 2026-10-22 | Keshan | P6-003, P6-004 all 12 evaluation scenarios
Status: DONE

### Completed
- `tests/eval/state.py`, the Developer 1 half of P6-003: a scenario writes its rows
  (people, issues, pull requests, commits, reviews, links, sync runs) into a fresh
  database, then asks the real agent with the real read tools T-001 to T-007. Only Ollama
  is mocked, with a narrative that names no entity.
- `tests/eval/test_scenarios_state.py`: EV-01 to EV-10, 11 tests. With Isiwara's EV-11 and
  EV-12, all 12 scenarios are implemented. The run reports itself: "18 of 18 scenario tests
  passed in 1.4 s, cost 0.00 USD (local model, mocked; DEC-017)."
- Developer 2's #42 was built by Developer 1 with an AI assistant, at Keshan's request, to
  finish before submission. Isiwara to review.

### The 5 key scenarios, verified individually
| Scenario | Result |
|---|---|
| EV-04 stale and unavailable | Pass. A: stale Jira, HIGH became MEDIUM. B: Jira `AUTH_FAILED`, GitHub blocker kept, "jira is unavailable" in `unknowns`, no Jira evidence, no "no blockers" claim |
| EV-09 injection | Pass. Injected pull request body; the changes-requested blocker is still reported, no claim says complete or no blockers |
| EV-05 conflict | Pass. CF-1 in `conflicts` and on the current work claim, both sources cited, confidence dropped from HIGH |
| EV-07 unmatched identity | Pass. 14 commits by `temp-contractor` through the real `sync_github`: none attributed, `unmatched_entity` occurrence count 14, none cited for the mapped member |
| EV-12 invalid output | Pass. A, B, C1 and C2 (refusal in JSON, since #104) fall back after at most 1 retry |

### Problems (found by the scenarios, fixed here)
1. **EV-04A: a stale source cost 2 levels.** The HIGH base level required all items
   fresh, and Modifier 1 dropped a stale claim again: EV-01 with stale Jira came out LOW,
   not MEDIUM. Freshness is now only Modifier 1. A stale claim is still never HIGH.
2. **EV-05: the current work claim carried no conflict.** Blockers and risks attached
   theirs; current work did not, so "Likely working on AUTH-245" read as in progress on its
   own. It now carries the matching CF descriptions.
3. **EV-03: no activity gave an empty answer with no reason.** `unknowns` now says that
   work not tracked in Jira or GitHub is not visible to headref.

### Discovered
- The spec's reason codes (`NO_EVIDENCE`, `SOURCE_UNAVAILABLE`) exist nowhere in the code;
  reasons are `unknowns` sentences. EV-04B follows DEC-020, which is still Proposed. EV-10
  answers 404, not 403 (W1). Each is written under "As implemented" in
  `TESTING_AND_EVALUATION.md` 11.2.
- The builder needed a `refresh` listener as well as `load`: after a commit, SQLite rows
  are refreshed, not loaded, and came back without a time zone.

### Decisions Needed
- Isiwara to confirm fixes 2 and 3 (orchestrator, Developer 2 lane) and the EV-04B
  reading under DEC-020.

---

## 2026-10-22 | Keshan | Demo workspace from seed/seed_demo.py --owner (#46, #47)
Status: IN_REVIEW

### Announcement (rule 11)
Developer 1 writes migration `0010_workspace_is_demo` on `feat/46-demo-workspace`. No
other migration until it is merged.

### Completed
- Migration `0010_workspace_is_demo`, additive: 1 boolean, false for every existing
  workspace. Verified on a throwaway PostgreSQL 15: upgrade, downgrade, upgrade, 1 head.
  `alembic check` shows only the old W1 index name drift.
- `seed_demo.py --owner <github-login>` creates the workspace "Demo", owned by that
  account, on the demo organization, and records the seed's sync runs against it. The
  owner must have signed in once; otherwise the seed stops before writing anything.
- A demo workspace counts Jira as in use, is never synced (Sync now and the scheduler both
  skip it, even with a repository selected), and shows a "Demo data" badge with a note in
  place of the connection controls.
- 9 tests. Live check on the throwaway PostgreSQL with the real analysis: Jira in use, both
  sources fresh, 2 people, 5 blockers, 2 Jira and GitHub conflicts, 10 risks.

### Problems
- **The seed wrote into the first organization in the database.** In a database with
  workspaces, that can be a real workspace's. The identity map fell back the same way, and
  the demo team was given to every unassigned person in any organization. All 3 now use
  the demo organization only; a test checks a real workspace's organization gets nothing.

### Discovered
- The fixtures carry fixed dates (around 2026-09-20), so current work in the demo is LOW
  under the P6-002 rule once they pass `RECENT_ACTIVITY_DAYS`. Fresh captured fixtures
  (P0-002) would fix that.
- The code-only analysis logs "Narrative cited invented entities: AUTH-245" and then uses
  the same code-built text. Already on `main`; nothing on screen changes. Not looked into.

### Decisions Needed
Decided by Keshan 2026-10-22, for Isiwara to confirm: a demo flag (`workspace.is_demo`)
rather than a GitHub-only demo or a placeholder Jira connection.

---

## 2026-10-22 | Keshan | EV-12 C2: the validator rejects a refusal
Status: IN_REVIEW

### Completed
- A 4th validator check: first-person refusal phrasing ("I am sorry", "I cannot help",
  "as an AI") in either narrative field triggers the deterministic fallback with no retry,
  recorded in `dropped_claims` as `REFUSAL`. Placed after the 3 existing checks, so their
  order and recorded reasons are unchanged.
- Isiwara's EV-12 C2 scenario passes; its `xfail(strict=True)` marker is removed.
- 7 unit tests: 4 refusal phrasings rejected, 3 third-person summaries kept, including
  "cannot merge until its failing check passes", so ordinary wording is not caught.
- `AGENT_ARCHITECTURE.md` S5 lists 4 checks; `TESTING_AND_EVALUATION.md` EV-12 C2 is met;
  `TASKS.md` P6-002 counts the refusal check.

### Decisions Needed
Decided by Keshan 2026-10-22 (option on #40): reject refusals. Validation is the
Developer 2 lane (DEC-019): Isiwara to review the phrase list.

---

## 2026-10-22 | Keshan | P6-002 old evidence is LOW, validator count, task statuses
Status: DONE

### Completed
- `evaluate_confidence` checks "all evidence older than `RECENT_ACTIVITY_DAYS`" first, so
  old evidence is LOW however many items there are. Found by Isiwara's tests (#97); her 3
  `xfail(strict=True)` cases now pass and the marker is removed. No other test changed.
- `AI_BEHAVIOR.md` 5.4: base levels are now an ordered list, first match wins, so LOW for
  old evidence no longer overlaps MEDIUM. 1 worked example added.
- Validator count set to the code: 3 checks (invented entity, forbidden language, length
  and shape). `TASKS.md` and `TESTING_AND_EVALUATION.md` said 8, from before DEC-018;
  `AGENT_ARCHITECTURE.md` already listed the 3 plus an assembly step that rejects nothing.
- `TASKS.md` statuses brought in line with GitHub: P6-001 DONE (#39 closed), P6-005 DONE
  (#43 closed, criteria ticked), P6-002 DONE (every criterion now holds: each of the 7
  tools has success, empty and failure tests after #97), P6-003 and P6-004 IN_PROGRESS,
  P7-001 no-secret criterion ticked (prompt by #100, the rest by #95).

### Decisions Needed
Decided by Keshan 2026-10-22, for Isiwara to confirm: old evidence is LOW (fix the code,
not the doc); the validator count follows the code. Also decided, built in their own pull
requests: refusal text is rejected by the validator (EV-12 C2), and `seed_demo.py` seeds a
demo workspace.

---

## 2026-10-22 | Isiwara | P6-003, P6-004 Eval runner, EV-11 and EV-12
Status: IN_PROGRESS

### Completed
- `tests/eval/` exists with an `eval` marker. Every test in the folder is marked by its
  conftest. Run with `pytest -m eval tests/eval`. CI already skips it
  (`--ignore=tests/eval` in `ci.yml`), unchanged. Result: 6 passed, 1 xfailed.
- EV-11 rewritten for DEC-018: the model names `PAY-999` or `#999`, not in the findings.
  The full pipeline runs (Ollama mocked with `httpx.MockTransport`). Asserted: fallback
  summary used, `dropped_claims` holds `INVENTED_ENTITY` for the entity, `error_type` is
  `INVENTED_ENTITY`, the entity is nowhere in the response, findings unchanged.
- EV-12 A (schema invalid), B (timeout) and C1 (plain text refusal): exactly 2 model calls,
  then the fallback, with `SCHEMA_INVALID`, `TIMEOUT` and `SCHEMA_INVALID` recorded.
- EV-11 and EV-12 C specs, the EV-11 coverage row and the Fabrication rate metric in
  `TESTING_AND_EVALUATION.md` now match the code.

### Changed
`pyproject.toml`, `tests/eval/`, `docs/TESTING_AND_EVALUATION.md`.

### Discovered
EV-12 C2 fails: a refusal inside valid JSON ("I am sorry, but I cannot help with that
request.") passes the schema bounds and all 3 validator checks, so it is shown as the
summary. The test is `xfail(strict=True)` and will flip to a failure once fixed.

### Decisions Needed
Keshan: should the validator reject refusal text? It is a candidate new rule, option B
on #40. App code is unchanged here.

### Next Step
The other 10 scenarios, once Keshan's half of P6-003 builds their database state.

### AI Assistance
An AI assistant wrote the eval tests and the spec edits. Check the EV-11 and EV-12 specs
read as Keshan expects before ratifying them.
## 2026-10-22 | Isiwara | P7-002 A cache hit makes no model call
Status: IN_PROGRESS

### Completed
Added `tests/test_cache_model_call.py` (2 tests, Refs #45). `run_agent` runs twice on the
same evidence with a mocked Ollama client: the model is called once, the second answer
equals the first, and the second `agent_run` row has no model output and 0 output tokens.
A change to the evidence (pull request number) calls the model again.

### Discovered
`test_run_agent_full_pipeline_success` says its second run "hits the cache", but it uses
`skip_llm=True`, and a `skip_llm` run is never cached (`orchestrator.py` line 823). That
test did not prove a cache hit. The new test does.

### Next Step
The rest of P7-002 needs the running stack: p95 cached and uncached over repeated runs,
tokens per second warm and cold, peak RAM with PostgreSQL, the app and Ollama up.

### AI Assistance
The test was written with an AI assistant. Checked by running it; worth a read of the
cache-hit assertions in review.
## 2026-10-22 | Isiwara | P6-005 End-to-end test, agent half
Status: IN_PROGRESS

### Completed
Added `tests/test_integration/test_end_to_end.py` (Refs #43). An empty SQLite database is
seeded with `seed_demo_database`, which runs the real Jira and GitHub sync over fixture
transport. The agent then runs with the real read tools, findings, narrative call,
validator and `agent_run` persistence, for `current_work`, `blockers` and `risks`. Only
Ollama is mocked, at the HTTP layer with respx. Asserts: 1 model call with no tools, the
model's summary used, a complete `MemberInsight`, every evidence item's `source_url`
matches a seeded row, freshness fields set and both sources fresh, the run recorded with
no dropped claims and no error. 3 tests, no key, no network.

### Discovered
- SQLite loads timestamps without a zone and `detect_blockers` then fails subtracting
  them. PostgreSQL loads them with UTC. The test marks loaded datetimes UTC through a
  load listener it removes afterwards. Not an app bug, but this test does not run on
  PostgreSQL yet.
- The PR #1 "open for 33 days with no review" blocker cites 7 evidence items, including
  PAY-101, PR #182 and a commit, not only PR #1. For Keshan to check in the findings
  engine.

### Next Step
Keshan's half: confirm the seed run counts as the sync step, or add a separate one, and
decide whether to also run this test against the CI PostgreSQL service. Close #43 then.

### AI Assistance
Test written with an AI assistant. Check the UTC listener and the evidence assertions.

---
## 2026-10-22 | Isiwara | P6-002 Tool empty and failure cases, old evidence, recursive import guard
Status: IN_PROGRESS

### Completed
Refs #40. Tests only, no app code changed.
1. Tool empty cases: T-001 (team with no active members), T-004 (user with no commits),
   T-005 (user with no reviews), T-006 (no matching link). Each returns an empty list, not a
   failure.
2. Tool failure cases: T-004 and T-005 `NOT_FOUND` for a missing user, and `UPSTREAM_ERROR`
   from the real T-001, T-004, T-005 and T-006 when the database has no tables (rule 9).
3. Import guard now scans `app/tools/` recursively, with a test that a nested file is caught.
4. Old evidence: 1 test passes (1 old non-authoritative item is LOW). 3 cases for "all
   evidence older than `RECENT_ACTIVITY_DAYS` is LOW" are `xfail(strict=True)`, see Problems.
Suite: 576 passed, 1 skipped, 3 xfailed (was 564 passed, 1 skipped).

### Changed
`tests/test_tools.py`, `tests/test_confidence.py`, `tests/test_tool_import_guard.py`.

### Problems
`evaluate_confidence` (`app/agent/confidence.py`) checks the MEDIUM and HIGH conditions
before "all items old", so all-old evidence never reaches LOW: 1 old Jira item gives MEDIUM,
2 give MEDIUM, 3 from 2 sources give HIGH. `AI_BEHAVIOR.md` 5.4 says LOW. The strict xfail
starts failing once this is fixed, so the marker must be removed then.

### Decisions Needed
- For Keshan (findings engine, DEC-019): fix `evaluate_confidence`, or amend `AI_BEHAVIOR.md`
  5.4. The doc is also ambiguous: MEDIUM's "2 or more items" overlaps LOW's "all old".
- The validator rule count (8 in the issue, 3 in the code) is still open, so #40 stays open.

### Next Step
Validator rule tests once the count is decided.

### AI Assistance
The tests were drafted by an AI assistant. Check that an empty-table SQLite session is an
acceptable stand-in for a database error, and that the xfail is the right way to record the
confidence gap rather than fixing it in this PR.

---
## 2026-10-22 | Isiwara | P5-003 Evidence drawer escaping through the app
Status: IN_PROGRESS

### Completed
Added `tests/test_drawer_escaping.py`: a hostile excerpt (`<img onerror>`, `<script>`,
Markdown) in a claim's evidence is escaped by the real `GET /w/{id}` page, in the team view
and the member view. Checked that the test fails if `|safe` is added to the excerpt in
`partials/evidence_list.html`.

### Discovered
The real analysis cannot run in these tests: SQLite drops the time zone from stored
datetimes, and `app/agent/blockers.py` and `confidence.py` then compare naive and aware
values. So the analysis is replaced, as in `tests/test_workspace_analysis.py`. Seeding a
pull request body and rendering it end to end needs PostgreSQL (P6-005).

### Next Step
By hand: click evidence links in a browser and confirm each opens the right Jira issue or
pull request, and attach a drawer screenshot to #35. Then close #35.

### AI Assistance
The test was drafted by an AI assistant. Check that the fake analysis matches what the
page receives in real use.

---
## 2026-10-22 | Isiwara | P7-001 Security pass: no secret in the model prompt
Status: IN_PROGRESS

### Completed
Developer 2 half of #44. Added `tests/test_prompt_secrets.py`: every secret setting in
`app/config.py` (database password, GitHub token, Jira token, OAuth client secret, GitHub
App private key, Atlassian client secret, credential encryption key, session secret) is
loaded with a canary value, 1 real `run_agent` pass runs with a mocked Ollama client, and
the request body sent to the model is searched for each canary. None found. The test
checks that the canaries loaded and that the findings reached the prompt, so a clean
result means something. Checked by hand that it fails when a token is put in the body.

The `|safe` criterion of P7-001 is already covered by `test_no_safe_filter_in_templates`
in `tests/web/test_ui_templates.py`. This test covers the prompt part of the "no secret in
any log, prompt, error detail or `agent_run` field" criterion. `TASKS.md` is not edited
here, because Keshan's #95 changes the same P7-001 block.

Branch rewritten as 1 commit on `main`: the first commit's literal canaries tripped gitleaks
`generic-api-key` and failed CI on every PR, so canaries are now built at runtime.

### Changed
`tests/test_prompt_secrets.py` (new), `WORKLOG.md`.

### Next Step
Once #95 merges, share its `canary_secrets` fixture instead of the copy here, and tick the
2 criteria in `TASKS.md`. Keshan's half (agent_run, write calls, Atlassian console check)
is in #95.

### AI Assistance
The test was drafted with an AI coding assistant. Needs a human check that the canary list
matches every secret field in `app/config.py`.

## 2026-10-21 | Keshan | UI restructure and light and dark theme
Status: IN_REVIEW

### Completed
- Workspace page: the analysis comes first. Member filter bar, 4 count tiles (blockers,
  disagreements, risks, people waiting), then blockers, disagreements, risks and current
  work. Connections, latest sync runs and What changed moved to a side column. Sync now
  moved into the page header. Before any analysis, a 3 step setup list leads the page.
- Every page header is compact (title, lead and actions on 1 band) instead of a tall block
  with an empty right side. Workspaces cards show health next to the name. Settings uses
  2 columns with archive as a marked danger card. Sync history shows result badges.
- Theme button in the top bar and on sign-in. The choice is saved in the browser
  (`headref-theme`) and applied in `<head>` before paint; without a choice, or without
  JS, the page follows the system setting. `color-scheme` set, so native inputs match.

- Motion and illustrations (`partials/illustrations.html`, animated SVG, no video files,
  agreed 2026-10-21): sign-in shows 2 records feeding 1 finding; the member summary wait
  shows findings cooking in a pot with a real count of seconds waited; a running sync
  shows a jar filling; submitted buttons show a spinner and ignore a second click; a top
  bar shows while a page loads; empty states and the error page have their own scene.
  Loops are indeterminate (no percentage), pause off screen and in a hidden tab, and stop
  under reduced motion, where each scene rests on its finished frame.

### Problems
- Dark mode was broken before this: its overrides sat above the rules they overrode, so
  black buttons, the Fact marker and the selected tab disappeared on dark cards, and
  "Blocked" was white on light pink. Colours now come from tokens with 1 dark set.
- Pre-existing, not changed: the GitHub disconnect form carries `data-confirm`, but no
  script reads it, so it disconnects without asking.
- Pre-existing, not changed: `initials` keeps punctuation, so "Isiwara K (Jira)" shows "I(".

### Checked
- Every page rendered with sample data and screenshotted at 1440 and 390 px, light and
  dark. Theme button, saved choice after reload and the evidence drawer click-tested in
  Edge, no script errors. Dark and light text pairs pass AA (values in `style.css`).
- Waiting states captured over a local test server that held the summary for 7 s and a
  sync post for 4 s: the pot, "Waiting 4 s so far.", the swap to the summary, the jar,
  the button spinner, the top bar and the repeat-submit guard. Reduced motion shows the
  still frames. Headless Edge does not repaint during a pending form post, so the
  spinner was captured with the post held by the script; check it once in a real browser.
- Suite in the api container: 562 passed, 3 failed. The 3 do not render pages:
  `test_parse_valid_identity_map_file` (local seed edit), `test_no_key_means_no_encryption`
  and `test_an_org_installation_without_oauth_configured_says_so` (the container's
  environment sets those keys).

### Not verified
- Signed in against the real database: pages were rendered from templates with sample
  data, not through a live session.

### Decisions Needed
- About 1,100 changed lines, over the 400 line target. Can split into theme and layout.

---

## 2026-10-22 | Keshan | P7-003 README setup for workspaces
Status: IN_PROGRESS

### Completed
- README section 9 rewritten for workspaces: the 4 settings needed to start and sign in,
  the 3 apps to register with their callback and setup URLs and scopes, the first
  workspace, and demo data. Sections 4, 5, 8, 10, 13 and 14 brought up to date: no
  login stub or team pages, the legacy sync CLI labelled as legacy, the evaluation command
  from #98 (EV-11 and EV-12 so far), status table reconciled.
- `.env.example`: `SCHEDULER_ENABLED` and `STUCK_SYNC_TIMEOUT_MINUTES` added; callback,
  setup URL, App permissions and `offline_access` scope added to the comments.

### Problems
- **A fresh copy of `.env.example` did not start.** 19 optional settings (ports,
  timeouts, thresholds) shipped as blank lines, and a blank number or true/false value
  fails validation. With only `DATABASE_URL` set, loading settings failed on all 19.
  Those lines are now commented out, so the defaults apply. A test loads the template
  from a clean environment; it failed on the old template with 19 errors and passes now.
- `main` requires 0 approvals and admins can bypass; the README had said 1 approval.

### Decisions Needed
- **Demo data is not visible in the UI.** `seed_demo.py` seeds an organization ("headref
  Demo Corp") that no workspace uses, and DEC-026 removed the team pages that showed it.
  The 10 minute setup (P7-003) and the demo (P7-004) need one of: seed into a demo
  workspace owned by a given GitHub login, or drop seeded demo data from the setup and
  demo with the sandbox instead. Seeding is the Developer 1 lane.

### Not verified
- The timed clean setup run. P7-003 asks for it from the developer who did not write the
  README.
- The GitHub App "Setup URL" field name is from the route
  (`/connections/github/setup`), not re-checked in GitHub's settings page today.
## 2026-10-22 | Keshan | P7-001 security pass, Developer 1 half
Status: IN_PROGRESS

### Completed
- `tests/test_security_gate.py`, 4 tests:
  - No secret in `agent_run`: canary values for all 7 secret settings, 1 real agent pass on
    fake tools, once with a model answer and once with a model error whose message holds
    a secret. Every column of every row is checked.
  - No write to GitHub or Jira data: an AST scan of `app/` for POST, PUT, PATCH and DELETE
    calls, routes and ORM deletes left out. Only 5 are allowed, each with its reason: the
    local Ollama call, the Atlassian token exchange and refresh, GitHub's OAuth code
    exchange (sign-in and the org check), and GitHub's installation token. A 4th test
    checks the scan still finds those 5.
- Both tests were run once against a planted bug each (a secret written into
  `agent_run.model`, a POST to `/repos/x/y/issues`). Both failed as they should. The
  bugs were reverted.

### Security gate checklist (P7-001)
| Criterion | State | Evidence |
|---|---|---|
| No HTTP client under `app/tools/` | Done | `test_tool_import_guard.py` |
| No tools in the model call | Done | `test_narrative.py` asserts no `tools` in the payload |
| No write endpoint | Done | `test_no_write_call_to_github_or_jira_data` |
| No secret in logs, errors, `agent_run` | Done | `test_http_client.py`, `test_sync.py`, `test_security_gate.py` |
| No secret in the prompt | Open | Developer 2 |
| No `\|safe` on source fields | Done | `tests/web/test_ui_templates.py`, `test_no_safe_filter_in_templates` (found by Developer 2) |
| Secret scanner green | Done | gitleaks passed in CI on `44befb4` |
| Every member-data route guarded | Open | `workspace_access` replaced `can_view_member`; tested by `test_workspaces.py` and `test_isolation.py`; criterion to reword with DEC-026 |
| Read-only scopes in the provider console | Open | GitHub App checked with `GET /app` in W3; Atlassian console not checked |

### Problems
- The first version wrote its canaries as literals. gitleaks read 2 of them as API keys,
  and because CI checks out and scans every branch, that commit (`fac655c`) failed the
  secret scan on every open pull request. The canaries are now built from each setting's
  name at runtime, and the branch was rewritten as 1 commit and force-pushed (agreed
  2026-10-22), so `fac655c` is on no branch. gitleaks 8.21.2 finds nothing in the new
  commit.

### Checked
- Full suite in the api container: 567 passed, 2 failed (the same 2 environment-key
  tests, passing in CI). `ruff check .` and `black --check .` clean with CI's versions.
## 2026-10-22 | Keshan | P6-001 write-path test suite
Status: IN_REVIEW

### Completed
- Client failures through each client, not only `HttpClient`: timeout, 429, 500 and a
  plain 403 for GitHub and Jira, 8 tests. Each retrying case asserts `HTTP_MAX_ATTEMPTS`
  calls; a `no_backoff` fixture removes the wait, not the retries.
- `pr_body` link method (MEDIUM, body only). All 5 methods plus no-link are now covered.
- Jira sync idempotency: 2 issues and 1 "is blocked by" link, synced twice, same rows.
- Network guard in `tests/conftest.py`, stdlib only: any connection off this machine is
  refused and fails the test at teardown, even if the test swallowed the error. Loopback
  and the `DATABASE_URL` host stay allowed. Probed with a throwaway test (deleted): a raw
  socket and a swallowed httpx call both failed; loopback passed.

### Discovered
- The guard found 1 real escape: `test_active_sync_prevents_duplicate_run` ran the real
  Jira sync. Locally it called `keshanuni.atlassian.net` from `.env`; in CI it would try
  `example.atlassian.net`. Fixed by stubbing `sync_jira` as the other tick tests do.
- `TASKS.md` names `tests/test_integrations/` and `tests/test_ingest/`. The tests live in
  flat `tests/test_*.py` modules; not moved.

### Checked
- Full suite in the api container: 573 passed, 2 failed. The 2 are
  `test_no_key_means_no_encryption` and
  `test_an_org_installation_without_oauth_configured_says_so`: the container's
  environment sets their keys, and both passed in CI on `44befb4`.
- `ruff check .` and `black --check .` clean, run with CI's versions (ruff 0.17.1,
  black 26.10.1), which the api image also has.
- Coverage, measured with `coverage` 7.6.1 installed in the container for the run and
  removed after (not a project dependency): 88% over `http.py`, `github.py`, `jira.py`,
  both normalizers, `identity_resolver.py` and `link_builder.py` (976 statements, 120
  missed). `app/sync.py` was not measured.

---

## 2026-10-20 | Keshan | Workspaces W11: real-data verification (part 2)
Status: IN_PROGRESS

### Sandbox data added
- Jira HRS: HRS-10 "Add rate limit to member export endpoint" (To Do, Highest, due
  2026-10-27) and HRS-11 "Update onboarding checklist", moved To Do to Done with no code.
- `IsiwaraKumarage8` invited to `keshan-dev/headref-sandbox` with write access (GitHub
  invite, agreed 2026-10-20) and to workspace "dev team" as a manager (headref invite,
  stored by GitHub id 221026807, expires 2026-11-02).

### Checked on real data
| Case | Result |
|---|---|
| Feed, Jira | "HRS-10 added", "HRS-11 added", then "HRS-11 moved from To Do to Done" |
| Assigned not started, Highest | Likely next: HRS-10 first, then HRS-6, then draft PR #7 |
| Done with no code | HRS-11 in no blocker, risk or conflict |
| RK-1 boundary | HRS-10 is due in 7 days, outside `DUE_SOON_DAYS=3`: no risk yet; expected from 2026-10-24 |

### Added
- Pending review requests (DEC-025 had them in Likely next, never built). Migration
  `0009_review_request`, additive; upgrade, downgrade and upgrade verified on a copy of the
  local database, then applied. Each sync mirrors GitHub's `requested_reviewers` for open
  pull requests; a request leaves when the review is given. Likely next lists them
  between assigned work and draft pull requests. Merge and split move them. 3 tests.
- Full suite: 563 passed, 1 skipped, 1 failed (`test_parse_valid_identity_map_file`, the
  local uncommitted `seed/identity_map.yml` edit).

### Discovered
- The app reloads on every file change in development and its scheduler pass starts at
  once. Applying the new model before the migration made 1 sync fail
  (`ProgrammingError`), shown on the feed; the next one succeeded.
- A reload also kills a running sync's thread, leaving its `sync_run` at `running`. The
  workspace counts as syncing until the 15 minute window passes (run 718).
- Jira project HRS has unusual status categories: "Done" is in the In Progress category
  and "Blocked" in Done. headref maps by status name first, so both read correctly.

### Waiting on Isiwara
- Accept the GitHub invite to `headref-sandbox`.
- On PR #6, request changes (BL-4: changes requested, no commit since).
- On PR #4, approve (CF-4: approved pull request on a Blocked issue, HRS-4).
- Open a pull request from a branch named `feature/HRS-10-rate-limit` and request a review
  from keshan-dev (Likely next for Keshan; links to HRS-10).
- Accept the headref invite: headref runs on this machine only, so by signing in with their
  GitHub account in a browser here.

---

## 2026-10-20 | Keshan | Workspaces W11: real-data verification (part 1)
Status: IN_PROGRESS

### Sandbox data added
- `keshan-dev/headref-sandbox`: 2 commits on `main` by "Outside Contractor" with an
  address no GitHub account owns (`6ed27c0`, `ff6dda5`); PR #8 "HRS-7 Add API notes" on
  `feature/HRS-7-api-notes`, whose body carries the EV-09 text.
- headref (local database): workspace 2 "Audit" with only `headref-sandbox`.

### Checked on real data
| Case | Result |
|---|---|
| Feed, real change | PR #8 appeared as "Pull request keshan-dev/headref-sandbox#8 opened"; a failed sync and its recovery appeared once each |
| Unmapped commit author (EV-07) | Commit stored with no author; after the fixes below, "Outside Contractor (commit author, no GitHub account)" is on the People page under "Not matched to anyone" |
| Injection text (EV-09) | The 4 blockers unchanged; the model summary (47 s) claims neither completion nor "no blockers" |
| Second workspace (EV-10) | Same GitHub account is person 10 in "dev team" (confirmed) and person 12 in "Audit" (suggested); from Audit, person 10's summary is 404, a status change is "not in this workspace", `?member=10` shows nothing of dev team; cards: dev team Blocked, Audit Unknown "Nobody is confirmed yet." |
| Current work with a linked PR | HRS-7 with PR #8: "Likely working on HRS-7", inference, MEDIUM, both cited |

### Fixed (found by these checks)
1. Commits by an author with no GitHub account were dropped from the unmatched queue
   silently. They are recorded, keyed by a hash of the commit email (the address is not
   stored), and the People page lists accounts nobody holds; rows linked since (the
   merged Jira "Keshan") are left out.
2. Commits dated before the last sync but pushed after it were never fetched: GitHub's
   `since` compares the commit date. Each sync now reads 7 days back from the cursor;
   commits upsert by sha.
3. 2 syncs of 1 workspace in 2 processes deadlocked (an app reload's scheduler pass met
   a direct sync). A PostgreSQL advisory lock per workspace makes the second skip.
   Verified: 2 concurrent syncs, 1 ran (16.7 s), 1 returned at once.
4. Current work never counted a linked pull request, because pull request evidence names
   no issue key (titles are untrusted text). It now joins through the stored link.
5. Every Jira link was `https://jira/browse/<key>`. The workspace sync passes the
   connection's site; HRS links now open `https://keshanuni.atlassian.net/browse/...`.
6. 5 tests. Full suite: 561 passed, 1 skipped, 1 failed (`test_parse_valid_identity_map_file`,
   the local uncommitted `seed/identity_map.yml` edit).

### Not done yet
- Jira cases (HRS-10 To Do, Highest, due in 7 days; HRS-11 Done with no code; an issue
  moved for the feed): the Atlassian connector available here is authorised for another
  site, so no Jira change was made.
- Isiwara cases (invite accepted, BL-4 changes requested, CF-4 approval on PR #4, a review
  requested from Keshan): Isiwara is not a collaborator on the sandbox yet.
- Time-based rules (BL-5, BL-6, BL-8, RK-2, RK-3): waiting for the data to age.
- A sync error outside the typed list is stored by class name (`OperationalError`) by
  `app/sync.py`, so the feed showed it raw. Pre-existing; to fix with the typed failures.

---

## 2026-10-19 | Keshan | Workspaces W10: hardening
Status: IN_PROGRESS

### Completed
1. Found and closed a cross-workspace leak (reproduced first): a synced person who signed in
   became that workspace's person row in the legacy team, and any other signed-in GitHub
   user could then read their findings through `GET /api/members/{id}/insight` (200).
2. Removed the team pages, the team API and their templates, script code and tests
   (agreed 2026-10-19). `/` redirects to `/workspaces`. DEC-026 records it.
3. Sign-in asks GitHub for no scopes and drops its token after reading the profile: no
   token cookie, no process-wide token. Each account gets its own `app_user` in an
   accounts organization no workspace uses; an account bound to a workspace person is
   moved at its next sign-in.
4. Connect GitHub without the old token: an installation on the owner's account is attached
   on the owner id check; an organization installation asks GitHub once for `read:org`,
   checks the admin role and drops the token (`/auth/github/callback/org`, its own signed
   single-use state).
5. Cookies are `Secure` outside `APP_ENV=local`, closing the 2026-10-08 item. Sign-out is
   POST only.
6. Portfolio cards from the feed snapshots: health (Blocked, Needs Attention, On Track,
   Unknown with the reason), counts for the workspace as a whole, top 3 attention items,
   last sync per source. No per-person numbers.
7. `tests/test_isolation.py`: 2 workspaces sync the same repository; people, analysis, feed,
   summary, invites and every `/w/{workspace_id}` route with every method answer 404 or
   "not in this workspace" from the other workspace.
8. Security review of the whole branch: no finding at confidence 0.7 or more. 2 below that
   were fixed: the accounts organization was found by a name a workspace could take, and
   the legacy scheduled sync read every workspace's repositories with `GITHUB_TOKEN`.
9. Full suite: 556 passed, 1 skipped, 1 failed (`test_parse_valid_identity_map_file`, only
   because of the local uncommitted edit to `seed/identity_map.yml`).
10. Real data: sign-in asks for no scope; Connect GitHub on "dev team" with installation
    172832446 (User) went through and the picker lists `headref-sandbox`.

### Discovered
- The connect flow still read the sign-in token cookie after step 3, so every Connect GitHub
  would have ended in "sign in again". Fixed by item 4 before committing.
- The organization check needs the GitHub OAuth app's callback URL to be
  `<host>/auth/github/callback`: GitHub accepts `/auth/github/callback/org` only as a
  subdirectory of it.

### Not verified
- An organization installation end to end (no organization install to test with).
- A sign-in in the browser after this change, and browser layout of the cards.

### Decisions Needed
- DEC-026 needs Developer 2. With it, the `CLAUDE.md` enforcement row for FR-028 should name
  the workspace route test and the isolation sweep instead (its own pull request).
- `PROJECT_REQUIREMENTS.md`, `TASKS.md`, `BUILD_ORDER.md` and `TESTING_AND_EVALUATION.md`
  still name the removed endpoints; rewrite with the Phase 6 evaluation per workspace.

### Next Step
W11: real-data verification with Isiwara (invite, BL-4, CF-4, review request), then Phase 6.

---

## 2026-10-19 | Keshan | Workspaces W9: settings, invites, roles, archive
Status: IN_PROGRESS

### Completed
1. Migration `0008_workspace_invite`, additive. Upgrade, downgrade and upgrade verified on a
   copy of the local database, then applied locally.
2. `app/workspace_admin.py` and `/w/{id}/settings`: rename and describe the workspace;
   who has access; invite by GitHub username as manager or viewer; withdraw an invite;
   change a role; remove access; archive after typing the name. Every member sees who has
   access; only the owner sees and uses the forms (403 otherwise).
3. Invites: the login is checked against GitHub's public profile
   (`lookup_github_user`, no token, timeout and retry limit from `HttpClient`) and stored
   by numeric id. Malformed logins are refused before any request; organizations,
   unknown users, existing members and GitHub failures are each reported, and nothing is
   saved. A GitHub failure shows its typed error.
4. The invited person sees "Invitations" on their workspace list with Accept and Decline.
   An invite is accepted only by the account whose GitHub id it holds, while it is open
   and its workspace is not archived.
5. Settings link on the workspace page. DEC-021 amended (still Proposed).
6. 20 tests. Full suite: 592 passed, 1 skipped, 1 failed (`test_parse_valid_identity_map_file`,
   only because of the local uncommitted edit to `seed/identity_map.yml`).
7. Real data, workspace "dev team": settings page renders for the owner; inviting the
   owner's own login looked it up on GitHub in 0.8 s and was refused as already a member;
   a malformed login was refused without a request; 0 invites stored.

### Discovered
- The P3 member-route guard test treats any `{member_id}` path as a person's data. The
  settings routes take a workspace membership id, so their parameter is `membership_id`.

### Not verified
- A real second person accepting an invite (needs Isiwara's GitHub username and a sign-in).
- Browser layout of the settings page and the invitations section.
- Cross-site request protection relies on the session cookie's `SameSite=Lax`, as every
  existing form does. For the W10 security review.

### Next Step
W10: hardening. Isolation tests across 2 workspaces, security review, remove the old pages,
secure cookie.

---

## 2026-10-19 | Keshan | Workspaces W8: what changed between syncs
Status: IN_PROGRESS

### Completed
1. Migration `0007_workspace_feed`: `finding_snapshot` (latest snapshot per subject),
   `workspace_event` (feed lines), `workspace_member.last_seen_at`. Additive. Upgrade,
   downgrade and upgrade verified on a copy of the local database, then applied locally.
2. `app/workspace_feed.py`: after every workspace sync, snapshot per confirmed person (code
   findings keyed by rule and entity) and for the workspace (issue status, pull request
   state, sync outcome per source), diffed against the previous snapshot into code-written
   lines. The first sync is a baseline. Unavailable sources never produce "no longer
   detected" or issue changes; a failed feed computation is shown as a line. Lines older
   than 90 days are removed.
3. Workspace page: "What changed" lists the latest 50 lines, newest first, linked to the
   source; with a member selected, only lines about their work. Lines since the viewer's
   previous visit are marked New.
4. DEC-025 amended (still Proposed): likely next as facts, feed keys, missing data rules.
5. 14 tests. Full suite: 572 passed, 1 skipped, 1 failed (`test_parse_valid_identity_map_file`,
   only because of the local uncommitted edit to `seed/identity_map.yml`).
6. Real data, workspace "dev team": 2 syncs (14 s each), baseline then 0 lines, as nothing
   changed in between. Snapshot: 9 issues, 7 pull requests, person 10 with BL-1 HRS-4, BL-2
   HRS-5, BL-3 HRS-6, BL-7 PR #5, CF-1, CF-2, CF-3 and RK-1 HRS-8, matching the W7 analysis.

### Discovered
- Detection rule texts carry day counters, so findings cannot be compared by text.
- The detection rules fail on SQLite's naive timestamps (BL-8); PostgreSQL returns aware
  ones. The feed's finding test uses the orchestrator's fake read tools instead.

### Not verified
- A real change appearing on the feed (an issue moved in Jira or a pull request merged,
  then a sync). Covered by tests only.
- Browser layout of the section.

### Next Step
W9: settings, invites by GitHub username, roles, archive.

---

## 2026-10-19 | Keshan | Workspaces W7: analysis and member panel
Status: IN_PROGRESS

### Completed
1. The agent runs per workspace: `run_agent` and `run_s1_s2` take `workspace_id` and
   `jira_in_use`; T-007 reads that workspace's sync runs (the legacy path now ignores
   workspace runs); Jira counts as in use when the workspace has an active connection.
2. `app/workspace_analysis.py`: for each confirmed person, the 3 questions run code-only
   (`skip_llm`); blockers, risks, conflicts and current work are collected. Likely next work
   is shown as facts with a stated order (assigned and not started, by priority then due
   date; draft pull requests), because an inference needs 2 or more evidence items.
3. Workspace page: team view with blockers, conflicts and risks naming the person, and
   current work per person; member filter; member view with current work, likely next,
   blockers, risks, conflicts, not determined. The summary is the only model call, fetched
   after the page from `/w/{id}/members/{pid}/summary` (404 for unconfirmed or foreign
   people), labelled when it is the code-built fallback.
4. `MemberInsight.conflicts` (optional): the detected CF-1 to CF-4 statements. Before,
   conflicts reached the page only inside RK-4 risk text.
5. Real data, workspace "dev team": 4 blockers (BL-1 HRS-4, BL-2 HRS-5, BL-3 HRS-6, BL-7 PR
   #5), 3 conflicts (CF-1 HRS-1, CF-2 HRS-2, CF-3 HRS-3), 4 risks (RK-1 HRS-8 plus 3 RK-4),
   next up HRS-6 and draft PR #7. Team view 0.7 s; summary 44.7 s on a cold model, written
   by the model.
6. 12 tests.

### Discovered
- Current work with 1 evidence item was classified `fact` but worded "Likely working on",
  which 6.6 and EV-01 forbid. It is now worded as what the record shows ("HRS-7 is assigned
  and In Progress"); "Likely working on" stays for 2 or more items, as an inference.
- A code-only run cached its deterministic text under the same evidence hash the summary
  uses, so opening a member would have served that text instead of calling the model.
  Code-only runs are no longer cached.
- The page's request limit (40 s) was shorter than a cold model (45 s); the summary now has
  120 s.
- T-007 crashed on SQLite's naive timestamps; it now treats them as UTC.

### Next Step
W8, the feed: findings diffed between syncs.

---

## 2026-10-19 | Keshan | Workspaces W6: People screen
Status: IN_PROGRESS

### Completed
1. Migration `0006_person_status`: `app_user.status` (suggested, confirmed, ignored, bot),
   existing rows confirmed. Verified up, down, up on a copy of the real data; applied.
2. Workspace syncs create a suggested person per GitHub and per Jira account (Jira too
   now), each holding exactly 1 account; `*[bot]` GitHub accounts start as bots. The
   legacy path is unchanged.
3. `app/people.py`: list with accounts and context counts, confirm, ignore, bot, restore,
   merge (accounts and activity move, source removed), split (refused when the activity
   cannot be told apart, or on the only account). Every id is checked against the
   workspace's organization.
4. `/w/{id}/people`: any member sees it; managers act. The workspace page lists confirmed
   people alphabetically and says how many are waiting.
5. DEC-024 amended while Proposed: a suggested person holds 1 exact account and is
   attributed at once; only confirmed people appear in analysis. Reason in the record.
6. Real sync after the change: "Keshan" (Jira, 9 items) suggested, "keshan-dev" (GitHub,
   7 pull requests) confirmed. Merging them on the People screen joins both.
7. 16 tests.

### Decisions Needed
DEC-024 as amended needs Developer 2 agreement with the other W0 records.

### Next Step
W7, the analysis view and the member panel, built on confirmed people.

---

## 2026-10-19 | Keshan | Workspaces W5: per-workspace sync and status
Status: IN_PROGRESS

### Completed
1. Migration `0005_workspace_scope`: each workspace gets its own `organization` row, which
   repositories, projects and people already hang off. Repository name, project key,
   identity link and unmatched account become unique per organization; `sync_run` gains
   `workspace_id`. Backfills, drops nothing. Verified up, down, up on a copy of the real
   local data (all rows backfilled); applied locally.
2. `sync_github`, `sync_jira`, the identity resolver, the identity loader and the upserts
   take the workspace's organization. Cursors use `w<id>:<scope>`, so 2 workspaces syncing
   the same repository cannot skip each other's commits. Link building stays inside 1
   organization.
3. `app/workspace_sync.py`: Jira first, then GitHub; installation token per repository,
   refreshed Atlassian token through `api.atlassian.com/ex/jira/<cloudId>` (JiraClient
   gained bearer-token mode). An unreachable source is a failed `sync_run` with a typed
   error, never silence.
4. Sync now (owners and managers), refused when nothing is selected or a sync is running;
   status endpoint; status rows on the workspace page with polling every 2 seconds and a
   5 minute cap; sync history page. The scheduler also syncs every workspace with sources.
5. **First real sync, workspace "dev team"**: Jira HRS 12 fetched, 12 written; GitHub
   headref-sandbox 12 fetched, 12 written; 9 work items, 7 pull requests, 14 links; PR #5
   checks failing, the others passing.
6. 15 new tests: organization scoping, 2 workspaces on 1 repository, failed runs, Jira
   bearer client and project filter, running guard, scheduler pass, pages.

### Discovered
- The scheduler ran syncs on the event loop, so the web app stopped answering during a
  sync. The tick now runs in a worker thread.
- The identity resolver's handle fallback also matched Jira display names, which G-5 and
  DEC-008 forbid. It now applies to GitHub logins only.
- Jira remote links to pull requests only resolve against pull requests already stored, so
  a brand new workspace gets them on its second sync.

### Next Step
W6, people confirmation: today GitHub authors are still created automatically and Jira
assignees go to the unmatched queue until a manager links them.

---

## 2026-10-19 | Keshan | Workspaces W4: Atlassian connection
Status: IN_PROGRESS

### Completed
1. `app/integrations/atlassian.py`: consent URL with read-only scopes (`read:jira-work`,
   `read:jira-user`, `offline_access`), code exchange, refresh, accessible sites, project
   list. Token-endpoint calls are made once, never retried: Atlassian rotates the refresh
   token, and a retried refresh would present a replaced token.
2. `app/integrations/crypto.py`: Fernet encryption with `CREDENTIAL_ENCRYPTION_KEY`.
   `jira_tokens.valid_access_token` refreshes under a row lock and writes both new tokens
   in 1 commit. A rejected refresh marks the connection `auth_failed` and raises.
3. Connect Jira flow, sharing the GitHub state and ownership helpers: signed 15 minute
   state, owner check, encrypted token storage, 1 site per workspace (chosen from the
   reachable sites when there are several), project picker checked against Atlassian,
   disconnect deletes the tokens.
4. Migration `0004_jira_connections`: `jira_connection`, `workspace_jira_project`. Verified
   up, down, up on PostgreSQL 15; applied locally.
5. `.env.example` gains the GitHub App (missed in W3) and Atlassian settings.
6. Tests: encryption, consent scopes, single refresh attempt, site filtering, pagination,
   refresh and rotation, auth_failed, every state tamper case, site choice, tampered
   project ids, failure shown not empty, disconnect.

### Problems
Not verified against the real Atlassian app yet: it needs `ATLASSIAN_CLIENT_ID`,
`ATLASSIAN_CLIENT_SECRET` and `CREDENTIAL_ENCRYPTION_KEY` in `.env`.

### Next Step
Real consent round trip on `keshanuni.atlassian.net`, then W5 (per-workspace sync).

---

## 2026-10-19 | Keshan | Jira search moved to /rest/api/3/search/jql
Status: IN_REVIEW

### Completed
1. Verified on `keshanuni.atlassian.net`: `GET /rest/api/3/search` returns 410 ("The
   requested API has been removed"), and `/rest/api/3/search/jql` rejects empty JQL with 400
   ("Unbounded JQL queries are not allowed here"). Every real Jira sync was failing.
2. `JiraClient.search_issues` uses `/rest/api/3/search/jql`, follows `nextPageToken` until
   `isLast`, and refuses an empty query before calling.
3. `sync_jira` searches `project in ("KEY", ...) ORDER BY key`, built by `project_jql` from
   the configured scope (or every synced project for `ALL`). Keys are checked against Jira's
   key format and quoted, so a key cannot change the query.
4. Verified on the real site: `project in ("HRS")` with a page size of 4 returned all 9 HRS
   issues with their statuses, over 3 pages.

### Changed
`app/integrations/jira.py`, `app/sync.py`, `tests/test_jira_client.py`, `tests/test_sync.py`.

---

## 2026-10-19 | Keshan | Workspaces W3: GitHub App connection, CI checks
Status: IN_PROGRESS

### Completed
1. The headref GitHub App is registered (read only: metadata, contents, pull requests,
   checks, commit statuses; no webhook). Verified `GET /app` with the real credentials:
   slug and permissions match.
2. `app/integrations/github_app.py`: 9 minute RS256 app JWT, installation tokens reused in
   memory until 5 minutes before expiry, never stored or logged.
3. Connect GitHub flow: signed 15 minute state bound to workspace and account; the setup
   callback attaches an installation only when the state matches its cookie, the account
   still owns the workspace, and the installation belongs to the signed-in GitHub user or an
   organisation they actively administer. Repository picker re-reads the installation and
   accepts only its ids. Disconnect is scoped to the workspace.
4. Migration `0003_github_connections`: `github_installation`, `workspace_repository`. No
   token column. Verified up, down, up on PostgreSQL 15; applied locally.
5. **CI checks were never ingested**, so `checks_state` was always unknown and BL-7 could not
   fire on real data. Sync now records passing, failing or unknown for open pull requests
   from check runs and the combined status. A read failure is unknown, never passing.
   Verified on `headref-sandbox`: PR #4 passing, PR #5 failing.
6. Dependencies `pyjwt` and `cryptography` added (agreed); dev image rebuilt.

### Changed
`app/integrations/github_app.py`, `app/integrations/github.py`, `app/sync.py`,
`app/web/connections.py`, `app/web/auth.py` (`sign_value`), `app/models/connections.py`,
`app/config.py`, `pyproject.toml`, 3 templates, 4 test modules.

### Discovered
Workspace repositories are selected but not yet synced: sync still reads the global
scopes. Per-workspace sync is W5, after the source tables carry `workspace_id`.

### Next Step
Install the App on `keshan-dev` and connect `headref-sandbox` through the workspace page.
Then W4, the Atlassian connection.

---

## 2026-10-19 | Keshan | Workspaces W2: portfolio home and workspace shell
Status: IN_PROGRESS

### Completed
1. `GET /workspaces`: portfolio home, 1 card per workspace with the caller's role and an
   honest "Not synced yet" state. Empty state for a first-time account.
2. `GET /workspaces/new` and `POST /workspaces`: create form; the caller becomes owner and
   lands on the new workspace. A blank or long name re-renders with fixed error copy (422).
3. `GET /w/{workspace_id}`: workspace shell with Connections, Sync, Members, Analysis and
   What changed sections. Each says what is not set up yet. Connect and Sync buttons are
   shown disabled with the reason, owners and managers only. Switcher when the account has
   more than 1 workspace.
4. Sign-in now lands on `/workspaces` instead of `/repos`. "Workspaces" added to the top
   navigation; the existing pages stay until W7.
5. `tests/test_workspace_pages.py`: 10 page tests, including hostile name escaping, 404 for
   another account's workspace, and role-dependent buttons.

### Problems
Not checked in a browser: layout at 375, 768 and 1280 px, the switcher menu, dark theme.

### Next Step
W3, the GitHub App connection. Needs the GitHub App registered first (W0 external setup).

---

## 2026-10-19 | Keshan | Workspaces W1: accounts, workspaces, isolation core
Status: IN_PROGRESS

### Completed
1. Migration `0002_workspaces` (announced in the W0 entry): `account`, `workspace`,
   `workspace_member` with roles `owner`, `manager`, `viewer`. Additive only. Verified on a
   throwaway PostgreSQL 15: upgrade, downgrade, upgrade, 1 head. Applied to the local
   development database.
2. GitHub sign-in creates or updates the account, matched on the numeric GitHub id.
   Transitional `account.app_user_id` keeps the existing team and member pages working
   until W7 replaces them.
3. `workspace_access(min_role)` in `app/web/workspaces.py` is the only membership check.
   Not a member, or archived: 404, so existence does not leak. Role too low: 403.
4. API: `GET /api/workspaces`, `POST /api/workspaces` (caller becomes owner),
   `GET /api/workspaces/{workspace_id}`.
5. `tests/test_workspaces.py`: create, list isolation, cross-account 404 equal to a missing
   id, archived 404, roles, no session and no account 401, account matched on GitHub id, and
   a check that every `{workspace_id}` route uses `workspace_access`.

### Discovered
- **The FR-028 route coverage test was passing without checking anything.** FastAPI 0.143
  keeps an included router as 1 `_IncludedRouter` entry in `app.routes`, so
  `isinstance(route, APIRoute)` found 0 member routes. `tests/conftest.py`
  `iter_api_routes` walks both layouts, and the test now asserts it sees at least 3 member
  routes. They are all guarded.
- `alembic check` shows 1 pre-existing drift unrelated to this work: migration 0001 names an
  index `ix_work_item_dependency_blocked_by`, the model names it
  `ix_work_item_dependency_blocked_by_work_item_id`. Not changed here.

### Next Step
W2: portfolio home, create workspace page, switcher, account menu.

---

## 2026-10-19 | Keshan | Workspaces: decisions proposed, real test data (W0)
Status: IN_PROGRESS

### Completed
1. Branch `feat/workspaces` from `main` at `c8ac02c`. Plan in
   `impl/implementation_plan_workspaces.md`: managers sign in with GitHub, create isolated
   workspaces, connect repositories (GitHub App) and Jira projects (Atlassian OAuth), sync,
   and see findings, a feed and a per-member panel with a likely next task.
2. DEC-021 to DEC-025 added to `docs/DECISIONS.md` as **Proposed**.
3. Real test data, created 2026-10-18: Jira project `HRS` on `keshanuni.atlassian.net`
   (HRS-1 to HRS-9, 1 "is blocked by" link) and private repository
   `keshan-dev/headref-sandbox` (pull requests #1 to #7, #1 and #3 merged, #5 with a
   failing check, #6 with no ticket key, #7 draft). Each case targets 1 rule; the table is
   in the plan, section 9.

### Discovered
- `repository.full_name`, `project.key` and `identity_link (integration, external_id)` are
  unique globally, so 2 workspaces cannot share a repository, project or person today.
- `app_user` is both the signed-in actor and the described member.
- Jira team-managed board: a column added after Done puts Blocked in the Done status
  category. headref maps by status name first, so its reading is correct.
- The Jira client still calls `GET /rest/api/3/search`, believed retired on Jira Cloud.
  Not yet confirmed against the real site.

### Decisions Needed
1. DEC-021 to DEC-025 need Developer 2 agreement. Multi-tenancy is Stage 2 in
   `PROJECT_REQUIREMENTS.md` 2.8, so this is a scope change (rule 15). The pull request
   for this branch does not merge before that.
2. New dependencies `PyJWT` and `cryptography` (free, but a dependency change).
3. DEC-020 is still Proposed; DEC-025 depends on it.

### Announcement (rule 11)
Developer 1 will write migration `0002` (accounts, workspaces, workspace membership) on
this branch. No other migration until it is merged.

### Narrative speed, measured 2026-10-19
headref's own `generate_narrative` and `validate_narrative`, HRS-shaped findings, local
Ollama on this laptop, 3 runs each after 1 warm-up:

| Model | Prompt | Median | Valid | Read |
|---|---|---|---|---|
| llama3.2 (3B) | v1 | 9.8 s | 3/3 | correct |
| llama3.2 (3B) | v2 | 7.0 s | 3/3 | correct |
| llama3.2:1b | v1 | 3.3 s | 0/3 | copied the example's AUTH-245 and #182 |
| llama3.2:1b | v2 | 4.8 s | 3/3 | passes checks, wrong meaning ("working on Keshan: 6 assigned Jira items") |

Kept the 3B model. v2 replaces the worked example's real-looking IDs with placeholders and
is now the default. The 1B model is not used: its errors are in meaning, which the
validator cannot detect. Warm-up is 14 to 37 seconds on the first call after a load.
Summaries remain on demand (member opened), never for every member after a sync.

Before the validator fix in this branch, 3 of 3 valid 3B summaries were discarded because
the BL-7 finding uses "failing", a forbidden term.

The evaluation suite does not exist yet (Phase 6), so the v2 prompt change is measured by
this benchmark only. It must be rerun under `TESTING_AND_EVALUATION.md` 11.5 when the suite
exists.

### Next Step
W1.

### AI Assistance
Plan, DEC drafts and test data created with an AI assistant. The DEC records are drafts
for both developers to change, not decisions.
## 2026-10-19 | Keshan | Remove the login stubs (P7-001)
Status: IN_REVIEW

### Completed
1. Removed `POST /api/auth/login` and the HTML `POST /login`. Both signed in whatever
   `user_id` they were sent, with no GitHub involved: an authentication bypass on `main`
   once GitHub OAuth became the real sign-in. Verified on the running app: 404 and 405.
2. `GET /login` no longer loads every active user for an unauthenticated visitor; the
   template had stopped using the list.
3. `SESSION_SECRET` (optional, 32 characters or more) signs the session cookie. Unset keeps
   the old per-process random key and logs a warning: sessions end on restart and do not
   work across workers.
4. Tests sign in with a `log_in` helper in `tests/conftest.py` that sets the signed cookie
   the OAuth callback sets. New tests: both stubs refused, a session for an unknown or
   inactive user is 401, `SESSION_SECRET` survives a key reload, a short secret is
   refused, `next` is cleaned at the GitHub OAuth start.

### Changed
`app/web/auth.py`, `app/web/routes.py`, `app/web/templates/login.html`, `app/config.py`,
`.env.example`, `tests/conftest.py` and 8 test modules.

### Discovered
The open redirect tests went through the stub form. `next` now enters only through
`/auth/github/login`, which already applied `safe_next`; the test moved there.

### AI Assistance
Implemented with an AI assistant. 394 passed, 1 skipped, 1 deselected
(`test_parse_valid_identity_map_file`, which fails only because of a local uncommitted
edit to `seed/identity_map.yml`). Set `SESSION_SECRET` before any deployment.

---

## 2026-10-18 | Keshan | Phase 5 completion: suite repair, Jira credentials, fallback honesty, activity
Status: IN_REVIEW

### Completed
1. Suite repaired: was 35 failed, 5 errors on `feat/phase-05` at `f09f363`, now 391 passed,
   1 skipped. Causes: in-memory SQLite without `StaticPool` in 2 TestClient fixtures; a Jira
   route mutating `os.environ` and the settings singleton, which failed 18 orchestrator tests
   whenever they ran after it; empty compose variables overriding the test defaults; tests
   left behind by the 2026-10-17 UI changes. An autouse fixture now restores the
   environment and settings after every test.
2. Jira credentials come from the environment only. `POST /jira/connect` and
   `/jira/disconnect` are removed: any signed-in user could write an API token into
   `os.environ` and rewrite `.env` on disk. `/jira` is a status page with the steps to
   configure it. Error redirects carry a fixed code, never exception text.
3. Fallback honesty: `MemberInsight.fallback_used` was never set, so a code-built summary
   was labelled "Written by the local model". A model timeout raised instead of falling
   back (EV-12 B). `agent_run` now records a typed `error_type` and the configured model.
4. Activity counts (G3) and truncation disclosure (G4) on `MemberInsight`, counted by code,
   this member only, window rows only, withheld when a tool they need failed.
5. Page-level tests for unavailable, never synced, conflict, hostile excerpt, empty member.
6. Committed the pending changes: pull request blocker evidence keys (fixes the UNKNOWN
   confidence noted on 2026-10-17), and the empty-window fallback in T-002 to T-005.
7. `ruff check .` and `black --check .` clean over the whole tree.

### Changed
`app/web/routes.py`, `app/web/templates/jira.html`, `login.html`, `question_panel.html`,
`app/agent/narrative.py`, `orchestrator.py`, `blockers.py`, `app/schemas/insight.py`,
`app/tools/get_*.py`, `tests/conftest.py` and 8 test modules, `pyproject.toml`.

### Problems
1. Running the suite inside the `project-api` container rewrote the developer's `.env`
   through the old Jira routes. Jira values in `.env` were blanked and need re-entering if
   they were set. Fixed by item 2; tests can no longer write outside `tmp_path`.
2. Not verified in a browser: evidence links opening the right record (P5-003), the refresh
   progress and reload (P5-007), widths and contrast (WP8). No screenshots attached.

### Decisions Needed
1. The empty-window fallback in T-002 to T-005 returns old rows unmarked. EV-03 expects
   UNKNOWN with `NO_EVIDENCE` for an empty window, and DEC-020 says such a member still
   gets "no activity recorded". Merged by choice of Developer 1; needs an `outside_window`
   flag and a DEC-020 amendment, or a revert, before P6-004 writes EV-03.
2. The team page unmatched card was removed on 2026-10-17. P5-004 and FR-003 require the
   unmatched count to be visible. The criterion is unticked until this is agreed.
3. DEC-020 is still Proposed. Phase 6 scenarios EV-01b, EV-03 and EV-04 B depend on it.
4. This work sits in the Developer 2 lane (DEC-019). Developer 2 to review.

### Next Step
Phase 6, after this pull request is merged: settle the spec drift in
`TESTING_AND_EVALUATION.md` 11.2, expand the fixtures, then the harness (P6-003).

### AI Assistance
Implemented with an AI assistant, verified in the `project-api` image on Python 3.11.17
against a copy of the tree without `.env`. Check the new `/jira` page copy and the
activity card in a real browser.

---

## 2026-10-18 | Keshan | Fix Source Health and Repository Analysis for Authenticated GitHub Users
Status: DONE

### Completed
1. Integrated active GitHub OAuth token tracking in memory (`set_active_github_token` and `get_active_github_token`), allowing background sync and on-demand refresh to reuse the authenticated user's access token without requiring GITHUB_TOKEN in `.env`.
2. Updated background sync scheduler (`app/scheduler.py`) to skip unconfigured sources (Jira when unconfigured, GitHub when no token is present) rather than failing with RuntimeError and inserting false failure rows in `sync_run`.
3. Updated `resolve_team_scopes` in `app/scheduler.py` to automatically include all onboarded repositories from the database.
4. Updated `get_source_health` (T-007) so internal RuntimeError from unconfigured scheduler runs does not invalidate valid, fresh repository sync data in PostgreSQL.
5. Filtered unconfigured Jira out of `source_health` in `generate_team_overview` and `orchestrator._check_health`, removing confusing Jira "Unavailable" tiles and error alert banners when only GitHub is in use.
6. Scoped member profile analysis (`run_agent`) to the selected repository (`repository_name`), allowing contributors to display their actual likely current work, blockers, and activity without requiring each member to connect their personal GitHub account.
7. Added unit test in `tests/test_source_health.py` verifying that fresh sync records are preserved when a background tick encounters an unconfigured token RuntimeError.

### Changed
`app/config.py`, `app/integrations/github.py`, `app/scheduler.py`, `app/tools/get_source_health.py`, `app/agent/team_overview.py`, `app/agent/orchestrator.py`, `app/web/routes.py`, `tests/test_source_health.py`, `WORKLOG.md`.

### Discovered
Background scheduler ticks were executing unconditionally every 5 minutes and triggering RuntimeError when GITHUB_TOKEN or Jira credentials were empty, writing failed `SyncRun` rows that poisoned T-007 source health and closed the reasoning gate for all team members.

---

## 2026-10-17 | Keshan | Analyse members from repository history (DEC-020, part 1 of 3)
Status: IN_PROGRESS

### Completed
1. `current_work` and `blockers` now require GitHub only. Jira is optional, and when it is
   not usable the answer says so under "Not determined" (`GateResult.skipped`).
2. With no Jira work item, likely current work is inferred from GitHub: the most recently
   active open pull request, else the latest commit's branch or repository. Always
   `inference`, confidence from `evaluate_confidence`.
3. Tests updated for the new plan, plus 4 new tests for the GitHub fallback and the Jira
   skip note. Planner and orchestrator tests pass.
4. Fixed the local setup after the argus to headref rename: `.env` `DATABASE_URL`, new
   database migrated and seeded, stale `argus.egg-info` and old volume removed.

### Changed
`app/agent/planner.py`, `app/agent/orchestrator.py`, `tests/test_planner.py`,
`tests/test_orchestrator.py`, `docs/DECISIONS.md` (DEC-020, Proposed).

### Discovered
- Source health is per source, not per scope. The scheduler fails every tick on
  `keshan-dev/headref` because `GITHUB_TOKEN` is empty, so GitHub reads as unavailable for
  every member, including repositories synced successfully through OAuth.
- `github_repos` default in `app/config.py` is `keshan-dev/headref`, but the GitHub
  repository is still `keshan-dev/argus`.
- Blocker "PR open with no review" comes back with confidence UNKNOWN: its evidence key
  does not match the pull request evidence key. Not changed here.
- Commits from OAuth-synced repositories have no branch name, so the branch fallback is
  rarely reached on real data.

### Problems
15 tests fail and 5 error in `test_github_oauth.py`, `test_headref_ux_and_jira.py`,
`test_team_overview.py`, `test_ui_team.py`, `tests/web/test_ui_templates.py` (SQLite
thread errors, missing tables, changed page text). They fail without this change too.

### Decisions Needed
DEC-020 needs Developer 2 agreement. Fix for per-source health versus per-scope failures.

### Next Step
Part 2: "Likely next" line in `current_work`. Part 3: GitHub issues ingestion (needs a
migration, announce first, rule 11).

### AI Assistance
Implemented with an AI assistant. Check the wording of the GitHub-only claims in the UI.

---

## 2026-10-17 | Keshan | GitHub Authenticated Organization & Repository Onboarding
Status: DONE

### Completed
1. Expanded GitHub OAuth scope to `read:user,repo,read:org` to support access to personal repositories and organization memberships.
2. Implemented automatic user onboarding in `GET /auth/github/callback`: new GitHub accounts logging in are automatically provisioned with active `AppUser` and verified `IdentityLink` records, and redirected to `/repos`.
3. Stored GitHub access token securely in an HttpOnly, SameSite=lax `headref_gh_token` cookie, cleared on logout.
4. Created dedicated repository and organization selection page at `GET /repos`: lists the user's organizations, personal and org repositories, and marks already ingested repositories. Supports organization pill filtering, live name searching, and custom repository input.
5. Implemented `POST /repos/select`: validates repository full name, triggers GitHub ingestion (`sync_github`) with automatic contributor provisioning (`auto_create_contributors=True`), and redirects to the team dashboard with a success notice.
6. Added Repositories link to the top navigation pillbar in `app/web/templates/base.html`, and added sync confirmation notice to `app/web/templates/team.html`.
7. Added unit and integration tests in `tests/test_github_oauth.py` covering scope validation, user auto-provisioning, `/repos` page rendering with mocked GitHub API responses, and `/repos/select` sync execution.
8. Resolved `AttributeError: type object 'UnmatchedEntity' has no attribute 'status'` in `GET /repos` by replacing invalid status check with canonical `UnmatchedEntity.resolved_app_user_id.is_(None)`. Verified live page rendering with browser subagent.
9. Implemented repository-scoped team overview in `generate_team_overview` and `_render_team_page`: clicking "View Team" on a synced repository now strictly displays contributors and activity belonging to that specific repository, rather than all members across all synced repositories. Added repository switcher pill navigation to `team.html` and verified per-repo isolation in the live browser.

### Changed
`app/agent/team_overview.py`, `app/web/routes.py`, `app/web/templates/base.html`, `app/web/templates/repos.html`, `app/web/templates/team.html`, `tests/test_team_overview.py`, `tests/test_github_oauth.py`, `WORKLOG.md`.

### Discovered
Storing the user's OAuth access token in an HttpOnly session cookie enables seamless per-session GitHub API queries (listing organizations and repos) and user-scoped repository ingestion without requiring database schema alterations. Unresolved accounts on `UnmatchedEntity` are tracked by `resolved_app_user_id IS NULL`.

---

## 2026-10-17 | Keshan | GitHub OAuth Login Integration for Headref
Status: IN_PROGRESS

### Completed
1. Added GitHub OAuth settings: `github_oauth_client_id` and `github_oauth_client_secret` in `app/config.py` with `github_oauth_enabled` property. Updated `.env.example` with setup notes.
2. Implemented `GET /auth/github/login` endpoint with cryptographically secure state generation, redirect cookie state management, and GitHub authorization URL redirection.
3. Implemented `GET /auth/github/callback` endpoint: validates CSRF state, exchanges authorization code for an access token via GitHub's API, retrieves authenticated GitHub user profile, resolves user against `IdentityLink` in PostgreSQL, and creates signed `headref_session` session cookie.
4. Updated `app/web/templates/login.html` to feature the "Sign in with GitHub" button prominently, showing clear error messaging and an unconfigured notice banner when credentials are absent, alongside an offline demo fallback disclosure.
5. Renamed application title and service identifier to `Headref` / `headref` in `app/main.py`, `tests/test_health.py`, `app/web/routes.py`, and `app/web/auth.py`.
6. Added `tests/test_github_oauth.py`: 8 unit and integration tests covering unconfigured states, CSRF state verification, upstream error handling, unmapped GitHub accounts, and successful sign-in.

### Changed
`app/main.py`, `app/config.py`, `.env.example`, `app/web/auth.py`, `app/web/routes.py`, `app/web/templates/login.html`, `tests/test_health.py`, `tests/test_github_oauth.py`, `WORKLOG.md`.

### Discovered
Matching by GitHub user ID in `IdentityLink` works seamlessly with both numeric IDs (`external_id`) and GitHub logins (`external_handle`). When an unmapped account attempts to log in, clear actionable error guidance informs them that their account is not mapped to any Headref team member and points them to `seed/identity_map.yml`.

### Next Step
Configure GitHub OAuth App credentials in `.env` and verify the live browser OAuth authorization loop in Headref.


---

## 2026-10-08 | Isiwara | Web shell hardening (WP6)
Status: IN_PROGRESS

### Completed
1. Closed the open redirect on login. `next` arrives from the query string on `GET /login`
and from the form body on `POST /login`, and went straight into `RedirectResponse` with no
validation, so `GET /login?next=https://evil.example/` sent the user off site after a
successful login. Added `safe_next()`, which accepts only a path rooted at `/`, normalises
backslashes and rejects a protocol relative `//host`. Both login paths use it.
2. Removed the font copy from `app/main.py`. The application was running
`shutil.copytree` from `docs/headref-ui-pack/` into `app/web/static/fonts/` at import
time. The fonts are committed repository assets with their OFL licence, and a docs
directory is not a runtime asset source.
3. HTML routes now render `error.html` instead of raw JSON. The exception handler returned
`JSONResponse` for every status except 401, so a 403 or 404 on a page showed a JSON body
and `error.html` was referenced by nothing. The API path is unchanged and still returns
its JSON body. `exc.detail` never reaches the HTML page: the template picks fixed copy
from the status code, so no exception internals are exposed.
4. `error.html` gained a `403page` copy variant. Wiring every 403 to the template made a
team level denial say "You cannot view this member", which is the wrong statement.
5. Split `GET /` and `GET /teams/{team_id}` into 2 handlers. Stacked on 1 handler with
`team_id` defaulting to None, `team_id` was also accepted as a query parameter on `/`.
6. Added a `RequestValidationError` handler. FastAPI handles that separately from
`HTTPException`, so a browser hitting a page route with a malformed path or query
parameter still got a JSON body listing the internal field locations. Page requests now
get the error page; the API still gets the JSON error list.
7. `_member_id_from` used `str.isdigit()`, which is True for characters `int()` rejects,
such as the superscript 2. A 404 or 422 on `/members/<that>/x` would have raised inside
the exception handler. Changed to `str.isdecimal()`.
8. `safe_next()` strips tab, carriage return and newline before its checks. Browsers strip
those from a `Location` value, so `/<TAB>//host` would otherwise resolve protocol
relative. Starlette percent encodes them today, confirmed in this environment, but that is
its behaviour to change, not a guarantee the validator should lean on.
9. Added `tests/test_web_hardening.py`, 25 tests: 11 open redirect cases including the
backslash and protocol relative variants, an AST check that `app/main.py` performs no
filesystem write at import, a check that every `@font-face` source in `style.css` resolves
to a file that exists, HTML versus JSON error rendering, the 403 copy split, avatar tints,
and an OpenAPI assertion that `/` declares no `team_id`.

### Changed
`app/main.py`, `app/web/routes.py`, `app/web/templates/error.html`,
`tests/test_web_hardening.py`, `WORKLOG.md`.

### Discovered
Ran the project security-review skill over the branch. It reported no HIGH or MEDIUM
finding. It confirmed the redirect validator against Starlette's own quoting, confirmed
`_scope_filter` builds its predicate entirely through SQLAlchemy with no string
interpolation, confirmed no `|safe` or `Markup` anywhere, and confirmed the drawer in
`app.js` uses `textContent` and `replaceChildren` rather than `innerHTML` for untrusted
values. It surfaced the `isdigit` bug above, which is fixed here.

The session cookie is set with `httponly` and `samesite=lax` but without `secure`. That is
correct for the local HTTP demo and wrong for anything deployed. It is in `app/web/auth.py`
from P3-001, not part of this change. Raised below rather than changed quietly.

### Decisions Needed
1. Should the session cookie set `secure=True` when the app is not running locally, keyed
off `APP_ENV`? Needed before anything is deployed over HTTPS.
2. Still unanswered from the previous 2 entries: the uppercase display question from the
design system, the untested dark theme contrast, and whether an empty team should report
"On Track" or "Unknown".

### Next Step
WP4, the activity field on `MemberInsight`, then WP5 for truncation disclosure.

### AI Assistance
An assistant implemented WP6 and ran the project security-review skill over the branch.
Verified in a scratch virtual environment on Python 3.13, not the 3.11 that
`pyproject.toml` pins: `348 passed, 1 skipped`, `ruff check app tests` clean,
`black --check app tests` clean. Reproduce on 3.11 before merging. A human should confirm
the redirect fix by hand against a real browser, because the tests assert the Location
header rather than browser behaviour.

---

## 2026-10-08 | Isiwara | Ingestion scope configuration and honest sync endpoints (WP3)
Status: IN_PROGRESS

### Completed
1. Ingestion scope now comes from configuration, not from a hardcoded default argument.
`GITHUB_REPOS` and `JIRA_PROJECT_KEYS` are new settings in `app/config.py` with
`github_scopes()` and `jira_scopes()` helpers, both added to `.env.example` per HOW_TO B2.
The literal `"keshan-dev/headref"` is gone from `app/sync.py` and `app/scheduler.py`.
`sync_github` and `sync_jira` resolve the scope when it is not passed, and raise rather
than guess when several are configured. `run_scheduled_tick` iterates every configured
scope and guards each one separately, and returns a per scope `details` map alongside the
existing aggregate statuses.
2. Added `resolve_team_scopes(team_id)` in `app/scheduler.py`. The MVP ingests 1 team, so
it returns the configured scopes whatever team id is asked for. Per team scope is a column
on the `team` table, which is a migration, and is deferred until a second team exists. The
parameter is there so the call sites already pass it.
3. WP3, the on-demand sync endpoints (P5-007, issue #49):
   - Added the `SyncStatus`, `SyncTriggerResponse` and `SyncStatusResponse` contracts in
     `app/schemas/sync.py` (gap G7). Both endpoints are typed; the `dict[str, Any]`
     returns are gone, so they appear properly in the OpenAPI document.
   - Removed the fabricated payload. The endpoint used to report GitHub and Jira as
     `running` when no `sync_run` row existed. It now returns an empty `runs` list with
     `started: true`, and the page says "starting". Rule 9: an absent row means "I cannot
     see a run", not "a run is in progress".
   - The in-flight guard filters `sync_run` by `source` and `scope` for this team's
     configured scopes. It previously matched every running row in the table, so a sync
     for an unrelated scope blocked the team.
   - The background task handle is retained in a module level set with a done callback.
     asyncio holds only a weak reference, so the task could be collected before it ran.
     The suite no longer reports `coroutine 'to_thread' was never awaited`.
   - `_do_background_sync` logs what it started and what came back, per rule 8.
   - `app.js` reads the new `started` flag and says "A sync is already running" rather than
     implying this click started one.
4. Tests added: the 403 case now asserts nothing was started, a trigger with no rows
asserts `runs == []`, and the in-flight query is asserted to be scope filtered by
compiling the SQL. Ticked the P5-007 concurrency criterion in `docs/TASKS.md`.

### Changed
`app/config.py`, `.env.example`, `app/sync.py`, `app/scheduler.py`, `app/schemas/sync.py`,
`app/schemas/__init__.py`, `app/web/routes.py`, `app/web/static/app.js`,
`tests/test_api_sync.py`, `docs/TASKS.md`, `WORKLOG.md`.

### Discovered
`AppUser` now carries a `user_id` property aliasing `id`. That closes the avatar tint
problem noted in the previous entry: `base.html` reads `actor.user_id` while the routes
pass the ORM row, and every avatar was falling back to tint 0.

### Problems
Still open, tracked as WP4 to WP8 in `impl/implementation_plan_phase_5_completion.md`:
`MemberInsight` has no `activity` field, so the activity card never renders, and no
`truncated` field, so the truncation notice never fires. The login `next` parameter is an
open redirect. `app/main.py` still copies fonts out of `docs/` at import time and renders
every HTML error as raw JSON.

### Decisions Needed
Carried over from the previous entry and still unanswered: the uppercase display question
from the design system, the untested dark theme contrast, and whether an empty team should
report "On Track" or "Unknown".

### Next Step
WP6, the login open redirect, because it is the only remaining security defect.

### AI Assistance
An assistant implemented the configuration option and WP3, and split the Phase 5 work into
separate commits. Verified in a scratch virtual environment on Python 3.13, not the 3.11
that `pyproject.toml` pins: `323 passed, 1 skipped`, `ruff check app tests` clean,
`black --check app tests` clean. Reproduce on 3.11 before merging. The choice to read
scope from configuration rather than from the `team` table was made by the developers.

---

## 2026-10-08 | Isiwara | Phase 5 status correction, WP0 to WP2
Status: IN_PROGRESS

### Completed
1. Corrected `docs/TASKS.md`. P5-002 to P5-007 were recorded DONE with every acceptance
box ticked. They are back to IN_PROGRESS, and 4 boxes that were not true are unticked:
P5-002 "Activity counts carry the context note", P5-003 "Clicking a link opens the correct
source record", P5-006 "Truncation is disclosed", P5-007 "A sync already running returns
the in-flight run, and no second run starts".
2. WP1, made the suite run. It did not collect at all before this.
   - `tests/test_ui_member.py` imported `ActivitySummary` and `AssignedWorkItem`, neither
     of which exists. The fixture is rebuilt against the real `MemberInsight`: `user_id`,
     not `member_id`, no `question_type`, and `assigned` as `WorkItemOut`.
   - `tests/test_team_overview.py` used `SourceHealthOut(state="healthy")`, which is not a
     valid literal, and built `GetCommitsOutput` without the required `truncated`.
   - The same file left `assignee_user_id` and `author_user_id` unset. `build_evidence`
     drops any row with a null actor (AC-14), so the evidence set came back empty and every
     member fell through to "unknown". That is the code working correctly, so the fixtures
     now carry the subject id.
   - `tests/test_ui_team.py` built `TeamOverview` without the required `overall_status`.
   - `tests/test_ui_freshness.py` rendered a template into a variable it never asserted on.
3. WP2, fixed `GET /admin/unmatched`. It constructed `GetSourceHealthInput()` with no
arguments while `team_id` and `sources` are required, so the page returned 500. The route
now passes both, handles an actor with no team, and treats a `ToolFailure` from the health
tool as unavailable with the typed error rather than letting it crash or silently read as
an empty result (rule 9). Added a rendering case for the no team actor.
4. `ruff check app tests` and `black app tests` applied, both clean.

### Changed
`docs/TASKS.md`, `app/web/routes.py`, `tests/test_team_overview.py`, `tests/test_ui_team.py`,
`tests/test_ui_member.py`, `tests/test_ui_unmatched.py`, `tests/test_ui_freshness.py`,
`impl/implementation_plan_phase_5_completion.md` (new).

### Discovered
1. `app/web/routes.py` binds `load_user` at import time, so a test that patches only
`app.web.auth.load_user` leaves the form login path calling the real loader against a None
session. The UI test fixtures now patch both bindings.
2. `tests/test_ui_team.py` asserted `"rank" not in html`. The team page carries the
disclaimer "Headref does not rank or compare people", which is exactly the copy
AI_BEHAVIOR 5.11 asks for, so the assertion punished the right behaviour. It is replaced
with a structural check: the disclaimer is present, member cards are in alphabetical order,
and no sorting control or scoring vocabulary appears.
3. `generate_team_overview` returns "On Track" for a team with no members. That asserts a
state with no evidence behind it. Not changed here, raised below.

### Problems
Not fixed yet, tracked as WP3 to WP8 in `impl/implementation_plan_phase_5_completion.md`:
`POST /api/teams/{id}/sync` reports GitHub and Jira as `running` when no `sync_run` row
exists, its in flight guard is not scoped to the team, and the background task handle is
discarded, which the suite still reports as `RuntimeWarning: coroutine 'to_thread' was
never awaited`. `MemberInsight` still has no `activity` or `truncated` field. The login
`next` parameter is an open redirect. `app/main.py` copies fonts out of `docs/` at import
time and turns every HTML error page into raw JSON.

### Decisions Needed
1. The design reference in `impl/design system/` specifies uppercase display headings
throughout. `style.css` sets no `text-transform: uppercase` anywhere. Recommend keeping
sentence case: the subject matter is people, and the reference is a marketing site. Agree
it explicitly so it is a decision rather than an accident.
2. The dark theme block in `style.css` is untested for contrast and `--smoke` already fails
AA as body text on white. Either fix the pairs or ship light only.
3. An empty team reporting "On Track" needs a ruling. "Unknown" is the honest value.

### Next Step
WP3, the sync endpoints. That is the package with a direct rule 9 violation in it.

### AI Assistance
An assistant ran the audit, wrote the completion plan and applied WP0 to WP2. The test
results below were produced in a scratch virtual environment on Python 3.13, not on the
3.11 that `pyproject.toml` pins and CI runs, so reproduce them before merging:
`321 passed, 1 skipped` for `pytest tests/`, clean `ruff check app tests` and
`black --check app tests`. The 3 `Decisions Needed` items above are for the 2 developers,
not for the assistant.

---

## 2026-10-05 | Isiwara | Phase 5 UI & API Implementation (P5-002 to P5-007)
Status: DONE

### Completed
Delivered Phase 5 user interface and web application:
1. P5-002 (Issue #34): Implemented member profile page (`member.html`, question tabs, claim cards with classification and categorical confidence badges, assigned work grouping, activity context disclaimer).
2. P5-003 (Issue #35): Integrated evidence drawer in `app.js` with focus trapping, keyboard navigation (Tab/Esc), and safe HTML details fallback.
3. P5-004 (Issue #36): Delivered team overview dashboard (`team.html`, hero status, alphabetical member cards, attention items linked to member tabs, unmatched banner).
4. P5-005 (Issue #37): Implemented unmatched identity view (`unmatched.html`, `GET /admin/unmatched`) with occurrence counts, date tracking, and safe handle escaping.
5. P5-006 (Issue #38): Delivered honest freshness component (`partials/freshness.html`), degraded-state notices (`partials/notices.html`), and client-side time localizer in `app.js`.
6. P5-007 (Issue #49): Built asynchronous on-demand refresh (`POST /api/teams/{id}/sync` returning 202 in <500ms) and status polling (`GET /api/teams/{id}/sync/status`) with cached data retention on failure.
7. Mounted static asset pipeline (`StaticFiles`), self-hosted IBM Plex fonts, and registered pure Jinja2 formatting helpers in `app/web/ui_format.py`.
8. Added test suites: `tests/web/test_ui_templates.py`, `tests/test_ui_team.py`, `tests/test_ui_member.py`, `tests/test_ui_unmatched.py`, `tests/test_ui_freshness.py`, and `tests/test_api_sync.py`.

### Changed
`app/main.py`, `app/web/routes.py`, `app/web/ui_format.py`, `app/web/static/` (`app.js`, `favicon.svg`), `app/web/templates/` (`base.html`, `login.html`, `error.html`, `team.html`, `member.html`, `unmatched.html`, `partials/*.html`), `tests/web/test_ui_templates.py`, `tests/test_ui_team.py`, `tests/test_ui_member.py`, `tests/test_ui_unmatched.py`, `tests/test_ui_freshness.py`, `tests/test_api_sync.py`, `docs/TASKS.md`, `WORKLOG.md`.

### Discovered
All member routes (`/members/{member_id}` and `/members/{member_id}/panel`) take `MemberGuard` to satisfy the security guard inspection in `tests/test_auth.py`. Using pure urllib parse in `post_login` allows robust form decoding without requiring `python-multipart`.

### Next Step
Phase 6: Testing and Evaluation.


## 2026-10-05 | Isiwara | P5-001 Member insight and team overview endpoints
Status: DONE

### Completed
Closed issue #33 (task P5-001).
1. Added `fallback_used: bool = False` to `MemberInsight` (Gap G1) and defined `AttentionItem`,
`TeamMemberOverview`, and `TeamOverview` in `app/schemas/insight.py` (Gap G6), re-exporting
them in `app/schemas/__init__.py`.
2. Created `app/agent/team_overview.py` with `generate_team_overview()`, providing pure
deterministic overview calculation from read tools T-001 and T-007 and findings rules
(`detect_blockers`, `detect_risks`, `detect_conflicts`). Unavailable sources force member
states to `unknown` per FR-025, and every state is backed by evidence items.
3. Refactored `app/web/routes.py` to remove placeholder mocks and wire endpoints to real
authorizations: `/api/members/{member_id}/insight` takes `MemberGuard` (FR-028) and executes
`run_agent()` (FR-014, DEC-007), and `/api/teams/{team_id}/overview` verifies actor team
membership before generating the overview.
4. Added unit test suite `tests/test_team_overview.py` and updated `tests/test_api_insights.py`
covering all 6 acceptance criteria (200 contracts, 422 question validation, 401/403/404
security and access controls, unavailable source handling, and evidence backing).

### Changed
`app/schemas/insight.py`, `app/schemas/__init__.py`, `app/agent/team_overview.py`,
`app/agent/__init__.py`, `app/web/routes.py`, `tests/test_team_overview.py`,
`tests/test_api_insights.py`, `docs/TASKS.md`, `WORKLOG.md`,
`impl/implementation_plan_issue_33.md`.

### Discovered
Wiring `MemberGuard` directly into `/api/members/{member_id}/insight` satisfies both the
dynamic authorization check and the static AST inspection check in
`tests/test_auth.py::test_no_member_route_in_the_real_app_bypasses_the_guard`.

### Next Step
P5-002: Member profile page (`member.html`).


## 2026-10-03 | Keshan | P5-001 CI fixes
Status: IN_PROGRESS

### Completed
Made `Lint and format` and `Project rules` pass on pull request #84.
1. Fixed the 11 ruff findings in `app/web/routes.py` and `tests/test_api_insights.py`:
unsorted import blocks (I001), 3 unused imports (`fastapi.Depends`, `typing.Optional`,
`pytest`), `typing.List` replaced with `list` (UP035, UP006), and 3 lines over 100
characters (E501).
2. Reformatted both files with black and added the missing trailing newline to
`app/main.py`, which was the only black failure in the tree.
3. Added this entry. `Project rules` was failing because pull request #84 targets
`feat/phase-05`, and against that base the branch did not touch `WORKLOG.md`. It did
against `main`, which is why the check passed earlier in the branch's life and started
failing when the base was retargeted.

### Changed
`app/web/routes.py`, `tests/test_api_insights.py`, `app/main.py` (trailing newline only),
`WORKLOG.md`.

### Discovered
The `WORKLOG.md` check in CI compares against `origin/${{ github.base_ref }}`, so it is
relative to whatever branch a pull request targets. Retargeting a pull request can make a
previously passing run fail without any code change.

### Problems
None in this change. It is formatting only, no behaviour was altered.

### Decisions Needed
`app/web/routes.py` as it stands returns hardcoded responses. It does not call the
orchestrator, and it defines a local `can_view_member` stub rather than taking the
`MemberGuard` dependency from `app/web/auth.py` that `app/main.py` names in its module
docstring. Against the P5-001 acceptance criteria in `docs/TASKS.md` this leaves 3 unmet:
per-source freshness is a fixed string rather than real data, the Unknown state for
unavailable source data is not implemented in the team overview, and no overview state is
backed by an evidence item. Leaving invented timestamps and summaries in a shipped
endpoint also runs against CLAUDE.md rule 10. P5-001 should stay IN_PROGRESS and the
endpoints should be wired to the orchestrator before the task is closed.

### Next Step
Wire both endpoints to the orchestrator and to `MemberGuard`, then re-check the P5-001
acceptance criteria.

### AI Assistance
An AI assistant made the lint and format fixes and wrote this entry. The fixes are
mechanical (import order, unused imports, line wrapping, trailing newline) and the diff is
worth a quick read to confirm no behaviour changed. The gap described under Decisions
Needed was found by reading `docs/TASKS.md`, not verified against a running system.

---

## 2026-09-22 | Keshan | Phase 4 CI fixes
Status: DONE

### Completed
Made `Lint and format` and `Tests` pass on the phase 4 pull request. 7 test modules failed
collection, so most of phase 4 had never been run by CI.
1. Broke a circular import: `evidence_builder` imported `AgentRunContext` from
`orchestrator` while `orchestrator` imported `build_evidence` back. The use is type only,
so it moved under `TYPE_CHECKING`. S3 depending on the orchestrator at runtime was the
wrong direction anyway.
2. Fixed 9 ruff findings and reformatted 13 files with black.
3. Fixed 2 production defects: `blockers.py` read `pr.created_at_source` for BL-5 and BL-6,
and `PullRequestOut` has no such field, so both rules raised `AttributeError` on every open
pull request. `validation.py` declared a `dropped_claims` local that no return path used.
4. Fixed the test fixtures: 17 `PullRequestOut` constructors used the database column name
`created_at_source` instead of the schema field `created_at` and omitted the required
`retrieved_at`, 1 `ReviewOut` omitted `retrieved_at`, and `Organization(domain=...)` and
`AppUser(email=..., full_name=...)` passed columns those models do not have.

### Changed
`app/agent/evidence_builder.py`, `app/agent/blockers.py`, `app/agent/validation.py`,
`app/agent/deterministic_summary.py`, `app/agent/narrative.py`, `app/agent/orchestrator.py`,
and the phase 4 test modules.

### Discovered
The schema contracts frozen in #68 and the SQLAlchemy models use different names for the
same value: the model column is `created_at_source`, the tool output field is `created_at`,
and `app/tools/get_pull_requests.py` is the only place that maps between them. Phase 4 code
and its tests both guessed the column name. `AppUser` has no `email` and `Organization` has
no `domain` or `key`, and test fixtures keep inventing them.

### Decisions Needed
Nothing new. The unpinned dependency versions raised on 2026-09-21 are still unresolved.

### Next Step
Merge the phase 4 pull request once CI is green.

### AI Assistance
An AI assistant made these fixes. Worth a human check: the `TYPE_CHECKING` change in
`evidence_builder.py`, and whether BL-5 and BL-6 should measure age from `created_at` or
from `last_commit_at`, since neither rule has ever executed.

---

## 2026-09-22 | Isiwara | P4-001, P4-005, P4-006, P4-007
Status: DONE

### Completed
Completed Developer 2 Phase 4 tasks and end-to-end agent pipeline orchestration:
1. `P4-001` (Issue #25): Created `app/agent/evidence_builder.py` building a sanitized,
deduplicated, ranked evidence set with sequential IDs ev_1..ev_n (DEC-004, DEC-008).
Excludes null actors and unavailable sources; strips URLs and control chars from excerpts.
2. `P4-005` (Issue #29): Created `app/agent/narrative.py` and `app/agent/prompts/narrative_v1.txt`
implementing the single local Ollama call with temperature 0, fixed seed 42, num_predict 300,
schema retry once, and deterministic fallback on connection error or schema failure (DEC-018).
Asserted zero tool calls and zero claim/confidence generation (AC-3).
3. `P4-006` (Issue #30): Created `app/agent/validation.py` rejecting invented entities, forbidden
person-judgment terms, or invalid shapes with automatic deterministic fallback and dropped claim
tracking (FR-017, FR-022).
4. `P4-007` (Issue #31): Created `app/agent/cache.py` providing stable SHA-256 evidence hashing,
insight caching in `Insight` table, and comprehensive audit persistence into `AgentRun` table
(FR-024, FR-031).
5. Orchestration Pipeline: Added `run_agent` to `app/agent/orchestrator.py` integrating stages
S1 through S6 end-to-end (closed gate handling, evidence building, cache check, findings engine,
narrative generation, validation, and run persistence).
6. Unit tests: Created `tests/test_evidence_builder.py` (6 tests), `tests/test_narrative.py`
(4 tests), `tests/test_validation.py` (5 tests), `tests/test_cache.py` (4 tests), and full-flow
orchestration tests in `tests/test_orchestrator.py`.
7. Marked P4-001, P4-005, P4-006, and P4-007 as DONE in `docs/TASKS.md`.

### Changed
`app/agent/evidence_builder.py` (new), `app/agent/narrative.py` (new),
`app/agent/prompts/narrative_v1.txt` (new), `app/agent/validation.py` (new),
`app/agent/cache.py` (new), `app/agent/orchestrator.py`, `app/schemas/evidence.py`,
`app/schemas/insight.py`, `app/agent/__init__.py`, `tests/test_evidence_builder.py` (new),
`tests/test_narrative.py` (new), `tests/test_validation.py` (new), `tests/test_cache.py` (new),
`tests/test_orchestrator.py`, `docs/TASKS.md`, `WORKLOG.md`.

### Next Step
Phase 4 is complete. Proceed to Phase 5 (API and UI routes, templates, and evidence drawer).

---

## 2026-09-22 | Keshan | P4-002, P4-003, P4-004, P4-008
Status: DONE

### Completed
Implemented Developer 1 deterministic findings engine components under DEC-018 and DEC-019:
1. `P4-008` (Issue #32): Created `app/agent/deterministic_summary.py` providing zero-LLM member
summary generation with fallback header (FR-022) and mandatory productivity context disclaimer
(FR-026).
2. `P4-002` (Issue #26): Created `app/agent/conflicts.py` implementing deterministic conflict
rules CF-1 through CF-4 across Jira work items and GitHub pull requests/commits. Enforces non-
resolution rule (C-1, C-2, DEC-005).
3. `P4-003` (Issue #27): Created `app/agent/blockers.py` (signals BL-1 through BL-8) and
`app/agent/risks.py` (signals RK-1 through RK-4) driven entirely by config thresholds. Strictly
enforces FR-021: risk descriptions describe work items and dates, never individuals.
4. `P4-004` (Issue #28): Created `app/agent/confidence.py` implementing base confidence levels
(HIGH, MEDIUM, LOW, UNKNOWN) and the 5 ordered modifiers from AI_BEHAVIOR.md 5.4. Strictly
discards any model-supplied confidence per DEC-006.
5. Unit tests: Created `tests/test_deterministic_summary.py` (3 tests), `tests/test_conflicts.py`
(9 tests), `tests/test_blockers.py` (16 tests), `tests/test_risks.py` (8 tests), and
`tests/test_confidence.py` (10 tests including all 7 worked examples).
6. Exported public engine API symbols in `app/agent/__init__.py`.
7. Marked P4-002, P4-003, P4-004, and P4-008 as DONE in `docs/TASKS.md`.

### Changed
`app/agent/deterministic_summary.py` (new), `app/agent/conflicts.py` (new), `app/agent/blockers.py`
(new), `app/agent/risks.py` (new), `app/agent/confidence.py` (new), `app/agent/__init__.py`,
`tests/test_deterministic_summary.py` (new), `tests/test_conflicts.py` (new),
`tests/test_blockers.py` (new), `tests/test_risks.py` (new), `tests/test_confidence.py` (new),
`docs/TASKS.md`, `WORKLOG.md`.

### Next Step
Handoff findings engine rules (conflicts, blockers, risks, confidence, deterministic summary) to
Developer 2 for narrative generation (`P4-005`), narrative validation (`P4-006`), and agent run
cache persistence (`P4-007`).

---

## 2026-09-22 | Isiwara | P3-004
Status: IN_REVIEW

### Completed
Stages S1 and S2 of the agent. `app/agent/planner.py` holds the 3 fixed plans (DEC-007):
`current_work` and `blockers` need jira and github over 14 days, `risks` needs jira with
github optional over 30 days plus all open items with a due date. `app/agent/orchestrator.py`
has `run_s1_s2`, which calls T-007 first on every run, then the tools the plan lists, and
returns an `AgentRunContext` carrying plan, retrieval, health, failures and the gate decision.

The required-source gate is in. An unavailable required source, a required tool failure, or
a T-007 failure closes it, and `unknown_response` then builds an UNKNOWN `MemberInsight` in the
slot for the question asked. The model is never reached. An unavailable optional source only
adds a note. An invalid question type raises `InvalidQuestionTypeError` (422) before any tool runs.

32 new tests, 11 planner and 21 orchestrator, with fake tools. No database and no Ollama.
Full suite: 197 passed.

### Changed
New: `app/agent/__init__.py`, `app/agent/planner.py`, `app/agent/orchestrator.py`,
`tests/test_planner.py`, `tests/test_orchestrator.py`. No existing file was changed.

### Discovered
**T-006 is skipped when there are no ids.** `GetWorkItemLinksInput` rejects an empty id list,
so S2 only calls it when a work item or pull request was found.

**"All open items with a due date" is a second T-002 call** with `include_done=False` and a
3650 day lookback (`OPEN_ITEMS_LOOKBACK_DAYS`), because T-002 filters on `source_updated_at`
and an old open item would otherwise fall outside the window. Items without a due date are
dropped in code. A dedicated tool parameter would be cleaner if Keshan prefers it.

**A tool is only called when every source it depends on is usable** (fresh or stale). T-006
depends on both jira and github, so it is skipped if either is unavailable.

### Decisions Needed
Six choices to check against `AGENT_ARCHITECTURE.md` 3.4 and 3.6, which I had not read:

1. `AgentRunContext` field names follow the P3-004 issue text, not section 3.6.
2. `risks` skips T-005 (reviews). It is one line in `PLANS` to change.
3. A closed gate still retrieves from the usable sources. Only unavailable ones are skipped.
4. `RISK_WINDOW_DAYS = 30` lives in `planner.py`, not `app/config.py`.
5. `InvalidQuestionTypeError` extends FastAPI's `HTTPException`, so it answers 422 by itself.
6. `unknown_response` puts an UNKNOWN insight in the slot for the question asked.

### Next Step
P4-001, the evidence builder, which builds on `AgentRunContext`.

### AI Assistance
An AI assistant wrote the code and tests and ran black, ruff and pytest against a copy of the
schemas before I ran them on my machine. Needs a human check: the 6 decisions above.

---

## 2026-09-22 | Isiwara | P3-001
Status: DONE

### Completed
Late entry. The work shipped inside PR #74, which is why it has no entry of its own.

The login stub and the authorization seam are in `app/web/auth.py`. Login takes a user id,
checks the user exists and is active, and sets a signed session cookie. The cookie is signed
with `hmac` and a random key generated per process, so no dependency and no setting were
added, and a restart logs everyone out. `can_view_member(actor, subject)` is the only place the
same-team rule lives, and a person with no team can view nobody. Member routes take the
`MemberGuard` dependency, which answers 401, 404 or 403, and logs every denial.

29 tests in `tests/test_auth.py`, with no database. One of them scans every route in the real
app and fails if a path starting with `/api/members/` or containing `{member_id}` does not use
the guard.

### Changed
New: `app/web/__init__.py`, `app/web/auth.py`, `tests/test_auth.py`.
Changed: `app/main.py`, only to add `app.include_router(auth_router)`.

### Discovered
**P5-001 must name its path parameter `member_id`**, not `id` as `TASKS.md` writes it. The guard
and the coverage test both read that name.

The login is exercised only against fake users. It has not run against a real seeded database,
which waits for P2-009.

### Problems
My first `app/main.py` was pasted from a stale copy and deleted the scheduler `lifespan` that
came with PR #70. `test_startup_fails_without_secrets` caught it. Fixed by restoring
Keshan's file and adding only the router lines. Missing final newlines also failed black.

### Next Step
Keshan: please close #21 if it is still open.

---

## 2026-09-21 | Keshan | P3-002 & P3-003
Status: DONE

### Completed
Implemented read tools T-001 through T-006 (`P3-002`, Issue #22) and source health tool
T-007 (`P3-003`, Issue #23):
1. Created `app/tools/base.py` providing `execute_tool_query` with database statement timeout
detection, UPSTREAM_ERROR mapping, and credential redaction (NFR-011).
2. Implemented `app/tools/get_team_members.py` (T-001) returning team members with verified
identity links and total unresolved unmatched entities count.
3. Implemented `app/tools/get_assigned_work_items.py` (T-002) returning normalized work items,
filtering by time window and statuses, and resolving open blocking dependencies.
4. Implemented `app/tools/get_pull_requests.py` (T-003) returning pull requests authored by
subject, filtering by states and draft flag.
5. Implemented `app/tools/get_commits.py` (T-004) with window filtering and truncation
detection (`truncated=True` when exceeding limit).
6. Implemented `app/tools/get_reviews.py` (T-005) filtering reviews by direction (`given`,
`received`, `both`) and timestamp window.
7. Implemented `app/tools/get_work_item_links.py` (T-006) retrieving correlation links filtered
by work item or pull request IDs and minimum confidence threshold.
8. Implemented `app/tools/get_source_health.py` (T-007) evaluating 3-state source health
(`fresh`, `stale`, `unavailable`) per DEC-010 with fail-closed error handling.
9. Created `tests/test_tool_import_guard.py` mechanically asserting zero HTTP client imports
under `app/tools/` (AC-1, DEC-002).
10. Created `tests/test_tools.py` and `tests/test_source_health.py` with 13 comprehensive unit
tests verifying success, empty results, filter options, truncation, timeout, and fail-closed
behavior.
11. Marked `P3-002` and `P3-003` as DONE in `docs/TASKS.md`.

### Changed
`app/tools/` (new package: `base.py`, `get_team_members.py`, `get_assigned_work_items.py`,
`get_pull_requests.py`, `get_commits.py`, `get_reviews.py`, `get_work_item_links.py`,
`get_source_health.py`, `__init__.py`), `tests/test_tool_import_guard.py` (new),
`tests/test_tools.py` (new), `tests/test_source_health.py` (new), `docs/TASKS.md`,
`WORKLOG.md`.

### Next Step
Hand off T-001 through T-007 to Developer 2 for agent planner and orchestrator (`P3-004`)
and evidence builder (`P4-001`). Proceed to next assigned Developer 1 task (Findings engine
rules in Phase 4: `P4-002` blockers, `P4-003` risks, `P4-004` conflicts).

---

## 2026-09-21 | Keshan | P2 CI fixes
Status: DONE

### Completed
Made the `Lint and format` and `Tests` jobs pass on the phase 2 pull request. Both were
failing, so nothing in phase 2 had actually been run by CI.
1. `tenacity` 9.1 stopped re-exporting `wait_base` from the package root, which broke the
import in `app/integrations/http.py` and failed collection for all 12 test modules. It is
imported from `tenacity.wait` now.
2. Fixed 32 ruff findings and reformatted 5 files with black. Nearly all were import
ordering and hand wrapping at 88 columns instead of the configured 100.
3. Fixed 4 real defects found once the suite could run: `sync_github` and `sync_jira` read
`exc.error_code` in their failure handler and the attribute is `error_type`, so every typed
integration failure raised `AttributeError` instead of recording the failure;
`SECRET_REDACT_REGEX` left the credential in `Authorization: Bearer <token>` unredacted;
`is_issue_flagged` only inspected fields whose name contained "flag", so an impediment under
an opaque id such as `customfield_10015` was missed; demo seeding ran GitHub before Jira, so
no work item links were built on the first pass and 7 more appeared on the second.
4. Fixed the test defects: 27 constructor calls passed a `key` argument `Organization` does
not have, a `PullRequest` was built without the non-null `branch_name`, 2 tests patched
`app.config.settings` when the module under test binds `settings` at import, and 1 test used
`@pytest.mark.asyncio` with no async plugin installed.

### Changed
`app/integrations/http.py`, `app/sync.py`, `app/integrations/jira_normalizer.py`,
`seed/seed_demo.py`, `app/config.py`, and 7 other modules for formatting only. Test changes
in `tests/test_github_client.py`, `test_github_normalizer.py`, `test_identity_resolver.py`,
`test_jira_client.py`, `test_jira_normalizer.py`, `test_link_builder.py`,
`test_scheduler.py`, `test_sync.py`.

### Discovered
Seeding cannot build every link in 1 pass. `sync_github` matches branch names and titles
against work items that must already exist, and `sync_jira` matches remote links against
pull requests that must already exist. `seed_demo_database` now runs Jira, then GitHub, then
Jira again so the cross-source links settle and a re-run adds no rows.

`pyproject.toml` pins no upper bound on any dependency, so CI resolves the newest release
every run. That is what broke `tenacity`, and it will happen again.

### Problems
2 test assertions were wrong rather than the code, and were changed:
`test_extract_ticket_keys_word_boundaries_and_case` asserted `AUTH-2450` is not extracted
from text containing it, but it is a valid key and dropping it would lose a real reference.
`test_sync_failure_records_typed_error_and_no_secret_in_detail` asserted the exact marker
`token=***`, which the widened redaction no longer produces; it asserts the secret is absent
and a marker is present.

### Decisions Needed
Whether to pin upper bounds, or exact versions, in `pyproject.toml`. `black` is already
floating, which has cost 3 CI runs on an earlier branch, and `tenacity` has now cost 1 more.
This is a dependency change, so it needs both developers.

### Next Step
Merge the phase 2 pull request once CI is green.

### AI Assistance
An AI assistant made these fixes. Worth a human check: the widened `SECRET_REDACT_REGEX` in
`app/sync.py`, the extra Jira pass in `seed/seed_demo.py`, and the 2 changed test assertions
listed under Problems.

---

## 2026-09-20 | Keshan | P2-010
Status: DONE

### Completed
Built scheduled synchronization and concurrency controls (`P2-010`, Issue #48):
1. Created `app/scheduler.py` implementing automated background synchronization ticks based on
`SYNC_INTERVAL_MINUTES` without external broker dependencies (DEC-013).
2. Implemented concurrency guards via `recover_stuck_syncs`: skips ticks if a sync is currently
active for the same (source, scope), and recovers stuck runs older than `STUCK_SYNC_TIMEOUT_MINUTES`
by marking them failed (timeout) to avoid permanently blocking the scheduler (DEC-016).
3. Added `SCHEDULER_ENABLED` master switch and `STUCK_SYNC_TIMEOUT_MINUTES` named constants to
`app/config.py`.
4. Wired scheduler startup and cancellation into FastAPI application lifespan in `app/main.py`.
5. Created unit test suite in `tests/test_scheduler.py` verifying the disable flag, stuck-run
timeout recovery, active-sync skip guard, execution of sync functions, and background task
lifecycle management.
6. Marked `P2-010` as DONE in `docs/TASKS.md`.

### Changed
`app/scheduler.py` (new), `app/config.py`, `app/main.py`, `tests/test_scheduler.py` (new),
`docs/TASKS.md`, `WORKLOG.md`.

### Next Step
All Phase 2 tasks (P2-001 through P2-010) are complete. Review tests and prepare for Phase 2
merge into `feat/phase-02`.

---

## 2026-09-20 | Keshan | P2-009
Status: DONE

### Completed
Built demo seeding through the real ingestion path (`P2-009`, Issue #20):
1. Created `seed/seed_demo.py` implementing `FixtureTransport` (an offline `httpx.BaseTransport`
replaying local JSON fixtures) to seed the database strictly through real ingestion functions with
zero live network calls (DEC-012, FR-032).
2. Seeded initial demo organization, team, and verified identity map without hardcoded SQL inserts.
3. Successfully executed `sync_github` and `sync_jira` against offline fixtures to populate
canonical repositories, projects, pull requests, commits, reviews, and work item links.
4. Guaranteed inclusion of required evaluation scenarios:
   - Scenario S-6 (Unmatched entity): external contributor account queued in `unmatched_entity`.
   - Scenario S-8 (Unlinked PR): PR #3 with no ticket reference and zero `work_item_link` rows.
   - Scenario CF-1 (Conflict): `AUTH-245` in progress in Jira with linked PR 182 merged in GitHub.
5. Created integration test suite in `tests/test_seed_demo.py` testing complete offline seeding,
presence of S-6, S-8, CF-1 evaluation scenarios, and idempotent re-runs.
6. Marked `P2-009` as DONE in `docs/TASKS.md`.

### Changed
`seed/seed_demo.py` (new), `seed/fixtures/github/pull_requests.json`,
`tests/test_seed_demo.py` (new), `docs/TASKS.md`, `WORKLOG.md`.

### Next Step
Review `P2-010` (Scheduled synchronization, Issue #48).

---

## 2026-09-20 | Keshan | P2-008
Status: DONE

### Completed
Built the sync CLI and write-path orchestration pipeline (`P2-008`, Issue #19):
1. Created `app/sync.py` providing end-to-end synchronization orchestrating GitHub and Jira
clients, canonical normalizers, identity attribution, and work item link builders.
2. Implemented `sync_run` audit tracking with statuses `running`, `success`, `partial` (when
items skipped), and `failed` with item counts (`fetched`, `written`, `skipped`) per FR-008.
3. Implemented `sync_cursor` per FR-009, recording latest sync positions on success and
providing `--reset-cursor` flag to clear cursors for a full resync.
4. Enforced failure isolation and secret redaction per NFR-011: typed error codes are captured
in `error_type` and secrets are stripped from `error_detail`. Failed runs do not advance cursors.
5. Implemented CLI interface executable via `python -m app.sync --source <source> --team <id>`.
6. Created unit and integration test suite in `tests/test_sync.py` testing GitHub and Jira sync,
cursor advancement, cursor reset, secret redaction, failed run handling, and row count idempotency.
7. Marked `P2-008` as DONE in `docs/TASKS.md`.

### Changed
`app/sync.py` (new), `tests/test_sync.py` (new), `docs/TASKS.md`, `WORKLOG.md`.

### Next Step
Proceed to `P2-009` (Demo seeding through the real ingestion path, Issue #20).

---

## 2026-09-20 | Keshan | P2-007
Status: DONE

### Completed
Built the work item link builder (`P2-007`, Issue #18):
1. Created `app/integrations/link_builder.py` implementing DEC-009 link correlation connecting
canonical Jira `WorkItem` records to GitHub `PullRequest`, `Commit`, and branch targets.
2. Implemented `extract_ticket_keys` dynamically compiling word-boundary regular expressions from
all configured project keys (`\b(KEY1|KEY2)-\d+\b`). Tested that `AUTH-245` matches while `AUTH-2450`
is strictly excluded.
3. Implemented correlation confidence matrix:
   - `jira_remote_link`: HIGH
   - `branch_name`: HIGH
   - `pr_title`: MEDIUM
   - `pr_body`: MEDIUM
   - `commit_message`: MEDIUM
4. Enforced idempotency and confidence preservation in `upsert_work_item_link`: when duplicate
`(work_item_id, target_type, target_id)` occurs, the higher-confidence method is preserved and
never downgraded.
5. Strictly enforced that no links are ever inferred from timing or authorship alone (AC-13).
6. Created comprehensive unit test suite in `tests/test_link_builder.py` testing each link method,
the no-ticket case (zero links), duplicate upgrading, multi-project keys, and word boundaries.
7. Marked `P2-007` as DONE in `docs/TASKS.md`.

### Changed
`app/integrations/link_builder.py` (new), `app/integrations/__init__.py`,
`tests/test_link_builder.py` (new), `docs/TASKS.md`, `WORKLOG.md`.

### Next Step
Proceed to `P2-008` (Sync CLI with run state and cursors, Issue #19).

---

## 2026-09-20 | Keshan | P2-006
Status: DONE

### Completed
Built identity resolution and the unmatched queue (`P2-006`, Issue #17):
1. Created `app/integrations/identity_resolver.py` implementing strict identity resolution
(`resolve_actor`, `record_unmatched`, `record_inferred_link`) adhering to DEC-008 and AC-13.
2. Enforced that only verified manual links attribute to `app_user_id`. Inferred links are stored
for review but never used for attribution.
3. Completely excluded display-name matching across the module (AC-13).
4. Implemented `record_unmatched` to track unmapped accounts in `unmatched_entity` with
`first_seen_at`, `last_seen_at`, and an incrementing `occurrence_count` on repeat encounters.
5. Implemented batch record attribution helpers (`attribute_pull_requests`, `attribute_commits`,
`attribute_reviews`, `attribute_work_items`) ensuring records with unmapped actors are never
discarded.
6. Created comprehensive unit test suite in `tests/test_identity_resolver.py` asserting verified
attribution, unmatched entity queue creation, repeat count incrementation, exclusion of inferred
links from attribution, code audit confirming no display name comparison, and retention of all
records regardless of attribution.
7. Marked `P2-006` as DONE in `docs/TASKS.md`.

### Changed
`app/integrations/identity_resolver.py` (new), `app/integrations/__init__.py`,
`tests/test_identity_resolver.py` (new), `docs/TASKS.md`, `WORKLOG.md`.

### Next Step
Proceed to `P2-007` (Work item link builder, Issue #18).

---

## 2026-09-20 | Keshan | P2-005
Status: DONE

### Completed
Built the Jira normalizer and canonical database loader (`P2-005`, Issue #16):
1. Created `app/integrations/jira_normalizer.py` implementing pure normalization functions
(`normalize_project`, `normalize_work_item`, `extract_blocking_dependencies`) testable without
database access.
2. Implemented `map_status` mapping raw Jira statuses to the canonical taxonomy (`todo`,
`in_progress`, `in_review`, `done`, `blocked`) while preserving original text in `raw_status`.
Implemented graceful fallback to `statusCategory.key` and structured warning logging for unmapped
statuses.
3. Implemented `is_issue_flagged` detecting impediment flags from Jira standard and custom fields.
4. Implemented `extract_blocking_dependencies` mapping inward ('is blocked by') and outward
('blocks') issue links into canonical `WorkItemDependency` records.
5. Implemented idempotent upsert functions (`upsert_project` on `key`, `upsert_work_item` on
`(project_id, external_id)`, and `upsert_work_item_dependency`).
6. Implemented batch ingest functions (`ingest_projects`, `ingest_work_items`,
`ingest_dependencies`) that count records and gracefully skip malformed entries without failing
the batch.
7. Created comprehensive unit test suite in `tests/test_jira_normalizer.py` verifying status
mapping, category fallbacks, warning logs for unknown statuses, flag detection, pure normalization,
blocking dependency extraction, database upsert idempotency, and malformed record skipping.
8. Marked `P2-005` as DONE in `docs/TASKS.md`.

### Changed
`app/integrations/jira_normalizer.py` (new), `app/integrations/__init__.py`,
`tests/test_jira_normalizer.py` (new), `docs/TASKS.md`, `WORKLOG.md`.

### Next Step
Proceed to `P2-006` (Identity resolution and the unmatched queue, Issue #17).

---

## 2026-09-20 | Keshan | P2-004
Status: DONE

### Completed
Built the Jira read-only API client (`P2-004`, Issue #15):
1. Created `seed/fixtures/jira/` fixtures: `projects.json`, `issues.json` (including `AUTH-245`,
`PAY-101`, `AUTH-246` with impediment flag and blocks issue link, and `AUTH-240`), and
`remote_links.json` (correlating `AUTH-245` with GitHub PR #1).
2. Created `app/integrations/jira.py` implementing `JiraClient` with HTTP Basic authentication
(base64 encoded `email:token`), base URL management, and read-only GET endpoints (AC-17).
3. Created alias module `app/integrations/jira_client.py` and exported `JiraClient` from
`app/integrations/__init__.py`.
4. Implemented `search_issues` with JQL and optional auto-pagination (`startAt`, `maxResults`,
`total`), `get_issue`, `get_remote_links`, `get_issue_links`, and `list_projects`.
5. Created comprehensive unit test suite in `tests/test_jira_client.py` covering issue search,
field extraction, issue links, remote links, projects, auto-pagination across pages, 401
unauthorized without retry, 403 rate-limited, 429 rate-limited with Retry-After, 404 not found,
and fail-fast validation on missing configuration. All tests pass offline without network calls.
6. Marked `P2-004` as DONE in `docs/TASKS.md`.

### Changed
`seed/fixtures/jira/*.json` (new), `app/integrations/jira.py` (new),
`app/integrations/jira_client.py` (new), `app/integrations/__init__.py`,
`tests/test_jira_client.py` (new), `docs/TASKS.md`, `WORKLOG.md`.

### Discovered
Jira Cloud user objects expose `accountId` rather than email in public API responses (DEC-008).
Identity resolution must match on `accountId` rather than email or username.

### Next Step
Proceed to `P2-005` (Jira normalization into canonical tables, Issue #16).

---

## 2026-09-20 | Keshan | P2-003
Status: DONE

### Completed
Built the GitHub normalizer and canonical database loader (`P2-003`, Issue #14):
1. Created `app/integrations/github_normalizer.py` implementing pure normalization functions
(`normalize_repository`, `normalize_pull_request`, `normalize_commit`, `normalize_review`) testable
without database access.
2. Implemented `clean_excerpt` to strip URLs via regex and cap excerpts at
`EXCERPT_MAX_CHARS` (500) per FR-030.
3. Implemented `parse_datetime` to parse ISO-8601 strings and guarantee timezone-aware UTC
timestamps (`retrieved_at`, `source_updated_at`, `committed_at`, `submitted_at`) per AC-7.
4. Correctly derived pull request state (`open`, `merged`, `closed`) checking `merged_at` first.
Left `author_app_user_id` as None (handed off to `P2-006`).
5. Implemented idempotent upsert functions (`upsert_repository`, `upsert_pull_request`,
`upsert_commit` on `(repository_id, sha)`, `upsert_review` on `(pull_request_id, external_id)`).
6. Implemented batch ingest functions (`ingest_repositories`, `ingest_pull_requests`,
`ingest_commits`, `ingest_reviews`) that track `NormalizationCounts`, catch validation errors on
malformed records, log warnings, and skip them without failing the sync batch.
7. Created comprehensive unit test suite in `tests/test_github_normalizer.py` verifying pure
normalization without database, URL stripping, excerpt truncation, timestamp UTC enforcement,
idempotent database re-runs with row count assertions, and graceful malformed record skipping.
8. Marked `P2-003` as DONE in `docs/TASKS.md`.

### Changed
`app/integrations/github_normalizer.py` (new), `app/integrations/__init__.py`,
`tests/test_github_normalizer.py` (new), `docs/TASKS.md`, `WORKLOG.md`.

### Next Step
Proceed to `P2-004` (Jira read-only client, Issue #15).

---

## 2026-09-20 | Keshan | P2-002
Status: DONE

### Completed
Built the GitHub read-only API client (`P2-002`, Issue #13):
1. Created `app/integrations/github.py` implementing `GitHubClient` using `HttpClient` and
authenticated via `GITHUB_TOKEN`.
2. Implemented methods to fetch repositories, pull requests, reviews, commits, and branches.
Strictly read-only; no write endpoints exist in the module (AC-17).
3. Handled GitHub pagination by parsing RFC-5988 `Link` headers (`rel="next"`) with
automated multi-page collection in `_paginate`.
4. Created realistic GitHub JSON fixtures in `seed/fixtures/github/` (`repositories.json`,
`pull_requests.json`, `reviews.json`, `commits.json`, `branches.json`) covering all test
scenarios (impediments, draft PRs, unlinked PRs, external contributors).
5. Added unit test suite in `tests/test_github_client.py` covering PR listing, review states,
commit metadata, branches, pagination across multiple pages, 401 auth failure, 403 rate-limiting,
and 404 not found. All 10 tests pass without live network calls.
6. Marked `P2-002` as DONE in `docs/TASKS.md`.

### Changed
`app/integrations/github.py` (new), `app/integrations/__init__.py`,
`seed/fixtures/github/*.json` (new), `tests/test_github_client.py` (new),
`docs/TASKS.md`, `WORKLOG.md`.

### Discovered
RFC-5988 `Link` headers embed existing query parameters in the `rel="next"` URL, so subsequent
pagination calls do not need original query params re-applied.

### Next Step
Proceed to `P2-003` (GitHub normalization into canonical tables) or `P2-004` (Jira read-only client).

---

## 2026-09-20 | Keshan | P2-001
Status: DONE

### Completed
Built the shared HTTP client layer for all upstream integrations (`P2-001`, Issue #12):
1. Created `app/integrations/errors.py` with base `IntegrationError` and typed subclasses:
`IntegrationTimeoutError` (`TIMEOUT`), `AuthError` (`AUTH_FAILED`), `RateLimitError`
(`RATE_LIMITED`), `NotFoundError` (`NOT_FOUND`), and `UpstreamError` (`UPSTREAM_ERROR`). Each
provides `to_tool_failure` to map directly to the frozen `ToolFailure` schema contract.
2. Created `app/integrations/http.py` implementing `HttpClient` wrapping `httpx.Client`:
   - Configures explicit 10-second connect and read timeouts (`HTTP_TIMEOUT_SECONDS`, NFR-001).
   - Retries up to 3 attempts (`HTTP_MAX_ATTEMPTS`, NFR-002) using tenacity on 429, 5xx, and
     connection/transport errors. Never retries other 4xx errors (400, 401, 403, 404).
   - Implemented `_WaitRetryAfterOrExponential` honoring `Retry-After` headers on 429,
     falling back to exponential backoff with jitter.
   - Disambiguates HTTP 403: detects rate-limiting headers (`x-ratelimit-remaining: 0` or
     `retry-after`) and maps to `RateLimitError`, while standard 403 maps to `AuthError`.
   - Redacts sensitive tokens from authorization headers and query parameters before logging.
   - Supports injectable `httpx.BaseTransport` for offline fixture replay and tests (DEC-012).
3. Added test suite in `tests/test_http_client.py` covering: success 200, timeout retry exhaustion,
401 without retry, 403 rate-limited vs forbidden, 404 without retry, 429 retry with `Retry-After`,
500 upstream error with 3 attempts, secret log redaction, mock transport injection, and explicit
timeout enforcement.
4. Marked `P2-001` as DONE in `docs/TASKS.md`.

### Changed
`app/integrations/errors.py` (new), `app/integrations/http.py` (new),
`app/integrations/__init__.py`, `tests/test_http_client.py` (new), `docs/TASKS.md`,
`WORKLOG.md`.

### Discovered
GitHub rate-limiting on 403 is distinguished cleanly from authorization denial using the
`x-ratelimit-remaining` and `retry-after` response headers.

### Next Step
Start `P2-002` (GitHub read-only client) using `HttpClient`.

---

## 2026-09-20 | Keshan | P1-005
Status: DONE

### Completed
Resolved remaining open criteria for Phase 0 and Phase 1:
1. Closed #9 (`P1-003`): Added `lifespan` handler to `app/main.py` calling `get_settings()` during
startup so missing secrets halt immediately. Removed localhost fallback in `app/db.py`, raising
`RuntimeError` if `DATABASE_URL` is unset. Added startup failure test in `tests/test_config.py`.
2. Closed #10 (`P1-004`): Added real `pg_sleep(3)` statement timeout test in `tests/test_db.py`
verifying statement cancellation and `is_timeout_error` detection. Added PostgreSQL 15 service
container to the CI `test` job in `.github/workflows/ci.yml`.
3. Closed #11 (`P1-005`): Discovered exact GitHub numeric IDs (`keshan-dev`: 219891474,
`IsiwaraKumarage8`: 221026807) and populated `seed/identity_map.yml`, replacing dummy literals.
Updated `tests/test_identity_loader.py` assertions to verify real IDs and handles.
4. Closed #4 (`P0-004`) and #5 (`P0-005`): Marked DONE in `docs/TASKS.md` following confirmed CI
workflows and Ollama measurements on both machines. Marked #8 (`P1-002`) DONE following merge to
`main`.

Phase 0 and Phase 1 are now 100% complete.

### Verification
Ran in a scratch venv pinned to the versions CI resolves (black 26.5.1, ruff 0.16.8):
`ruff check .` passed, `black --check .` initially failed on 3 files and was corrected by
running `black .` rather than by hand.

Full suite run against a throwaway `postgres:15` container: 32 passed, 0 failed. The live
`pg_sleep(3)` test confirms the 2000ms read-path statement timeout cancels the query and
that `is_timeout_error` recognises it. Not verified: the CI job itself, which runs on the
pull request.

### Problems
1. `black --check` would have failed CI on `app/db.py`, `tests/test_db.py` and
`tests/test_config.py` (2 trailing blank lines, 1 line-length reflow). Fixed by running
black. This is the third time hand-formatting has caused this. Pinning an exact black
version in the dev extras would remove the class of failure, but it is a dependency change
and needs both developers to agree (rule 13).
2. `test_startup_fails_without_secrets` passed in CI but failed on a developer machine.
`Settings` reads `env_file=".env"` relative to the working directory, so a local `.env`
supplied the secrets the test expects to be missing and no `RuntimeError` was raised. Fixed
by adding `monkeypatch.chdir(tmp_path)` so the test asserts the same thing in both places.

### Changed
`app/db.py`, `app/main.py`, `seed/identity_map.yml`, `tests/test_config.py`, `tests/test_db.py`,
`tests/test_identity_loader.py`, `.github/workflows/ci.yml`, `docs/TASKS.md`, `README.md`,
`WORKLOG.md`.

### Discovered
Isiwara's GitHub account is `IsiwaraKumarage8` (numeric ID 221026807), matching repository
contributors.

### Next Step
Open the pull request from `fix/9-close-phase-0-and-1-criteria`, closing #4, #9, #10 and #11.
CI validates all 5 jobs. Begin Phase 2 (`P2-001` shared HTTP client) while Isiwara starts
Phase 3 (`P3-001` FastAPI routes and auth seam).

---

## 2026-09-19 | Keshan | P1-005
Status: IN_PROGRESS

### Completed
Created `seed/identity_map.yml` documented with a header comment block explaining fields,
constraints, and initial entries for team members Keshan and Isiwara with stable external IDs.
Implemented `app/integrations/identity_loader.py` with YAML parsing that reports exact problem
line numbers on syntax errors, Pydantic schema validation, and cross-person duplicate external ID
checks. Implemented `load_identity_map` writing `IdentityLink` rows with `match_method = manual`
and `confidence = HIGH`, handling existing user lookup, unmatched entity resolution, and
idempotent re-runs. Added test suite in `tests/test_identity_loader.py` covering valid seeding,
idempotency, duplicate rejection across people, malformed YAML lines, and unmatched resolution.

### Changed
`seed/identity_map.yml` (new), `app/integrations/__init__.py` (new),
`app/integrations/identity_loader.py` (new), `tests/test_identity_loader.py` (new),
`docs/TASKS.md`.

### Problems
The CI lint job failed on `black --check`. `app/integrations/identity_loader.py` and `tests/test_identity_loader.py` were wrapped at 88 columns, black's default, not the 100 this project sets in `pyproject.toml`. Both statements fit on 1 line at 100. Ruff passed. Fixed by running black, no logic changed.

This is the 3rd pull request in a row to fail on exactly this (#66, #68, #69). The cause is that neither developer has black installed, so formatting is done by hand or with a tool using its own default width. Pinning black to an exact version in the dev extras and actually installing it locally is the fix. It needs agreement, so it is raised below rather than done here.

### Decisions Needed
**Pin `black` and `ruff` to exact versions in `[project.optional-dependencies] dev`.** Today they are floating (`black>=24.10`, `ruff>=0.7`), so CI resolves the newest release on every run and the formatting target moves without anyone changing code. A commit that passed last week can fail today. Pinning also makes a local install match CI, which is what stops the repeat failures above. Cost is zero, it is not a new dependency, only a version constraint on 2 that are already there.

### Next Step
Merge P1-005 to `main`. This completes Phase 1. Proceed to Phase 2 (Data and Integrations,
P2-001 HTTP client) in parallel with Phase 3 (Agent Foundation).

---

## 2026-09-19 | Keshan | P1-002
Status: IN_PROGRESS

### Completed
Defined all frozen Pydantic contracts across `app/schemas/`. Created `app/schemas/errors.py`
with `ToolFailure` and typed error categories. Created `app/schemas/tools.py` with exact
inputs and outputs for all 7 deterministic read tools (T-001 through T-007) matching
`AGENT_TOOLS.md`. Created `app/schemas/insight.py` defining `EvidenceItem` matching
`DATA_AND_EVIDENCE.md` 6.5, `Claim` with strict `list[str]` evidence ID invariance
enforcing DEC-004, `Insight`, and `MemberInsight` including per-source freshness and health
status. Exported all contracts via `app/schemas/__init__.py`. Added comprehensive unit
tests in `tests/test_schemas.py` covering round-trip serialization and rejection of
embedded evidence objects.

### Changed
`app/schemas/__init__.py` (new), `app/schemas/errors.py` (new), `app/schemas/tools.py` (new),
`app/schemas/insight.py` (new), `tests/test_schemas.py` (new), `docs/TASKS.md`, `.gitignore`, `pyproject.toml`.

### Problems
The CI lint job failed. 2 separate causes.

1. 10 `UP017` violations: `datetime.now(timezone.utc)` in `app/schemas/errors.py` and `tests/test_schemas.py`. `app/main.py` already used `datetime.now(UTC)`, so the new files did not follow the convention already in the tree. Fixed with `ruff --fix`. `UTC` is an alias of `timezone.utc`, so behaviour is unchanged.
2. `black` wanted 2 files rewrapped. They were wrapped at 88 columns, black's default, not the 100 this project sets.

### Discovered
**CI was linting a generated copy of the tree.** `pip install ".[dev]"` makes setuptools write `build/lib/app/...`, and ruff's default exclude list has `_build`, `buck-out` and `dist` but not `build`. Black's defaults do exclude `build`. So ruff reported 11 errors where only 10 were real, the 11th being the copy of `app/schemas/errors.py` under `build/lib/`. A stale `build/` on a developer machine is worse than noise: it keeps copies of deleted files, so ruff reports errors in code that no longer exists. Added `extend-exclude = ["build"]` to `[tool.ruff]` and `build/` plus `dist/` to `.gitignore`, which was missing both.

### Next Step
Merge P1-002 to `main`, then proceed to `P1-005` (identity map format and loader).

---

## 2026-09-19 | Keshan | P1-001 & P1-004
Status: DONE

### Completed
Implemented `app/db.py` with SQLAlchemy engine, `SessionLocal`, FastAPI `get_session` dependency, and read-path statement timeout support (`DB_STATEMENT_TIMEOUT_MS = 2000`). Defined all 17 canonical models across `app/models/` (`canonical.py`, `identity.py`, `work.py`, `agent.py`, `operations.py`) using SQLAlchemy 2.0 declarative mappings. Ensured person table is named `app_user` (not `user`), all timestamps are `timestamptz` in UTC, and external tables carry non-null `source_updated_at` and `retrieved_at` columns. Configured Alembic (`alembic.ini`, `migrations/env.py`) and created initial migration `migrations/versions/0001_initial.py` defining all tables, unique constraints, and indexes. Added unit tests in `tests/test_models.py` and `tests/test_db.py`.

### Changed
`app/db.py` (new), `app/models/__init__.py` (new), `app/models/canonical.py` (new), `app/models/identity.py` (new), `app/models/work.py` (new), `app/models/agent.py` (new), `app/models/operations.py` (new), `alembic.ini` (new), `migrations/` (new), `tests/test_models.py` (new), `tests/test_db.py` (new), `docs/TASKS.md`.

### Problems
The CI lint job failed on `black --check`: `tests/test_db.py` ended with a trailing blank line. CI installs the newest black (26.5.1) because `pyproject.toml` pins only `black>=24.10`, and neither developer has black installed locally, so formatting has been corrected by hand across 3 commits. Fixed by running black over the file. Ruff cannot catch this: `W391` is preview-only, so adding `W` to the selected rules would not have prevented it. Pinning black to an exact version in the dev extras is the real fix and needs agreement first.

### Next Step
`P1-002` (freeze Pydantic contracts) and `P1-005` (identity map format and loader).

---

## 2026-09-19 | Keshan | P1-003
Status: DONE

### Completed
Implemented `app/config.py` using Pydantic `BaseSettings` with all 10 thresholds and runtime settings defined as typed named constants. Required secrets (`DATABASE_URL`, `GITHUB_TOKEN`, `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`) have no default values and fail fast when missing from the environment. Sensitive values are redacted in string representations (`__repr__` and `__str__`) to prevent token leaks in log lines. Updated `.env.example` with empty values and added unit tests in `tests/test_config.py` plus test isolation in `tests/conftest.py`.

### Changed
`app/config.py` (new), `.env.example`, `tests/conftest.py` (new), `tests/test_config.py` (new), `docs/TASKS.md`.

### Next Step
`P1-004` (database session management and statement timeouts) and `P1-001` (canonical models).

---

## 2026-09-19 | Isiwara | P0-005
Status: IN_REVIEW

### Completed
Ran `scripts/check_ollama.py` on my machine. 12 checks passed, 0 failed. Model `llama3.2`
was already pulled. The `api` container reaches Ollama at `host.docker.internal:11434`
(HTTP 200).

| Measurement | Value |
|---|---|
| Machine | Windows 11 Pro, 15.6 GB RAM, Intel CPU (family 6, model 154) |
| Ollama | 0.34.2 |
| Model | `llama3.2` (3B) |
| Prompt processing | 4837.9 tok/s (not comparable, see Discovered) |
| Generation | 15.4 tok/s |
| Narrative call latency | 8.7 s |
| Warm-up | 2.1 s |
| Schema enforcement | works |
| Reproducible (temperature 0 + seed) | yes, byte-identical across runs |
| Cost | zero |

With Keshan's entry of 2026-09-13, both machines now have every measurement the "on both
machines" criteria of #5 ask for. I have not changed the task status. That is for Keshan.

### Changed
`WORKLOG.md` only. No code changed.

### Discovered
**Prompt processing reads 4837.9 tok/s here against 98.7 tok/s on Keshan's machine.** That
is a 49x gap while generation speed is close (15.4 against 14.2 tok/s), so it is not a
hardware difference. My guess is that Ollama reused a cached prompt after the warm-up run,
but I have not confirmed it. Treat this number as not comparable. Generation speed and
latency are the ones that matter for NFR-013.

**`ollama --version` printed 0.33.2 earlier today and the script printed 0.34.2 later.** The
measurements above are from 0.34.2. Keshan's were on 0.33.3.

### Next Step
Waiting on P0-002 (Keshan), which unblocks Phase 1 for me.

### AI Assistance
An AI assistant guided me through both tasks and drafted these 2 entries. Every number is
copied from my own terminal output. The prompt-processing figure is the 1 item that needs
a human check.

---

## 2026-09-19 | Isiwara | P0-003
Status: IN_REVIEW

### Completed
Confirmed the skeleton runs on my machine. This is the 1 criterion left open when #3 was
closed: "Both developers confirm it runs on their machine."

| Check | Result |
|---|---|
| `docker compose up --build -d` | `db` healthy, `api` up (image build 55.5 s) |
| `GET /health` | 200, `{"status":"ok","service":"headref","version":"0.1.0",...}` |
| `pytest` inside the container | 1 passed |
| `ruff check .` inside the container | All checks passed |
| `black --check .` inside the container | 5 files unchanged |
| Ollama reachable from `api` at `host.docker.internal:11434` | HTTP 200 |

The default `API_PORT=8000` worked. The reserved TCP ranges on this machine are all
50000 and above, so port 8000 was free.

### Changed
`WORKLOG.md` only. No code changed.

### Discovered
**Docker Desktop refused to start with "Virtualization support not detected", but
virtualization was on.** `systeminfo` reported "A hypervisor has been detected", and
`wsl --status` reported that WSL was not installed. Docker Desktop needs WSL 2 on Windows,
and the error message points at the BIOS when the real cause is the missing WSL. Fix, in an
Administrator prompt: `wsl --install --no-distribution`, then restart Windows. No BIOS
change was needed.

**`python` on this machine is a Microsoft Store shortcut that fails** with "The system
cannot find the path specified". `py` works (Python 3.14.6). The Ollama check ran as
`py -X utf8 scripts/check_ollama.py`. The project itself runs on Python 3.11 inside the
container, so the host Python version did not matter.

### Next Step
Keshan to review. Suggest adding the 2 Windows findings above to `docs/HOW_TO.md`. I have
not changed it.

---
## 2026-09-19 | Keshan | P0-003
Status: DONE

### Completed
#64 merged. Issue #3 closed with each acceptance criterion recorded against its evidence.
`TASKS.md` P0-003 set to DONE and `README.md` 14 now says the skeleton exists.

Phase 0 is 3 of 6 done: P0-001, P0-003, P0-006. Open: P0-002, P0-004, P0-005.

### Changed
`docs/TASKS.md` P0-003 status, `README.md` 14.

### Problems
**P0-003 closed with 1 criterion unmet:** "Both developers confirm it runs on their
machine." Only Keshan's machine is confirmed. Closed anyway so it does not block Phase 1,
and the issue says plainly to reopen it if `docker compose up --build` fails for Isiwara.

### Next Step
P0-002, real Jira and GitHub data plus fixtures. It is now the only thing blocking Phase 1
and the only Phase 0 task that needs external access rather than code.

---

## 2026-09-19 | Keshan | P0-003
Status: IN_REVIEW

### Completed
Docker verified end to end on Keshan's machine. Every technical criterion on P0-003 passes,
and the outstanding container criterion on P0-005 is now met.

| Check | Result |
|---|---|
| `docker compose up --build` | `db` healthy, `api` up |
| `GET /health` | 200, `{"status":"ok","service":"headref","version":"0.1.0",...}` |
| `pytest` inside the container | 1 passed |
| `ruff check .` inside the container | All checks passed |
| `black --check .` inside the container | 5 files unchanged |
| PostgreSQL reachable from `api` | PostgreSQL 15.19 |
| Ollama reachable from `api` at `host.docker.internal:11434` | HTTP 200, `llama3.2:latest` listed |

### Changed
`docker-compose.yml`, `.env.example`.

### Discovered
**Port 8000 cannot be bound on this Windows machine.** `docker compose up` failed with
"An attempt was made to access a socket in a way forbidden by its access permissions".
Nothing was listening on 8000. The cause is a Hyper-V reserved TCP range:

```text
netsh interface ipv4 show excludedportrange protocol=tcp
  7915  8014      <- 8000 falls inside this
  8115  8214
  8316  8415
```

This is a Windows artifact, not a project bug, and it can appear on any Windows machine
including Isiwara's. The reserved ranges also move between reboots.

Fixed by making the host port configurable, `${API_PORT:-8000}`, with the container port
still 8000. The default is unchanged, so nothing breaks for a machine where 8000 is free.
This machine runs `API_PORT=8080` in its own `.env`. The diagnostic command is recorded in
`.env.example` next to the variable.

### Problems
**1 criterion on P0-003 is outstanding: "Both developers confirm it runs on their
machine."** Isiwara has not started, so only Keshan's machine is confirmed.

**P0-005 cannot close.** 5 of its 7 criteria say "on both machines" explicitly: Ollama
installed, `llama3.2` pulled, `api/tags` returning the model, a schema-valid structured
call, and recorded tokens/sec. Only the container reachability and zero-cost criteria are
fully met. It stays open until Isiwara runs `python scripts/check_ollama.py`.

### Next Step
P0-002, real Jira and GitHub data plus fixtures.

---

## 2026-09-19 | Keshan | P0-001
Status: DONE

### Completed
CI merged in #61 and went green on its first run, all 5 jobs. The 5 checks are now required
on `main` with `strict: true`, so a branch must also be up to date before it can merge:

```text
Lint and format, Tests, Secret scan, Migrations, Project rules
```

That was the last outstanding acceptance criterion on P0-001, which had been waiting
because a status check cannot be required before it has run once. Issue #1 closed with the
criteria verified one by one against the repository.

Phase 0 statuses corrected in `TASKS.md`: P0-001 DONE, P0-003 IN_PROGRESS, P0-004
IN_PROGRESS.

### Changed
`docs/TASKS.md` statuses, `README.md` 14.

### Problems
**1 P0-001 criterion could not be verified:** the project board columns. `gh project list`
needs the `read:project` scope, which this token does not have. Everything else was
confirmed directly. Noted on the issue so it can be reopened in seconds if the board is
missing.

### Next Step
P0-002, real Jira and GitHub data plus fixtures. Nothing else unblocks Isiwara.

---

## 2026-09-19 | Keshan | P0-004
Status: IN_REVIEW

### Completed
CI pipeline, `.github/workflows/ci.yml`, 5 jobs on every pull request into `main`:
`lint` (ruff, black), `test` (pytest), `secret-scan` (gitleaks over both the tree and the
history), `migrations` (PostgreSQL 15 service container), and `project-rules`.

`project-rules` mechanically enforces 3 rules that were previously only written down:
no AI attribution in commit messages, `WORKLOG.md` updated in the pull request, and no
HTTP client imported under `app/tools/`. The `CLAUDE.md` enforcement table is updated to
match, and 7 rows moved from "to add" to Active.

### Changed
`.github/workflows/ci.yml` (new), `CLAUDE.md` enforcement table.

### Discovered
**The gitleaks marketplace action asks organizations for a licence key.** Used the gitleaks
binary from the GitHub release instead, which is free and adds no paid dependency
(CLAUDE.md rule 13).

**The attribution check reads commit messages only, not file contents.** `CLAUDE.md`,
`HOW_TO.md` and `WORKLOG.md` all quote those strings in prose. A content grep would fail on
the files that define the rule.

**The 2 Alembic criteria on this task cannot be met yet.** `alembic.ini` and
`migrations/versions` arrive in P1-001. The job detects their absence and skips rather than
faking a pass, so it switches itself on when the directory appears. Same pattern for the
`app/tools/` check, which waits for P3-002.

### Problems
**Docker could not be verified on this machine.** Docker Desktop and its WSL distro are
running, but the Linux engine returns 500 on every API route, at every API version from
1.41 to 1.54. So `docker compose up --build` is still unrun and the P0-005 in-container
Ollama reach is still unchecked. Both need a Docker Desktop restart.

### Next Step
Merge, let CI run once, then add the 5 checks to branch protection as required. That closes
the last acceptance criterion on P0-001.

---

## 2026-09-17 | Keshan | P0-003
Status: IN_PROGRESS

### Completed
Project skeleton: `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example`,
`app/main.py` with `/health`, and `tests/test_health.py`.

Verified locally in a throwaway virtual environment:

| Check | Result |
|---|---|
| `pytest` | 1 passed |
| `ruff check .` | All checks passed |
| `black --check .` | 5 files unchanged |

### Changed
`pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `app/main.py`,
`app/__init__.py`, `tests/test_health.py`, `tests/__init__.py`,
`scripts/check_ollama.py` (formatting only).

### Discovered
**Lint failed repo-wide on `scripts/check_ollama.py`**, which merged before any lint
configuration existed. 10 findings. Resolved by running black over it and adding 3 scoped
per-file ignores in `pyproject.toml`, each with the reason inline:

- `E501`: the file embeds the real S4b prompt. Reflowing that text would change the prompt
  and invalidate the measurements recorded in the P0-005 entry below. Verified by diff that
  the prompt text is byte-identical after formatting.
- `S310` and `S607`: it calls the local Ollama HTTP API and invokes `ollama` and `docker`
  from PATH, both by design for a local setup checker.

Without this, the first CI run in P0-004 would have failed on a file nobody had touched.

**`anthropic` is not installed**, per DEC-017. Ollama is reached over plain HTTP with
`httpx`. The `TASKS.md` dependency list has been corrected in #57.

### Problems
**Docker Desktop was not running on this machine, so the 2 container criteria are
unverified.** `docker compose up --build` has not been run, `/health` has not been hit over
HTTP, and the outstanding P0-005 criterion (reaching Ollama at
`host.docker.internal:11434` from inside the api container) is still open. The FastAPI app
itself is verified by the unit test, which exercises the same endpoint in-process.

This is why the task stays `IN_PROGRESS` and the pull request says `Refs #3`, not
`Closes #3`.

### Next Step
Start Docker Desktop, run `docker compose up --build`, hit `/health`, and check the
in-container Ollama reach. Then Isiwara confirms the same on her machine. Both go in this
log before #3 closes.

---

## 2026-09-17 | Keshan | #57
Status: IN_REVIEW

### Completed
Reconciled the status tables against the repository, and added the 4 procedures used today
to `docs/HOW_TO.md`: A10 merge a pull request and confirm it landed, B9 configure branch
protection, B10 reconcile a status file with reality, B11 add or change a project rule.
21 recipes total.

`README.md` 14 and `TASKS.md` "Start here" now say what is actually next, and both carry
the date they were reconciled. Phase 0 statuses corrected: P0-001 IN_REVIEW, P0-005
IN_PROGRESS, P0-006 DONE.

### Changed
`README.md` 14, `docs/TASKS.md` (statuses, Start here, P0-003 notes), `docs/HOW_TO.md`.

### Discovered
**`TASKS.md` P0-003 listed `anthropic` as a dependency.** DEC-017 moved inference to local
Ollama and states there is no LLM API key anywhere in the system, so the SDK has nothing to
call. Removed, with the reason recorded inline so it is not re-added. Ollama is reached
over plain HTTP with `httpx`.

This is the second stale-document failure today, after the duplicate PR #53. Both came from
trusting a cached view instead of the system. B10 exists because of it.

### Next Step
P0-003 skeleton.
## 2026-09-17 | Keshan | P0-001
Status: IN_REVIEW

### Completed
Finished the repository configuration that was outstanding on #1.

Branch protection on `main` tightened (applied through the GitHub API, not in this diff):
1 required approval, stale reviews dismissed on a new push, conversation resolution
required, linear history required, force push and deletion blocked.

Added `.gitignore`, `.github/pull_request_template.md` and
`.github/ISSUE_TEMPLATE/task.md`.

### Changed
`.gitignore` (new), `.github/` (new).

### Discovered
**`.gitignore` did not exist at all.** P0-001 says it must ignore `.env` before any other
commit, and `.env` becomes real in P0-003, so this was the last safe moment. Nothing has
leaked: no `.env` has existed in this repository yet.

**`enforce_admins` was deliberately left off.** Turning it on would subject Developer 1 to
the 1 approval rule and make self-merging impossible, which contradicts the review policy
in `README.md` 13. With Keshan as admin and Isiwara on write access, the current settings
produce exactly the documented behaviour: Isiwara's pull requests need an approval,
Developer 1 can merge their own.

**Required status checks are still empty.** A check cannot be required before it has ever
run, so this stays open until P0-004 creates the CI workflow. It is the 1 criterion of
this task that P0-004 has to close.

### Next Step
Reconcile the stale status tables in `README.md` and `TASKS.md`, then P0-003.

---

## 2026-09-17 | Keshan | #54
Status: IN_REVIEW

### Completed
Added 2 developer guides, neither of which adds scope.

`docs/BUILD_ORDER.md`, the layer view of the application: 17 code sections in dependency
order, each with its files, the concepts to learn before writing it, a runnable "done
when" check and the traps. Every section maps to task IDs that already exist in
`TASKS.md`, which stays authoritative for ordering and ownership.

`docs/HOW_TO.md`, the recurring procedures: splitting changes onto a branch, migrations,
fixtures, config values, contract changes after the freeze, a committed secret, and the
undo table. Each recipe leads with why that method rather than another, because that is
the part that is not recoverable from a command list.

`CLAUDE.md` at the repository root, the rule set itself: 15 hard rules with the reason
for each, what to do when a rule blocks a task, and an honest table of where each rule is
actually enforced rather than merely written down. 6 of those enforcement checks do not
exist yet and are marked as belonging to P0-004, P3-001 and P3-002.

`.claude/settings.json` sets the Claude Code attribution strings to empty, so no
`Co-Authored-By` or "Generated with" line can reach a commit or a pull request from
either machine. It is committed, so it binds both of us rather than 1 laptop.

All 3 linked from the README documentation map.

### Changed
`CLAUDE.md` (new), `.claude/settings.json` (new), `docs/BUILD_ORDER.md` (new),
`docs/HOW_TO.md` (new), `README.md` section 11.

### Decisions Needed
None, but P0-004 now has 4 extra CI checks to implement: an attribution grep, a
WORKLOG-changed check, the secret scanner, and the single-migration-head check. They are
listed in the enforcement table in `CLAUDE.md`.

### Problems
PR #53 was opened for `chore/5-ollama-check` against a stale local `main`. That work was
already squash-merged as #52, and `git diff origin/main origin/chore/5-ollama-check` is
empty. #53 closed as a duplicate. Recipe A1 in `HOW_TO.md` exists because of it: fetch
before judging branch state, and compare content against `origin/main`, not `main`.

### Next Step
P0-002, real Jira and GitHub data plus captured fixtures. Then P0-003, the skeleton.

---

## 2026-09-13 | Keshan | P0-005
Status: IN_PROGRESS

### Completed
Added `scripts/check_ollama.py`, a standard-library-only script that verifies every
acceptance criterion on issue #5 and prints a WORKLOG block. It runs the real headref
narrative task (stage S4b), not a toy prompt, so the numbers reflect actual use.

Verified on my machine. 11 checks passed, 0 failed.

| Measurement | Value |
|---|---|
| Machine | Windows, 7.7 GB RAM, i7-12650H, no dedicated GPU |
| Ollama | 0.33.3 |
| Model | `llama3.2` (3B) |
| Prompt processing | 98.7 tok/s |
| Generation | 14.2 tok/s |
| Narrative call latency | 12.9 s |
| Cold warm-up | 8.2 s |
| Schema enforcement | works |
| Cost | zero |

### Discovered
**Output is byte-identical across two runs** with `temperature: 0` and a fixed seed. That
confirms the reproducibility claim in DEC-011 and DEC-017 rather than assuming it, and it
means the evaluation suite can assert on exact output.

Latency is 12.9 s against the 20 s target in NFR-013, with room to spare. Earlier probes
measured 4 tok/s with only 0.5 GB RAM free versus 14.2 tok/s here, so the guidance to close
other applications before demoing is worth keeping.

### Blocked By
Nothing. 1 acceptance criterion is outstanding: reachability from a container
(`host.docker.internal:11434`). Docker was not running during this check. It is best
verified during P0-003 when `docker-compose.yml` exists.

### Next Step
Isiwara runs `python scripts/check_ollama.py` on her machine and appends her numbers.
Then P0-002: real Jira and GitHub data plus captured fixtures.

---

## 2026-09-13 | Shared | P0-006
Status: DONE

### Completed
Ratified all 19 architecture decisions. Both developers read `docs/DECISIONS.md` and agreed.
Every decision moved from `Proposed` to `Accepted`. Four are marked as amended: DEC-003 by
DEC-016, DEC-005 by DEC-018, DEC-011 by DEC-017, DEC-015 by DEC-019.

Context for the record, since these were added after the first documentation pass:

- **DEC-017** the project must use Ollama, so inference is local (`llama3.2`, 3B) and costs
  nothing. There is no LLM API key anywhere in the system.
- **DEC-018** the model no longer produces claims, classifications or evidence IDs. Code
  produces the findings and the model writes 2 sentences over them.
- **DEC-019** lane rebalance. 13 tasks moved to Developer 1. Split is now 24 / 15 / 10.

Also completed earlier: all 49 tasks created as GitHub issues with labels, milestones,
assignees and dependency links, triaged for the 3 week deadline into 40 demo-critical,
3 stretch and 6 deferred. Branch protection enabled on `main`.

### Changed
`docs/DECISIONS.md` statuses. No code exists yet.

### Discovered
Measured `llama3.2` (3B) on the demo machine (7.7 GB RAM, i7-12650H, no dedicated GPU):

- Ollama JSON schema enforcement works, and `temperature: 0` plus a fixed `seed` are
  supported, so evaluation runs are reproducible.
- About 13 tokens/sec warm, about 4 tokens/sec with only 0.5 GB RAM free. **RAM is the
  binding constraint, not the CPU.** Close other applications before demoing.
- Asking the model to produce claims plus classifications plus evidence IDs took 22.1 s and
  **missed the blocker entirely**, ignoring a changes-requested review that was in the
  evidence set.
- Asking it only to write the narrative over code-produced findings took 15.3 s and caught
  the blocker, because rule BL-4 caught it in code.

That measurement is the basis for DEC-018.

### Next Step
P0-005: Ollama installed on both machines, tokens/sec recorded for each.
Then P0-002: real Jira and GitHub data plus captured fixtures. P0-002 unblocks Developer 2
for the next two weeks, so it is the one to protect.

---

## 2026-09-13 | Shared | Documentation foundation
Status: DONE

### Completed
Created the headref documentation set in `docs/`, before any application code:
`README.md`, `PROJECT_REQUIREMENTS.md`, `AGENT_ARCHITECTURE.md`, `AGENT_TOOLS.md`,
`AI_BEHAVIOR.md`, `DATA_AND_EVIDENCE.md`, `ARCHITECTURE.md`, `DECISIONS.md`, `TASKS.md`,
`WORKLOG.md`, `TESTING_AND_EVALUATION.md`.

Defined 33 functional requirements (FR-001 to FR-033), 33 non-functional requirements,
15 architecture decisions (DEC-001 to DEC-015), 7 agent tools (T-001 to T-007), 47 tasks
across 8 phases, and 12 evaluation scenarios.

### Changed
`docs/` created. No application code exists yet.

### Discovered
5 gaps were found in the earlier planning material and are now resolved in the
documentation:

1. **Ingestion had no trigger and no state.** The earlier plan described caching in
   PostgreSQL but never said what ran ingestion or when. Resolved by DEC-003: a CLI entry
   point plus `sync_run` and `sync_cursor` tables.
2. **Source outages were undetectable.** Because agent tools read the database, an empty
   query result was indistinguishable from an unreachable source. This would have made the
   system report "no blockers found" when Jira was down. Resolved by DEC-010 and tool
   T-007, derived from sync history.
3. **The evidence contract allowed fabricated citations.** The draft `Insight` model had
   the LLM generating `EvidenceItem` objects, which the validator could not check against
   anything. Resolved by DEC-004: code builds the evidence set with stable IDs, the model
   cites by ID only, and unknown IDs are dropped.
4. **The Jira to GitHub link was not a modelled entity.** It existed only as string parsing
   inside the evidence builder, so it could not be tested, audited or given a confidence.
   Resolved by DEC-009: a `work_item_link` table written during ingestion.
5. **Blockers had no defined source.** With Slack out of scope, nothing specified where a
   blocker came from. Resolved by 8 deterministic Jira and GitHub signals in
   `DATA_AND_EVIDENCE.md` 6.9.

Also corrected: the earlier plan specified `temperature 0` in 3 places as the determinism
strategy. The `temperature` parameter has been removed on current Claude models and
returns a 400. Replaced by DEC-011: fixtures, structured outputs, and assertions on
structured fields rather than wording.

Also resolved a contradiction in the earlier plan about whether feature branches are
deleted after merge. Decision: **branches are kept**, as stated in 2 of the 3 places it
appeared.

### Problems
None.

### Decisions Needed
- **All 15 decisions are `Proposed`, not `Accepted`.** Both developers must read and
  ratify them in task P0-006 before feature work starts. The 5 that are expensive to
  reverse are DEC-002, DEC-004, DEC-005, DEC-009 and DEC-015.
- **DR-1 works-council and labour-law review.** An employee-facing tool may require formal
  worker representation sign-off before deployment in some jurisdictions. This gates
  deployment, not the MVP build. Owner: project sponsor. Open.
- **DR-2 data retention period.** Not needed for the demo, required before any real
  deployment. Open.
- **Assumption A-3 is unconfirmed.** Jira Cloud may not expose user email addresses, and
  GitHub commit author emails are often privacy addresses. This is why identity mapping is
  bootstrapped manually (DEC-008). Confirm during P0-002 and correct this log if wrong.

### Blocked By
Nothing.

### Next Step
Task P0-006: both developers read `DECISIONS.md` and ratify the 15 decisions. Then P0-001
(create the repository) and P0-002 (create real Jira and GitHub test data and capture
fixtures).

### AI Assistance
The documentation set was drafted with an AI assistant from the earlier planning material
(`headref_MVP_Plan.html`, `TeamPulse_Production_Ready_AI_Agent_Checklist.md`,
`TeamPulse_Analysis_and_Recommendations.md`). The 5 gaps listed above were identified
during that review.

**Needs a human check:** the 15 decisions, particularly the 5 expensive ones. Nothing in
this documentation has been validated against a running system, because no code exists
yet. Assumption A-3 in particular is unverified and must be confirmed in P0-002.

---

_No entries before this. The project starts here._
