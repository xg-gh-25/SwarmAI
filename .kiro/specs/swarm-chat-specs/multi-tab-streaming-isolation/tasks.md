# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - Multi-Tab Streaming State Isolation
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate global streaming state corrupts per-tab isolation
  - **Scoped PBT Approach**: Scope the property to concrete multi-tab scenarios where at least one tab is streaming
  - Add test file `desktop/src/__tests__/multiTabStreamingIsolation.pbt.test.ts`
  - Use vitest + fast-check for property-based testing
  - Use `renderHook` with `useChatStreamingLifecycle` and the existing `createMockDeps` / `initTestTab` patterns from `useChatStreamingLifecycle.test.ts`
  - **Bug Condition from design**: `isBugCondition(input)` — tabs.size > 1 AND anyOtherStreaming AND globalIsStreaming != tabs.get(activeTabId).isStreaming
  - **Test Case 1 - Blocked Send on Idle Tab**: Create Tab A (streaming) and Tab B (idle). Switch to Tab B. Assert `isStreamingRef.current` is `false` on Tab B (will FAIL — global `isStreamingRef` reflects Tab A's state)
  - **Test Case 2 - Pending State Kill**: Start streaming on Tab A and Tab B (`_pendingStream = true` for both). Tab A receives `session_start` → `_pendingStream = false`. Assert Tab B still shows pending (will FAIL — single boolean kills both)
  - **Test Case 3 - Tab Switch Corruption**: Tab A is streaming. Switch to Tab B. Assert Tab A's `tabMapRef` entry still has `isStreaming: true` (will FAIL — `handleTabSelect` calls `setIsStreaming(false)` globally)
  - **Test Case 4 - Message Isolation**: Tab A and Tab B both streaming. Assert each tab's messages in `tabMapRef` contain only their own content (will FAIL — shared `setMessages` interleaves)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct — it proves the bug exists)
  - Document counterexamples found to understand root cause
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Single-Tab Streaming Behavior
  - **IMPORTANT**: Follow observation-first methodology
  - Add tests to `desktop/src/__tests__/multiTabStreamingIsolation.pbt.test.ts`
  - Use vitest + fast-check for property-based testing
  - **Observation Phase**: Run UNFIXED code with single-tab and non-streaming inputs to capture baseline behavior
  - Observe: Single-tab streaming lifecycle (send → spinner → messages accumulate → complete → idle) works correctly
  - Observe: Tab open/close/rename without concurrent streaming works correctly
  - Observe: SSE event processing (session_start, content_block_delta, result, error, ask_user_question) works correctly in single-tab mode
  - **Property Test 1 - Single-Tab Streaming Preservation**: For all single-tab scenarios (tabs.size == 1), the streaming lifecycle SHALL produce identical behavior: `isStreaming` transitions correctly, messages accumulate in order, completion clears streaming state, input re-enables
  - **Property Test 2 - Tab Lifecycle Preservation**: For all tab lifecycle operations (open, close, rename) without concurrent streaming, behavior is identical to unfixed code
  - **Property Test 3 - SSE Event Processing Preservation**: For all SSE event types in single-tab mode, events are processed correctly and state transitions match unfixed behavior
  - Verify all preservation tests PASS on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 3. Fix for multi-tab streaming state isolation

  - [x] 3.1 Replace `_pendingStream` boolean with `pendingStreamTabs: Set<string>` in `useChatStreamingLifecycle.ts`
    - Change `useState<boolean>(false)` to `useState<Set<string>>(new Set())`
    - Each tab's pending state is tracked by its tabId in the set
    - Update all references from `_pendingStream` / `setPendingStream` to use the new Set-based state
    - _Bug_Condition: isBugCondition(input) where tabs.size > 1 AND single _pendingStream boolean is shared_
    - _Expected_Behavior: pendingStreamTabs.has(tabId) returns independent per-tab pending state_
    - _Preservation: Single-tab behavior unchanged — Set with one entry behaves like boolean_
    - _Requirements: 1.2, 1.6, 2.2, 2.6_

  - [x] 3.2 Derive `isStreaming` from active tab's per-tab state in `useChatStreamingLifecycle.ts`
    - Replace global derivation `sessionId ? streamingSessions.has(sessionId) || _pendingStream : _pendingStream`
    - New derivation: `activeTabState?.isStreaming || pendingStreamTabs.has(activeTabIdRef.current ?? '')`
    - Read from `tabMapRef` keyed by `activeTabIdRef.current` for authoritative value
    - _Bug_Condition: isBugCondition(input) where globalIsStreaming != tabs.get(activeTabId).isStreaming_
    - _Expected_Behavior: isStreaming always reflects only the active tab's streaming state_
    - _Preservation: Single-tab derivation produces same result_
    - _Requirements: 1.1, 1.5, 2.1, 2.5_

  - [x] 3.3 Make `setIsStreaming` tab-aware in `useChatStreamingLifecycle.ts`
    - Add optional `tabId` parameter to `setIsStreaming` callback
    - Always update per-tab map entry for the target tab
    - Update `pendingStreamTabs` Set (add on true, delete on false) for the target tab
    - Only trigger React state re-render if target tab is the active tab
    - _Bug_Condition: setIsStreaming(false) from one tab clears all tabs' state_
    - _Expected_Behavior: setIsStreaming(value, tabId) modifies only the specified tab_
    - _Preservation: Calls without tabId default to activeTabIdRef.current — same as before_
    - _Requirements: 1.1, 1.3, 2.1, 2.2, 2.3_

  - [x] 3.4 Update stream handlers to write to tabMapRef only for non-active tabs in `useChatStreamingLifecycle.ts`
    - In `createStreamHandler`, ensure ALL state mutations follow the `isActiveTab` guard pattern
    - `setIsStreaming`, `setSessionId`, `setPendingQuestion` — always write to `tabMapRef`, only call React setters when `isActiveTab` is true
    - Prevents race condition where background stream handler fires between tab-switch save and restore
    - _Bug_Condition: Background stream handler calls shared React setters, corrupting active tab state_
    - _Expected_Behavior: Background handlers write only to tabMapRef; React state reflects active tab only_
    - _Preservation: Active tab stream handlers behave identically to before_
    - _Requirements: 1.4, 2.4, 2.5_

  - [x] 3.5 Make `createErrorHandler` tab-aware in `useChatStreamingLifecycle.ts`
    - Change `setIsStreaming(false)` to `setIsStreaming(false, capturedTabId)`
    - Background tab error only clears that tab's streaming state in per-tab map
    - Does NOT modify active tab's React state
    - _Bug_Condition: Background tab error clears active tab's streaming indicator_
    - _Expected_Behavior: Error handler clears only capturedTabId's streaming state_
    - _Preservation: Active tab error handling unchanged_
    - _Requirements: 2.2, 2.3_

  - [x] 3.6 Make `createCompleteHandler` tab-aware in `useChatStreamingLifecycle.ts`
    - Ensure completion removes tab from `pendingStreamTabs` Set
    - Only call React `setIsStreaming(false)` when `capturedTabId === activeTabIdRef.current`
    - _Bug_Condition: Background tab completion clears active tab's streaming/pending state_
    - _Expected_Behavior: Completion clears only capturedTabId's state_
    - _Preservation: Active tab completion unchanged_
    - _Requirements: 1.2, 2.2_

  - [x] 3.7 Update `handleSendMessage` guard in `ChatPage.tsx` to use active tab's per-tab state
    - Replace `if (isStreamingRef.current) return` with per-tab check
    - New guard: `const activeTab = tabMapRef.current.get(activeTabIdRef.current ?? ''); if (activeTab?.isStreaming || pendingStreamTabs.has(activeTabIdRef.current ?? '')) return;`
    - _Bug_Condition: Global isStreamingRef blocks idle tabs when other tabs are streaming_
    - _Expected_Behavior: Guard checks only active tab's per-tab streaming state_
    - _Preservation: Single-tab guard behavior identical_
    - _Requirements: 1.1, 2.1_

  - [x] 3.8 Pass `tabId` to all `setIsStreaming` calls in `ChatPage.tsx`
    - Update `handleSendMessage`, `handleAnswerQuestion`, `handlePermissionDecision`, `handleStop`
    - All calls pass `activeTabIdRef.current` as the tabId parameter
    - _Bug_Condition: setIsStreaming calls without tabId affect global state_
    - _Expected_Behavior: All setIsStreaming calls are scoped to the originating tab_
    - _Preservation: Active tab calls behave identically_
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.9 Remove `setIsStreaming(tabState.isStreaming)` from `handleTabSelect` in `ChatPage.tsx`
    - Tab switch should only: (a) save current tab's React state to tabMapRef, (b) call `selectTab(tabId)`, (c) restore target tab's state from tabMapRef to React state
    - `isStreaming` derivation automatically reflects target tab's state from `tabMapRef` keyed by `activeTabIdRef.current`
    - _Bug_Condition: handleTabSelect calls setIsStreaming which modifies global state, corrupting source tab_
    - _Expected_Behavior: Tab switch preserves source tab's streaming state, restores target tab's state_
    - _Preservation: Tab switch visual behavior unchanged — correct state still displayed_
    - _Requirements: 1.3, 2.3, 2.5_

  - [x] 3.10 Restore React state without side effects on tab switch in `ChatPage.tsx`
    - When restoring from per-tab map, set React state directly (`setMessages`, `setSessionId`, `setPendingQuestion`)
    - Do NOT call `setIsStreaming` during restore — the derivation handles it
    - Prevents corrupting the source tab's streaming state
    - _Bug_Condition: Restoring state via setIsStreaming triggers global side effects_
    - _Expected_Behavior: React state restored from tabMapRef without modifying other tabs_
    - _Preservation: Restored state matches what was saved — no behavioral change_
    - _Requirements: 2.3, 2.5_

  - [x] 3.11 Use per-tab sessionId in `handleStop`, `handleAnswerQuestion`, `handlePermissionDecision` in `ChatPage.tsx`
    - `handleStop`: Replace `if (!sessionId) return` with reading sessionId from `tabMapRef.current.get(activeTabIdRef.current)?.sessionId`
    - `handleStop`: Use per-tab sessionId for `chatService.stopSession()` call
    - `handleStop`: Pass tabId to `setIsStreaming(false, tabId)` in finally block
    - `handleAnswerQuestion`: Replace shared `sessionId` with per-tab sessionId from tabMapRef
    - `handlePermissionDecision`: Replace shared `sessionId` with per-tab sessionId from tabMapRef
    - `handlePermissionDecision`: Pass tabId to `setIsStreaming(false, tabId)` in deny branch and `cmd_permission_acknowledged` handler
    - _Bug_Condition: Shared sessionId targets wrong backend session after tab switch_
    - _Expected_Behavior: All action handlers use the active tab's per-tab sessionId_
    - _Preservation: Single-tab behavior unchanged — per-tab sessionId matches shared sessionId when only one tab exists_
    - _Requirements: 1.7, 1.8, 1.9, 2.7, 2.8, 2.9_

  - [x] 3.12 Remove legacy dead code: `streamingSessions`, `_pendingStream`, shared `abortRef`, `isStreamingRef`
    - In `useChatStreamingLifecycle.ts`: Remove `streamingSessions` useState and all `setStreamingSessions()` calls
    - In `useChatStreamingLifecycle.ts`: Remove `_pendingStream` useState and all `_setPendingStream()` calls (replaced by `pendingStreamTabs`)
    - In `useChatStreamingLifecycle.ts`: Remove `abortRef` declaration, remove from return interface and `ChatStreamingLifecycle` type
    - In `ChatPage.tsx`: Remove all `abortRef.current = abort` assignments (per-tab `abortController` in tabMapRef is the sole abort mechanism)
    - In `ChatPage.tsx`: Remove `isStreamingRef` declaration and `isStreamingRef.current = isStreaming` sync (guard now reads from tabMapRef)
    - Update module-level docstring in `useChatStreamingLifecycle.ts` to reflect removed state
    - _Bug_Condition: Legacy shared state creates confusion and potential regression vectors_
    - _Expected_Behavior: All streaming state tracked exclusively in per-tab map and pendingStreamTabs_
    - _Preservation: No behavioral change — dead code removal only_
    - _Requirements: 2.10_

  - [x] 3.13 Update existing test files to reflect new API
    - In `useChatStreamingLifecycle.test.ts`: Update `setIsStreaming` calls to use new `(boolean, tabId?)` signature
    - Remove any assertions about `streamingSessions` or `_pendingStream` internals
    - Remove any assertions about `abortRef` from the streaming hook (now per-tab only)
    - Update `isStreaming derivation` test section to verify per-tab derivation instead of global
    - Ensure all existing test patterns (createMockDeps, initTestTab) work with the new implementation
    - _Preservation: All existing test intent preserved — only API surface changes_
    - _Requirements: 3.1, 3.2, 3.5_

  - [x] 3.14 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Multi-Tab Streaming State Isolation
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10_

  - [x] 3.15 Verify preservation tests still pass
    - **Property 2: Preservation** - Single-Tab Streaming Behavior
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all tests still pass after fix (no regressions)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

- [x] 4. Checkpoint - Ensure all tests pass
  - Run the full test suite (`cd desktop && npm test -- --run`)
  - Verify all ~986 existing tests still pass (no regressions)
  - Verify exploration test (Property 1) passes after fix
  - Verify preservation tests (Property 2) pass after fix
  - Ensure all tests pass, ask the user if questions arise.
