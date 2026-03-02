<!-- PE-REVIEWED -->
# Tab Persistence DB Migration Bugfix Design

## Overview

Chat tabs lose their session references after app restart because the Tauri WebKit webview's `localStorage` doesn't persist to disk on macOS. Messages remain in the SQLite database but no tab points to them. The fix replaces `localStorage` entirely with filesystem-based persistence: tab state is written to `~/.swarm-ai/open_tabs.json` via the backend API (`GET/PUT /api/settings/open-tabs`). On startup, `ChatPage` calls `restoreFromFile()` which reads the exact tabs the user had open. Messages are loaded from the DB on demand when a tab becomes active. Tab changes are written back to the file with a 500ms debounce. The change is scoped to `useUnifiedTabState.ts` (new `restoreFromFile` method, debounced file save effect), `ChatPage.tsx` (file restore on mount), a new `tabPersistenceService` (`desktop/src/services/tabPersistence.ts`), and two new endpoints on the settings router (`GET/PUT /api/settings/open-tabs`).

## Glossary

- **Bug_Condition (C)**: `localStorage` does not persist across app restarts on macOS Tauri WebKit — tab state is lost
- **Property (P)**: Tabs are restored from `~/.swarm-ai/open_tabs.json` with correct `sessionId` and `title`, and the active tab's messages load from the DB
- **Preservation**: All existing tab operations (new conversation, tab switching, history sidebar, streaming, tab close/add, sessionStorage) continue to work identically
- **`useUnifiedTabState`**: The hook in `desktop/src/hooks/useUnifiedTabState.ts` that manages the `Tab_Map` (authoritative in-memory store) and persists the serializable subset to `~/.swarm-ai/open_tabs.json` via the backend API
- **`tabPersistenceService`**: Frontend service (`desktop/src/services/tabPersistence.ts`) that reads/writes tab state via `GET/PUT /api/settings/open-tabs`
- **`restoreFromFile()`**: Async method on `useUnifiedTabState` that loads tab state from the backend API on startup
- **`listSessions(agentId?)`**: Frontend service call (`desktop/src/services/chat.ts`) that hits `GET /api/chat/sessions` — returns sessions sorted by `last_accessed` DESC
- **`BackendStartupOverlay`**: Component that gates route rendering until the backend is healthy (production mode only; skipped in dev mode via `isDev`)
- **`isBackendReady`**: State in `App.tsx` — `true` in dev mode immediately, `true` in production after overlay confirms health

## Bug Details

### Fault Condition

The bug manifests when the app restarts on macOS and `localStorage` contains no tab data. The `useUnifiedTabState` hook's synchronous initialization block calls `loadTabsFromStorage()`, gets `null`, and creates a single default tab with no `sessionId`. The user sees a welcome screen instead of their previous conversations.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type AppStartupContext
  OUTPUT: boolean
  
  RETURN loadTabsFromStorage() IS NULL
         AND sessionsExistInDatabase()
         AND userHadOpenTabsBeforeRestart()
END FUNCTION
```


### Examples

- **Example 1 (Primary bug)**: User has 3 tabs open with active sessions. App restarts. `loadTabsFromStorage()` returns `null`. Currently: single empty tab with welcome screen. Expected: 3 tabs restored from DB with their session titles and messages.
- **Example 2 (Single session)**: User has 1 tab with a long conversation. App restarts. `localStorage` is empty. Currently: welcome screen. Expected: 1 tab restored with the session's title and messages loaded.
- **Example 3 (No prior sessions)**: Fresh install, no sessions in DB. App starts. `loadTabsFromStorage()` returns `null`. Currently: single default tab. Expected: single default tab (same behavior — no regression).
- **Example 4 (localStorage works)**: On a platform where `localStorage` persists, tabs restore from `localStorage` as before. No DB call made. Expected: identical to current behavior.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Starting a brand new conversation (no prior session) creates a fresh tab, obtains a `sessionId` from the streaming response, and functions normally
- Tab switching saves/restores tab state from the in-memory `Tab_Map` without disruption
- Chat History sidebar session selection loads messages into a tab
- Active streaming continues without interruption
- `sessionStorage` for pending question persistence operates independently
- Closing a tab aborts streaming, removes the tab, auto-creates a default tab if last
- Adding a new tab enforces the MAX_OPEN_TABS limit of 6

**Scope:**
All inputs that do NOT involve app startup with empty `localStorage` should be completely unaffected by this fix. This includes:
- All runtime tab operations (add, close, switch, rename)
- Message sending and streaming
- History sidebar interactions
- Session creation and updates (already write to DB via streaming lifecycle)


## Hypothesized Root Cause

Based on the bug description and code analysis, the root cause is:

1. **Tauri WebKit localStorage Not Persisted**: On macOS, the Tauri 2.0 WebKit webview does not write `localStorage` data to disk. The WebKit LocalStorage directory is empty after app quit. This is a platform-level issue — not a bug in the application code itself.

2. **No Fallback Path in Initialization**: The `useUnifiedTabState` hook's synchronous initialization block (lines 200-220 in `useUnifiedTabState.ts`) has a simple two-branch structure:
   - If `loadTabsFromStorage()` returns data → hydrate tabs from localStorage
   - If `loadTabsFromStorage()` returns `null` → create a single default tab
   
   There is no third branch to fall back to the database. The DB contains all session data (the `sessions` table has `id`, `title`, `last_accessed`, `agent_id`) but the frontend never queries it during tab initialization.

3. **Synchronous Init vs Async DB Call**: The initialization runs synchronously via a `useRef` guard (`initialized.current`). A DB fallback requires an async API call (`listSessions`), which cannot run in the synchronous init block. This is a structural constraint that requires the initialization to be split into two phases: synchronous (localStorage attempt) and async (DB fallback).

4. **Session Data Already Exists**: The backend `sessions` table already tracks everything needed for tab restoration — `id` (sessionId), `title`, `last_accessed`, `agent_id`. The `GET /api/chat/sessions` endpoint returns sessions sorted by `last_accessed` DESC. No schema changes are needed.

## Correctness Properties

Property 1: Fault Condition - DB Fallback Restores Tabs When localStorage Is Empty

_For any_ app startup where `loadTabsFromStorage()` returns `null` AND sessions exist in the database, the system SHALL fetch up to MAX_OPEN_TABS (6) most recently accessed sessions from the backend and create tabs with their `sessionId` and `title` populated, so that the active tab loads its messages from the DB.

**Validates: Requirements 2.1, 2.2, 2.4**

Property 2: Fault Condition - Empty DB Graceful Degradation

_For any_ app startup where `loadTabsFromStorage()` returns `null` AND no sessions exist in the database (or the API call fails), the system SHALL create a single default tab with no `sessionId` and title "New Session", identical to the current behavior.

**Validates: Requirements 2.1**

Property 3: Preservation - localStorage Fast Path Unchanged

_For any_ app startup where `loadTabsFromStorage()` returns valid tab data, the system SHALL use that data directly without making any backend API call, preserving the current synchronous initialization performance and behavior.

**Validates: Requirements 2.6, 3.1, 3.2**

Property 4: Preservation - Runtime Tab Operations Unchanged

_For any_ runtime tab operation (add, close, switch, rename, streaming) performed after initialization, the fixed code SHALL produce exactly the same behavior as the original code, preserving all existing tab management, streaming, and history sidebar functionality.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

Property 5: Safety - No User Work Lost During Async Restore

_For any_ DB fallback fetch, if the user has already started a conversation on the default tab (the tab has a `sessionId` set), the system SHALL NOT replace that tab with DB-restored tabs. The restore is only applied when the default tab is still pristine (no `sessionId`). Note: messages in the tab map are always empty at init time — only `sessionId` is a reliable indicator of user interaction.

**Validates: Requirements 2.1, 3.1**


## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `desktop/src/hooks/useUnifiedTabState.ts`

**Function**: `useUnifiedTabState`

**Specific Changes**:

1. **Add `needsDBFallback` flag**: During the synchronous init block, if `loadTabsFromStorage()` returns `null`, set a `useRef<boolean>` flag `needsDBFallback = true`. This signals to the consumer that a DB fetch should be attempted. Do NOT embed the `chatService` call inside the hook — the hook remains a pure state manager with no service dependencies.

2. **Expose `needsDBFallback` and `restoreFromSessions`**:
   - `needsDBFallback: boolean` — `true` when localStorage was empty and no DB restore has occurred yet. Backed by a `useRef` (not state), so changes do NOT trigger re-renders. The consumer's `useEffect` must check it on mount, not react to value changes.
   - `restoreFromSessions(sessions: ChatSession[]): void` — Accepts an array of sessions, clears the default tab, creates tabs from the session data, sets the first as active (updating both `activeTabIdRef` and `setActiveTabId` state), writes the new tab list and `activeTabId` to localStorage (so the next restart has fresh data), and sets `needsDBFallback = false`. This keeps the hook decoupled from the network layer. Note: the newly generated tab UUIDs won't match any stale `activeTabId` in localStorage from a previous session — the write to localStorage here ensures consistency.

3. **Race condition guard in `restoreFromSessions`**: Before replacing tabs, check that the current default tab is still pristine — specifically, that it has no `sessionId` set. (Messages in the tab map are always empty at init time since they're only populated after `loadSessionMessages`, so checking messages is unreliable.) If the default tab already has a `sessionId` (meaning the user started a conversation during the async fetch window), skip the restore and set `needsDBFallback = false`. This prevents losing user work.

4. **Tab creation from DB sessions**: When creating tabs from DB sessions, generate new tab UUIDs (frontend concern) but set `sessionId` from the DB session's `id`, `title` from the session's `title`, and `agentId` from the session's `agentId`. For sessions with `null`/`undefined` `agentId` (legacy data from before agent support was added), use the `defaultAgentId` as fallback — this ensures old sessions still restore correctly. Set `isNew = false` since these are existing sessions.

5. **Re-mount guard**: The `needsDBFallback` ref is set once during the `initialized.current` block. Hot module reload or re-mount won't re-trigger it because `initialized.current` remains `true`.

6. **Graceful handling of sessions with no messages**: A DB-restored tab may point to a session that has no messages (e.g., session record exists but messages were deleted, or the session was created but never completed). When `loadSessionMessages` returns an empty array for such a tab, display the welcome screen with the session title preserved — do not treat it as an error. The tab remains usable for starting a new conversation under the same session ID.

**File**: `backend/routers/chat.py`

**Endpoint**: `GET /api/chat/sessions`

**Specific Changes**:

1. **Add `limit` query param with validation**: Add an optional `limit: int | None = None` parameter to `list_sessions()`. Validate: if provided, clamp to range `[1, 100]` (reject `<= 0` with 422, cap at 100 silently). Pass the `limit` down to `session_manager.list_sessions()` so the SQL query uses `LIMIT` — avoid fetching all sessions then slicing in Python.

2. **Backend `session_manager.list_sessions`**: Add an optional `limit` parameter. When provided, append `LIMIT ?` to the SQL query. This pushes the bound to the database layer for efficiency. Add a secondary sort by `created_at DESC` after `last_accessed DESC` to ensure deterministic ordering when two sessions have the same `last_accessed` timestamp.

**File**: `desktop/src/services/chat.ts`

**Specific Changes**:

1. **Pass `limit` param**: Update `listSessions(agentId?, limit?)` to accept an optional `limit` parameter and pass it as a query param: `GET /api/chat/sessions?limit=6`.

**File**: `desktop/src/pages/ChatPage.tsx`

**Specific Changes**:

1. **Orchestrate DB fallback with retry**: Add a `useEffect` that runs on mount (empty dependency array or mount-only guard) and checks `needsDBFallback`. Since `needsDBFallback` is a `useRef` (not state), it won't trigger re-renders — the effect must check it synchronously on mount. When `true`, call `chatService.listSessions(undefined, MAX_OPEN_TABS)` as a one-shot `fetch` call (NOT a `useQuery`) to avoid cache conflicts with the existing `['chatSessions', selectedAgentId]` query used by the sessions sidebar. On success, call `restoreFromSessions(sessions)`. On error, retry up to 3 times with 1-second delay (handles dev mode where the backend may start after the frontend). After all retries exhausted, log a warning (`console.warn('[ChatPage] DB tab restore failed after retries:', error)`) and leave the default tab in place. Use an `AbortController` or mounted-ref guard to cancel retries if the component unmounts.

2. **Reuse `isLoadingHistory` for loading state**: While the DB fallback fetch is in flight, set `isLoadingHistory = true` to show the existing loading indicator. This avoids adding a new state variable and reuses the same visual treatment as tab-switch loading. Reset to `false` after restore completes or fails.

3. **No changes to message loading**: The existing `useEffect` that syncs active tab content when `activeTabId` changes will automatically load messages for the DB-restored active tab via `loadSessionMessages()`. No additional wiring needed.

**Observability**:

- Log `[useUnifiedTabState] Restored N tabs from localStorage` or `[useUnifiedTabState] localStorage empty, needs DB fallback` during init
- Log `[ChatPage] Restoring tabs from DB: fetched N sessions` on successful DB fetch
- Log `[ChatPage] DB tab restore failed: <error>` on API error (console.warn, not console.error — it's a graceful degradation)
- Log `[ChatPage] DB restore skipped: default tab already has sessionId` if the race condition guard triggers


## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that mock `localStorage.getItem` to return `null` (simulating the macOS Tauri behavior) and verify that the hook's initialization creates only a default tab with no `sessionId`. Run these tests on the UNFIXED code to observe the failure mode.

**Test Cases**:
1. **Empty localStorage Test**: Mock `localStorage.getItem` to return `null`. Initialize `useUnifiedTabState`. Assert that `openTabs` has exactly 1 tab with no `sessionId` and title "New Session" (will demonstrate the bug — no DB fallback occurs).
2. **Sessions Exist in DB Test**: Mock `localStorage.getItem` to return `null` AND mock `chatService.listSessions` to return 3 sessions. Initialize the hook. Assert that only 1 default tab exists (will demonstrate the bug — DB is never queried).
3. **Multiple Tabs Lost Test**: Mock `localStorage.getItem` to return `null` when the DB has 5 sessions. Assert only 1 tab is created (will demonstrate the bug — all 5 sessions are lost).

**Expected Counterexamples**:
- Hook creates a single default tab with no `sessionId` even when sessions exist in the DB
- No API call to `listSessions` is made during initialization
- Root cause confirmed: no DB fallback path exists in the synchronous init block

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FUNCTION expectedBehavior(result)
  INPUT: result of type TabInitializationResult
  OUTPUT: boolean

  IF dbSessions.length > 0 THEN
    RETURN result.openTabs.length == MIN(dbSessions.length, MAX_OPEN_TABS)
           AND ALL tabs have sessionId matching a DB session
           AND ALL tabs have title matching the DB session title
           AND result.activeTabId IS NOT NULL
           AND result.isRestoringFromDB transitions from true to false
  ELSE
    RETURN result.openTabs.length == 1
           AND result.openTabs[0].sessionId IS NULL
           AND result.openTabs[0].title == "New Session"
  END IF
END FUNCTION

FOR ALL input WHERE isBugCondition(input) DO
  result := useUnifiedTabState_fixed(input)
  ASSERT expectedBehavior(result)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT useUnifiedTabState_original(input) = useUnifiedTabState_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for localStorage-present scenarios and runtime tab operations, then write property-based tests capturing that behavior.

**Test Cases**:
1. **localStorage Present Preservation**: When `localStorage` has valid tab data, verify the hook hydrates tabs identically to the original code — no DB call made, same tab IDs, same titles, same sessionIds
2. **Tab Add Preservation**: Verify `addTab()` creates a new tab with the same structure and enforces MAX_OPEN_TABS identically
3. **Tab Close Preservation**: Verify `closeTab()` removes the tab, aborts streaming, and auto-creates a default tab if last — identical to original
4. **Tab Switch Preservation**: Verify `selectTab()` updates `activeTabId` and triggers the same state transitions
5. **Session Update Preservation**: Verify `updateTabSessionId()` writes to both the tab map and triggers localStorage persistence identically

### Unit Tests

- Test DB fallback initialization when localStorage is empty and sessions exist
- Test DB fallback initialization when localStorage is empty and no sessions exist
- Test DB fallback respects MAX_OPEN_TABS limit (e.g., 10 sessions in DB → only 6 tabs)
- Test DB fallback ordering (most recently accessed sessions first)
- Test `isRestoringFromDB` transitions: `true` during fetch, `false` after completion
- Test graceful handling of API errors during DB fallback (falls back to default tab)
- Test that localStorage fast path skips DB call entirely
- Test tab creation from DB sessions has correct `sessionId`, `title`, `agentId`, `isNew=false`

### Property-Based Tests

- Generate random sets of DB sessions (0 to 20) with random titles and timestamps. When localStorage is empty, verify exactly `min(N, MAX_OPEN_TABS)` tabs are created, ordered by `last_accessed` DESC
- Generate random valid localStorage tab arrays (1 to 6 tabs). Verify the hook hydrates them identically to the original code with no DB call
- Generate random sequences of tab operations (add, close, switch) after initialization. Verify all operations produce identical results regardless of whether tabs were restored from localStorage or DB

### Integration Tests

- Test full app startup flow: backend starts → overlay clears → ChatPage mounts → tabs restored from DB → active tab messages load
- Test that DB-restored tabs can be used for new conversations (send message, get sessionId from stream)
- Test that closing a DB-restored tab and opening a new one works correctly
- Test tab persistence round-trip: restore from DB → use tabs → restart → restore from localStorage (if it persists) or DB again
