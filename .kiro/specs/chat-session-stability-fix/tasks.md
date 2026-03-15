# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** — Session State Destroyed Before Auto-Retry
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bugs exist
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the three interacting bugs exist
  - **Scoped PBT Approach**: Scope the property to concrete failing cases for each bug:
    - Bug 1: Simulate a retriable `error_during_execution` (e.g., "exit code: -9") with `_path_a_retry_count=0` and `MAX_RETRY_ATTEMPTS=2`. Assert `_active_sessions[eff_sid]` still exists after the handler runs. On unfixed code, `_cleanup_session` is called before the retry check, so the session is popped.
    - Bug 2: Simulate PATH B streaming completion. Assert `info["last_used"]` is updated after the `_run_query_on_client` yield loop. On unfixed code, `last_used` stays at the value set by `_get_active_client` at request start.
    - Bug 3: Set `_path_a_retry_count=1`, `_path_a_retried=True`, `MAX_RETRY_ATTEMPTS=2`. Evaluate retry eligibility in both the SDK reader error path (`_retry_count < _max_retries` → True) and the `error_during_execution` path (`not _path_a_retried` → False). Assert they agree. On unfixed code, they disagree.
  - Test assertions match Expected Behavior Properties from design (Properties 1, 2, 3)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bugs exist)
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 2.1, 2.2, 2.5, 2.6_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** — Non-Buggy Error Paths Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - **Observe on UNFIXED code**:
    - Non-retriable errors (e.g., auth failure): `_cleanup_session` is called AND error event is yielded
    - Interrupted sessions (`interrupted=True`): cleanup is skipped, error is suppressed
    - Exhausted retries (`_retry_count >= MAX_RETRY_ATTEMPTS`): error event is yielded even for retriable errors
    - Genuinely idle sessions (`time.time() - last_used > SUBPROCESS_IDLE_SECONDS`, no active streaming): subprocess is disconnected
  - Write property-based tests capturing observed behavior:
    - For any error where `_is_retriable_error` returns False, verify `_cleanup_session` is called and error event is yielded
    - For any session with `interrupted=True`, verify cleanup is skipped and error is suppressed
    - For any error where `_retry_count >= MAX_RETRY_ATTEMPTS`, verify error event is yielded
    - For any session idle longer than `SUBPROCESS_IDLE_SECONDS` with no active streaming, verify subprocess disconnect
  - Property-based testing generates many combinations of error strings, retry counts, and session states
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.6, 3.8_

- [x] 3. Implement chat session stability fix

  - [x] 3.1 Change 1: Defer `_cleanup_session` in `error_during_execution` handler (Bug 1 + Bug 3 partial)
    - In `_run_query_on_client`, the `error_during_execution` branch (~line 2960):
    - Move `_will_auto_retry_ede` evaluation BEFORE the `_cleanup_session` call
    - Replace `not session_context.get("_path_a_retried")` with `session_context.get("_path_a_retry_count", 0) < self.MAX_RETRY_ATTEMPTS`
    - Wrap `_cleanup_session` in `if not _will_auto_retry_ede:` guard
    - Non-retriable errors and exhausted retries still call `_cleanup_session` (preservation)
    - _Bug_Condition: isBugCondition(input) where error is retriable AND cleanup called before retry check_
    - _Expected_Behavior: Session entry preserved in `_active_sessions` when retry will be attempted_
    - _Preservation: Non-retriable errors still cleaned up (Req 3.1), exhausted retries still yield error (Req 3.6)_
    - _Requirements: 2.1, 2.6, 3.1, 3.6_

  - [x] 3.2 Change 2: Update `last_used` after PATH B streaming completes (Bug 2)
    - In `_execute_on_session_inner`, after the PATH B `_run_query_on_client` yield loop:
    - Add `_path_b_info = self._active_sessions.get(session_id)` lookup
    - Add `if _path_b_info: _path_b_info["last_used"] = time.time()`
    - Place BEFORE the `if session_context.get("had_error")` check
    - Runs regardless of error state — timestamp reflects last activity
    - _Bug_Condition: isBugCondition(input) where path=="B" AND last_used not updated after streaming_
    - _Expected_Behavior: info["last_used"] updated to time.time() after streaming completes_
    - _Preservation: `_get_active_client` still updates last_used at request start (Req 3.7)_
    - _Requirements: 2.2, 2.3, 2.4, 3.7_

  - [x] 3.3 Change 3: Unify retry-eligibility in `is_error` ResultMessage handler (Bug 3)
    - In `_run_query_on_client`, the `is_error` ResultMessage handler (~line 3085):
    - Replace `not session_context.get("_path_a_retried")` with `session_context.get("_path_a_retry_count", 0) < self.MAX_RETRY_ATTEMPTS`
    - This aligns all three error paths (SDK reader error, `error_during_execution`, `is_error`) to use count-based condition
    - _Bug_Condition: isBugCondition(input) where retry_count < max_retries != (NOT path_a_retried)_
    - _Expected_Behavior: All error paths use identical `_retry_count < MAX_RETRY_ATTEMPTS` condition_
    - _Preservation: Non-retriable SDK reader errors still yield error events immediately (Req 3.8)_
    - _Requirements: 2.5, 3.8_

  - [x] 3.4 Change 4: Update `last_used` during PATH A retry loop (defensive)
    - In `_execute_on_session_inner`, inside the PATH A retry `while` loop, after `session_context["had_error"] = False`:
    - Add lookup of `_early_active_key` from `session_context`
    - Add `_early_info["last_used"] = time.time()` on the early-registered session info
    - Prevents cleanup loop from interfering during retries
    - _Bug_Condition: Defensive — prevents stale last_used during retry backoff_
    - _Expected_Behavior: Early-registered session info timestamp updated each retry iteration_
    - _Requirements: 2.2, 2.3_

  - [x] 3.5 Change 5: Increase `SUBPROCESS_IDLE_SECONDS` from 120 to 300 (Bug 2 mitigation)
    - In `AgentManager` class constants (~line 487):
    - Change `SUBPROCESS_IDLE_SECONDS = 2 * 60` to `SUBPROCESS_IDLE_SECONDS = 5 * 60`
    - Update the comment to explain 5-minute rationale (normal user reading/thinking time)
    - Note that OOM prevention is handled by `MAX_CONCURRENT_SUBPROCESSES` cap, not aggressive idle timeouts
    - _Bug_Condition: Mitigation — 2min too aggressive for normal interaction patterns_
    - _Expected_Behavior: 5-minute idle threshold matches user behavior patterns_
    - _Preservation: Genuinely idle sessions still disconnected (Req 3.3), TTL unchanged (Req 3.4)_
    - _Requirements: 2.7, 3.3, 3.4_

  - [x] 3.6 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** — Session State Preserved for Auto-Retry
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms:
      - Bug 1: `_cleanup_session` is deferred when retry is warranted
      - Bug 2: `last_used` is updated after PATH B streaming
      - Bug 3: All error paths use the same count-based retry condition
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bugs are fixed)
    - _Requirements: 2.1, 2.2, 2.5, 2.6_

  - [x] 3.7 Verify preservation tests still pass
    - **Property 2: Preservation** — Non-Buggy Error Paths Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix:
      - Non-retriable errors still cleaned up and error events yielded
      - Interrupted sessions still preserved
      - Genuinely idle sessions still disconnected
      - Exhausted retries still yield error events

- [x] 4. Checkpoint — Ensure all tests pass
  - Run the full test suite to verify no regressions
  - Ensure bug condition exploration test passes (task 1 test on fixed code)
  - Ensure preservation property tests pass (task 2 tests on fixed code)
  - Ensure existing backend tests pass
  - Ask the user if questions arise
