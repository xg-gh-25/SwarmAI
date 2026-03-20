# Requirements: Lightweight Process Lifecycle Watchdog

## Introduction

The SwarmAI backend spawns Claude CLI subprocesses via `SessionUnit`. The Claude SDK already manages its own MCP server child processes — we don't need to track those. The only gap is:

1. **Startup**: Orphaned processes from a previous crash (claude CLI, dev backends) need cleanup. The existing `_reap_orphans()` handles claude and dev backends but misses orphaned pytest processes.
2. **Shutdown**: `disconnect_all()` already kills all alive SessionUnits. Just ensure completeness.
3. **Pytest zombies**: The agent's bash tool can spawn pytest processes that outlive the session. These need tracking and cleanup.

No continuous scanning. No process tree walking. No new classes. Zero impact on chat experience.

## Requirements

### Requirement 1: Track Non-SDK Child Processes

**User Story:** As the backend system, I want to track PIDs of non-SDK child processes (like pytest) spawned during agent sessions, so that they can be cleaned up at shutdown or tab close.

#### Acceptance Criteria

1. WHEN `LifecycleManager` is initialized THEN the system SHALL maintain a `_tracked_child_pids: set[int]` for non-SDK child process PIDs.
2. WHEN a tracked PID's process has already exited by shutdown time THEN the system SHALL silently skip it during cleanup without error (removal happens at shutdown via `_kill_tracked_pids()`, not via continuous liveness checking).
3. WHEN a PID is added to the tracked set THEN the system SHALL log the PID at debug level.
4. WHEN a non-SDK child process (e.g., pytest) is spawned by the agent's bash tool THEN the calling code SHOULD pass the PID to `LifecycleManager.track_pid(pid)` so it is registered for shutdown cleanup.

### Requirement 2: Startup Orphan Reaping for Pytest

**User Story:** As the backend system, I want the startup orphan reaper to also kill orphaned pytest processes from previous sessions, so that zombie pytest processes don't accumulate across restarts.

#### Acceptance Criteria

1. WHEN `LifecycleManager._reap_orphans()` runs at startup THEN the system SHALL also find and kill orphaned pytest processes (ppid=1) in addition to claude CLI and dev backend processes.
2. WHEN an orphaned pytest process is found THEN the system SHALL only kill it if its parent PID is 1 (truly orphaned), to avoid killing the user's own pytest runs.
3. WHEN orphaned pytest cleanup fails THEN the system SHALL log a warning and continue startup normally.
4. WHEN searching for orphaned pytest processes THEN the system SHALL use `pgrep -f pytest` (substring match) to catch both `pytest` and `python -m pytest` invocations, relying on the ppid=1 guard (Req 2.2) as the primary safety mechanism against false positives.

### Requirement 3: Shutdown Cleanup of Tracked PIDs

**User Story:** As the backend system, I want all tracked child PIDs to be killed during app shutdown, so that no pytest or other tracked processes survive app exit.

#### Acceptance Criteria

1. WHEN `LifecycleManager.stop()` is called during shutdown THEN the system SHALL kill all PIDs in `_tracked_child_pids` after cancelling the maintenance loop and draining hooks.
2. WHEN a tracked PID has already exited by shutdown time THEN the system SHALL skip it silently.
3. WHEN killing a tracked PID fails THEN the system SHALL log a debug message and continue with remaining PIDs.
4. WHEN killing tracked PIDs THEN the system SHALL use SIGKILL directly (the app is shutting down — no need for graceful SIGTERM escalation).

### Requirement 4: Zero Impact on Chat Experience

**User Story:** As a user, I want process cleanup to be invisible during normal use, so that my chat experience is never affected.

#### Acceptance Criteria

1. THE system SHALL only perform cleanup at lifecycle boundaries (startup, shutdown) and never during active chat sessions.
2. THE system SHALL NOT add any background scanning, polling, or continuous monitoring of processes.
3. THE system SHALL NOT walk process trees or enumerate child processes during normal operation.
4. WHEN cleanup operations encounter errors THEN the system SHALL never block or delay the chat response path.
