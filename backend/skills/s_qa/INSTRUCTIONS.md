# Structured QA

Diff-aware testing with atomic fixes and self-regulation. Works on any git repo
at L0 (no project needed). Gets smarter with artifacts (L1) and DDD docs (L2).

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

**L1 (with artifacts):**
- Read `changeset` artifact for precise file list and branch base
- Read `design_doc` artifact for acceptance criteria
- Read `review` artifact for flagged issues to verify

**L2 (with DDD docs) — read these BEFORE auto-detection:**

Read `Projects/<project>/TECH.md`:
- **Dev Commands section** — use the explicit test command instead of auto-detecting. If TECH.md says `pytest -x --tb=short`, use that, not bare `pytest`.
- **Conventions section** — understand naming patterns, file structure, commit format.
- **Key Files section** — know which domains map to which files for targeted testing.
- **Environment Notes** — port randomization, credential chains, sandbox restrictions that affect test execution.

Read `Projects/<project>/IMPROVEMENT.md`:
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

If design_doc artifact has `acceptance_criteria`, build a checklist from those.
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

**L1+:** Publish this report as a `test_report` artifact:
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

## Rules

- **Never modify test files to make tests pass.** Fix the code, not the tests.
- **Never skip failing tests** unless they are pre-existing failures unrelated to the changeset.
- **Atomic commits only** — one fix per commit, with `fix(qa):` prefix.
- **WTF gate is non-negotiable** — if the score hits 5, stop fixing immediately.
- **Report everything** — even if you can't fix it, diagnose and document it.
- **L0 works without setup** — auto-detect everything. Never tell the user "I need TECH.md first."

## Artifact Operations

**Discover upstream artifacts (before scoping):**
```bash
python backend/scripts/artifact_cli.py discover --project <PROJECT> --types changeset,design_doc,review --full
```

**Publish test report (after completing QA):**
```bash
python backend/scripts/artifact_cli.py publish \
  --project <PROJECT> --type test_report --producer s_qa \
  --summary "<X passed, Y failed, Z fixed>" \
  --data '<JSON of test report>'
```

**Advance pipeline:**
```bash
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state deliver
```
