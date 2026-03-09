# Implementation Plan: Compact Button Relocation

## Overview

Relocate the Compact Context button from `ChatHeader.tsx` to the last assistant message's action row in `AssistantMessageView.tsx`. Store `contextWarning` as a field on `UnifiedTab` in `tabMapRef` (per multi-tab isolation principles) to ensure multi-tab/multi-session isolation. Thread the active tab's display-mirror `contextWarning` from `ChatPage` through `MessageBubble` to `AssistantMessageView`. The button is conditionally visible based on context warning level, with urgency coloring at critical level. Add a Context Usage Ring indicator in the ChatInput bottom row. Backend emits context status at all levels on turn 1 + every 5 turns.

## Tasks

- [x] 1. Remove compact button from ChatHeader
  - [x] 1.1 Remove compact state, handler, Toast, and button JSX from ChatHeader.tsx
    - Remove `compactStatus` and `compactToast` state declarations
    - Remove `handleCompact` async handler function
    - Remove the compact `<button>` JSX block
    - Remove the compact `<Toast>` JSX block
    - Remove `chatService` import if no longer used
    - Verify remaining header buttons (New Session, ToDo Radar, Chat History, File Browser) are unchanged
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. Thread contextWarning prop through component tree
  - [x] 2.1 Add contextWarning field to UnifiedTab and update SSE handlers
    - Add `contextWarning: ContextWarning | null` to `UnifiedTab` interface in `useUnifiedTabState.ts`
    - Initialize to `null` in `initTabState`
    - Update `context_warning` SSE handler in `createStreamHandler` to write to `tabMapRef.get(capturedTabId).contextWarning` (using closure-captured `capturedTabId`, NOT `sessionIdRef.current`)
    - Update `context_compacted` SSE handler to clear `tabMapRef.get(capturedTabId).contextWarning`
    - Mirror to React `setContextWarning()` only when `capturedTabId === activeTabIdRef.current` (display mirror pattern)
    - Add `contextWarning` to the tab switch restore path (alongside `messages`, `sessionId`, etc.)
    - Keep existing `clearContextWarning` callback — update to clear active tab's `tabMapRef` entry + React state
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 2.2 Add contextWarning prop to MessageBubble
    - Import `ContextWarning` type from `useChatStreamingLifecycle`
    - Add `contextWarning?: ContextWarning | null` to `MessageBubbleProps`
    - Forward `contextWarning` to `AssistantMessageView` in the assistant branch
    - _Requirements: 3.2_

  - [x] 2.3 Add contextWarning prop to AssistantMessageView
    - Import `ContextWarning` type from `useChatStreamingLifecycle`
    - Add `contextWarning?: ContextWarning | null` to `AssistantMessageViewProps`
    - Destructure from props
    - _Requirements: 3.3_

  - [x] 2.4 Pass contextWarning from ChatPage to MessageBubble
    - In `ChatPage.tsx`, pass `contextWarning={contextWarning}` to `<MessageBubble>` in `messages.map()`
    - `contextWarning` is already the active tab's warning via the display mirror pattern (no map resolution needed)
    - The context warning Toast in ChatPage continues to use `contextWarning` directly (unchanged)
    - _Requirements: 3.1, 6.4, 6.5, 6.6_

- [x] 3. Implement compact button in AssistantMessageView
  - [x] 3.1 Add compact local state and handler to AssistantMessageView
    - Add `compactStatus` state (`'idle' | 'loading' | 'done'`)
    - Add `compactToast` state (`string | null`)
    - Import `chatService` for `compactSession` call
    - Implement `handleCompact` callback with loading/success/error flow
    - Guard against missing `sessionId` and concurrent clicks
    - _Requirements: 2.3, 2.4, 2.5, 2.6_

  - [x] 3.2 Add compact button JSX to action row
    - Compute `showCompactButton` visibility condition: `isLastAssistant && !isStreaming && sessionId && contextWarning?.level in ['warn', 'critical']`
    - Render button after Save-to-Memory with `compress` icon (idle), `progress_activity` spinner (loading), `check_circle` (done)
    - Apply urgency coloring: `text-red-500` at critical, `text-[var(--color-text-muted)]` at warn
    - Set `title` to `"Compact Context (${pct}% used)"` and `aria-label="Compact Context"`
    - Match existing action row styling: `text-xs`, `px-2 py-0.5`, hover-to-reveal pattern
    - _Requirements: 2.1, 2.2, 2.7, 2.8, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [x] 3.3 Add compact Toast to AssistantMessageView
    - Render `<Toast>` for compact success/error messages after the memory save Toast
    - Auto-dismiss after 4 seconds
    - _Requirements: 2.5, 2.6_

- [x] 4. Checkpoint - Verify compact button integration
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement Context Usage Ring indicator
  - [x] 5.1 Create ContextUsageRing component
    - Create `desktop/src/pages/chat/components/ContextUsageRing.tsx`
    - SVG circular progress ring: 18px diameter, 2.5px stroke
    - Color coding: green < 70%, amber 70–84%, red >= 85%, gray for null
    - Tooltip on hover: "X% context used" or "No context data yet"
    - `aria-label` for accessibility
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 5.2 Add contextPct prop to ChatInput and render ring
    - Add `contextPct?: number | null` to `ChatInputProps`
    - Render `<ContextUsageRing pct={contextPct} />` after `<TSCCPopoverButton>` in the bottom row
    - _Requirements: 7.1_

  - [x] 5.3 Thread contextPct from ChatPage to ChatInput
    - Pass `contextPct={contextWarning?.pct ?? null}` to `<ChatInput>` in ChatPage
    - `contextWarning` is already the active tab's display mirror — per-session isolated
    - _Requirements: 7.6, 7.7_

  - [x] 5.4 Backend: emit context_status at all levels, turn-1 + every 5 turns
    - In `context_monitor.py`, change `CHECK_INTERVAL_TURNS` from 15 to 5
    - In `agent_manager.py` `_run_query_on_client`, change the check condition from `if turns % CHECK_INTERVAL_TURNS == 0:` to `if turns == 1 or turns % CHECK_INTERVAL_TURNS == 0:`
    - Remove the `if status.level in ("warn", "critical"):` guard — always yield the `context_warning` event
    - Same changes in `continue_with_answer` path
    - Update `test_context_monitor.py` to reflect new interval value (5 instead of 15)
    - _Requirements: 7.8, 7.9_

- [x] 6. Checkpoint - Verify ring + compact integration
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Property-based tests
  - [ ]* 7.1 Write property test for compact button visibility invariant
    - **Property 1: Compact Button Visibility Invariant**
    - Generate random combinations of `contextWarning`, `isLastAssistant`, `isStreaming`, `sessionId`
    - Assert button renders ↔ all visibility conditions met
    - **Validates: Requirements 2.1, 2.2, 3.4, 3.5, 5.3**

  - [ ]* 7.2 Write property test for urgency coloring
    - **Property 2: Compact Button Urgency Coloring**
    - Generate `contextWarning` with level `warn` or `critical` and random `pct`
    - Assert red classes for critical, muted classes for warn
    - **Validates: Requirements 4.3, 4.4**

  - [ ]* 7.3 Write property test for title percentage
    - **Property 3: Compact Button Title Shows Usage Percentage**
    - Generate random `pct` values (0–100)
    - Assert `title` matches `"Compact Context (${pct}% used)"`
    - **Validates: Requirements 4.6**

  - [ ]* 7.4 Write property test for click invokes compactSession
    - **Property 4: Click Invokes compactSession with Correct Session**
    - Generate random `sessionId` strings
    - Click button, assert `chatService.compactSession` called with correct sessionId
    - **Validates: Requirements 2.3**

  - [ ]* 7.5 Write property test for header buttons preserved
    - **Property 5: Header Buttons Preserved After Compact Removal**
    - Generate random `activeSidebar` values
    - Assert four header buttons present, no Compact Context button
    - **Validates: Requirements 1.1, 1.3**

  - [ ]* 7.6 Write property test for copy button preservation
    - **Property 6: Copy Button Preservation**
    - Generate random `isLastAssistant` and `contextWarning` combinations
    - Assert Copy button always present on non-streaming assistant messages
    - **Validates: Requirements 5.1**

  - [ ]* 7.7 Write property test for per-session warning isolation
    - **Property 7: Per-Session Warning Isolation**
    - Generate two distinct tab IDs (T1, T2) with separate UnifiedTab entries in tabMapRef
    - Set contextWarning on T1's UnifiedTab, assert T2's contextWarning is still null
    - Clear T1's contextWarning, assert T2's entry (if any) is unchanged
    - Simulate tab switch from T1 to T2 — assert React `contextWarning` state reflects T2's value (null), not T1's
    - Render AssistantMessageView with T2's contextWarning (null) — assert no Compact button
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.5, 6.7**

  - [ ]* 7.8 Write property test for context usage ring color invariant
    - **Property 8: Context Usage Ring Color Invariant**
    - Generate random `pct` values (0–100) and null
    - Render `ContextUsageRing` with each value
    - Assert stroke color: green (#10b981) < 70, amber (#f59e0b) 70–84, red (#ef4444) >= 85, border color for null
    - **Validates: Requirements 7.2, 7.3**

  - [ ]* 7.9 Write property test for context usage ring tooltip
    - **Property 9: Context Usage Ring Tooltip**
    - Generate random `pct` values (0–100) and null
    - Render `ContextUsageRing`, assert `title` matches "X% context used" or "No context data yet" for null
    - **Validates: Requirements 7.4**

- [ ] 8. Unit tests
  - [ ]* 8.1 Write unit tests for compact button states and behavior
    - Test idle state renders `compress` icon
    - Test loading state shows spinner and disables button
    - Test success state shows `check_circle` with green styling
    - Test error shows Toast with error message
    - Test `aria-label="Compact Context"` is present
    - Test button disappears when `contextWarning` changes to null
    - Test Save-to-Memory still renders alongside Compact button
    - Test ChatHeader no longer renders compact-related UI
    - Test UnifiedTab.contextWarning stores warnings per tab — setting T1 doesn't affect T2
    - Test clearing T1's warning via context_compacted doesn't affect T2's warning
    - Test tab switch from warned session to clean session hides compact button
    - _Requirements: 2.3, 2.4, 2.5, 2.6, 4.2, 4.5, 5.1, 5.2, 6.1, 6.2, 6.3, 6.5_

  - [ ]* 8.2 Write unit tests for Context Usage Ring
    - Test ring renders with green stroke when pct < 70
    - Test ring renders with amber stroke when 70 <= pct < 85
    - Test ring renders with red stroke when pct >= 85
    - Test ring renders with gray border stroke when pct is null
    - Test tooltip shows "X% context used" for numeric pct
    - Test tooltip shows "No context data yet" for null pct
    - Test ring SVG fill offset is proportional to pct value
    - Test ring renders in ChatInput bottom row after TSCC button
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 9. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Backend change: emit context_warning SSE at all levels (not just warn/critical) so the ring can show usage before warnings
