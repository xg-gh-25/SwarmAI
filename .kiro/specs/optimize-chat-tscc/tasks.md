# Implementation Plan: Optimize Chat TSCC

## Overview

Reclaim vertical space in the chat window by removing TSCC Snapshot Cards and the full-width TSCC Panel, extracting cognitive modules to a shared file, and adding a compact TSCC icon button with popover in ChatInput's bottom row. All changes are rendering-layer only — `useTSCCState` hook and backend services are untouched.

## Tasks

- [x] 1. Extract cognitive modules to TSCCModules.tsx
  - [x] 1.1 Create `desktop/src/pages/chat/components/TSCCModules.tsx`
    - Move the five cognitive module components (`CurrentContextModule`, `ActiveAgentsModule`, `WhatAIDoingModule`, `ActiveSourcesModule`, `KeySummaryModule`) from `TSCCPanel.tsx` into this new file
    - Move shared helper functions used by the modules (`lifecycleLabel`, `freshness`, `capSummary`) into this file
    - Export all five modules and helpers as named exports
    - Each module keeps its existing interface: `{ tsccState: TSCCState }`
    - _Requirements: 4.6_

  - [ ]* 1.2 Write property test: Popover displays all five cognitive modules (Property 4)
    - **Property 4: Popover displays all five cognitive modules**
    - **Validates: Requirements 4.6**
    - For any non-null TSCCState generated via fast-check, rendering all five modules produces five distinct sections
    - Test file: `desktop/src/pages/chat/components/TSCCModules.test.tsx`

- [x] 2. Create TSCCPopoverButton component
  - [x] 2.1 Create `desktop/src/pages/chat/components/TSCCPopoverButton.tsx`
    - Implement `TSCCPopoverButtonProps` interface: `{ tsccState: TSCCState | null }`
    - Render a `<button>` with `psychology` Material Symbol icon
    - Add `aria-haspopup="true"`, `aria-expanded`, and `aria-label="TSCC context"` attributes
    - Manage open/close state via local `useState<boolean>(false)` — always starts closed
    - When `tsccState` is null: render with `disabled` attribute and muted opacity, do not attach onClick
    - When `tsccState` is non-null: render active button, toggle `isOpen` on click
    - Conditionally render popover content ONLY when `isOpen === true` (not CSS hidden)
    - Popover renders above the button, anchored to button position
    - Popover content: import and render all five cognitive modules from `TSCCModules.tsx`, passing `tsccState`
    - Popover container: scrollable with `max-h-[320px]`, styled with `bg-[var(--color-surface)]`, `border-[var(--color-border)]`, rounded corners, shadow
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 4.1, 4.5, 4.6, 5.3_

  - [x] 2.2 Implement click-outside and Escape dismissal
    - Add `useEffect` that attaches `mousedown` and `keydown` listeners on `document` ONLY when `isOpen === true`
    - Click-outside handler: close popover if click target is outside BOTH the popover container ref AND the toggle button ref
    - Escape handler: close popover on `Escape` key press
    - Clean up listeners on close or component unmount
    - _Requirements: 4.3, 4.4, 4.8, 4.9_

  - [x] 2.3 Implement tab-switch auto-close via threadId watch
    - Add `useEffect` watching `tsccState?.threadId`
    - When threadId changes (tab switch) or tsccState becomes null (session deleted), set `isOpen = false`
    - Use a ref to track previous threadId for comparison
    - _Requirements: 4.10, 4.11_

  - [ ]* 2.4 Write property test: TSCC button state reflects data availability (Property 2)
    - **Property 2: TSCC button state reflects data availability**
    - **Validates: Requirements 3.4, 3.5**
    - For any TSCCState value (including null), button is disabled iff tsccState is null
    - Test file: `desktop/src/pages/chat/components/TSCCPopoverButton.test.tsx`

  - [ ]* 2.5 Write property test: Popover toggle is an involution (Property 3)
    - **Property 3: Popover toggle is an involution**
    - **Validates: Requirements 4.1, 4.2**
    - For any initial state, clicking twice returns to original state
    - Test file: `desktop/src/pages/chat/components/TSCCPopoverButton.test.tsx`

  - [ ]* 2.6 Write property test: Tab-switch closes popover and resets TSCC context (Property 7)
    - **Property 7: Tab-switch closes popover and resets TSCC context**
    - **Validates: Requirements 4.10, 5.3**
    - For any sequence of threadId changes or null transitions, popover auto-closes
    - Test file: `desktop/src/pages/chat/components/TSCCPopoverButton.test.tsx`

- [x] 3. Checkpoint — Verify new components
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Wire TSCCPopoverButton into ChatInput
  - [x] 4.1 Add `tsccState` prop to ChatInput
    - Add `tsccState?: TSCCState | null` to `ChatInputProps` interface in `desktop/src/pages/chat/components/ChatInput.tsx`
    - Import `TSCCPopoverButton` from `./TSCCPopoverButton`
    - Render `<TSCCPopoverButton tsccState={tsccState ?? null} />` in the bottom row, after `FileAttachmentButton` and before the "Type / for commands" hint
    - _Requirements: 3.1, 5.1, 5.2_

  - [ ]* 4.2 Write property test: Popover content updates reactively (Property 5)
    - **Property 5: Popover content updates reactively**
    - **Validates: Requirements 4.7, 5.3**
    - For any sequence of TSCCState prop changes while popover is open, displayed content reflects the most recent state
    - Test file: `desktop/src/pages/chat/components/TSCCPopoverButton.test.tsx`

- [x] 5. Modify ChatPage — Remove TSCC timeline and panel, pass tsccState to ChatInput
  - [x] 5.1 Remove TSCC Snapshot Card rendering from ChatPage
    - Remove `TSCCSnapshotCard` import
    - Remove `listSnapshots` import from `../services/tscc`
    - Remove `threadSnapshots` state variable and its `useEffect` fetcher
    - Remove `TimelineItem` type alias and `timeline` useMemo that merges messages with snapshots
    - Render `messages` array directly in the scroll container instead of `timeline`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 7.1, 7.2, 7.3, 7.4, 7.5_

  - [x] 5.2 Remove TSCCPanel from ChatPage layout
    - Remove `TSCCPanel` import
    - Remove `<TSCCPanel>` JSX from between message area and ChatInput
    - _Requirements: 2.1, 2.2_

  - [x] 5.3 Pass tsccState to ChatInput and clean up destructuring
    - Destructure only `tsccState`, `applyTelemetryEvent`, and `triggerAutoExpand` from `useTSCCState` (omit unused expand/pin returns)
    - Pass `tsccState={tsccState}` prop to `<ChatInput>`
    - _Requirements: 5.1, 5.2, 6.1, 6.2_

  - [ ]* 5.4 Write property test: Messages-only rendering (Property 1)
    - **Property 1: Messages-only rendering**
    - **Validates: Requirements 1.1, 1.2, 1.4, 7.5**
    - For any list of messages, ChatPage renders exactly one MessageBubble per message and zero TSCCSnapshotCard components
    - Test file: `desktop/src/pages/ChatPage.test.tsx`

  - [ ]* 5.5 Write property test: Telemetry event application preserves state structure (Property 6)
    - **Property 6: Telemetry event application preserves state structure**
    - **Validates: Requirements 6.3**
    - For any valid TSCCState and any valid telemetry StreamEvent, applying the event produces a TSCCState retaining all required fields
    - Test file: `desktop/src/hooks/useTSCCState.test.ts`

- [x] 6. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use `fast-check` with `vitest` and `@testing-library/react`
- The `useTSCCState` hook is NOT modified — only its consumers change
- `TSCCPanel.tsx` and `TSCCSnapshotCard.tsx` files are left on disk (not deleted) to avoid breaking any other potential imports; they are simply no longer imported by ChatPage
