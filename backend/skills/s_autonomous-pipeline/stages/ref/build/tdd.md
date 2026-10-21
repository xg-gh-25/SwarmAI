# BUILD ref — the TDD loop (tracer bullet, increments, micro-replan, mocks)

Read before the first RED, when a RED→GREEN cycle keeps failing, or when choosing what to
mock. `RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`.

## Tests BEFORE code — the iron law

Write the test FIRST. Watch it FAIL. Then write the implementation. Code written before a
test exists for it is "code then rationalize tests", not TDD. **The test defines what
"correct" means; code without a pre-existing test has no definition of correct.** If you
wrote code before a test: DELETE IT and start over from RED. The profile selects STAGES,
not RIGOR within a stage: trivial still does BUILD test-first. A better approach spotted
mid-implementation is a deviation from the Change Spec: use the micro-replan trigger
below or go back to PLAN.

## Anti-Pattern: Horizontal Slices (BLOCKING) `[MUST]` <!-- R-build-001 -->

> "BLOCKING" here = TDD discipline, not a code gate — no validator inspects RED/GREEN
> ordering. `[MUST]`. <!-- R-build-002 -->

**DO NOT write all tests first, then all implementation** (RED = "write all tests",
GREEN = "write all code"). Bulk tests test _imagined_ behavior, test the _shape_ of things
(data structures, signatures) instead of user-facing behavior, pass when behavior breaks,
and commit you to a test structure before you understand the implementation.

**Correct: vertical tracer bullets.** One test → one implementation → repeat; each test
responds to what the previous cycle taught you.

```
WRONG (horizontal):  RED: test1..test5   GREEN: impl1..impl5
RIGHT (vertical):    test1→impl1 (tracer bullet, proves the path end-to-end)
                     test2→impl2 (responds to what you learned)  test3→impl3 ...
```

## Step 1: RED→GREEN tracer bullet

1. Read the acceptance criteria (PLAN design_doc, else the evaluation artifact).
2. **Read the project's TECH.md `## Dev Commands` + `## Stack`: THE source for the test
   framework and conventions of THIS project** (`pytest`, `vitest`, `go test`,
   `cargo test`, `mvn test`, `jest`…). `pytest`/`vitest` are SwarmAI's own stack; do NOT
   default to them for another project.
3. Pick the **single most important AC**, the one that proves the core path end-to-end.
4. Write ONE test for it; it MUST fail (nothing implemented yet).
5. Write minimal code to make that ONE test pass.
6. Stage it (`git add` your files by name): this is your tracer bullet, proof the path
   works. Do not commit (Step 2, item 9).

## Running tests inside the loop

- SwarmAI repo: `RUN --mode tdd --files <test files>`: only the listed files, max 5
  (backend `backend/tests/test_*.py` or `desktop/` tests). This replaces raw
  `pytest <file>` / `npx vitest run <file>`, which count as no evidence during a
  run. Unchanged inputs return the cached result at 0 s.
- More than 8 tdd runs of one file prints a reminder: batch the edits, then re-run. It
  never blocks; TDD iteration is legitimate.
- Other stacks or repos: run TECH.md's command as
  `RUN --mode probe --name <stack>-tdd -- <argv>` (stdout must be JSON findings; see
  `stages/ref/test/layers.md` "Stacks and repos the runner does not cover").
- A timed-out test comes back with its stack attached. Read it; never triage by hand
  with `ps`, `kill`, `faulthandler` or `sleep` polling.

## Step 2: incremental RED→GREEN loop

**Follow the Change Spec order** (PLAN artifact): sub-changes in dependency order, no
skipping ahead. For each sub-change's AC:

7. Write the next test → it fails (RED).
8. Write minimal code to pass → it passes (GREEN).
9. **Stage** after each green cycle (`git add` your files by name, never `-A`); do
   **NOT** `git commit`. `run-commit` (DELIVER) is the SOLE commit point, so the run's
   code and its REPORT.md audit projection land in **ONE** commit (the review-purpose
   invariant); committing here would split the code from the REPORT.md generated later at
   REFLECT. Enforced structurally: `active_run_commit_gate` DENYs an agent `git commit`
   of any active-run file and routes you to `run-commit`.
10. **Don't anticipate future tests**: only enough code for the current test.
11. **Completeness bias:** when the complete implementation costs minutes more than the
    shortcut, do the complete thing. Cover edge cases, handle errors.
12. **Failure-path test for every success-path test.** For each "X works when Y" test,
    write a companion "X handles failure gracefully when Z". If a function consumes a
    resource (event, message, token), test what happens when the consumer FAILS: is the
    resource preserved for retry or permanently lost? The failure test forces that
    design decision BEFORE implementation.

## Micro-replan trigger (automatic)

**If the same AC fails RED→GREEN 2 consecutive times** (test written, code attempted,
still failing; a fundamental approach mismatch, not a typo fix): **STOP the TDD loop. Do
NOT retry a third time.** Instead:

1. **Diagnose** why the approach fails (interface mismatch? missing dependency? wrong
   assumption from PLAN?).
2. **Micro-replan** THIS AC only: test it from a different angle? does file discovery
   reveal an interface the plan missed? is there an existing utility for it?
3. **Record** it: `{"replanned_acs": [{"ac": "AC2", "original_approach": "...",
   "new_approach": "...", "reason": "..."}]}` in the build stage record (run-update
   `--stage-json` now, and again in the stage-complete `record`, which replaces it).
4. **Resume TDD** with the new approach for this AC.

Rules:
- A replan is scoped to ONE AC; don't redesign the whole feature.
- Max 1 replan per AC. If the replan also fails → mark that AC unmet (`replanned_acs`
  with the failure), finish the other ACs, and carry on to DELIVER, where it caps
  push_ready and lands in REPORT §10. Never a checkpoint, never a question.
- The replan insight flows to REFLECT as a lesson.

## TDD constraint

Fix code, not tests. Tests are derived from the accepted design; changing a test =
changing the spec = go back to PLAN.

## Adversarial inputs in RED

For NLP/parsing code the RED phase must include adversarial inputs: URLs, file paths,
code snippets, empty/minimal strings, Unicode edge cases (CJK, Kana, Hangul, emoji) and
multi-language mix. These break keyword extractors, parsers and formatters.

## Mock discipline

**Boundary-only rule: mock at system boundaries ONLY. Never mock your own code.**

| Dependency category | Example | Test strategy | Mock? |
|---|---|---|---|
| **In-process** | Pure computation, in-memory state | Test directly | ❌ Never |
| **Local-substitutable** | SQLite, filesystem, Redis | Test stand-in (tmp dir, in-memory DB) | ❌ Prefer stand-in |
| **Remote-owned** | Your own microservice/API | Port interface + in-memory adapter | ✅ Mock the adapter |
| **True-external** | Stripe, AWS API, GitHub | Mock the leaf SDK call | ✅ Mock at boundary |

Mock external APIs, databases (when no test DB), time/randomness, sometimes the
filesystem. Never mock your own classes, internal collaborators, anything you control.
If refactoring internals breaks tests while behavior is unchanged, the mocks are too deep.

**Spec enforcement:** use `spec=RealClass`, or set only attributes the real class has. A
bare `MagicMock()` accepts ANY attribute and hides `AttributeError` bugs that crash in
production.

**Interface-first testing:** test behavior through public interfaces; a good test reads
like a specification ("user can checkout with valid cart") and survives refactors. Red
flags: mocking internal collaborators, testing private methods, asserting call counts or
order of internal calls, a test that breaks on a behavior-preserving refactor, verifying
through external means (a DB query) instead of the interface.

## Recording files_touched (MANDATORY, feeds the DELIVER commit)

Every Edit/Write of a source file during BUILD must be recorded so DELIVER's `run-commit`
can `git add` exactly this run's files (never `git add -A`, which sweeps a parallel
session's edits; R29). Record in batches (after each cluster of edits, or at the end):

`python backend/scripts/artifact_cli.py run-update --project <P> --run-id <R> --files-touched '["/abs/repo/src/foo.py","/abs/repo/tests/test_foo.py"]'`

- WRITTEN files only (Edit/Write targets + new test files), NOT files you merely Read; an
  over-broad list re-introduces the cross-session bleed this prevents.
- Prefer ABSOLUTE paths: `run-commit` resolves each to its git repo regardless of cwd.
  Relative paths work only if `run-commit` runs inside that file's repo.
- Dedup-append: safe to call repeatedly. `stage-complete` derives `files_changed` from it.
- Forgotten files: `run-commit` WARNS about untracked working-tree changes instead of
  committing the wrong set; record diligently anyway.
