# Requirements Document

## Introduction

This feature completes the end-to-end file interaction experience in the Workspace Explorer. Two gaps exist after the file-attachment-e2e spec: (1) double-clicking binary files (images, PDFs) in the explorer opens the text-based FileEditorModal, which shows garbled content instead of a proper viewer, and (2) the TreeNodeRow component lost drag support during the virtualized-tree refactor, preventing users from dragging files from the explorer into the chat panel. This spec addresses both gaps — binary file preview and drag-to-chat — while preserving existing text-file editing behavior.

## Glossary

- **Explorer**: The Workspace Explorer panel rendered by `VirtualizedTree` and `TreeNodeRow`, displaying the file tree for all mounted workspaces.
- **TreeNodeRow**: The virtualized row component (`TreeNodeRow.tsx`) that renders a single file or directory node in the Explorer.
- **FileEditorModal**: The existing modal (`FileEditorModal.tsx`) that opens text files for viewing and editing with syntax highlighting.
- **BinaryPreviewModal**: A new modal component that displays non-text files (images, PDFs) with format-appropriate viewers.
- **ChatDropZone**: The drop-target wrapper (`ChatDropZone.tsx`) around the chat panel that accepts dragged files and routes them to the attachment pipeline.
- **FileTreeItem**: The interface representing a file or directory node, used as the JSON payload format for workspace drag-and-drop operations.
- **Workspace_API**: The Python FastAPI backend router (`workspace_api.py`) that serves file content via `GET /workspace/file`.
- **Attachment_Pipeline**: The unified file attachment system (from file-attachment-e2e) that processes files into chat context, including `addWorkspaceFiles` and `addFiles` callbacks.

## Requirements

### Requirement 1: Binary File Type Detection

**User Story:** As a user, I want the Explorer to detect whether a file is an image, PDF, text, or unsupported binary, so that double-clicking opens the correct viewer.

#### Acceptance Criteria

1. WHEN a user double-clicks a file in the Explorer, THE Explorer SHALL determine the file type by matching the file extension against known image extensions (png, jpg, jpeg, gif, webp, svg, bmp, ico), PDF extension (pdf), and known text extensions.
2. WHEN the file extension matches an image format, THE Explorer SHALL open the BinaryPreviewModal in image-viewer mode.
3. WHEN the file extension matches PDF, THE Explorer SHALL open the BinaryPreviewModal in PDF-viewer mode.
4. WHEN the file extension matches a known text format or has no extension matching a known binary format, THE Explorer SHALL open the FileEditorModal as it does today.
5. IF the file extension matches an unsupported binary format (e.g., mp4, mp3, docx, xlsx), THEN THE BinaryPreviewModal SHALL display the file name, file type badge, and a message stating the file type cannot be previewed, along with a "Reveal in Finder" button.

### Requirement 2: Backend Binary File Serving

**User Story:** As a frontend component, I want the Workspace API to serve binary file content as base64 with MIME type metadata, so that the BinaryPreviewModal can render images and PDFs.

#### Acceptance Criteria

1. WHEN a request is made to `GET /workspace/file` for a file that is not valid UTF-8, THE Workspace_API SHALL return the file content encoded as base64, the detected MIME type, the encoding value "base64", and the file size in bytes.
2. WHEN a request is made to `GET /workspace/file` for a valid UTF-8 text file, THE Workspace_API SHALL continue to return the content as UTF-8 text with encoding value "utf-8" (preserving existing behavior).
3. IF the requested file exceeds 50 MB, THEN THE Workspace_API SHALL return HTTP 413 with a descriptive error message indicating the file is too large to preview.
4. THE Workspace_API SHALL detect MIME types using the file extension (via Python `mimetypes` module) rather than reading file content for type detection.

### Requirement 3: Image Preview

**User Story:** As a user, I want to view images directly in the app when I double-click them in the Explorer, so that I can inspect screenshots and visual assets without leaving the application.

#### Acceptance Criteria

1. WHEN the BinaryPreviewModal opens in image-viewer mode, THE BinaryPreviewModal SHALL render the image using a base64 data URI with the correct MIME type.
2. THE BinaryPreviewModal SHALL display the file name in the modal title bar.
3. WHEN the image is displayed, THE BinaryPreviewModal SHALL allow the user to zoom in and zoom out using mouse wheel scroll or pinch gesture.
4. WHEN the image is zoomed in, THE BinaryPreviewModal SHALL allow the user to pan the image by clicking and dragging.
5. THE BinaryPreviewModal SHALL display the image dimensions (width × height) and file size below the image.
6. WHEN the user presses Escape or clicks the close button, THE BinaryPreviewModal SHALL close and return focus to the Explorer.
7. THE BinaryPreviewModal SHALL fit the image within the viewport on initial load, scaling down large images while preserving aspect ratio.

### Requirement 4: PDF Preview

**User Story:** As a user, I want to view PDF documents directly in the app when I double-click them in the Explorer, so that I can read reports and documentation without switching to an external application.

#### Acceptance Criteria

1. WHEN the BinaryPreviewModal opens in PDF-viewer mode, THE BinaryPreviewModal SHALL render the PDF document using a PDF rendering library (e.g., react-pdf or pdf.js).
2. THE BinaryPreviewModal SHALL display the file name in the modal title bar.
3. WHEN the PDF has multiple pages, THE BinaryPreviewModal SHALL allow the user to scroll through all pages vertically.
4. THE BinaryPreviewModal SHALL display the current page number and total page count.
5. WHEN the user presses Escape or clicks the close button, THE BinaryPreviewModal SHALL close and return focus to the Explorer.
6. IF the PDF fails to render (corrupted or password-protected), THEN THE BinaryPreviewModal SHALL display an error message and a "Reveal in Finder" button.

### Requirement 5: Explorer Drag-to-Chat Support

**User Story:** As a user, I want to drag files from the Workspace Explorer into the chat panel, so that I can quickly attach files as context for my conversation.

#### Acceptance Criteria

1. THE TreeNodeRow SHALL set the `draggable` attribute to `true` for file nodes (type "file") and `false` for directory nodes.
2. WHEN the user initiates a drag on a file node, THE TreeNodeRow SHALL set the drag data to `application/json` format containing a serialized FileTreeItem object with the node's id, name, type, path, workspaceId, and workspaceName fields.
3. WHEN a drag starts, THE TreeNodeRow SHALL set the drag effect to "copy".
4. WHEN the user drags a file over the ChatDropZone, THE ChatDropZone SHALL display the existing drop overlay with "Drop to attach" messaging (no change to ChatDropZone required).
5. WHEN the user drops a file from the Explorer onto the ChatDropZone, THE ChatDropZone SHALL parse the `application/json` payload and route the FileTreeItem to the `addWorkspaceFiles` callback (existing behavior).
6. THE TreeNodeRow drag implementation SHALL preserve the existing click, double-click, context-menu, and keyboard event handlers without interference.

### Requirement 6: Drag Visual Feedback

**User Story:** As a user, I want clear visual feedback when dragging a file from the Explorer, so that I know the drag operation is active and where I can drop.

#### Acceptance Criteria

1. WHEN a drag starts on a TreeNodeRow, THE TreeNodeRow SHALL display a drag ghost image showing the file icon and file name.
2. WHILE a drag is in progress, THE cursor SHALL display the "copy" cursor icon to indicate a copy operation.
3. WHILE a drag is in progress over the ChatDropZone, THE ChatDropZone SHALL continue to show its existing drop overlay (no change required).

### Requirement 7: Backward Compatibility

**User Story:** As a user, I want existing text-file editing to continue working unchanged, so that the new binary preview and drag features do not break my current workflow.

#### Acceptance Criteria

1. WHEN a user double-clicks a text file (e.g., .py, .ts, .json, .md), THE Explorer SHALL open the FileEditorModal with syntax highlighting and editing capabilities, identical to current behavior.
2. WHEN a user double-clicks a Swarm Workspace file, THE Explorer SHALL show the Swarm Workspace warning dialog before opening, identical to current behavior.
3. THE Explorer SHALL preserve the existing virtualized tree scrolling performance with no measurable degradation from the addition of drag attributes.
4. THE Explorer SHALL preserve all existing keyboard navigation, context menu, and selection behaviors on TreeNodeRow.

### Requirement 8: Accessibility

**User Story:** As a user relying on assistive technology, I want the binary preview modals and drag interactions to be accessible, so that I can use all features with keyboard and screen reader.

#### Acceptance Criteria

1. THE BinaryPreviewModal SHALL be keyboard-navigable: Escape to close, Tab to cycle focusable elements.
2. THE BinaryPreviewModal SHALL set appropriate ARIA attributes: `role="dialog"`, `aria-modal="true"`, and `aria-label` with the file name.
3. WHEN an image is displayed, THE BinaryPreviewModal SHALL include an `alt` attribute on the image element containing the file name.
4. WHEN a PDF is displayed, THE BinaryPreviewModal SHALL provide an accessible label indicating the document name and current page.
5. THE TreeNodeRow drag interaction SHALL not interfere with existing `role="treeitem"` and `aria-selected` attributes.
6. IF a file type cannot be previewed, THEN THE BinaryPreviewModal SHALL announce the "cannot preview" status to screen readers via an `aria-live` region or equivalent.
