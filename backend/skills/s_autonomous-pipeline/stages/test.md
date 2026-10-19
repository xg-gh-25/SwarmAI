# TEST — execution card

Purpose: fresh runner evidence for the post-BUILD tree; fix what this change broke; record it.

**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage test`

`RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`.
Tests go ONLY through `RUN`: `pipeline_test_gate` denies direct pytest/vitest/tsc/eslint/vite build.
One call per mode, foreground, never split; no full suite (STEERING R9).

1. **L1+L2+L3 in one call:** `RUN --mode regression`. L1 = BUILD `ac_coverage` tests, L2 =
   tests importing a changed module; those naming a changed function always run, the
   rest are cut to the time budget (`skipped_for_budget`, flagged in REPORT §10). A
   must-run set over the deadline is refused with the larger-deadline command: run it
   as given. L3 =
   import smoke; `desktop/src` changes add `vitest related` + `tsc`. Run it fresh even
   if BUILD was green (post-refactor tree). Zero L2 files, non-SwarmAI stack, skips:
   `ref/test/layers.md`.
2. **Classify** by `failures[].classification`: `regression` / `new` → fix the CODE,
   never the test expectation (that changes the spec → back to PLAN); `pre-existing` →
   log in IMPROVEMENT.md "Known Issues", do not fix or debug it here; `flaky` → fix the
   flake (passes 9/10 = FAILS); `unknown` / timeout → read the attached stack (no manual
   ps/kill/faulthandler).
3. **Fix loop, max 20 fixes** (then checkpoint and report, whatever remains). Per fix:
   one atomic commit → `python backend/skills/s_autonomous-pipeline/scripts/wtf_gate.py --files-touched N --fix-count M`
   (score ≥ 5 → halt, L2 BLOCK) → `RUN --mode recheck`. Each recheck lays its results
   over `latest-regression.json`, which `stage-complete` reads: no final regression
   re-run. Formula + table: `ref/test/fixing.md`.
4. **Layer 4** only if EVALUATE `cross_boundary.value == true`: `ref/test/cross-boundary.md`
   (real-wiring test + `RUN --mode mutation --mutations <spec.json>`). Otherwise record
   `"cross_boundary_e2e": {"run": false, "reason": "not a cross-boundary change"}`.
5. **Cross-platform** changeset (`#[cfg]`, `#ifdef`, Go build tags, per-target code):
   `ref/test/cross-platform.md`.
6. **Exit criteria:** once `stage-complete --check` reports `exit_criteria_met: true`,
   stop verifying and exit.

**Gates**
- `[GATE·validator]` artifact needs `passed` + `layers.ac_driven`; layers and
  `regressions` come from runner evidence. <!-- R-test-001 -->
- `[GATE·artifact_cli]` cross_boundary=true → DELIVER exits 1 without a passing
  `cross_boundary_e2e.run` record (full rule: `ref/test/cross-boundary.md`).
- `[MUST]` WTF gate after EVERY fix, score shown even if 0. `wtf_gate.py` does NOT block
  publish: halt-at-≥5 is an agent-obeyed L2 checkpoint, not a validator publish gate. <!-- R-test-004 -->
- `[MUST]` BLOCKING: a single-platform compile is never a fully-green TEST result for a
  cross-platform changeset (`ref/test/cross-platform.md`).
- `pipeline_test_gate`: runner only. `SWARM_TEST_GATE_FORCE=1` is operator-set, never yours.

**Read only if:** layer detail / skip records / other stacks → `ref/test/layers.md`;
fixing, WTF formula, regression table → `ref/test/fixing.md`; cross-boundary →
`ref/test/cross-boundary.md`; cross-platform → `ref/test/cross-platform.md`.

**Exit:** write `/tmp/<R>/test.json` from the stage-start example:
`{"artifact": {"passed": true, "failed": 0, "fixed": K, "skipped": J, "tests_new": N, "tests_total": T, "cross_boundary_e2e": {...}}, "record": {"decisions": [...], "exit_evidence": {...}}}`.
`exit_evidence` keys, each ONE string: `wtf` (score, even 0), `fixes` (each atomic
commit), `unfixed` (each with diagnosis), `expectations` ("no test expectation modified"),
`classes` (regression / new / pre-existing kept apart), `platforms` (cross-platform only).
A list of strings is accepted and joined with `; `; an empty list is stored as `""`, so it
never satisfies a manual exit criterion.
Then `python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage test --data @/tmp/<R>/test.json`
(it validates before writing: no `--check` first).

> **⏭️ Continuation — advance is not the end of your turn.** `stage-complete` records and
> advances; read `next_card` and run that stage in this same turn. Legal hand-backs only:
> a stage-boundary **L2 Judgment** (Escalation Routing Protocol), a true **checkpoint**
> trigger (retries exhausted, mid-stage L2), a pending user question, or an abandon
> verdict. `next_action` is AUTHORITATIVE over this prose.
