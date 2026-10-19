# REFLECT — execution card
Purpose: turn this run's lessons into durable learning (IMPROVEMENT.md, MEMORY.md, ADRs, checklists, DDD docs) and finalize REPORT.md. Pipeline-owned stage, no sibling skill.
**Enter**: `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage reflect` (returns the lessons DELIVER seeded, upstream artifacts and a valid record example).
**Goal profile:** first run the Full REFLECT in `stages/ref/reflect/goal-mode.md`, then continue here at step 4. Per-cycle mini-reflects belong to goal_cycle.md, not here.
## Methodology (Standard — all profiles except goal's per-cycle mini-reflect) `[MUST]` <!-- R-reflect-001 -->
> REFLECT has NO validator-required artifact fields (it is artifactless; `schema --stage reflect` →
> required=[]); the record's only required field is `lessons`. Every step below (lessons, ADR gate,
> cultivation, learn, report) is agent-discipline `[MUST]` — the cultivation/learn/report are real
> scripts the agent invokes, not publish-blocking gates. "Do NOT skip" here means honor the
> discipline. <!-- R-reflect-002 -->
1. **Reuse the seeded lessons — do NOT re-extract.** DELIVER's "Pipeline Report" step already wrote
   this run's lessons into the reflect record. Finalize them; add one ONLY if REFLECT surfaces
   genuinely new material. Never author a DIFFERENT set (two sources of REPORT §9 = drift).
   Format, `[type]` prefixes, quality bar: `ref/reflect/lessons.md`.
2. **IMPROVEMENT.md**: what worked, what failed, patterns discovered.
3. **MEMORY.md** only if the lesson is cross-project, and only through
   `backend/scripts/locked_write.py` (never a raw write).
4. **Checklist maintenance**: if a post-pipeline review (E2E, external, user feedback) found a bug
   the pipeline missed: fits an existing RP pattern → investigate why it was missed; no pattern →
   add a new RP pattern (trigger, what to verify, the real bug as example) to
   `backend/skills/s_autonomous-pipeline/REVIEW_PATTERNS.md`; a resource type missing from the
   lifecycle table → add the row to Resource Lifecycle (BUILD Step 4, `stages/build.md`).
5. **ADR gate**: for each judgment/taste decision, write a 1-paragraph ADR only when ALL three hold:
   hard to reverse, surprising without context, real trade-off. Any missing → skip (most decisions).
6. **Dead code checkpoint** (skip without `code_intel.db`): compare dead code before vs after;
   increased → IMPROVEMENT "What to Watch For", decreased → "What Worked".
7. **Learn** (calibration): `python backend/scripts/artifact_cli.py learn --project <P> --evaluation-id <eval_artifact_id> --outcome success --actual-effort "<T-shirt>" --lessons "lesson 1;lesson 2"`
8. **Record = Exit** (below): writes the final lessons to run.json.
9. **DDD cultivation** — do NOT skip, it closes the REFLECT → DDD loop:
   `python backend/scripts/artifact_cli.py run-cultivate --project <P> --run-id <R>` →
   `{"applied": N, "escalated": M, "rejected": K}`. It reads the recorded lessons, so run it after step 8.
10. **Regenerate REPORT.md** (final version; §9 inlines the lessons), after the record and BEFORE
   COMPLETE: `python backend/scripts/artifact_cli.py run-report --project <P> --run-id <R> --force`
Recipes for steps 2-6 and 9 (ADR format, locked_write command, tiered autonomy): `ref/reflect/writes.md`.
## Gates
- `[GATE·cli]` stage-complete rejects a reflect record with no `lessons`, or any lesson ≤20 chars.
- `[GATE·cli]` `run-update --status completed` BLOCKS when reflect has no entry, no lesson >20 chars, or REPORT.md is missing / <500 bytes.
- `[MUST]` prefix every lesson with its `[type]`: guideline pitfall decision principle correction process model.
## Read only if
- `stages/ref/reflect/goal-mode.md` — the profile is `goal`.
- `stages/ref/reflect/lessons.md` — refining/adding lessons, where each `[type]` lands, the quality bar, or "nothing went wrong".
- `stages/ref/reflect/writes.md` — any write in steps 2-6 or 9 (IMPROVEMENT / MEMORY / ADR / checklist / dead code / cultivation).
## Exit (step 8)
Write `/tmp/<R>/reflect.json` = `{"record": {"lessons": ["[pitfall] ...", "[decision] ..."], "decisions": []}}` (no artifact), then:
`python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage reflect --data @/tmp/<R>/reflect.json`
On `ok: false`, fix the field named in `errors[].path`. Exit records the stage; steps 9-10 still follow.
> **⏭️ Continuation contract — advance is not the end of your turn.** REFLECT is the last stage,
> so this is the LAST place the stall can happen, and the most expensive. After steps 9-10 go
> straight to **Step 6 COMPLETE** in `INSTRUCTIONS.md`: read `stages/complete.md`, run
> `run-update --status completed`, relay its `_display_block`. Legal stops instead: a
> question you cannot answer yourself, a user stop, or an abandon verdict.
