# Implementation Plan: Thread-Scoped Cognitive Context (TSCC)

## Overview

This plan implements the TSCC feature: a thread-owned, collapsible cognitive context panel above the chat input that provides live, thread-specific cognitive state via SSE telemetry events, with filesystem-based snapshot archival. Implementation follows bottom-up order: backend data models → backend managers → backend API/router → frontend types → frontend service → frontend hook → frontend components → ChatPage integration → property tests → verification.

Depends on Cadences 1–4 (`swarmws-foundation`, `swarmws-projects`, `swarmws-explorer-ux`, `swarmws-intelligence`) being completed first.

## Tasks

- [x] 1. Define backend Pydantic data models and TelemetryEmitter
  - [x] 1.1 Create `backend/schemas/tscc.py` with all TSCC Pydantic models
    - Include module-level docstring per project code documentation standards
    - Define `TSCCContext(BaseModel)` with `scope_label: str`, `thread_title: str`, `mode: Optional[str]`
    - Define `TSCCActiveCapabilities(BaseModel)` with `skills: list[str]`, `mcps: list[str]`, `tools: list[str]`
    - Define `TSCCSource(BaseModel)` with `path: str`, `origin: str`
    - Define `TSCCLiveState(BaseModel)` with `context`, `active_agents`, `active_capabilities`, `what_ai_doing` (max 4), `active_sources`, `key_summary` (max 5)
    - Define `TSCCState(BaseModel)` with `thread_id`, `project_id`, `scope_type`, `last_updated_at`, `lifecycle_state`, `live_state`
    - Define `TSCCSnapshot(BaseModel)` with `snapshot_id`, `thread_id`, `timestamp`, `reason`, `lifecycle_state`, `active_agents`, `active_capabilities`, `what_ai_doing`, `active_sources`, `key_summary`
    - Define `TelemetryEvent(BaseModel)` with `type`, `thread_id`, `timestamp`, `data`
    - Define `SnapshotCreateRequest(BaseModel)` with `reason: str`
    - All field names use snake_case per backend convention
    - _Requirements: 18.1, 18.2, 18.3, 18.4_

  - [x] 1.2 Create `backend/core/telemetry_emitter.py` with `TelemetryEmitter` class
    - Include module-level docstring per project code documentation standards
    - `__init__(self, thread_id: str)` — stores thread_id for all emitted events
    - `agent_activity(agent_name, description) -> dict` — emits when agent begins/completes reasoning step
    - `tool_invocation(tool_name, description) -> dict` — emits when a tool is invoked
    - `capability_activated(cap_type, cap_name, label) -> dict` — emits when skill/MCP/tool activated; cap_type: 'skill'|'mcp'|'tool'
    - `sources_updated(source_path, origin) -> dict` — emits when agent references a new source; normalizes source paths to workspace-relative form before emission; strips absolute paths, `~` prefixes, and `{app_data_dir}` references; origin: 'Project'|'Knowledge Base'|'Notes'|'Memory'|'External MCP'
    - `summary_updated(key_summary: list[str]) -> dict` — emits when working conclusion changes
    - Each method returns a dict with `type`, `thread_id`, `timestamp` (ISO 8601), and `data` fields
    - All field names in emitted dicts use snake_case
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_

  - [x] 1.3 Write unit tests for TelemetryEmitter in `backend/tests/test_telemetry_emitter.py`
    - Include module-level docstring describing what is tested
    - Test each emitter method produces correct dict structure with required fields
    - Test `type` field matches one of the five telemetry types
    - Test `thread_id` is present and matches constructor arg
    - Test `timestamp` is valid ISO 8601
    - Test all field names in output are snake_case
    - Test path normalization in `sources_updated`: verify absolute paths, `~` prefixes, and `{app_data_dir}` references are stripped to workspace-relative form
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_


  - [ ]* 1.4 Write property test: Telemetry event structure validity (`backend/tests/test_property_tscc_telemetry.py`)
    - **Property 13: Telemetry event structure validity**
    - Generate random event parameters (agent names, tool names, capability types, source paths, summary lists) using hypothesis
    - Verify every emitted dict contains `type` matching one of five telemetry types, non-empty `thread_id`, valid ISO 8601 `timestamp`, and `data` dict with correct fields for that event type
    - Verify all field names in the payload are snake_case (no camelCase keys)
    - Tag: `Feature: thread-scoped-cognitive-context, Property 13: Telemetry event structure validity`
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7**

- [x] 2. Implement TSCCStateManager for in-memory per-thread state
  - [x] 2.1 Create `backend/core/tscc_state_manager.py` with `TSCCStateManager` class
    - Include module-level docstring per project code documentation standards
    - `__init__(self, max_entries: int = 200)` — initializes `_states: OrderedDict[str, TSCCState]` (LRU-evicting), `_locks: dict[str, asyncio.Lock]`, and `_max_entries`
    - `_get_lock(thread_id) -> asyncio.Lock` — returns per-thread asyncio.Lock, creating one if needed
    - `async get_state(thread_id) -> Optional[TSCCState]` — returns current state or None (guarded by per-thread lock)
    - `async get_or_create_state(thread_id, project_id, thread_title) -> TSCCState` — creates default state with correct scope_type and scope_label; scope_label is `"Workspace: SwarmWS (General)"` when project_id is None, or `"Project: {name}"` when project_id is set; when `len(self._states) >= self._max_entries`, evicts least-recently-used entry via `self._states.popitem(last=False)`
    - `async apply_event(thread_id, event: dict)` — routes event by type to update the correct live_state fields (guarded by per-thread lock):
      - `agent_activity`: appends to `active_agents` (deduplicated) and updates `what_ai_doing` (max 4, FIFO)
      - `tool_invocation`: updates `what_ai_doing` (max 4, FIFO)
      - `capability_activated`: adds to appropriate `active_capabilities` category (deduplicated)
      - `sources_updated`: adds to `active_sources` (deduplicated by (path, origin) tuple)
      - `summary_updated`: replaces `key_summary` (max 5)
    - `async set_lifecycle_state(thread_id, state)` — validates transition against state machine, raises ValueError for invalid transitions; valid transitions: `new→active`, `active→paused`, `active→failed`, `active→cancelled`, `active→idle`, `paused→active`, `paused→cancelled`, `failed→active`, `failed→cancelled`, `cancelled→active`, `idle→active`
    - `async clear_state(thread_id)` — removes state for a thread
    - Updates `last_updated_at` on every mutation
    - _Requirements: 18.1, 18.2, 9.1–9.7, 4.3, 4.5, 5.1, 5.4, 6.3, 7.1, 12.1, 12.3_

  - [x] 2.2 Write unit tests for TSCCStateManager in `backend/tests/test_tscc_state_manager.py`
    - Include module-level docstring describing what is tested
    - Test `get_or_create_state` creates default state with correct fields and scope_label
    - Test `apply_event` for each of the five telemetry event types
    - Test `what_ai_doing` max 4 enforcement (FIFO eviction)
    - Test `key_summary` max 5 enforcement
    - Test `active_agents` deduplication
    - Test `active_sources` deduplication by (path, origin) tuple — same path with different origin keeps both entries
    - Test `active_capabilities` deduplication per category
    - Test `set_lifecycle_state` valid transitions succeed (including `cancelled→active` direct transition)
    - Test `set_lifecycle_state` invalid transitions raise ValueError
    - Test `clear_state` removes thread state
    - Test LRU eviction: when max_entries is reached, oldest entry is evicted
    - Test asyncio.Lock: verify concurrent access is properly guarded
    - Test scope_label is `"Workspace: SwarmWS (General)"` when project_id is None
    - Test scope_label never contains "None", "No project", "not selected"
    - _Requirements: 18.1, 9.1–9.7, 3.4, 3.5, 8.1, 8.4_

  - [ ]* 2.3 Write property test: Lifecycle state machine validity (`backend/tests/test_property_tscc_lifecycle.py`)
    - **Property 7: Lifecycle state machine validity**
    - Generate random sequences of lifecycle state transitions using hypothesis
    - Verify only valid transitions succeed: `new→active`, `active→paused`, `active→failed`, `active→cancelled`, `active→idle`, `paused→active`, `paused→cancelled`, `failed→active`, `failed→cancelled`, `cancelled→active`, `idle→active`
    - Verify any transition not in the valid set raises ValueError
    - Tag: `Feature: thread-scoped-cognitive-context, Property 7: Lifecycle state machine validity`
    - **Validates: Requirements 9.2, 9.3, 9.4, 9.5, 9.6, 9.7**

  - [ ]* 2.4 Write property test: List length enforcement (`backend/tests/test_property_tscc_list_length.py`)
    - **Property 5: List length enforcement**
    - Generate random sequences of telemetry events (varying counts of agent_activity, tool_invocation, summary_updated) using hypothesis
    - Apply all events to a TSCCStateManager instance
    - Verify `what_ai_doing` never exceeds 4 items and `key_summary` never exceeds 5 items after any event application
    - Tag: `Feature: thread-scoped-cognitive-context, Property 5: List length enforcement`
    - **Validates: Requirements 5.1, 7.1**

  - [ ]* 2.5 Write property test: No negative scope labels (`backend/tests/test_property_tscc_scope_labels.py`)
    - **Property 4: No negative scope labels**
    - Generate random TSCC states with random project_id values (None and non-None) using hypothesis
    - Verify `context.scope_label` never contains "None", "No project", "not selected", and is never empty
    - Tag: `Feature: thread-scoped-cognitive-context, Property 4: No negative scope labels`
    - **Validates: Requirements 3.5, 8.4**

  - [ ]* 2.6 Write property test: Thread isolation (`backend/tests/test_property_tscc_thread_isolation.py`)
    - **Property 10: Thread isolation — no cross-thread data leakage**
    - Generate random pairs of thread IDs and random telemetry events using hypothesis
    - Apply events with `thread_id=A`, verify state for thread B is unchanged; source deduplication uses (path, origin) tuple
    - Apply events with `thread_id=B`, verify state for thread A is unchanged
    - Tag: `Feature: thread-scoped-cognitive-context, Property 10: Thread isolation — no cross-thread data leakage`
    - **Validates: Requirements 12.1, 12.3, 14.2, 14.4, 4.3, 6.3**

  - [ ]* 2.7 Write property test: Source paths are workspace-relative (`backend/tests/test_property_tscc_source_paths.py`)
    - **Property 6: Source paths are workspace-relative**
    - Generate random source paths and apply `sources_updated` events using hypothesis
    - Verify no source in `active_sources` has a `path` starting with `/`, `~`, or containing `{app_data_dir}`
    - Tag: `Feature: thread-scoped-cognitive-context, Property 6: Source paths are workspace-relative`
    - **Validates: Requirements 6.5**


- [x] 3. Implement TSCCSnapshotManager for filesystem-based snapshots
  - [x] 3.1 Create `backend/core/tscc_snapshot_manager.py` with `TSCCSnapshotManager` class
    - Include module-level docstring per project code documentation standards
    - `__init__(workspace_manager: SwarmWorkspaceManager, state_manager: TSCCStateManager)` — stores workspace manager reference for path resolution and state_manager for thread→project path mapping
    - `MAX_SNAPSHOTS_PER_THREAD = 50` class constant
    - `create_snapshot(thread_id, state: TSCCState, reason) -> TSCCSnapshot` — creates snapshot from current state including `lifecycle_state`; generates `snapshot_id` (UUID), `timestamp` (ISO 8601); writes JSON to `{snapshot_dir}/snapshot_{YYYY-MM-DDTHH-MM-SSZ}.json` (colon-safe format); checks dedup window before writing; calls `_enforce_retention` after writing
    - `list_snapshots(thread_id) -> list[TSCCSnapshot]` — lists all snapshots in chronological order by reading and parsing JSON files from snapshot directory
    - `get_snapshot(thread_id, snapshot_id) -> Optional[TSCCSnapshot]` — reads a single snapshot by ID; returns None if not found
    - `_get_snapshot_dir(thread_id) -> Path` — resolves snapshot directory using `state_manager` for thread→project path resolution: `{workspace_path}/chats/{thread_id}/snapshots/` for workspace-scoped, `{workspace_path}/Projects/{project}/chats/{thread_id}/snapshots/` for project-scoped
    - `_is_duplicate(snapshot_dir, reason, window_seconds=30) -> bool` — checks if a snapshot with the same reason exists within the dedup window
    - `_enforce_retention(self, snapshot_dir: Path)` — deletes oldest snapshots if count exceeds `MAX_SNAPSHOTS_PER_THREAD` (50)
    - Creates snapshot directory on first write if it doesn't exist
    - Handles corrupted/unreadable JSON files gracefully (log warning, skip)
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 11.4, 11.5_

  - [x] 3.2 Write unit tests for TSCCSnapshotManager in `backend/tests/test_tscc_snapshot_manager.py`
    - Include module-level docstring describing what is tested
    - Test `create_snapshot` writes JSON file with correct fields and colon-safe filename pattern (`snapshot_{YYYY-MM-DDTHH-MM-SSZ}.json`)
    - Test `create_snapshot` includes `lifecycle_state` field in snapshot
    - Test `list_snapshots` returns snapshots in chronological order
    - Test `get_snapshot` returns correct snapshot by ID
    - Test `get_snapshot` returns None for non-existent snapshot_id
    - Test deduplication: two snapshots with same reason within 30s produces only one file
    - Test deduplication: same reason after 30s produces a second file
    - Test snapshot directory is created on first write
    - Test corrupted JSON file is skipped gracefully in `list_snapshots`
    - Test snapshot contains all required fields from TSCCState
    - Test retention enforcement: verify oldest snapshots deleted when exceeding 50
    - Test constructor accepts `state_manager` parameter
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [ ]* 3.3 Write property test: Snapshot round-trip (`backend/tests/test_property_tscc_snapshot_roundtrip.py`)
    - **Property 8: Snapshot round-trip**
    - Generate random valid TSCCState objects and trigger reasons using hypothesis
    - Create snapshot via `create_snapshot()`, read back via `get_snapshot()`
    - Verify all required fields (`snapshot_id`, `thread_id`, `timestamp`, `reason`, `lifecycle_state`, `active_agents`, `active_capabilities`, `what_ai_doing`, `active_sources`, `key_summary`) are present and match original state
    - Verify filename matches pattern `snapshot_{YYYY-MM-DDTHH-MM-SSZ}.json`
    - Tag: `Feature: thread-scoped-cognitive-context, Property 8: Snapshot round-trip`
    - **Validates: Requirements 10.2, 10.3, 10.4, 18.1, 18.2, 18.3**

  - [ ]* 3.4 Write property test: Snapshot deduplication within 30-second window (`backend/tests/test_property_tscc_snapshot_dedup.py`)
    - **Property 9: Snapshot deduplication within 30-second window**
    - Generate random thread IDs and trigger reasons using hypothesis
    - Create two snapshots with the same reason within 30 seconds, verify only one file exists
    - Create a snapshot with the same reason after 30 seconds, verify a second file is created
    - Tag: `Feature: thread-scoped-cognitive-context, Property 9: Snapshot deduplication within 30-second window`
    - **Validates: Requirements 10.5**

- [x] 4. Implement TSCC FastAPI router
  - [x] 4.1 Create `backend/routers/tscc.py` with TSCC API endpoints
    - Include module-level docstring per project code documentation standards
    - `GET /api/chat_threads/{thread_id}/tscc` — returns current TSCCState; returns 404 if thread not found
    - `POST /api/chat_threads/{thread_id}/snapshots` — accepts `SnapshotCreateRequest` body with `reason`; creates snapshot; returns created TSCCSnapshot
    - `GET /api/chat_threads/{thread_id}/snapshots` — returns all snapshots for thread in chronological order
    - `GET /api/chat_threads/{thread_id}/snapshots/{snapshot_id}` — returns single snapshot; returns 404 if not found
    - All responses use snake_case field names
    - Follow existing router patterns from `backend/routers/chat.py`
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 11.4, 11.5_

  - [x] 4.2 Register the TSCC router in `backend/main.py`
    - Import and include the new TSCC router
    - _Requirements: 15.1_

  - [x] 4.3 Write unit tests for TSCC router in `backend/tests/test_tscc_router.py`
    - Include module-level docstring describing what is tested
    - Test `GET /tscc` returns 200 with valid TSCCState for existing thread
    - Test `GET /tscc` returns 404 for non-existent thread
    - Test `POST /snapshots` creates snapshot and returns 200
    - Test `GET /snapshots` returns list in chronological order
    - Test `GET /snapshots/{id}` returns 200 for existing snapshot
    - Test `GET /snapshots/{id}` returns 404 for non-existent snapshot
    - Test all response field names are snake_case
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

  - [ ]* 4.4 Write property test: 404 for non-existent resources (`backend/tests/test_property_tscc_404.py`)
    - **Property 17: 404 for non-existent resources**
    - Generate random UUIDs for thread_id and snapshot_id that do not exist using hypothesis
    - Verify `GET /api/chat_threads/{thread_id}/tscc` returns 404
    - Verify `GET /api/chat_threads/{thread_id}/snapshots/{snapshot_id}` returns 404
    - Tag: `Feature: thread-scoped-cognitive-context, Property 17: 404 for non-existent resources`
    - **Validates: Requirements 15.4**

  - [ ]* 4.5 Write property test: API response snake_case (`backend/tests/test_property_tscc_api_case.py`)
    - **Property 14: API response snake_case / frontend camelCase round-trip**
    - Generate random TSCCState and TSCCSnapshot objects using hypothesis
    - Serialize to JSON via the API response path
    - Verify all field names in the JSON output are snake_case (no camelCase keys)
    - Tag: `Feature: thread-scoped-cognitive-context, Property 14: API response snake_case / frontend camelCase round-trip`
    - **Validates: Requirements 15.5, 15.6, 11.5**

- [x] 5. Checkpoint — Ensure all backend tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Run `cd backend && pytest` to verify all TSCC backend tests pass
  - Verify TelemetryEmitter, TSCCStateManager, TSCCSnapshotManager, and TSCC router are working correctly


- [x] 6. Add frontend TypeScript types and TSCC service
  - [x] 6.1 Add TSCC TypeScript interfaces to `desktop/src/types/index.ts`
    - Define `TSCCContext` with `scopeLabel`, `threadTitle`, `mode?`
    - Define `TSCCActiveCapabilities` with `skills`, `mcps`, `tools` (all `string[]`)
    - Define `TSCCSource` with `path`, `origin`
    - Define `TSCCLiveState` with `context`, `activeAgents`, `activeCapabilities`, `whatAiDoing`, `activeSources`, `keySummary`
    - Define `ThreadLifecycleState` type: `'new' | 'active' | 'paused' | 'failed' | 'cancelled' | 'idle'`
    - Define `ScopeType` type: `'workspace' | 'project'`
    - Define `TSCCState` with `threadId`, `projectId`, `scopeType`, `lastUpdatedAt`, `lifecycleState`, `liveState`
    - Define `TSCCSnapshot` with `snapshotId`, `threadId`, `timestamp`, `reason`, `lifecycleState`, `activeAgents`, `activeCapabilities`, `whatAiDoing`, `activeSources`, `keySummary`
    - Define `TelemetryEventType` type for the five telemetry event types
    - All fields use camelCase per frontend convention
    - _Requirements: 18.5, 14.1_

  - [x] 6.2 Extend `StreamEvent` interface in `desktop/src/types/index.ts` with TSCC telemetry event types
    - Add `'agent_activity' | 'tool_invocation' | 'capability_activated' | 'sources_updated' | 'summary_updated'` to the `type` union
    - Add optional TSCC telemetry fields: `threadId?`, `agentName?`, `description?`, `capabilityType?`, `capabilityName?`, `label?`, `sourcePath?`, `origin?`, `keySummary?`
    - _Requirements: 14.1_

  - [x] 6.3 Create `desktop/src/services/tscc.ts` with TSCC API service
    - Include file-level JSDoc comment per project code documentation standards
    - Implement `toCamelCase(data: any): TSCCState` — converts snake_case API response to camelCase TypeScript types (handles nested `live_state`, `active_capabilities`, `active_sources`)
    - Implement `snapshotToCamelCase(data: any): TSCCSnapshot` — converts snapshot response
    - Implement `getTSCCState(threadId: string): Promise<TSCCState>` — calls `GET /api/chat_threads/{threadId}/tscc`
    - Implement `createSnapshot(threadId: string, reason: string): Promise<TSCCSnapshot>` — calls `POST /api/chat_threads/{threadId}/snapshots`
    - Implement `listSnapshots(threadId: string): Promise<TSCCSnapshot[]>` — calls `GET /api/chat_threads/{threadId}/snapshots`
    - Implement `getSnapshot(threadId: string, snapshotId: string): Promise<TSCCSnapshot>` — calls `GET /api/chat_threads/{threadId}/snapshots/{snapshotId}`
    - Follow existing service patterns from `desktop/src/services/agents.ts`
    - _Requirements: 15.1, 15.2, 15.3, 15.6_

  - [x] 6.4 Write unit tests for TSCC service in `desktop/src/services/__tests__/tscc.test.ts`
    - Test `toCamelCase` correctly converts all nested snake_case fields to camelCase
    - Test `snapshotToCamelCase` correctly converts snapshot fields
    - Test `getTSCCState` constructs correct URL
    - Test `createSnapshot` sends correct request body
    - Test `listSnapshots` constructs correct URL
    - Test `getSnapshot` constructs correct URL with snapshot_id
    - _Requirements: 15.6_

- [x] 7. Implement useTSCCState React hook
  - [x] 7.1 Create `desktop/src/hooks/useTSCCState.ts` with TSCC state management hook
    - Include file-level JSDoc comment per project code documentation standards
    - Accept `threadId: string | null` parameter
    - Fetch initial TSCCState from `GET /api/chat_threads/{threadId}/tscc` on mount or when `threadId` changes
    - On fetch failure, fall back to default empty state with lifecycle `new`
    - Maintain per-thread expand/collapse preference in memory via `Map<string, boolean>` (not persisted to localStorage)
    - Maintain per-thread pin preference in memory via `Map<string, boolean>`
    - Implement `applyTelemetryEvent(event: TelemetryEvent)` — incrementally updates state:
      - `agent_activity`: appends to `activeAgents` (deduplicated), updates `whatAiDoing` (max 4, FIFO)
      - `tool_invocation`: updates `whatAiDoing` (max 4, FIFO)
      - `capability_activated`: adds to appropriate `activeCapabilities` category (deduplicated)
      - `sources_updated`: adds to `activeSources` (deduplicated by path)
      - `summary_updated`: replaces `keySummary` (max 5)
    - Implement `toggleExpand()`, `togglePin()`, `setAutoExpand(expanded: boolean)`
    - Reset state cleanly when `threadId` changes — no cross-thread data leakage
    - Return `{ tsccState, isExpanded, isPinned, lifecycleState, toggleExpand, togglePin, applyTelemetryEvent, setAutoExpand }`
    - Follow patterns from `desktop/src/hooks/useTabState.ts` and `desktop/src/hooks/useSidebarState.ts`
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 12.2_

  - [x] 7.2 Write unit tests for useTSCCState in `desktop/src/hooks/__tests__/useTSCCState.test.ts`
    - Test hook fetches state on mount with valid threadId
    - Test hook resets state when threadId changes
    - Test `applyTelemetryEvent` for each of the five event types
    - Test `what_ai_doing` max 4 enforcement
    - Test `key_summary` max 5 enforcement
    - Test expand/collapse preference preserved per thread across switches
    - Test pin preference preserved per thread
    - Test default state returned on fetch failure
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5_

  - [ ]* 7.3 Write property test: Incremental telemetry event application (`desktop/src/hooks/__tests__/useTSCCState.property.test.ts`)
    - **Property 12: Incremental telemetry event application**
    - Generate random TSCCState and random valid telemetry events using fast-check
    - Apply event via `applyTelemetryEvent()`, verify only fields relevant to that event type are modified, all other fields remain unchanged
    - Specifically: `agent_activity` updates `whatAiDoing` and `activeAgents`; `tool_invocation` updates `whatAiDoing`; `capability_activated` adds to appropriate `activeCapabilities` category; `sources_updated` adds to `activeSources`; `summary_updated` replaces `keySummary`
    - Tag: `Feature: thread-scoped-cognitive-context, Property 12: Incremental telemetry event application`
    - **Validates: Requirements 14.3, 19.3, 4.5, 5.4**

  - [ ]* 7.4 Write property test: Per-thread expand/collapse preference preservation (`desktop/src/hooks/__tests__/useTSCCState.property.test.ts`)
    - **Property 11: Per-thread expand/collapse preference preservation**
    - Generate random sequences of thread switches (A→B→A→C→A) and toggle operations using fast-check
    - Verify expand/collapse state for each thread is preserved independently when switching back
    - Tag: `Feature: thread-scoped-cognitive-context, Property 11: Per-thread expand/collapse preference preservation`
    - **Validates: Requirements 12.2, 19.4**


- [x] 8. Implement TSCCPanel frontend component
  - [x] 8.1 Create `desktop/src/pages/chat/components/TSCCPanel.tsx` with CollapsedBar and ExpandedView
    - Include file-level JSDoc comment per project code documentation standards
    - Accept props: `threadId`, `tsccState`, `isExpanded`, `isPinned`, `onToggleExpand`, `onTogglePin`
    - Render CollapsedBar: single-line summary with scope label, active agent count, capability summary (up to 2 names), source count, freshness indicator (relative timestamp from `lastUpdatedAt`)
    - CollapsedBar click anywhere expands to ExpandedView
    - CollapsedBar includes pin toggle button
    - Render ExpandedView with five cognitive modules:
      - CurrentContextModule: scope label, thread title, optional mode tag
      - ActiveAgentsModule: agent list with human-readable names; grouped capabilities (Skills, MCPs, Tools); shows "Using core SwarmAgent only" when empty
      - WhatAIDoingModule: 2–4 bullet points; shows "Waiting for your input" when idle; human-readable error descriptions (no raw codes)
      - ActiveSourcesModule: source list with origin tags; workspace-relative paths; shows "Using conversation context only" when empty
      - KeySummaryModule: 3–5 bullet points; shows "No summary yet — ask me to summarize this thread" when empty
    - Use CSS variables (`--color-*`) for all colors — never hardcode
    - Use soft visual separators and calm background that doesn't compete with chat messages
    - Must not cause scroll position changes when expanding/collapsing
    - Must not block or obscure ChatInput in any state
    - Lifecycle state display: "New thread · Ready" for `new`, "Updated just now" for `active`, "Paused · Waiting for your input" for `paused`, human-readable error for `failed`, "Execution stopped · Partial progress saved" for `cancelled`, "Idle · Ready for next task" for `idle`
    - After resumption from `cancelled`, show transient "Resumed · Continuing previous analysis" indicator for 5 seconds before reverting to normal `active` display
    - ExpandedView has `max-height: 280px` with `overflow-y: auto`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1–3.6, 4.1–4.5, 5.1–5.5, 6.1–6.5, 7.1–7.4, 8.1–8.4, 9.1–9.7_

  - [x] 8.2 Add ARIA accessibility attributes to TSCCPanel
    - CollapsedBar: `role="region"`, `aria-label="Thread cognitive context"`, `aria-expanded` reflecting state
    - CollapsedBar: focusable via Tab, expandable via Enter or Space key
    - ExpandedView cognitive modules: semantic heading hierarchy (`h3` or `h4`) for module titles
    - `aria-live="polite"` region for significant state changes (lifecycle transitions, error states)
    - _Requirements: 20.1, 20.2, 20.3, 20.4_

  - [x] 8.3 Write unit tests for TSCCPanel in `desktop/src/pages/chat/components/__tests__/TSCCPanel.test.tsx`
    - Test collapsed bar renders all required fields (scope, agent count, capabilities, source count, freshness)
    - Test click on collapsed bar triggers expand
    - Test expanded view renders all five cognitive modules
    - Test idle state displays "Ready" text
    - Test empty agents displays "Using core SwarmAgent only"
    - Test empty sources displays "Using conversation context only"
    - Test empty summary displays "No summary yet — ask me to summarize this thread"
    - Test keyboard navigation: Enter/Space expand, ARIA attributes present
    - Test pin toggle works
    - Test lifecycle state displays correct text for each state
    - _Requirements: 1.1–1.6, 2.1–2.6, 20.1–20.4_

  - [ ]* 8.4 Write property test: Panel visibility across all thread states (`desktop/src/pages/chat/components/__tests__/TSCCPanel.property.test.tsx`)
    - **Property 1: Panel visibility across all thread states**
    - Generate random lifecycle states and project associations using fast-check
    - Verify TSCCPanel renders without errors and produces a non-empty collapsed bar for every combination
    - Tag: `Feature: thread-scoped-cognitive-context, Property 1: Panel visibility across all thread states`
    - **Validates: Requirements 1.2, 9.1–9.7**

  - [ ]* 8.5 Write property test: Collapsed bar content completeness (`desktop/src/pages/chat/components/__tests__/TSCCPanel.property.test.tsx`)
    - **Property 2: Collapsed bar content completeness**
    - Generate random valid TSCCState objects using fast-check
    - Verify collapsed bar text contains: scope label, agent count as number, at most 2 capability names, source count as number, and a freshness indicator
    - Tag: `Feature: thread-scoped-cognitive-context, Property 2: Collapsed bar content completeness`
    - **Validates: Requirements 2.2**

  - [ ]* 8.6 Write property test: Error state produces human-readable description (`desktop/src/pages/chat/components/__tests__/TSCCPanel.property.test.tsx`)
    - **Property 18: Error state produces human-readable description**
    - Generate random error messages and apply to TSCCState using fast-check
    - Verify `whatAiDoing` entries contain no raw error codes, stack traces, HTTP status codes, or internal pipeline stage names
    - Tag: `Feature: thread-scoped-cognitive-context, Property 18: Error state produces human-readable description`
    - **Validates: Requirements 5.5, 9.4**

- [x] 9. Implement TSCCSnapshotCard frontend component
  - [x] 9.1 Create `desktop/src/pages/chat/components/TSCCSnapshotCard.tsx`
    - Include file-level JSDoc comment per project code documentation standards
    - Accept props: `snapshot: TSCCSnapshot`
    - Render collapsed by default: shows timestamp and trigger reason
    - On expand: shows agents, capabilities (grouped), sources with origin tags, activity description, key summary
    - Use CSS variables (`--color-*`) for all colors
    - _Requirements: 11.1, 11.2, 11.3_

  - [x] 9.2 Write unit tests for TSCCSnapshotCard in `desktop/src/pages/chat/components/__tests__/TSCCSnapshotCard.test.tsx`
    - Test card renders collapsed by default with timestamp and reason
    - Test card expands to show all snapshot fields
    - Test agents, capabilities, sources, activity, and summary are displayed correctly
    - _Requirements: 11.1, 11.2, 11.3_

- [x] 10. Checkpoint — Ensure all frontend tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Run `cd desktop && npm test -- --run` to verify all TSCC frontend tests pass
  - Verify TSCCPanel, useTSCCState, TSCC service, and TSCCSnapshotCard are working correctly


- [x] 11. Integrate TSCC into ChatPage and AgentManager
  - [x] 11.1 Update `desktop/src/pages/chat/index.ts` (ChatPage) to render TSCCPanel
    - Import `TSCCPanel` and `useTSCCState`
    - Call `useTSCCState(activeThreadId)` to get TSCC state and controls
    - Insert `<TSCCPanel>` between the message list and `<ChatInput>` component
    - Pass `threadId`, `tsccState`, `isExpanded`, `isPinned`, `onToggleExpand`, `onTogglePin` props
    - Route incoming SSE telemetry events to `applyTelemetryEvent()` based on `threadId` match
    - Ignore telemetry events whose `threadId` does not match the currently active thread
    - Implement auto-expand logic: auto-expand only for first plan creation, blocking issue requiring user input, or explicit user request; never auto-expand during normal streaming
    - _Requirements: 1.1, 14.2, 14.3, 14.4, 16.1, 16.2, 16.3, 16.4_

  - [x] 11.2 Insert TSCCSnapshotCards into thread message history
    - In the message list rendering logic, interleave `TSCCSnapshotCard` components at the chronological position where each snapshot was captured
    - Fetch snapshots via `listSnapshots(threadId)` when thread loads
    - Match snapshot timestamps to message timestamps for correct positioning
    - _Requirements: 11.1, 11.2_

  - [x] 11.3 Update `backend/core/agent_manager.py` to yield telemetry events from `_run_query_on_client`
    - Import `TelemetryEmitter` from `backend/core/telemetry_emitter`
    - Instantiate `TelemetryEmitter(thread_id)` at the start of `_run_query_on_client`
    - Yield `agent_activity` event when `_format_message` processes an `AssistantMessage`
    - Yield `tool_invocation` event when `_format_message` processes a `ToolUseMessage`
    - Yield `capability_activated` event when `_resolve_allowed_tools` or `_build_mcp_config` activates a capability
    - Yield `sources_updated` event when agent references a file via tool use (detect from tool input paths)
    - Yield `summary_updated` event when a `ResultMessage` completes a multi-turn execution phase
    - Update `TSCCStateManager` in parallel with each emitted event (call `apply_event`)
    - Update lifecycle state: set `active` when execution starts, `paused` when waiting for input, `failed` on error, `idle` on completion
    - Wrap all telemetry emission in try/except — log warning on failure, never interrupt agent SSE stream
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 9.2, 9.3, 9.4, 9.7_

  - [x] 11.4 Add snapshot trigger logic to AgentManager
    - After plan decomposition completes, call `TSCCSnapshotManager.create_snapshot(thread_id, state, "Plan decomposition completed")`
    - After a decision is recorded, call `create_snapshot` with appropriate reason
    - After a multi-step execution phase completes, call `create_snapshot` with appropriate reason
    - Wrap snapshot creation in try/except — log warning on failure, never interrupt execution
    - _Requirements: 10.1_

  - [x] 11.5 Write unit tests for AgentManager telemetry integration in `backend/tests/test_agent_telemetry_integration.py`
    - Include module-level docstring describing what is tested
    - Test that `_run_query_on_client` yields telemetry events alongside normal SSE events
    - Test that telemetry emission failure does not interrupt agent execution (graceful degradation)
    - Test that lifecycle state transitions occur at correct points (active on start, idle on completion)
    - Test that snapshot triggers fire at correct points
    - _Requirements: 13.1, 13.6, 9.2, 9.7, 10.1_

- [x] 12. Implement TSCC interaction rules and auto-expand logic
  - [x] 12.1 Add auto-expand logic to useTSCCState and ChatPage integration
    - TSCC_Panel does NOT auto-expand during normal chat message streaming
    - TSCC_Panel auto-expands ONLY for: first plan creation in thread, blocking issue requiring user input, explicit user request (e.g., slash command)
    - When TSCC_State changes during normal operation, update Collapsed_Bar content and Freshness_Indicator silently
    - TSCC_Panel must not cause layout shifts, scroll jumps, or input focus loss when updating
    - _Requirements: 16.1, 16.2, 16.3, 16.4_

  - [x] 12.2 Ensure TSCC and ContextPreviewPanel independence
    - Verify TSCC_Panel operates independently from ContextPreviewPanel
    - Expanding/collapsing TSCC does not affect ContextPreviewPanel state, and vice versa
    - TSCC is accessible from every chat thread (above chat input); ContextPreviewPanel remains in project detail view
    - _Requirements: 17.1, 17.2, 17.3, 17.4_

  - [ ]* 12.3 Write property test: Auto-expand only for high-signal events (`desktop/src/hooks/__tests__/useTSCCState.property.test.ts`)
    - **Property 15: Auto-expand only for high-signal events**
    - Generate random streams of SSE events (mix of normal chat events and telemetry events) using fast-check
    - Verify TSCC_Panel remains in current expand/collapse state for normal events (`assistant`, `tool_use`, `tool_result`)
    - Verify auto-expand triggers only for first plan creation, blocking issue, or explicit user request
    - Tag: `Feature: thread-scoped-cognitive-context, Property 15: Auto-expand only for high-signal events`
    - **Validates: Requirements 16.1, 16.2**

  - [ ]* 12.4 Write property test: TSCC and ContextPreviewPanel state independence (`desktop/src/hooks/__tests__/useTSCCState.property.test.ts`)
    - **Property 16: TSCC and ContextPreviewPanel state independence**
    - Generate random sequences of expand/collapse operations on TSCC and ContextPreviewPanel using fast-check
    - Verify toggling one panel never affects the other panel's state
    - Tag: `Feature: thread-scoped-cognitive-context, Property 16: TSCC and ContextPreviewPanel state independence`
    - **Validates: Requirements 17.4**

- [x] 13. Remaining backend property tests
  - [ ]* 13.1 Write property test: Scope label correctness (`backend/tests/test_property_tscc_scope_labels.py`)
    - **Property 3: Scope label correctness**
    - Generate random scope_type values and project_id values using hypothesis
    - Verify: when `scope_type` is `"workspace"`, `context.scope_label` is `"Workspace: SwarmWS (General)"`; when `scope_type` is `"project"` and `project_id` is non-null, `context.scope_label` contains the project display name
    - Verify `context.thread_title` is always present and non-empty
    - Verify `context.mode` appears only when explicitly set
    - Tag: `Feature: thread-scoped-cognitive-context, Property 3: Scope label correctness`
    - **Validates: Requirements 3.1, 3.2, 3.3, 8.1, 8.2**

- [x] 14. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Run `cd backend && pytest` to verify all backend tests pass
  - Run `cd desktop && npm test -- --run` to verify all frontend tests pass
  - Verify TSCC panel renders correctly in ChatPage between message list and ChatInput
  - Verify SSE telemetry events flow from AgentManager → TelemetryEmitter → SSE → useTSCCState → TSCCPanel
  - Verify snapshots are created at trigger points and displayed inline in thread history
  - Verify thread switching resets TSCC state with no cross-thread leakage
  - Verify expand/collapse and pin preferences are preserved per thread
  - Verify TSCC and ContextPreviewPanel operate independently
  - Verify all ARIA accessibility attributes are present and keyboard navigation works

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability (Req 1–20)
- Checkpoints ensure incremental validation at backend-complete and frontend-complete boundaries
- Property tests validate universal correctness properties (P1–P18) from the design document
- Unit tests validate specific examples and edge cases
- TSCC state is in-memory only (not DB-persisted); snapshots provide the persistence mechanism
- TelemetryEmitter errors never interrupt the agent SSE stream (best-effort telemetry)
- All backend responses use snake_case; frontend `toCamelCase()` in `desktop/src/services/tscc.ts` handles conversion
- CSS variables (`--color-*`) used for all theming — no hardcoded colors
- Module-level docstrings required on all new files per project code documentation standards
