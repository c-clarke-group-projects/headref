# Headref UI implementation handoff

How to turn the Headref UI canvas (the bold version) into the real Jinja2 interface in
`keshan-dev/headref`. Read this together with `DESIGN.md`. `DESIGN.md` still owns the rules,
routes, contracts and copy. This file owns the look, the component markup and the file
layout.

**For the AI coding agent:** read `CLAUDE.md`, `DESIGN.md`, `docs/DECISIONS.md`, the 3 newest
`WORKLOG.md` entries, then this file. Where this file and the repository disagree, the
repository wins; flag the difference in `WORKLOG.md`.

---

## 1. What is in this pack

Every file sits at the path it should have in the repository.

| Path | What it is | Status |
|---|---|---|
| `app/web/static/style.css` | The whole stylesheet: tokens, every component, responsive, reduced motion | Ready |
| `app/web/static/app.js` | Drawer, tabs, refresh polling, local time, logout. Vanilla, 1 file | Ready |
| `app/web/static/fonts/` | IBM Plex Sans 400, 500, 600; Plex Sans Condensed 600, 700; Plex Mono 400. Latin subset woff2, OFL licence included | Ready |
| `app/web/static/favicon.svg` | Pointer glyph on black | Ready |
| `app/web/ui_format.py` | Jinja filters and helpers: time formats, labels, safe external links, sorting, signal rows | Ready, 2 TODOs |
| `app/web/templates/base.html` | Page shell: top nav, hero slot, banners slot, empty drawer | Ready |
| `app/web/templates/team.html` | S1 Team overview | Needs gap G6 |
| `app/web/templates/member.html` | S2 Member profile | Ready on frozen fields |
| `app/web/templates/unmatched.html` | S4 Unmatched accounts | Ready |
| `app/web/templates/login.html` | S0 Login stub | Ready |
| `app/web/templates/error.html` | S5 Error pages | Ready |
| `app/web/templates/partials/*.html` | badges, icons, freshness, notices, claim, evidence_list, question_panel | Ready |
| `tests/web/test_ui_templates.py` | Escaping, no `safe` filter, no dashes, no remote assets, helpers | 12 tests, passing |

All templates were render tested with the seed names and tickets from `DESIGN.md` section 12,
including the hostile excerpt, and screenshot checked at 1280 px and 390 px.

Not included, by design: `routes.py` (P5-001 owns it), the sync endpoints (P5-007), and any
change to `app/schemas/`. Section 9 lists exactly what the routes must pass to each template.

---

## 2. Decisions to record before merging

The canvas design differs from `DESIGN.md` section 9 in these places. Add 1 `Decisions
Needed` entry in `WORKLOG.md` covering all of them, agree it with both developers, then add a
DEC record per `docs/HOW_TO.md` B6.

| # | `DESIGN.md` says | Canvas design does | Why |
|---|---|---|---|
| D1 | Palette: paper `#FBFCFD`, ink `#1D2A36`, evidence blue `#1F5F8B` | Brand palette from the uploaded tokens: canvas `#E5E5E5`, black, white, mint `#D1FFCA`, voltage `#FFF100`. State colours from `DESIGN.md` kept | Team choice of visual identity |
| D2 | Radius 6 px, borders do the separating | Radius 32 px cards, 20 px tiles, pill buttons and badges | Same |
| D3 | Freshness strip is a thin bar under the header | Freshness lives in dark tiles inside a black hero block at the top of each page, with Refresh under them | Keeps "is this data current" next to the page subject |
| D4 | No big numbers | Team overview hero sentence shows the team total of attention items, for example "4 items need your attention". It counts work items only, never people | Answers "what needs me today" first |
| D5 | Fonts: Plex Sans and Plex Mono | Adds Plex Sans Condensed 600 and 700 for page titles and the hero. Same free family, self hosted | Stand in for the condensed display face in the uploaded tokens |
| D6 | Evidence markers after the claim, hover preview | Evidence chain: 1 tile per evidence item under the claim, each with marker, source, summary and observed time. Clicking a tile opens the drawer at that item | The proof is visible before any click |
| D7 | Page width 1120 px, left aligned | Page container 1280 px, centred; prose capped near 70 characters | Matches the canvas |
| D8 | Not in spec | Extra explanatory blocks: "How to read a statement" (overview), "How this answer was built" (profile), "Signals checked" grid (blockers and risks) | Teaches the classes and makes empty answers honest |
| D9 | Not in spec | Tab counts on Blockers and Risks | Only when the API returns real counts; see 9.3 |
| D10 | Uploaded tokens use all caps display and mono labels | Not adopted. Sentence case everywhere, mono only for branches, SHAs and evidence ids | `DESIGN.md` hard rules |
| D11 | Suisse Intl fonts in the uploaded tokens | Not adopted. They are commercial; the project needs free, self hosted fonts and no paid dependency | Org rule and DEC-012 |

The hard rules in `DESIGN.md` section 3 are unchanged and every file here follows them.

---

## 3. Design tokens

All tokens are CSS custom properties on `:root` in `style.css`. Use the token, never the raw
value, in any new CSS.

### 3.1 Colour

| Token | Light | Use |
|---|---|---|
| `--canvas` | `#e5e5e5` | Page background |
| `--card` | `#ffffff` | Cards, drawer body, banners |
| `--mist` | `#f3f3f3` | Summary block, inner tiles, quiet buttons |
| `--ink` | `#000000` | Text, hero, primary button, High confidence |
| `--graphite` | `#2f2f2f` | Tiles and pills on the dark hero |
| `--slate` | `#444444` | Secondary text and timestamps (9.7 to 1 on white) |
| `--ash` | `#c6c6c6` | Borders, outlines, muted text on dark |
| `--smoke` | `#979797` | Decoration only. Fails AA as text on white |
| `--mint` | `#d1ffca` | Evidence markers, Refresh, highlight, link hover |
| `--voltage` | `#fff100` | Needs attention badge, unmatched count and card |
| `--ok` | `#2e6b45` | On track, "Checked, not found" |
| `--warn` / `--warn-tint` | `#8a5300` / `#fbf0dc` | Stale text / stale evidence background, fallback notice |
| `--block` / `--block-tint` | `#b3261e` / `#fbe6e4` | Blocked badge / unavailable banner, blocked status |
| `--unknown` | `#66727e` | Dashed borders for unknown |
| `--ok-on-dark`, `--warn-on-dark`, `--block-on-dark` | `#7cc495`, `#e3b25c`, `#f08a82` | Fresh, stale, unavailable text inside the black hero |

Colour roles, 1 job each: mint means evidence or the main action, yellow means attention,
amber means stale, red means blocked or unavailable, black is structure. Never add a new
meaning to an existing colour.

Dark theme values are in `style.css` under `prefers-color-scheme: dark`. The canvas only
designed light mode, so **check every text and badge pair in dark mode for AA before merging**
(`DESIGN.md` 9.2).

### 3.2 Type

| Token | Size | Face and weight | Where |
|---|---|---|---|
| `--fs-display-xl` | clamp 64 to 128 px | Condensed 700, line height 0.82 | Member name in hero |
| `--fs-display` | clamp 52 to 96 px | Condensed 700, line height 0.9 | Overview hero sentence |
| `--fs-h1` | clamp 40 to 60 px | Condensed 700 | Panel heading, page titles |
| `--fs-h2` | clamp 32 to 44 px | Condensed 700 | "Assigned work", "Signals checked" |
| `--fs-claim` | clamp 24 to 36 px | Sans 500 | Main claim text |
| `--fs-h3` | 24 px | Sans 500 | Summary text, conflict title |
| `--fs-body` | 16 px | Sans 400 | Body |
| `--fs-small`, `--fs-caption`, `--fs-micro` | 14, 13, 12 px | Sans 400 or 500 | Meta, labels, badges |

Letter spacing on condensed is `-0.03em`. Sentence case everywhere. Ticket keys and numbers use
the `.num` class (tabular figures).

### 3.3 Spacing and radius

Spacing: `--s-1` 4 to `--s-9` 56 on a 4 px grid (4, 8, 12, 16, 24, 32, 40, 48, 56).

Radius: `--r-xl` 32 (hero, cards), `--r-lg` 24 (summary, conflict sides), `--r-md` 20 (tiles),
`--r-sm` 16 (list items), `--r-xs` 12 (excerpt), `--r-pill` 64 (badges, buttons, tabs).

---

## 4. Component inventory

Macros are imported with `{% import "partials/badges.html" as b %}` and
`{% import "partials/icons.html" as i %}`.

| Component | Where | Classes | Data |
|---|---|---|---|
| Member state badge | `b.member_state(state)` | `.badge--blocked`, `--needs-attention`, `--on-track`, `--unknown` | `state` |
| Claim class badge | `b.claim_class(classification)` | `.badge--outline` plus `.marker--fact`, `--inference`, `--unknown` | `Insight.classification` |
| Confidence badge | `b.confidence(level)` | `.badge--conf-high`, `-medium`, `-low`, `-unknown`; tooltip from `confidence_rules` | `Insight.confidence` |
| Work status badge | `b.work_status(status, raw_status)` | `.badge--status-blocked`, `--status-progress`, `--outline` | `WorkItemOut.status`, `raw_status` |
| Attention tag | `b.attention_tag(kind)` | `.badge--blocker` or outline | `kind` (see G6) |
| Evidence marker | `b.ev_marker(id)` | `.ev-marker` | `EvidenceItem.id` |
| Timestamp | `b.ts(dt)` | `time[data-local]` | any datetime |
| Source tiles and Refresh | `partials/freshness.html` | `.sources`, `.source-tile`, `.source-tile__state--*` | `source_health`, `team_id`, `now` |
| Banners | `n.banners(source_health, unmatched_count)` | `.banner`, `.banner--unavailable`, `.banner--failed` | same |
| Claim card | `partials/claim.html` | `.claim`, `.evidence-chain`, `.ev-tile`, or `.unknown-claim` | `insight`, `claim_id`, `label`, `plain_text` |
| Evidence list | `partials/evidence_list.html` | `.ev-list`, `.ev-item`, `.ev-item--stale` | `evidence`, `claim_id` |
| Answer panel | `partials/question_panel.html` | `.layout-main` and all panel blocks | see 9.2 |
| Drawer shell | `base.html` | `.drawer`, `.drawer__head`, `.drawer__body` | filled by `app.js` |
| Icons | `i.icon(name, size)` | inline SVG in `currentColor` | names: clock, warning, refresh, external, close, arrow-right, arrow-left, check, jira, github, unmatched |

---

## 5. Screens

Every page is: top nav, then a black hero, then banners, then content. The hero always holds
the page subject on the left and source freshness plus Refresh on the right.

### S0 Login (`login.html`)

Split screen. Left: black panel with wordmark, tagline in condensed 88 px, 3 promise pills.
Right: white card with the yellow "Demo sign in" badge, "Sign in", the stub explanation, the
Person select and a black Sign in button. The form posts to a `/login` page route so it works
without JS. On 401 render with `error="That user does not exist or is inactive."`.

Open question from `DESIGN.md` 15.3 still stands: listing users on an unauthenticated page.

### S1 Team overview (`team.html`)

```text
[pill nav: Headref | Platform team | Unmatched accounts (1)]      [KE Signed in as Keshan | Log out]
+-- black hero ------------------------------------------------------------------------+
| . Platform team, 2 members                              Source freshness             |
| Before standup, [4 items] need your attention.          [GitHub   Fresh ]            |
| States come from Jira and GitHub records ...            [Jira     Stale ]            |
|                                                         [ Refresh (mint) ]           |
+--------------------------------------------------------------------------------------+
[banners: unavailable (red), refresh failed (red, hidden), unmatched (white pill)]
[yellow: 1 unmatched account, button]   [How to read a statement: Fact, Inference, Unknown, confidence]
Members                                   Member order is alphabetical. Headref does not rank ...
[member card]  [member card]      2 columns, alphabetical, no sort or filter controls
```

Member card: initials avatar, name (link), role, state badge top right. Then "N items need
attention", up to 3 attention lines (tag, claim text, arrow), each linking to
`?question=blockers` or `?question=risks`, then "and N more". Footer: "Uses stale Jira data"
when any cited evidence is stale, and "Open profile". Unknown state shows `state_reason`
instead of items and never looks like On track.

Hero sentence rules: total over 0 gives "Before standup, N items need your attention." with
the count in a mint highlight; 0 gives "No blockers or risks detected."; every member unknown
gives "States are unknown until the sources sync." Empty team shows
"This team has no active members. Members are added through seed/identity_map.yml."

### S2 Member profile (`member.html` plus `partials/question_panel.html`)

```text
+-- black hero ------------------------------------------------------------------------+
| < Back to team                                          Source freshness             |
| (KE)  Backend Engineer                                   [GitHub  Fresh ]            |
|       Keshan  (condensed 128 px)                         [Jira    Stale ]            |
| [GitHub keshan-dev (manual)] [Jira Keshan P. (manual)] [This is your profile]  [Refresh] |
+--------------------------------------------------------------------------------------+
[banners]
[ Current work | Blockers 1 | Risks 2 ]     pill tabs, real links
+-- main column -------------------------------------+  +-- side column (360 px) ----+
| Panel heading (condensed 60 px)                    |  | How this answer was built  |
|   Summary block (mist): label, model text          |  |   (black card, 3 steps)    |
| Claim card: label, stale note, claim 36 px,        |  | Recent activity: counts    |
|   class + confidence badges, conflict lines,       |  |   + mandatory context note |
|   Evidence chain: tiles ev_1 ev_2 ev_3,            |  |   + unlinked pull requests |
|   footer: "3 evidence items" + Show button         |  | Not determined (dashed)    |
| Conflicts card: Jira says / not equal / GitHub says|  +----------------------------+
| Assigned work table                                |
+----------------------------------------------------+
```

Section order follows `DESIGN.md` S2 exactly. Own profile shows match methods, the mint "This
is your profile" pill and the identity map note. There is no member state badge on this page
(team overview only, `DESIGN.md` 6.2).

Blockers and Risks tabs: each item is a claim card labelled Blocker or Risk. Empty list with
healthy sources: "No blockers detected in the last 14 days." or "No delivery risks
detected." When a required source is unavailable the API returns an Unknown claim; the card
renders hatched and dashed, shows the plain sentence ("Jira data is unavailable, so blockers
cannot be determined.") with the code reason underneath, and has no evidence button. Then the
Signals checked grid: solid tiles "Checked, not found", dashed tiles "Not checked, needs Jira".

Conflicts, frozen contract: `Insight.conflicts` are strings, so each renders as a title in
the conflict card. The side by side "Jira says / GitHub says" layout needs structured
conflicts (gap G9 below); the template already handles both shapes.

Assigned work: sorted blocked, in progress, in review, to do, done, then due date
(`sort_assigned`). Keys link to Jira. "Past due" in amber under the date when the item is not
done. When Jira is unavailable the table is replaced by "Assigned work comes from Jira and
cannot be shown while Jira is unavailable."

### S3 Evidence drawer (`base.html` shell, `app.js`)

Floating panel, 480 px wide, 16 px from the viewport edges, 32 px radius, drawer shadow, black
header with "Evidence" in condensed 48 px, the claim, its badges and a mint "3 items" count.
Body: vertical timeline, 1 node per item (black, amber when stale), item card with marker,
source, summary, excerpt block, Record, Observed, Retrieved, and "Open in Jira" or "Open in
GitHub". Clicking an evidence tile opens the drawer scrolled to that item with a 2 px outline.

Without JS each claim shows the same list in a `details` element. With JS `app.js` hides the
details, clones its list into the drawer, and makes no request.

### S4 Unmatched accounts (`unmatched.html`)

Hero with the condensed title, explanation, a yellow count card and the source tiles. Then a
table (Source, Handle, External ID, Seen, First seen, Last seen), newest first, "no handle
recorded" for a null handle. Then "How to fix a mapping" as 3 numbered step cards (a real
sequence, so numbers are correct). Empty: "Every account seen in Jira and GitHub is mapped to
a team member."

### S5 Errors (`error.html`)

Black hero with "Error N", heading, body and 1 mint action, copy from `DESIGN.md` S5. The 422
page links to the 3 tabs. 401 on a page route redirects to `/login?next=...` and never
renders this page.

---

## 6. State matrix, where each state shows

| Condition | Component | What renders |
|---|---|---|
| Source fresh | Source tile | Green dot, "Fresh", "Synced 4 min ago" |
| Source stale | Source tile, claim, tiles, drawer | Amber "Stale" with detail; "Uses stale Jira data" on claims; amber evidence tiles and drawer items |
| Source unavailable | Source tile, banner, answer | Red outline tile with last sync and error label; red banner; Unknown claim; conflicts and assigned work replaced by plain notes |
| Never synced | Source tile | "Never synced", "Jira has never synced." |
| LLM fallback (G1) | Summary block | Amber pill "AI reasoning is temporarily unavailable, showing recorded facts only." plus "Built by code from the findings below, without the local model." |
| Truncated (G4) | Recent activity | "Showing the most recent 100 commits." |
| `SCHEMA_INVALID` | Near the list | `n.schema_invalid_notice()` |
| Conflict | Claim card and Conflicts card | Warning line in the card; conflict card with "Headref does not choose. Check which one is out of date." |
| No evidence | Answer | Hatched unknown card with the `unknown.no_evidence` copy |
| Unmatched accounts | Nav count, banner, overview card | Yellow count; white banner; yellow card |
| Refresh running | Refresh button, status line | Spinner, "Refreshing", disabled; "Syncing GitHub and Jira" in the live region |
| Refresh failed or slow | Red banner (hidden until needed) | Typed error copy from `DESIGN.md` 6.4; page content stays |
| Tab swap loading | Panel | Skeleton, "Building the answer from recorded data. This can take up to 20 seconds.", then "Still working." after 20 s |

---

## 7. Interactions and motion

| Element | Trigger | Behaviour |
|---|---|---|
| Evidence tile or "Show N evidence items" | Click, Enter | Opens drawer; tile opens it at that item |
| Drawer | Esc, close button, backdrop click | Closes, focus returns to the trigger |
| Drawer | Tab | Focus trapped inside |
| Drawer | Open | Slides in 150 ms ease out; instant under reduced motion |
| Tabs | Click | With the fragment route: swap panel, `history.replaceState`, focus the panel heading. Without it or on failure: normal page load |
| Tabs | Left, Right, Home, End | Moves focus between tabs |
| Refresh | Click | POST `/api/teams/{id}/sync`, poll status every 2 s, stop after 5 min, reload on success; 403 shows "You can only refresh your own team." |
| `time[data-local]` | Page load | Visible text becomes local time, title keeps UTC |
| Hover | Links, tiles, attention items | Mint background. Nothing else moves |

Constants live at the top of `app.js`: `SYNC_POLL_INTERVAL_MS`, `SYNC_POLL_TIMEOUT_MS`,
`REQUEST_TIMEOUT_MS`, `REQUEST_MAX_RETRIES`, `ANSWER_SLOW_MS`. Every request has a timeout and
a retry limit, retries only on network errors and 5xx, and logs what was attempted, what came
back and what was skipped.

---

## 8. Responsive and accessibility

| Width | Changes |
|---|---|
| 1024 px and up | Hero in 2 columns; profile main plus 360 px side column; drawer floats right |
| 720 to 1023 px | Hero stacks (source tiles under the subject); side column moves below; overview grid is 1 column; Priority column folds into the title cell |
| Under 720 px | Page padding 12 px; source tiles 2 across; tabs scroll; evidence chain becomes a vertical list; conflict sides stack with the not equal sign between; tables become stacked rows; drawer is full screen; touch targets at least 44 px |

Accessibility is built in: skip link; visible 2 px focus ring (mint inside dark areas); every
badge has text; tabs follow the WAI ARIA pattern; the drawer is a modal dialog with focus trap;
Refresh status is in an `aria-live="polite"` region; external links have accessible names
"Open in GitHub (opens in a new tab)"; the not equal sign has `aria-label="does not match"`;
page titles follow "Keshan, current work, Headref".

---

## 9. What the routes must pass

Register the filters once:

```python
from fastapi.templating import Jinja2Templates
from app.web import ui_format

templates = Jinja2Templates(directory="app/web/templates")
ui_format.register(templates.env)   # autoescape stays on
```

Mount static files at `/static` (the templates use `url_for('static', path=...)`).

### 9.1 Every authenticated page (`base.html`)

| Name | Type | Source |
|---|---|---|
| `request` | Request | FastAPI |
| `actor` | ActorInfo | `/api/auth/me` dependency |
| `team_name` | str | team record |
| `unmatched_count` | int | `GetTeamMembersOutput.unmatched_count` |
| `nav_current` | str | "team", "member" or "unmatched" |
| `now` | aware datetime | `datetime.now(timezone.utc)`, pass it so relative times are testable |
| `team_id` | int | actor team |

### 9.2 Per page

| Template | Extra context |
|---|---|
| `team.html` | `overview` (TeamOverview, G6) |
| `member.html` and `partials/question_panel.html` | `member` (TeamMemberOut from T-001), `insight` (MemberInsight), `question`, `is_own`, optional `tab_counts`, optional `signals` from `ui_format.signal_rows(question, settings, insight.source_health)` for blockers and risks |
| `unmatched.html` | `rows` (unmatched entities, newest first), `source_health` |
| `login.html` | `users`, `next`, `error` |
| `error.html` | `code`, `kind` ("member", "page", "question"), `member_id` for 422 |

Build `signals` only when the answer list is empty or a required source is unavailable,
otherwise "Checked, not found" would be false. If the backend later reports which signal
produced each finding, pass `found_ids`.

### 9.3 Contract gaps the templates use

The templates render cleanly without these fields and show the extra blocks only when they
exist. Agree them in `WORKLOG.md` (step 0 of `DESIGN.md` section 13) before depending on them.

| Gap | Field | Used by |
|---|---|---|
| G1 | `MemberInsight.fallback_used` | Fallback notice in the summary |
| G3 | `MemberInsight.activity` | Recent activity card (hidden until present) |
| G4 | `MemberInsight.truncated` | Truncated notice |
| G5 | linked accounts | Hero pills; pass `member` from T-001 until then |
| G6 | `TeamOverview`, **plus** `kind: "blocker" or "risk"` on each attention item | Team overview; `kind` picks the tag and the tab link |
| G7 | `SyncStatus` | `app.js` refresh polling |
| G9 (new) | Structured conflicts: `entity_key`, `jira_state`, `github_state`, `github_entity`, `github_detail` | Side by side conflict layout; strings still render without it |
| G10 (new, optional) | Counts for tabs | `tab_counts`; omit rather than guess |

PROPOSED routes the JS uses: `GET /members/{id}/panel?question=` (fragment, set
`tabs.USE_FRAGMENT_ROUTE = false` in `app.js` if not agreed) and a `POST /logout` page route
for the no JS logout form (`app.js` uses `/api/auth/logout` when JS runs).

---

## 10. Build order

Same steps as `DESIGN.md` section 13; this is which pack files each PR takes. Keep each PR
under 400 changed lines; the fonts are binary and do not count toward the line budget, but
mention them in the PR.

| Step | Task | Take from this pack |
|---|---|---|
| 0 | Decisions | Section 2 of this file into `WORKLOG.md`; gaps in 9.3 |
| 1 | P5-001 | Route context from section 9; register `ui_format` |
| 2 | Shell | `style.css`, fonts, favicon, `base.html`, `partials/icons.html`, `partials/badges.html`, `partials/freshness.html`, `partials/notices.html`, `error.html`, `login.html`, `ui_format.py`, the tests |
| 3 | P5-002 | `member.html`, `partials/question_panel.html`, `partials/claim.html` |
| 4 | P5-003 | `partials/evidence_list.html`, drawer part of `app.js` |
| 5 | P5-004 | `team.html` |
| 6 | P5-006 | Time localizer in `app.js`; check every row of section 6 |
| 7 | P5-007 | Refresh part of `app.js` |
| 8 | P5-005 | `unmatched.html` |

If `style.css` alone passes 400 lines in step 2, split it: tokens, base and badges first,
screen sections with the screen PRs.

---

## 11. Open items

1. **Confidence tooltips.** Copy the 1 line rules from `docs/AI_BEHAVIOR.md` 5.4 into
   `ui_format.CONFIDENCE_RULES`. Only Medium is quoted in `DESIGN.md`; do not invent the rest.
2. **Signal sources and thresholds.** Confirm which source each BL and RK signal needs, and
   replace the placeholder config attribute names in `ui_format.BLOCKER_SIGNALS` and
   `RISK_SIGNALS` with the real ones from `app/config.py`.
3. **Mono entity keys.** `evidence_list.html` sets branch names and SHAs in mono when
   `entity_type` is `commit` or `work_item_link`. Confirm that is how branches arrive.
4. **Dark mode.** Tokens are in place but untested against the canvas; run the AA check.
5. **Not equal sign.** The Latin font subset has no `≠`, so it falls back to a system font.
   Fine visually; swap to an SVG if it ever looks off.
6. **Rename.** Templates already say Headref. The headref rename items in `DESIGN.md` 1a still
   need their own PR.

---

## 12. Definition of done

Copy of `DESIGN.md` section 14, with what this pack already covers ticked.

- [x] No `safe` filter on any template (test)
- [x] Hostile excerpt escaping test (test)
- [x] No en or em dash in `app/web` (test)
- [x] No remote asset URLs (test)
- [x] Works without JS except Refresh (details fallback, link tabs, form login)
- [x] Keyboard: drawer trap and return, tab arrows, visible focus
- [ ] Route test that every `{member_id}` route uses `MemberGuard` (P5-001)
- [ ] Rendering tests per state: populated, empty, stale, unavailable, fallback, truncated, conflict (extend `tests/web` with real fixtures)
- [ ] Both themes pass AA
- [ ] Checked at 375, 768 and 1280 px in the real app
- [ ] Screenshots on each PR, `WORKLOG.md` entry, no AI attribution in commits

---

## 13. Prompt for the AI coding agent

Paste this into Claude Code (or your agent) from the repository root after copying the pack in:

```text
Read CLAUDE.md, DESIGN.md, docs/DECISIONS.md, the 3 newest WORKLOG.md entries and
docs/UI_IMPLEMENTATION.md. The UI pack files are already in place under app/web and
tests/web.

Do step <N> of docs/UI_IMPLEMENTATION.md section 10 only. Rules:
- Use the provided templates, partials, style.css and app.js as the source of truth for
  markup and styling. Do not restyle; if something is missing, add it with the existing
  tokens and classes.
- Wire routes to pass exactly the context in section 9. Do not change app/schemas; if a
  field from section 9.3 is missing, leave the template block hidden and add a Decisions
  Needed entry in WORKLOG.md.
- Keep autoescape on. Never use the safe filter. No CDN, no npm, no new dependencies.
- No en or em dashes anywhere. Numerals, sentence case.
- Run pytest, including tests/web/test_ui_templates.py, before finishing.
- 1 PR, under 400 changed lines, branch feat/<issue>-<desc>, Conventional Commits,
  WORKLOG.md updated in the same PR.
```
