# Requirements Document

## Introduction

Performance optimization of the SwarmAI desktop application restart and startup workflow. The app uses a Tauri 2.0 shell with a React frontend and a Python FastAPI backend sidecar.

### Current Startup Sequence

**Backend (fast path — seed-sourced or returning user):**
1. `_ensure_database_initialized()` — checks/copies seed.db
2. `initialize_database(skip_schema=True)` — connection pool, no DDL
3. `ensure_default_workspace()` — filesystem check + `verify_integrity()` (runs `_cleanup_legacy_content()` on first upgrade)
4. `refresh_builtin_defaults()` — re-scans skills directory, projects skill symlinks, refreshes context files
5. `channel_gateway.startup()` — **BLOCKING** — loads adapters, queries all channels from DB, starts each one sequentially
6. `AppConfigManager.load()` — reads config.json
7. `CmdPermissionManager.load()` — reads cmd_permissions/
8. `CredentialValidator()` — initialized
9. `asyncio.create_task(_prewarm_boto3())` — background boto3 import
10. `agent_manager.configure(...)` — wires injected components
11. `register_tscc_dependencies(...)` — wires TSCC state manager
12. `_startup_complete = True` — health check now returns 200

**Backend (full init path — dev-mode, no seed.db):**
Same as above but step 1–3 replaced with full DDL + migrations (45s timeout), initialization check, and either `run_quick_validation()` or `run_full_initialization()` (creates agent, workspace, skills, MCPs).

**Frontend — BackendStartupOverlay (the "flash/splash page"):**
1. Calls `initializeBackend()` (Tauri command to start sidecar, returns port)
2. Polls `GET /health` every 1s (up to 60 attempts = 60s timeout)
3. Once healthy, fetches `GET /api/system/status`
4. Builds init steps from system status: Database initialized, SwarmAgent ready (agent + skills count + MCP count), Channel gateway started (running flag), Swarm Workspace initialized (config + path)
5. Polls system status every 1s until `checkReadiness()` passes (checks agentReady AND workspaceReady — notably does NOT wait for channel gateway)
6. Once ready: shows all steps with sequential check-mark animation (150ms per step × ~4 steps = ~600ms), then 500ms delay, then 500ms fade-out = ~1.6s of animation before `onReady()` fires
7. `onReady()` sets `isBackendReady = true`, which unmounts overlay and mounts routes

**Frontend — ChatPage mount (after overlay dismisses):**
1. Fires 5 parallel React Query hooks immediately (no `enabled` guards): agents, skills, mcpServers, plugins, chatSessions
2. Tab restore: `restoreFromFile()` → loads `open_tabs.json` → loads session messages for active tab via `chatService.getSessionMessages(sid)` — returns ALL messages, no pagination
3. Shows loading spinner (`isLoadingHistory`) while messages load

**Frontend — ThreeColumnLayout mount:**
1. Renders ExplorerProvider + WorkspaceExplorer
2. WorkspaceExplorer fetches workspace file tree from backend

### Identified Bottlenecks

1. `channel_gateway.startup()` blocks the lifespan even when zero channels exist
2. `refresh_builtin_defaults()` re-scans skills directory synchronously on every startup
3. BackendStartupOverlay animation sequence adds ~1.6s to perceived startup after backend is ready
4. ChatPage fires 5 parallel React Query hooks immediately on mount with no prioritization
5. `GET /chat/sessions/{id}/messages` returns all messages with no pagination
6. `_cleanup_legacy_content()` dispatches one `anyio.to_thread.run_sync()` call per file/directory (up to 15+ dispatches)
7. `initialized` field in SystemStatusResponse requires `channel_gateway.running=True`, but `checkReadiness()` only checks agent + workspace — mismatch between the two readiness concepts
8. System status polling runs every 1s with no backoff, even when only waiting for a single component

## Glossary

- **Backend**: The Python FastAPI sidecar process launched by Tauri, defined in `backend/main.py`
- **Frontend**: The React single-page application rendered inside the Tauri webview
- **Lifespan**: The FastAPI `lifespan` async context manager in `backend/main.py` that runs startup and shutdown logic
- **Channel_Gateway**: The `ChannelGateway` singleton (`backend/channels/gateway.py`) responsible for starting and managing communication channel adapters
- **BackendStartupOverlay**: The React component (`desktop/src/components/BackendStartupOverlay.tsx`) that blocks route rendering until the backend is ready, showing initialization progress steps with animated check marks
- **ChatPage**: The main React page component (`desktop/src/pages/ChatPage.tsx`) that mounts after the overlay dismisses and renders the chat interface
- **WorkspaceExplorer**: The React component that renders the file tree sidebar, fetching the workspace directory structure from the backend on mount
- **Session_Messages_Endpoint**: The `GET /api/chat/sessions/{id}/messages` FastAPI route that returns all messages for a chat session
- **SystemStatus_Endpoint**: The `GET /api/system/status` FastAPI route that returns database health, agent status, channel gateway state, and workspace readiness
- **Tab_Restore**: The process of loading previously open tabs from `open_tabs.json` via `restoreFromFile()` in `useUnifiedTabState`
- **Health_Check**: The `GET /api/health` endpoint that returns healthy only after `_startup_complete = True` in the Backend Lifespan
- **Time_To_Interactive**: The elapsed time from app launch until the user can see and interact with the ChatPage (messages rendered, input enabled)
- **Legacy_Cleanup**: The `_cleanup_legacy_content()` method in `SwarmWorkspaceManager` that removes pre-restructure files and directories on first run
- **Refresh_Builtin_Defaults**: The `initialization_manager.refresh_builtin_defaults()` method that re-scans the skills directory, projects skill symlinks, and refreshes context files on every startup

## Requirements

### Requirement 1: Defer Channel Gateway Startup

**User Story:** As a user, I want the app to start faster when I have no channels configured, so that I reach the chat interface without waiting for channel initialization.

#### Acceptance Criteria

1. WHEN the Backend starts and no channels exist in the database, THE Lifespan SHALL skip calling `channel_gateway.startup()` during the synchronous startup sequence and mark startup as complete without waiting for channel initialization
2. WHEN the Backend starts and one or more channels exist in the database, THE Lifespan SHALL defer `channel_gateway.startup()` to a background task that runs after `_startup_complete` is set to `True`
3. WHILE the Channel_Gateway background startup is in progress, THE Health_Check SHALL return healthy (status 200) so the Frontend can begin loading
4. IF the deferred Channel_Gateway startup fails for one or more channels, THEN THE Backend SHALL log the failure and schedule retries using the existing retry mechanism without affecting the Health_Check status
5. THE Backend SHALL expose the channel gateway readiness state via the SystemStatus_Endpoint as a `startup_state` field with values `"not_started"`, `"starting"`, `"started"`, or `"failed"` so the Frontend can distinguish between gateway states

### Requirement 2: Simplify and Polish BackendStartupOverlay

**User Story:** As a user, I want the splash screen to show simple, friendly progress messages and dismiss quickly, so that startup feels fast and polished rather than technical.

#### Acceptance Criteria

##### Step Simplification

1. THE BackendStartupOverlay SHALL display exactly 3 user-friendly initialization steps (replacing the current 4 parents + 3 children tree):
   - **"Loading your data"** — maps to `database.healthy` from the SystemStatus_Endpoint
   - **"Preparing your agent"** — maps to `agent.ready` from the SystemStatus_Endpoint (absorbs skills count and MCP servers count sub-steps, which are no longer displayed separately)
   - **"Setting up workspace"** — maps to `swarmWorkspace.ready` from the SystemStatus_Endpoint (the workspace path sub-step is no longer displayed)
2. THE BackendStartupOverlay SHALL NOT display the Channel Gateway step, since channel gateway startup is deferred (Requirement 1) and is not a dismissal gate
3. THE BackendStartupOverlay SHALL NOT display technical sub-steps (skills count, MCP servers count, workspace path) — these are internal details not meaningful to end users

##### Visual Polish

4. THE BackendStartupOverlay SHALL use the application's standard UI font (not monospace) for step labels, matching the rest of the app's visual style
5. THE BackendStartupOverlay SHALL use small SVG icons for step status indicators: a filled checkmark circle (success), an animated spinner (in_progress), and a filled error circle (error) — replacing the current text characters (`✓`, `○`, `✗`)
6. THE BackendStartupOverlay SHALL display the app version in muted text below the "SwarmAI" title (e.g., "v0.8.2"), sourced from the health check or system status response

##### Animation Timing

7. WHEN all readiness checks pass (agentReady AND workspaceReady), THE BackendStartupOverlay SHALL complete the step animation sequence in no more than 300ms total (100ms per step × 3 steps)
8. WHEN all readiness checks pass, THE BackendStartupOverlay SHALL apply a fade-out transition of no more than 200ms after a 200ms delay before calling `onReady()` (total animation budget: ~700ms vs current ~2050ms)
9. WHEN the Backend responds healthy AND all readiness checks pass on the first system status poll, THE BackendStartupOverlay SHALL skip the step-by-step animation entirely — show all 3 steps as checked simultaneously and proceed directly to the fade-out transition

##### Dismissal Logic

10. THE BackendStartupOverlay SHALL use `checkReadiness()` (agentReady AND workspaceReady) as the sole dismissal gate, ignoring the `initialized` field from the SystemStatus_Endpoint (resolves the mismatch where `initialized` requires `channel_gateway.running=True`)
11. THE BackendStartupOverlay SHALL preserve the step display for cases where the backend takes more than 2 seconds to become ready, so the user sees meaningful progress feedback during slow startups

### Requirement 3: Prioritize Critical Frontend Queries on Mount

**User Story:** As a user, I want the chat interface to appear quickly after the backend is ready, so that I can start working without waiting for non-essential data to load.

#### Acceptance Criteria

1. WHEN the ChatPage mounts, THE ChatPage SHALL load agents data before loading skills, MCP servers, and plugins data
2. WHEN the ChatPage mounts, THE ChatPage SHALL defer loading of skills, MCP servers, and plugins queries until after the active tab's session messages have been rendered (or the welcome screen is displayed)
3. WHEN the ChatPage mounts, THE ChatPage SHALL load the sessions list query with a lower priority than the agents query and the active tab's session messages
4. THE ChatPage SHALL continue to load all five data sets (agents, skills, mcpServers, plugins, sessions) within the same mount lifecycle, deferring only the timing of non-critical queries
5. IF a deferred query (skills, mcpServers, plugins) fails, THEN THE ChatPage SHALL retry using the existing React Query retry mechanism without blocking the chat interface

### Requirement 4: Paginated Session Message Loading

**User Story:** As a user, I want my chat sessions to load quickly on restart even when they contain hundreds of messages, so that I see the most recent conversation context without waiting for the entire history.

#### Acceptance Criteria

1. THE Session_Messages_Endpoint SHALL accept optional `limit` and `before_id` query parameters for cursor-based pagination
2. WHEN the `limit` parameter is provided, THE Session_Messages_Endpoint SHALL return at most `limit` messages, ordered by creation time descending (most recent first)
3. WHEN the `before_id` parameter is provided, THE Session_Messages_Endpoint SHALL return only messages created before the message with the given ID
4. WHEN neither `limit` nor `before_id` is provided, THE Session_Messages_Endpoint SHALL return all messages for backward compatibility with existing callers
5. WHEN the Tab_Restore process loads messages for the active tab on restart, THE ChatPage SHALL request only the most recent 50 messages using the `limit` parameter
6. WHEN the user scrolls to the top of the message list, THE ChatPage SHALL load the next page of older messages using the `before_id` parameter of the oldest currently displayed message
7. WHILE older messages are being fetched, THE ChatPage SHALL display a loading indicator at the top of the message list
8. THE ChatPage SHALL prepend newly loaded older messages to the existing message list without disrupting the user's current scroll position
9. IF the Session_Messages_Endpoint returns fewer messages than the requested `limit`, THEN THE ChatPage SHALL treat the session's message history as fully loaded and stop requesting additional pages
10. FOR ALL valid session message sets, fetching all messages via paginated requests (limit=50, chaining before_id) SHALL produce the same ordered set as fetching all messages without pagination (round-trip property)

### Requirement 5: Batch Legacy Cleanup Filesystem Operations

**User Story:** As a first-time user upgrading from an older version, I want the legacy cleanup to complete quickly, so that my first startup is not noticeably slower than subsequent startups.

#### Acceptance Criteria

1. WHEN the Legacy_Cleanup runs (marker file absent), THE Legacy_Cleanup SHALL collect all filesystem paths to remove into a single list and execute all removals within a single `anyio.to_thread.run_sync()` call instead of dispatching one thread call per file or directory
2. THE Legacy_Cleanup SHALL preserve the existing marker file mechanism (`.legacy_cleaned`) so that cleanup runs at most once per workspace
3. IF any individual file or directory removal fails within the batched operation, THEN THE Legacy_Cleanup SHALL log the failure and continue removing the remaining items without raising an exception
4. THE Legacy_Cleanup SHALL complete the batched removal in a single thread dispatch regardless of the number of legacy items found (currently up to 15+ items)

### Requirement 6: Defer Refresh of Builtin Defaults

**User Story:** As a user, I want the app to start without waiting for the skills directory to be re-scanned on every launch, so that returning-user startups are faster.

#### Acceptance Criteria

1. WHEN the Backend starts on the fast path (seed-sourced or returning user), THE Lifespan SHALL defer `refresh_builtin_defaults()` to a background task that runs after `_startup_complete` is set to `True`
2. WHILE the Refresh_Builtin_Defaults background task is in progress, THE Backend SHALL serve skill and context file queries using the data already present in the database from the previous session
3. WHEN the Refresh_Builtin_Defaults background task completes, THE Backend SHALL make any newly discovered or updated skills available without requiring a restart
4. IF the Refresh_Builtin_Defaults background task fails, THEN THE Backend SHALL log the error and continue operating with the previously cached skill data

### Requirement 7: Defer WorkspaceExplorer Tree Fetch

**User Story:** As a user, I want the chat interface to be interactive before the file tree sidebar finishes loading, so that I can start chatting immediately.

#### Acceptance Criteria

1. WHEN the ThreeColumnLayout mounts, THE WorkspaceExplorer SHALL fetch the workspace file tree asynchronously without blocking the ChatPage from rendering or accepting input
2. WHILE the WorkspaceExplorer tree is loading, THE WorkspaceExplorer SHALL display a lightweight skeleton or spinner placeholder instead of an empty panel
3. IF the workspace tree fetch fails, THEN THE WorkspaceExplorer SHALL display an error state with a retry option without affecting the ChatPage functionality

### Requirement 8: Startup Sequence Observability

**User Story:** As a developer, I want to measure the time each startup phase takes, so that I can identify regressions and validate that optimizations are effective.

#### Acceptance Criteria

1. THE Lifespan SHALL log the elapsed wall-clock time (in milliseconds) for each major startup phase: database initialization, workspace verification, refresh_builtin_defaults, channel gateway startup (or deferral), config/permission loading, and agent manager configuration
2. THE Lifespan SHALL log the total elapsed time from the start of the `lifespan` function to `_startup_complete = True`
3. WHEN the BackendStartupOverlay dismisses (calls `onReady()`), THE BackendStartupOverlay SHALL log the elapsed time from the first health poll to overlay dismissal
4. WHEN the ChatPage completes its initial render (active tab messages displayed or welcome screen shown), THE ChatPage SHALL log the elapsed time from component mount to first interactive render
5. THE Backend SHALL include a `startup_time_ms` field in the SystemStatus_Endpoint response representing the total Backend startup duration in milliseconds
6. THE Backend SHALL include a `phase_timings` object in the SystemStatus_Endpoint response containing per-phase durations (database_ms, workspace_ms, refresh_defaults_ms, gateway_ms, config_ms, agent_manager_ms) so that regressions in individual phases can be identified
