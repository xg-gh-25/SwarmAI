# TEST Stage

## Base Methodology

> **Reference:** `backend/skills/s_qa/INSTRUCTIONS.md`
>
> Follow the scoped test methodology defined there: detect test framework, run scoped tests, fix failures with atomic commits.

## Pipeline-Specific Behavior

### Test Strategy (3 layers, executed in order)

#### Layer 1: AC-Driven Verification

Load the BUILD artifact and extract `ac_coverage`:

```bash
# Retrieve BUILD artifact (changeset type)
python backend/scripts/artifact_cli.py discover --project <PROJECT> --types changeset --full
# Read the ac_coverage array from the artifact JSON
```

For each ac_coverage entry, execute its declared test:

```bash
# Extract test functions from ac_coverage and run them explicitly
pytest -xvs -k "test_login_email or test_handle_error or test_rate_limit"
```

**Why this layer:** BUILD claims "AC1 is tested by test_foo." This layer
verifies that claim by actually running test_foo. If BUILD fabricated
coverage (impl exists but test doesn't pass), this catches it immediately.

**On failure:** Report which AC's test failed. Do NOT fix the test expectation —
fix the code. If the test was wrong from BUILD, that's a BUILD quality issue
(flag in artifact, don't silently adjust).

#### Layer 2: Dependency-Scoped Regression

Run tests that import or reference the changed modules:

```bash
# Python projects:
grep -rl "from module_name\|import module_name" tests/ --include="*.py" | \
  xargs pytest -x --timeout=60

# TypeScript/JavaScript projects:
grep -rl "from.*module_name\|require.*module_name" src/ --include="*.ts" --include="*.tsx" | \
  xargs npx vitest run --reporter=verbose
```

Adapt the grep pattern to the project's language (read TECH.md for test framework).
If Layer 2 returns zero test files for a non-trivial changeset, emit a WARNING —
this may indicate the grep pattern doesn't match the project's import style.
If the project has no `tests/` directory, skip Layer 2 with WARN in artifact:
`"dependency_scoped": {"run": false, "reason": "no test directory"}`.

**Why this layer:** A change to `auth.py` might break `test_session.py` that
imports auth. AC-driven tests only verify what was explicitly declared;
dependency-scoped tests catch collateral damage.

**Scope limit:** Only tests that directly import changed modules. NOT the full
suite (STEERING R9: full suite needs user approval + xdist deadlock risk).

#### Layer 3: Import Smoke (Zero-Infrastructure E2E Proxy)

For each new `.py` file in `files_changed`, verify it can be imported without crash:

```bash
# For each new/changed python file:
python -c "import sys; sys.path.insert(0, '.'); import module_name"
```

**Why this layer:** Catches wiring bugs (circular imports, missing dependencies,
wrong relative paths) that unit tests miss because they import individual
functions, not modules. C011 (Voice Mode) had code that passed all unit tests
but crashed on real import because of a circular dependency.

**On failure:** ImportError = wiring bug. Fix the import structure before
proceeding.

### Regression Definition

| Status | Meaning | Action |
|--------|---------|--------|
| **Regression** | Test PASSED before this changeset, FAILS now | Fix the code (not the test) |
| **New failure** | Test was ADDED in this changeset and fails | Fix the code (TDD green phase incomplete) |
| **Pre-existing** | Test was already failing before this changeset | Log in IMPROVEMENT.md "Known Issues", do NOT fix in this pipeline run |
| **Flaky** | Test passes/fails non-deterministically | Fix the flake (race condition, time dependency). A test that passes 9/10 = FAILS |

### Common Rationalizations

| Rationalization | Reality | Source |
|---|---|---|
| "Tests pass, no need for scoped re-run" | Run changed + related test files. Pass in isolation ≠ pass together. LL13: mock-based tests all passed but real DB had zero matching rows — function returned empty string in production. | LL13 |
| "This fix is simple, skip the WTF score" | Score every fix. "Simple" fixes that touch 4 files are not simple. C009: 5 iterations on a "simple" hook because each fix revealed new scope. | C009 |
| "I'll adjust the test expectation to match the new behavior" | Fix the CODE, not the test. Changing test expectations = changing the spec = go back to PLAN. Tests define CORRECT behavior; code must conform to them. | TDD principle |
| "Pre-existing failure, not our problem" | Log it in IMPROVEMENT.md "Known Issues." Never silently pass over a red test — it erodes the signal. Today's "pre-existing" is tomorrow's "we thought it was fine." | Pipeline design |
| "19 fixes done, just one more to clean up" | 20 is the hard cap. Checkpoint. Report. Quality > completion. The 21st fix historically introduces more bugs than it solves (WTF score data). | WTF Gate |
| "Tests are flaky, re-run until green" | Flaky = non-deterministic = real bug (race condition, shared state, time dependency). Fix the flake, don't re-roll the dice. A test that passes 9/10 times FAILS. | STEERING.md |

### WTF Gate

Calculate WTF score via script:

```bash
python backend/skills/s_autonomous-pipeline/scripts/wtf_gate.py --files-touched N --fix-count M
```

WTF score formula:
```
wtf_score = 0
+2 if fix touches > 3 files
+3 if fix modifies unrelated module
+2 if fix changes API contract
+1 if fix_count > 10
+3 if previous fix broke something
--> halt if wtf_score >= 5 (judgment decision --> L2 BLOCK)
```

### Max Fixes

Max 20 fixes per session. After 20, checkpoint and report regardless of remaining failures.

### Test Execution Summary

```
1. Layer 1: Run AC-declared tests (from ac_coverage)
2. Layer 2: Run dependency-scoped tests (grep imports of changed modules)
3. Layer 3: Import smoke for new files
4. For each failure: attempt fix + atomic commit
5. Run WTF gate after each fix
6. Re-run failed tests after fixes (confirm green)
```

### Exit Evidence Checklist

Confirm each before publishing:
- [ ] Layer 1 output pasted (AC tests: N/N pass)
- [ ] Layer 2 output pasted (dependency scope: N tests, N pass)
- [ ] Layer 3 output (import smoke: N modules, N success)
- [ ] WTF score calculated and shown (even if 0)
- [ ] Each fix has atomic commit listed
- [ ] Remaining unfixed issues documented with diagnosis
- [ ] No test expectation modifications (only code fixes)
- [ ] Regressions vs new failures vs pre-existing clearly separated

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type test_report --producer s_autonomous-pipeline \
  --summary "Tests: <passed>/<total> pass, <fixed> bugs fixed, layers: AC/dep/smoke" --stage test \
  --data '{"passed":true,"failed":M,"fixed":K,"skipped":J,"tests_new":N,"tests_total":T,"regressions":0,"layers":{"ac_driven":{"run":true,"pass":A},"dependency_scoped":{"run":true,"tests":B,"pass":C},"import_smoke":{"run":true,"modules":D,"pass":E}}}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state deliver
```
