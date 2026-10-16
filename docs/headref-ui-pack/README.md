# Headref UI pack

Everything needed to build the Headref UI canvas design in `keshan-dev/headref`.

## Quick start

1. Unzip at the repository root. Files land at their real paths (`app/web/...`,
   `docs/...`, `tests/web/...`). Nothing existing is overwritten except files with the
   same name, and none of these exist yet.
2. Read `docs/UI_IMPLEMENTATION.md`. Section 2 lists the design decisions to record in
   `WORKLOG.md` first; section 10 is the build order; section 13 is a ready prompt for
   Claude Code or any coding agent.
3. Register the template filters where `Jinja2Templates` is created:

   ```python
   from app.web import ui_format
   ui_format.register(templates.env)
   ```

4. Mount static files at `/static`.
5. Run `pytest tests/web` to check escaping and the writing rules.

## What is here

```text
app/web/static/style.css        tokens, components, responsive
app/web/static/app.js           drawer, tabs, refresh polling, local time
app/web/static/fonts/           IBM Plex, self hosted, OFL licence
app/web/static/favicon.svg
app/web/ui_format.py            Jinja filters and helpers
app/web/templates/              base, team, member, unmatched, login, error
app/web/templates/partials/     badges, icons, freshness, notices, claim,
                                evidence_list, question_panel
docs/UI_IMPLEMENTATION.md       the handoff spec
tests/web/test_ui_templates.py  12 guard tests
```

No paid dependency, no CDN, no npm, no build step. The fonts are free (SIL Open Font
License) and bundled so the demo runs offline.
