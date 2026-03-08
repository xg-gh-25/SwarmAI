# Bugfix Requirements Document

## Introduction

The "Save to Memory" button is incorrectly placed in the global ChatHeader component, causing two categories of bugs: (1) the button is in the wrong location — users expect it near the copy button after each assistant response, and (2) the single `useMemorySave` hook instance in ChatHeader creates cross-session state leaks where visual status (saved/error/loading) and incremental save indices bleed across tab switches. The fix relocates the button to `AssistantMessageView` (last assistant message only) and ensures session-scoped state isolation.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the user looks for the Save-to-Memory button THEN the system displays it in the global header bar instead of near the assistant message content, making it disconnected from the conversation flow

1.2 WHEN the user saves memory in Tab A (session S1) and then switches to Tab B (session S2) THEN the system continues to display the "saved" checkmark status from session S1 on the header button, because the `useMemorySave` hook holds a single `status` state that is not reset on tab/session change

1.3 WHEN the user saves memory in Tab A (session S1, advancing `nextMessageIdxRef` for S1), switches to Tab B (session S2), and clicks save again THEN the system correctly looks up S2's index in the ref map, but the visual status (saved/loading/error) still reflects the previous session's result until the new API call completes — there is no reset to `idle` on session change

1.4 WHEN the user clicks the Save-to-Memory button while no session is active (e.g., on a fresh "New Session" tab) THEN the system shows a disabled button in the header but provides no contextual feedback about why saving is unavailable, since the button is detached from message context

### Expected Behavior (Correct)

2.1 WHEN the user views an assistant message that is the last assistant message in the session and the message is not currently streaming THEN the system SHALL display a Save-to-Memory button next to the existing Copy button, following the same hover-to-reveal pattern (`opacity-0` → `opacity-100` on `group-hover/msg`)

2.2 WHEN the user saves memory for a session via the per-message button THEN the system SHALL track save status (idle/loading/saved/empty/error) scoped to that specific session, so switching tabs does not carry over stale status from another session

2.3 WHEN the user switches from Tab A to Tab B THEN the system SHALL display the correct save status for Tab B's session (idle if never saved, saved if previously saved in this hook instance), not Tab A's status

2.4 WHEN the user clicks the Save-to-Memory button on the last assistant message THEN the system SHALL save the entire session up to that point (using the correct `since_message_idx` for that session) and display a Toast notification with the result

2.5 WHEN the Save-to-Memory button is removed from ChatHeader THEN the system SHALL no longer render any memory-save button or related state/imports in the header component

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the user hovers over an assistant message that is NOT the last assistant message THEN the system SHALL CONTINUE TO show only the Copy button (no Save-to-Memory button)

3.2 WHEN the user clicks the Copy button on any assistant message THEN the system SHALL CONTINUE TO copy the message text to clipboard and show the "Copied!" feedback

3.3 WHEN the user clicks Save-to-Memory and the backend returns a successful save THEN the system SHALL CONTINUE TO display a Toast notification with the formatted save summary (decisions, lessons, threads, context counts)

3.4 WHEN the user clicks Save-to-Memory and the backend returns "empty" (nothing new to save) THEN the system SHALL CONTINUE TO display a Toast notification indicating nothing new was saved

3.5 WHEN the assistant message is still streaming THEN the system SHALL CONTINUE TO hide all action buttons (Copy and Save-to-Memory) until streaming completes

3.6 WHEN the Compact Context button is in the header THEN the system SHALL CONTINUE TO function as before — only the Save-to-Memory button is relocated; all other header buttons remain unchanged
