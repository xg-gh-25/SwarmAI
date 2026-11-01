# Structured QA

Diff-aware testing with atomic fixes and self-regulation. Works on any git repo
at L0 (no project needed). Gets smarter with run documents (L1) and DDD docs (L2).

## Workflow

Execute these steps in order. Skip steps that don't apply.

### Step 1: SCOPE

Determine what changed and what to test.

**L0 (always available):**
```bash
# Find changed files vs main branch (or master, or default)
git diff --name-only main...HEAD 2>/dev/null || git diff --name-only HEAD~5
```

Detect the test framework from project config files:
```bash
# Check for common test configs
ls pyproject.toml package.json Cargo.toml go.mod Makefile 2>/dev/null
```

| Config File | Test Command | Scope Flag |
|-------------|-------------|-----------|
| `pyproject.toml` | `pytest` | `-k <pattern>` |
| `package.json` + vitest | `npx vitest run` | `--reporter=verbose` |
| `package.json` + jest | `npx jest` | `--testPathPattern` |
| `Cargo.toml` | `cargo test` | `-- <pattern>` |
| `go.mod` | `go test ./...` | `-run <pattern>` |

**L1 (inside a pipeline run, with its documents):**
- Use the run's changeset for the precise file list and branch base
- Use the run's design document for acceptance criteria
- Use the run's review findings for flagged issues to verify

**L2 (with DDD docs) — read these BEFORE auto-detection:**

From the project's TECH.md (look each up with `"$SWARM_BACKEND_BIN" cognition find --project <project> --doc TECH.md --query "<section words>"`, or `cognition map --project <project>` to see its sections; never read the whole file):
- **Dev Commands section** — use the explicit test command instead of auto-detecting. If TECH.md says `pytest -x --tb=short`, use that, not bare `pytest`.
- **Conventions section** — understand naming patterns, file structure, commit format.
- **Key Files section** — know which domains map to which files for targeted testing.
- **Environment Notes** — port randomization, credential chains, sandbox restrictions that affect test execution.

From the project's IMPROVEMENT.md (`cognition find --project <project> --doc IMPROVEMENT.md --query "<module or failure words>"`):
- **What Failed section** — identify historically buggy areas. If a module has failed before, test it more thoroughly even if it's not in the changeset.
- **Known Issues section** — skip known-flaky tests that are pre-existing, not caused by the changeset. Report them as "skipped (known issue)" not "failed".
- **What Worked section** — reuse testing patterns that previously caught real bugs.

**Priority:** TECH.md test commands override auto-detection. IMPROVEMENT.md known issues override default pass/fail interpretation. If DDD docs conflict with auto-detection, DDD wins.

### Step 2: PLAN

Map changes to what needs testing:

```
Changed backend router → run related pytest module
Changed React component → visual test that route
Changed CSS/Tailwind → check responsive breakpoints
Changed database schema → run migration + integration tests
Changed config → verify startup still works
```

If the design document lists acceptance criteria, build a checklist from those.
Otherwise, derive test targets from the diff.

### Step 3: UNIT TEST

Run the detected test framework, scoped to changed files where possible:

```bash
# Python — scope to changed test files or related modules
pytest tests/test_changed_module.py -x --tb=short -q

# TypeScript — scope to changed components
npx vitest run --reporter=verbose src/changed-component.test.ts
```

Report results clearly:
- Total: X passed, Y failed, Z skipped
- For each failure: file, test name, error summary (one line)

### Step 4: VISUAL TEST (if UI files changed)

Only run if `.tsx`, `.css`, `.html`, or `.vue` files are in the changeset.

1. Start the dev server if not running (read TECH.md for the command, or detect from package.json)
2. Use `s_browser-agent` to navigate to affected routes
3. Take screenshots of each affected page
4. Check for: layout breaks, missing content, console errors, responsive issues

### Step 5: FIX LOOP

For each bug found in steps 3-4:

1. **Diagnose** — identify the root cause (not just the symptom)
2. **Fix** — make the minimal code change
3. **Verify** — run the specific test that caught it
4. **Commit** — atomic commit: `fix(qa): <one-line description>`

**WTF Gate — halt if fixes get risky:**

After each fix, score it:

| Condition | Score |
|-----------|-------|
| Fix touches more than 3 files | +2 |
| Fix modifies an unrelated module | +3 |
| Fix changes an API contract or interface | +2 |
| More than 10 fixes already this session | +1 |
| Previous fix broke a different test | +3 |

**If score >= 5: HALT immediately.**

This is an **[BLOCK]** escalation — the pipeline pauses for human review.

Report remaining unfixed issues with diagnosis but don't attempt more fixes:
```markdown
> [BLOCK] **WTF Gate triggered: QA fixes getting risky (score: N)**
>
> Stopped after M fixes. N issues remain unfixed.
> **Options:**
> 1. Continue fixing (I'll be more conservative)
> 2. Ship with known issues (list attached)
> 3. Revert last N fixes and take a different approach
```

**Hard cap: 20 fixes per session.** After 20, halt regardless of WTF score.

**Unexpected regression (test fails outside changeset):**
```markdown
> [BLOCK] **Unexpected regression: test_payment_flow fails but no payment code changed**
>
> This may indicate a coupling issue. I need direction:
> 1. Investigate the regression (adds scope)
> 2. Skip it — not related to current work
> 3. Log it as a known issue in IMPROVEMENT.md
```

### Step 6: REPORT

Summarize results:

```markdown
## QA Report

### Summary
- **Passed:** X tests
- **Failed:** Y tests (Z fixed by QA)
- **Skipped:** W tests
- **WTF halts:** 0 (or explain why)

### Fixes Applied
1. `fix(qa): <description>` — <commit hash>
2. ...

### Remaining Issues
1. <issue description> — <file:line> — <diagnosis>
2. ...

### Visual Test Results
- [route]: OK / FAIL (screenshot attached)
```

**L1+:** Include this structured form with the report:
```json
{
  "passed": 45,
  "failed": 3,
  "fixed": 2,
  "skipped": 5,
  "bugs": [{"file": "...", "line": 42, "description": "...", "fixed": true}],
  "wtf_halts": 0,
  "screenshots": []
}
```

## Anti-Rationalization

The agent will rationalize cutting corners. These rebuttals are non-negotiable:

| Agent Shortcut | Required Response |
|---|---|
| "Tests pass, so the code works" | Tests prove what they test. Walk the real user path to catch what they miss. |
| "This is a minor fix, visual testing isn't needed" | Minor CSS changes cause major layout breaks. If UI files changed, test visually. |
| "The WTF gate score is close but not quite 5" | Score of 4 means one decision away from risky. Slow down and assess. |
| "I'll fix this test failure by adjusting the test" | Fix the CODE, not the test. Changing a test = changing the spec. |
| "Running the full suite would take too long" | Run scoped tests for changed files. Never skip testing to save time. |
| "This regression is pre-existing, not from our changes" | Document it in IMPROVEMENT.md as known issue. Never silently ignore a failure. |
| "I've already fixed 19 things, one more won't hurt" | 20 is the hard cap. Checkpoint and report. Quality > completion. |

## Verification

Before marking QA complete, show evidence for each:

- [ ] **Test output pasted** — actual pytest/vitest output showing pass/fail counts
- [ ] **Zero silent skips** — every skipped test has an explicit reason
- [ ] **Fix commits listed** — each fix has atomic commit hash + one-line description
- [ ] **WTF score calculated** — show the score breakdown, even if 0
- [ ] **Visual test evidence** — screenshots shown or "no UI files in changeset" stated
- [ ] **Remaining issues documented** — every unfixed issue has diagnosis + file:line
- [ ] **QA Report generated** — all subsections filled (Summary, Fixes, Remaining, Visual)

## Rules

- **Never modify test files to make tests pass.** Fix the code, not the tests.
- **Never skip failing tests** unless they are pre-existing failures unrelated to the changeset.
- **Atomic commits only** — one fix per commit, with `fix(qa):` prefix.
- **WTF gate is non-negotiable** — if the score hits 5, stop fixing immediately.
- **Report everything** — even if you can't fix it, diagnose and document it.
- **L0 works without setup** — auto-detect everything. Never tell the user "I need TECH.md first."

## Inside a pipeline run

Stage progress is recorded by `"$SWARM_BACKEND_BIN" pipeline-v1`
(`stage-complete --project <PROJECT> --run-id <RUN_ID>`), never by this skill. Standalone,
the report shown to the user is the whole hand-off.

> **⏭️ Continuation contract — advance is not the end of your turn.** `advance` only records the state transition; it is NOT a
> handoff point. Immediately Read the next stage's doc (the `advance` output prints its
> absolute path as `next_stage_doc`) and execute that stage **in this same turn**. The ONLY
> legal reasons to hand control back are: a stage-boundary **L2 Judgment** routed through the
> Escalation Routing Protocol, a **true checkpoint trigger** (`should_checkpoint=true`,
> retries exhausted, or a mid-stage L2), a pending question awaiting the user, or an abandon
> verdict. Absent one of those, ending your turn here is the stall this contract exists to
> prevent.
>
> **`next_action` is AUTHORITATIVE — this sentence defers to it.** The payload is measured at
> the moment you advance; this prose was written long before. When `advance` reports a due
> checkpoint (or that it could not measure the budget), it withholds the continue instruction
> ON PURPOSE — follow the payload, not this paragraph. Prose cannot see the run's state; the
> payload can.
