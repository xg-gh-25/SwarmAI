# BUILD ref — SMOKE, Resource Lifecycle, user-path trace, wire PROBE

Read when any modified file has new branches, acquires a resource, a frontend change
touches tabs/geometry/entries, or the changeset adds an endpoint the frontend consumes.
REFLECT adds new rows to the Resource Lifecycle table here (old "BUILD Step 4").
`RUN` = `perl -e 'alarm 110; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R>`.

## Step 4: SMOKE — exercise new code paths (catch runtime crashes)

14. For each modified file with new branches (if/else, try/except, config-gated paths),
    write a minimal inline smoke that forces execution through the new path. Goal: the
    runtime crashes unit tests miss because they mock the context (AttributeError/
    NameError in Python, nil-panics in Go, unwrap panics in Rust, NPEs in Java…).

    > **Stack note:** the snippets here (Python imports, `vitest`/`jsdom`, React/Tauri)
    > are SwarmAI's own stack, shown as illustration. The technique (force-execute new
    > branches in-process before trusting a slow subprocess/e2e) is language-neutral:
    > translate from TECH.md `## Stack` (Python import smoke → Go `go build ./... && go run`,
    > Rust `cargo check`, TS `tsc --noEmit` + node import, Java compile + main). Do NOT skip
    > the technique because the example is in another language. During a run the compile
    > or typecheck steps go through `RUN --mode probe --name smoke-<x> -- <argv>`
    > (`pipeline_test_gate` denies a raw `tsc`).

    Example (SwarmAI-self), new config-gated code in prompt_builder.py, run as
    `backend/.venv/bin/python -c "..."` from `backend/`: `from core.prompt_builder import
    PromptBuilder; pb = PromptBuilder(config={"memory_progressive_disclosure": True})`,
    then call the method holding the new branch; don't assert output, just verify it
    doesn't crash with AttributeError/TypeError.

Rules:
- **Integration import smoke (BLOCKING for handler/subprocess code):** When your changeset
  adds a new handler/script that will run as a subprocess (type="script" jobs, CLI tools),
  don't rely on the 30s+ subprocess invocation to find AttributeError. Instead, SMOKE it
  in-process with a direct import + call: e.g. `from jobs.handlers.code_intel_reindex
  import reindex_projects; reindex_projects(full=False)` hits the real GraphStore API in
  ~1 s instead of ~38 s; an AttributeError means the API assumption was wrong. Mock-based
  TDD CONFIRMS your assumptions; import smoke VERIFIES them against reality (wrong method
  name, wrong return type, missing attribute). <!-- R-build-011 -->
- Behind a config flag → smoke with flag=True. Behind a conditional (channel_context,
  resume…) → construct the triggering condition.
- Inside try/except → temporarily remove the except to verify the try body doesn't crash
  (the except silently swallows bugs like `self.config_manager` vs `self._config`).
- **Multi-context callers:** code called from both sync AND async contexts (e.g.
  `record_token_usage()` from async `session_unit` AND sync `bedrock.invoke()`) → smoke
  BOTH contexts. Working in async FastAPI does not mean working from a sync background job.
- **Cross-language boundaries:** a changeset spanning languages (Python + Rust +
  TypeScript) → smoke the **data format at each boundary**: produce the real serialized
  output on the sender side and verify the receiver parses it. `json.dumps({"version":
  "1.8.4"})` emits `"version": "1.8.4"` (with a space); verify the Rust parser handles that
  exact string, not a hand-crafted compact version.
- **Pattern grep after any bug fix:** after fixing a bug in SMOKE or USER-PATH TRACE,
  immediately grep the **entire codebase** for the same pattern
  (`grep -rn "the_broken_pattern" . --include="*.py" --include="*.rs" --include="*.ts"`).
  It likely exists in 2-5 other places.
- **FRONTEND SMOKE (the pipeline is weaker here; this is the counterweight):** the Python
  import smoke does NOT transfer to a React/Tauri change. `vitest`+`jsdom` prove
  STRUCTURE/LOGIC only (jsdom returns 0-rects, has no ResizeObserver, runs no CSS), so for
  a frontend changeset unit-green is NOT a smoke pass. Three parts, gated on what the
  change touches (REVIEW_PATTERNS RP57-60 + TECH.md "Frontend Fragile Zones"):
  1. **Multi-tab isolation (RP57):** new per-tab state → open tab A, set the value, open
     tab B, confirm B is independent, switch back, confirm A restored, close, confirm
     cleared. jsdom cannot fake keep-mounted multi-tab: real machine or store-level test.
  2. **Geometry/scroll/animation (RP59/RP60):** layout, scroll, overflow, drag-drop or a
     CSS transition → build the frontend, relaunch the .app and exercise it (open
     Canvas/overlay, resize the window, drag the radar, switch tabs, watch a stream land at
     the bottom). The build is `cd desktop && npm run build:all`; during a run
     `pipeline_test_gate` denies it raw, so wrap it:
     `perl -e 'alarm 590; exec @ARGV' backend/.venv/bin/python backend/scripts/pipeline_test.py --project <P> --run-id <R> --mode probe --name frontend-build --deadline 575 -- <wrapper>` with the Bash tool timeout 600000 (a build probe cannot be split across calls)
     where `<wrapper>` runs the build and prints `{"findings": [...]}` (one finding per
     error). A daemon-only restart does NOT embed frontend changes.
  3. **Every ENTRY, not just the one you designed (RP60):** enumerate every way a user
     reaches the feature (each drop source, nav path, trigger) and exercise EACH; an "E2E
     verified" that traces only your happy path is not E2E. For a drop/paste/import
     surface test the APP-NATIVE gesture (drag from the Explorer = `application/json`),
     not just OS files (RP56).
  Pure logic (a util, a reducer, no geometry/tabs/entries) → the unit smoke suffices;
  don't build:all for a pure function.
- Smoke tests are **inline verification only**: don't commit them. They are a build-time
  gate, not regression tests.
- A smoke test crashes → fix the bug before proceeding to REVIEW.

## Resource Lifecycle verification

For each new resource acquisition in the changeset, verify BOTH the success path AND the
failure/timeout path release the resource:

| Resource Type | Success Check | Failure Check |
|---------------|--------------|---------------|
| subprocess (`create_subprocess_exec`) | exits with returncode | `proc.kill()` + `await proc.wait()` in finally. `wait_for` timeout → kill before re-raise. `FileNotFoundError` caught. |
| temp files | deleted after use | deleted in finally (`unlink(missing_ok=True)`) |
| MediaStream / hardware | tracks stopped | stopped in useEffect cleanup |
| network / sockets | closed / consumed | timeout set + cleanup on error |
| file handles | closed | context manager or finally |
| SDK handler/client (SocketModeHandler, WebClient, etc.) | `.close()` after use | `.close()` before reassignment AND in error path. Old instance closed before `self._handler = new_handler`. |
| upload form (multipart) | `await form.close()` | in finally block (releases SpooledTemporaryFile) |

For each applicable row, write a smoke test that:
1. triggers the failure path (mock timeout, FileNotFoundError, etc.), and
2. asserts the resource was released (`mock.kill.assert_called`, etc.).

Don't skip this for "simple" subprocess calls: mocks that replace the whole subprocess
lifecycle prove "if transcription returns X, the endpoint returns X", not "the subprocess
is killed on timeout". Test the resource, not the happy path around it.

## Step 5: USER-PATH TRACE — walk real scenarios through real code

15. For each AC, pick **one concrete user action** and trace it through the actual
    production call chain (not tests, not mocks):
    a. **Start from the user action** ("Titus sends a Slack DM", not
       "`_poll_channel_messages` is called").
    b. **Follow every function call** in the real source, not from memory; note the
       actual input each function receives.
    c. **Check external data shapes:** when the code consumes external API data (Slack,
       DB, filesystem), verify test mocks match the real response schema
       (`conversations.history` messages lack `channel`; Socket Mode events have it).
    d. **Check cross-component boundaries:** when one component calls another (adapter →
       gateway, hook → registry), trace the OTHER side: error callbacks, state resets,
       object destruction.
    e. **Check competing paths:** if two mechanisms handle the same event (an `_on_error`
       callback AND a health monitor both react to thread death), which fires first, and
       does it prevent the second from ever running?
    f. **Check empty/partial data:** a rendered/processed collection with 0 items, 1 item,
       or only some optional fields. Frontend: does the layout collapse gracefully (no
       blank columns, no empty cards)? Backend: empty list → `[]` not `null`; a missing
       optional field uses the default instead of crashing on `None.something`.

    Each finding → **fix immediately** (always a real bug) and add a test for the path.
    Engineering-complete != user-complete.

## Step 6: PROBE — send a real request through the wire (catch format bugs)

16. **Only when the changeset adds a new API endpoint consumed by the frontend.** Skip for
    backend-only or frontend-only changes. Write ONE integration test per new endpoint
    that builds the request **the way the real client would**, not via TestClient
    shortcuts that bypass serialization; run it in the loop with `RUN --mode tdd --files`.

```python
# BAD — TestClient auto-serializes, hides Content-Type bugs:
client.post("/api/chat/transcribe", files={"audio": ...})
# GOOD — construct the request the way Axios/fetch would:
async with httpx.AsyncClient(app=app, base_url="http://test") as client:
    files = {"audio": ("test.wav", wav_bytes, "audio/wav")}  # no explicit Content-Type
    resp = await client.post("/api/chat/transcribe", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert "transcript" in data and "duration_ms" in data  # backend snake_case
```

Rules:
- Use `httpx.AsyncClient(app=app)`: it exercises the real ASGI stack including
  Starlette's multipart parser, unlike TestClient (`requests`, different serialization).
- Do NOT set `Content-Type` manually; let the HTTP library handle it (RP5).
- Verify the response JSON keys match what the frontend expects (after snake→camel).
- Endpoint needs auth or external services → mock only the leaf (e.g. the Amazon
  Transcribe API), NOT the HTTP layer.
