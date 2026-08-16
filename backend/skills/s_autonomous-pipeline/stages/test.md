# TEST Stage

### 🚨 STOP — Are You About to Skip Test Layers?

**The temptation at TEST is not to skip testing entirely — it's to run only
Layer 1 (AC tests you wrote) and declare "tests pass." Layers 2 and 3 catch
the bugs YOUR tests can't — because you wrote both the code AND the tests.**

| What you're thinking right now | Why it's wrong | Evidence |
|------|------|------|
| "My unit tests pass, that's sufficient" | Unit tests verify YOUR assumptions. Layer 2 catches collateral damage to code that imports yours. | C011: all unit tests green, feature broken |
| "Dependency-scoped grep found 0 files" | Wrong grep pattern? Check TECH.md for import style. 0 results for non-trivial change = suspicious, not reassuring. | LL31: mock format ≠ production format |
| "Import smoke is pointless for existing files" | Circular imports, missing deps, wrong relative paths — all invisible to unit tests that import individual functions. | C011: circular dep crashed real import |
| "I'm running low on budget, skip Layer 3" | Layer 3 is ONE command per file, usually <10 seconds total. It costs less context than the sentence you used to rationalize skipping it. | O008: measure before optimizing |
| "Tests passed in BUILD, why run again?" | BUILD tests ran BEFORE the refactor step. Post-refactor state may differ. Fresh run = fresh evidence. | SOUL P1: Verify, Don't Infer |
| "Full suite would be better but takes too long" | NEVER run full suite (STEERING R9). Layers 1-3 ARE the scoped alternative. Skipping them means you have NO regression signal. | C013: full suite deadlocked |
| "The fix was obvious, no need for WTF scoring" | Score EVERY fix. WTF gate is mechanical, not discretionary. "Obvious" fixes that touch 4+ files are not obvious. | C009 |

---

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
# Extract test functions from ac_coverage and run them explicitly, using the
# project's test runner (read TECH.md ## Dev Commands). SwarmAI-self reference:
pytest -xvs -k "test_login_email or test_handle_error or test_rate_limit"
# Other stacks: go test -run 'TestLoginEmail|TestHandleError'; cargo test <names>;
# mvn -Dtest=... ; npx vitest run -t "login email".
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
suite. *(SwarmAI-SELF note: SwarmAI's full suite needs user approval + has a
pytest-xdist deadlock risk — STEERING R9. Other projects: read their TECH.md/
STEERING for the full-suite policy; the xdist hazard is SwarmAI-specific.)*

#### Layer 3: Build/Import Smoke (Zero-Infrastructure E2E Proxy)

Verify each new/changed file loads without crashing, using the project's stack.
**Python projects** — import each new module:

```bash
# SwarmAI-self (Python): for each new/changed python file
python -c "import sys; sys.path.insert(0, '.'); import module_name"
```

**Other stacks — use the equivalent load/compile check, or WARN-skip Layer 3
if none applies** (read TECH.md ## Stack): Go `go build ./...`; Rust `cargo check`;
TypeScript `tsc --noEmit`; Java compile the changed sources. If the project has
no cheap load-check, record `"import_smoke": {"run": false, "reason": "<stack> has no import-smoke equivalent"}`.

**Why this layer:** Catches wiring bugs (circular imports, missing dependencies,
wrong relative paths) that unit tests miss because they import individual
functions, not modules. C011 (Voice Mode) had code that passed all unit tests
but crashed on real import because of a circular dependency.

**On failure:** ImportError = wiring bug. Fix the import structure before
proceeding.

#### Layer 4: Cross-Boundary E2E (CONDITIONAL — fires ONLY when `cross_boundary.value == true`)

Read the EVALUATE artifact's `cross_boundary` flag (see EVALUATE § Cross-Boundary
Classification). **If `false` → skip this layer** (record `"cross_boundary_e2e": {"run": false, "reason": "not a cross-boundary change"}` and move on — no ceremony tax). **If `true` → this layer is a MANDATORY DoD item; the run is not TEST-complete without it.**

Layers 1-3 are all *unit-shaped* — they exercise one side of a seam (AC's own test,
importers, a module import). None drive the SEAM. Layer 4 does: it drives the **real
system through the actual boundary** the change crosses.

**Two hard requirements (both, or it's theater):**

1. **Drive the REAL wiring, not a mock of it.** The test must exercise the actual
   contract end-to-end: fire the real event through the real listener/registry; send
   the real payload through the real serializer; run the real reader against the real
   writer's output. **The test MUST NOT `patch`/mock the thing-under-change** — mocking
   the event bus / the registry / the serializer you just edited is the CLASS-A
   test-theater the whole layer exists to prevent. (Mock only the far leaf boundary —
   a network call, the LLM — never the seam itself.)
2. **Mutation-verify it's non-vacuous.** Revert the one contract line the change added
   (the dispatch→open mapping, the migrated field, the allowlist entry) → the Layer-4
   test MUST go RED. A green-on-revert Layer-4 test proves nothing; state the mutation
   you ran + that it went RED in the artifact.

**Per boundary kind, "drive the real system" means:**
- *event-bus / ACT-SENSE* → mount the real provider + host + registry, `dispatchEvent`
  the real `swarm:*` event, assert the surface opens (+ that its state is READ back).
  (Canonical example: `overlayHostE2E.test.tsx`, run_567b107e — 7 surfaces × real
  registry × real event; mutation = revert open-on-show → all RED.)
- *frontend↔backend contract* → assert the frontend table is DERIVED from (or bound by a
  test to) the backend SSOT, so a divergence is impossible/RED (e.g.
  `test_backend_allowlist_is_bound_to_leftnav_ssot`).
- *data/schema migration* → run the real reader against a real writer's output shape
  (O009: real production data shape, not a fixture that encodes your assumption).
- *multi-subsystem shared path* → smoke EACH subsystem independently through the shared
  path before combining (R16: send 1 msg → stream → content persists on tab switch).

**Record in the TEST artifact:** `"cross_boundary_e2e": {"run": true, "test_file": "...", "drives_real": "what real wiring it exercises", "mutation": "reverted X → test RED"}`.

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
| "All units pass, the migration is done — Layer 4 is overkill" | Units pass ONE side of a seam; a cross-boundary change breaks the SEAM, which no unit sees. run_fdeaead8: every unit green, the ACT/SENSE contract silently severed, caught by adversarial not E2E. If EVALUATE set `cross_boundary=true`, Layer 4 is mandatory. | run_fdeaead8 (M4) |
| "I wrote a Layer-4 test and it's green" | Green ≠ non-vacuous. If it mocks the thing-under-change, or stays green when you revert the contract line, it's theater. Mutation-verify: revert the seam → it MUST go RED. | CLASS-A test-theater |

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

### Single-Platform Compile Trap (BLOCKING for cross-platform changes)

**Trigger:** the changeset adds a `#[cfg(...)]` / `#ifdef` / platform-conditional
symbol, OR touches code that compiles per-target (Rust, C/C++, Go build tags,
platform-specific imports).

**The trap:** a compile check on ONE platform (`cargo check` on the dev Mac)
passes while another target FAILS — e.g. a `#[cfg(target_os="macos")]` function
referenced by an un-gated caller breaks the Windows build (E0425). The green
local check hides it. This run (run_8a9de435) hit exactly this; adversarial
review caught the Windows break a macOS `cargo check` reported as clean (RP40).

**Rule:** a single-platform compile success may NOT be reported as a fully-green
TEST result for a cross-platform changeset. Either:
1. Run the cross-target check locally if the toolchain exists
   (`cargo check --target <other>`), OR
2. Mark the result explicitly: `"platform_verified": ["macos"],
   "platform_pending_ci": ["windows", "linux"]` and surface it as a known gap
   in DELIVER — the CI matrix is the definitive check, not the local build.

Never write `all_pass: true` / "compiles" unqualified when only one of N targets
was actually checked.

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
- [ ] Cross-platform changeset: single-platform compile NOT reported as fully-green (Single-Platform Compile Trap above)

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> --run-id <RUN_ID> \
  --type test_report --producer s_autonomous-pipeline \
  --summary "Tests: <passed>/<total> pass, <fixed> bugs fixed, layers: AC/dep/smoke" --stage test \
  --data '{"passed":true,"failed":M,"fixed":K,"skipped":J,"tests_new":N,"tests_total":T,"regressions":0,"layers":{"ac_driven":{"run":true,"pass":A},"dependency_scoped":{"run":true,"tests":B,"pass":C},"import_smoke":{"run":true,"modules":D,"pass":E}}}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state deliver --run-id <RUN_ID>
```
