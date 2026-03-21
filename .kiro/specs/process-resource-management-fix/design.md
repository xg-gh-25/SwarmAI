# Process Resource Management Bugfix Design

## Overview

Seven interacting bugs in the process/resource management subsystem cause memory blindness, orphan MCP leaks, concurrency limit violations, file descriptor leaks, lifecycle gaps, and zombie streaming states from disconnected SSE clients. The fix targets ~200 lines across 7 backend files with no new components, no frontend changes, and no architectural changes. The strategy is: (1) fix the vm_stat formula to exclude "inactive" pages, (2) eliminate the sync `_crash_to_cold()` entirely — all callers become async, (3) add an asyncio.Lock to `_acquire_slot()` with deadline-based timeout, (4) enumerate and kill MCP children with a two-pass approach, (5) read MCP patterns dynamically from config, (6) move lifecycle manager start/stop into the main.py lifespan, and (7) add SSE disconnect detection to prevent zombie STREAMING states.

### Deferred Scope

The following bugs from the original 23-bug audit are NOT addressed in this spec and are deferred to a follow-up:
- Frontend bugs #15-18 (closeTab cleanup, tab restore validation, SSE stall timeout, beforeunload handler) — separate frontend PR
- Bug #19 (sessionStorage leak) — low severity, cosmetic
- Bug #22 (stop endpoint doesn't notify SSE consumer) — low severity, covered by streaming timeout watchdog

## Glossary

- **Bug_Condition (C)**: The union of five conditions that trigger incorrect behavior — inflated memory reading, leaked file descriptors, slot over-allocation, orphaned MCP processes, and missing lifecycle hooks
- **Property (P)**: The desired behavior — accurate memory readings, proper wrapper cleanup, atomic slot acquisition, complete child process kill, and eager lifecycle management
- **Preservation**: Existing behaviors that must remain unchanged — psutil code path, pessimistic fallback, compute_max_tabs formula, explicit kill() sequence, _cleanup_internal field resets, alive-unit fast path, queue timeout, state-change signaling, PGID-based killpg, error handling, existing reaper patterns, idempotent start(), maintenance loop cadence
- **`_read_memory_macos_fallback()`**: Method in `resource_monitor.py` that parses `vm_stat` output to estimate available RAM when psutil is unavailable
- **`_crash_to_cold()`**: Synchronous method in `session_unit.py` that transitions DEAD → COLD on error paths, currently skipping wrapper `__aexit__()` cleanup. To be deleted — all callers (including `health_check` and `force_unstick_streaming`) become async.
- **`_acquire_slot()`**: Async method in `session_router.py` that gates concurrency — currently has a check-then-act race between `alive_count < max_tabs` and actual spawn
- **`_force_kill()`**: Async method in `session_unit.py` that kills the subprocess and calls `wrapper.__aexit__()` — currently only kills the CLI PID when PGID is shared, leaving MCP children alive. Fix uses two-pass child kill to close TOCTOU race.
- **`_reap_orphans()`**: Async method in `lifecycle_manager.py` that finds and kills orphaned processes — currently missing MCP server patterns. Fix reads MCP config dynamically instead of hardcoding names.
- **TOCTOU**: Time-of-check-to-time-of-use race condition — between enumerating children and killing them, new children may spawn
- **PGID**: Process Group ID — used by `killpg()` to kill an entire process tree

## Bug Details

### Bug Condition

The bug manifests across five interacting conditions. Any single condition being true constitutes a bug trigger.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type SystemEvent (memory_read | error_path_crash | slot_request | process_kill | app_lifecycle)
  OUTPUT: boolean

  // C1: Memory inflation
  IF input.type == "memory_read"
     AND NOT psutil_installed
     AND platform == "darwin"
     AND vm_stat_available
  THEN RETURN TRUE   // inactive pages included in "available"

  // C2: FD leak on crash path
  IF input.type == "error_path_crash"
     AND input.unit._wrapper IS NOT NULL
     AND input.caller == "_crash_to_cold"
  THEN RETURN TRUE   // wrapper.__aexit__() never called

  // C3: Slot race
  IF input.type == "slot_request"
     AND concurrent_acquire_slot_calls > 1
     AND alive_count < max_tabs  // both see this as true
  THEN RETURN TRUE   // both return "ready", exceeding max_tabs

  // C4: MCP orphan on kill
  IF input.type == "process_kill"
     AND target_pid_pgid == parent_pgid  // shared PGID
     AND target_has_mcp_children
  THEN RETURN TRUE   // children survive parent kill

  // C5: Lifecycle gap
  IF input.type == "app_lifecycle"
     AND (phase == "startup" AND lifecycle_manager NOT started)
     OR (phase == "shutdown" AND lifecycle_manager.stop NOT called)
  THEN RETURN TRUE

  // C6: SSE disconnect → zombie STREAMING
  IF input.type == "sse_disconnect"
     AND unit.state == STREAMING
     AND sse_client_disconnected
     AND unit.state NOT transitioned to IDLE/COLD
  THEN RETURN TRUE   // unit stays STREAMING forever

  // C7: Dead unit dict leak
  IF input.type == "unit_lifecycle"
     AND unit.state == COLD
     AND (now - unit.last_used) > 3600
     AND unit still in router._units dict
  THEN RETURN TRUE   // unbounded dict growth

  RETURN FALSE
END FUNCTION
```

### Examples

- **C1 — Memory inflation**: `vm_stat` reports free=200MB, inactive=9000MB, speculative=50MB. Current code: available = 200 + 9000 + 50 = 9250MB. Actual available ≈ 250MB. `compute_max_tabs()` returns `floor((9250 - 1024) / 500) = 16` → clamped to 4. Correct answer: `floor((250 - 1024) / 500)` → clamped to 1.
- **C2 — FD leak**: Streaming crash triggers `_crash_to_cold()` → `_cleanup_internal()` sets `self._wrapper = None`. The wrapper's `__aexit__()` is never called, leaking stdin/stdout/stderr pipes (3 FDs per crash).
- **C3 — Slot race**: Two tabs send simultaneously. Both coroutines read `alive_count == 1`, `max_tabs == 2`. Both return "ready". Both spawn. Result: 3 alive processes when max is 2.
- **C4 — MCP orphan**: `_force_kill(pid=1234)` where pid 1234 shares PGID with the Tauri app. Safety guard skips `killpg()`, falls back to `os.kill(1234)`. Five MCP children (PIDs 1235-1239) survive as orphans (PPID=1).
- **C5 — Lifecycle gap**: App starts, user doesn't chat for 10 minutes. During those 10 minutes, orphan reaper never runs, 21 MCP orphans accumulate. On shutdown, `lifecycle_manager.stop()` is never called, background loop task leaks.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- psutil code path (`_HAS_PSUTIL == True`) continues to use `psutil.virtual_memory()` without modification (Req 3.1)
- vm_stat fallback failure continues to return pessimistic fallback (16GB total, 1600MB available, 90% used) (Req 3.2)
- `compute_max_tabs()` formula `max(1, min(floor((available_mb - 1024) / 500), 4))` is unchanged — only the input (available_mb) becomes accurate (Req 3.3)
- Explicit `kill()` path (`_force_kill()` → `_cleanup_internal()` → COLD) continues to work as before (Req 3.4)
- `_cleanup_internal()` continues to reset `_client`, `_wrapper`, `_interrupted`, `_retry_count`, `_model_name` and preserve `_sdk_session_id` (Req 3.5)
- Already-alive units bypass slot acquisition entirely (Req 3.6)
- Queue timeout at 60 seconds continues to return "timeout" (Req 3.7)
- `_on_unit_state_change()` continues to signal `_slot_available` on protected→unprotected transitions (Req 3.8)
- `os.killpg()` continues to be used when child has its own PGID (Req 3.9)
- `ProcessLookupError`/`PermissionError` continue to be silently handled (Req 3.10)
- "claude" pattern continues to reap without `require_orphaned` (Req 3.11)
- "python main.py" and "pytest" patterns continue to require `require_orphaned=True` (Req 3.12)
- `lifecycle_manager.start()` remains idempotent (Req 3.13)
- Maintenance loop continues 60-second cadence with all existing checks (Req 3.14)

**Scope:**
All inputs that do NOT match any of the five bug conditions should be completely unaffected. This includes:
- All psutil-based memory readings
- All explicit `kill()` calls (eviction, TTL, disconnect)
- Single-coroutine slot acquisition (no concurrency)
- Process kills where child has its own PGID
- All existing orphan reaper patterns
- All maintenance loop operations

## Hypothesized Root Cause

Based on the bug description and code analysis, the root causes are:

1. **vm_stat formula includes "inactive" pages (C1)**: In `_read_memory_macos_fallback()`, line `available = free + inactive + speculative` treats macOS "inactive" pages as available. On macOS, "inactive" pages are file-backed/compressed memory held by the unified buffer cache — the kernel reclaims them under pressure but they are NOT free RAM. Apple's own `memory_pressure` tool and `psutil` both exclude inactive from available. The ~9.2GB inflation directly causes `compute_max_tabs()` to return 4 instead of 1-2.

2. **`_crash_to_cold()` is synchronous (C2)**: The method was designed as a sync convenience wrapper for the `DEAD → cleanup → COLD` pattern. But `_force_kill()` (which calls `wrapper.__aexit__()`) is async. The sync method cannot await it, so it calls `_cleanup_internal()` directly, which sets `self._wrapper = None` without closing the wrapper's file descriptors. There are 10 call sites in `send()` error paths, all in async generator methods that could await an async version.

3. **No mutual exclusion in `_acquire_slot()` (C3)**: The check `self.alive_count < max_tabs` and the implicit "claim" (returning "ready") are not atomic. Between the check and the actual spawn (which happens later in `send()`), another coroutine can also pass the check. Additionally, after `_slot_available.wait()` wakes up, the code returns "queued" without re-checking — another coroutine may have claimed the slot in the meantime.

4. **Shared-PGID fallback only kills parent PID (C4)**: When the Claude CLI shares the Tauri app's PGID (which is the common case since `start_new_session=True` is not used), `_force_kill()` correctly avoids `killpg()` but falls back to `os.kill(pid)` which only kills the CLI process. The 5+ MCP server children (spawned by the CLI) are not enumerated or killed, becoming orphans with PPID=1.

5. **No MCP patterns in orphan reaper (C4 continued)**: `_reap_orphans()` only has patterns for "claude", "python main.py", and "pytest". MCP server processes (`builder-mcp`, `aws-sentral-mcp`, etc.) are not matched, so even the reaper cannot clean them up after they become orphans.

6. **Lifecycle manager not wired into lifespan (C5)**: `lifecycle_manager.start()` is called lazily on the first chat request in `chat.py`. `lifecycle_manager.stop()` is never called anywhere. The `main.py` lifespan shutdown calls `session_registry.disconnect_all()` and `channel_gateway.shutdown()` but omits the lifecycle manager entirely.

## Correctness Properties

Property 1: Bug Condition — vm_stat Available Memory Accuracy

_For any_ macOS system where psutil is not installed and `vm_stat` succeeds, the fixed `_read_memory_macos_fallback()` SHALL compute available memory as `free + speculative` pages only (excluding "inactive"), producing a value within 10% of `psutil.virtual_memory().available` on the same system.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation — psutil and Fallback Paths Unchanged

_For any_ system where psutil IS installed, or where `vm_stat` fails entirely, the fixed code SHALL produce exactly the same `SystemMemory` result as the original code — psutil path returns `psutil.virtual_memory()` values, failure path returns the pessimistic fallback (16GB/1600MB/90%).

**Validates: Requirements 3.1, 3.2, 3.3**

Property 3: Bug Condition — Wrapper Cleanup on Crash Paths

_For any_ error path where `_crash_to_cold_async()` is called and `self._wrapper` is not None, the fixed method SHALL call `_force_kill()` (which calls `wrapper.__aexit__()`) BEFORE `_cleanup_internal()` clears the wrapper reference, ensuring zero file descriptor leaks.

**Validates: Requirements 2.4, 2.5**

Property 4: Preservation — Explicit kill() Path Unchanged

_For any_ explicit `kill()` call (eviction, TTL, disconnect), the fixed code SHALL produce exactly the same sequence: `_force_kill()` → `_cleanup_internal()` → COLD, with `_cleanup_internal()` continuing to reset the same fields and preserve `_sdk_session_id`.

**Validates: Requirements 3.4, 3.5**

Property 5: Bug Condition — Slot Acquisition Atomicity

_For any_ set of N concurrent `_acquire_slot()` calls where N > max_tabs, the fixed method SHALL ensure that at most max_tabs coroutines return "ready" (or "queued" after re-verification), preventing the alive count from exceeding `max_tabs`.

**Validates: Requirements 2.6, 2.7**

Property 6: Preservation — Slot Management Behavior Unchanged

_For any_ single-coroutine slot acquisition, or for already-alive units, or for queue timeout scenarios, the fixed code SHALL produce exactly the same result as the original code — alive units get "ready" immediately, single requests pass through without lock contention, timeouts return "timeout" after 60 seconds.

**Validates: Requirements 3.6, 3.7, 3.8**

Property 7: Bug Condition — MCP Child Process Kill

_For any_ `_force_kill()` call where the target CLI process shares the parent's PGID and has MCP child processes, the fixed method SHALL enumerate children via `pgrep -P <pid>` and SIGKILL each child BEFORE killing the parent CLI process, leaving zero orphaned MCP processes.

**Validates: Requirements 2.8, 2.9**

Property 8: Preservation — PGID-Based Kill and Error Handling Unchanged

_For any_ `_force_kill()` call where the child has its own PGID, the fixed code SHALL continue to use `os.killpg()`. For `ProcessLookupError`/`PermissionError`, the fixed code SHALL continue to silently handle the error.

**Validates: Requirements 3.9, 3.10**

Property 9: Bug Condition — Lifecycle Manager Eager Start and Clean Stop

_For any_ application startup, `lifecycle_manager.start()` SHALL be called during the lifespan startup (after `session_registry.initialize()`). For any shutdown, `lifecycle_manager.stop()` SHALL be called before `session_registry.disconnect_all()`.

**Validates: Requirements 2.10, 2.11**

Property 10: Preservation — Orphan Reaper Patterns and Idempotent Start

_For any_ orphan reaper run, the existing "claude" (no require_orphaned), "python main.py" (require_orphaned=True), and "pytest" (require_orphaned=True) patterns SHALL continue to work identically. `lifecycle_manager.start()` SHALL remain idempotent.

**Validates: Requirements 3.11, 3.12, 3.13, 3.14**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/core/resource_monitor.py`

**Function**: `_read_memory_macos_fallback()`

**Specific Changes**:
1. **Remove "inactive" from available calculation**: Change `available = free + inactive + speculative` to `available = free + speculative`. Remove the `inactive` variable assignment. Add a debug log showing the excluded inactive pages for observability.

---

**File**: `backend/core/session_unit.py`

**Function**: `_crash_to_cold()` → new `_crash_to_cold_async()`

**Specific Changes**:
2. **Delete sync `_crash_to_cold()` entirely**: Create `_crash_to_cold_async()` that calls `await _force_kill()` before `_cleanup_internal()`, then transitions to COLD. The sync version is deleted — ALL callers become async:
   - 10 call sites in `send()` error paths (already async)
   - `health_check()` — already async, currently calls sync `_crash_to_cold` via `_cleanup_internal` directly. Switch to `await _crash_to_cold_async()`.
   - `force_unstick_streaming()` — currently sync. Make async (`async def force_unstick_streaming`). Its only caller is `LifecycleManager._check_streaming_timeout()` which is already async.
3. **Switch ALL callers**: Replace every `self._crash_to_cold(...)` with `await self._crash_to_cold_async(...)`. No sync version remains.

**Function**: `_force_kill()`

**Specific Changes**:
4. **Two-pass child kill to close TOCTOU race**: In the shared-PGID branch (where `pgid == my_pgid`):
   - Pass 1: `pgrep -P <pid>` → SIGKILL each child
   - SIGKILL parent PID (stops it from spawning new children)
   - Pass 2: `pgrep -P <pid>` again → SIGKILL any stragglers spawned between pass 1 and parent kill
   - This closes the race where the CLI spawns a new MCP child between enumeration and parent kill.

---

**File**: `backend/core/session_router.py`

**Function**: `_acquire_slot()`

**Specific Changes**:
5. **Add asyncio.Lock with deadline-based timeout**: Add `self._slot_lock = asyncio.Lock()` to `__init__()`. Wrap the check-then-act section of `_acquire_slot()` in `async with self._slot_lock:` so only one coroutine at a time can evaluate `alive_count < max_tabs` and return "ready".
6. **Re-check after wake with deadline accounting**: After `_slot_available.wait()` returns, re-check `alive_count < max_tabs` under the lock. Use a single `deadline = time.monotonic() + QUEUE_TIMEOUT` computed once at entry. Each wait iteration uses `remaining = deadline - time.monotonic()` as its timeout. This prevents a request from waiting indefinitely through repeated wake-and-recheck cycles.

---

**File**: `backend/core/lifecycle_manager.py`

**Function**: `_reap_orphans()`

**Specific Changes**:
7. **Dynamic MCP server patterns from config**: Instead of hardcoding MCP server names, read them from the MCP config file (`mcp-dev.json`) at reap time. Extract the command basename from each server's `command` field. Fall back to a static list if config read fails. Add `require_orphaned=True` so only truly orphaned MCP processes (PPID=1) are killed.

---

**File**: `backend/main.py`

**Function**: `lifespan()`

**Specific Changes**:
8. **Eager start via public API**: After `session_registry.initialize(app_config)` and `session_registry.configure_hooks(...)`, call `await session_registry.start_lifecycle()`. Add `start_lifecycle()` and `stop_lifecycle()` public methods to `session_registry.py` to avoid accessing private `_lifecycle_manager` attribute.
9. **Clean stop via public API**: In the shutdown section, call `await session_registry.stop_lifecycle()` BEFORE `await session_registry.disconnect_all()`.

---

**File**: `backend/routers/chat.py`

**Specific Changes**:
10. **Remove lazy start**: Remove the `if not _lifecycle_started and _lifecycle_manager: await _lifecycle_manager.start()` block and the `_lifecycle_started` flag, since the lifecycle manager is now started eagerly in `main.py`.

---

**File**: `backend/routers/chat.py`

**Function**: `sse_with_heartbeat()` and `chat_stream()`

**Specific Changes**:
11. **SSE disconnect detection (C6)**: In `sse_with_heartbeat()`, accept a `request: Request` parameter. In the heartbeat loop, check `await request.is_disconnected()` on each iteration. If disconnected, break the loop and cancel the consumer task. This prevents the message_generator (and thus the SessionUnit) from running indefinitely after the client disconnects.
12. **CancelledError → IDLE transition**: In `chat_stream()`'s `message_generator()`, wrap the `async for msg in router.run_conversation(...)` in a try/except for `asyncio.CancelledError`. On cancellation, call `unit.interrupt()` to transition the unit from STREAMING to IDLE, preventing zombie streaming states.

---

**File**: `backend/core/lifecycle_manager.py`

**Function**: `_maintenance_loop()`

**Specific Changes**:
13. **Stale COLD unit purge (C7)**: Add `_purge_stale_cold()` to the maintenance loop (every 10th cycle, same as orphan reaper). Remove COLD units from `router._units` that have been idle > 1 hour. This prevents unbounded dict growth.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fixes work correctly and preserve existing behavior.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bugs BEFORE implementing the fixes. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that exercise each bug condition in isolation and assert the expected (correct) behavior. Run these tests on the UNFIXED code to observe failures and confirm the root causes.

**Test Cases**:
1. **vm_stat Inflation Test**: Mock `vm_stat` output with known page counts (free=200MB, inactive=9000MB, speculative=50MB). Assert `_read_memory_macos_fallback()` returns available ≈ 250MB. (Will fail on unfixed code — returns ~9250MB)
2. **compute_max_tabs Accuracy Test**: With mocked vm_stat returning inflated available, assert `compute_max_tabs()` returns 1 (not 4). (Will fail on unfixed code)
3. **Wrapper Cleanup Test**: Create a SessionUnit with a mock wrapper, call `_crash_to_cold()`, assert `wrapper.__aexit__()` was called. (Will fail on unfixed code — `__aexit__` never called)
4. **Slot Race Test**: Launch 3 concurrent `_acquire_slot()` calls with `max_tabs=2`. Assert at most 2 return "ready". (Will fail on unfixed code — all 3 may return "ready")

**Expected Counterexamples**:
- vm_stat available memory is ~37x higher than reality (9250MB vs 250MB)
- `wrapper.__aexit__()` call count is 0 after `_crash_to_cold()`
- 3 out of 3 concurrent slot requests return "ready" when max is 2

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed functions produce the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  IF input.type == "memory_read" THEN
    result := _read_memory_macos_fallback_fixed(input.vm_stat_output)
    ASSERT result.available == (free + speculative) * page_size
    ASSERT result.available does NOT include inactive pages
  
  IF input.type == "error_path_crash" THEN
    result := _crash_to_cold_async_fixed(input.unit)
    ASSERT input.unit._wrapper.__aexit__ was called
    ASSERT input.unit.state == COLD
  
  IF input.type == "slot_request" THEN
    results := concurrent_acquire_slot_fixed(input.N_requests, input.max_tabs)
    ASSERT count(results where result == "ready") <= input.max_tabs
  
  IF input.type == "process_kill" THEN
    result := _force_kill_fixed(input.pid)
    ASSERT all children of input.pid received SIGKILL
    ASSERT input.pid received SIGKILL
  
  IF input.type == "app_lifecycle" THEN
    ASSERT lifecycle_manager.start() called during startup
    ASSERT lifecycle_manager.stop() called during shutdown
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed functions produce the same result as the original functions.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT original_function(input) == fixed_function(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for non-bug inputs, then write property-based tests capturing that behavior.

**Test Cases**:
1. **psutil Path Preservation**: Mock psutil as installed, generate random `virtual_memory()` values. Verify `system_memory()` returns identical results before and after fix.
2. **Fallback Failure Preservation**: Mock vm_stat as failing. Verify pessimistic fallback (16GB/1600MB/90%) is returned identically.
3. **compute_max_tabs Formula Preservation**: For any available_mb value, verify `max(1, min(floor((available_mb - 1024) / 500), 4))` produces the same result — only the input changes, not the formula.
4. **Explicit kill() Preservation**: Call `kill()` on a unit with a mock wrapper. Verify `_force_kill()` → `_cleanup_internal()` → COLD sequence is identical.
5. **Alive Unit Fast Path Preservation**: Call `_acquire_slot()` with an already-alive unit. Verify it returns "ready" immediately without touching the lock.
6. **Queue Timeout Preservation**: Call `_acquire_slot()` when all slots are occupied by STREAMING units. Verify timeout after 60 seconds returns "timeout".

### Unit Tests

- Test `_read_memory_macos_fallback()` with mocked vm_stat output: verify inactive pages excluded
- Test `_crash_to_cold_async()` calls `_force_kill()` then `_cleanup_internal()` in order
- Test `_force_kill()` child enumeration via mocked `pgrep -P` output
- Test `_acquire_slot()` with asyncio.Lock prevents concurrent over-allocation
- Test `_acquire_slot()` re-check loop after `_slot_available.wait()` wake-up
- Test `_reap_orphans()` includes MCP patterns with `require_orphaned=True`
- Test lifecycle manager start in lifespan startup, stop in shutdown

### Property-Based Tests

- Generate random vm_stat page counts (free, inactive, speculative, wired, active) and verify `compute_max_tabs()` formula correctness: `max(1, min(floor(((free + speculative) * page_size / 1024 / 1024 - 1024) / 500), 4))`
- Generate random available_mb values [0, 100000] and verify `compute_max_tabs()` output matches the formula exactly (preservation of formula)
- Generate random concurrent slot request counts [1, 10] with random max_tabs [1, 4] and verify at most max_tabs "ready" results (atomicity property)
- Generate random SessionUnit states and verify `_crash_to_cold_async()` always reaches COLD state with wrapper cleaned up

### Integration Tests

- Test full lifecycle: startup → lifecycle_manager.start() called → maintenance loop running → shutdown → lifecycle_manager.stop() called → loop cancelled
- Test end-to-end slot management: spawn 2 sessions, verify 3rd is queued, kill one, verify queued session proceeds
- Test MCP child kill: spawn a mock process tree (parent + children), call `_force_kill()`, verify all processes killed
