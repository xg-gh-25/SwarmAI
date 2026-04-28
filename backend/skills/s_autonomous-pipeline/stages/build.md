# BUILD Stage (TDD Red-Green Cycle)

Pipeline-owned stage (no sibling skill). This is the core implementation stage.

The BUILD stage follows TDD methodology: tests before code, code until tests pass.

## Anti-Pattern: Horizontal Slices (BLOCKING)

**DO NOT write all tests first, then all implementation.** This is "horizontal
slicing" — treating RED as "write all tests" and GREEN as "write all code."

This produces crap tests:
- Tests written in bulk test _imagined_ behavior, not _actual_ behavior
- You test the _shape_ of things (data structures, signatures) not user-facing behavior
- Tests become insensitive to real changes — pass when behavior breaks
- You outrun your headlights, committing to test structure before understanding impl

**Correct approach: Vertical tracer bullets.** One test → one implementation → repeat.
Each test responds to what you learned from the previous cycle.

```
WRONG (horizontal):
  RED:   test1, test2, test3, test4, test5
  GREEN: impl1, impl2, impl3, impl4, impl5

RIGHT (vertical tracer bullet):
  RED→GREEN: test1→impl1  (tracer bullet — prove the path end-to-end)
  RED→GREEN: test2→impl2  (each test responds to what you learned)
  RED→GREEN: test3→impl3
```

## Step 1: RED→GREEN Tracer Bullet

1. Read acceptance criteria from the evaluation artifact (or design_doc if PLAN ran)
2. Read TECH.md for test framework (pytest, vitest) and conventions
3. Pick the **single most important acceptance criterion** — the one that proves
   the core path works end-to-end
4. Write ONE test for it (it MUST fail — nothing implemented yet)
5. Write minimal code to make that ONE test pass
6. Commit: this is your tracer bullet — proof the path works

## Step 2: Incremental RED→GREEN Loop

For each remaining acceptance criterion, one at a time:

7. Write the next test → it fails (RED)
8. Write minimal code to pass → it passes (GREEN)
9. Commit after each green cycle
10. **Don't anticipate future tests** — only enough code for the current test
11. **Completeness bias:** when the complete implementation costs minutes more
    than the shortcut, do the complete thing. Cover edge cases, handle errors.

## Step 3: VERIFY -- Targeted tests, zero regressions

**VERIFY rules (BLOCKING):**
- Run **changed test files + test files that import changed modules**.
  ```
  pytest tests/test_foo.py tests/test_bar.py --timeout=60
  ```
- **For widely-imported modules** (database/sqlite.py, core/prompt_builder.py,
  session_router.py, etc.), find ALL dependent test files via grep:
  ```
  grep -rl "from database\|import database\|SQLiteDatabase" tests/ --include="*.py" | sort -u
  ```
  Then run exactly those files. This catches interaction bugs without running
  the full 700+ test suite (which hangs with xdist --maxfail).
  **NEVER run the full suite (`SWARMAI_SUITE=1`) as an agent** — it has known
  xdist deadlock issues that cause infinite hangs. Full suite is human-only.
- If you're unsure which tests to run, use `pytest --lf --timeout=60` (last-failed).
- **NEVER** pipe pytest through `| tail` -- it hides pass/fail and xdist status.
- **NEVER** pipe pytest through `| tail` -- it hides pass/fail and xdist status.
- If all tests pass -- proceed to Step 4. Done.
- If tests fail -- fix code, re-run **only failing tests**.
- **Max 2 VERIFY re-runs total.** After 2 runs, if still failing:
  publish changeset with `"regressions": N` and advance to REVIEW anyway.
- Track VERIFY attempt count explicitly: "VERIFY attempt 1/2", "VERIFY attempt 2/2".

11. Run changed + related test files -- all must pass
12. If existing tests break -- fix production code, NOT the existing tests
13. Track all files changed and test results

## Step 4: SMOKE -- exercise new code paths (catch runtime crashes)

14. For each modified file that has new branches (if/else, try/except,
    config-gated paths), write a minimal inline smoke test that forces
    execution through the new path. The goal is to catch AttributeError,
    NameError, and other runtime crashes that unit tests miss because
    they mock the surrounding context.

    ```python
    # Example: new config-gated code in prompt_builder.py
    from core.prompt_builder import PromptBuilder
    pb = PromptBuilder(config={"memory_progressive_disclosure": True})
    # Call the method that contains the new branch -- don't assert output,
    # just verify it doesn't crash with AttributeError/TypeError.
    ```

    Rules:
    - If the new code is behind a config flag -- test with flag=True
    - If behind a conditional (channel_context, resume, etc.) -- construct
      the triggering condition
    - If new code is in a try/except -- temporarily remove the except to
      verify the try body doesn't crash (the except silently swallows
      bugs like `self.config_manager` -- `self._config`)
    - **Multi-context callers:** If new code is called from both sync
      AND async contexts (e.g., `record_token_usage()` called from
      async `session_unit` AND sync `bedrock.invoke()`), smoke test
      BOTH calling contexts. A function that works in async FastAPI
      may silently fail from a sync background job. Don't assume one
      passing smoke test covers all callers.
    - **Cross-language boundaries:** When a changeset spans multiple
      languages (e.g., Python backend + Rust desktop + TypeScript frontend),
      smoke test the **data format at each boundary**. Produce the actual
      serialized output from the sender side and verify the receiver can
      parse it. Example: `json.dumps({"version": "1.8.4"})` produces
      `"version": "1.8.4"` (with space) — verify Rust parser handles this
      exact string, not a hand-crafted compact version. Cross-language
      format assumptions are invisible to single-language unit tests.
    - **Pattern grep after any bug fix:** After fixing a bug in SMOKE or
      USER-PATH TRACE, immediately grep the **entire codebase** for the
      same pattern: `grep -rn "the_broken_pattern" . --include="*.py" --include="*.rs" --include="*.ts"`.
      The bug you just found likely exists in 2-5 other places.
      This session: fixed `"version":"` JSON parsing in one function,
      missed 3 identical `contains("\"healthy\"")` patterns 200 lines away.
    - Smoke tests are **inline verification only** -- don't commit them.
      They're a build-time gate, not regression tests.
    - If a smoke test crashes -- fix the bug before proceeding to REVIEW.

    This step exists because of a real incident: `self.config_manager`
    (wrong attribute name) passed 8 pipeline stages undetected because
    it was inside try/except and no test exercised the actual runtime path.

### Resource Lifecycle Verification

Added after run_c2881d2f: 3 CRITICAL subprocess bugs survived 14 green unit tests + 4 smoke tests.

For each new resource acquisition in the changeset, verify BOTH the success path AND the failure/timeout path release the resource:

| Resource Type | Success Check | Failure Check |
|---------------|--------------|---------------|
| subprocess (`create_subprocess_exec`) | exits with returncode | `proc.kill()` + `await proc.wait()` in finally. `wait_for` timeout -- kill before re-raise. `FileNotFoundError` caught. |
| temp files | deleted after use | deleted in finally (`unlink(missing_ok=True)`) |
| MediaStream / hardware | tracks stopped | stopped in useEffect cleanup |
| network / sockets | closed / consumed | timeout set + cleanup on error |
| file handles | closed | context manager or finally |
| SDK handler/client (SocketModeHandler, WebClient, etc.) | `.close()` after use | `.close()` before reassignment AND in error path. Old instance closed before `self._handler = new_handler`. |
| upload form (multipart) | `await form.close()` | in finally block -- releases SpooledTemporaryFile |

For each applicable row, write a smoke test that:
1. Triggers the failure path (mock timeout, FileNotFoundError, etc.)
2. Asserts the resource was released (mock.kill.assert_called, etc.)

Don't skip this for "simple" subprocess calls. Voice Input had 14
green tests yet 3 CRITICAL subprocess bugs because mocks replaced
the entire subprocess lifecycle. The mock proved "if transcription
returns X, endpoint returns X" -- but not "subprocess is killed on
timeout." Test the resource, not the happy path around it.

## Step 5: USER-PATH TRACE -- walk real scenarios through real code

15. For each acceptance criterion, pick **one concrete user action** and
    trace it through the actual production code path -- not tests, not
    mocks, the real call chain.

    For each trace:
    a. **Start from the user action** -- "Titus sends a Slack DM", not
       "\_poll\_channel\_messages is called"
    b. **Follow every function call** -- read the real source, not from
       memory. Note the actual input each function receives.
    c. **Check external data shapes** -- when the code consumes data from
       an external API (Slack, DB, filesystem), verify your test mocks
       match the real response schema. `conversations.history` messages
       lack `channel`; Socket Mode events have it. These differences
       are invisible in unit tests that supply hand-crafted dicts.
    d. **Check cross-component boundaries** -- when one component calls
       another (adapter -- gateway, hook -- registry), trace what happens
       on the OTHER side. Error callbacks, state resets, object
       destruction -- these are where bugs hide.
    e. **Check competing paths** -- if two mechanisms handle the same
       event (e.g., `_on_error` callback AND health monitor both react
       to thread death), which fires first? Does the first one prevent
       the second from ever running?

    f. **Check empty/partial data** -- when the code renders or processes
       a collection (list, grid, table), trace what happens when the
       collection is empty, has 1 item, or has only some optional fields
       populated. For frontend: does the layout collapse gracefully
       (no blank columns, no empty cards)? For backend: does an empty
       list produce `[]` not `null`? Does a missing optional field use
       the default, not crash on `.get()` → `None.something`?

    **Action on findings:**
    - Each finding -- **fix immediately** (these are always real bugs)
    - Update tests to cover the discovered path

    **Why this exists:** run_ec4a73ff shipped with 26 TDD tests, 10/10
    confidence, and 8 pipeline stages passed. User-path trace found 2
    CRITICAL bugs in 5 minutes: (1) `_on_error` fired before health
    monitor -- gateway destroyed adapter -- `_ws_fail_count` reset --
    polling never activated, (2) `conversations.history` messages lack
    `channel` field -- `external_chat_id=""` -- routing broken. Both
    invisible to unit tests because mocks didn't match real API data
    and no test crossed the adapter/gateway boundary. This is LL04's
    third recurrence: engineering-complete != user-complete.

## Step 6: PROBE -- send a real request through the wire (catch format bugs)

16. **Only when the changeset adds a new API endpoint consumed by frontend.**
    Skip for backend-only or frontend-only changes.

    Write ONE integration test per new endpoint that constructs the request
    **the same way the real client would** -- not using TestClient shortcuts
    that bypass serialization.

    ```python
    # BAD -- TestClient auto-serializes, hides Content-Type bugs:
    client.post("/api/chat/transcribe", files={"audio": ...})

    # GOOD -- Construct request the way Axios/fetch would:
    import httpx
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        # FormData without explicit Content-Type (browser auto-adds boundary)
        files = {"audio": ("test.wav", wav_bytes, "audio/wav")}
        resp = await client.post("/api/chat/transcribe", files=files)
        assert resp.status_code == 200
        data = resp.json()
        # Verify response shape matches frontend expectations
        assert "transcript" in data
        assert "duration_ms" in data  # backend snake_case
    ```

    Rules:
    - Use `httpx.AsyncClient(app=app)` -- it exercises the real ASGI stack
      including Starlette's multipart parser, unlike TestClient which
      uses `requests` (different HTTP library, different serialization).
    - Do NOT set `Content-Type` manually -- let the HTTP library handle it.
      This is the exact bug pattern (RP5) we're testing against.
    - Verify the response JSON keys match what the frontend expects (after
      any snake-to-camel conversion).
    - If the endpoint requires auth or external services, mock only the
      leaf (e.g., Amazon Transcribe API), NOT the HTTP layer.

    **Why this exists:** Voice Input's Content-Type bug (explicit header
    broke Axios boundary string) would have been caught by ANY real HTTP
    request. 14 unit tests + 4 smoke tests + integration trace missed it
    because none actually sent a multipart request through the ASGI stack.
    The most fatal bug was the cheapest to catch.

## Pre-Change Checks

### Before adding validation/constraints to existing functions

**BLOCKING: grep all callers before adding input validation.**
```
grep -rn "function_name(" . --include="*.py" --include="*.ts"
```
Every existing call site (including tests) becomes a potential breakage.
Read the actual arguments they pass. Adjust your validation to accept
existing valid inputs. This session: added regex to `render_user_data()`,
broke 3 tests that used `s3_bucket="b"` and `auth_hash="$2a$..."`.

### Before editing a file modified by parallel sessions

**Check recent changes:** `git log -5 --oneline -- <file>`.
If the file was modified by another session since your last read, re-read
the entire file. This session: parallel commit added a stub method,
our commit added the real method — duplicate definition in the same class.

## TDD Constraint

Fix code, not tests. Tests are derived from the accepted design. Changing a test = changing the spec = go back to PLAN.

## Mock Discipline

### Boundary-Only Rule

**Mock at system boundaries ONLY.** Never mock your own code.

| Dependency Category | Example | Test Strategy | Mock? |
|-------------------|---------|---------------|-------|
| **In-process** | Pure computation, in-memory state | Test directly, no mocking | ❌ Never |
| **Local-substitutable** | SQLite, filesystem, Redis | Use test stand-in (tmp dir, in-memory DB) | ❌ Prefer stand-in |
| **Remote-owned** | Your own microservice/API | Port interface + in-memory adapter for tests | ✅ Mock the adapter |
| **True-external** | Stripe, AWS API, GitHub | Mock the leaf SDK call | ✅ Mock at boundary |

**What to mock:** External APIs, databases (when no test DB), time/randomness, filesystem (sometimes).
**What NOT to mock:** Your own classes, internal collaborators, anything you control.

**The test for good mocking:** If you refactor internals and tests break despite behavior being unchanged — your mocks are too deep. Mock the system boundary, not the internal wiring.

### Spec Enforcement

When mocking, use `spec=RealClass` or only set attributes that exist on the real class. Bare `MagicMock()` silently accepts ANY attribute access — this hides `AttributeError` bugs that crash in production.

### Interface-First Testing

Tests verify behavior through **public interfaces**, not implementation details.
A good test reads like a specification: "user can checkout with valid cart" tells
you what capability exists. Tests survive refactors because they don't care about
internal structure.

Red flags for bad tests:
- Mocking internal collaborators (not system boundaries)
- Testing private methods
- Asserting on call counts/order of internal calls
- Test breaks on refactor when behavior hasn't changed
- Verifying through external means (DB query) instead of the interface

## Adversarial Inputs in RED Phase

For NLP/parsing code, the RED phase must include adversarial inputs: URLs, file paths, code snippets, empty/minimal strings, Unicode edge cases (CJK, Kana, Hangul, emoji), and multi-language mix. These are the inputs that break keyword extractors, parsers, and formatters.

## Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type changeset --producer s_autonomous-pipeline \
  --summary "<N> files changed, <M> commits, TDD: <red>/<green>/<verify>" \
  --data '{"branch":"...","commits":[...],"files_changed":[...],"diff_summary":"...","tdd":{"acceptance_criteria_count":N,"tests_generated":M,"red_failures":K,"green_pass":true,"regressions":0,"smoke_tests":S,"smoke_crashes_caught":C,"user_path_traces":T,"user_path_bugs_found":B,"probes":P,"probe_bugs_found":Q}}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state review
```
