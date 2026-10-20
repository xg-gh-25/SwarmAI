# BUILD — execution card

Implement the PLAN test-first (vertical RED→GREEN), break its design yourself, prove every AC wired and covered.

**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage build` (ACs, change spec, exit criteria, `files_touched`, valid examples).

`RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`; `continue: true` → same command again.
Tests go ONLY through `RUN` (`pipeline_test_gate` denies direct pytest/vitest/tsc/eslint/`npm run build*`). Never the full suite, never `| tail`.

1. **Gate 1 Skeptic+SSA** (full, bugfix, goal first cycle; skip trivial/research): stage-start returns `gate1_prompt_path` (6 inputs filled), or `gate1_verdict` if this plan was judged (no respawn). Spawn ONE sub-agent, prompt = `gate1_spawn` exactly (neither key → `spawn_prompt.py --gate gate1-skeptic`, exit 0 only), `run_in_background: false` (hides the stream). BLOCK → one replan for ALL its checks + rerun ≤2×, then ask the user (`ref/build/gate1.md`). Spawn rejected → retry once, then `gate1_unverified_reason` + go on; never self-review.
2. **Before code:** read TECH.md `## Dev Commands` + `## Stack` (never assume SwarmAI's); meet `implementation_constraints` (`ref/plan/consumers.md`); API check, mechanism declaration, pre-change greps.
3. **Vertical TDD**, Change Spec order: tracer bullet (most important AC) first, then one test → one impl. Each cycle `RUN --mode tdd --files <≤5 test files>` (RED, then GREEN). `git add` your files by name; never `git commit` (DELIVER's `run-commit` is the sole commit). Code before its test → delete it, restart at RED. Fix code, not tests. A failure-path test per success-path test. Same AC fails twice → micro-replan (1 per AC, then mark it unmet, go on).
4. **After each GREEN:** path symmetry; real-data replay if it reads stored data.
5. **Break it yourself** `[GATE·validator]`: per plan `I<n>` / `F<n>` a test trying to violate it (kill, concurrency, retry, stale state) → `break_tests: [{targets, test, attack}]`. Experts only confirm.
6. **No full-scope run:** verify / regression / recheck run once, at DELIVER after the last change (refused here); BUILD evidence = tdd.
7. **Wiring:** callers, interface seams, proximity scan (±50 lines), framework patterns from source.
8. **SMOKE** every new branch in-process (import smoke for handlers/subprocess; frontend tabs/geometry/entries), **Resource Lifecycle** (release on success + failure), user-path trace per AC, wire PROBE for new frontend endpoints.
9. **Record written files:** `python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> --files-touched '["<abs path>", ...]'` (written only, absolute, dedup-append).
10. **AC Coverage Matrix:** every PLAN AC → `impl` `file::symbol`, `test` `file::test`, `verified: true`. `exit_criteria_met: true` → stop verifying.

**Gates**
- `[GATE·validator]` Check 8f BLOCKS a missing/incomplete `ac_coverage`; `files_changed`, `tdd.green_pass`, `tdd.smoke_tests` are schema-required.
- `[GATE·validator]` `break_tests` must target every plan `I<n>` / `F<n>` (step 5).
- `[MUST]` Gate 1, slice ban, API check, mechanism, replay, seams, import smoke, pre-greps: discipline.
- `[GATE·hook]` `active_run_commit_gate` denies `git commit` of run files; `pipeline_test_gate` runner only (its bypass is operator-set, never yours).

**Read only if**
- Profile runs Gate 1, or a verdict/rejected spawn/record → `stages/ref/build/gate1.md`
- First RED, a cycle keeps failing, mocks, recording files → `stages/ref/build/tdd.md`
- Calling another module / OS mechanism, shared or stateful code, persisted data → `stages/ref/build/pre-checks.md`
- Tests in BUILD, new public symbols/interfaces, changed bodies, framework patterns → `ref/build/verify.md`
- New branches, resources, frontend, endpoint → `stages/ref/build/smoke.md`
- Writing build.json, or stage-complete rejects it → `stages/ref/build/artifact.md`

**Exit:** write `/tmp/<R>/build.json` per the stage-start example: `{"artifact": {"tdd": {"green_pass": true, "smoke_tests": S, "tests_generated": N, "regressions": 0}, "ac_coverage": [...], "diff_summary": "..."}, "record": {"decisions": [...], "gate1_verdict": "PASS", ...}}` + `break_tests` (`files_changed`: every changed file; `gate1_*` + `replanned_acs` go in `record`; it replaces the build record). Then `python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage build --data @/tmp/<R>/build.json` (no `--check` first).

> **⏭️ Continuation contract — advance is not the end of your turn.** `stage-complete` records and advances (to review); read `next_card` and run that stage in this same turn. Legal hand-backs only: an L2 or unclear-requirement question (ask, or checkpoint), a user stop, or a terminal verdict (`ref/instructions/escalation.md`). `next_action` is AUTHORITATIVE over this prose.
