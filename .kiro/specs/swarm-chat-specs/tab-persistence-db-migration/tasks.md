# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - localStorage Empty Loses All Tabs
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate tabs are not restored from DB when localStorage is empty
  - **Scoped PBT Approach**: Generate random sets of DB sessions (1–6) with random titles and timestamps. Mock `localStorage.getItem` to return `null` for `OPEN_TABS_STORAGE_KEY`. Initialize `useUnifiedTabState` and simulate the `ChatPage` DB fallback orchestration. Assert that `openTabs.length == min(sessions.length, MAX_OPEN_TABS)` and each tab has a matching `sessionId` and `title` from the DB sessions ordered by `last_accessed` DESC.
  - **Test file**: `desktop/src/hooks/__tests__/tabPersistenceDBMigration.fault.pbt.test.ts`
  - **Setup**: Use `vitest` + `fast-check` + `@testing-library/react` (matching existing test patterns in `useUnifiedTabState.test.ts`)
  - **Bug condition from design**: `isBugCondition(input)` = `loadTabsFromStorage() IS NULL AND sessionsExistInDatabase() AND userHadOpenTabsBeforeRestart()`
  - **Expected behavior from design**: `expectedBehavior(result)` = tabs restored from DB with correct `sessionId`, `title`, ordered by `last_accessed` DESC, capped at `MAX_OPEN_TABS`
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bug exists: hook creates 1 default tab instead of restoring from DB)
  - Document counterexamples found: e.g., "3 sessions in DB but only 1 empty tab created with no sessionId"
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.4_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - localStorage Fast Path and Runtime Tab Operations Unchanged
  - **IMPORTANT**: Follow observation-first methodology
  - **Test file**: `desktop/src/hooks/__tests__/tabPersistenceDBMigration.preservation.pbt.test.ts`
  - **Setup**: Use `vitest` + `fast-check` + `@testing-library/react` (matching existing patterns)
  - **Observation phase** (run on UNFIXED code):
    - Observe: When `localStorage` has valid tab data (1–6 serialized tabs), `useUnifiedTabState` hydrates them with correct IDs, titles, sessionIds, and no backend API call is made
    - Observe: `addTab()` creates a new tab with `isNew=true`, no `sessionId`, enforces `MAX_OPEN_TABS` limit of 6
    - Observe: `closeTab()` removes the tab, auto-creates a default tab if it was the last one
    - Observe: `selectTab()` updates `activeTabId` correctly
    - Observe: `updateTabState()` with `sessionId` persists to localStorage
  - **Property tests** (from Preservation Requirements in design):
    - Property 2a: For all valid localStorage tab arrays (1–6 tabs with random titles/sessionIds), the hook hydrates them identically — same count, same IDs, same titles, same sessionIds. No DB call made.
    - Property 2b: For all sequences of runtime tab operations (add, close, switch) after localStorage-based init, the hook produces the same results — tab count invariants hold, activeTabId always points to a valid tab, MAX_OPEN_TABS enforced.
    - Property 2c: For all tab metadata updates (title, sessionId, status), localStorage is written with the updated serializable state.
  - Verify tests pass on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 2.6, 3.1, 3.2, 3.3, 3.5, 3.6, 3.7_


- [x] 3. Implement dual-persistence DB fallback fix

  - [x] 3.1 Add `limit` query param to backend `GET /api/chat/sessions` endpoint
    - In `backend/routers/chat.py`: Add optional `limit: int | None = None` parameter to `list_sessions()`
    - Validate: reject `limit <= 0` with 422, silently cap at 100
    - Pass `limit` down to `session_manager.list_sessions(agent_id, limit)`
    - In `backend/core/session_manager.py`: Add optional `limit` parameter to `list_sessions()`
    - When provided, append `LIMIT ?` to the SQL query
    - Add secondary sort by `created_at DESC` after `last_accessed DESC` for deterministic ordering
    - _Bug_Condition: isBugCondition(input) where loadTabsFromStorage() IS NULL AND sessionsExistInDatabase()_
    - _Expected_Behavior: Backend returns at most `limit` sessions sorted by last_accessed DESC, created_at DESC_
    - _Preservation: Existing calls without `limit` param return all sessions as before_
    - _Requirements: 2.8_

  - [x] 3.2 Update frontend `listSessions` service to accept `limit` param
    - In `desktop/src/services/chat.ts`: Update `listSessions(agentId?, limit?)` to accept optional `limit`
    - Pass `limit` as query param: `GET /api/chat/sessions?limit=6`
    - _Bug_Condition: Frontend needs to request bounded session list for tab restore_
    - _Expected_Behavior: `listSessions(undefined, 6)` sends `?limit=6` to backend_
    - _Preservation: Existing calls without `limit` continue to work unchanged_
    - _Requirements: 2.1, 2.8_

  - [x] 3.3 Add `needsDBFallback` flag and `restoreFromSessions` method to `useUnifiedTabState`
    - In `desktop/src/hooks/useUnifiedTabState.ts`:
    - Add `useRef<boolean>` flag `needsDBFallback`, set `true` during sync init when `loadTabsFromStorage()` returns `null`
    - Expose `needsDBFallback` (ref value) in the hook return type `UseUnifiedTabStateReturn`
    - Add `restoreFromSessions(sessions: ChatSession[]): void` method:
      - Race condition guard: check default tab has no `sessionId` before replacing
      - Clear default tab, create tabs from sessions (new UUIDs, `sessionId` from DB, `title` from DB, `agentId` from DB with `defaultAgentId` fallback for legacy null values, `isNew = false`)
      - Set first tab as active (update both `activeTabIdRef` and `setActiveTabId` state)
      - Write new tab list and `activeTabId` to localStorage for consistency
      - Set `needsDBFallback = false`
    - Add observability logging: `[useUnifiedTabState] Restored N tabs from localStorage` or `localStorage empty, needs DB fallback`
    - _Bug_Condition: isBugCondition(input) where loadTabsFromStorage() IS NULL_
    - _Expected_Behavior: needsDBFallback=true signals consumer to fetch from DB; restoreFromSessions creates tabs from DB sessions with correct sessionId, title, agentId_
    - _Preservation: When localStorage has data, needsDBFallback=false, restoreFromSessions never called_
    - _Requirements: 2.1, 2.2, 2.4, 2.5_

  - [x] 3.4 Orchestrate DB fallback with retry in `ChatPage.tsx`
    - In `desktop/src/pages/ChatPage.tsx`:
    - Add `useEffect` (mount-only) that checks `needsDBFallback`
    - When `true`: call `chatService.listSessions(undefined, MAX_OPEN_TABS)` as a one-shot fetch (NOT `useQuery`)
    - On success: call `restoreFromSessions(sessions)`
    - On error: retry up to 3 times with 1-second delay (handles dev mode backend startup lag)
    - After all retries exhausted: `console.warn('[ChatPage] DB tab restore failed after retries:', error)`, leave default tab
    - Use `AbortController` or mounted-ref guard to cancel retries on unmount
    - Reuse `isLoadingHistory` state for loading indicator during DB fetch
    - Add observability logging: `[ChatPage] Restoring tabs from DB: fetched N sessions`
    - _Bug_Condition: isBugCondition(input) where needsDBFallback is true on mount_
    - _Expected_Behavior: Tabs restored from DB within 3 retries; user sees loading indicator then restored tabs_
    - _Preservation: When needsDBFallback=false, useEffect is a no-op — no API call, no state change_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.7_

  - [x] 3.5 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - localStorage Empty Restores Tabs From DB
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior (tabs restored from DB with correct sessionId, title, count)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.4_

  - [x] 3.6 Verify preservation tests still pass
    - **Property 2: Preservation** - localStorage Fast Path and Runtime Tab Operations Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all preservation tests still pass after fix (no regressions in localStorage hydration, tab add/close/switch, metadata updates)

- [x] 4. Checkpoint — Ensure all tests pass
  - Run full test suite: `cd desktop && npm test -- --run`
  - Ensure exploration test (task 1) now PASSES
  - Ensure preservation tests (task 2) still PASS
  - Ensure all existing tests in `useUnifiedTabState.test.ts` still PASS
  - Ensure all existing tests in `ChatPage.test.tsx` and `ChatPageSpinner.property.test.tsx` still PASS
  - Ensure backend tests pass: `cd backend && pytest`
  - Ask the user if questions arise
