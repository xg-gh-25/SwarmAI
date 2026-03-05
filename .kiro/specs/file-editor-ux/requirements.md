# Requirements Document

## Introduction

The FileEditorModal is the primary file editing surface in SwarmAI's desktop app. It opens as a modal overlay when users double-click or right-click "Open File" on files in the Workspace Explorer. The current implementation provides basic editing with syntax highlighting, save/cancel flow, and unsaved-changes confirmation. This spec covers six UX improvements to make the modal a best-in-class edit/review panel: file-type icons with git status badges, an "Attach to Chat" button, breadcrumb path display, inline diff view, line numbers, and in-file search.

## Glossary

- **File_Editor_Modal**: The modal overlay component (`FileEditorModal.tsx`) that displays file content for editing with syntax highlighting, save, and cancel actions.
- **Workspace_Explorer**: The left-panel file tree (`WorkspaceExplorer`) that displays the workspace filesystem and supports file selection, context menus, and double-click to open.
- **Tree_Node_Row**: The row renderer (`TreeNodeRow.tsx`) for the explorer tree; contains `fileIcon()`, `fileIconColor()`, and `gitStatusBadge()` helper functions.
- **Layout_Context**: The React context (`LayoutContext.tsx`) that manages layout state including `attachFile()` for adding files to chat context.
- **Breadcrumb_Bar**: A visual path display that splits a file path into clickable-looking segments separated by chevron dividers.
- **Diff_View**: An inline display mode that highlights added and removed lines between the original file content and the current edited content.
- **Search_Bar**: A floating bar within the editor area that supports text search with match highlighting, navigation, and match count display.
- **Git_Status_Badge**: A small colored label (A/M/D/U/R/C) indicating the git status of a file, consistent with the explorer tree badges.
- **Line_Gutter**: A vertical column on the left side of the editor displaying line numbers synchronized with the textarea scroll position.
- **Diff_Algorithm**: A lightweight line-based diff computation (e.g., `diff-match-patch` or custom LCS) that compares original and edited content.

## Requirements

### Requirement 1: File-Type Icon and Git Status Badge in Modal Title Bar

**User Story:** As a user, I want the file editor modal title bar to show a file-type icon and git status badge matching the explorer tree, so that I have immediate visual context about the file I am editing.

#### Acceptance Criteria

1. WHEN the File_Editor_Modal opens, THE File_Editor_Modal SHALL display a file-type icon using the same icon-selection logic as Tree_Node_Row (`fileIcon()` function) in the title bar next to the filename.
2. WHEN the File_Editor_Modal opens, THE File_Editor_Modal SHALL color the file-type icon using the same color-selection logic as Tree_Node_Row (`fileIconColor()` function).
3. WHEN the file has a git status, THE File_Editor_Modal SHALL display a Git_Status_Badge next to the filename using the same badge style (label, color, background) as Tree_Node_Row (`gitStatusBadge()` function).
4. WHEN the file has no git status, THE File_Editor_Modal SHALL display only the file-type icon without a Git_Status_Badge.
5. THE File_Editor_Modal SHALL accept a `gitStatus` prop of type `GitStatus | undefined` to receive the git status from the parent component.

### Requirement 2: Attach to Chat Button

**User Story:** As a user, I want to attach the currently open file to the chat context directly from the editor modal, so that I can quickly reference the file in conversation without closing the modal.

#### Acceptance Criteria

1. THE File_Editor_Modal SHALL display an "Attach to Chat" button in the modal header toolbar area.
2. WHEN the user clicks the "Attach to Chat" button, THE File_Editor_Modal SHALL call `Layout_Context.attachFile()` with a `FileTreeItem` representing the current file.
3. WHEN the file is successfully attached, THE File_Editor_Modal SHALL display a brief visual confirmation (e.g., the button text changes to "Attached" with a checkmark icon) for 2 seconds before reverting to the default state.
4. WHILE the current file is already present in `Layout_Context.attachedFiles`, THE File_Editor_Modal SHALL display the button in a disabled state with the label "Attached" to prevent duplicate attachments.
5. THE File_Editor_Modal SHALL accept `onAttachToChat` as a callback prop so the parent component can wire it to `Layout_Context.attachFile()`.

### Requirement 3: Breadcrumb Path Navigation

**User Story:** As a user, I want the file path displayed as a breadcrumb trail instead of a flat string, so that I can visually parse the file's location in the project hierarchy.

#### Acceptance Criteria

1. THE File_Editor_Modal SHALL display the file path as a Breadcrumb_Bar with each path segment separated by a chevron (›) divider.
2. THE Breadcrumb_Bar SHALL style the last segment (the filename) with bold font weight or primary color to distinguish it from parent directory segments.
3. THE Breadcrumb_Bar SHALL style parent directory segments in muted text color (`var(--color-text-muted)`).
4. THE Breadcrumb_Bar SHALL truncate with an ellipsis on the left side when the full breadcrumb exceeds the available header width, preserving the filename segment.
5. THE Breadcrumb_Bar segments SHALL be non-interactive (no click handlers) since the modal operates on files only, not folder navigation.

### Requirement 4: Diff View Toggle

**User Story:** As a user, I want to toggle a diff view that shows my changes compared to the original content, so that I can review what I have modified before saving.

#### Acceptance Criteria

1. THE File_Editor_Modal SHALL display a "Show Changes" toggle button in the toolbar area.
2. WHILE the content is identical to the original content (no modifications), THE File_Editor_Modal SHALL disable the "Show Changes" button.
3. WHEN the user clicks "Show Changes", THE File_Editor_Modal SHALL compute a line-based diff between `originalContent` and the current `content` using a Diff_Algorithm.
4. WHEN the Diff_View is active, THE File_Editor_Modal SHALL display removed lines with a red background (`var(--color-git-deleted)` at low opacity) and added lines with a green background (`var(--color-git-added)` at low opacity).
5. WHILE the Diff_View is active, THE File_Editor_Modal SHALL make the editor content read-only (the textarea is non-editable).
6. WHEN the user clicks the toggle button while Diff_View is active, THE File_Editor_Modal SHALL switch back to edit mode and restore full editing capability.
7. WHEN the Diff_View is active, THE File_Editor_Modal SHALL expand the modal width from `max-w-4xl` to `max-w-6xl` to accommodate the diff content.
8. THE Diff_View SHALL display line numbers in the gutter for both removed and added lines.
9. THE File_Editor_Modal SHALL use a lightweight Diff_Algorithm (line-based comparison) that does not require a heavy editor dependency like Monaco.

### Requirement 5: Line Numbers

**User Story:** As a user, I want to see line numbers in the editor gutter, so that I can reference specific lines when discussing code or reviewing changes.

#### Acceptance Criteria

1. THE File_Editor_Modal SHALL display a Line_Gutter on the left side of the editor area showing sequential line numbers starting from 1.
2. THE Line_Gutter SHALL scroll vertically in sync with the textarea content so that line numbers always align with their corresponding lines.
3. THE Line_Gutter SHALL style line numbers in muted color (`var(--color-text-muted)`) with a monospace font to avoid competing with the editor content.
4. THE Line_Gutter SHALL have a fixed width sufficient to display the maximum line number (e.g., 4-character width for files up to 9999 lines).
5. WHEN the user places the cursor on a line, THE Line_Gutter SHALL highlight the current line number with a slightly brighter color or background to indicate the active line.
6. THE Line_Gutter SHALL have a subtle right border (`var(--color-border)`) separating it from the editor content area.

### Requirement 6: Search Within File

**User Story:** As a user, I want to search within the file using Cmd+F (or Ctrl+F), so that I can quickly find text in large files without leaving the editor modal.

#### Acceptance Criteria

1. WHEN the user presses Cmd+F (macOS) or Ctrl+F (Windows/Linux) while the File_Editor_Modal is open, THE File_Editor_Modal SHALL display a Search_Bar at the top of the editor area.
2. WHEN the user types in the Search_Bar input, THE File_Editor_Modal SHALL highlight all matching occurrences in the editor content.
3. THE Search_Bar SHALL display the current match index and total match count (e.g., "3 of 12").
4. WHEN the user presses Enter or the down-arrow button in the Search_Bar, THE File_Editor_Modal SHALL navigate to the next match and scroll it into view.
5. WHEN the user presses Shift+Enter or the up-arrow button in the Search_Bar, THE File_Editor_Modal SHALL navigate to the previous match.
6. WHEN the user presses Escape while the Search_Bar is focused, THE File_Editor_Modal SHALL close the Search_Bar and return focus to the textarea.
7. THE Search_Bar SHALL not interfere with the existing Cmd+S / Ctrl+S save keyboard shortcut.
8. IF no matches are found for the search query, THEN THE Search_Bar SHALL display "0 of 0" and apply no highlights to the editor content.
9. THE Search_Bar SHALL perform case-insensitive search by default.
10. WHEN the Search_Bar is closed, THE File_Editor_Modal SHALL remove all search highlights from the editor content.

### Requirement 7: Theme Compatibility

**User Story:** As a user, I want all new editor UI elements to respect the current light/dark theme, so that the editing experience is visually consistent.

#### Acceptance Criteria

1. THE File_Editor_Modal SHALL style all new UI elements (Breadcrumb_Bar, Search_Bar, Line_Gutter, Diff_View, Git_Status_Badge, Attach to Chat button) using CSS variables defined in `index.css`.
2. WHEN the application theme changes between light and dark mode, THE File_Editor_Modal SHALL update all new UI element colors without requiring a modal close/reopen.
3. THE Diff_View added-line and removed-line backgrounds SHALL use the existing git status CSS variables (`--color-git-added`, `--color-git-deleted`) at reduced opacity to maintain readability in both themes.

### Requirement 8: Modal Architecture Constraints

**User Story:** As a developer, I want the editor improvements to work within the existing modal architecture, so that the save/close flow and overlay behavior remain intact.

#### Acceptance Criteria

1. THE File_Editor_Modal SHALL retain the existing overlay behavior (backdrop blur, click-outside-to-close with unsaved-changes guard).
2. THE File_Editor_Modal SHALL retain the existing Cmd+S / Ctrl+S save shortcut and Save/Cancel button flow.
3. THE File_Editor_Modal SHALL not introduce any dependency on Monaco Editor or other heavy editor frameworks.
4. THE File_Editor_Modal SHALL continue to use the existing `hljs` (highlight.js) library for syntax highlighting in edit mode.
5. WHEN the Diff_View is active, THE File_Editor_Modal SHALL disable the Save button since the user cannot edit content in diff mode.
