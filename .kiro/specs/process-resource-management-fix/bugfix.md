# Bugfix Requirements Document

## Introduction

SwarmAI's process and resource management subsystem has five interacting bugs that cause memory blindness, orphan process leaks, concurrency limit violations, file descriptor leaks, and lifecycle management gaps. Together these bugs allow the system to run with 21+ orphaned MCP processes, 4 concurrent CLIs when the limit should be 2-3, zero memory pressure alerts at 98% real RAM usage, and leaked file descriptors on every error path. The bugs span 5 backend files (`resource_monitor.py`, `session_unit.py`, `session_router.py`, `lifecycle_manager.py`, `main.py`) and affect approximately 150 lines of code.

## Bug Analysis

### Current Behavior (Defect)

**Memory Reading (resource_monitor.py)**

1.1 WHEN psutil is not installed AND the macOS vm_stat fallback is used THEN the system calculates available memory as `free + inactive + speculative` pages, inflating the reading by ~9.2GB because "inactive" pages are compressed/file-backed memory that is NOT truly available

1.2 WHEN available memory is inflated by the vm_stat fallback THEN `compute_max_tabs()` returns 4 instead of the correct 2-3, allowing more concurrent processes than the machine can safely support

1.3 WHEN available memory is inflated by the vm_stat fallback THEN `memory_pressure` stays "ok" even at 98% real RAM usage, and `_check_memory_pressure()` never fires (zero memory_pressure log entries ever observed)

**Wrapper File Descriptor Leak (session_unit.py)**

1.4 WHEN `_crash_to_cold()` is called on an error path (streaming crash, permission error, answer continuation error) THEN `_cleanup_internal()` sets `self._wrapper = None` without calling `wrapper.__aexit__()`, leaking file descriptors and pipes for the subprocess wrapper

1.5 WHEN `_crash_to_cold()` is called THEN it executes synchronously and cannot call the async `_force_kill()` method which is the only code path that properly calls `wrapper.__aexit__()`

**Slot Race Condition (session_router.py)**

1.6 WHEN two or more coroutines call `_acquire_slot()` concurrently AND `self.alive_count < max_tabs` THEN both coroutines pass the check and return "ready" before either has actually spawned, allowing the alive count to exceed `max_tabs` (observed: 4 CLIs alive when max should be 2-3)

1.7 WHEN a queued coroutine wakes up after `_slot_available.wait()` THEN it returns "queued" without re-verifying that a slot is actually available, because another coroutine may have claimed the slot between the event signal and the wake-up

**MCP Orphan Leak (session_unit.py + lifecycle_manager.py)**

1.8 WHEN `_force_kill()` kills a Claude CLI process that shares the parent's PGID (the Tauri app) THEN the safety guard correctly prevents `killpg()` but falls back to `os.kill(pid)` which only kills the CLI process, NOT its 5+ MCP child processes (builder-mcp, aws-sentral-mcp, aws-outlook-mcp, slack-mcp, taskei-p-mcp)

1.9 WHEN the orphan reaper runs in `_reap_orphans()` THEN it only matches patterns for "claude", "python main.py", and "pytest" but has ZERO patterns for MCP server processes, so orphaned MCP processes are never cleaned up (21 orphans found, 4-5 days old, 243MB total)

**Lifecycle Manager Gaps (main.py + chat.py)**

1.10 WHEN the application starts up THEN `lifecycle_manager.start()` is NOT called during the lifespan startup sequence — it is only lazy-started on the first chat request in `chat.py`, meaning the orphan reaper and maintenance loop do not run until the first user message

1.11 WHEN the application shuts down THEN `lifecycle_manager.stop()` is NEVER called — `main.py` calls `session_registry.disconnect_all()` and `channel_gateway.shutdown()` but not `lifecycle_manager.stop()`, so the background loop task leaks and `hook_executor.drain()` never runs

### Expected Behavior (Correct)

**Memory Reading (resource_monitor.py)**

2.1 WHEN psutil is not installed AND the macOS vm_stat fallback is used THEN the system SHALL calculate available memory as `free + speculative` pages only (excluding "inactive" pages which are compressed/file-backed), producing an accurate reading within 10% of the real available memory

2.2 WHEN available memory is accurately calculated THEN `compute_max_tabs()` SHALL return a value consistent with actual system resources (2-3 on a 36GB machine at 98% usage, not 4)

2.3 WHEN real memory usage exceeds 90% THEN `_check_memory_pressure()` SHALL detect the critical pressure level and proactively evict IDLE units to prevent OOM kills

**Wrapper File Descriptor Leak (session_unit.py)**

2.4 WHEN `_crash_to_cold()` is called on any error path THEN the system SHALL call `wrapper.__aexit__()` to properly close file descriptors and pipes before setting `self._wrapper = None`

2.5 WHEN `_crash_to_cold()` needs to clean up the wrapper THEN it SHALL be an async method (or delegate to an async cleanup path) so it can properly await `_force_kill()` which handles `wrapper.__aexit__()`

**Slot Race Condition (session_router.py)**

2.6 WHEN two or more coroutines call `_acquire_slot()` concurrently THEN the system SHALL use a reservation mechanism (asyncio.Lock or equivalent) to ensure that only one coroutine at a time can claim a slot, preventing the alive count from exceeding `max_tabs`

2.7 WHEN a queued coroutine wakes up after `_slot_available.wait()` THEN it SHALL re-verify that a slot is actually available before returning "queued", looping back to wait if the slot was claimed by another coroutine

**MCP Orphan Leak (session_unit.py + lifecycle_manager.py)**

2.8 WHEN `_force_kill()` kills a Claude CLI process that shares the parent's PGID THEN the system SHALL enumerate and kill the CLI's child processes (via `pgrep -P <pid>` or psutil `children()`) BEFORE killing the parent CLI process, preventing MCP server orphans

2.9 WHEN the orphan reaper runs in `_reap_orphans()` THEN it SHALL include patterns for known MCP server processes (builder-mcp, aws-sentral-mcp, aws-outlook-mcp, slack-mcp, taskei-p-mcp) with `require_orphaned=True` to clean up orphaned MCP processes

**Lifecycle Manager Gaps (main.py + chat.py)**

2.10 WHEN the application starts up THEN `lifecycle_manager.start()` SHALL be called during the lifespan startup sequence in `main.py` (after `session_registry.initialize()`), so the orphan reaper and maintenance loop run immediately at startup without waiting for the first chat request

2.11 WHEN the application shuts down THEN `lifecycle_manager.stop()` SHALL be called during the lifespan shutdown sequence in `main.py` (before `session_registry.disconnect_all()`), ensuring the background loop is cancelled and `hook_executor.drain()` runs to flush pending hooks

### Unchanged Behavior (Regression Prevention)

**Memory Reading**

3.1 WHEN psutil IS installed THEN the system SHALL CONTINUE TO use `psutil.virtual_memory()` for accurate cross-platform memory readings without any change to the psutil code path

3.2 WHEN the macOS vm_stat fallback fails entirely (subprocess error) THEN the system SHALL CONTINUE TO return the pessimistic fallback (16GB total, 1600MB available, 90% used) without crashing

3.3 WHEN `compute_max_tabs()` is called THEN the formula `max(1, min(floor((available_mb - 1024) / 500), 4))` SHALL CONTINUE TO be used, only now with accurate available memory input

**Wrapper Cleanup**

3.4 WHEN `kill()` is called explicitly (e.g., eviction, TTL kill, user disconnect) THEN the existing `_force_kill()` → `_cleanup_internal()` → COLD sequence SHALL CONTINUE TO work as before, with `_force_kill()` calling `wrapper.__aexit__()` before `_cleanup_internal()` clears the reference

3.5 WHEN `_cleanup_internal()` is called THEN it SHALL CONTINUE TO reset `_client`, `_wrapper`, `_interrupted`, `_retry_count`, and `_model_name` fields and preserve `_sdk_session_id` for resume capability

**Slot Management**

3.6 WHEN a requesting unit is already alive (`requesting_unit.is_alive`) THEN `_acquire_slot()` SHALL CONTINUE TO return "ready" immediately without any slot check or reservation

3.7 WHEN all slots are occupied by protected units (STREAMING/WAITING_INPUT) THEN the system SHALL CONTINUE TO queue the request with a 60-second timeout and return "timeout" if no slot becomes available

3.8 WHEN a slot becomes available via state transition (STREAMING/WAITING_INPUT → IDLE/COLD/DEAD) THEN `_on_unit_state_change()` SHALL CONTINUE TO signal `_slot_available` to wake queued requests

**Process Kill**

3.9 WHEN `_force_kill()` detects that the child process has its own PGID (different from the parent's) THEN it SHALL CONTINUE TO use `os.killpg()` to kill the entire process group

3.10 WHEN `_force_kill()` encounters a `ProcessLookupError` or `PermissionError` THEN it SHALL CONTINUE TO silently handle the error (process already dead)

**Orphan Reaper**

3.11 WHEN the orphan reaper matches a "claude" pattern process THEN it SHALL CONTINUE TO reap it without requiring `ppid==1` (always reap unowned Claude CLI processes)

3.12 WHEN the orphan reaper matches "python main.py" or "pytest" patterns THEN it SHALL CONTINUE TO require `require_orphaned=True` (only kill if `ppid==1`)

**Lifecycle Manager**

3.13 WHEN `lifecycle_manager.start()` is called multiple times THEN it SHALL CONTINUE TO be idempotent (subsequent calls are no-ops via the `_started` guard)

3.14 WHEN the maintenance loop runs THEN it SHALL CONTINUE TO execute health checks, streaming timeout detection, idle hook firing, TTL cleanup, dead unit cleanup, and memory pressure checks every 60 seconds
