# TEST ref — the three test layers through the runner

Read when the TEST card sends you here: a layer looks empty, a layer must be skipped,
or the project's stack is not one the runner covers.

`RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`

## Test Strategy (3 layers, executed in order) `[GATE·validator]`

(The gate itself is on the card, tagged R-test-001; this is the layer detail.)

The `layers` block (+ `passed`) is a REQUIRED test-artifact field: the validator BLOCKS
if it is absent. Presence is code-checked. Layer content (which tests ran) is now
produced by the runner: `stage-complete --stage test` copies `layers` and counts
`regressions` from `evidence/tests/latest-regression.json`. Do not hand-copy numbers.

All three layers run in ONE call, `RUN --mode regression` (this replaces the old
three hand-run commands: `pytest -k <ac tests>`, `grep -rl ... | xargs pytest`, and
`python -c "import module"`). The runner shards per file, runs in parallel under one
~95 s deadline, kills on timeout with a faulthandler stack attached, and caches by
input digest (unchanged inputs return `cached`, 0 s).

The old base methodology pointer (`backend/skills/s_qa/INSTRUCTIONS.md`: detect the
framework, run scoped tests, fix failures with atomic commits) still describes the
method; inside a pipeline run the runner IS that method. Do not run s_qa's raw
commands — `pipeline_test_gate` denies them.

### Layer 1 — AC-driven verification (`layers.ac_driven`)

- Source: the BUILD artifact's `ac_coverage[].test` refs (`file.py::test_fn` or
  `file.py`). The runner reads them itself; you no longer `discover --types changeset`
  and build a `-k` expression.
- Why: BUILD claims "AC1 is tested by test_foo". This layer verifies the claim by
  running test_foo. Fabricated coverage (impl exists, test does not pass) fails here.
- `warnings[]` containing `ac_coverage test ref not found: <ref>` means BUILD declared
  a test that does not exist. Flag it in the artifact as a BUILD quality issue.
- On failure: report which AC's test failed. Fix the code, NOT the test expectation.
  If the test was wrong from BUILD, that is a BUILD quality issue: flag it in the
  artifact, don't silently adjust it.

### Layer 2 — dependency-scoped regression (`layers.dependency_scoped`)

- Scope: tests that import (transitively, via the runner's AST reverse-import graph)
  the changed modules, plus changed test files. Changed = run.json `files_touched` ∪
  `git diff HEAD` ∪ untracked. Cap 40 files; the overflow is listed in
  `skipped_for_budget`, never dropped silently. NOT the full suite.
- Frontend: changed `desktop/src/**` files add `vitest related <files> --run` and
  `tsc --noEmit` inside the same call (result under `frontend` in the output).
- Why: a change to `auth.py` can break `test_session.py`, which imports auth. AC tests
  only verify what was declared; this layer catches collateral damage.
- Zero L2 tests for a non-trivial changeset is suspicious, not reassuring. Emit a
  WARNING in the artifact and check why (the changed files may not be importable
  modules, or the code is reached only dynamically).
- No test directory in the project → skip with WARN:
  `"dependency_scoped": {"run": false, "reason": "no test directory"}`.
- Full-suite policy: SwarmAI's full suite needs user approval and has a pytest-xdist
  deadlock risk (STEERING R9); the runner has no full-suite mode. Other projects: read
  their TECH.md / STEERING for the full-suite policy (the xdist hazard is SwarmAI-only).

### Layer 3 — build/import smoke (`layers.import_smoke`)

- The runner imports every changed, non-test backend module (zero-infrastructure E2E
  proxy). For desktop changes, `tsc --noEmit` is the load check.
- Why: catches wiring bugs (circular imports, missing dependencies, wrong relative
  paths) that unit tests miss because they import individual functions. One feature
  passed all unit tests but crashed on real import because of a circular dependency.
- On failure: ImportError = wiring bug. Fix the import structure before proceeding.

## Stacks and repos the runner does not cover

The runner scopes the SwarmAI repo (`backend/tests/**` via pytest, `desktop/src/**` via
vitest + tsc). For any other stack or repo, read TECH.md `## Dev Commands` / `## Stack`
and run the project's own command through the runner's probe mode, so it stays inside
the gate and gets a recorded, deduplicated result:

`RUN --mode probe --name <stack>-<layer> -- <argv>`

The probe's stdout must be JSON `{"findings": [{"id": "...", ...}]}`; wrap a plain
tool in a tiny script that turns each failure into a finding with a stable id.
Old per-stack equivalents, now run as probe argv:

| Layer | Go | Rust | Java | TypeScript (other repo) |
|---|---|---|---|---|
| L1 AC tests | `go test -run 'TestLoginEmail\|TestHandleError'` | `cargo test <names>` | `mvn -Dtest=...` | `npx vitest run -t "login email"` |
| L3 load check | `go build ./...` | `cargo check` | compile the changed sources | `tsc --noEmit` |

A probe with 0 new findings has converged; re-running it with target and probe unchanged
returns the last result at 0 s (design §4-G2). Do not add rounds after convergence.

## Skip records (when a layer cannot apply)

Record the skip in the artifact instead of omitting the layer:

- `"dependency_scoped": {"run": false, "reason": "no test directory"}`
- `"import_smoke": {"run": false, "reason": "<stack> has no import-smoke equivalent"}`
  (WARN-skip Layer 3 only when the stack has no cheap load check at all)
- `"cross_boundary_e2e": {"run": false, "reason": "not a cross-boundary change"}`

## Reading the runner output

- Exit 0 = green, or only pre-existing / flaky failures (none a timeout). Exit 1 =
  failures, any timeout, a selected file that collected zero tests, a shard that
  exited abnormally (`runner_errors`), or new probe findings. Exit 2 = usage error,
  refusal, runner error, or interrupted.
- `failures[]` carries `nodeid`, a ~10-line `summary`, `outcome`, `classification`
  and `deadline`. Full logs are under `runs/<R>/evidence/tests-<mode>-<ts>/`.
- A timed-out test carries its stack. Read it; never hand-triage with `ps`, `kill`,
  `python -X faulthandler` or `sleep` polling (the gate denies these too).
- `--deadline` defaults to 95 s. Above 100 s the outer alarm must be at least
  deadline + 10 s (e.g. `alarm 310` with `--deadline 300`).
