# Implementation Plan: Resource Management Hardening

## Overview

Six targeted fixes to the SwarmAI resource management subsystem: CompactionGuard read-only tool classification with higher thresholds, escalation grace periods, comment accuracy fixes, flaky test fix, ResourceExhaustedException SSE handling, and dead code removal. All changes are backward-compatible and localized to the backend.

## Tasks

- [x] 1. Add read-only tool classification and higher consecutive thresholds to CompactionGuard
  - [x] 1.1 Add `_READ_ONLY_TOOLS` frozenset, `_CONSEC_SOFT_READONLY`/`_CONSEC_HARD_READONLY`/`_CONSEC_KILL_READONLY` constants, `_classify_tool()` static method, and `_get_consec_thresholds()` method to `backend/core/compaction_guard.py`
    - `_READ_ONLY_TOOLS` contains exactly: Read, Grep, ListDir, Glob, ReadFile, GrepSearch, ListDirectory, FileSearch, ReadCode, ReadMultipleFiles
    - `_classify_tool(tool_name)` returns `"read_only"` if in set, else `"write_execute"`
    - `_get_consec_thresholds(tool_name)` returns `(5, 8, 10)` for read-only, `(3, 5, 7)` for write-execute
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 1.2 Modify `_is_consecutive_repeat()` to use `_get_consec_thresholds()` based on `self._last_pair[0]` tool name instead of hardcoded `_CONSEC_SOFT`/`_CONSEC_HARD`/`_CONSEC_KILL`
    - Extract `tool_name` from `self._last_pair`, look up thresholds, compare `self._consec_count`
    - Write-execute tools keep existing (3, 5, 7) behavior unchanged
    - _Requirements: 1.5, 1.6_

  - [ ]* 1.3 Write property test: Tool classification correctness (Property 1)
    - **Property 1: Tool classification correctness**
    - For any string `t`, `_classify_tool(t)` returns `"read_only"` iff `t ∈ _READ_ONLY_TOOLS`
    - **Validates: Requirements 1.1, 1.2, 1.3**

  - [ ]* 1.4 Write property test: Threshold selection matches tool classification (Property 2)
    - **Property 2: Threshold selection matches tool classification**
    - For any tool name, `_get_consec_thresholds()` returns `(5, 8, 10)` for read-only, `(3, 5, 7)` for write-execute
    - **Validates: Requirements 1.5, 1.6**

  - [ ]* 1.5 Write unit tests for read-only tool consecutive repeat behavior
    - Test that 4 consecutive identical Read calls → MONITORING (below threshold)
    - Test that 5 consecutive identical Read calls → SOFT_WARN (at threshold)
    - Test that Bash still triggers SOFT_WARN at 3 consecutive calls (backward compat)
    - Test mixed sequences (read-only then write tool resets consecutive count)
    - _Requirements: 1.5, 1.6_

- [x] 2. Add escalation grace period to CompactionGuard
  - [x] 2.1 Add `_GRACE_WINDOW` constant (3), `_grace_calls_remaining` and `_grace_level` fields to `__init__`, and grace logic to `check()` in `backend/core/compaction_guard.py`
    - Grace check at top of `check()`: if `_grace_calls_remaining > 0`, decrement and return current escalation
    - On each escalation event, set `_grace_calls_remaining = _GRACE_WINDOW`
    - KILL always returns KILL regardless of grace state
    - _Requirements: 2.1, 2.2, 2.3, 2.6_

  - [x] 2.2 Update `reset()` and `reset_all()` to clear grace state
    - `reset()`: set `_grace_calls_remaining = 0`
    - `reset_all()`: set `_grace_calls_remaining = 0` and `_grace_level = EscalationLevel.MONITORING`
    - _Requirements: 2.4, 2.5_

  - [ ]* 2.3 Write property test: Grace period prevents escalation (Property 3)
    - **Property 3: Grace period prevents escalation**
    - For any state where `_grace_calls_remaining > 0`, `check()` decrements counter and does not increase escalation
    - **Validates: Requirements 2.1, 2.2**

  - [ ]* 2.4 Write property test: Escalation monotonicity (Property 4)
    - **Property 4: Escalation monotonicity**
    - For any sequence of `check()` calls without `reset()`/`reset_all()`, escalation is monotonically non-decreasing
    - **Validates: Requirements 2.6, 2.7**

  - [ ]* 2.5 Write unit tests for grace period behavior
    - Test grace window prevents escalation for exactly 3 calls after SOFT_WARN
    - Test grace expiry allows next escalation
    - Test `reset()` clears grace counter
    - Test `reset_all()` clears all grace state
    - Test KILL stays KILL regardless of grace
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

- [x] 3. Checkpoint - Verify CompactionGuard changes
  - Ensure all tests pass (`pytest backend/tests/test_compaction_guard.py backend/tests/test_compaction_guard_bugfix.py`), ask the user if questions arise.

- [x] 4. Fix comment accuracy and dead code in ResourceMonitor
  - [x] 4.1 Fix "80%" → "85%" in `spawn_budget()` and `compute_max_tabs()` docstrings in `backend/core/resource_monitor.py`
    - `spawn_budget()` docstring: change "80% rule" to "85% rule"
    - `compute_max_tabs()` docstring: change "80% memory usage" to "85% memory usage"
    - `_MEMORY_THRESHOLD_PCT = 85.0` is already correct — only comments need fixing
    - _Requirements: 3.1, 3.2, 3.3_

  - [x] 4.2 Remove dead code line in `_read_memory_macos_fallback()` in `backend/core/resource_monitor.py`
    - Remove `available = total - used if 'total' in dir() else free + speculative` (the line before the sysctl call)
    - The sysctl call on the next line defines `total`, and subsequent lines compute real `used` and `available`
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [x] 5. Fix flaky test: test_reap_orphans_has_timeout
  - [x] 5.1 Modify `test_reap_orphans_has_timeout` in `backend/tests/test_resource_governance.py` to mock the `_reap_orphans` timeout to 5 seconds
    - Patch the timeout constant used by `_reap_orphans` (e.g., `asyncio.wait_for` timeout) to 5s instead of production 30s
    - Adjust the assertion to `elapsed < 10` (was `< 35`)
    - The hanging `_reap_by_pattern` mock stays — it validates the timeout guard works
    - _Requirements: 4.1, 4.2, 4.3_

- [x] 6. Add ResourceExhaustedException catch in chat.py SSE generator
  - [x] 6.1 Add `except ResourceExhaustedException` block in `message_generator()` in `backend/routers/chat.py`, placed between `except asyncio.TimeoutError` and `except Exception`
    - Import `ResourceExhaustedException` from `core.exceptions`
    - Yield SSE event with `code="RESOURCE_EXHAUSTED"`, `message=e.message`, `suggested_action=e.suggested_action`
    - Use existing `_build_error_event()` helper
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 6.2 Write property test: ResourceExhaustedException SSE event fidelity (Property 5)
    - **Property 5: ResourceExhaustedException SSE event fidelity**
    - For any `ResourceExhaustedException` with arbitrary `message` and `suggested_action`, the SSE event contains `code="RESOURCE_EXHAUSTED"` and includes both fields verbatim
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4**

  - [ ]* 6.3 Write unit test for ResourceExhaustedException SSE handling
    - Mock `run_conversation()` to raise `ResourceExhaustedException`
    - Verify yielded event has `code="RESOURCE_EXHAUSTED"` with correct message and suggested_action
    - Verify non-ResourceExhaustedException still hits generic error classification
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

- [x] 7. Final checkpoint - Ensure all tests pass
  - Run full test suite: `pytest backend/tests/test_compaction_guard.py backend/tests/test_compaction_guard_bugfix.py backend/tests/test_resource_governance.py`
  - Ensure all existing tests in `test_compaction_guard.py` still pass (backward compat for write tools)
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- All property tests use Hypothesis (already in dev dependencies)
- Property tests should be added to `backend/tests/test_compaction_guard.py` (Issues 1-2) and a new test section in the appropriate test file (Issue 5)
- Existing `test_compaction_guard.py` tests must pass without modification — write-execute tool behavior is unchanged
- The 85% threshold is already correct in code (`_MEMORY_THRESHOLD_PCT = 85.0`) — only comments/docstrings are wrong
