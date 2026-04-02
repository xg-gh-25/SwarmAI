# Requirements Document

## Introduction

SwarmAI currently has a hardcoded frontend tab limit (`MAX_OPEN_TABS=6`) and a hardcoded backend concurrency cap (`MAX_CONCURRENT=2`). The gap between these two values creates a deceptive user experience: tabs 3–6 appear functional in the UI but queue for 60 seconds then fail with `QUEUE_TIMEOUT`. This feature replaces both hardcoded limits with a single dynamic value computed from available system RAM, ensuring every open tab is a full-capability tab backed by a real concurrency slot.

The dynamic limit is computed as `clamp(floor((available_ram_mb - 1024) / 500), 1, 4)` — a hard ceiling of 4 tabs, a floor of 1, with 1GB headroom reserved for OS stability. The limit is evaluated at app startup and re-checked before each tab creation. Existing open tabs are never killed when the budget shrinks.

## Glossary

- **ResourceMonitor**: Backend singleton (`backend/core/resource_monitor.py`) that provides cached system memory metrics and spawn budget decisions.
- **SessionRouter**: Backend routing layer (`backend/core/session_router.py`) that enforces concurrency caps and dispatches chat requests to SessionUnits.
- **TabStateHook**: Frontend hook (`useUnifiedTabState.ts`) that manages all tab state including creation, deletion, and persistence.
- **ChatPage**: Frontend page component (`ChatPage.tsx`) that renders the chat interface and consumes the tab limit for UI guards.
- **MaxTabs_Endpoint**: New backend API endpoint (`GET /api/system/max-tabs`) that exposes the computed tab limit to the frontend.
- **Available_RAM**: The amount of system RAM currently available (not total), as reported by `psutil.virtual_memory().available` or the macOS `vm_stat` fallback.
- **Spawn_Cost**: The estimated memory cost of one CLI+MCP subprocess (~500MB from COE measurement data), stored as `_DEFAULT_SPAWN_COST_MB` in ResourceMonitor.
- **Headroom**: A 1024MB memory reserve subtracted before computing max tabs, ensuring OS and background processes remain stable.
- **Memory_Pressure_Warning**: A UI indicator shown when system memory is under pressure after tabs are already open.
- **ChannelGateway**: Existing backend component (`backend/channels/gateway.py`) that routes inbound messages from external channels (Slack) through `SessionRouter.run_conversation()`. Currently shares the same concurrency pool as desktop tabs — future channel support will use a separate lightweight session pool.
- **Tab_Pool**: The set of concurrency slots reserved for desktop tab sessions (full CLI+MCP subprocesses, ~500MB each). Governed by `compute_max_tabs()`.
- **Channel_Pool**: (Future) A separate set of lightweight sessions for channel integrations (~10MB each, API-direct). Not implemented in this feature but the architecture must not preclude it.

## Requirements

### Requirement 1: Compute Dynamic Tab Limit

**User Story:** As a user, I want the maximum number of tabs to be determined by my machine's available memory, so that every open tab has enough resources to run a full CLI+MCP subprocess without queueing or timing out.

#### Acceptance Criteria

1. THE ResourceMonitor SHALL expose a `compute_max_tabs()` method that returns an integer in the range [1, 4].
2. WHEN `compute_max_tabs()` is called, THE ResourceMonitor SHALL compute the result as `clamp(floor((available_ram_mb - 1024) / 500), 1, 4)` where `available_ram_mb` is the current available system RAM in megabytes.
3. WHEN available RAM is 1524MB or less, THE ResourceMonitor SHALL return 1 as the minimum tab count.
4. WHEN available RAM is 3024MB or more, THE ResourceMonitor SHALL return 4 as the maximum tab count.
5. THE ResourceMonitor SHALL use the same memory reading mechanism already used by `system_memory()` (psutil with macOS `vm_stat` fallback).
6. IF `system_memory()` fails and returns the pessimistic fallback (1600MB available), THEN THE ResourceMonitor SHALL return 1 from `compute_max_tabs()`.

### Requirement 2: Backend Concurrency Cap Uses Dynamic Limit

**User Story:** As a user, I want the backend concurrency cap to match the dynamic tab limit, so that every open tab can actually acquire a concurrency slot without queueing.

#### Acceptance Criteria

1. WHEN the SessionRouter evaluates concurrency in `_acquire_slot()`, THE SessionRouter SHALL read the current limit from `ResourceMonitor.compute_max_tabs()` instead of the hardcoded `MAX_CONCURRENT=2`.
2. THE SessionRouter SHALL re-evaluate `compute_max_tabs()` on every call to `_acquire_slot()`, so the limit reflects current memory conditions at the moment of slot acquisition.
3. WHEN the dynamic limit decreases after tabs are already running, THE SessionRouter SHALL continue to allow existing alive sessions to operate without eviction.
4. THE SessionRouter SHALL preserve the existing `spawn_budget()` safety check as a second-layer gate after the concurrency cap check.

### Requirement 3: API Endpoint for Frontend Tab Limit

**User Story:** As a frontend developer, I want a backend API endpoint that returns the current maximum tab count, so that the frontend can enforce the same dynamic limit without duplicating the computation.

#### Acceptance Criteria

1. THE MaxTabs_Endpoint SHALL respond to `GET /api/system/max-tabs` with a JSON body containing `{"maxTabs": N}` where N is the integer result of `ResourceMonitor.compute_max_tabs()`.
2. WHEN the endpoint is called, THE MaxTabs_Endpoint SHALL read fresh system memory (invalidate cache) to return an up-to-date value.
3. THE MaxTabs_Endpoint SHALL return an HTTP 200 status code on success.
4. IF the ResourceMonitor fails to read system memory, THEN THE MaxTabs_Endpoint SHALL return `{"maxTabs": 1}` as a safe fallback.

### Requirement 4: Frontend Fetches Dynamic Tab Limit for New Tab Creation

**User Story:** As a user, I want the frontend tab creation limit to match my machine's actual capacity, so that I cannot open more tabs than my system can support.

#### Acceptance Criteria

1. WHEN the ChatPage initializes, THE TabStateHook SHALL fetch the current max tabs value from `GET /api/system/max-tabs`.
2. WHEN `addTab()` is called, THE TabStateHook SHALL re-fetch the max tabs value from the API before evaluating the limit, so the check reflects current memory conditions.
3. WHEN the number of open tabs equals or exceeds the fetched max tabs value, THE TabStateHook SHALL reject the `addTab()` call and return `undefined`.
4. THE TabStateHook SHALL remove the hardcoded `MAX_OPEN_TABS = 6` constant and replace all references with the dynamically fetched value for new tab creation.
5. WHEN the API call fails, THE TabStateHook SHALL fall back to a max tabs value of 2 to prevent unbounded tab creation.

### Requirement 4a: Tab Restore on App Restart

**User Story:** As a user, I want all my previously open tabs to be visible when I restart the app, even if my machine currently has fewer resources than when I opened them, so that I do not lose access to my conversations.

#### Acceptance Criteria

1. WHEN the app starts and `restoreFromFile()` loads tabs from `open_tabs.json`, THE TabStateHook SHALL restore ALL saved tabs up to the hard ceiling of 4, regardless of the current `compute_max_tabs()` value.
2. THE `restoreFromFile()` limit SHALL be the hard ceiling constant (4), NOT the dynamic `compute_max_tabs()` value.
3. WHEN restored tabs exceed the current `compute_max_tabs()` value, THE restored tabs SHALL be visible in the UI but their backend subprocesses SHALL only spawn on-demand when the user interacts with them (sends a message or switches to the tab).
4. WHEN a user interacts with a restored COLD tab and no concurrency slot is available, THE SessionRouter SHALL use the existing eviction logic (evict IDLE, queue if needed) to acquire a slot — the same behavior as any cold-resume scenario.
5. THE "+" button limit SHALL still use the dynamic `compute_max_tabs()` value — users cannot create NEW tabs beyond the resource limit, even if restored tabs exceed it.

### Requirement 5: Tab Creation Button Disabled at Limit

**User Story:** As a user, I want clear visual feedback when I cannot open more tabs, so that I understand the limitation and know what to do about it.

#### Acceptance Criteria

1. WHEN the number of open tabs equals the dynamic max tabs value, THE ChatPage SHALL disable the "+" (new tab) button.
2. WHEN the "+" button is disabled, THE ChatPage SHALL display a tooltip or message: "System resources are limited. Close a tab or free memory to open another."
3. WHEN a tab is closed and the open tab count drops below the dynamic max tabs value, THE ChatPage SHALL re-enable the "+" button.

### Requirement 6: Memory Pressure Warning

**User Story:** As a user, I want to be warned when system memory is under pressure after tabs are open, so that I can proactively close tabs or free memory before experiencing degraded performance.

#### Acceptance Criteria

1. WHILE system memory pressure is at "warning" level (75–90% used) and at least one tab is open, THE ChatPage SHALL display a yellow memory pressure indicator in the UI.
2. WHILE system memory pressure is at "critical" level (≥90% used) and at least one tab is open, THE ChatPage SHALL display a red memory pressure indicator in the UI.
3. WHEN memory pressure returns to "ok" level (<75% used), THE ChatPage SHALL hide the memory pressure indicator.
4. THE ChatPage SHALL poll memory pressure status at a reasonable interval (no more frequently than every 30 seconds) to avoid excessive API calls.
5. THE memory pressure indicator SHALL be informational only — THE ChatPage SHALL NOT automatically close or evict any tabs based on memory pressure.

### Requirement 7: Existing Tabs Protected from Budget Shrinkage

**User Story:** As a user, I want my existing open tabs to remain functional even if available memory decreases after I opened them, so that I do not lose work in progress.

#### Acceptance Criteria

1. WHEN the dynamic max tabs value decreases below the number of currently open tabs, THE SessionRouter SHALL continue to serve all existing alive sessions without eviction.
2. WHEN the dynamic max tabs value decreases below the number of currently open tabs, THE TabStateHook SHALL prevent opening new tabs but SHALL NOT close any existing tabs.
3. THE SessionRouter SHALL preserve the existing eviction policy: only IDLE sessions are evicted, and only when a new session needs a slot — never proactively due to budget shrinkage.

### Requirement 8: Channel-Forward Architecture Compatibility

**User Story:** As a platform architect, I want the dynamic tab scaling design to not preclude future channel integrations (Slack), so that channels can be added as a separate session pool without rewriting the tab budget logic.

#### Acceptance Criteria

1. THE ResourceMonitor method SHALL be named `compute_max_tabs()` (not `compute_max_sessions()`), so that future channel session pools can have their own independent budget computation.
2. THE SessionRouter SHALL NOT hardcode the assumption that every session is a CLI+MCP subprocess — the routing interface SHALL remain compatible with future lightweight session types (e.g., API-direct channel sessions at ~10MB each).
3. THE `_acquire_slot()` method SHALL continue to use `session_id`-based routing without assuming the session type, so that future channel sessions can flow through the same router with a different session unit implementation.
4. THE `compute_max_tabs()` method SHALL govern only the desktop tab pool budget — it SHALL NOT be used to gate channel session creation when channel support is added in the future.
5. THE existing `channels/gateway.py` integration point (`session_registry.session_router.run_conversation()`) SHALL remain functional and unmodified by this feature, preserving the path for future channel session backend wiring.
