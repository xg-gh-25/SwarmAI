# Bugfix Requirements Document

## Introduction

When the user closes the SwarmAI desktop app (Cmd+Q, window close button, etc.), the Python FastAPI backend sidecar is immediately force-killed via `kill_process_tree()` without first sending a graceful shutdown request. This means the three session lifecycle hooks — `DailyActivityExtractionHook`, `WorkspaceAutoCommitHook`, and `DistillationTriggerHook` — never fire on app close, causing loss of daily activity summaries, uncommitted workspace changes, and skipped distillation checks.

The graceful shutdown path already exists and works correctly in the `stop_backend` Tauri command (`send_shutdown_request(port)` → sleep 2s → `kill_process_tree(pid)`), but the three window close/exit event handlers in `lib.rs` skip it entirely and go straight to `kill_process_tree()`.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the main window is destroyed (`WindowEvent::Destroyed` handler) THEN the system immediately calls `kill_process_tree(pid)` without first sending a graceful shutdown request to the backend, so `disconnect_all()` never executes and all 3 lifecycle hooks are skipped.

1.2 WHEN the application exits (`RunEvent::Exit` handler) THEN the system immediately calls `kill_process_tree(pid)` without first sending a graceful shutdown request to the backend, so `disconnect_all()` never executes and all 3 lifecycle hooks are skipped.

1.3 WHEN exit is requested (`RunEvent::ExitRequested` handler) THEN the system immediately calls `kill_process_tree(pid)` without first sending a graceful shutdown request to the backend, so `disconnect_all()` never executes and all 3 lifecycle hooks are skipped.

### Expected Behavior (Correct)

2.1 WHEN the main window is destroyed (`WindowEvent::Destroyed` handler) AND the backend is running THEN the system SHALL send a graceful shutdown request (`POST /shutdown`) to the backend, wait a bounded period (2-5 seconds) for lifecycle hooks to complete, and then call `kill_process_tree(pid)` as a safety net.

2.2 WHEN the application exits (`RunEvent::Exit` handler) AND the backend is running THEN the system SHALL send a graceful shutdown request (`POST /shutdown`) to the backend, wait a bounded period (2-5 seconds) for lifecycle hooks to complete, and then call `kill_process_tree(pid)` as a safety net.

2.3 WHEN exit is requested (`RunEvent::ExitRequested` handler) AND the backend is running THEN the system SHALL send a graceful shutdown request (`POST /shutdown`) to the backend, wait a bounded period (2-5 seconds) for lifecycle hooks to complete, and then call `kill_process_tree(pid)` as a safety net.

2.4 WHEN any window close/exit handler attempts a graceful shutdown AND the backend is already dead or unreachable THEN the system SHALL fail silently on the shutdown request and fall through to `kill_process_tree(pid)` without hanging or crashing.

2.5 WHEN any window close/exit handler performs graceful shutdown THEN the total added delay on app close SHALL be bounded to no more than 5-8 seconds maximum, including the shutdown request timeout and the post-request sleep.

2.6 WHEN multiple window close/exit handlers fire in sequence during the same app close (e.g., `WindowEvent::Destroyed` followed by `RunEvent::Exit`) THEN only the FIRST handler to acquire the lock and observe `backend.running == true` SHALL perform the graceful shutdown sequence (shutdown request + sleep). Subsequent handlers SHALL observe `backend.running == false` and skip the shutdown request and sleep, proceeding directly to the force-kill safety net.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the `stop_backend` Tauri command is invoked (e.g., from the UI settings) THEN the system SHALL CONTINUE TO perform the existing graceful shutdown sequence (`send_shutdown_request` → sleep 2s → `kill_process_tree`) unchanged.

3.2 WHEN the backend is not running at the time of window close/exit THEN the system SHALL CONTINUE TO skip shutdown logic and exit cleanly without errors or delays.

3.3 WHEN the backend is running on Windows THEN the system SHALL CONTINUE TO use the PowerShell-based `send_shutdown_request` implementation, and when on macOS/Linux SHALL CONTINUE TO use the curl-based implementation.

3.4 WHEN the backend process tree is killed after graceful shutdown THEN the system SHALL CONTINUE TO also call `child.kill()` as a fallback, preserving the existing defense-in-depth cleanup pattern.
