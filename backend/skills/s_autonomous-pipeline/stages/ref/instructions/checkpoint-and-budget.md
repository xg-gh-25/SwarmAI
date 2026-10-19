# INSTRUCTIONS ref — Checkpoint Protocol + Budget Tracking
Read when: you are about to checkpoint, a checkpoint was refused, you need `run-budget` /
`run-history` output explained, or you record `token_cost` by hand on the legacy run-update path.

## Checkpoint Protocol
<!-- R-instr-013 -->
### ⚠️ [GATE·cli] BLOCKING: True-Trigger Required Before ANY Checkpoint
**NEVER checkpoint on "feeling" or "intuition" about context usage.** Before every checkpoint run
`python backend/scripts/artifact_cli.py run-budget --project <PROJECT> --run-id <RUN_ID>`.
The `cmd_run_checkpoint` CLI command HARD-BLOCKS a checkpoint via `sys.exit(2)` when
`should_checkpoint=false` + no true-trigger + no `--force`, and force-blocks a
confabulation-denylist reason — code-enforced by the CLI, not a PreToolUse hook, not prose.
**The pipeline does not checkpoint to hand a run to a human** (autonomous since 2026-10-19:
`escalation.md` gives every L2 / retry / stuck / error case an autonomous disposition that
ends in a marked local commit). `run-checkpoint` is left for a user-requested pause and for a
session that is being torn down mid-stage (so a later session can resume).
`should_checkpoint` is ALWAYS false now (budget is never a trigger — STEERING #2), so it is NOT a
checkpoint condition; run-budget is a consumption report, not a checkpoint decision. With 1M
context (SESSION_BUDGET=800K) a full pipeline fits comfortably in ONE session.

### After each stage (follow sequentially)
```
Stage complete
  ├─ L2 decision pending? ── yes → most reversible option, recorded (`escalation.md`), CONTINUE
  ├─ retries >= max_retries / stuck / circuit breaker? ── yes → DELIVER with push_ready false
  ├─ unexpected error? ── retry once with the printed fix; still failing → as stuck
  └─ otherwise → CONTINUE (next stage)
CHECKPOINT only when the user asked to pause. Budget is never an entry.
```
**NOT valid reasons to checkpoint:** "BUILD is a big stage" (~60K tokens of 800K); "I've read a
lot of files" (~2K per file); "context might be getting full" (SDK autocompact handles physical
overflow — don't guess); **any budget / token-consumption reason** (STEERING #2 + XG directive;
a high % is informational, never a trigger).

### How to checkpoint
```bash
python backend/scripts/artifact_cli.py run-checkpoint --project <PROJECT> --run-id <RUN_ID> \
  --stage <next_stage> --reason "<why paused>"
```
Atomically: sets the run to "paused" with checkpoint metadata, publishes a checkpoint artifact to
`.artifacts/`, creates a high-priority Radar todo for visibility and resume. Then tell the user:
```
Pipeline PAUSED at <STAGE> (run_<id>)
Reason: <why>
  Completed: evaluate, think, plan
  Next: build
  Pending: <escalation summary>
  Budget: <consumed>/<total> tokens (<pct>% used)
  Resume: resolve the issue, then "resume pipeline for <PROJECT>"
  (A Radar todo has been created for tracking.)
```
Also emit the abandon telemetry (`cli-reference.md`, Abandon Protocol).

## Budget Tracking
### Before each stage (informational only)
`python backend/scripts/artifact_cli.py run-budget --project <PROJECT> --run-id <RUN_ID>` returns
`consumed` (sum of stage `token_cost`), `remaining` (session budget minus consumed), `next_stage`,
`next_stage_estimate` (calibrated), `should_checkpoint` (**ALWAYS false** — budget is NEVER a
checkpoint trigger, STEERING #2 + XG directive "budget 从来不应该是 block run 的重要因素"; a real
checkpoint comes only from a true-trigger reason) and `calibration_source` ("historical" or
"defaults").

### After each stage — `token_cost`
`stage-complete` fills `token_cost` with measured usage inside the stage's time window. It reads
the CLI transcript first: `stage-complete` (and `stage-start` on a first entry) records the
session's transcript id in `run.json` `transcripts[]`, and the transcript logs usage per model
response, so a stage that ends mid-turn is still measured. A session that only re-enters a stage
and never runs `stage-complete` is not recorded, so its usage in that window is missed. If there is no usable transcript, it falls back to the
per-turn `token_usage` rows. Measured wins either way; else a `record.token_cost` you pass is
kept as `token_cost_source: "estimate"`; else 0, `"unavailable"`. On the legacy `run-update --stage-json` path, estimate:
```
token_cost = base_stage_cost + (ddd_docs_read * 2000) + (artifacts_consumed * 3500)
           + (lines_of_code_changed * 50) + (test_count * 200) + (tool_calls * 1500)
```
Base stage costs (when no history): evaluate 6K (4-10K) · think 10K (5-20K) · plan 8K (5-15K) · build 40K
(15-80K, TDD cycle) · review 15K (8-25K) · test 25K (10-50K) · deliver 20K (8-50K, audit +
adversarial + convergence + report) · reflect 3K (2-5K). After 5+ completed runs `run-history`
gives calibrated per-stage averages (with a 20% buffer); history always overrides these bases.

### Historical calibration
`python backend/scripts/artifact_cli.py run-history --project <PROJECT>` → per-stage averages from
completed runs. `run-create` uses them automatically (20% buffer) when available.
