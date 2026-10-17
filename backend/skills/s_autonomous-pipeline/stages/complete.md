# COMPLETE Stage — Exec Card

Terminal output of every run: surface the run's changes, complete the run, relay the
code-rendered `_display_block` verbatim, add one ```next-steps block, STOP. Read this
card fresh at INSTRUCTIONS Step 6 (never from memory). Run-status gate details stay in
INSTRUCTIONS Step 6. Why this card exists: `stages/history/complete.md`.

---

## Step 0: Surface this run's changes to Canvas (MANDATORY — before the summary) `[GATE·artifact_cli]`
<!-- R-complete-001 -->

> **⛔ ORDER PREREQUISITE: `run-report` MUST run BEFORE `surface_run_outputs`.**
> `surface_run_outputs` appends the run's `REPORT.md` as the LAST OUTPUTS row
> (kind=knowledge) so the Canvas auto-SELECTS + renders the report — but ONLY if
> `REPORT.md` already exists on disk at surface time (`build_surface_events` is
> exists-guarded: no file → the report row is silently skipped and the finish batch
> auto-opens on a source file instead). So REPORT.md must be on disk before you
> surface. If it is absent, run `run-report` explicitly first.
<!-- R-complete-002 -->

**Who makes REPORT.md:** `stage-complete --stage deliver` auto-generates it when missing
(its output carries `report_generated: true`). It never overwrites an existing report,
so a hand-written one is left alone. `run-report --force` is only needed to refresh a
hand-written or stale report. Ordering detail: `stages/ref/complete/report-ordering.md`.

Do Step 0 BEFORE `run-update --status completed` (it is gated on `outputs_surfaced`, and
the summary it returns links the same files). Full Canvas behavior:
`stages/deliver.md` § "COMPLETE-stage Canvas review".

```bash
python backend/scripts/artifact_cli.py run-surface-changes
# → {"content":[...], "knowledge":[...], "source":[...], "process":[...]}
```

1. **content + knowledge** (DDD / design docs / MEMORY / KNOWLEDGE) already surfaced as
   they were written — no action; `run-surface-changes` just confirms them.
2. **source** (code) → call the `surface_run_outputs` tool ONCE with this run_id:
   ```
   ui_action-style tool:  surface_run_outputs   run_id=<run_id>
   ```
   The backend emits one OUTPUTS row per committed file (kind=source-final), each
   openable as that file's changes. Mid-run edits are not displayed. There is no
   LOCAL_PR.md (removed); the per-file rows are the deliverable.
3. **Record it:** `run-update --stage-json '{"stage":"deliver","status":"completed","outputs_surfaced":true,...}'`.

**GATED:** if this run committed run-scoped source (commits ∩ files_touched) and you
skip step 2/3, `run-update --status completed` BLOCKS. A knowledge/docs-only run (no
source commits) is exempt — skip steps 2-3, still do step 1.

---

## Output the delivery summary to chat (MANDATORY — never skip) `[MUST]`
<!-- R-complete-003 -->

The summary is RENDERED BY CODE from recorded data (intent in the user's words, final
requirements, real token cost, runtime, added/modified files as Canvas links, quality,
REPORT.md link). Design: `docs/design/pipeline-speed-systematic-fix.md` §4-E.

1. `python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> --status completed`
   returns it as `_display_block`. Lost it? Re-render:
   `python backend/scripts/artifact_cli.py run-summary --project <P> --run-id <R>`.
2. Output `_display_block` **VERBATIM** — no retyping, no reformatting, no code fence
   around it (a fenced block makes the file links unclickable).
3. Add 2-3 next steps the user would type next, in a ```next-steps block (rendered as
   clickable chips).
4. **STOP** — nothing after the ```next-steps block (no recap, no "shall I push?"). The
   stage-by-stage view (①–⑩), decisions and lessons live in REPORT.md, which the
   summary links to.
