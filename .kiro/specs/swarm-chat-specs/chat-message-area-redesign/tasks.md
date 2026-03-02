# Implementation Plan: Chat Message Area Redesign

## Overview

Incrementally redesign the SwarmAI chat message area by extracting new sub-components from `MessageBubble.tsx`, modifying existing components, and adding a branded welcome screen. Each task builds on the previous, ending with integration wiring and tests. All code is TypeScript/React in `desktop/src/pages/chat/`.

## Tasks

- [x] 1. Update constants and add shared CSS animation
  - [x] 1.1 Add `USER_MESSAGE_MAX_LINES = 5` to `desktop/src/pages/chat/constants.ts` and update `createWelcomeMessage` emoji from 🤖 to 🐝
    - Add the new constant for line clamp threshold
    - Update the emoji in the default welcome text and in `createWorkspaceChangeMessage` references
    - _Requirements: 1.3, 5.2_

  - [x] 1.2 Add the `swarm-icon-streaming` CSS keyframes animation
    - Create or extend a CSS file (e.g., inline styles or a shared `.css` file in the components directory) with the `@keyframes swarm-pulse` and `.swarm-icon-streaming` class as specified in the design
    - _Requirements: 2.3, 2.4_

- [x] 2. Create AssistantHeader sub-component
  - [x] 2.1 Create `desktop/src/pages/chat/components/AssistantHeader.tsx`
    - Implement `AssistantHeaderProps` interface with `timestamp: string` and `isStreaming?: boolean`
    - Render a single header line: 🐝 emoji (with `swarm-icon-streaming` class when streaming), "SwarmAI" label, `·` separator, formatted timestamp (`HH:MM AM/PM`)
    - Handle invalid/missing timestamps gracefully (display empty string)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 9.2_

  - [ ]* 2.2 Write property test for AssistantHeader — Property 4: Icon animation matches streaming state
    - **Property 4: Icon animation matches streaming state**
    - Generate random assistant messages and random `isStreaming` boolean; verify `swarm-icon-streaming` class is present iff `isStreaming === true`
    - **Validates: Requirements 2.3, 2.4**

- [x] 3. Create UserMessageView sub-component
  - [x] 3.1 Create `desktop/src/pages/chat/components/UserMessageView.tsx`
    - Implement `UserMessageViewProps` interface with `message: Message`
    - Render user messages with light background (`bg-[var(--color-card)]`), no avatar, no timestamp
    - Implement 5-line truncation using CSS `line-clamp-5` (referencing `USER_MESSAGE_MAX_LINES` constant)
    - Use `useRef` + `useEffect` + `ResizeObserver` to detect overflow and set `isClamped` state
    - Show "Show more" / "Show less" toggle with `aria-expanded` attribute when content is clamped
    - State: `isExpanded` (default `false`), `isClamped` (default `false`)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 8.1_

  - [ ]* 3.2 Write property test — Property 1: User messages render without avatar and timestamp
    - **Property 1: User messages render without avatar and timestamp**
    - Generate random `Message` with `role: 'user'`; verify no avatar icon element and no timestamp element in rendered output
    - **Validates: Requirements 1.1, 1.2**

  - [ ]* 3.3 Write property test — Property 2: Long user messages are truncated with expand toggle
    - **Property 2: Long user messages are truncated with expand toggle**
    - Generate `Message` with `role: 'user'` and text with 6+ newline-separated lines; verify expansion toggle is present. Generate short messages; verify no toggle.
    - **Validates: Requirements 1.3**

- [x] 4. Create AssistantMessageView sub-component
  - [x] 4.1 Create `desktop/src/pages/chat/components/AssistantMessageView.tsx`
    - Implement `AssistantMessageViewProps` interface with `message`, `onAnswerQuestion?`, `pendingToolUseId?`, `isStreaming?`
    - Render `AssistantHeader` with timestamp and streaming state
    - Render content blocks via `ContentBlockRenderer` left-aligned, no avatar indentation, with `max-w-3xl` constraint
    - Wrap content in red border container when `message.isError === true` (preserve existing error styling: `border-red-500/60`, `bg-red-500/10`)
    - _Requirements: 2.1, 3.1, 3.2, 3.3, 6.1, 6.2_

  - [ ]* 4.2 Write property test — Property 3: All assistant messages display branded header
    - **Property 3: All assistant messages display branded header**
    - Generate random assistant `Message` with random `isError` boolean; verify 🐝 icon, "SwarmAI" text, and timestamp are present; verify no `smart_toy` icon
    - **Validates: Requirements 2.1, 2.2, 6.2**

  - [ ]* 4.3 Write property test — Property 5: Assistant content is left-aligned with no avatar indentation
    - **Property 5: Assistant content is left-aligned with no avatar indentation**
    - Generate random assistant `Message`; verify no `text-right`, no `flex-row-reverse`, no left-side avatar element, and `max-w-3xl` constraint present
    - **Validates: Requirements 3.1, 3.2, 3.3**

  - [ ]* 4.4 Write property test — Property 8: Error messages preserve red border styling
    - **Property 8: Error messages preserve red border styling**
    - Generate assistant `Message` with `isError: true`; verify `border-red-500/60` and `bg-red-500/10` classes present
    - **Validates: Requirements 6.1**

- [x] 5. Checkpoint — Verify sub-components compile and render
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Modify ToolUseBlock to collapse by default
  - [x] 6.1 Update `desktop/src/pages/chat/components/ToolUseBlock.tsx`
    - Change default state to collapsed (`useState(false)`)
    - Collapsed view: single line with light-gray background (`bg-gray-100 dark:bg-gray-800/50`), `terminal` icon, tool name, chevron icon, `aria-expanded` attribute
    - Expanded view: existing full rendering (header bar + JSON content + copy button)
    - Defer JSON serialization: only compute `JSON.stringify(input, null, 2)` via `useMemo` when `isExpanded` is true
    - Make entire header row the click target in both states
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 7.2_

  - [ ]* 6.2 Write property test — Property 6 (tool_use): Tool use blocks default to collapsed state
    - **Property 6 (partial): All tool_use blocks default to collapsed state**
    - Generate random `ToolUseContent` blocks; verify initial render shows collapsed summary, not expanded JSON content
    - **Validates: Requirements 4.1, 4.2, 7.2**

- [x] 7. Create ToolResultBlock component
  - [x] 7.1 Create `desktop/src/pages/chat/components/ToolResultBlock.tsx`
    - Implement `ToolResultBlockProps` interface with `content?: string` and `isError: boolean`
    - Default state: collapsed (single summary line)
    - Collapsed view: `check_circle` icon (or `error` if `isError`), "Tool Result" label, chevron, `aria-expanded` attribute, light-gray background matching ToolUseBlock
    - Expanded view: full `<pre><code>` content block with copy button, `aria-expanded="true"`
    - _Requirements: 4.5, 7.2_

  - [ ]* 7.2 Write property test — Property 6 (tool_result): Tool result blocks default to collapsed state
    - **Property 6 (partial): All tool_result blocks default to collapsed state**
    - Generate random `ToolResultContent` blocks; verify initial render shows collapsed summary, not expanded content
    - **Validates: Requirements 4.5, 7.2**

  - [ ]* 7.3 Write property test — Property 7: Expand/collapse round-trip idempotence
    - **Property 7: Expand/collapse round-trip idempotence**
    - For ToolUseBlock and ToolResultBlock, simulate expand then collapse; verify DOM matches initial rendered state
    - **Validates: Requirements 4.3, 4.4, 7.1**

- [x] 8. Update ContentBlockRenderer to use ToolResultBlock
  - [x] 8.1 Modify `desktop/src/pages/chat/components/ContentBlockRenderer.tsx`
    - Import `ToolResultBlock` from `./ToolResultBlock`
    - Replace the inline `tool_result` `<div>` rendering with `<ToolResultBlock content={block.content} isError={block.isError} />`
    - All other cases remain unchanged
    - _Requirements: 4.5_

- [x] 9. Refactor MessageBubble as thin dispatcher
  - [x] 9.1 Modify `desktop/src/pages/chat/components/MessageBubble.tsx`
    - Import `UserMessageView` and `AssistantMessageView`
    - Branch on `message.role`: `'user'` → `<UserMessageView>`, `'assistant'` → `<AssistantMessageView>`
    - Pass through all relevant props (`onAnswerQuestion`, `pendingToolUseId`, `isStreaming`) to `AssistantMessageView`
    - Remove the old shared avatar/header/content layout code that is now handled by the sub-components
    - _Requirements: 1.1, 1.2, 2.1, 3.1, 3.2, 6.1, 6.2_

- [x] 10. Checkpoint — Verify message rendering end-to-end
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Create WelcomeScreen component
  - [x] 11.1 Create `desktop/src/pages/chat/components/WelcomeScreen.tsx`
    - Centered layout with circular SwarmAI icon (`swarmai-icon-round.png` in `desktop/public/`), gradient glow effect
    - "Welcome to SwarmAI!" heading, "Your AI Team, 24/7" slogan, "Work smarter, move faster, and enjoy the journey." tagline
    - Style per the prototype's `.welcome-state` CSS
    - This is NOT a message bubble — it's a standalone presentational component
    - _Requirements: 5.1, 5.2, 5.3, 9.1_

  - [ ]* 11.2 Write property test — Property 9: Welcome screen visibility is determined by message count
    - **Property 9: Welcome screen visibility is determined by message count per tab**
    - Generate random arrays of `Message` (including empty); verify `WelcomeScreen` renders iff array length is 0
    - **Validates: Requirements 5.1, 5.4, 5.5**

  - [ ]* 11.3 Write property test — Property 10: Icon treatment differentiation
    - **Property 10: Icon treatment differentiation**
    - Render `WelcomeScreen` and verify icon is an `<img>` element (not 🐝 emoji). Render `AssistantHeader` and verify icon is 🐝 emoji text (not `<img>`)
    - **Validates: Requirements 2.1, 5.2, 9.1, 9.2**

- [x] 12. Wire WelcomeScreen into the chat page
  - [x] 12.1 Integrate WelcomeScreen in the parent chat area component
    - Conditionally render `<WelcomeScreen />` when `messages.length === 0` for the active tab
    - Otherwise render the message list
    - Ensure the welcome screen disappears when the first message is sent
    - _Requirements: 5.1, 5.4, 5.5_

- [x] 13. Update component barrel export
  - [x] 13.1 Update `desktop/src/pages/chat/components/index.ts`
    - Export `WelcomeScreen`, `UserMessageView`, `AssistantMessageView`, `AssistantHeader`, `ToolResultBlock`
    - _Requirements: all_

- [ ] 14. Final unit tests for edge cases
  - [ ]* 14.1 Write unit tests for edge cases and specific examples
    - WelcomeScreen renders all required text elements (heading, slogan, tagline)
    - WelcomeScreen is not wrapped in MessageBubble
    - AssistantHeader formats various timestamp strings correctly (valid, invalid, missing)
    - User message with exactly 5 lines does NOT show expand toggle
    - User message with 0 content blocks renders without error
    - Tool result with `isError: true` shows error icon in collapsed view
    - ToolUseBlock with TodoWrite special case still works after redesign
    - Test file: `desktop/src/pages/chat/components/__tests__/MessageBubble.test.tsx`
    - _Requirements: 1.3, 2.1, 4.5, 5.2, 5.3, 6.1_

- [x] 15. Final checkpoint — Full regression
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- All components use local `useState` — no global state changes needed
- The `createWelcomeMessage()` utility is retained for `createWorkspaceChangeMessage()` compatibility
