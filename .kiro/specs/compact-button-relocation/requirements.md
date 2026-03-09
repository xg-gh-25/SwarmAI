# Requirements Document

## Introduction

The "Compact Context" button currently lives in `ChatHeader.tsx` as a global header action. This creates two UX problems: (1) the button is spatially disconnected from the conversation it acts on, and (2) in a multi-tab environment, users cannot tell which session the compact targets — the same discoverability problem that was already solved for Save-to-Memory by relocating it to the per-message action row.

This feature relocates the Compact Context button from the header to the last assistant message's action row (after the Save-to-Memory button), with a key behavioral change: the button is invisible by default and only appears when the backend context monitor emits a warning (70%+ context usage). After successful compaction, the backend emits a `context_compacted` event (level: `ok`), the warning clears, and the button disappears again.

The backend API (`POST /chat/compact/{session_id}`) and auto-compact flow (PreCompact hook) remain unchanged.

## Glossary

- **Compact_Button**: The UI button that triggers manual context window compaction for the active session
- **Context_Warning**: A state object (`ContextWarning`) emitted by the backend via SSE `context_warning` events when context usage exceeds 70% (warn) or 85% (critical), containing `level`, `pct`, `tokensEst`, and `message` fields. Stored as a field on `UnifiedTab` in `tabMapRef` (per multi-tab isolation Principle 1), with a React `useState` display mirror for the active tab
- **ChatHeader**: Component in `desktop/src/pages/chat/components/ChatHeader.tsx` that renders session tabs and right-side action buttons
- **AssistantMessageView**: Component in `desktop/src/pages/chat/components/AssistantMessageView.tsx` that renders assistant messages with branded layout, content blocks, and the hover-to-reveal action row
- **MessageBubble**: Thin dispatcher in `desktop/src/pages/chat/components/MessageBubble.tsx` that routes to `UserMessageView` or `AssistantMessageView` by role
- **ChatPage**: Top-level chat component that owns `contextWarning` display mirror state from `useChatStreamingLifecycle` and threads it as a prop to `MessageBubble`. The display mirror reflects the active tab's `UnifiedTab.contextWarning` via the save/restore/re-derive tab switch protocol
- **Action_Row**: The hover-to-reveal row below assistant message content containing Copy and Save-to-Memory buttons, styled with `opacity-0 group-hover/msg:opacity-100`
- **Context_Monitor**: Backend system that emits `context_warning` SSE events on the first user turn and every 5 turns thereafter at all levels (ok, warn, critical), and `context_compacted` events after compaction

## Requirements

### Requirement 1: Remove Compact Button from ChatHeader

**User Story:** As a SwarmAI user, I want the Compact Context button removed from the global header, so that the header is not cluttered with a session-specific action that belongs closer to the conversation content.

#### Acceptance Criteria

1. THE ChatHeader SHALL NOT render a Compact Context button
2. THE ChatHeader SHALL NOT maintain compact-related state (`compactStatus`, `compactToast`)
3. THE ChatHeader SHALL continue to render the New Session (+), ToDo Radar, Chat History, and File Browser buttons with identical behavior
4. WHEN the Compact_Button is removed from ChatHeader, THE ChatHeader SHALL NOT import or reference `chatService.compactSession`

### Requirement 2: Add Compact Button to Last Assistant Message Action Row

**User Story:** As a SwarmAI user, I want the Compact Context button to appear in the last assistant message's action row (after Save-to-Memory), so that the compaction action is contextually located near the conversation content it affects.

#### Acceptance Criteria

1. WHEN a Context_Warning with level `warn` or `critical` is active AND the assistant message is the last assistant message in the session AND the message is not streaming, THE AssistantMessageView SHALL render the Compact_Button in the Action_Row after the Save-to-Memory button
2. WHEN no Context_Warning is active (level is `ok` or null), THE AssistantMessageView SHALL NOT render the Compact_Button regardless of message position
3. WHEN the Compact_Button is visible and the user clicks the Compact_Button, THE AssistantMessageView SHALL call `chatService.compactSession(sessionId)` for the current session
4. WHILE the compact API call is in progress, THE Compact_Button SHALL display a loading spinner icon (`progress_activity` with `animate-spin`) and be disabled
5. WHEN the compact API call succeeds, THE Compact_Button SHALL display a success icon (`check_circle`) with green styling for 3 seconds before returning to idle state
6. IF the compact API call fails, THEN THE AssistantMessageView SHALL display a Toast with the error message
7. WHEN the compact API call succeeds and the backend emits a `context_compacted` event (level: `ok`), THE Context_Warning SHALL clear and THE Compact_Button SHALL disappear from the Action_Row
8. THE Compact_Button SHALL follow the same hover-to-reveal pattern (`opacity-0 group-hover/msg:opacity-100`) as the Copy and Save-to-Memory buttons

### Requirement 3: Thread Context Warning State to AssistantMessageView

**User Story:** As a SwarmAI developer, I want the `contextWarning` state from `useChatStreamingLifecycle` threaded through ChatPage → MessageBubble → AssistantMessageView, so that the Compact_Button can conditionally render based on context usage level.

#### Acceptance Criteria

1. THE ChatPage SHALL pass the `contextWarning` object as a prop to MessageBubble for each message
2. THE MessageBubble SHALL accept a `contextWarning` prop and forward the prop to AssistantMessageView
3. THE AssistantMessageView SHALL accept a `contextWarning` prop of type `ContextWarning | null`
4. WHEN `contextWarning` is null or `contextWarning.level` is `ok`, THE AssistantMessageView SHALL treat the compact button as not visible
5. WHEN `contextWarning.level` is `warn` or `critical`, THE AssistantMessageView SHALL treat the compact button as visible (subject to `isLastAssistant` and `isStreaming` conditions from Requirement 2)

### Requirement 4: Compact Button Styling and Accessibility

**User Story:** As a SwarmAI user, I want the Compact Context button to be visually consistent with the existing action row buttons and accessible via screen readers, so that the UI remains cohesive and inclusive.

#### Acceptance Criteria

1. THE Compact_Button SHALL use the same text size (`text-xs`), padding (`px-2 py-0.5`), and transition classes as the Copy and Save-to-Memory buttons in the Action_Row
2. THE Compact_Button SHALL display the `compress` Material Symbol icon in idle state
3. WHEN the Context_Warning level is `critical`, THE Compact_Button text and icon SHALL use a red/amber color to indicate urgency
4. WHEN the Context_Warning level is `warn`, THE Compact_Button text and icon SHALL use the standard muted color matching Copy and Save-to-Memory
5. THE Compact_Button SHALL have an `aria-label` of "Compact Context"
6. THE Compact_Button SHALL have a `title` attribute showing the context usage percentage (e.g., "Compact Context (72% used)")

### Requirement 5: Preservation of Existing Behaviors

**User Story:** As a SwarmAI user, I want all existing chat behaviors to remain unchanged after the Compact_Button relocation, so that the feature change does not introduce regressions.

#### Acceptance Criteria

1. THE Copy button on every assistant message SHALL continue to appear on hover and copy text to clipboard with "Copied!" feedback
2. THE Save-to-Memory button on the last assistant message SHALL continue to function identically with per-session status tracking
3. WHILE an assistant message is streaming, THE Action_Row (Copy, Save-to-Memory, and Compact_Button) SHALL remain hidden
4. THE Context_Warning Toast notification in ChatPage SHALL continue to display independently of the Compact_Button visibility
5. THE backend auto-compact flow (PreCompact hook) SHALL remain unchanged — auto-compaction events SHALL continue to emit `context_compacted` SSE events that clear the Context_Warning state
6. THE backend `POST /chat/compact/{session_id}` API contract SHALL remain unchanged
7. WHEN the user is not hovering over the last assistant message, THE Action_Row (including the Compact_Button when visible) SHALL be hidden via the `opacity-0` class

### Requirement 6: Per-Session Context Warning Isolation

**User Story:** As a SwarmAI user with multiple tabs/sessions open, I want context warnings to be scoped to the session that triggered them, so that a warning in one session does not cause the Compact button to appear in a different session's tab.

#### Acceptance Criteria

1. THE `contextWarning` state SHALL be stored as a field on `UnifiedTab` in `tabMapRef` (per multi-tab isolation Principle 1), not as a single global `useState` or a shared `Record` map
2. WHEN the backend emits a `context_warning` SSE event during a stream for tab T1, THE stream handler SHALL write the warning to `tabMapRef.get(capturedTabId).contextWarning` using the closure-captured `capturedTabId` (per Principle 3), and SHALL NOT use `sessionIdRef.current`
3. WHEN the backend emits a `context_compacted` SSE event during a stream for tab T1, THE stream handler SHALL clear ONLY `tabMapRef.get(capturedTabId).contextWarning` — other tabs' warnings SHALL remain unchanged
4. WHEN ChatPage threads `contextWarning` to MessageBubble, IT SHALL use the display mirror `contextWarning` from `useChatStreamingLifecycle` (which already reflects the active tab's `UnifiedTab.contextWarning` via the save/restore/re-derive tab switch protocol)
5. WHEN the user switches from Tab A (session S1, with active warning) to Tab B (session S2, no warning), THE Compact_Button SHALL NOT appear on Tab B's last assistant message
6. WHEN the user switches from Tab B (no warning) back to Tab A (session S1, with active warning), THE Compact_Button SHALL reappear on Tab A's last assistant message with the correct `pct` and `level`
7. WHEN the user manually compacts session S1 via the Compact_Button, THE compact API call SHALL target session S1's ID and SHALL NOT affect session S2's warning state

### Requirement 7: Context Usage Ring Indicator

**User Story:** As a SwarmAI user, I want to see a persistent context usage ring indicator near the chat input (after the TSCC icon), so that I can monitor how much context window capacity has been used without waiting for a warning.

#### Acceptance Criteria

1. THE ChatInput bottom row SHALL render a Context_Usage_Ring after the TSCC popover button, showing a circular SVG progress ring representing context window usage percentage
2. WHEN no context usage data is available (fresh session, no context checks yet), THE Context_Usage_Ring SHALL display as a subtle gray empty ring (0% fill)
3. WHEN context usage data is available, THE Context_Usage_Ring fill SHALL be proportional to the `pct` value with color coding: green (0–69%), amber (70–84%), red (85–100%)
4. WHEN the user hovers over the Context_Usage_Ring, A tooltip SHALL display "X% context used" where X is the current percentage
5. THE Context_Usage_Ring SHALL be approximately 18–20px in diameter to match the visual weight of the TSCC icon button
6. THE Context_Usage_Ring SHALL read its data from the active tab's `UnifiedTab.contextWarning` (per-session isolated, same data source as the Compact button)
7. WHEN the user switches tabs, THE Context_Usage_Ring SHALL update to reflect the new active tab's context usage (or reset to gray empty ring if no data)
8. THE backend SHALL emit `context_status` SSE events at ALL levels (ok, warn, critical) on the first user turn AND every 5 turns thereafter (lowered from 15), so the ring has data immediately and stays responsive for short sessions
9. THE `context_status` SSE event SHALL use the existing `context_warning` event type with the same shape: `{ type: 'context_warning', level, pct, tokensEst, message }` — the frontend handler already stores all levels on `UnifiedTab.contextWarning`
