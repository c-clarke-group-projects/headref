# headref How To

The recurring procedures in this project, each with the reasoning behind it.

**Why this file exists.** `WORKLOG.md` records what happened. This file records **how it
was done, and why that method rather than another**. When you look back at a merged change
6 weeks later, the log tells you the change existed. This tells you how to do it again.

**How to use it.** Find the recipe, follow the steps, run the check. If you do something
recurring that is not here, add a recipe in the same pull request.

**Recipe format**

| Part | Meaning |
|---|---|
| When | The situation that calls for this |
| Why this way | The reasoning. This is the part worth reading |
| Steps | Exact commands |
| Check | How you know it worked |
| Worked example | A real instance from this repository, where one exists |

---

# Part A. Git and change management

---

## A1. Check the real state before you judge it

**When.** Before opening a pull request, before saying a branch is unmerged, before
deciding anything from `git log` or `git status`.

**Why this way.** `main` on your machine is a cached copy. It is only as current as your
last fetch. Every command that compares against `main` compares against that cached copy,
so a stale local `main` makes `git log main..HEAD` report work as unmerged when it was
merged days ago. The fix costs 1 command and the mistake costs a duplicate pull request.

Compare against `origin/main`, not `main`, whenever you want the truth about the remote.

**Steps**

```bash
git fetch --all --prune
git log --oneline origin/main -5          # what is actually on the remote main
git log --oneline origin/main..HEAD       # what my branch adds that main does not have
git diff --stat origin/main...HEAD        # the content difference
```

**Check.** If `git diff --stat origin/main <your-branch>` prints nothing, the content is
already on `main` no matter what your commit graph looks like. A squash merge rewrites
your commits into 1 new commit, so your original commit hashes will never appear in
`main`'s history. Judge by content, not by hash.

**Worked example, 2026-09-17.** `git log main..HEAD` showed 1 commit on
`chore/5-ollama-check`, so PR #53 was opened for it. The work had already been
squash-merged as PR #52 that was not fetched locally, and
`git diff origin/main origin/chore/5-ollama-check` was empty. PR #53 was closed as a
duplicate. Fetching first would have prevented it.

---

## A2. Split unrelated changes onto their own branch

**When.** You are on a branch for task X and you have edits belonging to task Y in your
working tree, committed or not.

**Why this way.** A pull request should answer 1 question so a reviewer can say yes or no
to 1 thing. Mixed changes force the reviewer to approve work they did not want to review,
and the project squash merges, so a mixed branch becomes 1 commit that can never be
reverted cleanly. Splitting takes 5 minutes now and saves an unpickable revert later.

There are 3 cases. Pick by whether the unrelated work is committed.

**Case 1. Uncommitted, and the branches share a base**

```bash
git stash push -u -m "docs work"      # -u includes untracked files
git checkout main
git fetch && git pull --ff-only
git checkout -b docs/54-developer-guides
git stash pop
```

**Case 2. Uncommitted, and the files also changed on the branch you are leaving**

`git stash pop` will conflict, because the stash was taken against a different version of
the file. Copy the new files aside, restore the branch, then re-apply by hand on the new
branch. Slower, but there is nothing to untangle.

```bash
cp docs/NEW_FILE.md /tmp/                     # park the new file
git checkout -- README.md WORKLOG.md          # discard the edits on this branch
rm docs/NEW_FILE.md
git checkout main && git pull --ff-only
git checkout -b docs/54-developer-guides
cp /tmp/NEW_FILE.md docs/                     # bring it back
# then re-apply the README and WORKLOG edits against this branch's version
```

**Case 3. Already committed on the wrong branch**

```bash
git log --oneline -3                           # note the hash to move
git checkout main && git pull --ff-only
git checkout -b docs/54-developer-guides
git cherry-pick <hash>
git checkout <wrong-branch>
git reset --hard HEAD~1                        # only if the branch is not shared
```

Never `reset --hard` a branch someone else has pulled. Use `git revert` there instead.

**Check**

```bash
git diff --stat origin/main...HEAD    # only the files this task should touch
git status                            # clean
```

**Worked example, 2026-09-17.** `docs/BUILD_ORDER.md` plus README and WORKLOG edits were
sitting on `chore/5-ollama-check`. Case 2 applied, because that branch's commit had also
modified `WORKLOG.md` and a stash pop onto `main` would have conflicted. The new file was
copied to a scratch directory, the 2 edits were discarded with `git checkout --`, and
everything was re-applied on `docs/54-developer-guides` cut from a freshly pulled `main`.

---

## A3. Start a task

**When.** Beginning any unit of work.

**Why this way.** The branch name carries the issue number so that 6 months later a
`git log` line can be traced back to the requirement it implements. Without the number,
that trail only exists in someone's memory.

**Steps**

1. Pick the task in `TASKS.md`. Confirm every dependency is `DONE`.
2. Read the acceptance criteria. They are the definition of finished, not your own sense
   of it.
3. Check `DECISIONS.md` for a constraint that applies.
4. Cut the branch from a current `main`:

```bash
git fetch && git checkout main && git pull --ff-only
git checkout -b feat/12-github-client
```

Naming is `type/<issue-number>-<short-desc>`. Types: `feat`, `fix`, `chore`, `docs`,
`test`, `refactor`.

**Check.** `git status -sb` shows the new branch and a clean tree before you write a line.

---

## A4. Write a commit message

**When.** Every commit.

**Why this way.** Conventional Commits makes the history greppable and makes it obvious
when a change is bigger than its task. `Closes #n` is what links the code to the
requirement permanently, and it closes the issue on merge so the board never drifts from
reality.

**Format**

```text
type(scope): imperative summary under 72 characters

Why the change was needed. Not what the diff shows, the diff already shows that.
Anything a reviewer would otherwise have to ask about.

Closes #12
```

**When not to use `Closes`.** If the task has acceptance criteria still outstanding, use
`Refs #12` instead. Closing an issue that is not finished loses the outstanding work.

**Worked example.** PR #53's branch carried an outstanding criterion on issue #5
(reachability from inside a container), so it used `Refs #5`, not `Closes #5`.

**Check.** `git log --oneline -5` reads as a list of changes, not a list of "wip" and
"fix".

---

## A5. Open a pull request

**When.** The task meets its acceptance criteria and CI is green locally.

**Why this way.** Small pull requests get real review. Large ones get approved unread, and
an unread approval is worse than no review because it creates false confidence. The
project targets under 400 changed lines for this reason.

**Steps**

```bash
git push -u origin <branch>
gh pr create --base main --title "type(scope): summary" --body-file <file>
```

The body states what changed, why, what is **not** covered, and how to verify it. Write
the body in a file rather than inline so quoting and markdown survive.

**Rules for this project**

| Rule | Detail |
|---|---|
| Review | Developer 2's pull requests need Developer 1's approval. Developer 1 may merge their own |
| Merge | Squash merge |
| Branches | Kept after merge, not deleted |
| CI | Lint, tests, secret scan and migration check must pass |
| Log | `WORKLOG.md` is updated in the same pull request |

**Check.** `gh pr view --json state,mergeable` and the CI status on the pull request page.

---

## A6. Update WORKLOG.md, and resolve its conflicts

**When.** Every pull request, without exception.

**Why this way.** The reviewer should see the change and the explanation of the change in
1 place. Writing the log afterwards never happens, and a log written from memory a week
later is fiction.

**Steps.** Add an entry at the top, directly under the header block, using the template in
the file. Keep it short. Omit empty sections.

**The conflict.** Both developers add an entry at the top of the same file, so git reports
a conflict on almost every parallel pull request. **It is never a real conflict.** Keep
both blocks, newest first, and delete the markers.

```bash
git checkout main && git pull --ff-only
git checkout <your-branch>
git merge main
# open WORKLOG.md, delete <<<<<<<, =======, >>>>>>>, keep both entries
git add WORKLOG.md && git commit
```

**Check.** Both entries survive and the file has no conflict markers left:

```bash
grep -n "<<<<<<<\|=======\|>>>>>>>" WORKLOG.md
```

---

## A7. Keep a branch current with main

**When.** `main` moved while you were working, or CI reports a conflict.

**Why this way.** Rebase for a branch only you have, because it produces a straight history
that is easy to read. Merge for a branch someone else has pulled, because a rebase rewrites
hashes and anyone who pulled the old ones gets a mess. The question is never which is
better in general, only whether anyone else has your commits.

**Steps**

```bash
git fetch origin

# branch is yours alone
git rebase origin/main

# branch is shared, or you are unsure
git merge origin/main
```

**Check.** `git log --oneline --graph -10` and a green CI run after the push. A rebase
needs `git push --force-with-lease`, never plain `--force`, because `--force-with-lease`
refuses if someone else pushed in the meantime.

---

## A8. When your work depends on unmerged work

**When.** Task B needs code from task A, and A is still in review.

**Why this way.** Waiting idles a developer for a day. Copying A's code into B duplicates
it and creates a conflict when A merges. A stacked branch avoids both: B is cut from A, and
GitHub automatically retargets B's pull request onto `main` when A merges, at which point
B's diff collapses to only its own changes.

**Steps**

```bash
git checkout feat/12-http-client        # branch A, already pushed
git checkout -b feat/13-github-client   # branch B, cut from A
# work, commit, push
gh pr create --base feat/12-http-client --title "..."
```

Say in B's description that it is stacked on A and must not merge first.

**Check.** After A merges, B's pull request shows only B's files. If it still shows A's,
rebase B onto the updated `main`.

**Prefer to avoid this.** 2 stacked branches are manageable. 3 are not. If you find
yourself stacking a third, the first pull request is too big and should be split instead.

---

## A9. Undo safely

**When.** Something went in wrong.

**Why this way.** The safe operations add a new commit and keep history intact. The unsafe
ones rewrite history that other people may already hold. Prefer the safe form whenever the
branch has been pushed.

| Situation | Command | Safe on a shared branch |
|---|---|---|
| Last commit message wrong, not pushed | `git commit --amend` | No |
| Uncommitted change to 1 file | `git checkout -- <file>` | Yes, local only |
| Merged pull request was wrong | `git revert -m 1 <merge-commit>` | Yes |
| Squash-merged commit was wrong | `git revert <commit>` | Yes |
| Local branch needs to match remote | `git reset --hard origin/<branch>` | Local only |
| Recover something you think you destroyed | `git reflog` | Yes |

`git reflog` holds roughly 90 days of every position HEAD has been in. Almost nothing done
locally is actually unrecoverable, so check there before panicking.

**Check.** `git log --oneline -5` after the undo, and CI green.

---

## A10. Merge a pull request, and confirm it landed

**When.** A pull request is approved and CI is green.

**Why this way.** The merge is not the end of the job. A squash merge rewrites your commits
into 1 new commit on `main`, so your local branch and your local `main` are both instantly
wrong. Confirming by content rather than by the word "merged" is what stops the next
mistake, which is usually reopening work that already landed.

**Steps**

```bash
gh pr merge <n> --squash
git checkout main && git fetch && git pull --ff-only
git log --oneline -3
git diff --stat origin/main <your-branch>     # expect empty
```

Branches are kept after merge in this project, not deleted, so there is no cleanup step.

**Check.** The empty diff above is the proof. `gh pr view <n> --json state,mergedAt` is the
record. If `gh pr merge` reports "already merged", someone merged it in the browser; that
is not an error, verify by content and move on.

**Worked example, 2026-09-17.** PR #55 reported "already merged" when the merge command
ran. `git log origin/main` showed it as `f250caa`, so it had landed and nothing was lost.

---

# Part B. Project-specific procedures

---

## B1. Create an Alembic migration

**When.** Any change to a file under `app/models/`.

**Why this way.** 2 migrations generated in parallel produce 2 heads, and Alembic then
refuses to upgrade until someone merges them by hand. CI enforces a single head so this is
caught before merge, but the cheap fix is to announce it first. Autogenerate is a draft,
not an answer: it reliably misses index changes, constraint changes, server defaults and
every type change that needs data migrated.

**Steps**

1. **Announce it.** Say in the team channel that you are creating a migration. 1 person at
   a time, per development rule 13.
2. Change the model.
3. Generate:

```bash
docker compose exec api alembic revision --autogenerate -m "add work_item_link"
```

4. **Read the generated file line by line.** Add the indexes and constraints it missed.
5. Test it in both directions:

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1
docker compose exec api alembic upgrade head
```

**Check**

```bash
docker compose exec api alembic heads      # exactly 1 head
docker compose exec api alembic current    # matches that head
```

---

## B2. Add a configuration value

**When.** You are about to type a number or a URL into application code.

**Why this way.** A value typed inline gets copied, and 2 copies drift. Config is the
single place it is defined, and `.env.example` is how the other developer discovers it
exists. A new variable that is not in `.env.example` breaks the other machine silently.

**Steps**

1. Add the field to `app/config.py`, typed, with a default if it is safe to have one.
2. Add the name to `.env.example`, with no real value.
3. Add your own value to `.env`, which git ignores.
4. Tell the other developer, or their next run fails on a missing variable.

**Check.** Grep for the literal you replaced. It should appear once, in `app/config.py`.

**Never.** A secret with a default. A secret must be absent and fail loudly, rather than
silently running with a placeholder.

---

## B3. Change a contract after the freeze

**When.** A file under `app/schemas/` needs to change after P1-002 froze it.

**Why this way.** The schemas are the boundary between the 2 developers. A quiet change
breaks the other lane silently, often in a way that only shows up in a demo. The freeze is
not a ban on change, it is a requirement that change is visible.

**Steps**

1. Raise it in `WORKLOG.md` under Decisions Needed, saying what breaks if it does not
   change.
2. Get the other developer's agreement before writing code.
3. Change it in its own pull request, touching nothing else.
4. Both names on the review.
5. If it changes the architecture rather than just a field, add a DEC record. See B6.

**Check.** The pull request touches only `app/schemas/` and the callers it forces to
change.

---

## B4. Add a read tool

**When.** The agent needs data it cannot currently get.

**Why this way.** A tool that reaches out to the network breaks the write path / read path
boundary, which is the rule the whole design rests on. It makes answers slow, unreproducible
and dependent on a source being up during a demo. The import test is what stops this
happening by accident 3 weeks from now.

**Steps**

1. Define the input and output models in `app/schemas/tools.py` first.
2. Write the tool in `app/tools/`, 1 file per tool, reading PostgreSQL only.
3. Return the Pydantic model. Never a raw row, never a dict.
4. Return a typed failure on failure. Never an empty list, which means something different.
5. Write the unit test against seeded data.
6. Document it in `AGENT_TOOLS.md` with its full contract.

**Check**

```bash
grep -rn "httpx\|requests\|_client" app/tools/     # must return nothing
docker compose exec api pytest tests/tools -v
```

---

## B5. Capture and redact a fixture

**When.** Recording a real API payload for tests, seeding or evaluation.

**Why this way.** Recorded payloads carry the nulls, missing keys and odd shapes that real
APIs return and that nobody predicts when hand-writing a fixture. A hand-written fixture
only proves your code agrees with itself. The redaction step is not optional: a fixture is
committed, and committed means permanent.

**Steps**

1. Fetch with the real read-only token, save the raw response.
2. Remove every real email address, real display name where it is not needed, and any
   token or header.
3. Keep the structure exactly: same keys, same nulls, same nesting.
4. Save under `seed/fixtures/<source>/` with a name describing the scenario, for example
   `pr_with_changes_requested.json`.
5. Note in `WORKLOG.md` which scenario it covers.

**Check**

```bash
grep -rniE "@[a-z0-9.-]+\.(com|org|net)|ghp_|gho_|Bearer " seed/fixtures/
```

That must return nothing before you commit.

---

## B6. Add a decision record

**When.** A choice constrains future work, or reverses an earlier constraint.

**Why this way.** In 2 months nobody remembers why the person table is `app_user` or why
there is exactly 1 LLM call. Without the record, someone reverses it reasonably and breaks
something that is not visibly connected. The record is what makes a constraint survive its
author's memory.

**Steps.** Append to `docs/DECISIONS.md` using the existing DEC format: context, decision,
consequences, status. Number it next in sequence. If it amends an earlier decision, mark
the earlier one as amended by the new one rather than editing it. Ratify it with the other
developer before relying on it.

**Check.** The new DEC is referenced from the code or doc it constrains, the way DEC-018
is referenced from `AGENT_ARCHITECTURE.md`.

---

## B7. If a secret reaches a commit

**When.** A token, password or key is in a commit, pushed or not.

**Why this way.** Order matters and most people get it backwards. Rewriting history first
feels like the fix, but the secret was already readable and may already be copied, and
GitHub keeps unreferenced commits reachable by hash. **Rotate first.** A rotated token in
public history is a non-event. An unrotated token scrubbed from history is still live.

**Steps**

1. **Revoke and reissue the token at the provider.** Immediately, before anything else.
2. Update your local `.env` with the new value.
3. Remove the value from the code and commit that.
4. Only then consider history: if it was pushed, tell the other developer, and treat the
   old value as public permanently.
5. Note it in `WORKLOG.md` under Problems. This is not a thing to hide from your teammate.

**Check.** The old token fails against the API. Run the secret scanner locally before every
push so this stays theoretical.

---

## B8. Run the checks before you push

**When.** Always. CI is a safety net, not a test runner you outsource to.

**Why this way.** A CI round trip is minutes. The same checks locally are seconds. Pushing
to see if it passes burns your reviewer's attention on failures you could have seen.

**Steps**

```bash
ruff check .
black --check .
docker compose exec api pytest
docker compose exec api alembic heads          # if the branch touches models
```

**Check.** All 4 clean, then push.

---

## B9. Configure branch protection

**When.** Setting up the repository, or when the review rules change.

**Why this way.** Protection is not 1 switch, and the defaults are weaker than they look.
`main` can be "protected" while still allowing a merge with 0 approvals, which is what this
repository had until 2026-09-17. Each setting below earns its place.

| Setting | Value here | Why |
|---|---|---|
| Required approvals | 1 | `README.md` 13: Developer 2's pull requests need Developer 1's approval |
| Dismiss stale reviews | on | An approval of the old diff is not an approval of the new one |
| Conversation resolution | on | An unanswered review comment cannot be merged past silently |
| Linear history | on | The project squash merges, so history is linear anyway. This makes it enforced |
| Force push, deletion | blocked | `main` is the shared baseline |
| Required status checks | empty for now | A check cannot be required before it has run once. Added by P0-004 |
| `enforce_admins` | **off, deliberately** | See below |

**The `enforce_admins` trap.** Turning it on subjects admins to the approval rule too. In a
2 person team where the policy says Developer 1 may merge their own work, that makes
Developer 1's own pull requests unmergeable without the other person present. Leave it off
here. On a larger team, turn it on.

**Steps**

```bash
gh api repos/<owner>/<repo>/branches/main/protection --jq '{
  approvals: .required_pull_request_reviews.required_approving_review_count,
  checks: .required_status_checks.contexts,
  enforce_admins: .enforce_admins.enabled }'          # read the real state first

gh api -X PUT repos/<owner>/<repo>/branches/main/protection --input protection.json
```

`PUT` replaces the whole protection object, so send every field you want kept, not just the
1 you are changing.

**Check.** Read it back with the same `--jq` and compare against the table above.

---

## B10. Reconcile a status file with reality

**When.** Before trusting `TASKS.md` or `README.md` section 14 to decide what to work on,
and any time you notice a row that is wrong.

**Why this way.** A status table is a cache of reality, and caches go stale silently. The
cost is not tidiness: it is picking up work that is already done, or skipping work you
believe is finished. A stale local `main` produced a duplicate pull request in this
repository on 2026-09-17, and a stale status table is the same failure with a longer fuse.

**The rule: the system is the truth, the document is the cache.** Verify, then fix the
document, in that order.

**Steps**

```bash
gh issue list --state all --limit 60 --json number,title,state
gh pr list --state all --limit 20 --json number,title,state,mergedAt
gh api repos/<owner>/<repo>/branches/main/protection --jq '.'
gh label list --limit 30 | wc -l
gh api repos/<owner>/<repo>/milestones --jq 'length'
git log --oneline origin/main -10
```

Then update the table, and stamp it with the date it was reconciled so the next reader
knows how much to trust it.

**Check.** Every row of the table maps to something you just observed, not to something you
remember.

---

## B11. Add or change a project rule

**When.** A rule needs to exist, or an existing one is wrong.

**Why this way.** A rule written in only 1 place, and enforced in none, decays. This project
keeps rules in 3 layers, and the goal is always to push a rule down to the lowest layer it
can live in.

| Layer | Holds | Binds |
|---|---|---|
| `CLAUDE.md` | The rule in words, with its reason | Both developers, every AI session |
| `.claude/settings.json` | Harness configuration, committed | Both machines, before anyone decides anything |
| CI | The mechanical check | Every pull request, on a bad day |

Personal memory and "I will remember" are not layers. They bind 1 person on 1 machine.

**Steps**

1. Raise it in `WORKLOG.md` under `Decisions Needed`. Both developers agree.
2. Write it in `CLAUDE.md` with the reason it exists. 2 sentences maximum.
3. Ask whether configuration can enforce it. Attribution, for example, is set to empty in
   `.claude/settings.json` rather than left to anyone remembering it.
4. Ask whether CI can check it. If yes, add the row to the `CLAUDE.md` enforcement table
   and name the task that owes the check.
5. If it is architectural, add a DEC record per B6 and reference it from the rule.

**Check.** The enforcement table in `CLAUDE.md` has a row for the new rule, and that row is
honest about whether the check exists yet. A table that claims enforcement that does not
exist is worse than no table.

---

# Part C. Adding to this file

**When.** You did something mechanical that was not obvious, and you will do it again.

Add a recipe in the same pull request as the work, while you still remember the part that
was not obvious. That part is the reason the recipe is worth writing. A recipe that only
lists commands is a manual page and adds nothing. The value is in **Why this way**, so
write that section first.
