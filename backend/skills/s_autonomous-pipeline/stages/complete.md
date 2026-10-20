# COMPLETE Stage — Exec Card

Terminal output of every run: surface the run's changes, complete the run, let the
chat's summary card speak (at most one line of your own), add one ```next-steps block, STOP. Read this
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

Do Step 0 BEFORE `run-update --status completed` (it is gated on surface evidence, and
the summary it returns links the same files). Full Canvas behavior:
`stages/ref/deliver/report-and-commit.md` § "COMPLETE-stage Canvas review — full behavior".

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
3. **Nothing to record by hand.** The tool writes the evidence the completion gate
   reads (`outputs_surfaced.json` beside run.json, listing the run's commits). In a
   session without the tool, add `--outputs-surfaced` to the completing
   `run-update --status completed` instead; do the same while the daemon has not been
   rebuilt since this marker was added (an older daemon writes no marker, and the gate's
   hint says so). Never re-record the deliver stage with
   `--stage-json` for this: that call REPLACES the deliver record and drops its
   token_cost and decisions.

**GATED:** if this run committed run-scoped source (commits ∩ files_touched) and step 2
did not run (and no `--outputs-surfaced`), `run-update --status completed` BLOCKS; a
commit made after the surface call needs the tool called again. A knowledge/docs-only
run (no source commits) is exempt — skip step 2, still do step 1.

---

## The run's result in the chat (MANDATORY) `[MUST]`
<!-- R-complete-003 -->

The desktop chat shows the result itself, RENDERED BY CODE from recorded data when
run.json reaches an end status (`backend/core/pipeline_stage_events.py`): the **summary
card** (verdict first: landed or not, ready to push or not; what was asked; before → now;
the run documents REQUIREMENTS / DESIGN_SPEC / REVIEW / FIXING_SPEC / REPORT) and the
**attention card** (what the user should check, one plain sentence per cause). The
documents are written by code (`backend/core/run_docs.py`; never hand-write one).

1. `python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> --status completed`.
2. Do **NOT** paste, retype or restate a summary: a second summary beside the card is
   the noise this card replaced. Write at most ONE line, and only what the cards do not
   say (e.g. a decision the user must make now).
3. A channel session (Slack etc.) has no cards: relay the code-rendered markdown block
   instead, verbatim, from
   `python backend/scripts/artifact_cli.py run-summary --project <P> --run-id <R>`.
4. Add the next steps the user would type next, in a ```next-steps block (none if there
   is no real one), then **STOP**. The stage-by-stage view, decisions and lessons live
   in REPORT.md, which the card links to.
