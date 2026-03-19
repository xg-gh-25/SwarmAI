# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Bug Condition** - Evicted Tab Loses SDK Session ID and Resume Capability
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to the concrete eviction→return flow:
    - Create a SessionUnit, set `_sdk_session_id` to a known value (simulating a prior conversation)
    - Call `kill()` (simulating eviction via `_evict_idle()`)
    - Assert `_sdk_session_id` is preserved after `_cleanup_internal()` runs (will FAIL - it gets cleared to None)
    - Mock `PromptBuilder.build_options()` and call `run_conversation()` on the evicted (COLD) unit
    - Assert `resume_session_id` passed to `build_options()` equals the original SDK session ID (will FAIL - `is_alive` gate returns None)
  - Use Hypothesis to generate arbitrary SDK session ID strings and verify the property holds for all of them
  - `isBugCondition(unit, action)`: `action == "send_message" AND unit.state == COLD AND unit._sdk_session_id_before_eviction IS NOT None`
  - Expected behavior: `_sdk_session_id` survives eviction AND `resume_session_id` is passed unconditionally
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document counterexamples found (e.g., "_sdk_session_id is None after kill(), resume_session_id is None in build_options()")
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Non-Eviction Behavior Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - **Step 1 — Observe on UNFIXED code**:
    - Observe: IDLE unit with alive subprocess reuses it on `send()` without spawning (Req 3.1)
    - Observe: COLD unit with `_sdk_session_id=None` (fresh tab) spawns without `--resume` (Req 3.2)
    - Observe: Non-retriable crash path calls `_cleanup_internal()` which clears `_sdk_session_id` (Req 3.3)
    - Observe: Retry loop captures `resume_session_id = self._sdk_session_id` BEFORE `_cleanup_internal()` (Req 3.4)
    - Observe: `disconnect_all()` kills all alive units and `_cleanup_internal()` clears `_sdk_session_id` (Req 3.5)
  - **Step 2 — Write property-based tests capturing observed behavior**:
    - Property: For all IDLE units with alive subprocesses, `send()` reuses the existing subprocess (no new spawn)
    - Property: For all COLD units with `_sdk_session_id=None`, `resume_session_id` passed to `build_options()` is None
    - Property: For all non-retriable error paths, `_sdk_session_id` is None after cleanup (use `_full_cleanup()` post-fix)
    - Property: For all retry attempts, `resume_session_id` equals the captured value from before cleanup
    - Property: After `disconnect_all()`, all units have `_sdk_session_id=None`
  - Use Hypothesis to generate random SessionUnit states and verify preservation properties
  - `NOT isBugCondition`: unit is alive (reuse), unit is fresh (no prior session), unit hit non-retriable crash, retry loop, shutdown
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix for session eviction context loss

  - [x] 3.1 Preserve `_sdk_session_id` in `_cleanup_internal()` (session_unit.py)
    - Remove `self._sdk_session_id = None` from `_cleanup_internal()` method (line ~949)
    - After fix, `_cleanup_internal()` clears only: `_client`, `_wrapper`, `_interrupted`, `_retry_count`
    - `_sdk_session_id` survives eviction (kill → DEAD → COLD) for resume on return
    - _Bug_Condition: isBugCondition(unit, action) where unit.state == COLD AND _sdk_session_id_before_eviction IS NOT None_
    - _Expected_Behavior: _sdk_session_id persists across _cleanup_internal() calls_
    - _Preservation: Retry loop already captures _sdk_session_id before cleanup (Req 3.4), health_check dead-process detection preserves for potential resume_
    - _Requirements: 2.1_

  - [x] 3.2 Add `_full_cleanup()` method (session_unit.py)
    - Create new method `_full_cleanup()` that calls `_cleanup_internal()` AND sets `self._sdk_session_id = None`
    - This method is for non-retriable crashes where the session should NOT be resumable
    - _Bug_Condition: Differentiates eviction cleanup (preserve) from crash cleanup (clear)_
    - _Expected_Behavior: _full_cleanup() clears ALL state including _sdk_session_id_
    - _Preservation: Non-retriable crash cleanup still clears _sdk_session_id (Req 3.3)_
    - _Requirements: 2.1, 3.3_

  - [x] 3.3 Update non-retriable crash paths in `send()` to use `_full_cleanup()` (session_unit.py)
    - Replace `_cleanup_internal()` with `_full_cleanup()` in three locations:
      - Non-retriable spawn failure (line ~260): `else` branch after `_is_retriable_error` check in initial spawn
      - All-retries-exhausted (line ~330): after the retry while-loop exits
      - Non-retriable streaming error (line ~340): final `else` branch in the error handler
    - Do NOT change `_cleanup_internal()` calls in: retry loop cleanup (uses captured resume_session_id), health_check, kill(), interrupt(), continue_with_answer(), continue_with_permission()
    - _Bug_Condition: Non-retriable errors must still clear _sdk_session_id to prevent stale resume_
    - _Expected_Behavior: Non-retriable paths use _full_cleanup(), retriable/eviction paths use _cleanup_internal()_
    - _Preservation: Non-retriable crash cleanup unchanged (Req 3.3), retry loop resume capture unchanged (Req 3.4)_
    - _Requirements: 2.1, 3.3, 3.4_

  - [x] 3.4 Remove `is_alive` gate on `resume_session_id` in `run_conversation()` (session_router.py)
    - Change line ~222 from: `resume_session_id=unit._sdk_session_id if unit.is_alive else None,`
    - To: `resume_session_id=unit._sdk_session_id,`
    - This ensures evicted (COLD) units with a preserved `_sdk_session_id` pass it for `--resume`
    - Fresh tabs with `_sdk_session_id=None` still pass None (correct behavior)
    - _Bug_Condition: is_alive gate always returns None for COLD units, blocking resume_
    - _Expected_Behavior: resume_session_id=unit._sdk_session_id unconditionally_
    - _Preservation: Fresh tabs still get None (Req 3.2), alive units still get their _sdk_session_id (Req 3.1)_
    - _Requirements: 2.2, 3.1, 3.2_

  - [x] 3.5 Clear `_sdk_session_id` in `disconnect_all()` (session_router.py)
    - After `await unit.kill()` in the disconnect_all loop, add `unit._sdk_session_id = None`
    - Since `kill()` calls `_cleanup_internal()` which no longer clears `_sdk_session_id`, explicit clearing is needed for shutdown
    - _Bug_Condition: Without this, shutdown would leave stale _sdk_session_ids on killed units_
    - _Expected_Behavior: disconnect_all() fully cleans up all units including _sdk_session_id_
    - _Preservation: Shutdown disconnect_all cleanup unchanged (Req 3.5)_
    - _Requirements: 3.5_

  - [x] 3.6 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Evicted Tab Resumes With Context
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied:
      - `_sdk_session_id` survives `kill()` / `_cleanup_internal()`
      - `run_conversation()` passes `_sdk_session_id` as `resume_session_id` for evicted units
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.7 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-Eviction Behavior Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm: alive subprocess reuse unchanged (Req 3.1)
    - Confirm: fresh tab spawning unchanged (Req 3.2)
    - Confirm: non-retriable crash cleanup still clears _sdk_session_id via _full_cleanup (Req 3.3)
    - Confirm: retry loop resume capture unchanged (Req 3.4)
    - Confirm: shutdown disconnect_all cleanup unchanged (Req 3.5)

- [x] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `cd backend && pytest`
  - Ensure all property-based tests pass (bug condition + preservation)
  - Ensure no existing tests are broken by the changes
  - Ensure all tests pass, ask the user if questions arise.
