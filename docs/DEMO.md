# headref demo script

The rehearsal script for P7-004 (#47), written for the workspace UI on 2026-10-22. It
replaces the team-page steps in the issue (DEC-026). **Not rehearsed yet:** both
developers run it end to end before the v0.1.0 tag, and fix what breaks.

## Before the demo

1. Close other applications. On an 8 GB machine the model is memory-bound (P7-002 in
   `WORKLOG.md`): with memory full, the uncached p95 was 75 s on `llama3.2` and 8.2 s on
   `llama3.2:1b`. Decide the model before the demo, not during it.
2. Pre-warm the model: `ollama run llama3.2 "hi"` (or the model you chose).
3. Start the stack and check it: `docker compose up -d`, then
   `docker compose exec api alembic upgrade head`, and `/health` answers 200.
4. Sign in with GitHub once, then seed the demo workspace:
   `docker compose exec api python seed/seed_demo.py --owner <your-github-login>`.
   Use a fresh database: older local seeds can leave stray demo rows.
5. For steps 4 and 5, use a real workspace connected to the sandbox (Jira project `HRS`,
   repository `keshan-dev/headref-sandbox`), synced in the last few minutes.

## The 7 steps

| Step | Where | What to show | What must be true |
|---|---|---|---|
| 1. Who needs attention | Workspace page, `/w/<id>` | The count tiles, then Blockers and Jira and GitHub disagree | Counts are for the workspace, never per person (rule 15) |
| 2. 1 member, with evidence | Pick a member in the filter bar | Likely current work; open "Show evidence"; click 1 evidence link | The link opens the record the evidence names (#35) |
| 3. A blocker | Blockers section | A blocker and its evidence chain | It names the work item or pull request, not the person |
| 4. A disagreement | "Jira and GitHub disagree" | For example "Jira shows HRS-1 In Progress, but linked pull request is merged" | headref reports it and does not choose a side |
| 5. Jira unavailable, not empty | A real workspace | Revoke headref's access to the Jira site in your Atlassian account's connected apps, then Sync now | The Jira run shows Failed with `AUTH_FAILED`; answers say Jira is unavailable; no "no blockers" claim. Connections shows "Reconnect needed" once the stored access token is refreshed and rejected |
| 6. Unmatched identities | People screen, `/w/<id>/people` | "Not matched to anyone" | Unmatched commits are attributed to nobody (EV-07) |
| 7. The hallucination defence | A terminal | The query below | A rejected narrative is recorded, and the user saw the code-built summary instead |

Step 7, on the database the app uses:

```bash
docker compose exec db psql -U headref -d headref -c \
  "select id, question_type, error_type, dropped_claims from agent_run
   where dropped_claims is not null order by id desc limit 5;"
```

Reconnect Jira after step 5: the workspace's Connections card, Reconnect.

## Success criteria before tagging v0.1.0

`PROJECT_REQUIREMENTS.md` 2.9, as of 2026-10-22:

| Criterion | State |
|---|---|
| 3 question types answered with labelled claims, evidence, confidence, conflicts | Met: EV-01 to EV-12 and the P6-005 end-to-end test |
| Every claim resolves to evidence with a working link | Link data checked against every record on the real workspace; the click-check (#35) is open |
| EV-01 to EV-12 pass | Met: `pytest -m eval tests/eval`, 19 of 19 |
| A broken Jira token shows "unavailable", never "no blockers" | Met in EV-04B; shown live in step 5 |
| No attribution by display name | Met: EV-07, identity tests |
| Runs from compose plus 2 commands in under 10 minutes | 148 s to the sign-in page, scripted (P7-003); the timed run with sign-in is open (#46) |
| Cost per insight call under 0.10 USD | Met: 0 USD, local model (DEC-017) |
| CI green on `main` | Met at the time of writing; check again before tagging |
| `WORKLOG.md` an honest record of who built what | Each entry names its author; work done for the other developer's lane says so. A final entry from both is open |
