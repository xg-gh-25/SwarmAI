# Implementation Plan: Right Sidebar Mutual Exclusion

## Overview

This implementation replaces three independent `useSidebarState` hooks with a unified `useRightSidebarGroup` hook that manages mutual exclusion for the right sidebars (TodoRadar, ChatHistory, FileBrowser). The approach ensures only one sidebar is visible at a time while maintaining width persistence.

## Tasks

- [x] 1. Add sidebar type constants and configuration
  - [x] 1.1 Add `RightSidebarId` type and constants to `desktop/src/pages/chat/constants.ts`
    - Add `RIGHT_SIDEBAR_IDS` array constant with `'todoRadar' | 'chatHistory' | 'fileBrowser'`
    - Add `RightSidebarId` type export
    - Add `DEFAULT_ACTIVE_SIDEBAR` constant set to `'todoRadar'`
    - Add `RIGHT_SIDEBAR_WIDTH_CONFIGS` record mapping each sidebar to its width config
    - _Requirements: 3.1, 3.2, 3.3_

- [x] 2. Create the `useRightSidebarGroup` hook
  - [x] 2.1 Create `desktop/src/hooks/useRightSidebarGroup.ts` with core state management
    - Implement `activeSidebar` state initialized to `defaultActive` option
    - Implement `openSidebar(id)` function with no-op behavior when clicking active sidebar
    - Implement `isActive(id)` helper function
    - Implement width state management for each sidebar (reuse resize logic from `useSidebarState`)
    - Add localStorage cleanup for old collapsed state keys on mount
    - Add invalid sidebar ID validation with console warning
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 4.1, 4.2, 4.3_

  - [x] 2.2 Export the new hook from `desktop/src/hooks/index.ts`
    - Add `export { useRightSidebarGroup } from './useRightSidebarGroup'`
    - _Requirements: 1.1_

  - [x] 2.3 Write unit tests for `useRightSidebarGroup` hook
    - Create `desktop/src/hooks/useRightSidebarGroup.test.ts`
    - Test initial state defaults to TodoRadarSidebar
    - Test localStorage is ignored for visibility state
    - Test width persistence still works
    - Test switching between sidebars
    - Test no-op when clicking active sidebar button
    - _Requirements: 1.1, 1.4, 2.2, 3.1, 4.2_

  - [x] 2.4 Write property test for Mutual Exclusion Invariant
    - **Property 1: Mutual Exclusion Invariant**
    - Create `desktop/src/hooks/useRightSidebarGroup.property.test.ts`
    - Use fast-check to generate sequences of sidebar open operations
    - Verify exactly one sidebar is active after any operation
    - Verify the active sidebar matches the last opened sidebar
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 2.1**

  - [x] 2.5 Write property test for No-op on Active Click
    - **Property 2: No-op on Active Sidebar Click**
    - Verify calling `openSidebar(activeSidebar)` results in no state change
    - **Validates: Requirements 2.2**

- [x] 3. Checkpoint - Ensure hook tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Update ChatHeader component
  - [x] 4.1 Update `ChatHeaderProps` interface in `desktop/src/pages/chat/components/ChatHeader.tsx`
    - Replace `chatSidebarCollapsed`, `todoRadarCollapsed`, `onToggleChatSidebar`, `onToggleTodoRadar` props
    - Add `activeSidebar: RightSidebarId` prop
    - Add `onOpenSidebar: (id: RightSidebarId) => void` prop
    - Import `RightSidebarId` type from constants
    - _Requirements: 2.1, 2.3, 2.4, 5.1, 5.2, 5.3, 5.4_

  - [x] 4.2 Update toggle button styling for visual indicators
    - Update TodoRadar button to use `activeSidebar === 'todoRadar'` for highlight state
    - Update ChatHistory button to use `activeSidebar === 'chatHistory'` for highlight state
    - Ensure highlighted state uses `text-primary bg-primary/10` styling
    - Ensure non-highlighted state uses `text-[var(--color-text-muted)]` styling
    - _Requirements: 2.3, 2.4, 5.1, 5.2, 5.3, 5.4_

  - [x] 4.3 Add FileBrowser toggle button to ChatHeader
    - Add new button with folder icon after ChatHistory button
    - Use `activeSidebar === 'fileBrowser'` for highlight state
    - Call `onOpenSidebar('fileBrowser')` on click
    - Add proper title, aria-label, and aria-pressed attributes
    - _Requirements: 1.3, 2.1, 5.3_

  - [x] 4.4 Write property test for Visual Indicator Consistency
    - **Property 3: Visual Indicator State Consistency**
    - Verify only the active sidebar button is highlighted
    - Verify `isHighlighted(button) === (activeSidebar === button.sidebarId)` for all buttons
    - **Validates: Requirements 2.3, 2.4, 5.1, 5.2, 5.3, 5.4**

- [x] 5. Migrate ChatPage to use new hook
  - [x] 5.1 Replace sidebar state hooks in `desktop/src/pages/ChatPage.tsx`
    - Remove individual `useSidebarState` calls for `chatSidebar`, `fileBrowserSidebar`, `todoRadarSidebar`
    - Add `useRightSidebarGroup` hook with `defaultActive: 'todoRadar'` and width configs
    - Import `RIGHT_SIDEBAR_WIDTH_CONFIGS` from constants
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 3.1_

  - [x] 5.2 Update ChatHeader props in ChatPage
    - Replace `chatSidebarCollapsed`, `todoRadarCollapsed`, `onToggleChatSidebar`, `onToggleTodoRadar`
    - Pass `activeSidebar={rightSidebars.activeSidebar}`
    - Pass `onOpenSidebar={rightSidebars.openSidebar}`
    - _Requirements: 2.1, 2.2_

  - [x] 5.3 Update sidebar rendering logic
    - Replace `!todoRadarSidebar.collapsed` with `rightSidebars.isActive('todoRadar')`
    - Replace `!chatSidebar.collapsed` with `rightSidebars.isActive('chatHistory')`
    - Replace `!fileBrowserSidebar.collapsed` with `rightSidebars.isActive('fileBrowser')`
    - Update width and resize props to use `rightSidebars.widths[sidebarId]`
    - Update `onClose` handlers (can be no-op or removed since toggle handles visibility)
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 6. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.
  - Verify mutual exclusion behavior works correctly
  - Verify default state shows TodoRadarSidebar on app startup
  - Verify visual indicators update correctly when switching sidebars

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate universal correctness properties from the design document
- The migration preserves width persistence while making visibility state ephemeral
- Old localStorage keys for collapsed state are cleaned up on first load
