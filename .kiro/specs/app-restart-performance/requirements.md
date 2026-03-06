# Requirements Document

## Introduction

Performance optimization of the SwarmAI desktop application restart and startup workflow. The app uses a Tauri 2.0 shell with a React frontend and a Python FastAPI backend sidecar. The current startup sequence has several bottlenecks that increase time-to-interactive: the channel gateway blocks startup even when no channels exist, the frontend fires five parallel data queries immediately on mount, session message loading is unbounded (no pagination), and legacy cleanup dispatches individual thread calls per file. This spec targets reducing time-to-interactive by deferring non-critical work, adding pagination, and eliminating unnecessary blocking.

## Glossary

- **Backend**: The Python FastAPI sidecar process launched by Tauri, defined in `backend/main.py`
- **Frontend**: The React single-page application rendered inside the Tauri webview
- **Lifespan**: The FastAPI `lifespan` async context manager in `backend/main.py` that runs startup and shutdown logic
- **Channel_Gateway**: The `ChannelGateway` singleton (`backend/channels/gateway.py`) responsible for starting and managing communication channel adapters
- **ChatPage**: The main React page component (`desktop/src/pages/ChatPage.tsx`) that mounts on app start and renders the chat interface
- **Session_Messages_Endpoint**: The `GET /api/chat/sessions/{id}/messages` FastAPI route that returns all messages for a chat session
- **Tab_Restore**: The process of loading previously open tabs from `~/.swarm-ai/open_tabs.json` via `restoreFromFile()` in `useUnifiedTabState`
- **Health_Check**: The `GET /api/health` endpoint that returns healthy only after `_startup_complete = True` in the Backend Lifespan
- **Time_To_Interactive**: The elapsed time from app launch until the user can see and interact with the ChatPage (messages rendered, input enabled)
- **Legacy_Cleanup**: The `_cleanup_legacy_content()` method in `SwarmWorkspaceManager` that removes pre-restructure files and directories on first run

## Requirements

### Requirement 1: Defer Channel Gateway Startup

**User Story:** As a user, I want the app to start faster when I have no channels configured, so that I reach the chat interface without waiting for channel initialization.

#### Acceptance Criteria

1. WHEN the Backend starts and no channels exist in the database, THE Lifespan SHALL skip calling `channel_gateway.startup()` during the synchronous startup sequence and mark startup as complete without waiting for channel initialization
2. WHEN the Backend starts and one or more channels exist in the database, THE Lifespan SHALL defer `channel_gateway.startup()` to a background task that runs after `_startup_complete` is set to `True`
3. WHILE the Channel_Gateway background startup is in progress, THE Health_Check SHALL return healthy (status 200) so the Frontend can begin loading
4. IF the deferred Channel_Gateway startup fails for one or more channels, THEN THE Backend SHALL log the failure and schedule retries using the existing retry mechanism without affecting the Health_Check status
5. THE Backend SHALL expose the channel gateway readiness state via the existing `GET /api/system/status` endpoint so the Frontend can distinguish between "gateway not yet started" and "gateway started but channel failed"

### Requirement 2: Prioritize Critical Frontend Queries on Mount

**User Story:** As a user, I want the chat interface to appear quickly after the backend is ready, so that I can start working without waiting for non-essential data to load.

#### Acceptance Criteria

1. WHEN the ChatPage mounts, THE ChatPage SHALL load agents data before loading skills, MCP servers, and plugins data
2. WHEN the ChatPage mounts, THE ChatPage SHALL defer loading of skills, MCP servers, and plugins queries until after the active tab's session messages have been rendered (or the welcome screen is displayed)
3. WHEN the ChatPage mounts, THE ChatPage SHALL load the sessions list query with a lower priority than the agents query and the active tab's session messages
4. THE ChatPage SHALL continue to load all five data sets (agents, skills, mcpServers, plugins, sessions) within the same mount lifecycle, deferring only the timing of non-critical queries
5. IF a deferred query (skills, mcpServers, plugins) fails, THEN THE ChatPage SHALL retry using the existing React Query retry mechanism without blocking the chat interface

### Requirement 3: Paginated Session Message Loading

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

### Requirement 4: Batch Legacy Cleanup Filesystem Operations

**User Story:** As a first-time user upgrading from an older version, I want the legacy cleanup to complete quickly, so that my first startup is not noticeably slower than subsequent startups.

#### Acceptance Criteria

1. WHEN the Legacy_Cleanup runs (marker file absent), THE Legacy_Cleanup SHALL collect all filesystem paths to remove into a single list and execute all removals within a single `anyio.to_thread.run_sync()` call instead of dispatching one thread call per file or directory
2. THE Legacy_Cleanup SHALL preserve the existing marker file mechanism (`.legacy_cleaned`) so that cleanup runs at most once per workspace
3. IF any individual file or directory removal fails within the batched operation, THEN THE Legacy_Cleanup SHALL log the failure and continue removing the remaining items without raising an exception
4. THE Legacy_Cleanup SHALL complete the batched removal in a single thread dispatch regardless of the number of legacy items found (currently up to 15+ items)

### Requirement 5: Startup Sequence Observability

**User Story:** As a developer, I want to measure the time each startup phase takes, so that I can identify regressions and validate that optimizations are effective.

#### Acceptance Criteria

1. THE Lifespan SHALL log the elapsed wall-clock time (in milliseconds) for each major startup phase: database initialization, workspace verification, channel gateway startup (or deferral), config/permission loading, and agent manager configuration
2. THE Lifespan SHALL log the total elapsed time from the start of the `lifespan` function to `_startup_complete = True`
3. WHEN the ChatPage completes its initial render (active tab messages displayed or welcome screen shown), THE ChatPage SHALL log the elapsed time from component mount to first interactive render
4. THE Backend SHALL include a `startup_time_ms` field in the `GET /api/system/status` response representing the total Backend startup duration in milliseconds
