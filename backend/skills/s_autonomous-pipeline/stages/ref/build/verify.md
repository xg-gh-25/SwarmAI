# BUILD ref — tests in BUILD, and wiring checks (callers, interface seams, proximity, sources)

Read before SMOKE when the changeset adds public symbols, cross-module interfaces, changed
function bodies or framework-specific patterns. `RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`.

## Step 3: tests in BUILD — tdd only

The change's whole dependency scope (`--mode verify` / `regression` / `recheck`: changed
tests, every direct importer, CI BVT) runs ONCE per run, at DELIVER after the last change
(`stages/deliver.md` 4b). Landing reads only that report, so a scope run in BUILD was never
used; the runner refuses it here. `[MUST]` <!-- R-build-008 -->

> These BUILD-step "BLOCKING" labels (1.5 API-check, 1.7 Mechanism-decl, 3.6 Interface-seam)
> are discipline, `[MUST]`. The only code-enforced BUILD gate is the AC Coverage Matrix
> (Check 8f, `[GATE·validator]`, in `ref/build/artifact.md`) + the `tdd`/`files_changed`/
> `ac_coverage` schema-required fields. <!-- R-build-009 -->

- BUILD's evidence is the tdd runs: `RUN --mode tdd --files <≤5 test files>` for the
  tests your change touches (RED, then GREEN). Batch a slice's tests into one call.
- **Resolve THIS project's test command first** from TECH.md `## Dev Commands` (and
  `## Stack`). SwarmAI repo → the runner. Any other stack → TECH.md's command via
  `RUN --mode probe --name <stack>-tdd -- <argv>` (`stages/ref/test/layers.md`).
- **NEVER run SwarmAI's full suite (`SWARMAI_SUITE=1`) as an agent**; never pipe a test run
  through `| tail` / `| head` (the runner prints one JSON object; read it whole).
- A failing existing test: fix production code, NOT the test. `timeout` (the call's
  deadline) is not a code failure: run the same command again (`continue: true`).
- `pre-existing` failures (the runner's HEAD-baseline classification) are not yours.

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
