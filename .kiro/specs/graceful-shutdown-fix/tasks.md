# Implementation Tasks

## Tauri Shell Changes (lib.rs)

- [x] 1. Add shutdown timing constants to lib.rs
  - [x] 1.1 Add `const SHUTDOWN_GRACE_SECONDS: u64 = 10` with doc comment explaining timing budget
  - [x] 1.2 Add `const STOP_BACKEND_SLEEP_SECONDS: u64 = 5` with doc comment
  - [x] 1.3 Replace inline `Duration::from_secs(3)` in `graceful_shutdown_and_kill` with named constant
  - Validates: Req 1.1, 1.2

- [x] 2. Change `send_shutdown_request` to return bool
  - [x] 2.1 Unix (curl): change return type to `-> bool`, change `-m 3` to `-m 10`, return `true` on `output.status.success()`, `false` otherwise
  - [x] 2.2 Windows (PowerShell): change return type to `-> bool`, change `-TimeoutSec 3` to `-TimeoutSec 10`, return `true` on success, `false` otherwise
  - Validates: Req 9.2, 9.4

- [x] 3. Update `graceful_shutdown_and_kill` with fast-path logic
  - [x] 3.1 Capture `send_shutdown_request` return value in `let success = send_shutdown_request(port)`
  - [x] 3.2 Remove hard-coded `thread::sleep(Duration::from_secs(3))` — curl timeout (10s) serves as grace period on timeout path, fast path skips sleep
  - [x] 3.3 Ensure `kill_process_tree(pid)` always called unconditionally after shutdown request path (safety net)
  - Validates: Req 1.1, 1.3, 6.1, 6.5, 8.5, 8.6

- [x] 4. Emit `shutdown-started` event in window close handlers
  - [x] 4.1 In `WindowEvent::Destroyed` handler: add `let _ = app_handle.emit("shutdown-started", ());` BEFORE `graceful_shutdown_and_kill()` (must be before `block_on` to avoid event loop blocking)
  - [x] 4.2 In `RunEvent::Exit` handler: add same emit call before `graceful_shutdown_and_kill()`
  - [x] 4.3 In `RunEvent::ExitRequested` handler: add same emit call before `graceful_shutdown_and_kill()`
  - Validates: Req 5.1, 5.3

- [x] 5. Update `stop_backend` command with new constants and fast-path
  - [x] 5.1 Capture `send_shutdown_request` return value
  - [x] 5.2 If returned `false`, sleep `STOP_BACKEND_SLEEP_SECONDS` (5s); if `true`, skip sleep (fast path)
  - [x] 5.3 Preserve existing `kill_process_tree` and `child.kill()` calls unchanged
  - Validates: Req 1.4, 6.1

## Backend Changes (agent_manager.py)

- [x] 6. Restructure `disconnect_all()` Phase 0 + 1a — logging and parallel HookContext
  - [x] 6.1 Snapshot `list(self._active_sessions.items())` at the start
  - [x] 6.2 Phase 0: Log session count, count with `activity_extracted=True`, pending hook tasks
  - [x] 6.3 Replace sequential loop with `asyncio.gather(*[self._build_hook_context(sid, info) for sid, info in sessions], return_exceptions=True)`
  - [x] 6.4 Filter results: skip sessions where build returned Exception, log errors
  - [x] 6.5 Log Phase 1a elapsed time
  - Validates: Req 4.1, 4.2, 4.3, 7.1, 7.2

- [x] 7. Restructure `disconnect_all()` Phase 1b — inline DailyActivity extraction
  - [x] 7.1 Find DA extraction hook by name (reuse pattern from `_extract_activity_early`)
  - [x] 7.2 Build DA task list: for each session where `activity_extracted` is not True, create `asyncio.wait_for(da_hook.execute(ctx), timeout=5.0)`
  - [x] 7.3 Run all DA tasks concurrently with global timeout: `await asyncio.wait_for(asyncio.gather(*da_tasks, return_exceptions=True), timeout=8.0)`
  - [x] 7.4 Set `info["activity_extracted"] = True` for sessions whose DA completed successfully
  - [x] 7.5 Log warnings for timed-out/failed DA extractions (session ID + elapsed time)
  - [x] 7.6 Handle `asyncio.TimeoutError` from global 8s timeout — log and proceed to Phase 1c
  - Validates: Req 3.1, 3.2, 3.3, 3.4, 3.5, 3.6

- [x] 8. Restructure `disconnect_all()` Phase 1c/1d — idempotent hooks and cleanup
  - [x] 8.1 Fire idempotent hooks via executor: `self._hook_executor.fire(ctx, skip_hooks=["daily_activity_extraction"])` per session
  - [x] 8.2 Session cleanup AFTER DA extraction: `_cleanup_session(sid, skip_hooks=True)` per session
  - [x] 8.3 Preserve existing transient client cleanup (`self._clients.clear()`)
  - [x] 8.4 Preserve existing cleanup loop cancellation
  - Validates: Req 8.1, 8.2, 8.3

- [x] 9. Restructure `disconnect_all()` Phase 2 — drain with increased timeout
  - [x] 9.1 Change `drain(timeout=2.0)` to `drain(timeout=8.0)`
  - [x] 9.2 Log drain results: elapsed time, completed count, cancelled count
  - [x] 9.3 Preserve existing warning about cancelled DA extraction
  - [x] 9.4 Add zero-sessions fast return: if no active sessions, return immediately without drain
  - Validates: Req 2.1, 2.3, 2.4, 6.2, 7.3

## Backend Changes (main.py)

- [x] 10. Add timing instrumentation to POST /shutdown endpoint
  - [x] 10.1 Wrap `disconnect_all()` with `time.monotonic()` start/end
  - [x] 10.2 Log total elapsed time before returning response
  - Validates: Req 7.4

## Frontend Changes

- [x] 11. Create ShutdownOverlay component
  - [x] 11.1 Create `desktop/src/components/common/ShutdownOverlay.tsx` with module-level JSDoc
  - [x] 11.2 Listen for Tauri `shutdown-started` event using `@tauri-apps/api/event` `listen()`
  - [x] 11.3 Render full-screen modal overlay with "Shutting down..." text and spinner
  - [x] 11.4 CSS: fixed positioning, full viewport, high z-index, pointer-events blocking
  - [x] 11.5 `useEffect` cleanup to unlisten on unmount
  - Validates: Req 5.2, 5.3, 5.4

- [x] 12. Mount ShutdownOverlay in App.tsx
  - [x] 12.1 Import and render `<ShutdownOverlay />` in App.tsx
  - Validates: Req 5.2

## Testing

- [x] 13. Backend unit tests for restructured disconnect_all
  - [x] 13.1 Zero sessions: returns immediately without calling `drain()`
  - [x] 13.2 Drain timeout value: assert `drain()` called with `timeout=8.0`
  - [x] 13.3 DA cancellation logging: mock slow DA (>5s), assert warning log
  - [x] 13.4 Phase 0 logging: assert log contains session count and activity_extracted counts
  - [x] 13.5 Skip-if-extracted: sessions with flag excluded from DA batch, `skip_hooks` passed to `fire()`
  - Validates: Req 2.4, 3.6, 6.2, 7.1, 8.2

- [ ] 14. Backend property-based tests (hypothesis)
  - [ ]* 14.1 Property 3: DA-first ordering — random sessions, assert DA gather completes before any `fire()` calls
  - [ ]* 14.2 Property 4: Per-session DA timeout — random durations, assert >5s cancelled within tolerance
  - [ ]* 14.3 Property 5: Global DA phase timeout — random sessions/durations, assert total ≤ 8s + tolerance
  - [ ]* 14.4 Property 6: Parallel HookContext — random delays, assert total ≈ max(delays) not sum
  - [ ]* 14.5 Property 7: HookContext error isolation — random failures, assert non-failing sessions get hooks
  - [ ]* 14.6 Property 8: Drain early completion — random times < timeout, assert drain returns in ≈ max(times)
  - [ ]* 14.7 Property 9: Skip-if-extracted — random flags, assert extracted sessions excluded from DA batch
  - [ ]* 14.8 Property 10: Cleanup ordering — random sessions, assert cleanup before drain
  - Validates: Properties 3-10 from design

- [ ]* 15. Frontend unit tests (vitest)
  - [ ]* 15.1 ShutdownOverlay renders on `shutdown-started` event
  - [ ]* 15.2 ShutdownOverlay blocks interaction (pointer-events, z-index)
  - [ ]* 15.3 ShutdownOverlay hidden by default
  - Validates: Req 5.2, 5.3, 5.4
