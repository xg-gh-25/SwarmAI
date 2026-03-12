# Implementation Plan: Hook Execution Decoupling

## Overview

The `BackgroundHookExecutor` infrastructure is already fully implemented and wired. This plan covers the remaining targeted changes: reducing the drain timeout in `disconnect_all()`, adding CancelledError handling and duration logging in `_run_all_safe()`/`_run_single_safe()`, exposing `pending_hook_tasks` in the `/health` endpoint, and comprehensive property-based + unit tests to validate all 18 correctness properties.

## Tasks

- [x] 1. Add CancelledError handling and duration logging to BackgroundHookExecutor
  - [x] 1.1 Update `_run_all_safe()` in `backend/core/session_hooks.py`
    - Add `time.monotonic()` tracking at the start of the method
    - Add `completed` counter incremented after each successful hook
    - Add inner `except asyncio.CancelledError` in the per-hook loop that logs at INFO and re-raises
    - Add outer `except asyncio.CancelledError` that logs session ID, elapsed time, and completed count, then returns
    - Add final `logger.info` on normal completion with session ID, elapsed time, and hook count
    - _Requirements: 6.3, 7.1, 7.2_

  - [x] 1.2 Update `_run_single_safe()` in `backend/core/session_hooks.py`
    - Add `except asyncio.CancelledError` that logs at INFO level with hook name and session ID, then re-raises
    - _Requirements: 6.3_

  - [ ]* 1.3 Write property test: Hook error isolation (Property 6)
    - **Property 6: Hook error isolation within a task**
    - Generate random sequences of hooks where some raise exceptions, verify all non-failing hooks execute
    - Test file: `backend/tests/test_property_hook_error_isolation.py`
    - **Validates: Requirements 6.1, 6.2**

  - [ ]* 1.4 Write property test: CancelledError handling (Property 7)
    - **Property 7: CancelledError is handled gracefully**
    - Cancel running hook tasks, verify CancelledError is caught and logged at INFO (not ERROR)
    - Test file: `backend/tests/test_property_hook_cancelled_error.py`
    - **Validates: Requirements 6.3**

  - [ ]* 1.5 Write property test: Per-hook timeout enforcement (Property 9)
    - **Property 9: Per-hook timeout is enforced**
    - Generate hooks with random durations, verify hooks exceeding timeout are interrupted and next hook executes
    - Test file: `backend/tests/test_property_hook_timeout.py`
    - **Validates: Requirements 5.2, 6.4**

  - [ ]* 1.6 Write property test: Hook execution order preserved (Property 10)
    - **Property 10: Hook execution order is preserved**
    - Generate random hook lists and skip lists, verify execution order matches registration order minus skipped
    - Test file: `backend/tests/test_property_hook_execution_order.py`
    - **Validates: Requirements 8.1**

- [x] 2. Checkpoint - Verify hook executor changes
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Update `disconnect_all()` in AgentManager
  - [x] 3.1 Reduce drain timeout and add observability logging in `backend/core/agent_manager.py`
    - Change `drain(timeout=8.0)` to `drain(timeout=2.0)` in `disconnect_all()`
    - Add `logger.info` before drain with `pending_count`
    - Add `logger.info` after drain with `(done, cancelled)` counts
    - Add `logger.warning` when `cancelled > 0` noting DA extraction may be lost
    - _Requirements: 3.3, 3.4, 3.5, 7.4_

  - [ ]* 3.2 Write unit test: disconnect_all drain timeout and logging
    - Verify drain is called with `timeout=2.0`
    - Verify pending count is logged before drain
    - Verify (done, cancelled) counts are logged after drain
    - Test file: `backend/tests/test_hook_disconnect_all.py`
    - _Requirements: 3.3, 7.4_

  - [ ]* 3.3 Write property test: drain cancels remaining tasks on timeout (Property 5)
    - **Property 5: drain cancels remaining tasks on timeout**
    - Generate random numbers of slow tasks, verify drain cancels stragglers and returns correct counts
    - Test file: `backend/tests/test_property_hook_drain_cancellation.py`
    - **Validates: Requirements 3.5**

  - [ ]* 3.4 Write property test: disconnect_all cleans up all sessions (Property 4)
    - **Property 4: disconnect_all cleans up all sessions without blocking on hooks**
    - Generate N sessions, call disconnect_all, verify _active_sessions empty, _clients empty, cleanup loop cancelled
    - Test file: `backend/tests/test_property_hook_disconnect_cleanup.py`
    - **Validates: Requirements 3.1, 3.2**

- [x] 4. Update `/health` endpoint in `main.py`
  - [x] 4.1 Add `pending_hook_tasks` field to `/health` response in `backend/main.py`
    - Access `agent_manager.hook_executor.pending_count` (use property directly, not hasattr)
    - Return 0 when `hook_executor` is None
    - _Requirements: 7.3_

  - [x] 4.2 Write unit test: health endpoint includes pending_hook_tasks
    - Verify `pending_hook_tasks` field is present in healthy response
    - Verify field is 0 when no hooks are running
    - Verify field reflects actual pending count when hooks are in flight
    - Test file: `backend/tests/test_hook_health_endpoint.py`
    - _Requirements: 7.3_

- [x] 5. Checkpoint - Verify all code changes
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Property-based tests for pending set tracking and skip semantics
  - [ ]* 6.1 Write property test: Pending task set tracking (Property 8)
    - **Property 8: Pending task set tracks and auto-cleans tasks**
    - Generate random sequences of fire/complete events, verify pending_count is always correct
    - Test file: `backend/tests/test_property_hook_pending_tracking.py`
    - **Validates: Requirements 5.1, 5.3**

  - [ ]* 6.2 Write property test: Skip-if-extracted semantics (Property 11)
    - **Property 11: Skip-if-extracted semantics preserved**
    - Generate random sessions with random activity_extracted flags, verify skip_hooks parameter is correct
    - Test file: `backend/tests/test_property_hook_skip_extracted.py`
    - **Validates: Requirements 8.2, 8.4**

  - [ ]* 6.3 Write property test: Same HookContext identity (Property 12)
    - **Property 12: Same HookContext instance passed to all hooks**
    - Verify all hooks in a single task receive the exact same HookContext object (identity equality)
    - Test file: `backend/tests/test_property_hook_context_identity.py`
    - **Validates: Requirements 8.3**

- [ ] 7. Property-based tests for concurrency and idle loop
  - [ ]* 7.1 Write property test: Git lock serialization (Property 13)
    - **Property 13: Git operations serialized via git_lock**
    - Generate random concurrent hook executions, verify git operations never overlap in time
    - Test file: `backend/tests/test_property_hook_git_serialization.py`
    - **Validates: Requirements 9c.7**

  - [ ]* 7.2 Write property test: Idle thresholds respected (Property 16)
    - **Property 16: Idle thresholds respected**
    - Generate random session idle times, verify extraction triggers only above 30min and cleanup only above 12h
    - Test file: `backend/tests/test_property_hook_idle_thresholds.py`
    - **Validates: Requirements 9f.16, 9f.17**

  - [ ]* 7.3 Write property test: Concurrent tasks for different sessions (Property 15)
    - **Property 15: Concurrent tasks for different sessions**
    - Fire N tasks for different sessions simultaneously, verify pending_count reflects all N
    - Test file: `backend/tests/test_property_hook_concurrent_sessions.py`
    - **Validates: Requirements 9e.14**

- [x] 8. Unit tests for session cleanup and context lifecycle
  - [x] 8.1 Write unit test: HookContext built before session pop (Property 1)
    - Create a session, call `_cleanup_session()`, verify the fired context has correct `agent_id`
    - Test file: `backend/tests/test_hook_session_cleanup.py`
    - **Validates: Requirements 2.1, 9a.2**

  - [x] 8.2 Write unit test: Session cleanup on context build failure (Property 2)
    - Mock `_build_hook_context()` to raise, verify session is still cleaned up and no hook task spawned
    - Test file: `backend/tests/test_hook_session_cleanup.py`
    - **Validates: Requirements 2.5, 9a.3**

  - [x] 8.3 Write unit test: Non-blocking session cleanup (Property 3)
    - Create a slow hook, call `_cleanup_session()`, verify it returns before the hook completes
    - Test file: `backend/tests/test_hook_session_cleanup.py`
    - **Validates: Requirements 2.2, 2.3**

  - [x] 8.4 Write unit test: activity_extracted flag lifecycle (Property 14)
    - Test flag set before spawn, not reset on background failure, reset on new message
    - Test file: `backend/tests/test_hook_session_cleanup.py`
    - **Validates: Requirements 9d.10, 9d.11, 9d.12, 9d.13**

  - [x] 8.5 Write unit test: CancelledError logged at INFO (Property 7)
    - Cancel a running hook task, verify log output contains INFO-level message with session ID
    - Test file: `backend/tests/test_hook_session_cleanup.py`
    - **Validates: Requirements 6.3**

  - [x] 8.6 Write unit test: Drain on empty pending set (edge case)
    - Call `drain()` with no pending tasks, verify returns `(0, 0)` immediately
    - Test file: `backend/tests/test_hook_session_cleanup.py`
    - **Validates: Requirements 3.5**

  - [x] 8.7 Write unit test: Double disconnect_all is a no-op (edge case)
    - Call `disconnect_all()` twice, verify second call completes without error
    - Test file: `backend/tests/test_hook_disconnect_all.py`
    - **Validates: Requirements 3.1**

- [x] 9. Integration tests for end-to-end flows
  - [x] 9.1 Write integration test: Full shutdown flow
    - Create sessions, call `POST /shutdown`, verify response returns and all sessions cleaned up
    - Test file: `backend/tests/test_hook_integration.py`
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4**

  - [x] 9.2 Write integration test: Idle loop extraction fires in background
    - Create a session, advance time past 30min, verify extraction fires via `fire_single()`
    - Test file: `backend/tests/test_hook_integration.py`
    - **Validates: Requirements 4.1, 4.4**

  - [x] 9.3 Write integration test: Concurrent session close with git lock
    - Close 5 sessions simultaneously, verify all hooks complete without git lock contention errors
    - Test file: `backend/tests/test_hook_integration.py`
    - **Validates: Requirements 9c.7, 9e.14**

- [x] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- The core code changes are in tasks 1, 3, and 4 — all other tasks are tests
- Each property test references its design document property number and validated requirements
- All tests use `pytest-asyncio`, `hypothesis` for PBT, and `unittest.mock.AsyncMock` for mocking
- Property-based tests use `@settings(max_examples=100)` per the design's testing strategy
- Test files follow the existing `backend/tests/test_property_*` naming convention for PBT tests
