# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - Window Close/Exit Handlers Skip Graceful Shutdown
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to the three concrete failing handler paths in `lib.rs`:
    - `WindowEvent::Destroyed` handler calls `kill_process_tree(pid)` without calling `send_shutdown_request(port)` first
    - `RunEvent::Exit` handler calls `kill_process_tree(pid)` without calling `send_shutdown_request(port)` first
    - `RunEvent::ExitRequested` handler calls `kill_process_tree(pid)` without calling `send_shutdown_request(port)` first
  - **Verification method**: Static code analysis test (Rust source inspection)
    - Parse/grep `desktop/src-tauri/src/lib.rs` to extract the three handler code blocks
    - For each handler, assert that `send_shutdown_request` appears BEFORE `kill_process_tree` in the handler body
    - For each handler, assert that `std::thread::sleep` or `thread::sleep` appears between `send_shutdown_request` and `kill_process_tree`
    - For each handler, assert that `backend.port` or `port` is captured from the locked state
    - Verify a shared helper function exists (DRY: all three handlers should call the same function)
    - Verify the helper sets `backend.running = false` under lock before the shutdown request (double-fire protection, Property 3)
  - **Test file**: `desktop/src-tauri/tests/test_graceful_shutdown_handlers.py` (Python script that inspects Rust source)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists: `send_shutdown_request` is absent from all three handlers)
  - Document counterexamples found (e.g., "WindowEvent::Destroyed handler has no send_shutdown_request call", "no sleep between shutdown request and kill")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - stop_backend Pattern, Platform Implementations, and Backend-Not-Running Behavior
  - **IMPORTANT**: Follow observation-first methodology
  - **Test file**: `desktop/src-tauri/tests/test_graceful_shutdown_handlers.py` (same file, separate test class)
  - **Observation phase** (run on UNFIXED code to capture baseline):
    - Observe: `stop_backend` function contains `send_shutdown_request(port)` → `tokio::time::sleep(Duration::from_secs(2))` → `kill_process_tree(pid)` → `child.kill()` sequence
    - Observe: `send_shutdown_request` function body uses `curl` on Unix and `PowerShell` on Windows (platform-specific `#[cfg]` blocks)
    - Observe: `kill_process_tree` function body is platform-specific (`#[cfg]` blocks for Unix/Windows)
    - Observe: all three handlers call `child.kill()` as fallback after `kill_process_tree`
    - Observe: all three handlers set `backend.running = false` and `backend.pid = None`
  - **Property-based tests** (source code inspection):
    - For `stop_backend`: assert function body contains `send_shutdown_request`, `tokio::time::sleep`, `kill_process_tree`, and `child.kill()` in that order
    - For `send_shutdown_request`: assert Unix impl contains `curl` and Windows impl contains `powershell` (platform implementations unchanged)
    - For `kill_process_tree`: assert function signature and body are unchanged (capture baseline hash or key patterns)
    - For all three handlers: assert `child.kill()` call is present (defense-in-depth preserved)
    - For all three handlers: assert `backend.running = false` and `backend.pid = None` are set
  - Verify tests PASS on UNFIXED code (these behaviors are correct in existing code)
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 3. Fix graceful shutdown in window close/exit handlers

  - [x] 3.1 Extract `graceful_shutdown_and_kill` helper function
    - Create a new function `fn graceful_shutdown_and_kill(state: SharedBackendState, context: &str)` in `lib.rs`
    - The function encapsulates the full shutdown sequence: lock state → capture `was_running`/`port`/`pid`/`child` → set `running=false`, `pid=None` → drop lock → if `was_running`: `send_shutdown_request(port)` + `std::thread::sleep(3s)` → `kill_process_tree(pid)` → `child.kill()`
    - The `was_running` guard serves double duty: it gates the shutdown request AND provides double-fire protection (Property 3)
    - The `context` parameter is for logging (e.g., "window_destroy", "exit", "exit_requested")
    - Add `println!` log line before `send_shutdown_request` for observability: `"[{context}] Attempting graceful shutdown on port {port}"`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.4_

  - [x] 3.2 Refactor `WindowEvent::Destroyed` handler to call helper
    - Replace the existing `block_on` async block with a single call to `graceful_shutdown_and_kill(state.inner().clone(), "window_destroy")`
    - _Requirements: 1.1, 2.1, 2.4, 2.5, 2.6, 3.4_

  - [x] 3.3 Refactor `RunEvent::Exit` handler to call helper
    - Replace the existing `block_on` async block with a single call to `graceful_shutdown_and_kill(state.inner().clone(), "exit")`
    - _Requirements: 1.2, 2.2, 2.4, 2.5, 2.6, 3.4_

  - [x] 3.4 Refactor `RunEvent::ExitRequested` handler to call helper
    - Replace the existing `block_on` async block with a single call to `graceful_shutdown_and_kill(state.inner().clone(), "exit_requested")`
    - Preserve `let _ = api;` line to allow default exit behavior
    - _Requirements: 1.3, 2.3, 2.4, 2.5, 2.6, 3.4_

  - [x] 3.5 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Window Close/Exit Handlers Include Graceful Shutdown
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior (send_shutdown_request before kill_process_tree with sleep)
    - When this test passes, it confirms all three handlers now include the graceful shutdown sequence
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 3.6 Verify preservation tests still pass
    - **Property 2: Preservation** - stop_backend Pattern, Platform Implementations, and Backend-Not-Running Behavior
    - **Property 3: Idempotency** - Double-Fire Protection via was_running guard
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm `stop_backend` unchanged, platform implementations unchanged, `child.kill()` preserved, state cleanup preserved
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `cd desktop/src-tauri && python tests/test_graceful_shutdown_handlers.py -v`
  - Verify all exploration tests (Property 1) pass — confirms bug is fixed in all three handlers
  - Verify all preservation tests (Property 2) pass — confirms no regressions to stop_backend or platform implementations
  - Manually verify: close app via window close button → check backend logs for `POST /shutdown` received
  - Manually verify: quit app via Cmd+Q → check backend logs for `POST /shutdown` received
  - Manually verify: use stop_backend from UI → confirm it still works identically
  - Ensure no new shared mutable state was introduced
  - Ask the user if questions arise
