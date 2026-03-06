# Implementation Plan: File Interaction Model

## Overview

Wire the existing `FileContextMenu` to the `VirtualizedTree` via portal rendering, create a `toFileTreeItem` bridge utility, add "Open File" context menu action, thread `onAttachToChat` from `ThreeColumnLayout` through to the context menu, and add keyboard accessibility. The existing inline `TreeNode→FileTreeItem` conversion in `RowRenderer` is replaced by the shared bridge function. Drag-from-explorer-to-chat (Requirement 9) is deferred as optional/future.

## Tasks

- [x] 1. Create `toFileTreeItem` bridge utility
  - [x] 1.1 Create `desktop/src/components/workspace-explorer/toFileTreeItem.ts`
    - Export a pure function `toFileTreeItem(node: TreeNode): FileTreeItem`
    - Set `id` to `node.path`, `workspaceId` and `workspaceName` to `''`
    - Recursively map `children` via optional chaining
    - _Requirements: 4.1, 4.2, 4.3, 4.5_

  - [x] 1.2 Replace inline conversion in `VirtualizedTree.tsx` `RowRenderer`
    - Import `toFileTreeItem` and replace the inline `FileTreeItem` object literal in `handleDoubleClick`
    - _Requirements: 4.4, 5.1_

  - [ ]* 1.3 Write property tests for `toFileTreeItem` (Property 1 & 2)
    - **Property 1: toFileTreeItem shared field round-trip**
    - **Property 2: toFileTreeItem invariant fields**
    - Create `desktop/src/components/workspace-explorer/__tests__/toFileTreeItem.test.ts`
    - Use `fast-check` to generate arbitrary `TreeNode` objects and verify field mapping
    - **Validates: Requirements 4.2, 4.3, 4.5**

- [x] 2. Wire context menu state and portal rendering in VirtualizedTree
  - [x] 2.1 Add `ContextMenuState` and `onAttachToChat` to `VirtualizedTree`
    - Add `onAttachToChat?: (item: FileTreeItem) => void` to `VirtualizedTreeProps`
    - Add `ContextMenuState` interface and `useState` in `VirtualizedTree`
    - Implement `handleContextMenu(e, node)` that calls `toFileTreeItem` and sets state
    - Pass `onContextMenu` through `RowCustomProps` to `RowRenderer` → `TreeNodeRow`
    - _Requirements: 1.1, 1.5, 8.1_

  - [x] 2.2 Render `FileContextMenu` via portal in `VirtualizedTree`
    - Import `createPortal` from `react-dom` and `FileContextMenu`
    - When `contextMenu.isOpen && contextMenu.item`, render `FileContextMenu` via `createPortal(..., document.body)`
    - Pass `onOpenFile` (reusing `onFileDoubleClick`), `onAttachToChat`, and `onClose` to `FileContextMenu`
    - _Requirements: 1.2, 2.2, 3.2_

  - [x] 2.3 Close context menu on scroll, outside click, and re-right-click
    - Add `useEffect` to listen for scroll events on the `List` container and close the menu
    - Existing `FileContextMenu` handles click-outside and Escape — verify integration
    - Right-clicking a different node replaces the current menu target (same setState call)
    - _Requirements: 1.3, 1.4, 8.2, 8.3_

  - [ ]* 2.4 Write property test for right-click replaces context menu target (Property 7)
    - **Property 7: Right-click replaces context menu target**
    - Create `desktop/src/components/workspace-explorer/__tests__/contextMenu.test.tsx`
    - **Validates: Requirements 8.2**

- [x] 3. Add "Open File" action to FileContextMenu
  - [x] 3.1 Add `onOpenFile` prop and "Open File" menu item to `FileContextMenu`
    - Add `onOpenFile?: (item: FileTreeItem) => void` to `FileContextMenuProps`
    - Add "Open File" menu item before "Attach to Chat", gated to `item.type === 'file'`
    - Menu order for files: Open File → Attach to Chat → divider → Rename → Delete → divider → Copy Path
    - Menu order for directories: Rename → Delete → divider → Copy Path (no Open File, no Attach to Chat)
    - _Requirements: 2.1, 2.3, 3.1, 3.4_

  - [ ]* 3.2 Write property test for directory nodes hide file-only menu items (Property 4)
    - **Property 4: Directory nodes hide file-only menu items**
    - Add to `desktop/src/components/workspace-explorer/__tests__/contextMenu.test.tsx`
    - **Validates: Requirements 2.3, 3.4**

- [x] 4. Checkpoint — Verify context menu wiring
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Thread `onAttachToChat` from ThreeColumnLayout through to VirtualizedTree
  - [x] 5.1 Add `onAttachToChat` prop to `WorkspaceExplorerProps` and pass through
    - Add `onAttachToChat?: (item: FileTreeItem) => void` to `WorkspaceExplorerProps`
    - Pass it through `AutoSizer` → `VirtualizedTree`
    - _Requirements: 3.2, 7.3_

  - [x] 5.2 Wire `attachFile` from `LayoutContext` in `ThreeColumnLayout`
    - Import `useLayout` and destructure `attachFile`
    - Pass `attachFile` as `onAttachToChat` to `WorkspaceExplorer`
    - _Requirements: 3.2, 7.1, 7.2, 7.3_

  - [ ]* 5.3 Write property test for attachFile idempotence (Property 3)
    - **Property 3: attachFile idempotence**
    - Create `desktop/src/components/workspace-explorer/__tests__/attachFile.test.ts`
    - Use `fast-check` to verify calling `attachFile` multiple times with the same item results in at most one entry
    - **Validates: Requirements 3.3**

- [x] 6. Add keyboard accessibility
  - [x] 6.1 Add `onKeyDown` handler to `TreeNodeRow`
    - Enter key → same action as double-click (open file or toggle directory)
    - Shift+F10 / ContextMenu key → trigger `onContextMenu` with synthetic position
    - _Requirements: 10.1, 10.2_

  - [x] 6.2 Add arrow-key navigation and focus return to `FileContextMenu`
    - Arrow keys navigate between menu items via focus management
    - Enter selects focused menu item
    - On Escape, return focus to the triggering tree node via `returnFocusRef` pattern
    - _Requirements: 10.3, 10.4_

  - [ ]* 6.3 Write property test for Enter key equivalence (Property 9)
    - **Property 9: Enter key equivalence with double-click**
    - Create `desktop/src/components/workspace-explorer/__tests__/treeInteractions.test.tsx`
    - **Validates: Requirements 10.1**

- [x] 7. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. (Optional/Future) Drag file from explorer to chat
  - [ ] 8.1 Add `draggable` attribute and drag handlers to `TreeNodeRow` for file nodes only
    - Set `draggable={true}` for file nodes, `draggable={false}` for directories
    - On `dragStart`, set drag data to the file's path
    - _Requirements: 9.1, 9.4_

  - [ ] 8.2 Add drop handler to Chat_Input area
    - On drop, parse drag data and call `LayoutContext.attachFile` with derived `FileTreeItem`
    - Show visual drop indicator while dragging over Chat_Input
    - Ensure no interference with existing `ChatDropZone` OS-level drag-and-drop
    - _Requirements: 9.2, 9.3, 9.5_

  - [ ]* 8.3 Write property test for drag initiation is file-only (Property 8)
    - **Property 8: Drag initiation is file-only**
    - Create `desktop/src/components/workspace-explorer/__tests__/dragAndDrop.test.tsx`
    - **Validates: Requirements 9.1, 9.4**

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Task 8 (drag-from-explorer-to-chat) is a future enhancement per Requirement 9 and can be deferred
- The existing `useFileAttachment` hook and `ChatDropZone` are intentionally untouched (Requirement 7)
- Run tests with: `cd desktop && npm test -- --run`
