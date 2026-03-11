# Requirements Document

## Introduction

The SwarmAI desktop app (Tauri 2.0 + Python FastAPI sidecar) has a shutdown timing problem. When the user closes the app, Tauri calls `graceful_shutdown_and_kill()` which sends `POST /shutdown` to the backend, waits a hard 3 seconds, then force-kills the process tree (SIGTERM → 100ms → SIGKILL).

The backend's `disconnect_all()` fires session lifecycle hooks as background tasks, disconnects SDK clients, then calls `drain(timeout=2.0)` to give hooks best-effort completion time. The four hooks run in order: DailyActivityExtractionHook (~1s, regex/heuristic, NOT idempotent), WorkspaceAutoCommitHook (2–10s, git ops, idempotent), DistillationTriggerHook (1–5s, file I/O with fcntl.flock, idempotent), EvolutionMaintenanceHook (1–3s, file I/O, idempotent).

The problem manifests in several edge cases:

1. **Slow curl delivery**: If curl takes 1–2s to connect (backend under load), `disconnect_all()` starts late, and `drain(2s)` gets squeezed or cut off entirely by Tauri's 3s kill.
2. **Multiple sessions**: `disconnect_all()` loops through all `_active_sessions` sequentially, building HookContext (async DB queries) for each. With 6+ active tabs, Phase 1 can take 300–600ms, eating into drain time.
3. **DailyActivity data loss**: DailyActivity extraction is the only non-idempotent hook. If cancelled during drain, that session's summary is permanently lost. With the current 2s drain timeout and multiple sessions, DailyActivity hooks for later sessions may never start.

The prior `hook-execution-decoupling` spec already moved hooks to background tasks and wired `BackgroundHookExecutor`. This spec addresses the remaining timing budget problem by increasing the Tauri grace period, increasing the backend drain timeout, prioritizing DailyActivity extraction, and adding a shutdown progress indicator.

## Glossary

- **Tauri_Shell**: The Tauri 2.0 desktop shell (`desktop/src-tauri/src/lib.rs`) that manages the Python sidecar lifecycle, including startup, health polling, and shutdown via `graceful_shutdown_and_kill()`.
- **Graceful_Shutdown_Function**: The `graceful_shutdown_and_kill()` function in `lib.rs` that sends `POST /shutdown`, sleeps for a hard-coded grace period, then calls `kill_process_tree()`.
- **Shutdown_Endpoint**: The `POST /shutdown` FastAPI route in `backend/main.py` that calls `agent_manager.disconnect_all()` and returns `{"status": "shutting_down"}`.
- **Agent_Manager**: The `AgentManager` class in `backend/core/agent_manager.py` that manages session lifecycle, SDK client connections, and hook orchestration.
- **Background_Hook_Executor**: The `BackgroundHookExecutor` class in `backend/core/session_hooks.py` that spawns hook execution as fire-and-forget `asyncio.Task`s and provides `drain()` for graceful shutdown.
- **DailyActivityExtractionHook**: First registered hook. Uses `SummarizationPipeline` (regex/heuristic, no LLM) to extract conversation summaries. Fast (~1s) but NOT idempotent — cancelled extraction means permanent data loss.
- **WorkspaceAutoCommitHook**: Second registered hook. Runs `git add -A` + `git commit` via subprocess. Idempotent (2–10s).
- **DistillationTriggerHook**: Third registered hook. Scans DailyActivity files and writes to MEMORY.md via `locked_read_modify_write()`. Idempotent (1–5s).
- **EvolutionMaintenanceHook**: Fourth registered hook. Scans EVOLUTION.md entries and performs deprecation/pruning. Idempotent (1–3s).
- **Grace_Period**: The hard-coded `thread::sleep` duration in `graceful_shutdown_and_kill()` between sending the shutdown request and force-killing the process tree. Currently 3 seconds.
- **Drain_Timeout**: The timeout passed to `BackgroundHookExecutor.drain()` during shutdown. Currently 2 seconds.
- **Session_Resource_Cleanup**: The set of operations that free session-owned resources: popping from `_active_sessions`, disconnecting SDK client, removing permission queue, clearing locks.
- **Kill_Process_Tree**: The platform-specific function that sends SIGTERM → 100ms → SIGKILL (Unix) or taskkill /F /T (Windows) to the sidecar process tree.

## Requirements

### Requirement 1: Increase Tauri grace period

**User Story:** As a user, I want the app to wait long enough after requesting shutdown so that the backend has sufficient time to complete critical hooks (especially DailyActivity extraction) before being force-killed.

#### Acceptance Criteria

1. WHEN `graceful_shutdown_and_kill()` sends the shutdown request, THE Tauri_Shell SHALL wait 10 seconds (increased from 3 seconds) before calling Kill_Process_Tree, giving the backend sufficient time for SDK disconnect, hook execution, and drain.
2. THE Tauri_Shell SHALL use a named constant or clearly documented value for the Grace_Period duration, replacing the current inline `Duration::from_secs(3)`.
3. WHEN the shutdown request (curl/PowerShell) times out or fails, THE Tauri_Shell SHALL still wait the full Grace_Period before force-killing, because the backend may have received the request but the response was lost.
4. THE `stop_backend` Tauri command SHALL increase its post-shutdown sleep from 2 seconds to 5 seconds, keeping it proportional to the new Grace_Period for manual stop operations.

### Requirement 2: Increase backend drain timeout

**User Story:** As a developer, I want the backend drain timeout to be large enough that all four hooks have a realistic chance of completing for multiple sessions, so that DailyActivity extraction is rarely cancelled.

#### Acceptance Criteria

1. WHEN `disconnect_all()` calls `Background_Hook_Executor.drain()`, THE Agent_Manager SHALL pass a timeout of 8 seconds (increased from 2 seconds), giving hooks realistic completion time within the 10-second Tauri Grace_Period.
2. THE Agent_Manager SHALL complete Session_Resource_Cleanup (Phase 1: build HookContexts, fire background tasks, disconnect SDK clients) within 2 seconds under normal conditions, leaving at least 8 seconds for drain.
3. IF `drain()` times out with tasks still running, THEN THE Background_Hook_Executor SHALL cancel remaining tasks and return immediately, as the current behavior already provides.
4. THE Shutdown_Endpoint SHALL log the total time spent in `disconnect_all()` (Phase 1 + drain) so that timing budget violations are observable.

### Requirement 3: Prioritize DailyActivity extraction during shutdown

**User Story:** As a user, I want my DailyActivity summaries to be preserved even when the app is shutting down, so that I never lose a session's work summary due to shutdown timing.

#### Acceptance Criteria

1. WHEN `disconnect_all()` fires background hook tasks for each session, THE Agent_Manager SHALL fire DailyActivity extraction tasks first (before other hooks) for all sessions, so that the non-idempotent hook gets maximum drain time.
2. WHEN `disconnect_all()` is called with multiple active sessions, THE Agent_Manager SHALL fire all DailyActivity extraction tasks concurrently (one per session) as the first batch, rather than sequentially processing all hooks per session.
3. WHEN all DailyActivity extraction tasks have completed or been individually timed out, THE Agent_Manager SHALL fire the remaining idempotent hooks (WorkspaceAutoCommit, Distillation, Evolution) as a second batch.
4. THE Agent_Manager SHALL enforce a per-session DailyActivity extraction timeout of 5 seconds. IF a single session's extraction exceeds 5 seconds, THEN THE Agent_Manager SHALL cancel that extraction and proceed with remaining sessions.
5. THE Agent_Manager SHALL enforce a global DailyActivity extraction phase timeout of 8 seconds. IF the total DailyActivity phase exceeds 8 seconds (e.g., 6+ sessions each taking close to 5s), THEN THE Agent_Manager SHALL cancel remaining extractions and proceed to the idempotent hooks phase.
6. WHEN a DailyActivity extraction is cancelled due to timeout, THE Agent_Manager SHALL log a warning including the session ID and elapsed time, so that data loss is observable.

### Requirement 4: Parallel HookContext construction during shutdown

**User Story:** As a developer, I want HookContext construction for multiple sessions to happen concurrently during shutdown, so that sequential DB queries for 6+ sessions do not consume a significant portion of the timing budget.

#### Acceptance Criteria

1. WHEN `disconnect_all()` builds HookContext snapshots for multiple active sessions, THE Agent_Manager SHALL use `asyncio.gather()` to build all HookContexts concurrently, instead of the current sequential loop.
2. IF a HookContext build fails for one session (DB query error), THEN THE Agent_Manager SHALL log the error and skip that session's hooks, without blocking HookContext construction for other sessions.
3. THE Agent_Manager SHALL complete HookContext construction for all sessions within 1 second under normal conditions (DB is local SQLite, queries are simple COUNT operations).

### Requirement 5: Shutdown progress indicator

**User Story:** As a user, I want to see a visual indicator that the app is shutting down, so that the longer shutdown time (up to 10s) does not feel like the app is frozen or unresponsive.

#### Acceptance Criteria

1. WHEN the user closes the app (window close, Cmd+Q, or system quit), THE Tauri_Shell SHALL emit a `shutdown-started` event to the frontend before sending the shutdown request to the backend.
2. WHEN the frontend receives the `shutdown-started` event, THE Frontend SHALL display a non-interactive overlay with a "Shutting down..." message and a subtle progress indicator (spinner or progress bar).
3. THE shutdown overlay SHALL appear within 100ms of the close action, before any backend communication begins.
4. THE shutdown overlay SHALL prevent user interaction with the underlying UI (modal overlay), so that the user does not attempt to start new sessions or send messages during shutdown.
5. IF the shutdown completes before the Grace_Period expires (backend responds quickly), THE Tauri_Shell SHALL proceed to force-kill immediately rather than waiting the full Grace_Period, reducing perceived shutdown time.

### Requirement 6: Early shutdown completion (fast path)

**User Story:** As a user, I want the app to close as fast as possible when there are no active sessions or hooks to run, so that the 10-second grace period is only used when needed.

#### Acceptance Criteria

1. WHEN the Shutdown_Endpoint returns a response to Tauri (curl succeeds), THE Tauri_Shell SHALL proceed to force-kill immediately instead of sleeping for the full Grace_Period, because the backend has already completed its cleanup.
2. WHEN `disconnect_all()` is called with zero active sessions, THE Agent_Manager SHALL return immediately without calling `drain()`, and the Shutdown_Endpoint SHALL respond within 100ms.
3. WHEN `disconnect_all()` is called and `drain()` completes before its timeout (all hooks finished), THE Shutdown_Endpoint SHALL respond immediately, allowing Tauri to proceed with force-kill.
4. THE Tauri_Shell SHALL use the curl/PowerShell timeout as the maximum wait time (set to 10 seconds to match the Grace_Period), and SHALL proceed to force-kill as soon as the HTTP response is received or the timeout expires.
5. WHEN the fast path is taken (curl returns quickly), THE Tauri_Shell SHALL still call Kill_Process_Tree as a safety net, but SHALL skip the Grace_Period sleep.

### Requirement 7: Shutdown timing observability

**User Story:** As a developer, I want detailed timing logs for the entire shutdown sequence, so that I can diagnose timing budget violations and DailyActivity data loss incidents.

#### Acceptance Criteria

1. WHEN `disconnect_all()` is called, THE Agent_Manager SHALL log the number of active sessions, the number of sessions with `activity_extracted=True`, and the total number of pending hook tasks before starting Phase 1.
2. WHEN Phase 1 (HookContext construction + hook firing + SDK disconnect) completes, THE Agent_Manager SHALL log the elapsed time for Phase 1.
3. WHEN `drain()` completes, THE Agent_Manager SHALL log the elapsed time for drain, the count of completed tasks, and the count of cancelled tasks.
4. WHEN the Shutdown_Endpoint handler returns, THE Shutdown_Endpoint SHALL log the total elapsed time from endpoint entry to response.
5. WHEN a DailyActivity extraction completes during shutdown drain, THE Background_Hook_Executor SHALL log the session ID and extraction duration.
6. WHEN a DailyActivity extraction is cancelled during shutdown drain, THE Background_Hook_Executor SHALL log a warning with the session ID and elapsed time at cancellation.

### Requirement 8: Preserve existing shutdown correctness

**User Story:** As a developer, I want the shutdown timing changes to be backward-compatible with the existing hook-execution-decoupling architecture, so that no regressions are introduced.

#### Acceptance Criteria

1. THE Agent_Manager SHALL continue to use `Background_Hook_Executor.fire()` for spawning hook tasks during shutdown, preserving the existing fire-and-forget model.
2. THE Agent_Manager SHALL continue to pass `skip_hooks=["daily_activity_extraction"]` when `activity_extracted=True` for a session, preserving the existing skip-if-extracted semantics.
3. THE Agent_Manager SHALL continue to complete Session_Resource_Cleanup (SDK disconnect, permission queue removal, lock cleanup) before awaiting drain, preserving the existing resource-cleanup-first guarantee.
4. THE Background_Hook_Executor SHALL continue to enforce per-hook timeouts (30 seconds default) within background tasks, preserving the existing per-hook timeout contract.
5. THE `graceful_shutdown_and_kill()` function SHALL remain double-fire safe: if `backend.running` is already false, the function SHALL skip the shutdown request and Grace_Period sleep, proceeding directly to force-kill.
6. THE Kill_Process_Tree function SHALL continue to be called as a safety net after the Grace_Period (or fast-path completion), regardless of whether the shutdown request succeeded.

### Requirement 9: Platform-consistent shutdown behavior

**User Story:** As a user on any platform (macOS, Linux, Windows), I want the shutdown timing improvements to work consistently, so that DailyActivity preservation is not platform-dependent.

#### Acceptance Criteria

1. THE Tauri_Shell SHALL apply the same Grace_Period (10 seconds) on all platforms (macOS, Linux, Windows).
2. THE Tauri_Shell SHALL set the curl timeout (`-m` flag on Unix) and PowerShell timeout (`-TimeoutSec` on Windows) to match the Grace_Period, so that the HTTP request has the full budget to complete.
3. WHEN the shutdown request tool (curl or PowerShell) returns successfully, THE Tauri_Shell SHALL proceed to force-kill immediately on all platforms, implementing the fast-path behavior consistently.
4. THE `send_shutdown_request()` function SHALL return a boolean indicating whether the request succeeded, so that `graceful_shutdown_and_kill()` can decide whether to use the fast path or wait the full Grace_Period.
