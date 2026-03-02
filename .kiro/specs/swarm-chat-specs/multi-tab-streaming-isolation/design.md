<!-- PE-REVIEWED -->
# Multi-Tab Streaming Isolation Bugfix Design

## Overview

The `useChatStreamingLifecycle` hook manages streaming state through a single set of React state variables (`isStreaming`, `_pendingStream`, `sessionId`, `messages`) that are shared across all tabs. When multiple tabs stream concurrently, these globals cause cross-tab state corruption: spinners appear on wrong tabs, message sends are blocked on idle tabs, and tab switching corrupts both source and target tab state.

The fix leverages the existing `UnifiedTab.isStreaming` field in the per-tab map as the source of truth, replaces the global `_pendingStream` boolean with a per-tab `Set<string>`, and makes `setIsStreaming` tab-aware so that React state always reflects only the active tab's streaming status.

## Glossary

- **Bug_Condition (C)**: Any scenario where more than one tab exists and at least one tab is streaming or pending, causing global state (`_pendingStream`, `streamingSessions`, `isStreamingRef`) to misrepresent the active tab's actual streaming status.
- **Property (P)**: The active tab's `isStreaming` derivation, `handleSendMessage` guard, and `messages` state SHALL reflect only that tab's own streaming lifecycle, independent of other tabs.
- **Preservation**: Single-tab streaming behavior, SSE event processing, auto-scroll suppression, tab lifecycle (open/close/switch), and all non-streaming UI interactions must remain unchanged.
- **`useChatStreamingLifecycle`**: Hook in `desktop/src/hooks/useChatStreamingLifecycle.ts` that owns streaming state machine (messages, sessionId, isStreaming, stream handler factories).
- **`useUnifiedTabState`**: Hook in `desktop/src/hooks/useUnifiedTabState.ts` that owns the per-tab `Map<string, UnifiedTab>` with per-tab `isStreaming`, `messages`, `sessionId`, `pendingQuestion` fields.
- **`tabMapRef`**: Direct `RefObject<Map<string, UnifiedTab>>` for synchronous reads in stream handlers.
- **`activeTabIdRef`**: Direct `RefObject<string | null>` for synchronous active-tab checks in stream handlers.
- **`_pendingStream`**: Global boolean covering the gap between `handleSendMessage` and `session_start` SSE event. Currently shared across all tabs.
- **`streamingSessions`**: Global `Set<string>` of sessionIds with active streams. Currently drives `isStreaming` derivation for whichever sessionId is in React state.

## Bug Details

### Fault Condition

The bug manifests when multiple tabs exist and at least one tab is streaming. The global `_pendingStream`, `streamingSessions`, and derived `isStreaming` reflect a single tab's perspective, causing incorrect state for all other tabs.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type { action: TabAction, tabs: Map<string, UnifiedTab>, activeTabId: string }
  OUTPUT: boolean

  LET otherTabs = tabs WHERE tab.id != activeTabId
  LET anyOtherStreaming = EXISTS tab IN otherTabs WHERE tab.isStreaming == true OR tab.pendingStream == true

  RETURN input.action IN ['sendMessage', 'switchTab', 'completeStream', 'startStream']
         AND tabs.size > 1
         AND anyOtherStreaming
         AND globalIsStreaming != tabs.get(activeTabId).isStreaming
END FUNCTION
```

### Examples

- **Blocked send on idle tab**: Tab A is streaming. User switches to idle Tab B. `isStreamingRef.current` is `true` (from Tab A's global state). User tries to send a message on Tab B → blocked by `if (isStreamingRef.current) return` guard. Expected: Tab B should allow sending because Tab B is idle.

- **Pending state killed**: Tab A starts streaming, `_pendingStream = true`. Tab B starts streaming, `_pendingStream = true`. Tab A receives `session_start`, sets `_pendingStream = false`. Tab B's spinner disappears even though Tab B hasn't received its `session_start` yet. Expected: Tab B's pending state should be independent.

- **Tab switch corrupts streaming tab**: Tab A is streaming. User switches to idle Tab B. `handleTabSelect` calls `setIsStreaming(tabState.isStreaming)` which sets `_pendingStream = false` and removes Tab A's sessionId from `streamingSessions`. Tab A's background stream handler now sees `isStreaming = false`. Expected: Tab A's streaming state should be preserved in the per-tab map, unaffected by switching away.

- **Message interleaving**: Tab A and Tab B both streaming. Both stream handlers call `setMessages(prev => ...)` on the same shared React state. Tab A's messages appear in Tab B's view and vice versa. Expected: Each tab's stream handler should write only to its own per-tab messages in the tab map.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Single-tab streaming: spinner, message accumulation, abort, and completion must work exactly as before
- SSE event processing order and semantics (session_start, content_block_delta, result, error, ask_user_question, cmd_permission_request) must remain unchanged
- Auto-scroll suppression when user scrolls up must continue to work
- Tab open/close lifecycle, welcome message initialization, and session cleanup must remain unchanged
- Tab status indicators (idle, streaming, complete_unread, error, waiting_input, permission_needed) must continue to reflect accurate per-tab status
- sessionStorage persistence of pending state (Fix 5) must continue to work
- Stream generation counter (Fix 1) must continue to guard stale complete handlers
- TSCC panel integration and telemetry events must remain unchanged

**Scope:**
All inputs that do NOT involve concurrent multi-tab streaming should be completely unaffected by this fix. This includes:
- Single-tab streaming sessions
- Tab management without concurrent streams (open, close, rename)
- Non-streaming interactions (plugin commands, session history loading, agent switching)
- Backend SSE event format and delivery

## Hypothesized Root Cause

Based on the bug description and code analysis, the root causes are:

1. **Single `_pendingStream` boolean**: `_pendingStream` is a single `useState<boolean>` shared across all tabs. When any tab calls `setIsStreaming(true)`, it sets `_pendingStream = true` globally. When any tab completes or the user switches tabs, `_pendingStream = false` kills all tabs' pending indicators. This is the primary cause of requirements 1.2 and 1.6.

2. **Single `sessionId` drives `isStreaming` derivation**: `isStreaming` is derived as `sessionId ? streamingSessions.has(sessionId) || _pendingStream : _pendingStream`. Since `sessionId` is a single React state variable that changes on tab switch, switching to an idle tab makes `isStreaming` derive from the idle tab's sessionId, losing the streaming tab's status. This is the primary cause of requirements 1.1 and 1.5.

3. **Single `messages` React state**: All stream handlers call `setMessages()` on the same React state. While per-tab map entries store messages independently, the React state used for rendering receives interleaved updates from concurrent streams. This is the primary cause of requirement 1.4.

4. **`handleSendMessage` guard uses global `isStreamingRef.current`**: The guard `if (isStreamingRef.current) return` checks the global derived `isStreaming`, not the active tab's per-tab streaming state. Since `isStreaming` can be `true` due to another tab streaming, idle tabs are incorrectly blocked. This is the primary cause of requirement 1.1.

5. **`handleTabSelect` calls `setIsStreaming()` which modifies global state**: When switching tabs, `setIsStreaming(tabState.isStreaming)` modifies `_pendingStream` and `streamingSessions` globally, corrupting the source tab's streaming state. This is the primary cause of requirement 1.3.

6. **No per-tab pending tracking**: The gap between `handleSendMessage` (which sets `_pendingStream = true`) and `session_start` (which adds to `streamingSessions` and clears `_pendingStream`) is tracked by a single boolean. Multiple tabs in this gap corrupt each other. This is the primary cause of requirement 1.6.

7. **`handleStop` uses shared `sessionId` state**: `handleStop` calls `chatService.stopSession(sessionId)` where `sessionId` is the shared React state. After switching from streaming Tab A to idle Tab B, `sessionId` reflects Tab B's session. Clicking stop on Tab B would send the stop request to Tab B's session (or fail if Tab B has no session), not Tab A's. This is the primary cause of requirement 1.7.

8. **`handleAnswerQuestion` and `handlePermissionDecision` use shared `sessionId`**: Both handlers reference the shared `sessionId` React state for backend API calls. After a tab switch, these could target the wrong backend session. This is the primary cause of requirement 1.8.

9. **`cmd_permission_acknowledged` handler calls `setIsStreaming(false)` without tabId**: In `handlePermissionDecision`, the inline SSE handler for `cmd_permission_acknowledged` calls `setIsStreaming(false)` without specifying a tabId, clearing global streaming state. This is the primary cause of requirement 1.9.

10. **Shared `abortRef` is overwritten by concurrent tabs**: Each `handleSendMessage`, `handleAnswerQuestion`, and `handlePermissionDecision` call sets `abortRef.current = abort`. With concurrent tabs, only the last tab's abort function is reachable. The per-tab `abortController` in `tabMapRef` already provides correct per-tab abort, making `abortRef` legacy dead code. This is the primary cause of requirement 1.10.

## Legacy/Dead Code Identified

The following code becomes dead after the fix and should be removed:

1. **`streamingSessions: Set<string>`** — The per-tab `isStreaming` field in `UnifiedTab` replaces session-based streaming tracking. The Set is no longer needed for derivation.
2. **`setStreamingSessions`** — All call sites (in `setIsStreaming`, `session_start` handler) become dead.
3. **`_pendingStream: boolean`** — Replaced by `pendingStreamTabs: Set<string>`.
4. **`_setPendingStream`** — All call sites become dead.
5. **`abortRef`** in `useChatStreamingLifecycle` — Redundant with per-tab `abortController` in `tabMapRef`. All `abortRef.current = abort` assignments in ChatPage should be removed.
6. **`isStreamingRef`** in ChatPage — Should read from `tabMapRef` directly instead of mirroring the global `isStreaming` derived state.
7. **Stale test assertions** referencing `streamingSessions`, `_pendingStream`, or the old `setIsStreaming(boolean)` signature need updating.

## Correctness Properties

Property 1: Fault Condition - Active Tab Streaming Isolation

_For any_ multi-tab scenario where Tab A is streaming and the user switches to idle Tab B, the derived `isStreaming` SHALL be `false` on Tab B, `handleSendMessage` SHALL NOT be blocked on Tab B, and Tab A's streaming state SHALL be preserved in the per-tab map unmodified.

**Validates: Requirements 2.1, 2.2, 2.3, 2.5**

Property 2: Preservation - Single-Tab Streaming Behavior

_For any_ single-tab scenario (only one tab open), the streaming lifecycle SHALL produce identical behavior to the current implementation: spinner appears on send, messages accumulate correctly, abort works, completion clears streaming state, and input is re-enabled.

**Validates: Requirements 3.1, 3.2, 3.5, 3.6, 3.7**

Property 3: Fault Condition - Per-Tab Pending State Independence

_For any_ scenario where multiple tabs are in the pending state (between `handleSendMessage` and `session_start`), completing or canceling one tab's pending state SHALL NOT affect any other tab's pending state.

**Validates: Requirements 2.2, 2.6**

Property 4: Preservation - Tab Lifecycle Unchanged

_For any_ tab lifecycle operation (open new tab, close tab, rename tab) that does NOT involve concurrent streaming, the behavior SHALL be identical to the current implementation.

**Validates: Requirements 3.3, 3.4, 3.8**

Property 5: Fault Condition - Concurrent Stream Message Isolation

_For any_ scenario where Tab A and Tab B are both streaming concurrently, Tab A's stream handler SHALL write messages only to Tab A's per-tab map entry, and Tab B's stream handler SHALL write messages only to Tab B's per-tab map entry. The shared React `messages` state SHALL only reflect the active tab's messages.

**Validates: Requirements 2.4, 2.5**

Property 6: Fault Condition - Background Stream Error Isolation

_For any_ scenario where a background tab's stream encounters an error, the error handler SHALL clear only that tab's streaming state in the per-tab map and SHALL NOT modify the active tab's React state (`isStreaming`, `messages`).

**Validates: Requirements 2.2, 2.3**

Property 7: Fault Condition - Per-Tab Session Identity for Actions

_For any_ user action (stop, answer question, permission decision) on a tab, the handler SHALL use the active tab's sessionId from the per-tab map, not the shared React `sessionId` state, ensuring the correct backend session is targeted.

**Validates: Requirements 2.7, 2.8, 2.9**

Property 8: Dead Code Elimination - No Shared Streaming State Remains

_After the fix_, the `streamingSessions` Set, `_pendingStream` boolean, and shared `abortRef` SHALL be removed. All streaming state SHALL be tracked exclusively in the per-tab map (`tabMapRef`) and `pendingStreamTabs` Set.

**Validates: Requirements 2.10**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `desktop/src/hooks/useChatStreamingLifecycle.ts`

**Function**: `useChatStreamingLifecycle`

**Specific Changes**:

1. **Replace `_pendingStream` boolean with `pendingStreamTabs: Set<string>`**: Change `useState<boolean>(false)` to `useState<Set<string>>(new Set())`. Each tab's pending state is tracked by its tabId in the set. This ensures one tab's `session_start` clearing its pending state doesn't affect other tabs.

2. **Derive `isStreaming` from active tab's per-tab state**: Replace the current derivation:
   ```
   // BEFORE (global):
   const isStreaming = sessionId
     ? streamingSessions.has(sessionId) || _pendingStream
     : _pendingStream;
   
   // AFTER (per-tab):
   const activeTabState = activeTabIdRef.current
     ? tabMapRef.current.get(activeTabIdRef.current)
     : undefined;
   const isStreaming = activeTabState?.isStreaming
     || pendingStreamTabs.has(activeTabIdRef.current ?? '');
   ```
   Use a `useSyncExternalStore`-like pattern or a `useState` that is updated whenever the active tab changes, reading from `tabMapRef` for the authoritative value.

3. **Make `setIsStreaming` tab-aware**: Change the signature to accept an optional `tabId` parameter. When called, it updates only the specified tab's state in `tabMapRef` and only updates React state (`_setPendingStream` equivalent, `setStreamingSessions`) if the specified tab is the active tab:
   ```typescript
   const setIsStreaming = useCallback((streaming: boolean, tabId?: string) => {
     const targetTabId = tabId ?? activeTabIdRef.current;
     // Always update per-tab map
     if (targetTabId) {
       const tabState = tabMapRef.current.get(targetTabId);
       if (tabState) tabState.isStreaming = streaming;
       // Update pendingStreamTabs
       setPendingStreamTabs(prev => {
         const next = new Set(prev);
         streaming ? next.add(targetTabId) : next.delete(targetTabId);
         return next;
       });
     }
     // Only update React state if this is the active tab
     if (targetTabId === activeTabIdRef.current) {
       // trigger re-render so derived isStreaming updates
     }
   }, []);
   ```

4. **Stream handlers: write to tabMapRef only for non-active tabs**: In `createStreamHandler`, the existing `isActiveTab` guard already controls whether `setMessages()` is called. Ensure that ALL state mutations (`setIsStreaming`, `setSessionId`, `setPendingQuestion`) follow the same pattern: always write to `tabMapRef`, only call React setters when `isActiveTab` is true. This prevents the race condition where a background stream handler fires between tab-switch save and restore.

5. **`createErrorHandler`: tab-aware error clearing**: Change `setIsStreaming(false)` to `setIsStreaming(false, capturedTabId)` so that a background tab's error only clears that tab's streaming state, not the active tab's.

6. **`createCompleteHandler`: tab-aware completion**: Already partially tab-aware (sets `tabState.isStreaming = false`). Ensure it also removes the tab from `pendingStreamTabs` and only calls React `setIsStreaming(false)` when `capturedTabId === activeTabIdRef.current`.

7. **`result` event handler: clear streaming state**: The `result` SSE event is the definitive backend signal that a conversation turn is complete. The handler MUST call `setIsStreaming(false, capturedTabId)` and `incrementStreamGen()` to clear the tab's streaming state and invalidate any pending `createCompleteHandler`. Without this, tabs stay in "processing..." state after the response completes — the `createCompleteHandler` (fired by SSE `[DONE]`) is a backstop but has a generation guard that can silently no-op.

**File**: `desktop/src/pages/ChatPage.tsx`

**Function**: `handleSendMessage`

**Specific Changes**:

7. **Guard uses active tab's per-tab state**: Replace `if (isStreamingRef.current) return` with a check against the active tab's per-tab streaming state:
   ```typescript
   const activeTab = tabMapRef.current.get(activeTabIdRef.current ?? '');
   if (activeTab?.isStreaming || pendingStreamTabs.has(activeTabIdRef.current ?? '')) return;
   ```
   This ensures idle tabs are not blocked by other tabs streaming.

8. **Pass `tabId` to `setIsStreaming` calls**: All `setIsStreaming(true)` and `setIsStreaming(false)` calls in `handleSendMessage`, `handleAnswerQuestion`, `handlePermissionDecision`, and `handleStop` should pass the current `activeTabIdRef.current` as the tabId parameter.

**Function**: `handleTabSelect`

**Specific Changes**:

9. **Remove `setIsStreaming(tabState.isStreaming)` call**: Instead of calling `setIsStreaming` (which currently modifies global state), the tab switch should only: (a) save current tab's React state to tabMapRef, (b) call `selectTab(tabId)`, (c) restore target tab's state from tabMapRef to React state. The `isStreaming` derivation will automatically reflect the target tab's state because it reads from `tabMapRef` keyed by `activeTabIdRef.current`.

10. **Restore React state without side effects**: When restoring from per-tab map, set React state directly (`setMessages`, `setSessionId`, `setPendingQuestion`) without calling `setIsStreaming` — the derivation handles it. This prevents corrupting the source tab's streaming state.

**File**: `desktop/src/pages/ChatPage.tsx`

**Function**: `handleStop`

**Specific Changes**:

11. **Use per-tab sessionId for stop request**: Replace `if (!sessionId) return` with reading sessionId from the active tab's per-tab map entry: `const tabState = tabMapRef.current.get(activeTabIdRef.current ?? ''); const tabSessionId = tabState?.sessionId; if (!tabSessionId) return;`. Use `tabSessionId` for `chatService.stopSession()`. This ensures the correct backend session is stopped after tab switches.

12. **Pass tabId to `setIsStreaming(false)` in finally block**: Change `setIsStreaming(false)` to `setIsStreaming(false, activeTabIdRef.current ?? undefined)`.

**Function**: `handleAnswerQuestion`

**Specific Changes**:

13. **Use per-tab sessionId**: Replace `if (!selectedAgentId || !sessionId) return` with reading sessionId from the active tab's per-tab map. Use the per-tab sessionId for the `chatService.streamAnswerQuestion()` call.

**Function**: `handlePermissionDecision`

**Specific Changes**:

14. **Use per-tab sessionId**: Replace `if (!pendingPermission || !sessionId || !selectedAgentId) return` with reading sessionId from the active tab's per-tab map. Use the per-tab sessionId for both `chatService.submitCmdPermissionDecision()` and `chatService.streamCmdPermissionContinue()`.

15. **Pass tabId to `setIsStreaming(false)` in `cmd_permission_acknowledged` handler**: Change the inline `setIsStreaming(false)` to `setIsStreaming(false, tabId)`.

16. **Pass tabId to `setIsStreaming(false)` in deny branch**: Change the `setIsStreaming(false)` in the deny `finally` block to `setIsStreaming(false, activeTabIdRef.current ?? undefined)`.

**Legacy/Dead Code Cleanup**:

17. **Remove `streamingSessions` state and `setStreamingSessions`**: Delete the `useState<Set<string>>` declaration and all `setStreamingSessions()` calls (in `setIsStreaming` callback and `session_start` handler). The per-tab `isStreaming` field in `UnifiedTab` replaces this.

18. **Remove `_pendingStream` state and `_setPendingStream`**: Delete the `useState<boolean>(false)` declaration and all `_setPendingStream()` calls. Replaced by `pendingStreamTabs: Set<string>`.

19. **Remove shared `abortRef` from `useChatStreamingLifecycle`**: Delete the `abortRef` declaration, remove it from the return interface and `ChatStreamingLifecycle` type. Remove all `abortRef.current = abort` assignments in ChatPage. The per-tab `abortController` in `tabMapRef` is the sole abort mechanism.

20. **Remove `isStreamingRef` from ChatPage**: Delete `const isStreamingRef = useRef(isStreaming); isStreamingRef.current = isStreaming;`. The `handleSendMessage` guard now reads from `tabMapRef` directly.

21. **Update test files**: Update `useChatStreamingLifecycle.test.ts` to reflect the new `setIsStreaming(boolean, tabId?)` signature, remove assertions about `streamingSessions` and `_pendingStream`, and update any tests that reference `abortRef`.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that simulate multi-tab scenarios with concurrent streaming and assert that each tab's streaming state is independent. Run these tests on the UNFIXED code to observe failures and understand the root cause.

**Test Cases**:
1. **Blocked Send Test**: Create Tab A (streaming) and Tab B (idle). Assert `handleSendMessage` is NOT blocked on Tab B (will fail on unfixed code — `isStreamingRef.current` is `true` globally)
2. **Pending State Kill Test**: Start streaming on Tab A and Tab B. Complete Tab A's `session_start`. Assert Tab B's pending indicator is still active (will fail on unfixed code — `_pendingStream = false` kills both)
3. **Tab Switch Corruption Test**: Start streaming on Tab A. Switch to Tab B. Assert Tab A's `isStreaming` in tabMapRef is still `true` (will fail on unfixed code — `setIsStreaming(false)` clears globally)
4. **Message Interleaving Test**: Stream on Tab A and Tab B concurrently. Assert Tab A's messages contain only Tab A's content and vice versa (will fail on unfixed code — shared `setMessages`)

**Expected Counterexamples**:
- `isStreamingRef.current` returns `true` on idle Tab B when Tab A is streaming
- `_pendingStream` becomes `false` for Tab B when Tab A receives `session_start`
- Tab A's `tabMapRef` entry has `isStreaming: false` after switching to Tab B
- Possible causes: single `_pendingStream` boolean, single `sessionId` derivation, global `setIsStreaming`

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FUNCTION expectedBehavior(result)
  INPUT: result of type { activeTabStreaming: boolean, activeTabMessages: Message[], otherTabsState: Map<string, UnifiedTab> }
  OUTPUT: boolean

  // Active tab's isStreaming reflects only its own state
  LET activeTab = tabs.get(activeTabId)
  ASSERT result.activeTabStreaming == (activeTab.isStreaming OR pendingStreamTabs.has(activeTabId))

  // Other tabs' streaming state is unmodified
  FOR EACH tab IN otherTabsState DO
    ASSERT tab.isStreaming == tab.previousIsStreaming
    ASSERT tab.messages == tab.previousMessages
  END FOR

  // Active tab's messages contain only its own content
  ASSERT result.activeTabMessages == activeTab.messages

  RETURN true
END FUNCTION
```

```
FOR ALL input WHERE isBugCondition(input) DO
  result := applyAction_fixed(input)
  ASSERT expectedBehavior(result)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT applyAction_original(input) = applyAction_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for single-tab streaming and non-streaming interactions, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Single-Tab Streaming Preservation**: Verify that single-tab streaming (send → spinner → messages → complete → idle) works identically before and after the fix
2. **Tab Lifecycle Preservation**: Verify that opening, closing, and renaming tabs without concurrent streaming works identically
3. **SSE Event Processing Preservation**: Verify that all SSE event types (session_start, assistant, result, error, ask_user_question, cmd_permission_request) are processed correctly in single-tab mode
4. **Auto-Scroll Preservation**: Verify that user scroll-up suppresses auto-scroll and new message resumes it

### Unit Tests

- Test `setIsStreaming(true, tabId)` only modifies the specified tab's state in tabMapRef
- Test `setIsStreaming(false, tabId)` on a background tab does NOT modify React state
- Test `isStreaming` derivation reads from active tab's per-tab state, not global
- Test `handleSendMessage` guard checks active tab's per-tab streaming state
- Test `handleTabSelect` preserves source tab's streaming state when switching away
- Test `createErrorHandler` for background tab only clears that tab's streaming state

### Property-Based Tests

- Generate random multi-tab configurations (N tabs, random streaming states) and verify `isStreaming` always matches the active tab's per-tab state
- Generate random sequences of tab switches during concurrent streaming and verify no cross-tab state corruption
- Generate random single-tab streaming scenarios and verify identical behavior to unfixed code

### Integration Tests

- Test full flow: open 2 tabs, stream on Tab A, switch to Tab B, send message on Tab B, switch back to Tab A — both tabs have correct messages
- Test full flow: open 3 tabs, stream on all 3, complete in random order — each tab shows correct final state
- Test full flow: stream on Tab A, switch to Tab B, Tab A errors — Tab B is unaffected, Tab A shows error when switched back

