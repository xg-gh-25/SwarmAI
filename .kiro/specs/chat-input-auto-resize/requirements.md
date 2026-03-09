# Requirements Document

## Introduction

The ChatInput component currently uses a `<textarea>` with `rows={2}` and an auto-grow mechanism capped at `MAX_ROWS = 20`. While the height-clamping logic works correctly (validated by existing property tests), the user experience for long text input is poor: the textarea starts at only 2–3 visible lines, and once content exceeds the max height the scrollable area is small relative to the overall chat layout. Users who paste or type multi-paragraph prompts cannot comfortably review and edit their text before sending.

This feature improves the ChatInput to provide a better editing experience for long-form text, including a larger expandable view, smooth auto-resize transitions, and keyboard-accessible controls for toggling between compact and expanded modes.

## Glossary

- **ChatInput**: The React component (`ChatInput.tsx`) that renders the message textarea, file attachment controls, slash-command suggestions, and send/stop button.
- **Textarea**: The `<textarea>` HTML element inside ChatInput used for text entry.
- **Compact_Mode**: The default state where the Textarea auto-grows from a minimum height up to a configurable maximum row count within the chat layout.
- **Expanded_Mode**: An alternate state where the Textarea occupies a larger portion of the viewport, giving the user more visible lines for reviewing and editing long text.
- **Max_Rows**: The maximum number of text lines the Textarea displays before enabling internal scrolling (currently 20).
- **Auto_Grow**: The behavior where the Textarea height increases automatically as the user types or pastes content, up to the Max_Rows limit.
- **Viewport**: The visible area of the application window.

## Requirements

### Requirement 1: Smooth Auto-Grow from Minimum to Maximum Height

**User Story:** As a user, I want the chat input to grow smoothly as I type or paste text, so that I can see my content without abrupt layout jumps.

#### Acceptance Criteria

1. WHEN the user types or pastes text into the Textarea, THE ChatInput SHALL increase the Textarea height to match the content height, up to the Max_Rows limit.
2. WHILE the Textarea content height is less than or equal to the Max_Rows limit, THE ChatInput SHALL display all content without internal scrolling.
3. WHILE the Textarea content height exceeds the Max_Rows limit, THE ChatInput SHALL cap the Textarea height at Max_Rows and enable vertical scrolling within the Textarea.
4. WHEN the Textarea height changes due to Auto_Grow, THE ChatInput SHALL animate the height change smoothly over a duration no longer than 150ms ONLY during mode toggles (compact↔expanded) and send-reset. During normal typing and pasting, height changes SHALL be applied immediately without animation to avoid visual flicker caused by the scrollHeight measurement reset.
5. THE ChatInput SHALL set the minimum Textarea height to 2 rows of text.

### Requirement 2: Expanded Mode for Long Text Editing

**User Story:** As a user, I want to expand the chat input to a larger editing area when composing long messages, so that I can review and edit multi-paragraph text comfortably.

#### Acceptance Criteria

1. THE ChatInput SHALL display an expand/collapse toggle button when the Textarea contains more than 3 lines of text or the user activates the keyboard shortcut.
2. WHEN the user clicks the expand toggle button, THE ChatInput SHALL transition from Compact_Mode to Expanded_Mode.
3. WHILE in Expanded_Mode, THE ChatInput SHALL increase the Textarea maximum height to 60% of the Viewport height.
4. WHILE in Expanded_Mode, WHEN the Viewport is resized, THE ChatInput SHALL re-clamp the Textarea height to the new 60% of Viewport height value.
5. WHILE in Expanded_Mode, THE ChatInput SHALL display a visible collapse button to return to Compact_Mode.
6. WHEN the user clicks the collapse button, THE ChatInput SHALL transition from Expanded_Mode to Compact_Mode, restoring the Max_Rows height limit.
7. WHEN the user sends a message while in Expanded_Mode, THE ChatInput SHALL return to Compact_Mode and reset the Textarea height to the minimum.

### Requirement 3: Keyboard Shortcut for Expand/Collapse

**User Story:** As a keyboard-focused user, I want to toggle the expanded input mode with a shortcut, so that I can stay in flow without reaching for the mouse.

#### Acceptance Criteria

1. WHEN the user presses Ctrl+Shift+E (or Cmd+Shift+E on macOS) while the Textarea is focused, THE ChatInput SHALL toggle between Compact_Mode and Expanded_Mode.
2. WHEN the keyboard shortcut is pressed, THE ChatInput SHALL prevent the default browser behavior for that key combination.
3. THE ChatInput SHALL display the keyboard shortcut hint in a tooltip on the expand/collapse toggle button.

### Requirement 4: Textarea Reset on Send

**User Story:** As a user, I want the input area to reset cleanly after I send a message, so that I have a fresh starting point for my next message.

#### Acceptance Criteria

1. WHEN the user sends a message, THE ChatInput SHALL clear the Textarea content and reset the Textarea height to the minimum (2 rows).
2. WHEN the user sends a message while in Expanded_Mode, THE ChatInput SHALL return to Compact_Mode before resetting the Textarea height.
3. WHEN the Textarea is reset after sending, THE ChatInput SHALL set overflow-y to hidden so no empty scrollable area remains.

### Requirement 5: Message List Layout Adjustment

**User Story:** As a user, I want the message list to adjust when the input area expands, so that I can still see recent messages and the input does not cover them.

#### Acceptance Criteria

1. WHILE the ChatInput is in Expanded_Mode, THE ChatPage SHALL reduce the message list visible area to accommodate the larger Textarea, keeping the most recent messages visible.
2. WHEN the ChatInput transitions between Compact_Mode and Expanded_Mode, THE ChatPage SHALL adjust the message list height smoothly without abrupt content jumps.
3. THE ChatPage SHALL keep the message list scrollable at all times, regardless of the ChatInput mode.

### Requirement 6: Scroll Position Preservation in Textarea

**User Story:** As a user, I want my cursor position and scroll position preserved when toggling between compact and expanded modes, so that I do not lose my place in a long message.

#### Acceptance Criteria

1. WHEN the user toggles from Compact_Mode to Expanded_Mode, THE ChatInput SHALL preserve the Textarea cursor position (selectionStart and selectionEnd).
2. WHEN the user toggles from Expanded_Mode to Compact_Mode, THE ChatInput SHALL preserve the Textarea cursor position (selectionStart and selectionEnd).
3. WHEN the user toggles between modes, THE ChatInput SHALL scroll the Textarea so the cursor remains visible within the Textarea viewport.

### Requirement 7: Accessibility

**User Story:** As a user relying on assistive technology, I want the expand/collapse controls to be accessible, so that I can use the feature with a screen reader or keyboard navigation.

#### Acceptance Criteria

1. THE expand/collapse toggle button SHALL have an aria-label that describes the current action ("Expand input" or "Collapse input").
2. THE expand/collapse toggle button SHALL have an aria-expanded attribute reflecting the current mode (true for Expanded_Mode, false for Compact_Mode).
3. WHEN the mode changes, THE ChatInput SHALL announce the transition to assistive technology via an aria-live region or equivalent mechanism.

### Requirement 8: Streaming State Interaction

**User Story:** As a user, I want the expanded input to behave correctly when the agent is streaming a response, so that the UI remains consistent.

#### Acceptance Criteria

1. WHILE the ChatInput is disabled during streaming, THE expand/collapse toggle button SHALL remain functional so the user can resize the input area while waiting.
2. WHILE in Expanded_Mode during streaming, THE ChatInput SHALL keep the Textarea disabled but maintain the expanded height.
3. WHEN streaming completes while in Expanded_Mode, THE ChatInput SHALL re-enable the Textarea and remain in Expanded_Mode.

### Requirement 9: Line Count and Character Feedback

**User Story:** As a user composing a long message, I want to see how much text I have entered, so that I can gauge the length of my prompt.

#### Acceptance Criteria

1. WHILE the Textarea contains more than 5 lines of text, THE ChatInput SHALL display a line count indicator showing the current number of lines.
2. WHEN the Textarea content changes, THE ChatInput SHALL update the line count indicator within the same render cycle.
3. THE line count indicator SHALL be positioned so it does not obscure the Textarea content or the send button.

### Requirement 10: Per-Tab State Isolation

**User Story:** As a user working across multiple chat tabs, I want each tab's input area to maintain its own expanded/compact state, line count, and textarea content independently, so that toggling or typing in one tab does not affect any other tab.

#### Acceptance Criteria

1. WHEN the user switches from Tab A to Tab B, THE ChatInput SHALL restore Tab B's previously saved expanded/compact mode, and Tab A's mode SHALL be preserved for when the user returns.
2. WHEN the user toggles Expanded_Mode in one tab, THE ChatInput in all other tabs SHALL remain in their current mode (Compact or Expanded) unaffected.
3. WHEN the user switches tabs, THE ChatInput SHALL restore the target tab's textarea content (inputValue) exactly as it was when the user left that tab.
4. WHEN the user switches tabs, THE ChatInput SHALL restore the target tab's line count indicator state (visible/hidden and count value) consistent with the restored textarea content.
5. WHEN a new tab is created, THE ChatInput SHALL initialize in Compact_Mode with an empty textarea and a line count of 1.
