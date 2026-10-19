# GOAL_CYCLE — execution card
Purpose: loop bounded BUILD+TEST cycles until the Definition of Done (DoD) passes and a final adversarial fails to break it (goal profile; replaces BUILD+REVIEW+TEST).
**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage goal_cycle` (returns the evaluation artifact, design_doc, a valid example).
`RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>` (`continue: true` → again), ONLY way to test.
**Setup (once):** load `dod_criteria`, `max_cycles`, `progress_path`, `cycle_scope`, `review_cadence`; create or resume the progress file; record `start_commit` (`git rev-parse HEAD`); create ONE `GoalMetrics`; apply the DoD Quality Rules (≥1 negative test, a non-default path, behavior over existence). All in `ref/goal_cycle/setup.md`.
**Each cycle:**
1. **DoD check (exit-first):** run ALL criteria. Shell: `bash -c '<check_command>' 2>&1; echo "EXIT:$?"` (`EXIT:0` = pass); a test criterion: `RUN --mode tdd --files <test>`; rubric → `PASS` / `FAIL: <reason>`. Record each. All pass → **Final Quality Gate**.
2. **Stuck:** the last 3 cycles flipped no criterion → STOP.
3. **Read progress:** met criteria, gaps, last action, blockers; target the largest gap.
4. **Pick ONE step:** 1-3 files, one gap, within `cycle_scope` (default "one test file or one module fix"). Announce `Cycle N: targeting [criterion] via [action]`.
5. **Execute (TDD):** write the test, `RUN --mode tdd --files <tests>` RED → implement → GREEN.
6. **Test:** `RUN --mode verify`. `regression`/`new` failure → Regression Protocol; `timeout` → re-run as is (2 fixes, each + `RUN --mode recheck`, then revert this cycle's source): `exits.md`.
7. **Progress + mini-reflect:** append the metrics row and ONE line `**Cycle N:** <what worked> → <insight>`. No DDD write, no LLM.
8. **Metrics:** `gm.track_cycle(...)`; `progress_delta` = criteria newly met / total (0.0 if none flipped).
9. **Periodic REVIEW** when `cycle % review_cadence == 0`: REVIEW `git diff <last_review_commit>..HEAD`, log each finding in the Findings Ledger, re-judge every row on the total diff (`findings-ledger.md`).
10. **Revert check:** 2 consecutive reverted cycles → REVERT_LIMIT.
11. **Loop** to step 1.
Heavy checks (fuzz, replay, e2e probe): `RUN --mode probe --name <n> -- <argv>` printing `{"findings": [...]}`; a round with 0 new findings = converged, stop. Mutations: `RUN --mode mutation --mutations <spec.json>`, one batch.
**Final Quality Gate** (`final-gate.md`): 0) re-judge the ledger on the total diff; 1) Spawn a sub-agent (`run_in_background: false` — keep the stream visible) with `git diff <start_commit>..HEAD` + DoD + requirement + the cross-path prompt; merge its findings into the ledger; 2.5) `migration_class` declared → class-completeness check; 2.6) `cross_boundary.value == true` → real-seam E2E + mutation RED; 3) issues → ≤3 fix cycles, then stop and DELIVER not push-ready.
**Stop on:** DoD met + gate clean (SUCCESS) · 3 zero-progress cycles (STOP) · 2 reverts (REVERT_LIMIT) · `max_cycles` (UNMET). Non-SUCCESS → DELIVER, `push_ready: false`; never a checkpoint. Budget is never an exit. Each exit: `gm.track_goal_complete(status=...)`. Done = you tried to break it and failed; add no verification rounds after a clean gate.
**Gates**
- `[MUST]` DoD Quality Rules at setup (BLOCKING = agent discipline, no validator).
- `[MUST]` → `[GATE·validator]` APPLICABLE-unresolved ledger findings go verbatim into DELIVER `adversarial_review.findings[]`; `_blocked_findings` blocks there.
- `[GATE·validator]` artifact needs `dod_met` + `adversarial_review` dict with `findings` (bare `true` fails).
- `[MUST]` class-completeness run + appended by hand; `[GATE·artifact_cli]` cross_boundary needs a truthy `cross_boundary_e2e.run`.
- `[GATE·cli]` goal completion needs `adversarial_review` on the goal_cycle RECORD.
**Read only if** (in `stages/ref/goal_cycle/`): setup, DoD, metrics → `setup.md`; ledger, progress format → `findings-ledger.md`; DoD met → `final-gate.md`; red tests, non-SUCCESS exit → `exits.md`; >10 cycles or overnight → `scheduled.md`. Goal REFLECT: `stages/ref/reflect/goal-mode.md`.
**Exit (SUCCESS):** write `/tmp/<R>/goal_cycle.json` = `{"artifact": {"dod_met": true, "cycles_run": N, "adversarial_review": {"profile_tier": "full", "findings": [...]}, "cross_boundary_e2e": {...}}, "record": {"adversarial_review": true, "decisions": [...]}}`, then `python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage goal_cycle --data @/tmp/<R>/goal_cycle.json` (no `--check` first). Other exits: checkpoint, no stage-complete.
> **⏭️ Continuation — advance is not the end of your turn.** Read `next_card` (deliver) and run it this turn. Legal hand-backs only: a user stop or a terminal verdict (abandon, DEFER/REJECT); never a question or a checkpoint (`ref/instructions/escalation.md`). `next_action` is AUTHORITATIVE.
