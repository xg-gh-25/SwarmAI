# Implementation Plan: Process Lifecycle Watchdog

## Overview

All changes go into `backend/core/lifecycle_manager.py` (implementation) and a new `backend/tests/test_lifecycle_watchdog.py` (tests). The plan adds tracked PID state, a shutdown kill method, and extends the startup orphan reaper to cover pytest — then validates each piece with property-based and unit tests.

## Tasks

- [ ] 1. Add tracked PID state and `track_pid()` method
  - [ ] 1.1 Add `_tracked_child_pids: set[int]` to `LifecycleManager.__init__()`
    - Initialize an empty `set[int]` alongside existing instance attributes
    - _Requirements: 1.1_
  - [ ] 1.2 Add `track_pid(pid: int)` method to `LifecycleManager`
    - Add the PID to `_tracked_child_pids` and log at debug level
    - _Requirements: 1.3, 1.4_
  - [ ] 1.3 Write property test: track_pid set membership (Property 1)
    - **Property 1: track_pid set membership**
    - Generate random integers via Hypothesis, call `track_pid()`, assert membership and idempotence (calling twice with same PID doesn't duplicate)
    - **Validates: Requirements 1.1, 1.3**
  - [ ] 1.4 Write unit test: `test_tracked_pids_empty_on_init`
    - Verify `_tracked_child_pids` is an empty set after `__init__()`
    - _Requirements: 1.1_

- [ ] 2. Implement `_kill_tracked_pids()` and wire into `stop()`
  - [ ] 2.1 Add async `_kill_tracked_pids()` method to `LifecycleManager`
    - Iterate over `list(self._tracked_child_pids)`, send SIGKILL to each, catch `ProcessLookupError`/`PermissionError` silently, log unexpected exceptions at debug, clear the set, log kill count at info
    - _Requirements: 3.1, 3.2, 3.3, 3.4_
  - [ ] 2.2 Extend `stop()` to call `await self._kill_tracked_pids()` after hook drain
    - Insert the call between the existing hook drain and the final log line
    - _Requirements: 3.1_
  - [ ] 2.3 Write property test: _kill_tracked_pids resilience (Property 2)
    - **Property 2: _kill_tracked_pids resilience**
    - Generate random sets of PIDs, mock `os.kill` to raise various exceptions (ProcessLookupError, PermissionError, OSError), assert no exception raised and set is empty afterward
    - **Validates: Requirements 3.2, 3.3, 4.4**
  - [ ] 2.4 Write property test: _kill_tracked_pids completeness (Property 3)
    - **Property 3: _kill_tracked_pids completeness**
    - Generate random non-empty sets of PIDs, mock `os.kill`, assert `os.kill` was called with `signal.SIGKILL` for every PID in the original set regardless of earlier failures
    - **Validates: Requirements 3.1**
  - [ ] 2.5 Write unit test: `test_kill_tracked_pids_noop_when_empty`
    - Verify `_kill_tracked_pids()` does nothing and doesn't raise when set is empty
    - _Requirements: 3.2_

- [ ] 3. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Extend `_reap_orphans()` with pytest orphan reaper
  - [ ] 4.1 Add pytest orphan reaping section to `_reap_orphans()`
    - After the existing dev backend reaper block, add a third section that: runs `pgrep -f pytest`, filters out own PID, checks ppid=1 via `ps -o ppid= -p <pid>`, sends SIGKILL only to orphaned pytest, logs each kill and summary count, wraps everything in try/except (non-fatal)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 4.1, 4.4_
  - [ ] 4.2 Write property test: ppid=1 orphan guard (Property 4)
    - **Property 4: ppid=1 orphan guard**
    - Generate random lists of (pid, ppid) pairs via Hypothesis, mock `pgrep` and `ps` output, run the orphan reaper logic, assert `os.kill` was called only for PIDs with ppid=1
    - **Validates: Requirements 2.2**
  - [ ] 4.3 Write property test: pytest pattern coverage (Property 5)
    - **Property 5: pytest pattern coverage**
    - Mock pgrep output to include PIDs from both `pytest ...` and `python -m pytest ...` invocations (all with ppid=1), verify both are killed by the reaper
    - **Validates: Requirements 2.1, 2.4**
  - [ ] 4.4 Write unit tests for orphan reaper edge cases
    - `test_reap_orphans_skips_own_pid`: Verify `_reap_orphans()` never kills `os.getpid()`
    - `test_reap_orphans_continues_on_pgrep_failure`: Verify startup completes when `pgrep` raises `FileNotFoundError`
    - _Requirements: 2.3, 4.1, 4.4_

- [ ] 5. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks are all required — no optional items
- All implementation goes into `backend/core/lifecycle_manager.py` — no new production files
- Test file: `backend/tests/test_lifecycle_watchdog.py`
- Property tests use Hypothesis (already configured with 5s deadline in conftest.py)
- Every cleanup path is best-effort: errors caught and logged, never propagated
