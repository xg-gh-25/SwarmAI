# Implementation Plan: Chat History Sidebar Relocation

## Overview

Relocate the Chat History sidebar from the left side to the right side of the Chat panel, consolidating all sidebars on the right. This involves renaming the component, updating layout positioning, modifying visual styling, and updating the resize logic.

## Tasks

- [x] 1. Rename ChatSidebar component to ChatHistorySidebar
  - [x] 1.1 Rename file from `ChatSidebar.tsx` to `ChatHistorySidebar.tsx`
    - Rename `desktop/src/pages/chat/components/ChatSidebar.tsx` to `ChatHistorySidebar.tsx`
    - Update component function name from `ChatSidebar` to `ChatHistorySidebar`
    - Update interface name from `ChatSidebarProps` to `ChatHistorySidebarProps`
    - _Requirements: 1.1_
  
  - [x] 1.2 Update barrel export in index.ts
    - Change `export { ChatSidebar } from './ChatSidebar'` to `export { ChatHistorySidebar } from './ChatHistorySidebar'`
    - _Requirements: 1.1_
  
  - [x] 1.3 Update import in ChatPage.tsx
    - Update import statement to use `ChatHistorySidebar` instead of `ChatSidebar`
    - _Requirements: 1.1_

- [x] 2. Update ChatHistorySidebar styling for right-side positioning
  - [x] 2.1 Change border from right to left
    - Replace `border-r` with `border-l` in the component's root div className
    - _Requirements: 2.2_
  
  - [x] 2.2 Update resize handle position
    - Change resize handle from `right-0` to `left-0`
    - Change resize handle hitbox from `-right-1` to `-left-1`
    - _Requirements: 2.1, 2.3_

- [x] 3. Update useSidebarState hook for position-aware resizing
  - [x] 3.1 Add position parameter to SidebarConfig interface
    - Add `position?: 'left' | 'right'` to the `SidebarConfig` interface
    - Default to `'left'` for backward compatibility
    - _Requirements: 2.3_
  
  - [x] 3.2 Update resize calculation logic
    - Modify `handleMouseMove` to use `config.position === 'right'` instead of `storageKey.includes('right')`
    - _Requirements: 2.3_
  
  - [x] 3.3 Write property test for right-side resize direction
    - **Property 1: Right-Side Resize Direction**
    - **Validates: Requirements 2.3**

- [x] 4. Relocate ChatHistorySidebar in ChatPage layout
  - [x] 4.1 Move ChatHistorySidebar rendering position
    - Move `ChatHistorySidebar` from before the main chat area to after it
    - Position between TodoRadarSidebar and FileBrowserSidebar
    - Order: Main Chat Area → TodoRadarSidebar → ChatHistorySidebar → FileBrowserSidebar
    - _Requirements: 1.1, 1.2, 1.3_
  
  - [x] 4.2 Update chatSidebar hook usage with position parameter
    - Add `position: 'right'` to the `useSidebarState` config for `chatSidebar`
    - _Requirements: 2.3, 3.1, 3.2, 3.3_
  
  - [x] 4.3 Write property test for state persistence round-trip
    - **Property 2: Sidebar State Persistence Round-Trip**
    - **Validates: Requirements 3.2, 3.3**

- [x] 5. Checkpoint - Verify implementation
  - Ensure all tests pass, ask the user if questions arise.
  - Verify sidebar appears on right side
  - Verify resize handle works correctly (drag left = wider, drag right = narrower)
  - Verify state persistence works (collapsed state and width)

- [x] 6. Update documentation
  - [x] 6.1 Update CHAT_ARCHITECTURE.md
    - Update component hierarchy diagram to show new sidebar position
    - Update any references to ChatSidebar to use ChatHistorySidebar
    - Document the new layout structure
    - _Requirements: 1.1, 1.2_

- [x] 7. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- The localStorage keys (`chatSidebarCollapsed`, `chatSidebarWidth`) remain unchanged to preserve user preferences
- The sidebar order in the right area is: TodoRadarSidebar → ChatHistorySidebar → FileBrowserSidebar
