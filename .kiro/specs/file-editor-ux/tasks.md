# Implementation Plan: File Editor UX Enhancements

## Overview

Incremental implementation of six UX improvements to `FileEditorModal`: file-type icons with git badges, attach-to-chat, breadcrumb path, inline diff view, line numbers, and in-file search. Each task builds on the previous, starting with shared utilities and ending with integration wiring.

## Tasks

- [x] 1. Extract shared file utilities into `desktop/src/utils/fileUtils.ts`
  - [x] 1.1 Create `desktop/src/utils/fileUtils.ts` with `fileIcon()`, `fileIconColor()`, `gitStatusBadge()`, and `gitStatusColor()` extracted from `TreeNodeRow.tsx`
    - Copy the four functions verbatim from `desktop/src/components/workspace-explorer/TreeNodeRow.tsx`
    - Export all four functions; include the `GitStatus` type import
    - Add module-level JSDoc docstring per project conventions
    - _Requirements: 1.1, 1.2, 1.3_

  - [x] 1.2 Update `TreeNodeRow.tsx` to import from `fileUtils.ts`
    - Replace the local `fileIcon`, `fileIconColor`, `gitStatusBadge`, `gitStatusColor` function definitions with imports from `desktop/src/utils/fileUtils`
    - Remove the duplicated function bodies; keep `isHiddenNode` in TreeNodeRow since it's explorer-specific
    - Verify no behavior change — this is a pure refactor
    - _Requirements: 1.1, 1.2, 1.3_

  - [ ]* 1.3 Write property tests for file utility functions
    - **Property 1: File icon and color determinism** — For any filename string, `fileIcon(name)` and `fileIconColor(name)` return the same non-empty string on repeated calls
    - **Validates: Requirements 1.1, 1.2**
    - **Property 2: Git status badge completeness** — For all 7 valid GitStatus values, `gitStatusBadge(status)` returns non-null with non-empty label/color/bg. For `undefined`, returns `null`
    - **Validates: Requirements 1.3, 1.4**
    - Create test file: `desktop/src/utils/fileUtils.property.test.ts`
    - Use `fast-check` with `fc.assert(fc.property(...), { numRuns: 100 })`

- [x] 2. Create line-based diff algorithm in `desktop/src/utils/lineDiff.ts`
  - [x] 2.1 Implement `computeLineDiff(oldText, newText): DiffLine[]` using LCS
    - Create `desktop/src/utils/lineDiff.ts`
    - Define and export `DiffLine` interface: `{ type: 'added' | 'removed' | 'unchanged'; content: string; oldLineNumber?: number; newLineNumber?: number }`
    - Implement line-based LCS diff algorithm — split by `\n`, compute LCS, produce DiffLine array with correct line numbers
    - Add module-level JSDoc docstring
    - _Requirements: 4.3, 4.8, 4.9_

  - [ ]* 2.2 Write property test for diff round-trip correctness
    - **Property 5: Diff algorithm round-trip** — For any two strings, applying the diff (keeping 'unchanged' + 'added', dropping 'removed') reconstructs currentContent; keeping 'unchanged' + 'removed' reconstructs originalContent
    - **Validates: Requirements 4.3, 4.8**
    - Create test file: `desktop/src/utils/lineDiff.property.test.ts`
    - Use `fast-check` arbitrary multiline string generators

- [x] 3. Checkpoint — Verify shared utilities
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [x] 4. Add new props and state to FileEditorModal
  - [x] 4.1 Extend `FileEditorModalProps` and `FileEditorState` interfaces
    - Add to `FileEditorModalProps`: `gitStatus?: GitStatus`, `onAttachToChat?: (item: FileTreeItem) => void`, `isAttached?: boolean`
    - Add to `FileEditorState`: `gitStatus?: GitStatus`
    - Import `GitStatus` and `FileTreeItem` types
    - _Requirements: 1.5, 2.1, 2.5_

  - [x] 4.2 Add new internal state variables to `FileEditorModal` component
    - Add state: `showDiff`, `showSearch`, `searchQuery`, `currentMatchIndex`, `activeLineNumber`, `attachFeedback`
    - These are local `useState` hooks inside the component
    - _Requirements: 4.1, 5.5, 6.1_

- [x] 5. Implement modal header enhancements (icon, badge, breadcrumb, attach)
  - [x] 5.1 Add file-type icon and git status badge to the modal header
    - Import `fileIcon`, `fileIconColor`, `gitStatusBadge` from `fileUtils.ts`
    - Render Material Symbols icon with correct color next to the filename
    - Conditionally render git status badge when `gitStatus` prop is provided
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 5.2 Implement BreadcrumbBar in the modal header
    - Split `filePath` on `/`, render segments separated by `›` chevrons
    - Style last segment (filename) with bold/primary color; parent segments in `var(--color-text-muted)`
    - Implement left-truncation with ellipsis using `direction: rtl` trick when path overflows
    - Segments are non-interactive (no click handlers)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

  - [ ]* 5.3 Write property test for breadcrumb path splitting
    - **Property 4: Breadcrumb path splitting** — For any path with `/` separators, segment count equals number of `/`-separated parts, and last segment equals the basename
    - **Validates: Requirements 3.1**

  - [x] 5.4 Implement Attach to Chat button
    - Render button in header toolbar; call `onAttachToChat` with constructed `FileTreeItem` on click
    - Show "Attached ✓" feedback for 2 seconds via `attachFeedback` state + `setTimeout`
    - Disable button when `isAttached` prop is true (label: "Attached")
    - Don't render button if `onAttachToChat` is undefined
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 5.5 Write property test for attach FileTreeItem construction
    - **Property 3: Attach produces valid FileTreeItem** — For any filePath/fileName/workspaceId, the constructed FileTreeItem has `id=filePath`, `name=fileName`, `type='file'`, `path=filePath`, `workspaceId=workspaceId`
    - **Validates: Requirements 2.2**

- [x] 6. Implement line numbers gutter
  - [x] 6.1 Create LineGutter sub-component inside FileEditorModal
    - Render sequential line numbers 1..N based on content line count
    - Sync vertical scroll with textarea via shared `scrollTop`
    - Fixed width using `ch` units: `Math.max(3, String(lineCount).length) + 1`
    - Highlight active line number (from cursor position) with brighter color
    - Right border separator using `var(--color-border)`
    - Style: monospace font, `var(--color-text-muted)` color
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ]* 6.2 Write property tests for line gutter
    - **Property 7: Line count consistency** — For any content string, line count equals `content.split('\n').length`, numbers are sequential from 1
    - **Validates: Requirements 5.1**
    - **Property 8: Cursor position to line number mapping** — For any content and valid cursor position, active line = newlines before cursor + 1
    - **Validates: Requirements 5.5**

- [x] 7. Checkpoint — Verify header and gutter
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [x] 8. Implement diff view toggle
  - [x] 8.1 Implement DiffView sub-component inside FileEditorModal
    - Render `DiffLine[]` as a `<pre>` block with line-by-line coloring
    - Added lines: green background (`var(--color-git-added)` at ~15% opacity)
    - Removed lines: red background (`var(--color-git-deleted)` at ~15% opacity)
    - Unchanged lines: no background
    - Each line shows old/new line numbers in a gutter column
    - Read-only (no textarea)
    - _Requirements: 4.4, 4.5, 4.8_

  - [x] 8.2 Add "Show Changes" toggle button and diff mode logic
    - Add toggle button in the footer toolbar area
    - Disable button when content === originalContent (no modifications)
    - On toggle on: compute diff via `computeLineDiff`, set `showDiff=true`, replace editor area with DiffView
    - On toggle off: restore textarea editor, set `showDiff=false`
    - When diff active: make editor read-only, disable Save button, expand modal from `max-w-4xl` to `max-w-6xl`
    - Content must remain unchanged through toggle cycle
    - Use `useMemo` for diff computation keyed on `showDiff + originalContent + content`
    - _Requirements: 4.1, 4.2, 4.3, 4.5, 4.6, 4.7, 8.5_

  - [ ]* 8.3 Write property test for diff mode state machine
    - **Property 6: Diff mode state machine** — Toggling diff on sets read-only + save disabled; toggling off restores editable + save enabled. Content unchanged through cycle
    - **Validates: Requirements 4.5, 4.6, 8.5**

- [x] 9. Implement in-file search
  - [x] 9.1 Implement search match computation utility
    - Create `findAllMatches(content: string, query: string): SearchMatch[]` function (can live in FileEditorModal or a small utility)
    - `SearchMatch`: `{ lineIndex: number; startOffset: number; length: number }`
    - Case-insensitive plain string matching (no regex)
    - Return all non-overlapping occurrences; empty query returns empty array
    - _Requirements: 6.2, 6.8, 6.9_

  - [x] 9.2 Implement SearchBar sub-component
    - Floating bar at top of editor area (absolute positioned)
    - Input field + match counter ("3 of 12") + up/down nav buttons + close button
    - Escape closes the bar and returns focus to textarea
    - Must not intercept Cmd+S / Ctrl+S
    - _Requirements: 6.1, 6.3, 6.6, 6.7_

  - [x] 9.3 Wire Cmd+F / Ctrl+F keyboard shortcut and search highlighting
    - Add `keydown` handler: Cmd+F / Ctrl+F opens search bar with `e.preventDefault()`
    - Compute matches via `useMemo` on `searchQuery + content`
    - Highlight all matches in the syntax-highlighted `<pre>` overlay
    - Navigate next (Enter / down-arrow) and previous (Shift+Enter / up-arrow) with scroll-into-view
    - Navigation wraps: next from last → first, previous from first → last
    - On search bar close: remove all highlights
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.10_

  - [ ]* 9.4 Write property tests for search
    - **Property 9: Search match completeness** — For any content and non-empty query, `findAllMatches` returns every case-insensitive non-overlapping occurrence. Empty query returns zero matches
    - **Validates: Requirements 6.2, 6.3, 6.9**
    - **Property 10: Search navigation wrapping** — For any N matches and index i, next yields `(i+1)%N`, previous yields `(i-1+N)%N`. When N=0, navigation has no effect
    - **Validates: Requirements 6.4, 6.5**

- [x] 10. Checkpoint — Verify diff and search
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [x] 11. Wire new props through ThreeColumnLayout
  - [x] 11.1 Update `fileEditorState` in ThreeColumnLayout to include `gitStatus`
    - Extend the `fileEditorState` type to include `gitStatus?: GitStatus`
    - Capture `gitStatus` from the `TreeNode` when the file editor is opened (in the open-file handler)
    - _Requirements: 1.5_

  - [x] 11.2 Pass `onAttachToChat` and `isAttached` props to FileEditorModal
    - Derive `onAttachToChat` from `useLayout().attachFile` — construct `FileTreeItem` and call `attachFile`
    - Derive `isAttached` by checking if `filePath` exists in `useLayout().attachedFiles`
    - Pass `gitStatus` from `fileEditorState` to `FileEditorModal`
    - _Requirements: 2.1, 2.2, 2.4, 2.5_

- [x] 12. Theme compatibility verification
  - [x] 12.1 Ensure all new UI elements use CSS variables from `index.css`
    - Audit all new components (BreadcrumbBar, SearchBar, LineGutter, DiffView, GitStatusBadge, AttachButton) for hardcoded colors
    - Replace any hardcoded values with appropriate CSS variables (`--color-text-muted`, `--color-border`, `--color-git-added`, `--color-git-deleted`, etc.)
    - Verify theme reactivity — CSS variable usage ensures automatic light/dark switching without modal close/reopen
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 13. Final checkpoint — Full integration verification
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.
  - Verify existing save/close flow, backdrop blur, unsaved-changes guard, Cmd+S shortcut all still work
  - Verify no Monaco or heavy editor dependency was introduced
  - _Requirements: 8.1, 8.2, 8.3, 8.4_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests validate universal correctness properties from the design document
- Checkpoints ensure incremental validation at natural breakpoints
- All code is TypeScript/React; tests use Vitest + fast-check
- Run tests with: `cd desktop && npm test -- --run`
