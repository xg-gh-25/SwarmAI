<!-- PE-REVIEWED -->
# Graceful Shutdown Fix — Bugfix Design

## Overview

The three window close/exit handlers in `desktop/src-tauri/src/lib.rs` (`WindowEvent::Destroyed`, `RunEvent::Exit`, `RunEvent::ExitRequested`) skip the graceful shutdown request and immediately call `kill_process_tree()`. This prevents the Python backend from running `disconnect_all()` and its three lifecycle hooks (`DailyActivityExtractionHook`, `WorkspaceAutoCommitHook`, `DistillationTriggerHook`).

The fix mirrors the existing `stop_backend` command pattern: call `send_shutdown_request(port)`, sleep for a bounded period, then `kill_process_tree(pid)` as a safety net. The change is Rust-only, confined to `lib.rs`.

## Glossary

- **Bug_Condition (C)**: Any of the three window close/exit event handlers firing while the backend is running — they skip `send_shutdown_request()` and go straight to `kill_process_tree()`.
- **Property (P)**: Each handler SHALL send a graceful shutdown request before force-killing, giving the backend time to run lifecycle hooks.
- **Preservation**: The existing `stop_backend` command, platform-specific implementations of `send_shutdown_request` and `kill_process_tree`, and the `child.kill()` fallback must remain unchanged.
- **`send_shutdown_request(port)`**: Platform-specific function in `lib.rs` that POSTs to `http://127.0.0.1:{port}/shutdown` (curl on Unix, PowerShell on Windows) with a 3-second timeout.
- **`kill_process_tree(pid)`**: Platform-specific function in `lib.rs` that force-kills the backend and all child processes.
- **`stop_backend`**: Tauri command that already implements the correct graceful pattern: `send_shutdown_request` → sleep 2s → `kill_process_tree` → `child.kill()`.
- **Lifecycle hooks**: `DailyActivityExtractionHook`, `WorkspaceAutoCommitHook`, `DistillationTriggerHook` — fired by `disconnect_all()` when the backend receives `POST /shutdown`.

## Bug Details

### Fault Condition

The bug manifests when the user closes the app (Cmd+Q, window close button, system shutdown, etc.) while the backend is running. All three event handlers immediately call `kill_process_tree(pid)` without first sending `POST /shutdown`, so the backend never executes `disconnect_all()` and the lifecycle hooks are skipped.

**Formal Specification:**
```
FUNCTION isBugCondition(event, backendState)
  INPUT: event of type {WindowEvent::Destroyed | RunEvent::Exit | RunEvent::ExitRequested},
         backendState of type BackendState
  OUTPUT: boolean

  RETURN event IN {WindowEvent::Destroyed, RunEvent::Exit, RunEvent::ExitRequested}
         AND backendState.running == true
         AND backendState.pid IS Some(pid)
         AND gracefulShutdownNotSent(event)
END FUNCTION
```

Where `gracefulShutdownNotSent(event)` is true because the current code never calls `send_shutdown_request()` in any of the three handlers.

### Examples

- User presses Cmd+Q on macOS → `RunEvent::ExitRequested` fires → `kill_process_tree()` called immediately → backend killed without running hooks → daily activity summary lost.
- User clicks the window close button → `WindowEvent::Destroyed` fires → `kill_process_tree()` called immediately → uncommitted workspace changes not auto-committed.
- System triggers `RunEvent::Exit` → `kill_process_tree()` called immediately → distillation check skipped.
- Backend is already dead when window closes → `pid` is `None` → no shutdown request needed, no force kill needed → should exit cleanly with no delay (edge case, not a bug).

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- The `stop_backend` Tauri command must continue to use its existing graceful shutdown sequence (`send_shutdown_request` → sleep 2s → `kill_process_tree` → `child.kill()`).
- The platform-specific `send_shutdown_request` implementations (curl on Unix, PowerShell on Windows) must remain unchanged.
- The platform-specific `kill_process_tree` implementations must remain unchanged.
- The `child.kill()` fallback call must be preserved in all handlers.
- When the backend is not running at close time (`running == false` or `pid == None`), the handlers must exit cleanly without errors or delays.

**Scope:**
All inputs that do NOT involve the three window close/exit events should be completely unaffected by this fix. This includes:
- The `stop_backend` Tauri command invoked from the UI
- Backend startup via `start_backend`
- Health check polling
- Backend status queries
- All other Tauri commands (`check_nodejs_version`, `check_python_version`, `check_git_bash_path`)

## Hypothesized Root Cause

Based on the code analysis, the root cause is straightforward — it is not a logic error but a missing code path:

1. **Missing `send_shutdown_request()` call**: All three handlers (`WindowEvent::Destroyed`, `RunEvent::Exit`, `RunEvent::ExitRequested`) were written to only call `kill_process_tree(pid)` and `child.kill()`. The `send_shutdown_request(port)` call was never added to these handlers, even though it exists and is correctly used in `stop_backend`.

2. **Missing sleep between shutdown request and force kill**: Even if `send_shutdown_request` were called, there is no `std::thread::sleep()` to give the backend time to process the shutdown and run lifecycle hooks before `kill_process_tree` force-kills everything.

3. **Port not captured before lock release**: The handlers currently lock the `BackendState`, read `pid`, and call `kill_process_tree`. They do not read `port`, which is needed for `send_shutdown_request(port)`. The port must be captured from the locked state before the shutdown request is sent.

There is no deeper architectural issue — the fix is to replicate the `stop_backend` pattern in each handler via a shared helper function, with a `was_running` guard that also serves as double-fire protection (since `WindowEvent::Destroyed` and `RunEvent::Exit` both fire during the same close sequence).

## Correctness Properties

Property 1: Fault Condition — Graceful Shutdown Before Force Kill

_For any_ window close/exit event (`WindowEvent::Destroyed`, `RunEvent::Exit`, `RunEvent::ExitRequested`) where the backend is running (`running == true` and `pid` is `Some`), the handler SHALL call `send_shutdown_request(port)` and then wait a bounded period (3 seconds) before calling `kill_process_tree(pid)`, giving the backend time to execute `disconnect_all()` and its lifecycle hooks.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

Property 2: Preservation — Existing stop_backend and Non-Close Behavior

_For any_ input that is NOT one of the three window close/exit events (e.g., `stop_backend` command, backend startup, status queries), the fixed code SHALL produce exactly the same behavior as the original code, preserving the existing `stop_backend` graceful shutdown sequence and all other Tauri command behavior unchanged.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Property 3: Idempotency — Double-Fire Protection

_For any_ app close sequence where multiple handlers fire in succession (e.g., `WindowEvent::Destroyed` followed by `RunEvent::Exit`), only the FIRST handler to acquire the lock and observe `backend.running == true` SHALL perform the graceful shutdown sequence. Subsequent handlers SHALL observe `backend.running == false` (set by the first handler under lock) and skip the shutdown request and sleep, proceeding directly to the force-kill safety net.

**Validates: Requirements 2.4, 2.5 (bounded delay even under double-fire)**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `desktop/src-tauri/src/lib.rs`

**Handlers**: `WindowEvent::Destroyed` closure, `RunEvent::Exit` match arm, `RunEvent::ExitRequested` match arm.

**Specific Changes**:

1. **Extract a shared helper function `graceful_shutdown_and_kill`**: To avoid duplicating the same 8-line pattern in 3 handlers (DRY violation), extract a synchronous helper function that encapsulates the full shutdown sequence. All three handlers call this single function.

2. **Double-fire protection via `was_running` guard**: The `WindowEvent::Destroyed` handler fires first, then `RunEvent::Exit` fires during the same close sequence. Both will attempt to lock the state. The first handler sets `backend.running = false` under lock. The second handler observes `running == false` and skips the shutdown request + sleep, proceeding directly to the force-kill safety net. This prevents double shutdown requests and bounds the total delay.

3. **Capture `port` from locked state**: In the helper function, read `backend.port` alongside `backend.pid` before releasing the lock.

4. **Add `send_shutdown_request(port)` call**: After capturing port and pid, and before calling `kill_process_tree(pid)`, call `send_shutdown_request(port)` if the backend was running.

5. **Add bounded sleep after shutdown request**: Insert `std::thread::sleep(Duration::from_secs(3))` after `send_shutdown_request` and before `kill_process_tree`. Use `std::thread::sleep` since these handlers run inside `block_on`. The `send_shutdown_request` function itself has a 3-second timeout, so total worst-case delay is ~6 seconds. Note: the backend's `/shutdown` endpoint awaits `disconnect_all()` synchronously, and each hook has a 30s timeout. The 3s sleep is optimistic — if hooks are slow, `kill_process_tree` will terminate them. This is acceptable because the hooks are error-isolated and the most common case (git status + commit) completes in <1 second.

6. **Preserve `child.kill()` fallback**: Keep the existing `child.kill()` call after `kill_process_tree` — this is the defense-in-depth pattern from `stop_backend`.

### Helper Function

```rust
/// Gracefully shut down the backend and then force-kill as safety net.
///
/// Mirrors the `stop_backend` command pattern:
/// 1. Capture state under lock, mark as not running
/// 2. Release lock before blocking I/O
/// 3. If was running: send_shutdown_request → sleep 3s
/// 4. Force kill process tree + child as safety net
///
/// Double-fire safe: if `backend.running` is already false (set by a
/// previous handler in the same close sequence), skips the shutdown
/// request and sleep, proceeding directly to force-kill.
fn graceful_shutdown_and_kill(state: SharedBackendState, context: &str) {
    tauri::async_runtime::block_on(async {
        let mut backend = state.lock().await;
        let was_running = backend.running;
        let port = backend.port;
        let pid = backend.pid;
        let child = backend.child.take();

        // Mark as not running under lock — prevents double-fire
        backend.running = false;
        backend.pid = None;
        drop(backend); // Release lock before blocking I/O

        // Graceful shutdown only if backend was actually running
        if was_running {
            println!("[{}] Attempting graceful shutdown on port {}", context, port);
            send_shutdown_request(port);
            std::thread::sleep(std::time::Duration::from_secs(3));
        }

        // Force kill as safety net (always, even if shutdown request succeeded)
        if let Some(pid) = pid {
            kill_process_tree(pid);
            println!("[{}] Killed backend process tree (PID: {})", context, pid);
        }

        if let Some(child) = child {
            let _ = child.kill();
        }
    });
}
```

### Handler Changes

Each handler becomes a one-liner calling the helper:

```rust
// WindowEvent::Destroyed
window.on_window_event(move |event| {
    if let tauri::WindowEvent::Destroyed = event {
        let state = app_handle.state::<SharedBackendState>();
        graceful_shutdown_and_kill(state.inner().clone(), "window_destroy");
    }
});

// RunEvent::Exit
tauri::RunEvent::Exit => {
    let state = app_handle.state::<SharedBackendState>();
    graceful_shutdown_and_kill(state.inner().clone(), "exit");
}

// RunEvent::ExitRequested
tauri::RunEvent::ExitRequested { api, .. } => {
    let _ = api;
    let state = app_handle.state::<SharedBackendState>();
    graceful_shutdown_and_kill(state.inner().clone(), "exit_requested");
}
```

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior. Since this is a Rust/Tauri application with OS-level process management, testing focuses on code review verification and integration-level validation rather than unit-level property-based testing.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Inspect the three handler code paths in the unfixed `lib.rs` and confirm that `send_shutdown_request` is never called. Run the app, trigger each close path, and observe whether the backend's `/shutdown` endpoint is hit (check backend logs for the shutdown log line).

**Test Cases**:
1. **WindowEvent::Destroyed Test**: Close the main window → check backend logs → confirm no `POST /shutdown` received (will fail on unfixed code)
2. **RunEvent::Exit Test**: Quit the app via Cmd+Q → check backend logs → confirm no `POST /shutdown` received (will fail on unfixed code)
3. **RunEvent::ExitRequested Test**: Trigger exit request → check backend logs → confirm no `POST /shutdown` received (will fail on unfixed code)
4. **Backend Already Dead Test**: Kill the backend manually, then close the window → confirm no crash or hang (edge case, should pass on both unfixed and fixed code)

**Expected Counterexamples**:
- Backend logs show no shutdown request received on any close path
- `disconnect_all()` never called, lifecycle hooks never fire
- Root cause confirmed: `send_shutdown_request(port)` is simply absent from all three handlers

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL event IN {WindowDestroyed, Exit, ExitRequested} WHERE backend.running == true DO
  result := handle_event_fixed(event)
  ASSERT send_shutdown_request_was_called(result)
  ASSERT sleep_occurred_before_kill(result)
  ASSERT kill_process_tree_was_called(result)
  ASSERT child_kill_was_called(result)
  ASSERT total_delay <= 8 seconds
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT originalFunction(input) = fixedFunction(input)
END FOR
```

**Testing Approach**: Code review verification is the primary approach for preservation checking because:
- The fix only adds code (shutdown request + sleep) before existing code in the three handlers
- No existing code paths are modified or removed
- The `stop_backend` command is in a completely separate function that is not touched
- All other Tauri commands are unaffected

**Test Plan**: Verify via code diff that `stop_backend`, `send_shutdown_request`, `kill_process_tree`, and all other commands are unchanged. Then manually test `stop_backend` from the UI to confirm it still works.

**Test Cases**:
1. **stop_backend Preservation**: Invoke stop_backend from UI settings → verify graceful shutdown still works with same timing
2. **Backend Not Running Preservation**: Close window when backend is not running → verify clean exit with no errors or delays
3. **Platform Implementation Preservation**: Verify `send_shutdown_request` and `kill_process_tree` function bodies are unchanged in the diff
4. **child.kill() Preservation**: Verify all three handlers still call `child.kill()` after `kill_process_tree`

### Unit Tests

- Code review: verify `send_shutdown_request(port)` is called before `kill_process_tree(pid)` in all three handlers
- Code review: verify `std::thread::sleep` is called between shutdown request and force kill
- Code review: verify `port` is captured from locked state in all three handlers
- Code review: verify the `was_running` guard is present in all three handlers

### Property-Based Tests

- Property-based testing is not practical for this fix because the code under test involves OS-level process management (curl/PowerShell subprocesses, process tree killing) and Tauri runtime event handlers that cannot be easily isolated or mocked in a PBT framework.
- The correctness properties are instead validated through code review and manual integration testing.

### Integration Tests

- Close the app via window close button → verify backend logs show `POST /shutdown` received → verify lifecycle hooks fire
- Quit the app via Cmd+Q → verify backend logs show `POST /shutdown` received → verify lifecycle hooks fire
- Close the app when backend is already dead → verify no crash, no hang, clean exit
- Use `stop_backend` from UI after fix → verify it still works identically to before
