<!-- PE-REVIEWED -->
# Streaming UX Lifecycle Bugfix Design

## Overview

Eight interrelated bugs degrade the chat streaming UX during multi-turn agent conversations with tool use. The bugs share a common root: the streaming lifecycle in `ChatPage.tsx` was designed for simple request/response flows but breaks down during long sessions with `sessionId` transitions, concurrent `setIsStreaming(false)` calls, rapid tool invocations, error events buried above the viewport, shallow activity labels, in-memory-only state for pending questions, shared single-instance state across multiple tabs, unbounded tab creation, and missing tab status indicators.

The fix strategy is minimal and additive — extend existing patterns (`deriveStreamingActivity`, `ContentBlockRenderer`, `createStreamHandler`) rather than rewrite them, introduce a per-tab state map to isolate cross-session state, enforce a tab limit to prevent resource exhaustion, and add tab status indicators for at-a-glance visibility. Each bug gets a targeted fix with a corresponding correctness property.

**Recommended implementation priority** (ship incrementally):
0. **Phase 0 — Extract Hook** (prerequisite): Extract streaming lifecycle logic from `ChatPage.tsx` into a `useChatStreamingLifecycle` custom hook. This hook owns all refs (`tabStateRef`, `activeTabIdRef`, `streamGenRef`, etc.), stream handler factories (`createStreamHandler`, `createCompleteHandler`, `createErrorHandler`), tab save/restore logic, `deriveStreamingActivity` + debounce, `isStreaming` derivation, and tab status management. `ChatPage.tsx` consumes the hook and focuses on rendering + user interactions. This reduces ChatPage from ~1000 lines to ~500 lines and gives each subsequent fix a clean, testable surface.
1. **Phase 1 — Stability** (Fix 1 + Fix 6): Stream generation counter + per-tab state isolation. These fix the most critical bugs (state corruption, data loss) and are prerequisites for all other fixes.
2. **Phase 2 — Visibility** (Fix 2 + Fix 3 + Fix 9): Auto-scroll + error visibility + elapsed time counter. These address the "user can't see what's happening" problem.
3. **Phase 3 — Context** (Fix 4 + Fix 5): Enhanced activity labels + sessionStorage persistence. These improve the UX for long sessions and pending questions.
4. **Phase 4 — Polish** (Fix 7 + Fix 8): Tab limit + tab status indicators. These are UX enhancements that build on the per-tab state from Phase 1.

## Glossary

- **Bug_Condition (C)**: The set of conditions that trigger one of the eight bugs — streaming state gaps, scroll failures, hidden errors, shallow labels, state loss on re-mount, cross-session state corruption during multi-tab usage, unbounded tab creation, or missing tab status indicators
- **Property (P)**: The desired correct behavior for each bug condition — continuous streaming state, auto-scroll to latest content, visible errors, contextual labels, persistent pending state, per-tab state isolation
- **Preservation**: Existing behaviors that must remain unchanged — simple query rendering, mouse clicks, stop button, ContentBlockRenderer output, ToolUseBlock rendering, completed session loading
- **`isStreaming`**: Derived boolean in `ChatPage.tsx` computed from `streamingSessions.has(sessionId) || _pendingStream`
- **`deriveStreamingActivity`**: Exported function in `ChatPage.tsx` that returns `{ hasContent, toolName } | null` for spinner label
- **`createStreamHandler`**: Callback factory in `ChatPage.tsx` that returns an SSE event handler for a given `assistantMessageId`
- **`createCompleteHandler`**: Callback factory that returns `() => setIsStreaming(false)` — called when SSE reader finishes
- **`_pendingStream`**: Boolean flag covering the gap before `session_start` assigns a `sessionId`
- **`streamingSessions`**: `Set<string>` tracking which sessionIds are actively streaming
- **Stream generation**: A monotonically increasing counter to distinguish successive SSE streams and prevent stale handlers from interfering
- **Per-tab state map (`tabStateRef`)**: A `useRef<Map<string, TabState>>` that stores per-tab state (messages, sessionId, pendingQuestion, abortController, pendingStream) keyed by tabId, enabling state isolation across concurrent chat tabs
- **Active tab**: The currently visible/foreground tab whose state is reflected in the `useState` variables; background tabs' state lives only in the per-tab state map
- **`MAX_OPEN_TABS` (value: 6) defining the maximum number of concurrent chat tabs. Enforced in `handleNewSession` to prevent resource exhaustion from unbounded SSE connections and per-tab state
- **Tab status**: A derived status for each tab indicating its current lifecycle state — one of `'idle'`, `'streaming'`, `'waiting_input'`, `'permission_needed'`, `'error'`, or `'complete_unread'`. Rendered as a visual indicator on the tab header

## Bug Details

### Fault Condition

The bugs manifest across five scenarios during streaming sessions. The common thread is that the streaming lifecycle state machine has gaps at transition boundaries.

**Formal Specification:**

```
FUNCTION isBugCondition(input)
  INPUT: input of type StreamingLifecycleEvent
  OUTPUT: boolean

  // Bug 1: Streaming state gaps
  LET sessionTransitionGap = (input.sessionId_before = undefined
    AND input.sessionId_after != undefined
    AND input.streamActive = true)
  LET doubleClear = (input.eventType IN ["ask_user_question", "cmd_permission_request"]
    AND input.sseReaderCompleteHandlerPending = true)
  LET staleClear = (input.eventType = "cmd_permission_request"
    AND input.newStreamStartedBeforeOldComplete = true)

  // Bug 2: Auto-scroll failure
  LET scrollFailure = (input.isStreaming = true
    AND input.newContentBlockAppended = true
    AND input.userHasNotScrolledUp = true
    AND input.latestContentBlockInViewport = false)

  // Bug 3: Error not visible
  LET errorHidden = (input.isStreaming = true
    AND input.errorEventReceived = true
    AND input.errorContentInViewport = false)

  // Bug 4: Shallow activity label
  LET shallowLabel = (input.isStreaming = true
    AND input.currentToolUse != null
    AND input.currentToolUse.input != null)

  // Bug 5: State loss on re-mount
  LET stateLoss = (input.pendingQuestion != null
    AND input.sseStreamEnded = true
    AND input.resultMessageReceived = false)

  // Bug 6: Cross-session state corruption during multi-tab usage
  LET crossSessionCorruption = (input.openTabCount > 1
    AND (input.tabSwitchOccurred = true OR input.concurrentStreamsActive = true))

  // Bug 7: Unlimited tab creation causes resource exhaustion
  LET tabLimitExceeded = (input.openTabCount >= MAX_OPEN_TABS
    AND input.createTabRequested = true)

  // Bug 8: No visual status indication on tab headers
  LET tabStatusMissing = ((input.tabIsStreaming = true
    OR input.tabHasPendingQuestion = true
    OR input.tabHasPendingPermission = true
    OR input.tabHasError = true
    OR (input.tabStreamCompleted = true AND input.tabIsBackground = true))
    AND input.tabHeaderIndicator = none)

  RETURN sessionTransitionGap OR doubleClear OR staleClear
      OR scrollFailure OR errorHidden OR shallowLabel OR stateLoss
      OR crossSessionCorruption OR tabLimitExceeded OR tabStatusMissing
END FUNCTION
```

### Examples

- **Bug 1 — sessionId transition gap**: User sends a message, `_pendingStream` is set to `true`, `session_start` arrives with `sessionId="abc"`, `setSessionId("abc")` triggers re-render, `isStreaming` computes `streamingSessions.has("abc") || _pendingStream` — but `_pendingStream` was cleared by `setIsStreaming(true)` which wrote to the old `undefined` session entry, and `"abc"` is not yet in `streamingSessions`. Result: spinner disappears for one render frame.
- **Bug 1 — double-clear**: `ask_user_question` event calls `setIsStreaming(false)`. SSE reader finishes, `createCompleteHandler` also calls `setIsStreaming(false)`. If user submits answer quickly (calling `setIsStreaming(true)`), the stale `createCompleteHandler` fires and clears the new stream's state.
- **Bug 2 — scroll failure**: Backend runs 15 tool invocations. Each `tool_use` block is rendered by `ContentBlockRenderer` inside the message bubble, but the viewport stays at the spinner position. The user sees "Running: Bash" but not the actual tool details above.
- **Bug 3 — hidden error**: Error event arrives during a 50-tool session. Error text is appended to message history above the viewport. Spinner continues showing "Processing..." at the bottom. User waits indefinitely.
- **Bug 4 — shallow label**: Spinner shows "Running: Bash" but not "Running: Bash — npm test". During rapid calls, label flickers between "Running: Bash", "Running: Read", "Running: Search" with no stabilization.
- **Bug 5 — state loss**: `ask_user_question` arrives, SSE stream ends, user switches tabs, React component unmounts. On return, `useState` is reset, `loadSessionMessages` returns nothing (no `ResultMessage` was persisted). User sees welcome screen instead of conversation + question form.
- **Bug 6 — cross-session message corruption**: User has Tab A streaming (sessionId="abc", assistantMessageId="msg1"). User clicks Tab B. `handleTabSelect` calls `loadSessionMessages("def")` which overwrites `messages` with Tab B's data. Tab A's `createStreamHandler` closure still calls `setMessages(prev => prev.map(m => m.id === "msg1" ? {...m, content: newContent} : m))` — but "msg1" doesn't exist in Tab B's messages, so the update is a no-op and Tab A's streaming content is lost. Meanwhile, if Tab B starts its own stream, its `createStreamHandler` and Tab A's `createStreamHandler` both call `setMessages` on the same shared state, interleaving content from both sessions.
- **Bug 6 — shared abortRef**: Tab A is streaming with `abortRef.current = controllerA`. Tab B starts a stream, setting `abortRef.current = controllerB`. User switches back to Tab A and clicks stop — `abortRef.current.abort()` aborts `controllerB` (Tab B's stream), not `controllerA` (Tab A's stream).
- **Bug 6 — pendingStream leak**: Tab A sends a message, `_pendingStream = true`. User switches to Tab B before `session_start` arrives. Tab B's `isStreaming` evaluates `streamingSessions.has("def") || _pendingStream` — `_pendingStream` is `true` (set by Tab A), so Tab B shows a spinner even though it's idle.
- **Bug 6 — pendingQuestion leak**: Tab A receives `ask_user_question`, setting `pendingQuestion` state. User switches to Tab B. Tab B renders the question form because `pendingQuestion` is a single shared `useState` — the question appears in Tab B's context with no relation to Tab B's conversation.
- **Bug 7 — unbounded tab creation**: User clicks "+" repeatedly, opening 15+ tabs. Each tab holds an SSE connection, an `AbortController`, and a messages array in the per-tab state map. The browser's per-origin connection limit (typically 6 for HTTP/1.1) is exhausted, causing new SSE connections to queue. Memory grows linearly with tab count × messages. There is no cap or feedback to the user.
- **Bug 8 — invisible tab status**: User has 4 tabs open. Tab 1 is streaming in the background, Tab 2 has a pending `ask_user_question`, Tab 3 encountered an error, and Tab 4's stream completed while the user was on Tab 1. All tab headers look identical — the user must click each tab to discover its state, missing the pending question on Tab 2 and the error on Tab 3.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- "Thinking..." spinner displays when backend has not yet sent any `assistant` SSE event (Req 3.1)
- Simple single-turn queries render in a single message bubble with no visual difference (Req 3.2)
- `ask_user_question` pauses streaming and displays the question form (Req 3.3)
- `cmd_permission_request` pauses streaming and displays the permission modal (Req 3.4)
- `result` event finalizes conversation, stops streaming, invalidates radar caches (Req 3.5)
- Stop button aborts stream and displays stop confirmation (Req 3.6)
- `ContentBlockRenderer` renders text, tool_use, tool_result blocks inside message bubble (Req 3.7)
- `ToolUseBlock` shows tool name and collapsible input (Req 3.8)
- `getSessionMessages` returns all persisted messages for completed sessions (Req 3.9)
- Error events are rendered as text content in message history (Req 3.10)
- Single-tab usage behaves identically to current behavior (Req 3.11)
- Closing a tab cleans up its resources as it does today (Req 3.12)
- Below the tab limit (< 6 tabs), the "+" button creates new tabs as it does today (Req 3.13)
- Idle tabs (no streaming, no pending events) display no status indicator on the tab header (Req 3.14)

**Scope:**
All inputs that do NOT involve the eight bug conditions should be completely unaffected. This includes:
- Simple single-turn conversations
- Mouse/keyboard interactions with the chat input
- Session history loading for completed sessions
- Tab management (open, close, switch) for non-streaming tabs
- Plugin commands

## Hypothesized Root Cause

Based on the bug descriptions and code analysis of `ChatPage.tsx`, `chat.ts`, and the streaming lifecycle:

### Bug 1: isStreaming State Machine Lifecycle Gaps

1. **Race between `setSessionId` and `setIsStreaming`**: When `session_start` arrives, `setSessionId(event.sessionId)` triggers a re-render. The `isStreaming` derivation uses the new `sessionId` to check `streamingSessions`, but `setIsStreaming(true)` was called with the old `sessionId` (undefined). The `_pendingStream` fallback was added to cover this gap, but `setIsStreaming(true)` clears `_pendingStream` when it also writes to `streamingSessions` — creating a window where neither flag is set for the new sessionId.

2. **`createCompleteHandler` is a closure over `setIsStreaming`**: The `createCompleteHandler` returns `() => setIsStreaming(false)`. This closure captures `setIsStreaming` which itself closes over `sessionId`. When `ask_user_question` calls `setIsStreaming(false)` and then the SSE reader finishes and calls the complete handler, we get a double-clear. If the user submits an answer between these two calls (triggering `setIsStreaming(true)` for the new stream), the stale complete handler clears the new stream's state.

3. **No stream generation tracking**: There is no mechanism to distinguish "which stream" a complete handler belongs to. When `cmd_permission_request` is approved and `streamCmdPermissionContinue` starts a new SSE stream, the old stream's complete handler can still fire and call `setIsStreaming(false)`.

### Bug 2: Auto-Scroll Failure

4. **`scrollToBottom` fires on every `messages` change but scrolls to `messagesEndRef`**: The `useEffect` calls `scrollToBottom()` whenever `messages` changes. However, `messagesEndRef` is placed after the spinner, not after the latest content block. During streaming, new content blocks are appended to an existing assistant message (same array reference updated via spread), so the scroll fires but the viewport jumps to the spinner area — the actual tool details rendered by `ContentBlockRenderer` may be above the fold if the message bubble is tall.

5. **No "user scrolled up" detection**: The current implementation always auto-scrolls. There is no mechanism to detect if the user manually scrolled up to review history, which would make auto-scroll disruptive.

### Bug 3: Error Visibility

6. **Error replaces entire assistant message content**: In `createStreamHandler`, the error handler does `content: [{ type: 'text', text: \`Error: ...\` }]` — it replaces the content array rather than appending. But the error message is rendered as plain text with no visual distinction. If the message is above the viewport, the user never sees it.

7. **Spinner continues after error**: The error handler in `createStreamHandler` does not call `setIsStreaming(false)`. Only `createErrorHandler` (for connection errors) and `createCompleteHandler` (for SSE reader finish) clear streaming state. If the backend sends an `error` event but the SSE stream stays open (e.g., heartbeat continues), the spinner persists.

### Bug 4: Shallow Activity Labels

8. **`deriveStreamingActivity` only extracts `toolName`**: The function finds the last `tool_use` block and returns `block.name`. It does not look at `block.input` to extract operational context (file path, command, search query).

9. **No minimum display duration**: Each new `tool_use` block immediately updates the label via `useMemo`. During rapid tool calls (< 2s apart), the label flickers.

### Bug 5: State Loss on Re-mount

10. **All state is `useState`-only**: `messages`, `pendingQuestion`, `sessionId` are React state. When the component unmounts (tab switch, hot reload), all state is lost. `loadSessionMessages` only works for completed sessions where a `ResultMessage` was persisted to the database.

### Bug 6: Cross-Session State Corruption During Multi-Tab Usage

11. **Single-instance `messages` state shared across all tabs**: `messages` is a single `useState<Message[]>` in `ChatPage.tsx`. When `handleTabSelect` is called, it overwrites `messages` with the target tab's data via `loadSessionMessages(tab.sessionId)`. But any active `createStreamHandler` closure from the previous tab still references `setMessages` and calls `setMessages(prev => prev.map(m => m.id === assistantMessageId ? {...m, content} : m))`. Since `messages` now contains the new tab's data, the `assistantMessageId` from the old tab doesn't match any message — the update is a no-op and the old tab's streaming content is silently lost. Worse, if the old tab's stream appends a new message (not just updates), it gets added to the new tab's message array, creating cross-session contamination.

12. **Single `abortRef` shared across all tabs**: `abortRef = useRef<AbortController | null>(null)` holds one abort controller. When Tab B starts a stream, it sets `abortRef.current = new AbortController()`, overwriting Tab A's controller. Tab A's stream can no longer be stopped. If the user clicks stop while viewing Tab A, `abortRef.current.abort()` aborts Tab B's stream instead.

13. **`_pendingStream` is a single boolean, not per-tab**: When Tab A sends a message, `_pendingStream` is set to `true`. If the user switches to Tab B before Tab A's `session_start` arrives, Tab B's `isStreaming` derivation (`streamingSessions.has(sessionId) || _pendingStream`) evaluates to `true` because `_pendingStream` is globally `true`. Tab B shows a spinner for a stream it never started.

14. **`pendingQuestion` is a single `useState`, not per-tab**: When Tab A receives `ask_user_question`, `setPendingQuestion(question)` sets a single shared state. Switching to Tab B renders the question form because the component reads from the same `pendingQuestion` state — the question appears in Tab B's context with no relation to Tab B's conversation.

15. **`handleTabSelect` overwrites in-progress state without saving**: When the user switches tabs, `handleTabSelect` calls `loadSessionMessages(tab.sessionId)` which sets `messages` to the target tab's persisted messages. If the source tab was streaming (messages only in React state, not yet persisted via `ResultMessage`), those messages are overwritten and lost. There is no mechanism to save the current tab's state before loading the new tab's state.

### Bug 7: Unlimited Tab Creation Causes Resource Exhaustion

16. **No cap on tab creation**: The `handleNewSession` callback (triggered by the "+" button) creates a new tab unconditionally. There is no guard checking the current tab count. Each tab holds an SSE connection (when streaming), an `AbortController`, and a messages array in the per-tab state map. The browser enforces per-origin connection limits (typically 6 for HTTP/1.1, higher for HTTP/2 but still finite). With enough tabs streaming concurrently, new SSE connections queue behind the limit, causing apparent hangs. Memory grows linearly with tab count × messages per tab.

### Bug 8: No Visual Status Indication on Tab Headers

17. **Tab headers show only the tab title**: The tab header component renders the tab name/title but has no mechanism to display the tab's lifecycle status. The `TabState` interface does not include a `status` field. Stream event handlers (`createStreamHandler`, `ask_user_question` handler, `cmd_permission_request` handler, error handler, result handler) update per-tab state but do not derive or store a status value. The tab header component has no access to per-tab status even if it were computed. As a result, users must switch to each tab to discover whether it is streaming, waiting for input, errored, or has new completed content.

## Correctness Properties

Property 1: Fault Condition — Streaming State Continuity During sessionId Transition

_For any_ streaming session where `sessionId` transitions from `undefined` to a real value while the stream is active, the fixed `isStreaming` derivation SHALL remain `true` continuously with no intermediate `false` value between consecutive renders.

**Validates: Requirements 2.1**

Property 2: Fault Condition — Single Authoritative Streaming State Clear

_For any_ streaming session where an `ask_user_question` or `cmd_permission_request` event pauses streaming, the fixed code SHALL call `setIsStreaming(false)` exactly once via the event handler, and the `createCompleteHandler` SHALL be a no-op if streaming was already cleared by the event handler.

**Validates: Requirements 2.2, 2.3**

Property 3: Fault Condition — Stream Generation Isolation

_For any_ streaming session where a `cmd_permission_request` is approved and a new stream starts, the previous stream's `createCompleteHandler` SHALL NOT clear the new stream's `isStreaming` state, enforced by a monotonically increasing stream generation counter.

**Validates: Requirements 2.3**

Property 4: Fault Condition — Auto-Scroll to Latest Content Block

_For any_ streaming session where a new content block (text, tool_use, tool_result) is appended to the assistant message and the user has not manually scrolled up, the chat viewport SHALL auto-scroll to keep the latest content block visible. Additionally, _for any_ state where the user has manually scrolled up and then sends a new message, the auto-scroll SHALL reset (re-enable) so the new conversation flow is visible from the start.

**Validates: Requirements 2.4**

Property 5: Fault Condition — Error Visibility During Streaming

_For any_ streaming session where an error event is received, the fixed code SHALL auto-scroll to the error content, visually distinguish it from normal text, and stop the streaming indicator so the spinner does not continue showing "Processing..." after an error.

**Validates: Requirements 2.5**

Property 6: Fault Condition — Activity Indicator Operational Context

_For any_ streaming session where a tool is invoked with non-empty input, the activity indicator SHALL display the tool name plus a brief summary extracted from the tool input (e.g., command, file path, search query), truncated to a maximum display length.

**Validates: Requirements 2.6**

Property 7: Fault Condition — Activity Indicator Stability

_For any_ streaming session with rapid tool invocations (interval < 2 seconds), the activity indicator SHALL display each label for a minimum duration before transitioning, and SHALL include a cumulative tool count for the current turn.

**Validates: Requirements 2.7**

Property 8: Fault Condition — Pending Question State Persistence

_For any_ session where an `ask_user_question` event has been received and the SSE stream has ended without a `result` event, the fixed code SHALL persist the conversation messages and pending question state to `sessionStorage` so they survive component re-mounts.

**Validates: Requirements 2.8, 2.9, 2.10**

Property 9: Fault Condition — Graceful Degradation on Storage Failure

_For any_ session where `sessionStorage` operations fail (quota exceeded on write, corrupted/schema-mismatched data on read, or `JSON.parse` throws), the fixed code SHALL fall back gracefully to the current behavior (no persistence, welcome screen on re-mount) without throwing unhandled exceptions or breaking the streaming lifecycle.

**Validates: Requirements 2.8, 2.9, 2.10**

Property 10: Preservation — Normal Streaming Lifecycle

_For any_ input where none of the eight bug conditions hold (simple single-turn queries, completed sessions, non-streaming interactions), the fixed code SHALL produce identical behavior to the original code, preserving spinner labels, message rendering, session loading, and all existing UI interactions.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12**

Property 11: Fault Condition — Per-Tab State Isolation

_For any_ multi-tab session where the user switches between tabs (including during active streaming), the fixed code SHALL maintain per-tab state isolation such that: (a) switching from Tab A to Tab B saves Tab A's state (messages, sessionId, pendingQuestion, abortController, pendingStream) and restores Tab B's state without data loss in either direction, (b) a `createStreamHandler` closure for Tab A SHALL only update Tab A's state in the per-tab store and SHALL NOT modify Tab B's foreground `useState` variables, and (c) `_pendingStream` and `pendingQuestion` for Tab A SHALL NOT be visible when Tab B is the active tab.

**Validates: Requirements 2.11, 2.12, 2.13, 2.14, 2.15**

Property 12: Fault Condition — Per-Tab Abort Controller Isolation

_For any_ multi-tab session where multiple tabs have active streams, each tab SHALL have its own `AbortController` stored in the per-tab state map. Clicking the stop button SHALL abort only the active (foreground) tab's stream via that tab's abort controller. Background tabs' abort controllers SHALL remain unaffected.

**Validates: Requirements 2.14**

Property 13: Fault Condition — Tab Creation Blocked at Limit

_For any_ state where `MAX_OPEN_TABS` (8) tabs are already open and the user clicks "+" to create a new tab, the fixed code SHALL NOT create the new tab and SHALL display a toast notification "Maximum tabs reached. Close a tab to open a new one." When the user closes a tab bringing the count below 6, the "+" button SHALL resume creating new tabs normally.

**Validates: Requirements 2.16, 2.17**

Property 14: Fault Condition — Tab Status Indicator Reflects Lifecycle State

_For any_ tab with an active stream, the tab header SHALL show a pulsing blue dot. _For any_ tab with a pending `ask_user_question`, the tab header SHALL show an orange "?" indicator. _For any_ tab with a pending `cmd_permission_request`, the tab header SHALL show a yellow "⚠" indicator. _For any_ tab whose stream encountered an error, the tab header SHALL show a red "!" indicator. _For any_ background tab whose stream completed (ResultMessage received while not active), the tab header SHALL show a static green dot.

**Validates: Requirements 2.18, 2.19, 2.20, 2.21, 2.22**

Property 15: Fault Condition — Tab Status Transitions Follow State Machine

Tab status transitions SHALL follow a defined state machine: `idle → streaming → {waiting_input | permission_needed | error | complete_unread} → idle`. Switching to a tab with `complete_unread` status SHALL clear it to `idle`. Answering a pending question SHALL transition from `waiting_input` back to `streaming` (or `idle` if the stream completes). Approving a permission request SHALL transition from `permission_needed` back to `streaming`. Sending a new message on a tab with `error` status SHALL transition from `error` to `streaming` (via `handleSendMessage` calling `updateTabStatus`).

**Validates: Requirements 2.23**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

### Fix 1: Stream Generation Counter and Robust isStreaming (Bug 1)

**File**: `desktop/src/pages/ChatPage.tsx`

**Specific Changes**:

1. **Add a stream generation counter (`streamGenRef`)**: Use a `useRef<number>(0)` that increments each time a new stream starts (`handleSendMessage`, `handleAnswerQuestion`, `handlePermissionDecision`). The `createCompleteHandler` captures the current generation at creation time and only calls `setIsStreaming(false)` if the generation hasn't changed. **CRITICAL**: The complete handler must use `streamGenRef.current` (ref access, not closure) for the comparison, and capture only the generation number as a closure value. This avoids the stale `sessionId` closure problem — the generation counter is the sole authority for whether a complete handler is still valid.

2. **Keep `_pendingStream` set until sessionId is registered, then clear it**: Modify `setIsStreaming(true)` to always set `_pendingStream = true`. In the `session_start` event handler, after calling `setSessionId(event.sessionId)`, also add the new sessionId to `streamingSessions` AND clear `_pendingStream = false`. This ensures a clean handoff: `_pendingStream` covers the gap, then `streamingSessions` takes over, and `_pendingStream` is cleared to prevent leaking across sessions. The `isStreaming` derivation remains `streamingSessions.has(sessionId) || _pendingStream`.

3. **Event-driven streaming clear**: When `ask_user_question` or `cmd_permission_request` events call `setIsStreaming(false)`, also increment `streamGenRef` so the pending `createCompleteHandler` becomes a no-op. When `error` events in `createStreamHandler` call `setIsStreaming(false)`, also increment `streamGenRef` for the same reason.

4. **Refactor `setIsStreaming` to not close over `sessionId`**: Replace the `useCallback` that closes over `sessionId` with a version that reads `sessionId` from a ref (`sessionIdRef`). Add `const sessionIdRef = useRef(sessionId)` kept in sync via `useEffect`. This ensures `setIsStreaming(false)` always operates on the current sessionId, not a stale closure value.

```
// Pseudocode for stream generation guard (per-tab aware)
// When starting a new stream for the active tab:
const tabId = activeTabIdRef.current;
const tabState = tabStateRef.current.get(tabId);
tabState.streamGen += 1;
const gen = tabState.streamGen;

const completeHandler = () => {
  const currentTabState = tabStateRef.current.get(tabId);
  if (!currentTabState || currentTabState.streamGen !== gen) return; // stale or closed tab — no-op
  
  // Clear streaming for this tab in the map
  currentTabState.pendingStream = false;
  
  // Only update useState if this is still the active foreground tab
  if (activeTabIdRef.current === tabId) {
    setIsStreaming(false);
  }
};

// In session_start handler (also tab-aware):
const tabState = tabStateRef.current.get(tabId);
if (tabState) {
  tabState.sessionId = event.sessionId;
  tabState.pendingStream = false; // Clean handoff to streamingSessions
}
setSessionId(event.sessionId);
setStreamingSessions(prev => { const next = new Set(prev); next.add(event.sessionId); return next; });
_setPendingStream(false);
```

### Fix 2: Auto-Scroll with User Scroll Detection (Bug 2)

**File**: `desktop/src/pages/ChatPage.tsx`

**Specific Changes**:

1. **Add `userScrolledUpRef`**: A `useRef<boolean>(false)` that tracks whether the user has manually scrolled up from the bottom.

2. **Add scroll event listener on the messages container**: Attach an `onScroll` handler to the `div.overflow-y-auto` container. If `scrollTop + clientHeight < scrollHeight - threshold` (e.g., threshold = 100px), set `userScrolledUpRef.current = true`. If the user scrolls back to the bottom, reset to `false`.

3. **Conditional auto-scroll**: Modify the `useEffect` that calls `scrollToBottom()` to only scroll if `userScrolledUpRef.current === false`.

4. **Reset on new user message**: When the user sends a new message, reset `userScrolledUpRef.current = false` so auto-scroll resumes.

### Fix 3: Error Visibility and Streaming Stop (Bug 3)

**File**: `desktop/src/pages/ChatPage.tsx`

**Specific Changes**:

1. **Stop streaming on error event**: In `createStreamHandler`, when `event.type === 'error'`, call `setIsStreaming(false)` AND increment `streamGenRef.current` after updating the message content. This stops the spinner from showing "Processing..." indefinitely and ensures the pending `createCompleteHandler` (which will fire when the SSE reader finishes) becomes a no-op via the stream generation guard from Fix 1.

2. **Force scroll to error**: After setting the error message content, reset `userScrolledUpRef.current = false` and trigger a scroll to bottom so the error is visible.

3. **Error styling**: Use a structured `isError` boolean flag on the message object rather than detecting "Error:" text prefix (which is fragile and could false-positive on assistant messages containing "Error:"). In `createStreamHandler`, when handling `event.type === 'error'`, set `isError: true` on the message. `MessageBubble` checks this flag and applies a red/warning border style. This keeps detection reliable and decoupled from content. **Type change**: Add `isError?: boolean` as an optional field to the `Message` interface in `desktop/src/types/index.ts`. This is a backward-compatible addition — existing messages without the field default to `false`/`undefined` (no error styling).

### Fix 4: Enhanced deriveStreamingActivity (Bug 4)

**File**: `desktop/src/pages/ChatPage.tsx`

**Function**: `deriveStreamingActivity`

**Specific Changes**:

1. **Extract operational context from tool input**: Extend the return type to `{ hasContent: boolean; toolName: string | null; toolContext: string | null; toolCount: number }`. For the last `tool_use` block, extract a brief summary from `block.input`:
   - If `input.command` exists: use first 60 chars of command, **sanitized to remove potential secrets** (strip anything after `--password`, `--token`, `--key`, or env var assignments like `KEY=value`; if the entire command is sensitive, return `[command]` placeholder)
   - If `input.path` or `input.file_path` exists: use the file path (paths are not sensitive — they're visible in ToolUseBlock already)
   - If `input.query` or `input.search` or `input.pattern` exists: use first 60 chars
   - Otherwise: `null`

2. **Count tool invocations**: Count all `tool_use` blocks in the last assistant message's content array. Return as `toolCount`.

3. **Minimum display duration via debounce state (separate from pure function)**: The debounce logic MUST NOT be placed inside `deriveStreamingActivity` (which remains a pure, exported function for testability). Instead, add a separate `useDisplayedActivity` hook or inline state in `ChatPage` that consumes the output of `deriveStreamingActivity` and applies a minimum display duration. Use a `useRef` for `lastActivityChangeTime` and a `useState` for `displayedActivity`. Only update `displayedActivity` if at least 1.5 seconds have elapsed since the last change, or if the new activity is the final one (streaming stopped). This prevents flickering during rapid tool calls. The 1.5s duration should be a named constant (`MIN_ACTIVITY_DISPLAY_MS = 1500`) to allow test overrides via dependency injection or timer mocking. **CRITICAL**: The debounce `setTimeout` must be cleaned up on unmount — store the timer ID in a ref and clear it in the useEffect cleanup function to prevent React state-update-on-unmounted-component warnings. **Testing**: Use `vi.useFakeTimers()` in tests to control time progression. Call `vi.advanceTimersByTime(MIN_ACTIVITY_DISPLAY_MS)` to trigger debounce transitions deterministically. Restore with `vi.useRealTimers()` in afterEach.

**Spinner label update** (in JSX) — use two i18n keys to handle context presence/absence cleanly:
```
// When toolContext is present: "Running: Bash — npm test (12 tools used)"
// When toolContext is absent:  "Running: Bash (3 tools used)"
// When toolCount is 1 and no context: "Running: Bash"
streamingActivity?.toolName
  ? (streamingActivity.toolContext
      ? t('chat.runningToolWithContext', {
          tool: streamingActivity.toolName,
          context: streamingActivity.toolContext,
          count: streamingActivity.toolCount
        })
      : streamingActivity.toolCount > 1
        ? t('chat.runningToolWithCount', {
            tool: streamingActivity.toolName,
            count: streamingActivity.toolCount
          })
        : t('chat.runningTool', { tool: streamingActivity.toolName }))
  : streamingActivity?.hasContent
    ? t('chat.processing')
    : t('chat.thinking')
```

**New i18n keys**:
```json
"runningToolWithContext": "Running: {{tool}} — {{context}} ({{count}} tools used)",
"runningToolWithCount": "Running: {{tool}} ({{count}} tools used)"
```

### Fix 5: Persist Pending State to sessionStorage (Bug 5)

**File**: `desktop/src/pages/ChatPage.tsx`

**Specific Changes**:

1. **Persist on `ask_user_question`**: When `ask_user_question` event is handled, write `{ messages, pendingQuestion, sessionId }` to `sessionStorage` under key `chat_pending_{sessionId}`.

2. **Restore on mount**: In the initialization `useEffect`, check `sessionStorage` for a pending state matching the current sessionId. If found, restore `messages`, `pendingQuestion`, and display the question form.

3. **Clean up on completion**: When a `result` event is received or the user submits an answer and streaming completes successfully, remove the `sessionStorage` entry.

4. **Serialization**: Messages contain `ContentBlock[]` which are plain objects — they serialize to JSON cleanly. The `PendingQuestion` type is also a plain object.

**Storage key format**: `swarm_chat_pending_{sessionId}`

**Size consideration**: A typical 50-tool conversation with content blocks is ~50-200KB of JSON. `sessionStorage` has a 5-10MB limit per origin, which is sufficient. We store at most one pending state per session. The serialization happens once per `ask_user_question` event (not on every render), so the synchronous JSON cost is acceptable. **For very large sessions (80+ tools)**, if serialization exceeds 500KB, truncate `tool_result` content blocks to their first 200 chars before serializing — tool results are the largest payload and are already rendered in the UI. This keeps the serialized size bounded.

**Trade-off — sessionStorage vs localStorage vs SQLite**: `sessionStorage` is chosen because pending question state is inherently ephemeral — it only matters while the browser tab is open. If the user closes the tab entirely, the agent conversation is effectively abandoned (the backend will time out). `localStorage` would persist across tab closes but risks stale entries. SQLite would require a new API endpoint and schema migration for a transient concern. `sessionStorage` is the simplest fit. **Tauri note**: In Tauri 2.0, the webview's `sessionStorage` persists for the lifetime of the app window (not just the page navigation). Tauri does not recreate the webview during normal tab switching or navigation within the SPA. If Tauri's webview lifecycle changes in future versions, this can be migrated to Tauri's `Store` plugin with minimal code change (same key-value API pattern).

**Stale entry cleanup**: On component mount, scan `sessionStorage` for keys matching `swarm_chat_pending_*`. Clean up is bounded: process at most 5 entries per mount (stale entries beyond 5 are cleaned on subsequent mounts). For each entry, check if the sessionId still exists via `chatService.getSession()`. If the session has a completed status or the API returns 404, remove the stale entry. **Defer cleanup** using `setTimeout(cleanup, 2000)` so it doesn't block initial render. This cleanup runs once per mount and is bounded by the cap.

**Error handling and availability check**: Before any `sessionStorage` operation, check `typeof window !== 'undefined' && typeof window.sessionStorage !== 'undefined'`. This guards against environments where `sessionStorage` is unavailable (private browsing restrictions, Tauri webview edge cases). Wrap `sessionStorage.setItem()` in a try/catch. If it throws (quota exceeded), log a warning and continue without persistence — the user experience degrades gracefully to the current behavior (state lost on re-mount). Similarly, wrap `sessionStorage.getItem()` + `JSON.parse()` in try/catch — if deserialization fails (corrupted data, schema mismatch after code update), discard the entry and fall back to normal initialization.

### Fix 6: Per-Tab State Map for Cross-Session Isolation (Bug 6)

**File**: `desktop/src/pages/ChatPage.tsx`

**Design Approach**: Introduce a `useRef<Map<string, TabState>>` that stores per-tab state keyed by `tabId`. The existing `useState` variables (`messages`, `sessionId`, `pendingQuestion`, `_pendingStream`) remain as the "active view" — they reflect the currently visible tab's state. The per-tab map acts as a backing store that is saved-to on tab switch away and restored-from on tab switch to. The `createStreamHandler` closures capture the `tabId` and write to the per-tab map directly, only updating `useState` if the tab is still the active foreground tab.

This is the minimal change that isolates state without a full architectural rewrite.

**Type Definition**:

```typescript
interface TabState {
  messages: Message[];
  sessionId: string | undefined;
  pendingQuestion: PendingQuestion | null;
  abortController: AbortController | null;
  pendingStream: boolean;
  streamGen: number; // per-tab stream generation counter
  status: 'idle' | 'streaming' | 'waiting_input' | 'permission_needed' | 'error' | 'complete_unread'; // tab lifecycle status for header indicators
}
```

**Specific Changes**:

1. **Add `tabStateRef`**: `const tabStateRef = useRef<Map<string, TabState>>(new Map())`. This map persists across renders (it's a ref, not state) and survives tab switches within the same component instance. **Note**: `TabState` entries in this map are intentionally mutable — `createStreamHandler` updates `tabState.messages` directly for background tabs without triggering re-renders. Only the active tab's state is mirrored to `useState` for rendering. This is by design: background tab updates should be silent (no re-renders) until the user switches to that tab.

2. **Add `activeTabIdRef`**: `const activeTabIdRef = useRef<string | null>(null)`. Tracks which tab is currently in the foreground. Updated in `handleTabSelect` and when a new tab is created. **Implementation note — ref grouping**: All refs introduced by this spec (`tabStateRef`, `activeTabIdRef`, `streamGenRef`, `userScrolledUpRef`, `sessionIdRef`, `messagesRef`, `pendingQuestionRef`, plus the existing `abortRef` and `messagesEndRef`) should be grouped together in the component with a comment block:
   ```typescript
   // --- Refs: streaming lifecycle & per-tab state isolation ---
   // These refs are used by stream handlers, tab switch logic, and scroll detection.
   // They are intentionally refs (not state) to avoid stale closures and unnecessary re-renders.
   const tabStateRef = useRef<Map<string, TabState>>(new Map());
   const activeTabIdRef = useRef<string | null>(null);
   const streamGenRef = useRef<number>(0);
   const userScrolledUpRef = useRef<boolean>(false);
   const sessionIdRef = useRef(sessionId);
   const messagesRef = useRef(messages);
   const pendingQuestionRef = useRef(pendingQuestion);
   ```

3. **Save current tab state on tab switch away**: In `handleTabSelect`, before loading the new tab's data, save the current tab's state to the map. **CRITICAL**: The save MUST read from refs (not useState) to capture the latest state, because a `createStreamHandler` callback may have fired between the last render and this save call, updating `useState` asynchronously. Refs are updated synchronously by the stream handler (via the per-tab map), so they always reflect the latest state:
   ```typescript
   // Save current tab state — read from per-tab map (authoritative) not useState (may be stale)
   const currentTabId = activeTabIdRef.current;
   if (currentTabId) {
     // The per-tab map already has the latest state from any background stream handlers.
     // Only update it from useState if no map entry exists yet (first save for this tab).
     if (!tabStateRef.current.has(currentTabId)) {
       tabStateRef.current.set(currentTabId, {
         messages: messagesRef.current,
         sessionId: sessionIdRef.current,
         pendingQuestion: pendingQuestionRef.current,
         abortController: abortRef.current,
         pendingStream: _pendingStream,
         streamGen: streamGenRef.current,
       });
     }
     // If the map already has an entry, it's authoritative (stream handlers update it directly).
     // Just sync the non-streaming fields that only change via useState:
     const existing = tabStateRef.current.get(currentTabId)!;
     existing.pendingQuestion = pendingQuestionRef.current;
     existing.pendingStream = _pendingStream;
   }
   ```
   **Note**: `messagesRef`, `sessionIdRef`, `pendingQuestionRef` are refs kept in sync with their corresponding `useState` values via a single consolidated `useEffect` (not 3 separate effects — see performance note below).

4. **Restore target tab state on tab switch to**: After saving, restore the target tab's state from the map (or initialize defaults if the tab is new). **CRITICAL — Async guard**: `loadSessionMessages` is async. If the user rapidly switches tabs (A→B→C), the async loads can resolve out of order. The restore MUST check `activeTabIdRef.current === targetTabId` when the async load resolves, and discard the result if the user has already switched away:
   ```typescript
   activeTabIdRef.current = targetTabId;
   const targetState = tabStateRef.current.get(targetTabId);
   if (targetState) {
     setMessages(targetState.messages);
     setSessionId(targetState.sessionId);
     setPendingQuestion(targetState.pendingQuestion);
     abortRef.current = targetState.abortController;
     _setPendingStream(targetState.pendingStream);
     streamGenRef.current = targetState.streamGen;
   } else if (tab.sessionId) {
     // New tab with existing session — load from API with async guard
     const loadedTabId = targetTabId; // capture for closure
     loadSessionMessages(tab.sessionId).then(() => {
       // Only apply if user hasn't switched away during the async load
       if (activeTabIdRef.current !== loadedTabId) return;
     });
     setPendingQuestion(null);
     abortRef.current = null;
     _setPendingStream(false);
   } else {
     // Brand new tab — initialize empty
     setMessages([createWelcomeMessage()]);
     setSessionId(undefined);
     setPendingQuestion(null);
     abortRef.current = null;
     _setPendingStream(false);
   }
   ```

5. **Modify `createStreamHandler` to be tab-aware**: The stream handler closure captures `tabId` at creation time. When updating messages, it checks if the tab is still active. **Call site updates required**: `handleSendMessage`, `handleAnswerQuestion`, `handlePermissionDecision` (approve path), and `streamCmdPermissionContinue` must all pass `activeTabIdRef.current` as the `tabId` argument when calling `createStreamHandler`. The `updateMessages` helper is extracted as a pure function and called once — the result is stored in both the per-tab map and (if active) the `useState`:
   ```typescript
   const createStreamHandler = (assistantMessageId: string, tabId: string) => {
     return (event: StreamEvent) => {
       // Guard: if tab was closed while stream was running, no-op
       const tabState = tabStateRef.current.get(tabId);
       if (!tabState) return;
       
       // Compute updated messages once (pure function)
       const updatedMessages = updateMessages(tabState.messages, assistantMessageId, event);
       
       // Always update the per-tab map (even for background tabs)
       tabState.messages = updatedMessages;
       tabStateRef.current.set(tabId, tabState);
       
       // Only update useState if this tab is the active foreground tab
       if (activeTabIdRef.current === tabId) {
         setMessages(updatedMessages);
       }
     };
   };
   ```
   This ensures background streams continue accumulating state in the map without corrupting the foreground tab's `useState`, and avoids running `updateMessages` twice.

6. **Per-tab abort controller**: When starting a new stream, create a new `AbortController` and store it in the per-tab map entry (not just in the shared `abortRef`). The stop button reads from `abortRef.current` which always points to the active tab's controller (restored during tab switch). Background tabs' controllers are only accessible via the map.

7. **Per-tab `_pendingStream`**: The `_pendingStream` flag is saved/restored as part of `TabState`. When switching tabs, the restored `_pendingStream` reflects only the target tab's pending state, not the source tab's.

8. **Per-tab `pendingQuestion`**: Same save/restore pattern. When switching to Tab B, `pendingQuestion` is restored from Tab B's `TabState` entry (or `null` if Tab B has no pending question). Tab A's pending question remains in the map.

9. **Clean up on tab close**: When a tab is closed, remove its entry from `tabStateRef.current` and abort its controller if active:
   ```typescript
   const handleTabClose = (tabId: string) => {
     const tabState = tabStateRef.current.get(tabId);
     if (tabState?.abortController) {
       tabState.abortController.abort();
     }
     tabStateRef.current.delete(tabId);
     // ... existing tab close logic
   };
   ```

**Interaction with Fix 1 (Stream Generation Counter)**: The stream generation counter from Fix 1 becomes per-tab — stored in `TabState.streamGen`. Each tab has its own generation counter. The `createCompleteHandler` captures both `tabId` and `gen` at creation time, and only clears streaming state if both match (correct tab AND correct generation).

**Interaction with Fix 5 (sessionStorage persistence)**: The `sessionStorage` persistence from Fix 5 operates on the per-tab state. When `ask_user_question` arrives for a tab, the persistence writes that tab's state from the map (not from `useState`, which may reflect a different tab if the user switched). The storage key includes the sessionId (which is unique per tab).

**Trade-off — `useRef<Map>` vs Context/Redux vs separate component instances**: A `useRef<Map>` is chosen because: (a) it's the minimal change — the existing `useState` variables remain as the active view, avoiding a rewrite of all consumers, (b) it doesn't trigger re-renders when background tabs update (unlike `useState` or Context), (c) it's local to `ChatPage` with no new dependencies, and (d) separate component instances per tab would require lifting all shared state (tab bar, session list) up and passing it down, which is a larger refactor. The downside is that the save/restore logic in `handleTabSelect` is imperative and must be kept in sync with any new state variables added in the future — a code comment and TypeScript interface (`TabState`) mitigate this risk.

**Ref sync performance**: The `messagesRef`, `sessionIdRef`, and `pendingQuestionRef` refs are kept in sync with their `useState` counterparts via a single consolidated `useEffect` (not 3 separate effects) to minimize hook overhead:
```typescript
useEffect(() => {
  messagesRef.current = messages;
  sessionIdRef.current = sessionId;
  pendingQuestionRef.current = pendingQuestion;
}, [messages, sessionId, pendingQuestion]);
```

**Per-tab streamGen initialization**: When a new tab is created (via `handleNewSession` or `addTab`), immediately initialize its entry in `tabStateRef` with `streamGen: 0` and default empty state. This ensures the generation counter is available from the start, not only after the first tab switch saves it. The `handleNewSession` callback should include:
```typescript
const newTabId = addTab(selectedAgentId); // addTab returns the new tab's ID
tabStateRef.current.set(newTabId, {
  messages: [createWelcomeMessage()],
  sessionId: undefined,
  pendingQuestion: null,
  abortController: null,
  pendingStream: false,
  streamGen: 0,
  status: 'idle',
});
activeTabIdRef.current = newTabId;
```

**Bounded map size**: The map is bounded by the number of open tabs, which is itself bounded by `MAX_OPEN_TABS` (8) enforced by Fix 7. Each `TabState` entry holds a reference to the messages array (not a copy), so memory overhead is minimal — it's the same objects that would exist in `useState` anyway. When a tab is closed, its entry is deleted from the map, releasing the reference.

### Fix 7: Tab Limit (Bug 7)

**File**: `desktop/src/pages/ChatPage.tsx`

**Specific Changes**:

1. **Add `MAX_OPEN_TABS` constant**: Define `const MAX_OPEN_TABS = 6` as a named constant at module scope. This bounds the number of concurrent SSE connections, per-tab state map entries, and `AbortController` instances.

2. **Guard in `handleNewSession`**: Before creating a new tab, check `tabStateRef.current.size >= MAX_OPEN_TABS`. If the limit is reached, show a toast notification and return early without creating the tab:
   ```typescript
   const handleNewSession = useCallback(() => {
     if (tabStateRef.current.size >= MAX_OPEN_TABS) {
       toast.info('Maximum tabs reached. Close a tab to open a new one.');
       return;
     }
     // ... existing tab creation logic
   }, [/* deps */]);
   ```

3. **No UI disabling of the "+" button**: The "+" button remains always clickable. The guard is in the handler, not the UI. This is simpler than managing a derived `isAtLimit` state for button disabling, and the toast provides clear feedback. The button could optionally show a tooltip when at limit, but this is a low-priority enhancement.

4. **Tab count derived from `tabStateRef`**: The check uses `tabStateRef.current.size` (the per-tab state map from Fix 6) as the authoritative tab count. This is always in sync with the actual number of open tabs because Fix 6 ensures entries are added on tab creation and removed on tab close.

**Interaction with Fix 6**: Fix 7 depends on Fix 6's `tabStateRef` map for the tab count. The `MAX_OPEN_TABS` constant also updates the "Bounded map size" guarantee — the map is now bounded by 6 entries, not "typically < 20".

**i18n key**:
```json
"maxTabsReached": "Maximum tabs reached. Close a tab to open a new one."
```

### Fix 8: Tab Status Indicators (Bug 8)

**File**: `desktop/src/pages/ChatPage.tsx`, tab header component (e.g., `ChatHeader.tsx` or the tab bar component)

**Design Approach**: Extend `TabState` with a `status` field that tracks the tab's lifecycle state. Update the status in stream event handlers. Expose tab statuses to the tab header component via a `useState<Record<string, TabStatus>>` that mirrors the status field from the per-tab map, triggering re-renders when status changes. Render a small indicator element in the tab header based on the status.

**Status States**:

| Status | Value | Icon | Color | Trigger |
|--------|-------|------|-------|---------|
| Idle | `'idle'` | none | — | Default, or after clearing unread |
| Streaming | `'streaming'` | `●` (pulsing) | blue | First `assistant` event in stream |
| Waiting for input | `'waiting_input'` | `?` | orange | `ask_user_question` event |
| Permission needed | `'permission_needed'` | `⚠` | yellow | `cmd_permission_request` event |
| Error | `'error'` | `!` | red | `error` event in stream |
| Complete (unread) | `'complete_unread'` | `●` (static) | green | `result` event while tab is background |

**Type Definition** (already added to `TabState` in Fix 6):

```typescript
type TabStatus = 'idle' | 'streaming' | 'waiting_input' | 'permission_needed' | 'error' | 'complete_unread';
```

**Specific Changes**:

1. **Add `tabStatuses` useState**: A `useState<Record<string, TabStatus>>` object that mirrors the `status` field from each `TabState` entry. This is the mechanism to trigger re-renders in the tab header when status changes. Updated alongside every `tabState.status` mutation:
   ```typescript
   const [tabStatuses, setTabStatuses] = useState<Record<string, TabStatus>>({});
   
   // Helper to update both the map and the useState
   const updateTabStatus = useCallback((tabId: string, status: TabStatus) => {
     const tabState = tabStateRef.current.get(tabId);
     if (tabState) {
       tabState.status = status;
     }
     setTabStatuses(prev => ({ ...prev, [tabId]: status }));
   }, []);
   ```

2. **Update status in stream handlers**: Modify `createStreamHandler` and related handlers to call `updateTabStatus`. **IMPORTANT**: Only call `updateTabStatus` on actual status transitions (guard with `if (tabState.status !== newStatus)`) to avoid unnecessary re-renders during rapid streaming events:
   - In `createStreamHandler`, on first `assistant` event (guard: `if (tabState.status !== 'streaming')`): `updateTabStatus(tabId, 'streaming')`
   - In `ask_user_question` handler: `updateTabStatus(tabId, 'waiting_input')`
   - In `cmd_permission_request` handler: `updateTabStatus(tabId, 'permission_needed')`
   - In error handler (within `createStreamHandler`): `updateTabStatus(tabId, 'error')`
   - In `result` handler: if `activeTabIdRef.current !== tabId` (background tab), `updateTabStatus(tabId, 'complete_unread')`; if foreground, `updateTabStatus(tabId, 'idle')`
   - When user answers a question or approves permission (restarting stream): `updateTabStatus(tabId, 'streaming')`
   - In `handleSendMessage`, when sending a new message on a tab with `error` status: `updateTabStatus(tabId, 'streaming')` (transitions `error → streaming → ...`). **Note**: `handleSendMessage` only operates on the active foreground tab — users cannot send messages to background tabs. If a background tab has `error` status, it retains that status until the user switches to it and sends a new message. The red "!" indicator on the tab header alerts the user to switch and address the error.

3. **Clear unread on tab switch**: In `handleTabSelect`, when switching to a tab with `complete_unread` status, clear it:
   ```typescript
   // In handleTabSelect, after restoring target tab state:
   if (tabStatuses[targetTabId] === 'complete_unread') {
     updateTabStatus(targetTabId, 'idle');
   }
   ```

4. **Render indicators in tab header**: The tab header component receives `tabStatuses` as a prop (or via context). For each tab, render a small indicator based on status:
   ```tsx
   const TabStatusIndicator: React.FC<{ status: TabStatus }> = ({ status }) => {
     switch (status) {
       case 'streaming':
         return <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" aria-label="Streaming" />;
       case 'waiting_input':
         return <span className="text-orange-500 text-xs font-bold" aria-label="Waiting for input">?</span>;
       case 'permission_needed':
         return <span className="text-yellow-500 text-xs" aria-label="Permission needed">⚠</span>;
       case 'error':
         return <span className="text-red-500 text-xs font-bold" aria-label="Error">!</span>;
       case 'complete_unread':
         return <span className="w-2 h-2 rounded-full bg-green-500" aria-label="New content" />;
       default:
         return null;
     }
   };
   ```

5. **Clean up on tab close**: When a tab is closed, remove its entry from `tabStatuses`:
   ```typescript
   // In handleTabClose:
   setTabStatuses(prev => {
     const next = { ...prev };
     delete next[tabId];
     return next;
   });
   ```

6. **Initialize status on tab creation**: When a new tab is created (in `handleNewSession`), initialize its status to `'idle'` in both the `TabState` map and `tabStatuses` useState.

**Accessibility**: Each indicator includes an `aria-label` attribute so screen readers can announce the tab status. The color choices provide sufficient contrast against typical tab header backgrounds. The pulsing animation uses CSS `animate-pulse` which respects `prefers-reduced-motion` media query (Tailwind's default behavior).

**Performance**: The `tabStatuses` useState is a flat `Record<string, TabStatus>` with at most 6 entries (bounded by `MAX_OPEN_TABS`). Updates create a shallow copy — negligible cost. The `updateTabStatus` helper is called only on status transitions (not on every stream event), so re-renders are infrequent.

**Trade-off — useState mirror vs. forceUpdate vs. useSyncExternalStore**: A `useState<Record>` mirror is chosen because: (a) it's the simplest pattern — no external store setup, no subscription management, (b) it integrates naturally with React's rendering model, (c) the data is small (≤ 6 entries) so shallow copies are cheap, and (d) `useSyncExternalStore` would require wrapping `tabStateRef` in a store API which is overkill for a single derived field. The downside is that `tabStatuses` must be kept in sync with `tabStateRef` manually — the `updateTabStatus` helper centralizes this to prevent drift.

### Fix 9: Elapsed Time Counter During Initial Wait (Customer Experience)

**File**: `desktop/src/pages/ChatPage.tsx` (or the extracted `useChatStreamingLifecycle` hook)

**Problem**: During the initial wait before the first `assistant` SSE event (typically 10-15 seconds, but can be 30+ seconds during cold starts or API throttling), the user sees only "Thinking..." with no indication of whether the system is stuck or just slow.

**Specific Changes**:

1. **Add `streamStartTimeRef`**: A `useRef<number | null>(null)` that records `Date.now()` when `setIsStreaming(true)` is called. Cleared when the first `assistant` event arrives or streaming stops.

2. **Add `elapsedSeconds` state**: A `useState<number>(0)` that updates every second while `isStreaming` is true and no content has been received yet (`streamingActivity === null`). Use a `setInterval` inside a `useEffect` that depends on `isStreaming` and `streamingActivity`. Clean up the interval on unmount or when content arrives.

3. **Show elapsed time after threshold**: Only show the elapsed counter after `ELAPSED_DISPLAY_THRESHOLD_MS = 10000` (10 seconds). Before that, just show "Thinking..." as today. After the threshold:
   ```
   "Thinking... (15s)"
   "Thinking... (30s)"
   "Thinking... (1m 5s)"
   ```

4. **i18n key**:
   ```json
   "thinkingWithElapsed": "Thinking... ({{elapsed}})"
   ```

5. **Format helper**: A small `formatElapsed(seconds: number): string` function that returns `"15s"`, `"1m 5s"`, etc.

6. **Clear on first content**: When `streamingActivity` transitions from `null` to non-null (first content block received), clear the interval and reset `elapsedSeconds` to 0. The spinner label switches to "Processing..." or "Running: {tool}..." and the elapsed counter disappears.

**Interaction with Fix 4 (debounce)**: The elapsed counter and the debounced activity label are mutually exclusive — elapsed shows only when `streamingActivity === null`, and the debounced label shows only when `streamingActivity !== null`. No conflict.

**Performance**: The `setInterval` fires once per second — negligible cost. It's active only during the initial wait period (typically 10-30 seconds), not during the entire streaming session.

### Fix 0: Extract `useChatStreamingLifecycle` Hook (Phase 0 Prerequisite)

**Files**: 
- New: `desktop/src/hooks/useChatStreamingLifecycle.ts`
- Modified: `desktop/src/pages/ChatPage.tsx`

**Design Approach**: Extract all streaming lifecycle logic from `ChatPage.tsx` into a custom hook. This is a pure refactor — no behavioral changes. The hook encapsulates:

1. **State**: `messages`, `sessionId`, `pendingQuestion`, `isStreaming`, `_pendingStream`, `streamingSessions`, `tabStatuses`, `elapsedSeconds`, `displayedActivity`
2. **Refs**: `tabStateRef`, `activeTabIdRef`, `streamGenRef`, `userScrolledUpRef`, `sessionIdRef`, `messagesRef`, `pendingQuestionRef`, `abortRef`, `streamStartTimeRef`
3. **Factories**: `createStreamHandler`, `createCompleteHandler`, `createErrorHandler`, `updateTabStatus`
4. **Handlers**: `handleTabSelect` (save/restore), `handleNewSession` (with tab limit), `handleTabClose` (cleanup)
5. **Derived**: `streamingActivity` (via `deriveStreamingActivity`), `displayedActivity` (debounced), `isStreaming` derivation

**Hook return type**:
```typescript
interface ChatStreamingLifecycle {
  // State for rendering
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  sessionId: string | undefined;
  setSessionId: React.Dispatch<React.SetStateAction<string | undefined>>;
  pendingQuestion: PendingQuestion | null;
  setPendingQuestion: React.Dispatch<React.SetStateAction<PendingQuestion | null>>;
  isStreaming: boolean;
  setIsStreaming: (streaming: boolean) => void;
  displayedActivity: StreamingActivity | null;
  elapsedSeconds: number;
  tabStatuses: Record<string, TabStatus>;
  
  // Refs for external access
  abortRef: React.MutableRefObject<AbortController | null>;
  userScrolledUpRef: React.MutableRefObject<boolean>;
  messagesEndRef: React.RefObject<HTMLDivElement>;
  
  // Factories
  createStreamHandler: (assistantMessageId: string, tabId: string) => (event: StreamEvent) => void;
  createCompleteHandler: (tabId: string, gen: number) => () => void;
  createErrorHandler: (assistantMessageId: string) => (error: Error) => void;
  
  // Tab management
  handleTabSelect: (tabId: string, tab: ChatTab) => Promise<void>;
  handleNewSession: () => void;
  handleTabClose: (tabId: string) => void;
}
```

**ChatPage.tsx after extraction**: ~500 lines focused on:
- JSX rendering (message list, spinner, input, sidebar, modals)
- User interaction handlers (`handleSendMessage`, `handleAnswerQuestion`, `handlePermissionDecision`, `handleStop`)
- Query hooks (`useQuery` for sessions, agents)
- TSCC panel integration
- Plugin command handling

**Testing**: The hook is testable independently using `renderHook` from `@testing-library/react`. All PBT properties can target the hook directly without rendering the full ChatPage component.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bugs on unfixed code, then verify the fixes work correctly and preserve existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bugs BEFORE implementing the fixes. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write unit tests that simulate the streaming lifecycle events and assert the expected state transitions. Run these tests on the UNFIXED code to observe failures.

**Test Cases**:
1. **sessionId Transition Test**: Simulate `session_start` event arriving after `setIsStreaming(true)` — assert `isStreaming` never becomes `false` during the transition (will fail on unfixed code)
2. **Double-Clear Test**: Simulate `ask_user_question` event followed by SSE reader completion — assert `setIsStreaming(false)` is called only once (will fail on unfixed code)
3. **Stale Complete Handler Test**: Simulate `cmd_permission_request` → approve → new stream start → old complete handler fires — assert new stream's `isStreaming` is not cleared (will fail on unfixed code)
4. **Auto-Scroll Test**: Simulate appending 20 tool_use blocks during streaming — assert viewport scrolls to latest block (will fail on unfixed code)
5. **Error Visibility Test**: Simulate error event during streaming — assert spinner stops and error is visible (will fail on unfixed code)
6. **State Persistence Test**: Simulate `ask_user_question` → unmount → remount — assert messages and question are restored (will fail on unfixed code)
7. **Cross-Session Message Corruption Test**: Simulate Tab A streaming → switch to Tab B → Tab A's stream handler fires — assert Tab B's messages are not modified and Tab A's messages are not lost (will fail on unfixed code)
8. **Shared AbortRef Test**: Simulate Tab A streaming → Tab B starts streaming → click stop on Tab A — assert Tab A's stream is aborted, not Tab B's (will fail on unfixed code)
9. **PendingStream Leak Test**: Simulate Tab A sends message (`_pendingStream = true`) → switch to Tab B — assert Tab B's `isStreaming` is `false` (will fail on unfixed code)
10. **PendingQuestion Leak Test**: Simulate Tab A receives `ask_user_question` → switch to Tab B — assert Tab B does not show the question form (will fail on unfixed code)
11. **Tab Limit Test**: Simulate opening 6 tabs → click "+" to create a 7th — assert no new tab is created and a toast is shown (will fail on unfixed code — currently allows unlimited tabs)
12. **Tab Status Indicator Test**: Simulate Tab A streaming in background → assert Tab A's header shows pulsing blue dot; simulate `ask_user_question` on Tab B → assert Tab B's header shows orange "?" (will fail on unfixed code — no indicators exist)

**Expected Counterexamples**:
- `isStreaming` briefly becomes `false` during sessionId transition
- `setIsStreaming(false)` called twice for `ask_user_question` events
- Stale complete handler clears new stream's state
- Latest content block not in viewport after rapid tool invocations
- Spinner continues after error event
- Messages and pending question lost after re-mount
- Tab A's messages corrupted when Tab A's stream handler fires while Tab B is active
- Stop button aborts Tab B's stream instead of Tab A's when both are streaming
- Tab B shows spinner due to Tab A's `_pendingStream` flag
- Tab A's pending question form appears in Tab B's context
- Tab A's in-progress conversation lost when switching to Tab B during streaming
- 9th tab created without any cap or user feedback when 8 tabs are already open
- Tab headers show no visual indicator for streaming, pending question, error, or completed background tabs

### Fix Checking

**Goal**: Verify that for all inputs where the bug conditions hold, the fixed functions produce the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := fixedStreamingLifecycle(input)
  ASSERT expectedBehavior(result)
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug conditions do NOT hold, the fixed functions produce the same result as the original functions.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT originalBehavior(input) = fixedBehavior(input)
END FOR
```

**Testing Approach**: Property-based testing with fast-check is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for normal streaming flows, then write property-based tests capturing that behavior.

**Test Cases**:
1. **Simple Query Preservation**: Verify single-turn queries render identically after fix
2. **Session Loading Preservation**: Verify `loadSessionMessages` returns same results for completed sessions
3. **Stop Button Preservation**: Verify stop button behavior is unchanged
4. **ContentBlockRenderer Preservation**: Verify text, tool_use, tool_result blocks render identically
5. **Tab Management Preservation**: Verify tab open/close/switch behavior is unchanged for non-streaming tabs

### Unit Tests

- Test `deriveStreamingActivity` with various message configurations (no messages, text only, tool_use with input, multiple tool_use blocks)
- Test stream generation counter logic: increment on new stream, complete handler no-op when generation mismatches
- Test `sessionStorage` persistence: write on `ask_user_question`, restore on mount, clean up on `result`
- Test auto-scroll logic: scroll when user at bottom, no scroll when user scrolled up, reset on new user message
- Test error handling: `setIsStreaming(false)` called on error event, error content visible
- Test per-tab state map: save state on tab switch, restore state on tab switch back, verify messages/sessionId/pendingQuestion are isolated
- Test tab-aware `createStreamHandler`: background tab stream updates per-tab map but not foreground `useState`
- Test per-tab abort controller: stop button aborts only active tab's controller
- Test per-tab `_pendingStream`: switching tabs does not leak `_pendingStream` from source to target
- Test per-tab `pendingQuestion`: switching tabs does not show source tab's pending question in target tab
- Test tab close cleanup: closing a tab removes its entry from `tabStateRef` and aborts its controller
- Test `MAX_OPEN_TABS` guard: `handleNewSession` returns early and shows toast when `tabStateRef.current.size >= 6`
- Test tab creation re-enabled after close: closing a tab when at limit allows `handleNewSession` to create a new tab
- Test `updateTabStatus` helper: updates both `tabStateRef` entry and `tabStatuses` useState in sync
- Test tab status transitions: `idle → streaming` on first assistant event, `streaming → waiting_input` on `ask_user_question`, `streaming → error` on error event, `streaming → complete_unread` on result while background, `complete_unread → idle` on tab switch
- Test `TabStatusIndicator` component: renders correct icon/color for each status, renders nothing for `idle`

### Property-Based Tests

- **Property 1 (Streaming Continuity)**: Generate random sequences of `session_start`, `assistant`, `ask_user_question`, `cmd_permission_request`, `result` events. Assert `isStreaming` is `true` continuously between stream start and the first pausing/completing event, with no false dips.
- **Property 2 (Single Clear)**: Generate random event sequences ending with `ask_user_question` or `cmd_permission_request`. Assert `setIsStreaming(false)` is called exactly once per pause.
- **Property 3 (Stream Generation Isolation)**: Generate sequences with overlapping streams (old complete handler firing after new stream starts). Assert the new stream's `isStreaming` is never cleared by the old handler.
- **Property 4 (Auto-Scroll)**: Generate random sequences of content block appends. Assert the latest block is in the viewport when user has not scrolled up.
- **Property 5 (Error Stops Streaming)**: Generate random streaming sessions with an error event at a random point. Assert `isStreaming` becomes `false` after the error.
- **Property 6 (Activity Label Stability)**: Generate rapid tool_use sequences (< 2s intervals). Assert each displayed label persists for at least the minimum display duration.
- **Property 7 (Operational Context Extraction)**: Generate random tool inputs with various key combinations (command, path, query, none). Assert the extracted context matches the expected key or is null.
- **Property 8 (State Persistence Round-Trip)**: Generate random message arrays and pending questions. Assert that serializing to sessionStorage and deserializing produces identical state.
- **Property 9 (Storage Graceful Degradation)**: Generate random corrupted JSON strings, schema-mismatched objects, and simulate quota-exceeded errors. Assert that the restore path never throws and falls back to default initialization.
- **Property 10 (Preservation)**: Generate random non-buggy inputs (simple queries, completed sessions). Assert `deriveStreamingActivity` output is identical between original and fixed code.
- **Property 11 (Per-Tab State Isolation)**: Generate random sequences of tab switches interleaved with stream events across 2-5 tabs. Assert that after any tab switch sequence, restoring a tab produces the exact state that was saved when switching away from it. Assert that no tab's messages array contains message IDs belonging to another tab's session.
- **Property 12 (Per-Tab Abort Controller Isolation)**: Generate random multi-tab scenarios with concurrent streams. Assert that aborting the active tab's controller does not affect any background tab's controller. Assert each tab's abort controller is a distinct instance.
- **Property 13 (Tab Limit Enforcement)**: Generate random sequences of tab open/close operations with tab counts ranging from 0 to 9. Assert that `handleNewSession` creates a new tab if and only if the current tab count is below `MAX_OPEN_TABS` (8). Assert that a toast is shown when creation is blocked. Assert that closing a tab at the limit re-enables creation.
- **Property 14 (Tab Status Indicator Correctness)**: Generate random sequences of stream events (`assistant`, `ask_user_question`, `cmd_permission_request`, `error`, `result`) across 1-6 tabs with random active/background states. Assert that after each event, the tab's status matches the expected value from the state machine (`idle → streaming → {waiting_input | permission_needed | error | complete_unread} → idle`).
- **Property 15 (Tab Status State Machine Transitions)**: Generate random tab status transition sequences. Assert that only valid transitions occur: `idle → streaming`, `streaming → waiting_input`, `streaming → permission_needed`, `streaming → error`, `streaming → complete_unread` (background only), `streaming → idle` (foreground result), `waiting_input → streaming` (answer submitted), `permission_needed → streaming` (permission approved), `complete_unread → idle` (tab switched to), `error → idle` (new message sent). Assert no invalid transitions (e.g., `idle → error`, `waiting_input → complete_unread`).

### Integration Tests

- Test full streaming flow: send message → receive `session_start` → receive `assistant` events with tool_use blocks → receive `result` → verify spinner labels, auto-scroll, and final state
- Test `ask_user_question` flow: stream → `ask_user_question` → unmount → remount → verify state restored → submit answer → verify new stream starts correctly
- Test `cmd_permission_request` flow: stream → `cmd_permission_request` → approve → new stream → verify old complete handler is no-op
- Test error flow: stream → error event → verify spinner stops, error visible, auto-scroll to error
- Test rapid tool invocations: stream with 20+ tool_use blocks in < 30 seconds → verify activity label stability and auto-scroll
- Test cross-session tab switch during streaming: Tab A streaming → switch to Tab B → switch back to Tab A → verify Tab A's messages are intact and streaming indicator is correct
- Test concurrent streams across tabs: Tab A streaming → switch to Tab B → start stream in Tab B → switch back to Tab A → verify both tabs' messages are independent and neither is corrupted
- Test stop button isolation: Tab A and Tab B both streaming → click stop while Tab A is active → verify Tab A's stream is aborted and Tab B's stream continues
- Test pendingQuestion isolation: Tab A receives `ask_user_question` → switch to Tab B → verify Tab B does not show question form → switch back to Tab A → verify question form is displayed
- Test tab close during streaming: Tab A streaming → close Tab A → verify Tab A's abort controller is aborted and its entry is removed from the per-tab state map
- Test tab limit enforcement: open 6 tabs → click "+" → verify no new tab created and toast displayed → close one tab → click "+" → verify new tab created successfully
- Test tab status during streaming: start stream on Tab A → switch to Tab B → verify Tab A's header shows pulsing blue dot → Tab A receives `ask_user_question` → verify Tab A's header shows orange "?" → switch to Tab A → verify question form displayed
- Test tab status on background completion: Tab A streaming → switch to Tab B → Tab A receives `result` event → verify Tab A's header shows static green dot → switch to Tab A → verify green dot cleared to idle
- Test tab status on error: Tab A streaming in background → Tab A receives error event → verify Tab A's header shows red "!" indicator
- Test tab status on permission request: Tab A streaming → `cmd_permission_request` received → verify Tab A's header shows yellow "⚠" → approve permission → verify Tab A's header returns to pulsing blue dot (streaming)
