# Implementation Plan

- [x] 1. Write bug condition exploration tests
  - **Property 1: Bug Condition** - Process Resource Management Bugs
  - **CRITICAL**: These tests MUST FAIL on unfixed code — failure confirms the bugs exist
  - **DO NOT attempt to fix the tests or the code when they fail**
  - **NOTE**: These tests encode the expected behavior — they will validate the fixes when they pass after implementation
  - **GOAL**: Surface counterexamples that demonstrate each bug exists
  - **Test C1 — vm_stat memory inflation**: Mock `vm_stat` output with known page counts (free=200MB, inactive=9000MB, speculative=50MB, page_size=16384). Assert `_read_memory_macos_fallback()` returns available ≈ 250MB (not ~9250MB).
  - **Test C1b — compute_max_tabs accuracy**: With mocked vm_stat returning the above values, assert `compute_max_tabs()` returns 1 (not 4).
  - **Test C2 — wrapper FD leak on crash path**: Create a SessionUnit with a mock async wrapper, call `_crash_to_cold()`, assert `wrapper.__aexit__()` was called.
  - **Test C3 — slot race condition**: Launch 3 concurrent `_acquire_slot()` calls with `max_tabs=2` and `alive_count=1`. Assert at most 2 return "ready".
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 1.7_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Existing Behavior Unchanged
  - **Test P1 — psutil path preservation** (Req 3.1)
  - **Test P2 — fallback failure preservation** (Req 3.2)
  - **Test P3 — compute_max_tabs formula preservation** — Use Hypothesis (Req 3.3)
  - **Test P4 — explicit kill() path preservation** (Req 3.4, 3.5)
  - **Test P5 — alive unit fast path** (Req 3.6)
  - **Test P6 — queue timeout** (Req 3.7)
  - **Test P7 — state change signaling** (Req 3.8)
  - **Test P8 — PGID-based killpg** (Req 3.9)
  - **Test P9 — error handling on dead process** (Req 3.10)
  - **Test P10 — existing reaper patterns** (Req 3.11, 3.12)
  - _Requirements: 3.1–3.14_

- [x] 3. Fix vm_stat formula in resource_monitor.py
  - [x] 3.1 Remove "inactive" from available memory calculation and add observability logging
    - Change `available = free + inactive + speculative` to `available = free + speculative`
    - Add `logger.info("compute_max_tabs: available=%.0fMB raw=%d result=%d pressure=%s", ...)`
    - _Requirements: 2.1, 2.2, 2.3_

- [x] 4. Fix wrapper leak and child kill in session_unit.py
  - [x] 4.1 Add `_crash_to_cold_async()` and DELETE sync `_crash_to_cold()`
    - Create async version: `_transition(DEAD)` → `await _force_kill()` → `_cleanup_internal()` → `_transition(COLD)`
    - Delete sync `_crash_to_cold()` entirely — no sync callers should remain
    - Make `force_unstick_streaming()` async (caller `_check_streaming_timeout` is already async)
    - Make `health_check()` use `await _crash_to_cold_async()` (already async method)
    - _Requirements: 2.4, 2.5_

  - [x] 4.2 Switch ALL callers from `_crash_to_cold()` to `await _crash_to_cold_async()`
    - 10 call sites in `send()` error paths
    - 1 call site in `force_unstick_streaming()` (now async)
    - 1 call site in `health_check()` (already async)
    - Verify zero remaining references to sync `_crash_to_cold`
    - _Requirements: 2.4, 2.5_

  - [x] 4.3 Add two-pass MCP child kill to `_force_kill()` shared-PGID branch
    - Pass 1: `pgrep -P <pid>` → SIGKILL each child
    - SIGKILL parent PID
    - Pass 2: `pgrep -P <pid>` again → SIGKILL stragglers (closes TOCTOU race)
    - Add `__aexit__` timeout: `asyncio.wait_for(wrapper.__aexit__(...), timeout=5.0)`
    - _Requirements: 2.8_

- [x] 5. Fix slot race condition in session_router.py
  - [x] 5.1 Add `asyncio.Lock` and deadline-based timeout to `_acquire_slot()`
    - Add `self._slot_lock = asyncio.Lock()` to `__init__()`
    - Wrap check-then-act in `async with self._slot_lock:`
    - Alive-unit fast path (`is_alive`) stays OUTSIDE the lock
    - Compute `deadline = time.monotonic() + QUEUE_TIMEOUT` once at entry
    - Re-check loop after wake uses `remaining = deadline - time.monotonic()` as timeout
    - _Requirements: 2.6, 2.7_

- [x] 6. Add dynamic MCP patterns to orphan reaper + stale COLD purge in lifecycle_manager.py
  - [x] 6.1 Read MCP server names from config and add to `_reap_orphans()`
    - Read `mcp-dev.json` to extract command basenames dynamically
    - Fall back to static list `["builder-mcp", "aws-sentral-mcp", "aws-outlook-mcp", "slack-mcp", "taskei-p-mcp"]` on config read failure
    - All MCP patterns use `require_orphaned=True`
    - _Requirements: 2.9_

  - [x] 6.2 Add `_purge_stale_cold()` to maintenance loop
    - Remove COLD units from `router._units` that have been idle > 1 hour
    - Run every 10th cycle (same as orphan reaper)
    - _Requirements: Bug #12 — dead unit dict leak_

- [x] 7. Wire lifecycle manager + SSE disconnect detection
  - [x] 7.1 Add `start_lifecycle()` / `stop_lifecycle()` public API to session_registry.py
    - Add two public async methods wrapping `lifecycle_manager.start()` and `lifecycle_manager.stop()`
    - _Requirements: 2.10, 2.11_

  - [x] 7.2 Call `start_lifecycle()` at startup and `stop_lifecycle()` at shutdown in main.py
    - After `configure_hooks()`: `await session_registry.start_lifecycle()`
    - Before `disconnect_all()`: `await session_registry.stop_lifecycle()`
    - _Requirements: 2.10, 2.11_

  - [x] 7.3 Remove lazy start from chat.py
    - Remove `_lifecycle_started` flag and the lazy-start block
    - _Requirements: 2.10_

  - [x] 7.4 Add SSE disconnect detection to chat.py (C6 — zombie STREAMING fix)
    - Pass `request` to `sse_with_heartbeat()`, check `await request.is_disconnected()` in heartbeat loop
    - In `message_generator()`, handle `asyncio.CancelledError` → call `unit.interrupt()` to transition STREAMING → IDLE
    - _Requirements: Bugs #9, #10, #11_

- [x] 8. Verify bug condition exploration tests now pass
  - [x] 8.1 Re-run bug condition exploration tests — EXPECTED: PASS
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6, 2.7_

  - [x] 8.2 Verify preservation tests still pass — EXPECTED: PASS

- [x] 9. Checkpoint — Ensure all tests pass
  - Run: `cd backend && pytest tests/test_process_resource_management.py -v`
  - Run: `cd backend && pytest --timeout=30` (full suite, no regressions)
