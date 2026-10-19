# BUILD ref — VERIFY and wiring checks (callers, interface seams, proximity, sources)

Read when running VERIFY, when VERIFY is red, or before SMOKE when the changeset adds
public symbols, cross-module interfaces, changed function bodies or framework-specific
patterns. `RUN` = `perl -e 'alarm 590; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`.

## Step 3: VERIFY — targeted tests, zero regressions

**Resolve THIS project's test command first** from TECH.md `## Dev Commands` (and
`## Stack`). SwarmAI repo → the runner modes below. Any other stack or repo → TECH.md's
command via `RUN --mode probe --name <stack>-verify -- <argv>` (`go test ./...`,
`mvn test`, `cargo test`, `npm test`…; see `stages/ref/test/layers.md` "Stacks and repos
the runner does not cover").

**VERIFY rules (BLOCKING):** `[MUST]` <!-- R-build-008 -->

> These BUILD-step "BLOCKING" labels (1.5 API-check, 1.7 Mechanism-decl, 3 VERIFY,
> 3.6 Interface-seam) are TDD/verification DISCIPLINE — `[MUST]`, behavioral. The only
> code-enforced BUILD gate is the AC Coverage Matrix (Check 8f, `[GATE·validator]`, in
> `ref/build/artifact.md`) + the `tdd`/`files_changed`/`ac_coverage` schema-required
> fields. <!-- R-build-009 -->

- Run **changed test files + test files that import changed modules**: `RUN --mode verify`,
  Bash timeout 600000 (one call, deadline sized to the selection; the runner picks
  changed tests, this run's recent real failures and EVERY direct importer of the changed modules,
  uncapped and never cut for time; tests reaching a change only through another module
  are listed in `transitive_unrun` and REPORT §10, not run). This replaces the old raw
  `pytest tests/test_foo.py tests/test_bar.py --timeout=60`.
- **Widely-imported modules:** the old recipe grepped every dependent test file
  (`grep -rl "from database\|import database\|SQLiteDatabase" tests/`) and ran exactly
  those. `--mode verify` builds that reverse-import set itself; do not hand-grep and do
  not run the full suite instead.
- **SwarmAI-SELF:** **NEVER run SwarmAI's full suite (`SWARMAI_SUITE=1`) as an agent**:
  known pytest-xdist deadlocks cause infinite hangs; the full suite is human-only. Other
  projects have their own full-suite policy in TECH.md/STEERING; don't assume xdist.
- Unsure which tests to re-run (the old `pytest --lf --timeout=60`)? `RUN --mode recheck`
  (last failures + tests affected by files changed since). Other runners: the
  equivalent last-failed/filtered command as a probe.
- **NEVER** pipe a test run through `| tail` (or `| head`); it hides pass/fail status.
  The runner prints one JSON object; read it whole.
- All pass → continue. Tests fail → fix production code, then re-run **only failing
  tests** (`RUN --mode recheck`).
- **Max 2 VERIFY re-runs total.** Still failing after 2: complete BUILD with
  `"regressions": N` in `tdd` and advance to REVIEW anyway.
- Track the attempt count explicitly: "VERIFY attempt 1/2", "VERIFY attempt 2/2".
- Changed + related test files must all pass. If existing tests break, fix production
  code, NOT the existing tests. Track all files changed and test results.
- `timeout` failures (the global deadline killed them before they finished) are not a
  code failure: don't edit code for them and don't count them as a re-run. Re-run the
  same command unchanged (it re-runs only the unfinished tests); they block push-ready
  until they have run. A runner `circuit_breaker` refusal means checkpoint, not re-run.
- `pre-existing` failures (the runner's HEAD-baseline classification) are not yours:
  note them, don't debug them here.

## Step 3.5: Caller verification (new public functions)

After tests pass, before SMOKE, check every NEW public function, class or hook is wired:

`grep -rn "function_name" <project_root> --include="*.py" | grep -v "def function_name" | grep -v "test_"`

| Result | Action |
|---|---|
| 0 callers (excluding tests + definition) | **WARN**: dead code unless wired. Add the caller NOW or document why it's deferred. |
| 1+ callers | Verify the caller's arguments match the signature and it handles the return type. |

A fully tested, review-passed function with zero production callers (never registered
where it must be, e.g. in `hook_builder.py`) is "correct but disconnected" code.

**Skip when** the changeset is pure refactoring (existing callers unchanged) or touches
only private/internal functions (leading underscore).

## Step 3.6: INTERFACE SEAM VERIFICATION (for cross-module wiring) `[MUST]` <!-- R-build-010 -->

**After caller verification, before SMOKE.** Only when the changeset introduces a new
interface boundary (Protocol, ABC, duck-typed contract, callback/callable). For each:

1. **Identify the contract:** which methods/signatures does the consumer expect?
2. **Identify the satisfier:** which concrete class/function is passed at runtime?
3. **Verify method-by-method:** open the satisfier's source; for EACH method the
   contract expects, verify it EXISTS with a COMPATIBLE signature.

```bash
# Example: HeartbeatManager expects sender.send_message_raw(channel, text)
# Satisfier at runtime: SlackChannelAdapter
grep -n "def send_message_raw" backend/channels/adapters/slack.py
# Result: NOT FOUND → BLOCK — AttributeError guaranteed at runtime
```

- **Callable/lambda parameters** (`lambda: adapter.some_method(...)` as a callback): read
  the lambda body, verify `adapter.some_method` EXISTS on the real adapter class, and that
  the arguments the lambda passes match its signature.
- **Protocol classes (`typing.Protocol`):** verify at build time, e.g. a
  `@runtime_checkable` Protocol + `assert isinstance(concrete_instance, TheProtocol)`, or
  grep each method name in the satisfier's file.

| Result | Action |
|---|---|
| Method missing on satisfier | **BLOCK**: fix now; it will crash at runtime. |
| Method exists but signature differs | **BLOCK**: e.g. expects `(self, channel, text)` but the real method is `(self, external_chat_id, text, *, is_final)` |
| Method exists, signature compatible | ✓ Pass |

**Also verify parameter semantics, not just existence:** if the contract says
`post(channel, text)` posts `text` to `channel` but the real method ignores `text` and
always posts a hardcoded string, that is a semantic mismatch. Read the implementation
body, not just the signature.

**Skip when** the changeset only modifies internal implementation within one module (no
cross-module boundary changed).

## Step 3.8: Proximity scan (changed function bodies)

After all code changes pass tests, before SMOKE, for EACH function whose body changed
(not merely added/deleted):

1. **Read ±50 lines around the change** for docstrings describing the changed logic with
   specific values or behavior, adjacent comments referencing the old behavior, nearby
   test comments explaining the expected math/flow.
2. **For each such doc/comment:** does it still match? (`need=0.1` in the docstring vs
   `need=0.3` in code → update it.) Verify every specific number, formula or step.
3. **Input boundary analysis:** who calls this function (grep callers)? What is the
   **smallest valid input** a caller can send, and does it produce a reasonable output
   with the new logic? (e.g. `compute_confidence(1, 3, 1.0)`: is the result right for a
   single correction on a 3-example skill?)

| Finding | Action |
|---|---|
| Docstring/comment has stale values | **FIX**: update to match code |
| Extreme-but-valid caller input gives an unexpected result | **FIX**: add a guard or adjust the formula |
| Comment references the wrong line/function | **FIX**: correct the reference |

**Skip when** the change is pure deletion, the function has no docstring/comments within
±50 lines, or the change adds a new function (nothing pre-existing to go stale).

## Step 7: Source verification (framework decisions against official docs)

**Only when the changeset uses framework-specific patterns** (React hooks, FastAPI
decorators, boto3 calls, Pydantic models, SQLAlchemy queries…). Skip for pure logic that
works the same across versions (loops, conditionals, data structures). For each
non-trivial framework-specific decision:

1. **Detect** the framework + exact version from `pyproject.toml` / `package.json`.
2. **Fetch** the specific official documentation page for the pattern (not the
   homepage). Source hierarchy: official docs > official blog/changelog > MDN/web.dev >
   caniuse. **NOT authoritative:** Stack Overflow, tutorials, blog posts, training data.
3. **Verify** the implementation matches the current documented pattern; flag deprecated
   APIs (feeds RP26 in REVIEW) and patterns that differ from the documented approach.
4. **Cite** the doc URL in a code comment for non-obvious patterns.

```
SDD CHECK:
  Framework: FastAPI 0.115.0 (from pyproject.toml)
  Pattern: BackgroundTasks for async work
  Doc: https://fastapi.tiangolo.com/tutorial/background-tasks/
  Verified: ✅ matches current docs
```

**Skip** for basic syntax (imports, function definitions, loops), patterns already
verified this session, or when the user explicitly says "just do it" / "skip
verification". Training data goes stale; official docs don't.
