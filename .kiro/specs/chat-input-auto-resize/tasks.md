# Implementation Plan: Chat Input Auto-Resize

## Overview

Enhance the ChatInput component with smooth auto-grow, an expanded editing mode (60vh), keyboard shortcut toggle (Ctrl/Cmd+Shift+E), per-tab state isolation for `isExpanded` and `inputValue`, line count indicator, and full accessibility support. All changes are scoped to `ChatInput.tsx` and `ChatPage.tsx` (frontend only). Property-based tests use `fast-check` + `vitest` (already in devDependencies).

## Tasks

- [x] 1. Extend ChatInput props and add expanded mode state management
  - [x] 1.1 Add `isExpanded` and `onExpandedChange` props to `ChatInputProps` interface in `ChatInput.tsx`
    - Add `isExpanded: boolean` and `onExpandedChange: (expanded: boolean) => void` to the interface
    - Update the function signature destructuring to accept the new props
    - _Requirements: 2.2, 2.6, 10.1_

  - [x] 1.2 Lift `isExpanded` state to `ChatPage.tsx` and pass as props to `ChatInput`
    - Add `const [isExpanded, setIsExpanded] = useState(false)` in ChatPage
    - Pass `isExpanded={isExpanded}` and `onExpandedChange={setIsExpanded}` to the `<ChatInput>` JSX
    - _Requirements: 2.2, 10.1, 10.2_

  - [x] 1.3 Add `inputValueMapRef` to `ChatPage.tsx` for per-tab draft text storage
    - Add `const inputValueMapRef = useRef<Map<string, string>>(new Map())` in ChatPage
    - This ref is never serialized — avoids writing large text to `open_tabs.json`
    - _Requirements: 10.3_

- [x] 2. Implement per-tab state save/restore in ChatPage
  - [x] 2.1 Extend `handleTabSelect` to save and restore `isExpanded` and `inputValue` per-tab
    - Before switching: save `isExpanded` into `tabMapRef` via `updateTabState(currentTabId, { isExpanded })` and save `inputValue` into `inputValueMapRef`
    - After switching: restore `setIsExpanded(tabState?.isExpanded ?? false)` and `setInputValue(inputValueMapRef.current.get(tabId) ?? '')`
    - ChatInput's existing `useEffect([inputValue, adjustHeight])` handles re-measure automatically
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x] 2.2 Extend `handleNewSession` to reset `isExpanded` to `false`
    - Add `setIsExpanded(false)` after the existing state resets in `handleNewSession`
    - _Requirements: 10.5_

  - [x] 2.3 Extend `handleTabClose` to clean up `inputValueMapRef` entry
    - Add `inputValueMapRef.current.delete(tabId)` to prevent unbounded memory growth
    - _Requirements: 10.3_

  - [x] 2.4 Add `isExpanded` as a runtime-only field in `UnifiedTab` interface (NOT serialized)
    - Add `isExpanded?: boolean` to the `UnifiedTab` interface in `useUnifiedTabState.ts`
    - Do NOT add it to `toSerializable()` or `hydrateTab()` — tabs always start compact after restart
    - _Requirements: 10.1, 10.2_

- [x] 3. Checkpoint - Verify per-tab state wiring compiles
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement expanded mode height logic and CSS transition in ChatInput
  - [x] 4.1 Update `adjustHeight` to use 60vh max-height when `isExpanded` is true
    - Compute `const maxHeight = isExpanded ? window.innerHeight * 0.6 : maxHeightRef.current`
    - Use this computed maxHeight instead of `maxHeightRef.current` directly
    - Also compute and set `lineCount` state: `const lines = el.value.split('\n').length`
    - Only call `setLineCount` when crossing the visibility threshold (>5 or ≤5) to avoid unnecessary re-renders
    - _Requirements: 1.1, 1.2, 1.3, 2.3, 9.1, 9.2_

  - [x] 4.2 Add CSS transition class for mode toggle animations only
    - Add `isTransitioning` ref (`useRef(false)`) to ChatInput
    - Create helper that adds `.chat-textarea-transitioning` class before mode toggle, removes after 150ms
    - The class applies `transition: height 150ms ease-out` — NOT applied during typing/pasting to avoid flicker from `height: auto` reset
    - Manage via `textareaRef.current.classList.add/remove()` to avoid React re-renders
    - _Requirements: 1.4, 5.2_

  - [x] 4.3 Add window resize listener when `isExpanded` is true
    - Add `useEffect` that registers a `resize` event listener when `isExpanded` is true
    - Listener calls `adjustHeight()` debounced to 100ms
    - Cleanup removes listener when `isExpanded` becomes false or component unmounts
    - _Requirements: 2.4_

  - [ ]* 4.4 Write property test for expanded max-height calculation (Property 3)
    - **Property 3: Expanded max-height is 60% of viewport**
    - Generate random `viewportHeight` (200–2000) and `isExpanded` (boolean). Assert correct max-height value.
    - **Validates: Requirements 2.3**

  - [ ]* 4.5 Write property test for window resize re-clamping (Property 12)
    - **Property 12: Window resize re-clamps expanded height**
    - Generate random viewport height changes while `isExpanded` is true, assert re-clamped to `window.innerHeight * 0.6`
    - **Validates: Requirements 2.3 (responsive)**

- [x] 5. Implement expand/collapse toggle button and keyboard shortcut
  - [x] 5.1 Add `toggleExpanded` function with cursor position preservation
    - Implement `toggleExpanded` as `useCallback` that saves `selectionStart`/`selectionEnd`, calls `onExpandedChange(!isExpanded)`, and restores cursor via `requestAnimationFrame`
    - Scroll cursor into view by computing cursor's vertical offset relative to textarea visible area
    - Add transition class before toggle, remove after 150ms
    - _Requirements: 2.2, 2.5, 2.6, 6.1, 6.2, 6.3_

  - [x] 5.2 Add expand/collapse toggle button JSX
    - Render button next to send button when `lineCount > 3` or `isExpanded` is true
    - Use `aria-label="Expand input"` / `"Collapse input"` based on `isExpanded`
    - Set `aria-expanded={isExpanded}` on the button
    - Add tooltip with keyboard shortcut hint (`Ctrl+Shift+E` / `⌘+Shift+E`)
    - Button uses `expand_content` / `collapse_content` Material Symbols icons
    - _Requirements: 2.1, 2.5, 7.1, 7.2, 3.3_

  - [x] 5.3 Extend `handleKeyDown` with `Ctrl+Shift+E` / `Cmd+Shift+E` shortcut
    - Detect `(e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'E'`
    - Call `e.preventDefault()` and `toggleExpanded()`
    - _Requirements: 3.1, 3.2_

  - [x] 5.4 Extend `handleSend` to reset expanded mode via `onExpandedChange(false)`
    - Call `onExpandedChange(false)` before clearing textarea styles
    - Add transition class for smooth animation on send-reset
    - Do NOT duplicate this reset in ChatPage's `handleSendMessage`
    - _Requirements: 2.7, 4.1, 4.2, 4.3_

  - [ ]* 5.5 Write property test for expand button visibility (Property 2)
    - **Property 2: Expand button visibility tracks line count**
    - Generate random `lineCount` (0–100) and `isExpanded` (boolean). Assert button visible iff `lineCount > 3 || isExpanded`.
    - **Validates: Requirements 2.1**

  - [ ]* 5.6 Write property test for toggle round-trip (Property 6)
    - **Property 6: Toggle is an involution (round-trip)**
    - Generate initial `isExpanded` (boolean). Toggle twice, assert state equals initial.
    - **Validates: Requirements 3.1**

  - [ ]* 5.7 Write property test for cursor preservation (Property 7)
    - **Property 7: Cursor position preservation across mode toggle**
    - Generate text string and valid cursor positions. Toggle mode, assert `selectionStart`/`selectionEnd` preserved.
    - **Validates: Requirements 6.1, 6.2**

  - [ ]* 5.8 Write property test for send reset (Properties 4 & 5)
    - **Property 4: Send resets expanded mode** — Generate `isExpanded` (boolean) and non-empty input. Call `handleSend`, assert `isExpanded === false`.
    - **Property 5: Send resets textarea to minimum** — Generate any input. Call `handleSend`, assert height style cleared and overflow-y hidden.
    - **Validates: Requirements 2.6, 4.1, 4.2, 4.3**

- [x] 6. Checkpoint - Verify expanded mode toggle works end-to-end
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Add accessibility features and line count indicator
  - [x] 7.1 Add `aria-live` region for mode change announcements
    - Add a visually-hidden `<div aria-live="polite">` that announces "Input expanded" / "Input collapsed" on mode change
    - Use a state variable or ref to control the announcement text, cleared after a short delay
    - _Requirements: 7.3_

  - [x] 7.2 Add line count indicator JSX
    - Add `<span>` showing `{lineCount} lines` in the bottom row, visible when `lineCount > 5`
    - Position so it does not obscure textarea content or send button
    - Update within the same render cycle as textarea content changes (already handled by `adjustHeight` computing `lineCount`)
    - _Requirements: 9.1, 9.2, 9.3_

  - [ ]* 7.3 Write property test for accessibility attributes (Property 8)
    - **Property 8: Accessibility attributes match expanded state**
    - Generate `isExpanded` (boolean). Assert `aria-expanded` and `aria-label` match expected values.
    - **Validates: Requirements 7.1, 7.2**

  - [ ]* 7.4 Write property test for line count indicator (Property 9)
    - **Property 9: Line count indicator visibility and accuracy**
    - Generate multi-line strings (0–50 lines). Assert indicator visible iff lines > 5, and displayed count matches `text.split('\n').length`.
    - **Validates: Requirements 9.1**

- [x] 8. Implement streaming state interaction
  - [x] 8.1 Ensure expand/collapse toggle button remains functional during streaming
    - The toggle button must NOT be disabled when `isStreaming` is true — only the textarea is disabled
    - Verify the button's `disabled` prop does not depend on `isStreaming`
    - _Requirements: 8.1_

  - [x] 8.2 Verify textarea stays disabled but expanded during streaming
    - When `isStreaming` is true and `isExpanded` is true, textarea remains disabled with expanded height
    - When streaming completes, textarea re-enables and stays in expanded mode
    - _Requirements: 8.2, 8.3_

- [ ] 9. Write per-tab isolation property tests
  - [ ]* 9.1 Write property test for tab state round-trip (Property 10)
    - **Property 10: Tab state round-trip preservation**
    - Generate random `isExpanded` (boolean) and `inputValue` (string) for Tab A. Simulate save → switch to Tab B → switch back to Tab A. Assert values match originals.
    - **Validates: Requirements 10.1, 10.3**

  - [ ]* 9.2 Write property test for toggle isolation across tabs (Property 11)
    - **Property 11: Toggle isolation across tabs**
    - Generate N tabs (2–5) each with random `isExpanded` (boolean). Toggle one tab's `isExpanded`. Assert all other tabs' values unchanged.
    - **Validates: Requirements 10.2**

- [ ] 10. Write unit tests for edge cases and interactions
  - [ ]* 10.1 Write unit tests for expand/collapse interactions
    - Clicking expand button transitions to expanded mode (Req 2.2)
    - Clicking collapse button returns to compact mode (Req 2.5)
    - `Ctrl+Shift+E` calls `preventDefault()` (Req 3.2)
    - Tooltip on toggle button contains shortcut hint (Req 3.3)
    - `aria-live` region announces mode change (Req 7.3)
    - _Requirements: 2.2, 2.5, 3.2, 3.3, 7.3_

  - [ ]* 10.2 Write unit tests for streaming state interactions
    - Toggle button is not disabled during streaming (Req 8.1)
    - Textarea is disabled but expanded during streaming (Req 8.2)
    - Textarea re-enables after streaming completes while expanded (Req 8.3)
    - _Requirements: 8.1, 8.2, 8.3_

  - [ ]* 10.3 Write unit tests for boundary conditions
    - Empty string input — line count should be 1, indicator hidden
    - Single character input — no expand button, no line count
    - Exactly 3 lines — expand button NOT visible (boundary)
    - Exactly 4 lines — expand button visible (boundary)
    - Exactly 5 lines — line count indicator NOT visible (boundary)
    - Exactly 6 lines — line count indicator visible (boundary)
    - Textarea has `rows={2}` attribute (Req 1.5)
    - _Requirements: 1.5, 2.1, 9.1_

  - [ ]* 10.4 Write unit tests for per-tab isolation
    - New tab initializes with `isExpanded=false`, empty textarea, `lineCount=1` (Req 10.5)
    - Switching tabs restores the target tab's expanded mode (Req 10.1)
    - Switching tabs restores the target tab's textarea content (Req 10.3)
    - Line count indicator updates correctly after tab switch restores multi-line content (Req 10.4)
    - Switching to a tab that was never interacted with — defaults to compact, empty
    - _Requirements: 10.1, 10.3, 10.4, 10.5_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The existing `ChatInput.autogrow.property.test.tsx` already covers Property 1 (height clamping) — no need to re-implement
- All new test files go in `desktop/src/pages/chat/components/`
- Test file naming: `ChatInput.autoresize.test.tsx` (unit) and `ChatInput.autoresize.property.test.tsx` (property-based)
