# Bugfix Requirements Document

## Introduction

The chat streaming UX has interrelated lifecycle bugs that degrade the user experience during multi-turn agent conversations with tool use. These bugs manifest during long-running sessions (50+ tool invocations, 2-7 minutes) and around state transitions involving `ask_user_question` and `cmd_permission_request` events. The existing `chat-streaming-visibility` spec addressed the spinner label (showing tool names instead of static "Thinking..."), and `ContentBlockRenderer` already renders text, tool_use, and tool_result blocks inside the message bubble. However, the underlying state machine gaps, auto-scroll failures that hide rendered content, missing error visibility during streaming, shallow activity indicator context, and conversation state loss on re-mount remain unresolved.

The bugs are:
1. **isStreaming state machine lifecycle gaps** — The streaming state can briefly become `false` during active streaming due to sessionId transitions and double-clearing from concurrent `setIsStreaming(false)` calls, causing spinner disappear/reappear flicker.
2. **Auto-scroll fails to keep latest content visible** — The message bubble already renders tool_use blocks via `ContentBlockRenderer`, but during rapid tool invocations the latest content scrolls out of view. The user sees only the spinner at the bottom, missing the tool details that are already being rendered above it.
3. **Errors during streaming are not prominently visible** — Error events from the backend are handled in `createStreamHandler` and rendered as text, but during long streaming sessions the error may be appended to the message history above the viewport. The user, scrolled to the spinner at the bottom, never sees the error — or the spinner hangs on "Processing..." indefinitely.
4. **Activity indicator lacks operational context** — `deriveStreamingActivity` returns the latest tool name for the spinner label (e.g., "Running: Bash"), but does not surface what the tool is operating on. During rapid tool calls, the label flickers between tool names with no context about what each tool is doing.
5. **Conversation/question disappears on re-mount** — When an `AskUserQuestion` event ends the SSE stream and the React component re-mounts (tab switch, hot reload), in-memory messages and the pending question are lost because they aren't persisted until a `ResultMessage` completes the conversation.
6. **Cross-session state corruption during multi-tab usage** — The ChatPage component uses single-instance React state (`messages`, `sessionId`, `pendingQuestion`, `isStreaming`, `_pendingStream`, `abortRef`) shared across ALL tabs. When users open multiple chat sessions via the "+" new session button, these shared state variables create cross-session corruption: switching tabs during streaming overwrites messages, the shared `abortRef` causes the stop button to affect the wrong session, `_pendingStream` leaks across tabs causing false spinners, and `pendingQuestion` appears in the wrong tab's context.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN `sessionId` transitions from `undefined` to a real value (after `session_start` SSE event arrives) THEN the system briefly computes `isStreaming` as `false` because the new sessionId is not yet in `streamingSessions` and `_pendingStream` may have already been cleared by `setIsStreaming(true)` which wrote to the old (undefined) session entry

1.2 WHEN an `ask_user_question` event sets `setIsStreaming(false)` and the SSE reader's `createCompleteHandler` also calls `setIsStreaming(false)` THEN the system double-clears the streaming state, which can cause state inconsistencies if a new stream is started between the two calls (e.g., the user quickly submits an answer)

1.3 WHEN a `cmd_permission_request` event sets `setIsStreaming(false)` and the user approves the command, restarting streaming via `streamCmdPermissionContinue` THEN the system may have the `createCompleteHandler` from the original stream fire after the new stream starts, clearing the new stream's `isStreaming` state

1.4 WHEN the backend runs multiple tool invocations during streaming and new content blocks (tool_use, tool_result) are appended to the assistant message THEN the system does not auto-scroll to keep the latest content block visible in the viewport, causing the user to see only the spinner at the bottom while the actual tool details rendered by `ContentBlockRenderer` are scrolled out of view above

1.5 WHEN an error event occurs during a long streaming session and the error is rendered as text content in the message history THEN the system does not bring the error into view or visually distinguish it from normal content, so the user remains scrolled to the spinner at the bottom and sees "Processing..." indefinitely without realizing an error occurred

1.6 WHEN the backend invokes a tool during streaming THEN the activity indicator shows only the tool name (e.g., "Running: Bash") without any context about what the tool is operating on, even though the tool input contains actionable details like file paths, commands, or search queries

1.7 WHEN the backend runs 50+ tool invocations over 2-7 minutes with rapid tool call intervals (< 2 seconds apart) THEN the activity indicator label flickers between tool names with no stabilization, providing no useful progress indication

1.8 WHEN the backend sends an `ask_user_question` event and the SSE stream ends (backend generator completes) THEN the system stores the pending question and all conversation messages only in React `useState` (in-memory), not persisted to any durable store

1.9 WHEN the React component re-mounts after an `ask_user_question` event (due to tab switch, hot reload, or session change) THEN the system loses all in-memory messages and the pending question because `useState` is reset on mount, and the messages are not available via `getSessionMessages` since no `ResultMessage` was emitted

1.10 WHEN the user returns to a chat tab where an `ask_user_question` was pending THEN the system shows the welcome message or an empty chat instead of the conversation context and the question form, because `loadSessionMessages` only retrieves persisted messages

1.11 WHEN Tab A is streaming and the user switches to Tab B THEN the system overwrites the shared `messages` state with Tab B's data, and Tab A's `createStreamHandler` closure still calls `setMessages(prev => prev.map(...))` which tries to find `assistantMessageId` in Tab B's messages, corrupting both sessions' message arrays

1.12 WHEN Tab B starts a new stream while Tab A's stream is still active THEN the system overwrites the shared `abortRef.current` with Tab B's SSE abort controller, so Tab A's stream can no longer be stopped via the stop button — clicking stop aborts Tab B's stream instead

1.13 WHEN Tab A sets `_pendingStream = true` and the user switches to Tab B THEN the system evaluates `isStreaming` for Tab B as `true` (because `isStreaming = sessionId ? streamingSessions.has(sessionId) || _pendingStream : _pendingStream`) causing Tab B to display a spinner even though it is not streaming

1.14 WHEN Tab A has a pending `ask_user_question` and the user switches to Tab B THEN the system displays the pending question form in Tab B's context because `pendingQuestion` is a single shared `useState` not scoped to any tab

1.15 WHEN the user switches away from Tab A while it is streaming (before a `ResultMessage` is received) THEN the system calls `handleTabSelect` which overwrites `messages` with the target tab's data via `loadSessionMessages`, losing Tab A's in-progress conversation that existed only in React state

1.16 WHEN the user opens many tabs via the "+" button THEN the system allows unlimited tabs with no cap, leading to resource exhaustion (open SSE connections, memory for per-tab state maps, browser connection limits)

1.17 WHEN a background tab is streaming, has a pending question, encounters an error, or completes THEN the system provides no visual indication on the tab header — the user must switch to each tab to discover its state

### Expected Behavior (Correct)

2.1 WHEN `sessionId` transitions from `undefined` to a real value during active streaming THEN the system SHALL maintain `isStreaming` as `true` continuously by ensuring the `_pendingStream` flag remains set until the new sessionId is registered in `streamingSessions`, with no observable flicker or gap in the streaming indicator

2.2 WHEN an `ask_user_question` or `cmd_permission_request` event pauses streaming THEN the system SHALL use a single authoritative mechanism to transition `isStreaming` to `false`, preventing the SSE reader's `createCompleteHandler` from double-clearing the state

2.3 WHEN a `cmd_permission_request` is approved and a new stream starts THEN the system SHALL ensure the previous stream's `createCompleteHandler` cannot interfere with the new stream's `isStreaming` state (e.g., via a stream generation counter or cancellation token)

2.4 WHEN new content blocks (text, tool_use, tool_result) are appended to the assistant message during streaming THEN the system SHALL auto-scroll the chat viewport to keep the latest content block visible, so the user can see the tool details rendered by `ContentBlockRenderer` as they arrive, not just the spinner

2.5 WHEN an error event occurs during streaming THEN the system SHALL ensure the error is visible to the user by auto-scrolling to the error content and visually distinguishing it (e.g., error styling, prominent placement) so it is not buried above the viewport while the spinner continues below

2.6 WHEN the backend invokes a tool during streaming THEN the activity indicator SHALL display the tool name along with a brief description of what the tool is operating on, extracted from the tool input (e.g., "Running: Bash — ls -la /path", "Running: Read — src/components/Chat.tsx", "Running: Search — error handling pattern"), truncated to a reasonable length

2.7 WHEN the backend runs multiple tool invocations during a long session THEN the activity indicator SHALL display a stable, non-flickering label showing the current tool name with operational context and a cumulative count of tools invoked so far in the current turn (e.g., "Running: Bash — npm test (12 tools used)"), with a minimum display duration per label to prevent rapid flickering

2.8 WHEN the backend sends an `ask_user_question` event THEN the system SHALL persist the current conversation messages and pending question state so they survive component re-mounts

2.9 WHEN the React component re-mounts and a session has a pending `ask_user_question` THEN the system SHALL restore the conversation messages and re-display the question form so the user can continue the conversation

2.10 WHEN the user returns to a chat tab where an `ask_user_question` was pending THEN the system SHALL show the full conversation history and the pending question form, not a welcome message or empty chat

2.11 WHEN the user has multiple chat tabs open THEN the system SHALL maintain per-tab state isolation — each tab SHALL have its own `messages`, `sessionId`, `pendingQuestion`, `abortController`, and `_pendingStream` state, so that operations on one tab cannot read or write another tab's state

2.12 WHEN the user switches from Tab A to Tab B THEN the system SHALL save Tab A's current state (messages, sessionId, pendingQuestion, abortController, pendingStream) to a per-tab store and restore Tab B's state from the same store, without data loss in either direction

2.13 WHEN Tab A is streaming in the background (user switched to Tab B) THEN Tab A's `createStreamHandler` closure SHALL continue updating Tab A's state in the per-tab store without corrupting Tab B's foreground state

2.14 WHEN the user clicks the stop button THEN the system SHALL abort only the active (foreground) tab's stream via that tab's own abort controller, not any background tab's stream

2.15 WHEN the user switches back to a tab that was streaming in the background THEN the system SHALL restore that tab's messages (including any updates received while in the background) and streaming indicator, showing the current state of the background stream

2.16 WHEN the user has 6 tabs open and clicks "+" to create a new tab THEN the system SHALL display a toast notification "Maximum tabs reached. Close a tab to open a new one." and SHALL NOT create the new tab

2.17 WHEN the user closes a tab bringing the count below 6 THEN the system SHALL allow creating new tabs again

2.18 WHEN a tab is actively streaming THEN the tab header SHALL display a pulsing blue dot indicator

2.19 WHEN a tab has a pending `ask_user_question` THEN the tab header SHALL display an orange "?" indicator to signal the user's input is needed

2.20 WHEN a tab has a pending `cmd_permission_request` THEN the tab header SHALL display a yellow "⚠" indicator

2.21 WHEN a tab's stream encounters an error THEN the tab header SHALL display a red "!" indicator

2.22 WHEN a background tab's stream completes (ResultMessage received while tab is not active) THEN the tab header SHALL display a static green dot to indicate new unread content

2.23 WHEN the user switches to a tab with a "complete (unread)" indicator THEN the indicator SHALL be cleared (the content is now "read")

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the backend has not yet sent any `assistant` SSE event (initial API wait time) THEN the system SHALL CONTINUE TO display the "Thinking..." spinner as it does today

3.2 WHEN a simple single-turn query completes quickly with one assistant response THEN the system SHALL CONTINUE TO render the response in a single message bubble with no visual difference from current behavior

3.3 WHEN the backend streams an `ask_user_question` event THEN the system SHALL CONTINUE TO pause streaming and display the question form as it does today (in addition to now persisting the state)

3.4 WHEN the backend streams a `cmd_permission_request` event THEN the system SHALL CONTINUE TO pause streaming and display the permission modal as it does today

3.5 WHEN the backend streams a `result` event THEN the system SHALL CONTINUE TO finalize the conversation, stop streaming, and invalidate radar caches as it does today

3.6 WHEN the user clicks the stop button during streaming THEN the system SHALL CONTINUE TO abort the stream and display the stop confirmation message as it does today

3.7 WHEN `ContentBlockRenderer` receives text, tool_use, or tool_result content blocks THEN the system SHALL CONTINUE TO render them inside the message bubble as it does today — the fix does not change block rendering, only ensures they remain visible via auto-scroll

3.8 WHEN `ToolUseBlock` renders a tool invocation THEN the system SHALL CONTINUE TO show the tool name and collapsible input (including file paths) as it does today — the fix does not change tool block rendering

3.9 WHEN `getSessionMessages` is called for a completed session (one that received a `ResultMessage`) THEN the system SHALL CONTINUE TO return all persisted messages as it does today

3.10 WHEN error events are received from the backend in `createStreamHandler` THEN the system SHALL CONTINUE TO render them as text content in the message history as it does today — the fix adds visibility (auto-scroll, styling) but does not change error capture or rendering logic

3.11 WHEN only a single tab is open (no tab switching occurs) THEN the system SHALL CONTINUE TO behave identically to the current single-tab behavior — the per-tab state map introduces no observable difference for single-tab usage

3.12 WHEN a tab is closed THEN the system SHALL CONTINUE TO clean up its resources (abort any active stream, remove from tab list) as it does today, and additionally remove the tab's entry from the per-tab state store

3.13 WHEN fewer than 6 tabs are open THEN the "+" button SHALL CONTINUE TO create new tabs as it does today

3.14 WHEN a tab is idle (no streaming, no pending events) THEN the tab header SHALL display no status indicator, matching current behavior


## Bug Condition Derivation

### Bug 1: isStreaming State Machine Lifecycle Gaps

**Bug Condition Function:**

```pascal
FUNCTION isBugCondition_StreamingLifecycle(X)
  INPUT: X of type StreamingTransitionState
  OUTPUT: boolean
  
  // Returns true when a streaming state transition can cause isStreaming
  // to briefly become false while streaming is actually active
  RETURN (X.sessionId_before = undefined AND X.sessionId_after != undefined AND X.streamActive = true)
      OR (X.event_type = "ask_user_question" AND X.sseReaderCompleteHandlerPending = true)
      OR (X.event_type = "cmd_permission_request" AND X.newStreamStartedBeforeOldComplete = true)
END FUNCTION
```

**Property Specification — Fix Checking:**

```pascal
// Property: Fix Checking — Streaming state continuity during sessionId transition
FOR ALL X WHERE isBugCondition_StreamingLifecycle(X) AND X.sessionId_before = undefined DO
  states ← observeIsStreamingOverTime(F'(X))
  ASSERT NOT EXISTS t WHERE states[t] = false AND states[t-1] = true AND states[t+1] = true
  // No false dip in isStreaming during active streaming
END FOR
```

```pascal
// Property: Fix Checking — No double-clear interference
FOR ALL X WHERE isBugCondition_StreamingLifecycle(X) AND X.event_type IN {"ask_user_question", "cmd_permission_request"} DO
  // After pausing event, only one mechanism clears isStreaming
  clearCount ← countSetIsStreamingFalseCalls(F'(X))
  ASSERT clearCount = 1
END FOR
```

**Preservation Goal:**

```pascal
// Property: Preservation Checking — Normal streaming lifecycle unchanged
FOR ALL X WHERE NOT isBugCondition_StreamingLifecycle(X) DO
  ASSERT F(X).isStreaming = F'(X).isStreaming
  // For normal start/complete cycles, behavior is identical
END FOR
```

### Bug 2: Auto-Scroll Fails to Keep Latest Content Visible

**Bug Condition Function:**

```pascal
FUNCTION isBugCondition_AutoScroll(X)
  INPUT: X of type StreamingViewportState
  OUTPUT: boolean
  
  // Returns true when new content blocks are appended during streaming
  // but the viewport has not scrolled to show them
  RETURN X.isStreaming = true
     AND X.newContentBlockAppended = true
     AND X.latestContentBlockInViewport = false
END FUNCTION
```

**Property Specification — Fix Checking:**

```pascal
// Property: Fix Checking — Latest content block stays visible during streaming
FOR ALL X WHERE isBugCondition_AutoScroll(X) DO
  viewport ← renderChat'(X)
  // After a new content block is appended, the viewport scrolls to show it
  ASSERT viewport.latestContentBlock.isVisible = true
  // The user can see tool_use/tool_result blocks as they arrive, not just the spinner
END FOR
```

**Preservation Goal:**

```pascal
// Property: Preservation Checking — Manual scroll position respected when user scrolls up
FOR ALL X WHERE NOT isBugCondition_AutoScroll(X) AND X.userScrolledUp = true DO
  ASSERT viewport.scrollPosition = X.userScrollPosition
  // If the user manually scrolled up to review history, auto-scroll does not override
END FOR
```

### Bug 3: Errors During Streaming Are Not Prominently Visible

**Bug Condition Function:**

```pascal
FUNCTION isBugCondition_ErrorVisibility(X)
  INPUT: X of type StreamingErrorState
  OUTPUT: boolean
  
  // Returns true when an error occurs during streaming but the user
  // cannot see it because it's above the viewport
  RETURN X.isStreaming = true
     AND X.errorEventReceived = true
     AND X.errorContentInViewport = false
END FUNCTION
```

**Property Specification — Fix Checking:**

```pascal
// Property: Fix Checking — Error is visible to user after error event
FOR ALL X WHERE isBugCondition_ErrorVisibility(X) DO
  viewport ← renderChat'(X)
  ASSERT viewport.errorContent.isVisible = true
  ASSERT viewport.errorContent.isVisuallyDistinguished = true
  // Error is scrolled into view and styled distinctly from normal content
  ASSERT viewport.spinnerLabel != "Processing..."
  // Spinner does not continue showing "Processing..." after an error
END FOR
```

**Preservation Goal:**

```pascal
// Property: Preservation Checking — Error rendering logic unchanged
FOR ALL X WHERE NOT isBugCondition_ErrorVisibility(X) DO
  ASSERT createStreamHandler(X).errorRendering = createStreamHandler'(X).errorRendering
  // Error capture and text rendering in createStreamHandler is unchanged
END FOR
```

### Bug 4: Activity Indicator Lacks Operational Context

**Bug Condition Function:**

```pascal
FUNCTION isBugCondition_ActivityContext(X)
  INPUT: X of type StreamingToolState
  OUTPUT: boolean
  
  // Returns true when a tool is invoked during streaming but the
  // activity indicator shows only the tool name without operational context
  RETURN X.isStreaming = true
     AND X.currentToolUse != null
     AND X.currentToolUse.input != null
END FUNCTION
```

**Property Specification — Fix Checking:**

```pascal
// Property: Fix Checking — Activity indicator shows operational context
FOR ALL X WHERE isBugCondition_ActivityContext(X) DO
  label ← deriveStreamingActivity'(X)
  ASSERT label.toolName = X.currentToolUse.name
  ASSERT label.operationSummary != null
  // operationSummary is extracted from tool input (e.g., command, file path, query)
  ASSERT length(label.operationSummary) <= MAX_LABEL_LENGTH
  // Summary is truncated to prevent UI overflow
END FOR
```

```pascal
// Property: Fix Checking — Activity indicator is stable during rapid tool calls
FOR ALL X WHERE isBugCondition_ActivityContext(X) AND X.toolCallInterval < 2 seconds DO
  labels ← observeActivityLabelsOverTime(F'(X))
  FOR EACH label IN labels DO
    ASSERT label.displayDuration >= MIN_DISPLAY_DURATION
    // Each label is shown for a minimum duration to prevent flickering
  END FOR
  ASSERT labels.last.toolCount >= 1
  // Cumulative tool count is shown
END FOR
```

**Preservation Goal:**

```pascal
// Property: Preservation Checking — deriveStreamingActivity base behavior unchanged
FOR ALL X WHERE NOT isBugCondition_ActivityContext(X) DO
  ASSERT deriveStreamingActivity(X).toolName = deriveStreamingActivity'(X).toolName
  // For sessions without tool input context, the existing tool name behavior is preserved
END FOR
```

### Bug 5: Conversation/Question Disappears on Re-mount

**Bug Condition Function:**

```pascal
FUNCTION isBugCondition_ConversationLoss(X)
  INPUT: X of type SessionState
  OUTPUT: boolean
  
  // Returns true when a pending ask_user_question exists and the
  // component may re-mount, causing in-memory state loss
  RETURN X.pendingQuestion != null
     AND X.sseStreamEnded = true
     AND X.resultMessageReceived = false
END FUNCTION
```

**Property Specification — Fix Checking:**

```pascal
// Property: Fix Checking — Conversation survives re-mount
FOR ALL X WHERE isBugCondition_ConversationLoss(X) DO
  // Simulate component unmount + remount
  unmount(chatComponent)
  chatComponent' ← mount(ChatPage, { sessionId: X.sessionId })
  
  ASSERT chatComponent'.messages.length = X.messages.length
  ASSERT chatComponent'.pendingQuestion = X.pendingQuestion
  ASSERT chatComponent'.pendingQuestion.toolUseId = X.pendingQuestion.toolUseId
  // All messages and the pending question are restored after re-mount
END FOR
```

```pascal
// Property: Fix Checking — Tab switch preserves pending question
FOR ALL X WHERE isBugCondition_ConversationLoss(X) DO
  switchTab(otherTab)
  switchTab(X.tabId)
  
  ASSERT currentMessages().length = X.messages.length
  ASSERT currentPendingQuestion() = X.pendingQuestion
  // Switching away and back restores full conversation state
END FOR
```

**Preservation Goal:**

```pascal
// Property: Preservation Checking — Completed sessions load from API
FOR ALL X WHERE NOT isBugCondition_ConversationLoss(X) AND X.resultMessageReceived = true DO
  ASSERT loadSessionMessages(X.sessionId) = loadSessionMessages'(X.sessionId)
  // Completed sessions continue to load from the API as before
END FOR
```

### Bug 6: Cross-Session State Corruption During Multi-Tab Usage

**Bug Condition Function:**

```pascal
FUNCTION isBugCondition_CrossSessionState(X)
  INPUT: X of type MultiTabState
  OUTPUT: boolean
  
  // Returns true when multiple tabs exist and a tab switch or
  // concurrent stream operation can corrupt shared state
  RETURN X.openTabCount > 1
     AND (X.tabSwitchOccurred = true OR X.concurrentStreamsActive = true)
END FUNCTION
```

**Property Specification — Fix Checking:**

```pascal
// Property: Fix Checking — Per-tab message isolation
FOR ALL X WHERE isBugCondition_CrossSessionState(X) AND X.tabSwitchOccurred = true DO
  // Save Tab A state, switch to Tab B, switch back to Tab A
  tabAStateBeforeSwitch ← getTabState(X.tabA.id)
  switchTab(X.tabB.id)
  switchTab(X.tabA.id)
  tabAStateAfterSwitch ← getTabState(X.tabA.id)
  
  ASSERT tabAStateAfterSwitch.messages = tabAStateBeforeSwitch.messages
  ASSERT tabAStateAfterSwitch.sessionId = tabAStateBeforeSwitch.sessionId
  ASSERT tabAStateAfterSwitch.pendingQuestion = tabAStateBeforeSwitch.pendingQuestion
  // Tab A's state is fully preserved through tab switches
END FOR
```

```pascal
// Property: Fix Checking — Background streaming does not corrupt foreground
FOR ALL X WHERE isBugCondition_CrossSessionState(X) AND X.concurrentStreamsActive = true DO
  tabBMessages ← getTabState(X.tabB.id).messages
  // Tab A streams in background, Tab B is foreground
  simulateBackgroundStreamEvent(X.tabA.id, newContentBlock)
  tabBMessagesAfter ← getTabState(X.tabB.id).messages
  
  ASSERT tabBMessagesAfter = tabBMessages
  // Tab B's messages are unchanged by Tab A's background streaming
END FOR
```

```pascal
// Property: Fix Checking — Abort controller is per-tab
FOR ALL X WHERE isBugCondition_CrossSessionState(X) AND X.concurrentStreamsActive = true DO
  abortA ← getTabState(X.tabA.id).abortController
  abortB ← getTabState(X.tabB.id).abortController
  
  ASSERT abortA != abortB
  // Each tab has its own abort controller
  
  // Clicking stop on foreground tab (B) does not abort Tab A
  triggerStop()
  ASSERT abortB.signal.aborted = true
  ASSERT abortA.signal.aborted = false
END FOR
```

```pascal
// Property: Fix Checking — _pendingStream does not leak across tabs
FOR ALL X WHERE isBugCondition_CrossSessionState(X) DO
  setTabPendingStream(X.tabA.id, true)
  switchTab(X.tabB.id)
  
  ASSERT getTabState(X.tabB.id).pendingStream = false
  // Tab B's pendingStream is independent of Tab A's
  ASSERT isStreaming(X.tabB.id) = streamingSessions.has(X.tabB.sessionId)
  // Tab B's isStreaming does not include Tab A's pendingStream
END FOR
```

**Preservation Goal:**

```pascal
// Property: Preservation Checking — Single-tab behavior unchanged
FOR ALL X WHERE NOT isBugCondition_CrossSessionState(X) AND X.openTabCount = 1 DO
  ASSERT F(X) = F'(X)
  // Single-tab usage produces identical behavior to the original code
END FOR
```

```pascal
// Property: Preservation Checking — Tab close cleanup unchanged
FOR ALL X WHERE NOT isBugCondition_CrossSessionState(X) AND X.tabCloseEvent = true DO
  ASSERT tabCleanup(X) = tabCleanup'(X)
  // Closing a tab cleans up resources identically, plus removes per-tab state entry
END FOR
```

### Bug 7: Unlimited Tab Creation Causes Resource Exhaustion

**Bug Condition Function:**

```pascal
FUNCTION isBugCondition_TabLimit(X)
  INPUT: X of type TabCreationEvent
  OUTPUT: boolean
  
  // Returns true when the user attempts to create a new tab
  // while the maximum number of tabs is already open
  RETURN X.openTabCount >= MAX_OPEN_TABS AND X.createTabRequested = true
END FUNCTION
```

**Property Specification — Fix Checking:**

```pascal
// Property: Fix Checking — Tab creation blocked at limit
FOR ALL X WHERE isBugCondition_TabLimit(X) DO
  result ← handleNewSession'(X)
  ASSERT result.tabCreated = false
  ASSERT result.toastDisplayed = true
  ASSERT result.toastMessage = "Maximum tabs reached. Close a tab to open a new one."
  ASSERT openTabCount(F'(X)) = MAX_OPEN_TABS
  // No new tab is created and a toast notification is shown
END FOR
```

```pascal
// Property: Fix Checking — Tab creation re-enabled after close
FOR ALL X WHERE X.openTabCount = MAX_OPEN_TABS AND X.tabCloseEvent = true DO
  closeTab(X.tabToClose)
  ASSERT openTabCount(F'(X)) = MAX_OPEN_TABS - 1
  result ← handleNewSession'(X)
  ASSERT result.tabCreated = true
  // After closing a tab, new tabs can be created again
END FOR
```

**Preservation Goal:**

```pascal
// Property: Preservation Checking — Tab creation unchanged below limit
FOR ALL X WHERE NOT isBugCondition_TabLimit(X) AND X.openTabCount < MAX_OPEN_TABS DO
  ASSERT handleNewSession(X).tabCreated = handleNewSession'(X).tabCreated
  // Below the limit, tab creation behaves identically to the original code
END FOR
```

### Bug 8: No Visual Status Indication on Tab Headers

**Bug Condition Function:**

```pascal
FUNCTION isBugCondition_TabStatusVisibility(X)
  INPUT: X of type TabHeaderState
  OUTPUT: boolean
  
  // Returns true when a tab has a meaningful status that should be
  // visually indicated but the tab header shows no indicator
  RETURN (X.tabIsStreaming = true
      OR X.tabHasPendingQuestion = true
      OR X.tabHasPendingPermission = true
      OR X.tabHasError = true
      OR (X.tabStreamCompleted = true AND X.tabIsBackground = true))
     AND X.tabHeaderIndicator = none
END FUNCTION
```

**Property Specification — Fix Checking:**

```pascal
// Property: Fix Checking — Streaming tab shows pulsing blue dot
FOR ALL X WHERE isBugCondition_TabStatusVisibility(X) AND X.tabIsStreaming = true DO
  indicator ← getTabHeaderIndicator'(X.tabId)
  ASSERT indicator.type = "pulsing_dot"
  ASSERT indicator.color = "blue"
END FOR
```

```pascal
// Property: Fix Checking — Pending question tab shows orange "?"
FOR ALL X WHERE isBugCondition_TabStatusVisibility(X) AND X.tabHasPendingQuestion = true DO
  indicator ← getTabHeaderIndicator'(X.tabId)
  ASSERT indicator.type = "text"
  ASSERT indicator.symbol = "?"
  ASSERT indicator.color = "orange"
END FOR
```

```pascal
// Property: Fix Checking — Pending permission tab shows yellow "⚠"
FOR ALL X WHERE isBugCondition_TabStatusVisibility(X) AND X.tabHasPendingPermission = true DO
  indicator ← getTabHeaderIndicator'(X.tabId)
  ASSERT indicator.type = "text"
  ASSERT indicator.symbol = "⚠"
  ASSERT indicator.color = "yellow"
END FOR
```

```pascal
// Property: Fix Checking — Error tab shows red "!"
FOR ALL X WHERE isBugCondition_TabStatusVisibility(X) AND X.tabHasError = true DO
  indicator ← getTabHeaderIndicator'(X.tabId)
  ASSERT indicator.type = "text"
  ASSERT indicator.symbol = "!"
  ASSERT indicator.color = "red"
END FOR
```

```pascal
// Property: Fix Checking — Background-completed tab shows static green dot
FOR ALL X WHERE isBugCondition_TabStatusVisibility(X) AND X.tabStreamCompleted = true AND X.tabIsBackground = true DO
  indicator ← getTabHeaderIndicator'(X.tabId)
  ASSERT indicator.type = "static_dot"
  ASSERT indicator.color = "green"
END FOR
```

```pascal
// Property: Fix Checking — Switching to unread tab clears indicator
FOR ALL X WHERE X.tabStatus = "complete_unread" DO
  switchTab(X.tabId)
  ASSERT getTabStatus'(X.tabId) = "idle"
  ASSERT getTabHeaderIndicator'(X.tabId) = none
  // Content is now "read", indicator is cleared
END FOR
```

**Preservation Goal:**

```pascal
// Property: Preservation Checking — Idle tabs show no indicator
FOR ALL X WHERE NOT isBugCondition_TabStatusVisibility(X) AND X.tabStatus = "idle" DO
  ASSERT getTabHeaderIndicator(X.tabId) = getTabHeaderIndicator'(X.tabId)
  // Idle tabs continue to display no status indicator
END FOR
```
