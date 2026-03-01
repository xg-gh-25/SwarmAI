# Bugfix Requirements Document

## Introduction

When multiple chat tabs are open and running concurrent conversations, streaming state bleeds between tabs. The root cause is that `useChatStreamingLifecycle` manages a single set of React state variables (`isStreaming`, `_pendingStream`, `sessionId`, `messages`) that are shared across all tabs. This causes:

- The "processing..." / "running..." spinner to appear or disappear incorrectly on tabs that aren't actively streaming
- One tab completing its stream to kill another tab's pending/streaming indicator
- The `handleSendMessage` guard (`isStreamingRef.current`) to block message sending on idle tabs when any other tab is streaming
- Tab switching during active streaming to corrupt the displayed messages and streaming state of both the source and target tabs

The bug is entirely in the frontend. The backend correctly handles concurrent sessions with independent async generators per `/api/chat/stream` request.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN Tab A is streaming and Tab B is idle THEN the system blocks message sending on Tab B because `isStreamingRef.current` reflects a global `isStreaming` derived from the shared `_pendingStream` boolean and single `sessionId`

1.2 WHEN Tab A starts streaming (setting `_pendingStream = true`) and Tab B also starts streaming THEN when Tab A completes and sets `_pendingStream = false`, Tab B's pending indicator is killed because `_pendingStream` is a single shared boolean

1.3 WHEN the user switches from a streaming Tab A to an idle Tab B THEN `handleTabSelect` calls `setIsStreaming(tabState.isStreaming)` which sets `_pendingStream = false` globally, potentially clearing Tab A's streaming state in `streamingSessions` derivation

1.4 WHEN multiple tabs are streaming concurrently THEN the shared `messages` React state (`useState` in `useChatStreamingLifecycle`) receives interleaved `setMessages` calls from different stream handlers, causing message corruption or loss for background tabs

1.5 WHEN Tab A is streaming and the user switches to Tab B THEN `isStreaming` re-derives as `streamingSessions.has(sessionId)` where `sessionId` is now Tab B's session, so Tab A's streaming status is no longer reflected in the derived `isStreaming` value

1.6 WHEN a tab's stream starts but the `session_start` SSE event has not yet arrived (no sessionId assigned) THEN the tab relies on the shared `_pendingStream` boolean, and any other tab toggling `_pendingStream` during this window corrupts the first tab's pending state

### Expected Behavior (Correct)

2.1 WHEN Tab A is streaming and Tab B is idle THEN the system SHALL allow message sending on Tab B because the streaming guard SHALL check only the active tab's streaming state, not a global flag

2.2 WHEN Tab A completes streaming THEN the system SHALL only clear Tab A's streaming/pending state, leaving Tab B's streaming/pending state unaffected

2.3 WHEN the user switches from a streaming Tab A to an idle Tab B THEN the system SHALL preserve Tab A's streaming state in the per-tab map and restore Tab B's idle state to the React state, without modifying Tab A's streaming indicators

2.4 WHEN multiple tabs are streaming concurrently THEN each tab's stream handler SHALL write messages only to its own per-tab state in the tab map, and only the active tab's messages SHALL be reflected in the shared React `messages` state for rendering

2.5 WHEN the user switches tabs THEN `isStreaming` SHALL derive from the newly active tab's per-tab streaming state, and background tabs SHALL continue tracking their own streaming state independently in the tab map

2.6 WHEN a tab's stream starts but `session_start` has not yet arrived THEN the pending state SHALL be tracked per-tab (e.g., keyed by tabId) so that other tabs toggling their own pending state cannot corrupt it

1.7 WHEN the user clicks "Stop" on Tab B after switching from streaming Tab A THEN `handleStop` uses the shared `sessionId` React state which may still reflect Tab A's session, causing the stop request to target the wrong backend session

1.8 WHEN the user answers a question or makes a permission decision after switching tabs THEN `handleAnswerQuestion` and `handlePermissionDecision` use the shared `sessionId` state, which may reference the wrong session after a tab switch

1.9 WHEN a `cmd_permission_acknowledged` event arrives in `handlePermissionDecision` THEN `setIsStreaming(false)` is called without a tabId, clearing global streaming state instead of only the originating tab's state

1.10 WHEN multiple tabs start streaming concurrently THEN each tab sets `abortRef.current = abort` in ChatPage, overwriting the previous tab's abort function — only the last tab's abort is reachable via the shared ref

1.11 WHEN a tab's conversation completes (backend sends `result` SSE event) THEN the `result` event handler does NOT call `setIsStreaming(false)`, leaving the tab in "processing..." state indefinitely — the system relies on the `createCompleteHandler` (fired by SSE `[DONE]`) which has a generation guard that can silently no-op

### Expected Behavior (Correct) — Additional

2.7 WHEN the user clicks "Stop" on a tab THEN the stop request SHALL use the active tab's sessionId from the per-tab map, not the shared React state, ensuring the correct backend session is stopped

2.8 WHEN the user answers a question or makes a permission decision THEN the handler SHALL use the active tab's sessionId from the per-tab map to ensure the correct backend session receives the response

2.9 WHEN a `cmd_permission_acknowledged` event arrives THEN `setIsStreaming(false)` SHALL be called with the originating tab's tabId so only that tab's streaming state is cleared

2.10 WHEN multiple tabs start streaming THEN each tab's abort function SHALL be stored only in its per-tab map entry, and the shared `abortRef` SHALL be removed as dead code

2.11 WHEN a tab's conversation completes (`result` SSE event) THEN the `result` event handler SHALL call `setIsStreaming(false, capturedTabId)` and `incrementStreamGen()` to clear the tab's streaming state immediately, without relying on the `createCompleteHandler` backstop

### Unchanged Behavior (Regression Prevention)

3.1 WHEN only a single tab is open and streaming THEN the system SHALL CONTINUE TO show the "processing..." / "running..." spinner, stream messages correctly, and allow the user to abort the stream

3.2 WHEN a tab completes streaming in a single-tab scenario THEN the system SHALL CONTINUE TO clear the streaming indicator, finalize messages, and enable the input for new messages

3.3 WHEN the user opens a new tab THEN the system SHALL CONTINUE TO initialize it with a welcome message and idle state

3.4 WHEN the user closes a tab THEN the system SHALL CONTINUE TO clean up that tab's state (abort controller, map entry) and switch to an adjacent tab

3.5 WHEN the backend sends SSE events (session_start, content_block_delta, result, error, etc.) THEN the system SHALL CONTINUE TO process them correctly for the originating tab's stream handler

3.6 WHEN the user scrolls up during streaming THEN the system SHALL CONTINUE TO suppress auto-scroll until the user scrolls back to the bottom

3.7 WHEN a tab receives an `ask_user_question` or `cmd_permission_request` event THEN the system SHALL CONTINUE TO pause streaming and display the appropriate prompt for that tab

3.8 WHEN tab status indicators (idle, streaming, complete_unread) are displayed in the tab bar THEN the system SHALL CONTINUE TO reflect accurate per-tab status, now correctly isolated per tab
