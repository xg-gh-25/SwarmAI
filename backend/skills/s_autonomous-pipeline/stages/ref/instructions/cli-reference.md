# INSTRUCTIONS ref — CLI reference, Observe / Abandon protocols, background execution
Read when: you need a low-level command (the legacy publish / run-update / advance path, sibling
skills, debugging), run telemetry by hand, abandon a run, or create/monitor a background pipeline.
The normal stage loop is only `stage-start` / `pipeline_test.py` / `stage-complete` (card).

## Artifact Operations Reference
All commands: `python backend/scripts/artifact_cli.py <command> [args]`
```bash
# Stage boundary (preferred)
stage-start    --project <P> --run-id <R> --stage <S>
stage-complete --project <P> --run-id <R> --stage <S> --data @/tmp/<R>/<S>.json [--check]
gate2-recheck  --project <P> --run-id <R> --findings @/tmp/<R>/findings.json   # DELIVER round 2: fix-only prompts (enforced)
gate0-prompt   --project <P> --run-id <R> --data @/tmp/<R>/gate0.json   # EVALUATE M3: skeptic prompt + `spawn` text
schema   --stage <stage>                          # expected shape + ready template (single-line JSON)
# Artifacts (legacy path)
discover --project <P> --types <types> --full     # find upstream artifacts
publish  --project <P> --run-id <RUN_ID> --type <T> --producer s_autonomous-pipeline --summary "<S>" --stage <stg> --data '<json>' [--quiet]
state    --project <P>                            # current pipeline state
advance  --project <P> --state <stage> --run-id <RUN>   # advance the state machine
# Pipeline runs
run-create  --project <P> --requirement "<text>" [--profile <profile>]
run-update  --project <P> --run-id <R> [--stage-json '<json>'] [--status <S>] [--profile <P>]
            [--outputs-surfaced]   # with --status completed, only when the surface_run_outputs tool is unavailable
run-get     --project <P> [--run-id <R>]
run-budget  --project <P> --run-id <R>            # informational consumed/pct (NOT a checkpoint gate)
run-checkpoint --project <P> --run-id <R> --stage <stg> --reason "<why>"
run-resume  --project <P> --run-id <R>
run-status  [--active-only]                       # cross-project dashboard
run-report  --project <P> --run-id <R> [--force]  # generate REPORT.md
run-summary --project <P> --run-id <R>            # re-render the delivery summary
run-observe --project <P> --run-id <R> --event <E> [args]  # telemetry
```
**Legacy publish rules:**
- **ALWAYS pass `--run-id <RUN_ID>` on a `--stage` publish.** Without it the auto-record target is
  "newest active run project-wide", which with 2+ concurrent pipelines in a project would record
  into a SIBLING session's run; a `--run-id`-less publish with 2+ active runs FAILS CLOSED (stderr
  + exit 3).
- `publish --stage` AUTO-RECORDS the stage (status="recorded" + artifact_id), but you STILL MUST
  `run-update` to FINALIZE it: status="completed" + stage_doc_consumed=true (+ token_cost /
  decisions / consumed_artifacts). A stage left at "recorded" BLOCKS completion. The deliver
  stage-json MUST include the `artifact_id` from the publish output.
- Pass `--quiet`: success prints ONLY `{"artifact_id": "..."}` to stdout; a validation failure
  writes `{"validation_failed":true,"errors":[...]}` to STDERR, stdout EMPTY, exit 1. ONE
  EXCEPTION — the EVALUATE publish on a desktop/direct run goes WITHOUT `--quiet` so the result
  carries the `_display_block` you relay into ① (still one JSON object; headless keeps `--quiet`).
- Never pipe stdout straight into `json.load` (on failure stdin is EMPTY → opaque
  `JSONDecodeError`). Guard on the exit code and surface stderr:
  `OUT=$(publish ... --quiet 2>/tmp/pub.err) || { echo "PUBLISH FAILED:"; cat /tmp/pub.err; exit 1; }`
  then parse `artifact_id` from `$OUT`. On failure fix the payload from `schema --stage <s>` and
  re-publish.
- DELIVER payload skeleton (full/bugfix depth fields): `{"title":"...","quality":{"tests_pass":true,
  "regressions":0,"smoke_pass":true},"adversarial_review":{"spawned":true,"profile_tier":"full|lite|skipped",
  "evidence":"Agent tool: <how spawned>","findings_total":N,"findings_fixed":N,"findings_remaining":0,
  "findings":[{"severity":"LOW","resolved":true,"finding":"file:line — what. Fixed: how."}]},
  "completion_audit":{"all_green":true,"requirements_met":N,"requirements_total":N},
  "ac_verification":{...},"meta_review":{...},"convergence":{"iterations":1,"final_status":"push-ready"}}`
  (`profile_tier` takes one literal value).

## Observe Protocol (Meta-Intelligence L1)
Stage-boundary telemetry for cross-run learning, persisted to METRICS.json in the run directory
(<100ms each). `stage-start` / `stage-complete` record stage start/end times automatically; emit
the rest by hand (`run-observe --project <PROJECT> --run-id <RUN_ID> --event <E> ...`):
- stage entry / exit (legacy path only): `--event stage_start --stage <s>` / `--event stage_end --stage <s> [--retries N]`
- profile selection (EVALUATE): `--event profile_selected --scope <scope> --indicators '["indicator1","indicator2"]' --files-estimated <N>`
- THINK completion: `--event think_depth --alternatives <N> --probes <N> --resolved <N> --escalated <N>`
- DELIVER adversarial results: `--event adversarial_patterns --categories '{"correctness":2,"security":1}' --rp-violations '["RP3","RP12"]' --fixed 3 --dismissed 0`
  and `--event review_gap --review-count 2 --adversarial-count 5 --overlap 1`
**Non-blocking:** an observe call that fails (file locked, permission error) → log and continue.
Telemetry must never block pipeline execution.

## Abandon Protocol (Meta-Intelligence L4)
When a run is abandoned (checkpoint, user stop, session crash), capture partial learnings before
exit:
`run-observe --project <PROJECT> --run-id <RUN_ID> --event abandon --reason "<category>" --stage "<last_stage>" --tokens-consumed <N> --partial "<what was accomplished>"`
Reason categories: `user_stopped` (user said stop / closed the session) · `budget` (RARE: physical
context limit SDK autocompact could not reclaim — NOT a token-count stop; that budget-block was
removed, STEERING #2) · `session_crash` (ended abnormally, no explicit stop) · `blocker` (L2 BLOCK
with no resolution available) · `superseded` (same requirement started in a new run) ·
`scope_explosion` (BUILD found 3x more work than estimated).
**Recovery artifact:** if any BUILD work was done, summarize it in `--partial` ("3 files created, 2
tests passing, blocked on X"), so a later `run-create` with a similar requirement lets EVALUATE see
the prior attempt, THINK skip explored alternatives, and PLAN reuse file discovery (if the files are
unchanged). Stale runs (>2h) are auto-marked abandoned with reason `session_crash` by
`cleanup-orphans`.

## Background Execution (v3)
Pipelines can run as background jobs via the Swarm Job System, decoupled from chat sessions:
```bash
# Recurring: every weekday at 1am
python -m jobs.job_manager pipeline --project SwarmAI --requirement "Run QA on recent changes" \
  --profile bugfix --schedule "0 1 * * 1-5"
# One-shot (a specific feature)
python -m jobs.job_manager pipeline --project ClientApp --requirement "Add payment retry logic" \
  --profile full --budget 3.00 --one-shot
# General form
python -m jobs.job_manager pipeline --project <PROJECT> --requirement "<what to build>" \
  [--schedule "0 9 * * 1-5"] [--profile full] [--budget 2.00] [--one-shot]
```
Monitoring: `python backend/scripts/artifact_cli.py run-status --active-only` (all active
pipelines across projects) or `run-status` (active + recent completed).
**Resuming after escalation:** a background run that checkpoints (L2 BLOCK / retry / stuck — never
budget) creates a Radar todo. After the user resolves the issue, mark it resumable with
`python backend/scripts/artifact_cli.py run-resume --project <PROJECT> --run-id <RUN_ID>`, then
either drag the Radar todo into chat, say "resume pipeline for <PROJECT>", or wait for the next
scheduler run to pick it up.
