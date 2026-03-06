# Implementation Plan: App Restart Performance

## Overview

Optimize the SwarmAI desktop app startup sequence by deferring non-critical backend work, simplifying the overlay, prioritizing frontend queries, paginating messages, batching cleanup, deferring sidebar fetch, and adding timing instrumentation. Backend changes are Python/FastAPI; frontend changes are React/TypeScript.

## Tasks

- [x] 1. Backend: Deferred channel gateway and system status extensions
  - [x] 1.1 Add `_startup_state` field to `ChannelGateway` class in `backend/channels/gateway.py`
    - Add `_startup_state: str` initialized to `"not_started"`
    - Expose via a property for the system status endpoint
    - _Requirements: 1.5_

  - [x] 1.2 Modify `lifespan()` in `backend/main.py` to defer channel gateway startup
    - Query `db.channels.count()` before calling `channel_gateway.startup()`
    - If zero channels: skip startup, set `_startup_state = "not_started"`
    - If channels exist: wrap `channel_gateway.startup()` in `asyncio.create_task()` with state transitions (`"starting"` → `"started"` / `"failed"`)
    - Ensure `_startup_complete = True` is set before the deferred task completes
    - Add fallback: if `channels.count()` fails, call startup synchronously (current behavior)
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 1.3 Extend `ChannelGatewayStatus` and `SystemStatusResponse` in `backend/routers/system.py`
    - Add `startup_state: str` to `ChannelGatewayStatus`
    - Add `startup_time_ms: Optional[float]` and `phase_timings: Optional[dict[str, float]]` to `SystemStatusResponse`
    - Update `initialized` flag logic: `channel_gateway.running` no longer required when `startup_state == "not_started"`
    - Wire `_startup_state` from the gateway instance into the status response
    - _Requirements: 1.5, 8.5, 8.6_

  - [ ]* 1.4 Write property test for deferred gateway (Property 1)
    - **Property 1: Deferred gateway does not block startup**
    - Use `hypothesis` + `pytest-asyncio` to test `lifespan()` with mocked gateway
    - For any positive channel count, assert `_startup_complete` is `True` before gateway task completes
    - **Validates: Requirements 1.2**

  - [ ]* 1.5 Write property test for system status metadata (Property 11)
    - **Property 11: System status response contains startup metadata**
    - Use `hypothesis` to generate system status responses after startup
    - Assert `startup_state` ∈ `{"not_started", "starting", "started", "failed"}`, `startup_time_ms >= 0`, and `phase_timings` contains all expected keys with non-negative values
    - **Validates: Requirements 1.5, 8.5, 8.6**

- [x] 2. Backend: Deferred refresh_builtin_defaults
  - [x] 2.1 Modify `lifespan()` fast path to defer `refresh_builtin_defaults()` to a background task
    - Wrap in `asyncio.create_task()` that runs after `_startup_complete = True`
    - Keep synchronous on full-init path (dev-mode, no seed.db)
    - Log success/failure of the background task
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [ ]* 2.2 Write property test for deferred refresh (Property 2)
    - **Property 2: Deferred refresh_builtin_defaults does not block startup**
    - Use `hypothesis` + `pytest-asyncio` to test fast-path lifespan with mocked initialization_manager
    - Assert `_startup_complete` is `True` before `refresh_builtin_defaults()` finishes
    - **Validates: Requirements 6.1**

- [x] 3. Backend: Startup timing instrumentation
  - [x] 3.1 Add `time.monotonic()` instrumentation to `lifespan()` phases in `backend/main.py`
    - Wrap each phase (database init, workspace verify, config/permission load, agent manager configure) with timing
    - Store `phase_timings` dict and `startup_time_ms` in module-level variables
    - Log per-phase and total durations
    - Wire stored timings into the system status endpoint response (from task 1.3)
    - _Requirements: 8.1, 8.2, 8.5, 8.6_

- [x] 4. Backend: Paginated session messages
  - [x] 4.1 Add composite index `idx_messages_session_created` on `messages(session_id, created_at, rowid)`
    - Add as migration or in schema DDL
    - _Requirements: 4.1_

  - [x] 4.2 Add `list_by_session_paginated()` method to `SQLiteMessagesTable` in `backend/database/sqlite.py`
    - Accept `session_id`, optional `limit` (1–200), optional `before_id`
    - Use `(created_at, rowid)` tuple for cursor-based tie-breaking
    - When both params provided: `WHERE session_id = ? AND (created_at, rowid) < (subquery) ORDER BY created_at DESC, rowid DESC LIMIT ?`, then reverse in Python
    - When only limit: most recent N messages (DESC + reverse)
    - When neither: full fetch (backward compat, same as `list_by_session`)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.10_

  - [x] 4.3 Update chat router in `backend/routers/chat.py` with `limit` and `before_id` query params
    - Add `limit: Optional[int] = Query(None, ge=1, le=200)` and `before_id: Optional[str] = Query(None)`
    - Call `list_by_session_paginated()` when either param is present, else existing behavior
    - _Requirements: 4.1, 4.4_

  - [ ]* 4.4 Write property test for paginated message count (Property 5)
    - **Property 5: Paginated message count respects limit**
    - Use `hypothesis` to generate sessions with N messages and limit L
    - Assert `len(result) == min(N, L)`
    - **Validates: Requirements 4.2**

  - [ ]* 4.5 Write property test for cursor pagination (Property 6)
    - **Property 6: Cursor pagination returns only older messages**
    - Use `hypothesis` to generate sessions with messages sharing timestamps
    - Assert every returned message has `(created_at, rowid)` strictly less than the cursor message
    - **Validates: Requirements 4.3**

  - [ ]* 4.6 Write property test for backward compatibility (Property 7)
    - **Property 7: Unpaginated query returns all messages**
    - Use `hypothesis` to generate sessions, call with no params, compare to `list_by_session()`
    - Assert identical ordered results
    - **Validates: Requirements 4.4**

  - [ ]* 4.7 Write property test for pagination round-trip (Property 8)
    - **Property 8: Pagination round-trip**
    - Use `hypothesis` to generate sessions, fetch all via chained paginated requests (limit=50), compare to full fetch
    - Assert identical ordered set
    - **Validates: Requirements 4.10**

- [x] 5. Backend: Batched legacy cleanup
  - [x] 5.1 Refactor `_cleanup_legacy_content()` in `backend/core/swarm_workspace_manager.py`
    - Create `_batch_remove(paths_to_remove: list[tuple[Path, str]]) -> list[str]` sync function
    - Collect all legacy paths first, then call `await anyio.to_thread.run_sync(lambda: _batch_remove(paths))` once
    - Log per-item failures, continue with remaining items
    - Preserve `.legacy_cleaned` marker file mechanism
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [ ]* 5.2 Write property test for cleanup idempotence (Property 10)
    - **Property 10: Legacy cleanup idempotence**
    - Use `hypothesis` to generate temp directories with random legacy content
    - Assert running cleanup twice produces same filesystem state as once (marker file prevents second run)
    - **Validates: Requirements 5.2**

- [x] 6. Checkpoint — Backend changes complete
  - Ensure all backend tests pass, ask the user if questions arise.

- [x] 7. Frontend: Update system service types and toCamelCase
  - [x] 7.1 Update `SystemStatus` interface and `toCamelCase()` in `desktop/src/services/system.ts`
    - Add `startupState: string` to `ChannelGatewayStatus` interface
    - Add `startupTimeMs: number | null` and `phaseTimings: Record<string, number> | null` to `SystemStatus` interface
    - Update `toCamelCase()` to map `startup_state`, `startup_time_ms`, `phase_timings` from backend response
    - _Requirements: 1.5, 8.5, 8.6_

- [x] 8. Frontend: Simplified BackendStartupOverlay
  - [x] 8.1 Rewrite `buildInitSteps()` and simplify `InitStep` interface in `desktop/src/components/common/BackendStartupOverlay.tsx`
    - Remove `children`, `labelKey`, `interpolation` fields from `InitStep`
    - Return exactly 3 flat steps: `database` ("Loading your data"), `agent` ("Preparing your agent"), `workspace` ("Setting up workspace")
    - No channel gateway step
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 8.2 Create `StatusIcon` SVG component in BackendStartupOverlay
    - Filled green checkmark circle for success (16×16 SVG)
    - Animated spinner for in_progress (existing `<Spinner size="sm" />`)
    - Filled red error circle for error (16×16 SVG)
    - Open circle for pending
    - Remove text character indicators (`✓`, `○`, `✗`)
    - _Requirements: 2.5_

  - [x] 8.3 Update TIMING constants and animation logic
    - Set `stepAnimationDelay: 100`, `fadeOutDelay: 200`, `fadeOutDuration: 200`
    - Total animation budget: ~700ms (down from ~2050ms)
    - Remove monospace font inline styles
    - _Requirements: 2.4, 2.7, 2.8_

  - [x] 8.4 Add fast-startup shortcut
    - When all readiness checks pass on first system status poll, skip step-by-step animation
    - Show all 3 steps as checked simultaneously, proceed directly to fade-out
    - _Requirements: 2.9_

  - [x] 8.5 Add app version display below "SwarmAI" title
    - Source version from health check response (`response.data.version`)
    - Store in `useState<string>`, display as muted text (e.g., "v0.8.2")
    - Graceful fallback: show nothing if version unavailable
    - _Requirements: 2.6_

  - [x] 8.6 Update dismissal logic to use `checkReadiness()` only
    - Gate on `agentReady AND workspaceReady`
    - Ignore `initialized` field from SystemStatusResponse
    - _Requirements: 2.10, 2.11_

  - [ ]* 8.7 Write property test for overlay step count (Property 3)
    - **Property 3: Overlay builds exactly 3 flat steps**
    - Use `fast-check` to generate arbitrary `SystemStatus` objects
    - Assert `buildInitSteps()` returns exactly 3 items with ids `["database", "agent", "workspace"]` and no `children`
    - **Validates: Requirements 2.1, 2.2, 2.3**

  - [ ]* 8.8 Write property test for checkReadiness (Property 4)
    - **Property 4: checkReadiness ignores the initialized field**
    - Use `fast-check` to generate `SystemStatus` with `agent.ready=true` and `swarmWorkspace.ready=true` but varying `initialized`, `channelGateway.running`, `channelGateway.startupState`
    - Assert `checkReadiness()` returns `allReady === true`
    - **Validates: Requirements 2.10**

- [x] 9. Frontend: Query prioritization in ChatPage
  - [x] 9.1 Add `messagesReady` state and `enabled` guards to queries in `desktop/src/pages/ChatPage.tsx`
    - Add `const [messagesReady, setMessagesReady] = useState(false)`
    - Set `messagesReady = true` after `loadSessionMessages()` completes (or immediately if no active tab)
    - Add `enabled: messagesReady` to skills, mcpServers, plugins queries (P3)
    - Add `enabled: !!selectedAgentId && messagesReady` to sessions query (P2)
    - Agents query remains unconditional (P1)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 10. Frontend: Paginated message loading
  - [x] 10.1 Add `getSessionMessagesPaginated()` to chat service in `desktop/src/services/chat.ts`
    - Accept `sessionId`, optional `limit`, optional `beforeId`
    - Build URL with query params, call API, map response with `toMessageCamelCase`
    - _Requirements: 4.1_

  - [x] 10.2 Update tab restore to use `limit=50` for initial message load
    - Modify `loadSessionMessages()` (or equivalent) to call `getSessionMessagesPaginated(sid, 50)` instead of `getSessionMessages(sid)`
    - Set `hasMoreMessages` based on whether 50 messages were returned
    - Set `messagesReady = true` after load completes
    - _Requirements: 4.5_

  - [x] 10.3 Add infinite scroll with `loadOlderMessages` callback
    - Add `hasMoreMessages` and `isLoadingOlderMessages` state
    - Implement `loadOlderMessages()`: fetch 50 messages before oldest displayed message via `getSessionMessagesPaginated`
    - Attach scroll event listener to messages container, trigger on `scrollTop === 0`
    - Show `<Spinner />` at top of message list while loading
    - Set `hasMoreMessages = false` when response length < 50
    - _Requirements: 4.6, 4.7, 4.9_

  - [x] 10.4 Add scroll position preservation with `useLayoutEffect`
    - Capture `scrollHeight` before prepending messages
    - Restore `scrollTop = newScrollHeight - prevScrollHeight` in `useLayoutEffect` after DOM update
    - _Requirements: 4.8_

  - [ ]* 10.5 Write property test for end-of-history detection (Property 9)
    - **Property 9: End-of-history detection**
    - Use `fast-check` to generate response arrays with length < limit
    - Assert `hasMoreMessages` is set to `false` when `response.length < limit`
    - **Validates: Requirements 4.9**

- [x] 11. Frontend: Deferred WorkspaceExplorer tree fetch
  - [x] 11.1 Add `TreeSkeleton` and `TreeErrorState` components in `desktop/src/components/WorkspaceExplorer.tsx`
    - `TreeSkeleton`: 6–8 pulsing placeholder lines with indentation
    - `TreeErrorState`: inline error message with "Retry" button calling `refetch`
    - Ensure tree fetch is async and non-blocking to ChatPage
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 12. Frontend: Timing instrumentation
  - [x] 12.1 Add overlay dismissal timing log in BackendStartupOverlay
    - Log `[Overlay] Health poll to dismissal: Xms` using `performance.now()` delta
    - _Requirements: 8.3_

  - [x] 12.2 Add ChatPage time-to-interactive log
    - Store `mountTimeRef = performance.now()` on mount
    - Log `[ChatPage] Time to interactive: Xms` when `messagesReady` becomes true
    - _Requirements: 8.4_

- [x] 13. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Backend tasks (1–5) are independent and can be implemented in parallel
- Frontend system service update (task 7) must precede overlay and ChatPage changes
- Property tests validate universal correctness properties from the design document
- Checkpoints at task 6 (backend complete) and task 13 (all complete) ensure incremental validation
