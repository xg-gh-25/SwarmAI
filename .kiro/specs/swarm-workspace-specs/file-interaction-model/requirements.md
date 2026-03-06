# Requirements Document

## Introduction

This spec defines the complete file interaction model for the SwarmAI desktop app's Workspace Explorer. The goal is to cleanly separate "open/view file" from "attach to chat" gestures, wire the existing but disconnected `FileContextMenu` to the virtualized tree, and bridge the `TreeNode` type to the legacy `FileTreeItem` type used by downstream consumers (FileEditorModal, LayoutContext.attachFile). A future enhancement adds drag-from-explorer-to-chat attachment.

## Glossary

- **Workspace_Explorer**: The middle column of the three-column layout that renders the workspace file tree using a virtualized list (react-window).
- **VirtualizedTree**: The react-window-based component that flattens `TreeNode[]` data and renders rows via `TreeNodeRow`.
- **TreeNodeRow**: The leaf row component rendered by `VirtualizedTree`; receives an `onContextMenu` callback prop.
- **FileContextMenu**: An existing portal-rendered context menu component with actions like "Attach to Chat", "Rename", "Delete", and "Copy Path".
- **FileEditorModal**: A modal dialog that displays file content for viewing/editing; accepts a `FileTreeItem` prop.
- **LayoutContext**: React context providing `attachFile(file: FileTreeItem)`, `attachedFiles`, `removeAttachedFile`, and `clearAttachedFiles`.
- **TreeNode**: The canonical type for tree nodes (`{ name, path, type, children?, gitStatus? }`) defined in `types/index.ts`.
- **FileTreeItem**: The legacy type (`{ id, name, type, path, workspaceId, workspaceName, children?, isSwarmWorkspace? }`) retained for backward compatibility with chat and layout components.
- **ChatDropZone**: A wrapper component around the main chat panel that handles drag-and-drop file attachment from the OS file picker.
- **SwarmWorkspace_Warning**: A confirmation dialog shown before editing files that belong to the system-managed SwarmWorkspace.
- **Chat_Input**: The text input area in the chat panel where users compose messages and see attached file chips.

## Requirements

### Requirement 1: Context Menu Wiring

**User Story:** As a user, I want to right-click any file or folder in the Workspace Explorer and see a context menu, so that I can perform file operations without leaving the tree view.

#### Acceptance Criteria

1. WHEN a user right-clicks a node in the VirtualizedTree, THE VirtualizedTree SHALL open the FileContextMenu at the cursor position with the clicked node's data.
2. THE VirtualizedTree SHALL render the FileContextMenu using a React portal attached to `document.body` so that the menu is not clipped by the react-window scroll container.
3. WHEN the FileContextMenu is open and the user clicks outside the menu or presses Escape, THE FileContextMenu SHALL close.
4. WHEN the FileContextMenu is open and the user scrolls the VirtualizedTree, THE FileContextMenu SHALL close.
5. THE VirtualizedTree SHALL convert the clicked `TreeNode` to a `FileTreeItem` before passing the node data to the FileContextMenu.

### Requirement 2: Context Menu "Open File" Action

**User Story:** As a user, I want a right-click menu option to open a file, so that I have an alternative to double-clicking.

#### Acceptance Criteria

1. WHEN the FileContextMenu is displayed for a file node, THE FileContextMenu SHALL show an "Open File" menu item.
2. WHEN the user selects "Open File" from the FileContextMenu, THE FileContextMenu SHALL invoke the same callback used by double-click to open the FileEditorModal.
3. WHEN the FileContextMenu is displayed for a directory node, THE FileContextMenu SHALL NOT show the "Open File" menu item.

### Requirement 3: Context Menu "Attach to Chat" Action

**User Story:** As a user, I want to right-click a file and attach it to the current chat context, so that I can reference files in my conversation with the AI agent.

#### Acceptance Criteria

1. WHEN the FileContextMenu is displayed for a file node, THE FileContextMenu SHALL show an "Attach to Chat" menu item.
2. WHEN the user selects "Attach to Chat" from the FileContextMenu, THE FileContextMenu SHALL call `LayoutContext.attachFile` with a `FileTreeItem` derived from the selected `TreeNode`.
3. WHEN the user selects "Attach to Chat" for a file that is already in the `attachedFiles` list, THE LayoutContext SHALL not add a duplicate entry.
4. WHEN the FileContextMenu is displayed for a directory node, THE FileContextMenu SHALL NOT show the "Attach to Chat" menu item.

### Requirement 4: TreeNode to FileTreeItem Bridge

**User Story:** As a developer, I want a single utility function that converts a `TreeNode` to a `FileTreeItem`, so that all downstream consumers receive consistent data without duplicated conversion logic.

#### Acceptance Criteria

1. THE Workspace_Explorer module SHALL export a pure function `toFileTreeItem(node: TreeNode): FileTreeItem` that maps `TreeNode` fields to `FileTreeItem` fields.
2. THE `toFileTreeItem` function SHALL set `FileTreeItem.id` to `TreeNode.path`.
3. THE `toFileTreeItem` function SHALL set `FileTreeItem.workspaceId` and `FileTreeItem.workspaceName` to empty strings (the single-workspace model does not use these fields).
4. THE VirtualizedTree SHALL use `toFileTreeItem` for both the double-click handler and the context menu handler, replacing any inline conversion logic.
5. FOR ALL valid TreeNode inputs, calling `toFileTreeItem` and then reading `name`, `path`, and `type` SHALL return values equal to the original TreeNode's `name`, `path`, and `type` (round-trip property on shared fields).

### Requirement 5: Double-Click Opens File (Existing Behavior Preservation)

**User Story:** As a user, I want to double-click a file in the Workspace Explorer to open it in the editor modal, so that I can view and edit file contents.

#### Acceptance Criteria

1. WHEN a user double-clicks a file node in the VirtualizedTree, THE VirtualizedTree SHALL invoke the `onFileDoubleClick` callback with a `FileTreeItem` derived via `toFileTreeItem`.
2. WHEN a user double-clicks a directory node, THE VirtualizedTree SHALL toggle the directory's expanded state and SHALL NOT invoke `onFileDoubleClick`.
3. WHEN the opened file belongs to the SwarmWorkspace, THE ThreeColumnLayout SHALL display the SwarmWorkspace_Warning dialog before opening the FileEditorModal.

### Requirement 6: Single-Click Selects Node (Existing Behavior Preservation)

**User Story:** As a user, I want to single-click a node to select and highlight it in the tree, so that I can see which item is focused.

#### Acceptance Criteria

1. WHEN a user single-clicks a file node, THE TreeNodeRow SHALL set the node as the selected path in ExplorerContext and highlight the row.
2. WHEN a user single-clicks a directory node, THE TreeNodeRow SHALL toggle the directory's expanded state and set the node as the selected path.
3. THE TreeNodeRow SHALL apply a visual highlight (primary color at 20% opacity) to the selected row.

### Requirement 7: Chat Attachment Flow Isolation

**User Story:** As a user, I want the existing chat drag-and-drop attachment flow to continue working unchanged, so that I can still drop files from my OS file picker onto the chat panel.

#### Acceptance Criteria

1. THE ChatDropZone component SHALL continue to handle OS-level drag-and-drop file attachment independently of the Workspace Explorer context menu attachment.
2. THE `useFileAttachment` hook SHALL remain unchanged and SHALL NOT be affected by the new context menu "Attach to Chat" action.
3. WHEN a file is attached via the context menu, THE LayoutContext SHALL add the file to `attachedFiles` using the same `attachFile` method used by ChatDropZone.

### Requirement 8: Context Menu State Management

**User Story:** As a developer, I want the context menu state (open/closed, position, target node) managed in a single place, so that the VirtualizedTree stays clean and the menu renders correctly above the virtualized list.

#### Acceptance Criteria

1. THE VirtualizedTree (or its parent WorkspaceExplorer) SHALL manage context menu state as `{ isOpen: boolean; x: number; y: number; item: FileTreeItem | null }`.
2. WHEN the context menu is open and the user right-clicks a different node, THE VirtualizedTree SHALL close the current menu and open a new one at the new cursor position with the new node's data.
3. WHEN the context menu is open and the user left-clicks anywhere in the tree, THE VirtualizedTree SHALL close the context menu.

### Requirement 9: Drag File from Explorer to Chat (Future Enhancement)

**User Story:** As a user, I want to drag a file from the Workspace Explorer and drop it onto the chat input area to attach it, so that I have a quick gesture for adding context to my conversation.

#### Acceptance Criteria

1. WHEN a user starts dragging a file node from the VirtualizedTree, THE TreeNodeRow SHALL initiate an HTML5 drag operation with the file's path as the drag data.
2. WHEN the user drops a dragged file node onto the Chat_Input area, THE Chat_Input SHALL call `LayoutContext.attachFile` with a `FileTreeItem` derived from the drag data.
3. WHILE a file is being dragged over the Chat_Input area, THE Chat_Input SHALL display a visual drop indicator (e.g., highlighted border).
4. WHEN a user drags a directory node, THE TreeNodeRow SHALL NOT initiate a drag operation.
5. THE drag-from-explorer flow SHALL NOT interfere with the existing ChatDropZone OS-level drag-and-drop flow.

### Requirement 10: Accessibility

**User Story:** As a user who relies on keyboard navigation, I want to access file actions via keyboard shortcuts, so that I can use the Workspace Explorer without a mouse.

#### Acceptance Criteria

1. WHEN a tree node has focus and the user presses Enter, THE TreeNodeRow SHALL perform the same action as double-click (open file or toggle directory).
2. WHEN a tree node has focus and the user presses the context menu key (Shift+F10 or the Menu key), THE VirtualizedTree SHALL open the FileContextMenu for the focused node.
3. THE FileContextMenu SHALL support arrow-key navigation between menu items and Enter to select.
4. WHEN the FileContextMenu is open and the user presses Escape, THE FileContextMenu SHALL close and return focus to the tree node that triggered the menu.
