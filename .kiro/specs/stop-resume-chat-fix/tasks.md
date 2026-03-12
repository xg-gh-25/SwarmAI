# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** — Interrupted sessions trigger cleanup and destroy client
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to the concrete failing case: `interrupt_session()` called → SDK returns `error_during_execution` → assert client preserved in `_active_sessions`
  - **Test file**: `backend/tests/test_stop_resume_bug_condition.py`
  - **Setup**: Mock Claude SDK client, create a session in `_active_sessions` and register client in `_clients`
  - **Bug Condition from design**: `isBugCondition(input)` = `result_message.subtype == 'error_during_execution' AND session_was_interrupted(session_id) AND client_exists_in_active_sessions(session_id)`
  - **Property under test**: For all inputs satisfying the bug condition, the client SHALL remain in `_active_sessions`, no error event SHALL be emitted, and `had_error` SHALL NOT be set
  - **Concrete test cases**:
    - Call `interrupt_session(session_id)`, then feed `ResultMessage(subtype='error_during_execution')` through `_run_query_on_client` — assert client is still in `_active_sessions` (WILL FAIL on unfixed code because `_cleanup_session` destroys it)
    - After interrupt + error_during_execution, assert no SSE error event with code `ERROR_DURING_EXECUTION` was yielded (WILL FAIL on unfixed code)
    - After interrupt + error_during_execution, simulate new message with `is_resuming=True` — assert `_get_active_client()` returns the preserved client (WILL FAIL on unfixed code because client was cleaned up)
    - Simulate SDK reader error (`source="error"`) after interrupt — assert client preserved (WILL FAIL on unfixed code)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bug exists)
  - Document counterexamples found (e.g., "`_cleanup_session` called unconditionally on `error_during_execution`, destroying client even after user interrupt")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** — Genuine errors still cleaned up, non-error flows unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - **Test file**: `backend/tests/test_stop_resume_preservation.py`
  - **Setup**: Mock Claude SDK client, create sessions in `_active_sessions`, register clients in `_clients`
  - **Non-bug condition**: All inputs where `NOT isBugCondition(input)` — i.e., `error_during_execution` without interrupt flag, normal completions, other error subtypes, permission flows
  - **Observation-first steps**:
    - Observe: genuine `error_during_execution` (no interrupt) → `_cleanup_session` called, `had_error = True`, error SSE event emitted
    - Observe: normal `ResultMessage` (no error subtype) → client stored in `_active_sessions`, no error event
    - Observe: `SESSION_BUSY` rejection when lock genuinely held → 409 response with SESSION_BUSY code
    - Observe: `interrupt_session()` with no client in `_clients` → returns `{"success": False}`
  - **Property-based tests**:
    - For all `(interrupted=False, subtype='error_during_execution')` inputs: `_cleanup_session` is called, `had_error` is set, error event emitted (matches unfixed behavior)
    - For all `(interrupted=False, subtype != 'error_during_execution')` inputs: no cleanup triggered by error handler (matches unfixed behavior)
    - For all normal completion inputs: client preserved in `_active_sessions`, no error event (matches unfixed behavior)
    - For random `(interrupted: bool, error_subtype: str)` pairs: cleanup happens iff `NOT interrupted AND error_subtype == 'error_during_execution'`
  - **Concrete preservation tests**:
    - Genuine auth failure → `_cleanup_session` called, error event emitted
    - Genuine subprocess crash → `_cleanup_session` called, error event emitted
    - `interrupt_session()` with missing client → returns `{"success": False}` without side effects
    - `_execute_on_session_inner` except block with `interrupted=False` → `_cleanup_session` called, error event emitted
  - Verify all tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 3. Implement the stop/resume fix

  - [x] 3.1 Implement Change 1 — Set interrupted flag in `interrupt_session()`
    - In `backend/core/agent_manager.py`, modify `interrupt_session()` to set `interrupted = True` on the matching `_active_sessions` entry BEFORE calling `client.interrupt()`
    - Use client-reference matching: iterate `_active_sessions` and match by `info.get("client") is client` to avoid key mismatch between `_clients` and `_active_sessions` (Finding 1)
    - Add logging: `logger.info(f"Set interrupted flag on _active_sessions[{sid}]")`
    - _Bug_Condition: isBugCondition(input) where interrupt_session() was called and error_during_execution follows_
    - _Expected_Behavior: interrupted flag is set on the correct _active_sessions entry before SDK interrupt call_
    - _Preservation: interrupt_session() with no client still returns {"success": False} without side effects_
    - _Requirements: 2.1_

  - [x] 3.2 Implement Change 2 — Check interrupted flag in `error_during_execution` handler
    - In `_run_query_on_client`, modify the `error_during_execution` branch to check `session_info.get("interrupted")`
    - If interrupted: skip `_cleanup_session()`, skip `had_error = True`, skip TSCC `set_lifecycle_state("failed")` (Finding 3), skip error event emission, save partial assistant content, clear the interrupted flag
    - If NOT interrupted: existing behavior unchanged (set `had_error`, cleanup, TSCC "failed", error event)
    - _Bug_Condition: error_during_execution with interrupted=True → preserve client, suppress error_
    - _Expected_Behavior: client remains in _active_sessions, no error event emitted, had_error not set_
    - _Preservation: error_during_execution with interrupted=False → unchanged cleanup + error event_
    - _Requirements: 2.1, 2.3, 3.1_

  - [x] 3.3 Implement Change 3 — Clear stale interrupted flag at start of `_run_query_on_client`
    - At the top of `_run_query_on_client`, compute `eff_sid` and pop any stale `interrupted` flag from `_active_sessions[eff_sid]`
    - Add comment explaining timing: flag cleared here, re-set by `interrupt_session()` during streaming, checked by error handler (Finding 4)
    - _Bug_Condition: stale interrupted flag from previous turn must not leak into current turn_
    - _Expected_Behavior: each query starts with a clean interrupted state_
    - _Preservation: no behavioral change for non-interrupt flows_
    - _Requirements: 2.1_

  - [x] 3.4 Implement Change 4 — Check interrupted flag in `source="error"` handler
    - In the `combined_queue` loop in `_run_query_on_client`, modify the `source="error"` branch to check the interrupted flag
    - If interrupted: log as user stop, clear flag, save partial content, `break` out of loop cleanly
    - If NOT interrupted: existing error handling unchanged (`had_error = True`, error logging)
    - _Bug_Condition: SDK reader error after interrupt → treat as user stop, not fatal error_
    - _Expected_Behavior: client preserved, partial content saved, loop exits cleanly_
    - _Preservation: genuine SDK reader errors still set had_error and log error_
    - _Requirements: 2.1, 2.3, 3.1_

  - [x] 3.5 Implement Change 5b — Check interrupted flag in `_execute_on_session_inner` except block
    - In the except block of `_execute_on_session_inner`, check `session_info.get("interrupted")` before calling `_cleanup_session()`
    - If interrupted: log, clear flag, skip cleanup and error event emission
    - If NOT interrupted: existing behavior unchanged (cleanup + error event)
    - This addresses Finding 2 — exceptions propagating from `_run_query_on_client` after interrupt must not destroy the session
    - _Bug_Condition: exception propagates from _run_query_on_client after interrupt_
    - _Expected_Behavior: session preserved for reuse, no error event_
    - _Preservation: genuine exceptions still trigger cleanup and error event_
    - _Requirements: 2.1, 2.3, 3.1_

  - [x] 3.6 Implement Change 6 — Soften frontend stop indicator
    - In `desktop/src/pages/ChatPage.tsx`, replace both occurrences of `{ type: 'text' as const, text: '⏹️ Generation stopped by user.' }` with `{ type: 'text' as const, text: '\n\n---\n*Stopped*' }`
    - This is a transient UI indicator only — not persisted to DB (Finding 5)
    - _Expected_Behavior: subtle "Stopped" indicator instead of jarring text block_
    - _Preservation: stop functionality unchanged, only visual presentation differs_
    - _Requirements: 2.5_

  - [x] 3.7 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** — Interrupted sessions preserve client
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior (client preserved, no error event, no had_error)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run: `cd backend && pytest tests/test_stop_resume_bug_condition.py -v`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.8 Verify preservation tests still pass
    - **Property 2: Preservation** — Genuine errors still cleaned up, non-error flows unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run: `cd backend && pytest tests/test_stop_resume_preservation.py -v`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all preservation tests still pass after fix (no regressions)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 4. Checkpoint — Ensure all tests pass
  - Run full backend test suite: `cd backend && pytest -v`
  - Run frontend tests: `cd desktop && npm test -- --run`
  - Ensure all tests pass, ask the user if questions arise
  - Verify no regressions in existing test suite
  - Consult steering files: `session-identity-and-backend-isolation.md`, `multi-tab-isolation-principles.md`
