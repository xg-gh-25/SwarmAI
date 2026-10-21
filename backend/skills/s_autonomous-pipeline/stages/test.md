# TEST — execution card

Purpose: the checks the one full-scope run does not make (cross-boundary wiring, mutation, platforms); record them.

**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage test`

`RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`; output `continue: true` → run the same command again.
Tests go ONLY through `RUN`: a direct pytest/vitest/tsc/eslint/vite build runs but is no evidence.
One call per mode, foreground, never split; no full suite (STEERING R9).

1. **No full-scope run here.** verify / regression / recheck run ONCE per run, at DELIVER
   after the last change (`stages/deliver.md` 4b): landing reads only that report, so a
   regression here was never used. The runner refuses them in TEST. L1 (AC tests), L2
   (direct importers) and L3 (CI BVT + import smoke) all run in that one regression.
2. **A test you need now** (a cross-boundary or platform check below, or a fix): run only
   the tests it affects, `RUN --mode tdd --files <≤5 test files>`, or `RUN --mode prove`
   for break tests. Red → classify, one fixing spec, fix in one pass (`ref/test/fixing.md`).
3. **WTF gate** after each fix pass: `python backend/skills/s_autonomous-pipeline/scripts/wtf_gate.py --files-touched N --fix-count M`
   (score ≥ 5 → halt: stop fixing, finish not push-ready).
4. **Layer 4** only if EVALUATE `cross_boundary.value == true`: `ref/test/cross-boundary.md`
   (real-wiring test + `RUN --mode mutation --mutations <spec.json>`). Otherwise record
   `"cross_boundary_e2e": {"run": false, "reason": "not a cross-boundary change"}`.
5. **Cross-platform** changeset (`#[cfg]`, `#ifdef`, Go build tags, per-target code):
   `ref/test/cross-platform.md`.
6. **Exit criteria:** once `stage-complete --check` reports `exit_criteria_met: true`,
   stop verifying and exit.

**Gates**
- `[GATE·validator]` layers (`ac_driven` …) and `regressions` come from the DELIVER
  regression's runner evidence, judged by the push_ready bound. <!-- R-test-001 -->
- `[GATE·artifact_cli]` cross_boundary=true → DELIVER exits 1 without a passing
  `cross_boundary_e2e.run` record (full rule: `ref/test/cross-boundary.md`).
- `[MUST]` WTF gate after EVERY fix, score shown even if 0. `wtf_gate.py` does NOT block
  publish: halt-at-≥5 is an agent-obeyed stop (finish not push-ready), not a validator publish gate. <!-- R-test-004 -->
- `[MUST]` BLOCKING: a single-platform compile is never a fully-green TEST result for a
  cross-platform changeset (`ref/test/cross-platform.md`).
- Only runner results count as test evidence.

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
> an L2 or unclear-requirement question (ask, or checkpoint), a user stop, or a terminal verdict
> (`ref/instructions/escalation.md`). `next_action` is AUTHORITATIVE over this prose.
