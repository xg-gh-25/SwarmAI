# BUILD — execution card

Implement the PLAN change spec test-first (vertical RED→GREEN), prove every AC is wired and covered, record it.

**Enter:** `python backend/scripts/artifact_cli.py stage-start --project <P> --run-id <R> --stage build` (ACs, change spec, exit criteria, `files_touched`, valid examples).

`RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`.
Tests go ONLY through `RUN` (`pipeline_test_gate` denies direct pytest/vitest/tsc/eslint/`npm run build*`). Never the full suite, never `| tail`.

1. **Gate 1 Skeptic+SSA** (full, bugfix, goal first cycle; skip trivial/research/docs): stage-start returns `gate1_prompt_path` (6 inputs filled), or `gate1_verdict` when this plan was already judged (do not respawn). Spawn ONE sub-agent, prompt = `EXPERT-ID: gate1-skeptic` + `Adversarially review this plan: read <gate1_prompt_path> first; follow it.` (neither key → `spawn_prompt.py --gate gate1-skeptic`, exit 0 only), `run_in_background: false` (background hides the stream). BLOCK → user picks Fix Plan / Proceed Anyway. Spawn rejected → retry once, then checkpoint `gate_spawn_blocked`; never self-review.
2. **Before code:** read TECH.md `## Dev Commands` + `## Stack` (never assume SwarmAI's stack); apply EVALUATE `build_injection_recommendations`; API existence check, mechanism declaration, pre-change greps.
3. **Vertical TDD**, Change Spec order: tracer bullet (most important AC) first, then one test → one impl. Each cycle `RUN --mode tdd --files <≤5 test files>` (RED, then GREEN). `git add` your files by name; never `git commit` (`run-commit` at DELIVER is the sole commit). Code before its test → delete it, restart at RED. Fix code, not tests. A failure-path test per success-path test. Same AC fails twice → micro-replan (1 per AC, then L2 checkpoint).
4. **After each GREEN:** path symmetry; real-data replay when the change reads persisted data.
5. **VERIFY:** `RUN --mode verify`; fix code, `RUN --mode recheck`; max 2 re-runs, then complete with `tdd.regressions: N`.
6. **Wiring:** caller verification, interface seam check, proximity scan (±50 lines), source verification for framework patterns.
7. **SMOKE** every new branch in-process (import smoke for handlers/subprocess code; frontend tabs/geometry/entries), **Resource Lifecycle** (success + failure path release), user-path trace per AC, wire PROBE for new frontend-consumed endpoints.
8. **Record written files:** `python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> --files-touched '["<abs path>", ...]'` (written only, absolute preferred, dedup-append).
9. **AC Coverage Matrix:** every PLAN AC → `impl` `file::symbol`, `test` `file::test`, `verified: true`. Once `stage-complete --check` reports `exit_criteria_met: true`, stop verifying.

**Gates**
- `[GATE·validator]` Check 8f BLOCKS a missing/incomplete `ac_coverage`; `files_changed`, `tdd.green_pass`, `tdd.smoke_tests` are schema-required.
- `[MUST]` Gate 1, horizontal-slice ban, API check, mechanism declaration, real-data replay, VERIFY, interface seam, import smoke, pre-change greps: discipline, no validator.
- `[GATE·hook]` `active_run_commit_gate` denies `git commit` of run files; `pipeline_test_gate` runner only (`SWARM_TEST_GATE_FORCE=1` is operator-set, never yours).

**Read only if**
- Profile runs Gate 1, or a verdict/rejected spawn/record → `stages/ref/build/gate1.md`
- Before the first RED, a cycle keeps failing, choosing mocks, recording files → `stages/ref/build/tdd.md`
- Calling another module / OS mechanism, changing shared code, stateful code, persisted data → `stages/ref/build/pre-checks.md`
- VERIFY, new public symbols/interfaces, changed bodies, framework patterns → `stages/ref/build/verify.md`
- New branches, resources, frontend change, new endpoint → `stages/ref/build/smoke.md`
- Writing build.json, or stage-complete rejects it → `stages/ref/build/artifact.md`

**Exit:** write `/tmp/<R>/build.json` from the stage-start example: `{"artifact": {"tdd": {"green_pass": true, "smoke_tests": S, "tests_generated": N, "regressions": 0}, "ac_coverage": [...], "diff_summary": "..."}, "record": {"decisions": [...], "gate1_verdict": "PASS", ...}}` (`files_changed` derived from `files_touched`; `gate1_*` + `replanned_acs` belong in `record`, which replaces the build record). Then `python backend/scripts/artifact_cli.py stage-complete --project <P> --run-id <R> --stage build --data @/tmp/<R>/build.json` (no `--check` first).

> **⏭️ Continuation contract — advance is not the end of your turn.** `stage-complete` records and advances (to review); read `next_card` and run that stage in this same turn. Legal hand-backs only: a stage-boundary **L2 Judgment** (Escalation Routing Protocol), a true **checkpoint** trigger (`should_checkpoint=true`, retries exhausted, a mid-stage L2), a pending user question, or an abandon verdict. `next_action` is AUTHORITATIVE over this prose.
