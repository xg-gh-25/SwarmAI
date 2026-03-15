# Requirements Document

## Introduction

The SwarmAI backend executes four session lifecycle hooks (DailyActivity extraction, workspace auto-commit, distillation trigger, evolution maintenance) at session close and application shutdown. Currently, `disconnect_all()` still runs hooks inline on the critical shutdown path, risking Tauri's 3-second kill deadline. The `BackgroundHookExecutor` infrastructure exists and is partially wired (`_cleanup_session` and `_extract_activity_early` use it), but `disconnect_all()` has not been migrated, the executor is not instantiated at startup, and the `WorkspaceAutoCommitHook` is not receiving the shared `git_lock`.

The slow operations are git subprocess calls (`WorkspaceAutoCommitHook`, 2–10s), file I/O with `fcntl.flock` locking (`DistillationTriggerHook` on MEMORY.md, 1–5s; `EvolutionMaintenanceHook` on EVOLUTION.md, 1–3s). DailyActivity extraction is fast (~1s) — it uses `SummarizationPipeline` which is pure regex/heuristic extraction with no Bedrock or LLM calls.

This feature completes the decoupling by wiring `BackgroundHookExecutor` at startup, migrating `disconnect_all()` to use it, passing the shared `git_lock` to `WorkspaceAutoCommitHook`, and addressing race conditions around `activity_extracted` flag semantics, HookContext snapshot timing, and concurrent git index.lock contention.

## Glossary

- **Agent_Manager**: The `AgentManager` class in `backend/core/agent_manager.py` that manages session lifecycle, SDK client connections, and hook orchestration.
- **Hook_Manager**: The `SessionLifecycleHookManager` class in `backend/core/session_hooks.py` that registers and sequentially executes lifecycle hooks with per-hook timeouts (30s default).
- **Background_Hook_Executor**: The `BackgroundHookExecutor` class in `backend/core/session_hooks.py` that spawns hook execution as fire-and-forget `asyncio.Task`s, tracks them in a `_pending` set, and provides `drain()` for graceful shutdown. Already defined but not yet instantiated at startup.
- **HookContext**: Frozen dataclass (`session_id`, `agent_id`, `message_count`, `session_start_time`, `session_title`) built from `_active_sessions` info + async DB queries. Passed to all hooks as an immutable snapshot.
- **Session_Resource_Cleanup**: The set of operations that free session-owned resources: popping the session from `_active_sessions`, calling `wrapper.__aexit__()` to disconnect the SDK client, removing the permission queue, clearing session locks, clearing approved commands, and removing system prompt metadata.
- **Hook_Execution**: The sequential invocation of all registered `SessionLifecycleHook` instances in registration order.
- **DailyActivityExtractionHook**: First registered hook. Uses `SummarizationPipeline` (regex/heuristic, no LLM) to extract conversation summaries into DailyActivity markdown files. Fast (~1s).
- **WorkspaceAutoCommitHook**: Second registered hook. Runs `git add -A` + `git commit` via subprocess. Accepts an optional `asyncio.Lock` (`git_lock`) to serialize git operations. Slow (2–10s).
- **DistillationTriggerHook**: Third registered hook. Scans DailyActivity files and writes to MEMORY.md via `locked_read_modify_write()` (`fcntl.flock`). Medium (1–5s).
- **EvolutionMaintenanceHook**: Fourth registered hook. Scans EVOLUTION.md entries and performs deprecation/pruning via `locked_field_modify()` and manual `fcntl.flock`. Medium (1–3s).
- **Timeout_Envelope**: A total wall-clock budget applied to the entire background hook task via `asyncio.wait_for`, distinct from the existing per-hook timeout.
- **Shutdown_Endpoint**: The `POST /shutdown` FastAPI route in `backend/main.py` called by Tauri before force-killing the sidecar process (3-second grace period).
- **Idle_Cleanup_Loop**: The `_cleanup_stale_sessions_loop()` coroutine that checks every 60s for idle sessions: activity extraction at 30 min, full cleanup at 12 h.
- **Critical_Path**: Any code path whose latency directly affects user-visible responsiveness: shutdown response time, session cleanup duration, or idle-loop iteration time.

## Requirements

### Requirement 1: Wire BackgroundHookExecutor at startup

**User Story:** As a developer, I want the BackgroundHookExecutor to be instantiated and injected at startup, so that all hook execution paths use the background task model instead of inline execution.

#### Acceptance Criteria

1. WHEN the application starts, THE Agent_Manager SHALL receive a Background_Hook_Executor instance via `set_hook_executor()` in `main.py`, after hook registration is complete.
2. WHEN the Background_Hook_Executor is created, THE Background_Hook_Executor SHALL expose its `git_lock` property so that `WorkspaceAutoCommitHook` can be constructed with the shared lock.
3. WHEN `WorkspaceAutoCommitHook` is registered, THE application SHALL pass the Background_Hook_Executor's `git_lock` to the hook constructor, serializing git operations across concurrent hook tasks.
4. THE Background_Hook_Executor SHALL be the sole mechanism for spawning hook tasks in production; inline fallback paths SHALL only execute when `_hook_executor` is None (test/error scenarios).

### Requirement 2: Decouple hooks from session cleanup

**User Story:** As a user, I want session resource cleanup to complete immediately when a session closes, so that slow hooks (git operations, file I/O with locking) never delay freeing SDK clients or block subsequent session operations.

#### Acceptance Criteria

1. WHEN a session is cleaned up via `_cleanup_session()`, THE Agent_Manager SHALL build the HookContext snapshot (including async DB queries) BEFORE popping the session from `_active_sessions`, because `_build_hook_context()` reads `info.get("agent_id")` from the session dict.
2. WHEN a session is cleaned up via `_cleanup_session()`, THE Agent_Manager SHALL complete Session_Resource_Cleanup after building the HookContext but before the background hook task accesses any session state.
3. WHEN `_cleanup_session()` is called with `skip_hooks=False`, THE Agent_Manager SHALL spawn Hook_Execution as a background task via `Background_Hook_Executor.fire()` and return control to the caller immediately.
4. THE background hook task SHALL receive the pre-built HookContext as its only session state. The task SHALL NOT reference `_active_sessions`, `_session_locks`, `_clients`, permission queues, or any per-session state that is cleaned up by Session_Resource_Cleanup.
5. IF `_build_hook_context()` fails (DB query error), THEN THE Agent_Manager SHALL log the error and proceed with Session_Resource_Cleanup without spawning a background hook task.

### Requirement 3: Decouple hooks from shutdown

**User Story:** As a user, I want the shutdown endpoint to return within Tauri's 3-second grace period, so that SDK clients are always cleanly disconnected before the sidecar is force-killed.

#### Acceptance Criteria

1. WHEN `disconnect_all()` is called, THE Agent_Manager SHALL build HookContext snapshots for all active sessions, then spawn background hook tasks via `Background_Hook_Executor.fire()` for each session, instead of executing hooks inline.
2. WHEN `disconnect_all()` is called, THE Agent_Manager SHALL complete Session_Resource_Cleanup for all sessions without awaiting hook completion.
3. WHEN `disconnect_all()` is called and a Background_Hook_Executor is available, THE Agent_Manager SHALL call `Background_Hook_Executor.drain()` with a bounded timeout (default 5 seconds) to give hooks a best-effort chance to complete before the process is killed.
4. WHEN `disconnect_all()` completes Session_Resource_Cleanup for all sessions, THE Agent_Manager SHALL finish within 2 seconds under normal conditions, leaving at least 1 second of Tauri's 3-second grace period for `drain()` and process teardown.
5. IF `drain()` times out with tasks still running, THEN THE Background_Hook_Executor SHALL cancel remaining tasks and return immediately. Cancelled hooks are designed to be idempotent and will retry on next session or app launch.
6. IF a background hook task is still running when the process is terminated, THEN THE Hook_Manager SHALL treat the interruption as a non-error condition (no corruption of persistent state beyond the incomplete operation).

### Requirement 4: Decouple activity extraction from the idle-cleanup loop

**User Story:** As a user, I want the idle-session cleanup loop to remain responsive even when a DailyActivity extraction is slow or errors, so that other idle sessions are still checked on schedule.

#### Acceptance Criteria

1. WHEN the Idle_Cleanup_Loop triggers early DailyActivity extraction for an idle session (30 min), THE Agent_Manager SHALL spawn the extraction as a background task via `Background_Hook_Executor.fire_single()` instead of awaiting it inline.
2. WHEN the Idle_Cleanup_Loop triggers full cleanup for a stale session (12 h), THE Agent_Manager SHALL build the HookContext, spawn hooks as a background task, then complete Session_Resource_Cleanup inline.
3. IF a background hook task spawned by the Idle_Cleanup_Loop raises an exception, THEN THE Idle_Cleanup_Loop SHALL continue processing remaining sessions without interruption.
4. THE Idle_Cleanup_Loop SHALL process each session's cleanup check within 1 second (excluding background hook task execution time), ensuring the 60-second loop cadence is maintained.

### Requirement 5: Background hook task lifecycle management

**User Story:** As a developer, I want background hook tasks to be tracked and bounded, so that runaway hooks do not leak resources or accumulate unbounded.

#### Acceptance Criteria

1. THE Background_Hook_Executor SHALL track all in-flight background hook tasks in its `_pending` set and automatically remove completed tasks via `done_callback`.
2. THE Background_Hook_Executor SHALL enforce the existing per-hook timeout (30 seconds default via `SessionLifecycleHookManager._timeout`) within each background task, in addition to any outer timeout on `drain()`.
3. WHEN a background hook task completes (success or failure), THE Background_Hook_Executor SHALL remove the task from the `_pending` set automatically via the `add_done_callback` pattern.
4. THE Background_Hook_Executor SHALL name each task with a descriptive prefix (e.g., `hooks-{session_id[:8]}`) for debuggability in asyncio task dumps.

### Requirement 6: Hook error isolation

**User Story:** As a user, I want hook failures to be completely invisible to my chat experience, so that a crashing git commit or failed file I/O never surfaces as an error in my session.

#### Acceptance Criteria

1. IF a hook within a background hook task raises an exception, THEN THE Background_Hook_Executor SHALL log the error with the hook name, session ID, and exception details, and continue executing subsequent hooks in registration order.
2. IF all hooks within a background hook task fail, THEN THE Background_Hook_Executor SHALL log a summary warning and remove the task from the `_pending` set without propagating errors to any user-facing code path.
3. IF a background hook task is cancelled (via `drain()` or process termination), THEN THE Background_Hook_Executor SHALL handle `asyncio.CancelledError` gracefully without logging it as an unexpected error.
4. THE Background_Hook_Executor SHALL preserve the existing per-hook timeout (30 seconds default) within each background task, so that a single hung hook does not block subsequent hooks in the same task.

### Requirement 7: Observability of background hook execution

**User Story:** As a developer, I want visibility into background hook execution outcomes, so that I can diagnose issues without hooks being silently lost.

#### Acceptance Criteria

1. WHEN a background hook task completes successfully, THE Background_Hook_Executor SHALL log a summary message including the session ID and total execution duration.
2. WHEN a background hook task is cancelled or times out, THE Background_Hook_Executor SHALL log a warning including the session ID, elapsed time, and which hook was active at cancellation.
3. THE Agent_Manager SHALL expose the count of in-flight background hook tasks (via `Background_Hook_Executor.pending_count`) in the existing `/health` endpoint response, so that operators can detect hook task accumulation.
4. WHEN `disconnect_all()` is called, THE Agent_Manager SHALL log the number of in-flight background hook tasks before calling `drain()`, and log the `(completed, cancelled)` counts returned by `drain()`.

### Requirement 8: Preserve hook execution ordering and skip-if-extracted semantics

**User Story:** As a developer, I want the existing hook ordering guarantees and activity-extracted skip logic to be preserved in the background execution model, so that hook dependencies remain correct.

#### Acceptance Criteria

1. THE background hook task SHALL execute hooks in registration order (DailyActivityExtractionHook → WorkspaceAutoCommitHook → DistillationTriggerHook → EvolutionMaintenanceHook), matching the current sequential execution contract.
2. WHEN the `activity_extracted` flag is set for a session, THE Agent_Manager SHALL pass `skip_hooks=["daily_activity_extraction"]` to `Background_Hook_Executor.fire()`, preserving the current skip-if-extracted behavior.
3. THE background hook task SHALL pass the same frozen HookContext instance to all hooks within a single task, preserving the current immutable-context contract.
4. WHEN `disconnect_all()` fires hooks for a session where `activity_extracted` is True, THE Agent_Manager SHALL skip the DailyActivityExtractionHook via the `skip_hooks` parameter, matching the existing behavior in `_cleanup_session()`.

### Requirement 9: Race condition prevention

**User Story:** As a developer, I want explicit safeguards against race conditions introduced by background hook execution, so that concurrent operations do not corrupt state or cause duplicate work.

#### Acceptance Criteria

##### 9a: HookContext self-containment

1. THE HookContext dataclass SHALL remain the sole carrier of session metadata for background hook tasks. Background hook tasks SHALL NOT read from `_active_sessions`, `_session_locks`, `_clients`, or any per-session dict that is mutated or removed by Session_Resource_Cleanup.
2. WHEN `_cleanup_session()` builds a HookContext, THE Agent_Manager SHALL call `_build_hook_context()` (which performs async DB queries for `message_count` and `session_title`) BEFORE calling `_active_sessions.pop()`, because `_build_hook_context()` reads `info.get("agent_id")` from the session dict.
3. IF `_build_hook_context()` raises an exception (DB unavailable), THEN THE Agent_Manager SHALL log the error and proceed with Session_Resource_Cleanup without spawning a background hook task, rather than leaving the session in a half-cleaned state.

##### 9b: File locking invariants preserved

4. THE DistillationTriggerHook SHALL continue to use `locked_read_modify_write()` from `scripts/locked_write.py` (which uses `fcntl.flock`) for all MEMORY.md writes. This existing mechanism is safe for concurrent access from background hook tasks and active agent sessions.
5. THE EvolutionMaintenanceHook SHALL continue to use `locked_field_modify()` and manual `fcntl.flock` for all EVOLUTION.md writes. No new locking mechanisms SHALL be introduced for the decoupling change.
6. THE DailyActivityExtractionHook SHALL continue to use `write_daily_activity()` for DailyActivity file writes. DailyActivity files use OS `O_APPEND` semantics and do not require explicit locking.

##### 9c: Git index.lock contention

7. THE WorkspaceAutoCommitHook SHALL use the shared `git_lock` (`asyncio.Lock`) provided by Background_Hook_Executor to serialize git operations across concurrent hook tasks from different sessions closing simultaneously.
8. IF `WorkspaceAutoCommitHook._smart_commit()` encounters a `.git/index.lock` held by another process (e.g., an active agent session running git via Bash tool), THEN THE WorkspaceAutoCommitHook SHALL log a warning and skip the commit rather than waiting indefinitely or failing loudly. This is an existing limitation not made worse by the decoupling.
9. THE WorkspaceAutoCommitHook SHALL continue to call `_cleanup_stale_git_lock()` before each commit attempt to handle stale locks from previous crashes.

##### 9d: activity_extracted flag semantics with background tasks

10. WHEN the Idle_Cleanup_Loop spawns a background DailyActivity extraction task, THE Agent_Manager SHALL set `activity_extracted = True` BEFORE spawning the task (current behavior), to prevent re-entry from the next 60-second loop iteration.
11. WHEN a background DailyActivity extraction task fails, THE Agent_Manager SHALL NOT reset `activity_extracted` to False. The flag means "extraction was initiated" not "extraction completed". The next full cleanup (2h TTL) will handle the session regardless.
12. WHEN a user sends a new message to a session (via `_get_active_client()`), THE Agent_Manager SHALL reset `activity_extracted = False` (current behavior), so that new activity after the user resumes gets captured in the next idle period.
13. THE `activity_extracted` flag SHALL NOT be modified by the background hook task itself. Only the Idle_Cleanup_Loop (set to True) and `_get_active_client()` (reset to False) SHALL modify the flag.

##### 9e: No duplicate background tasks for the same session

14. THE Background_Hook_Executor SHALL allow multiple concurrent tasks for different sessions (this is the normal case when multiple sessions close around the same time).
15. WHEN `_cleanup_session()` is called for a session that already has a pending background hook task (e.g., from an earlier idle-loop extraction), THE Agent_Manager SHALL still spawn the full hook task. The per-hook skip logic (`skip_hooks=["daily_activity_extraction"]` when `activity_extracted` is True) prevents duplicate DailyActivity extraction. The `git_lock` prevents concurrent git operations. The `fcntl.flock` prevents concurrent MEMORY.md/EVOLUTION.md corruption.

##### 9f: Background tasks and active streaming sessions

16. THE Idle_Cleanup_Loop SHALL only trigger early DailyActivity extraction for sessions that have been idle for `ACTIVITY_IDLE_SECONDS` (30 min), as determined by the `last_used` timestamp. This naturally excludes sessions that are actively streaming, because `_get_active_client()` updates `last_used` on every user message.
17. THE Idle_Cleanup_Loop SHALL only trigger full cleanup for sessions idle for `SESSION_TTL_SECONDS` (2 h). A session that is actively streaming will never meet this threshold.
