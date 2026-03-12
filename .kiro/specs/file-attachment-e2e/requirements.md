# Requirements Document

## Introduction

The SwarmAI desktop app currently has three independent file attachment input paths (File Picker/Clipboard, Workspace Explorer drag-drop, and OS Finder drag-drop) with two separate delivery mechanisms. Two of the three paths are broken end-to-end: workspace drag-drop displays chips but never includes content in the message, and text/code files are uploaded to the workspace requiring an unnecessary tool call. This feature unifies all input paths into a single pipeline so every attached file actually reaches Claude with the correct content block type (image, PDF, text, or path hint), eliminating dead-end UI flows and wasted tool calls.

## Glossary

- **Unified_Attachment_Pipeline**: The single processing pipeline that normalizes files from all input sources into typed content blocks for the backend message payload.
- **Content_Block**: A structured unit within the message payload sent to the backend. Types include `image` (base64), `document` (base64 PDF), `text` (inline file content), and `path_hint` (workspace path reference for large files).
- **File_Picker**: The native file selection dialog triggered by the attachment button in ChatInput, handled by `useFileAttachment`.
- **Workspace_Explorer**: The file tree panel in the left sidebar that allows browsing workspace files, handled by `LayoutContext.attachFile`.
- **ChatDropZone**: The drop target wrapper around the chat panel that accepts dragged files from Workspace Explorer and OS Finder.
- **Attachment_Chip**: A visual pill/tag displayed in ChatInput representing an attached file, showing filename, type icon, and a remove button.
- **ChatInput**: The message composition area at the bottom of the chat panel where users type messages and see Attachment_Chips.
- **Backend**: The Python FastAPI sidecar process that receives chat messages and forwards them to Claude via the Claude Agent SDK.
- **Claude_SDK_Client**: The ClaudeSDKClient wrapper that sends structured message payloads (including content blocks) to Claude via Bedrock.
- **Size_Threshold**: The configurable file size limit (default 50 KB for text files) above which a file is delivered as a path hint instead of inline content.
- **Inline_Delivery**: Sending file content directly within the message payload as a text Content_Block, avoiding the need for Claude to use a Read tool call.
- **Path_Hint_Delivery**: Saving a file to the workspace and including only its path in the message, requiring Claude to use a Read tool call to access the content.
- **MIME_Type**: The media type identifier (e.g., `image/png`, `application/pdf`, `text/plain`) used to classify files for delivery strategy selection.
- **Extension_Fallback**: A secondary file type detection mechanism that uses file extension when MIME type is unavailable or generic (e.g., `application/octet-stream`).

## Requirements

### Requirement 1: Unified Attachment Pipeline

**User Story:** As a user, I want all file attachment methods (File Picker, Workspace Explorer drag-drop, OS Finder drag-drop) to go through a single processing pipeline, so that every attached file reaches Claude regardless of how I attached it.

#### Acceptance Criteria

1. WHEN a file is selected via File_Picker, THE Unified_Attachment_Pipeline SHALL process the file and produce a typed Content_Block for the message payload.
2. WHEN a file is dragged from Workspace_Explorer and dropped on ChatDropZone, THE Unified_Attachment_Pipeline SHALL process the file and produce a typed Content_Block for the message payload.
3. WHEN a file is dragged from the operating system file manager and dropped on ChatDropZone, THE Unified_Attachment_Pipeline SHALL process the file and produce a typed Content_Block for the message payload.
4. WHEN a file is pasted from the clipboard, THE Unified_Attachment_Pipeline SHALL process the file and produce a typed Content_Block for the message payload.
5. FOR ALL input sources, THE Unified_Attachment_Pipeline SHALL produce identical Content_Block output for the same file content, regardless of the input source used.

### Requirement 2: Content Block Type Selection

**User Story:** As a user, I want each attached file to be delivered to Claude in the optimal format for its type, so that Claude can process images visually, read PDFs natively, and access text content without extra tool calls.

#### Acceptance Criteria

1. WHEN an image file (MIME type `image/png`, `image/jpeg`, `image/gif`, `image/webp`) is processed, THE Unified_Attachment_Pipeline SHALL produce an `image` Content_Block containing base64-encoded data and the source MIME type.
2. WHEN a PDF file (MIME type `application/pdf`) is processed, THE Unified_Attachment_Pipeline SHALL produce a `document` Content_Block containing base64-encoded data.
3. WHEN a text or code file (MIME type `text/*` or recognized code extension) with size at or below Size_Threshold is processed, THE Unified_Attachment_Pipeline SHALL produce a `text` Content_Block containing the file content inline.
4. WHEN a text or code file with size above Size_Threshold is processed, THE Unified_Attachment_Pipeline SHALL save the file to the workspace and produce a `path_hint` Content_Block containing the workspace file path.
5. WHEN a CSV file is processed, THE Unified_Attachment_Pipeline SHALL produce a `text` Content_Block containing the CSV content inline if the file is at or below Size_Threshold, or a `path_hint` Content_Block if above.

### Requirement 3: Workspace Explorer Drag-Drop Delivery

**User Story:** As a user, I want files I drag from the Workspace Explorer to actually be included in my message to Claude, so that the attachment chips are not just visual decoration.

#### Acceptance Criteria

1. WHEN a file is dragged from Workspace_Explorer and dropped on ChatDropZone, THE ChatDropZone SHALL pass the file metadata to the Unified_Attachment_Pipeline.
2. WHEN a workspace file is passed to the Unified_Attachment_Pipeline, THE Unified_Attachment_Pipeline SHALL read the file content from disk using the file path from the FileTreeItem metadata.
3. WHEN a workspace file has been processed by the Unified_Attachment_Pipeline, THE ChatInput SHALL display an Attachment_Chip for the file AND the message payload SHALL include the corresponding Content_Block.
4. IF a workspace file cannot be read from disk (file deleted, permission denied), THEN THE Unified_Attachment_Pipeline SHALL display an error message on the Attachment_Chip and exclude the file from the message payload.

### Requirement 4: OS Finder Drag-Drop Support

**User Story:** As a user, I want to drag files directly from my operating system file manager (Finder, Explorer, Nautilus) into the chat panel, so that I can attach files without using the File Picker dialog.

#### Acceptance Criteria

1. WHEN a file is dragged from the operating system file manager and enters the ChatDropZone, THE ChatDropZone SHALL display a visual drop overlay indicating the drop target is active.
2. WHEN a file is dropped from the operating system file manager onto ChatDropZone, THE ChatDropZone SHALL extract the file from the native drag event DataTransfer and pass it to the Unified_Attachment_Pipeline.
3. WHEN multiple files are dropped from the operating system file manager in a single drop event, THE ChatDropZone SHALL pass each file to the Unified_Attachment_Pipeline individually.
4. IF a dropped file has an unsupported file type, THEN THE Unified_Attachment_Pipeline SHALL reject the file and display an error message indicating the file type is not supported.

### Requirement 5: MIME Type Detection with Extension Fallback

**User Story:** As a user, I want the system to correctly identify file types even when the MIME type is missing or generic, so that my code files and text files are handled properly.

#### Acceptance Criteria

1. WHEN a file has a recognized MIME type, THE Unified_Attachment_Pipeline SHALL use the MIME type to determine the Content_Block type.
2. WHEN a file has a missing or generic MIME type (`application/octet-stream`, empty string), THE Unified_Attachment_Pipeline SHALL use Extension_Fallback to determine the Content_Block type based on the file extension.
3. THE Extension_Fallback SHALL recognize common code file extensions (`.ts`, `.tsx`, `.js`, `.jsx`, `.py`, `.rs`, `.go`, `.java`, `.c`, `.cpp`, `.h`, `.rb`, `.sh`, `.yaml`, `.yml`, `.toml`, `.json`, `.xml`, `.html`, `.css`, `.scss`, `.sql`, `.md`, `.txt`, `.log`, `.env`, `.cfg`, `.ini`, `.conf`) as text type.
4. THE Extension_Fallback SHALL recognize image extensions (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`) as image type.
5. THE Extension_Fallback SHALL recognize `.pdf` as PDF type.
6. IF a file has neither a recognized MIME type nor a recognized extension, THEN THE Unified_Attachment_Pipeline SHALL reject the file and display an error message indicating the file type is not supported.

### Requirement 6: File Size Validation and Limits

**User Story:** As a user, I want clear feedback when my attached files exceed size limits, so that I understand why an attachment was rejected and can take corrective action.

#### Acceptance Criteria

1. THE Unified_Attachment_Pipeline SHALL enforce a maximum file size of 5 MB for image files.
2. THE Unified_Attachment_Pipeline SHALL enforce a maximum file size of 10 MB for PDF files.
3. THE Unified_Attachment_Pipeline SHALL enforce a maximum file size of 1 MB for text and code files delivered via Inline_Delivery.
4. WHEN a text or code file exceeds Size_Threshold (50 KB) but is at or below 1 MB, THE Unified_Attachment_Pipeline SHALL use Path_Hint_Delivery instead of Inline_Delivery.
5. IF a file exceeds the maximum size limit for its type, THEN THE Unified_Attachment_Pipeline SHALL reject the file and display an error message stating the file size limit and the actual file size.
6. THE Unified_Attachment_Pipeline SHALL enforce a maximum of 10 attachments per message.
7. IF a user attempts to attach more than 10 files, THEN THE Unified_Attachment_Pipeline SHALL reject the additional files and display an error message indicating the attachment count limit.

### Requirement 7: Attachment Chip Display

**User Story:** As a user, I want to see a visual representation of each attached file in the chat input area, so that I can review and manage my attachments before sending.

#### Acceptance Criteria

1. WHEN a file is successfully processed by the Unified_Attachment_Pipeline, THE ChatInput SHALL display an Attachment_Chip showing the file name and a type-appropriate icon.
2. WHEN an image file is attached, THE Attachment_Chip SHALL display a thumbnail preview of the image.
3. WHEN a text or code file is attached, THE Attachment_Chip SHALL display a truncated preview of the first 200 characters of the file content.
4. WHEN a user clicks the remove button on an Attachment_Chip, THE Unified_Attachment_Pipeline SHALL remove the file from the attachment list and THE ChatInput SHALL remove the corresponding Attachment_Chip.
5. WHILE a file is being processed (reading, encoding), THE Attachment_Chip SHALL display a loading indicator.
6. IF a file fails processing, THEN THE Attachment_Chip SHALL display an error state with the error message.

### Requirement 8: Backend Content Block Handling

**User Story:** As a developer, I want the backend to correctly forward all content block types to Claude via the SDK, so that multimodal content (images, PDFs, text) is delivered to the model.

#### Acceptance Criteria

1. WHEN a message payload containing `image` Content_Blocks is received, THE Backend SHALL forward the base64 image data as an image content block to Claude_SDK_Client.
2. WHEN a message payload containing `document` Content_Blocks is received, THE Backend SHALL forward the base64 PDF data as a document content block to Claude_SDK_Client.
3. WHEN a message payload containing `text` Content_Blocks is received, THE Backend SHALL forward the text content as a text content block to Claude_SDK_Client.
4. WHEN a message payload containing `path_hint` Content_Blocks is received, THE Backend SHALL include the file path reference as a text content block with a contextual prefix indicating the file location.
5. IF the Claude_SDK_Client rejects a content block (unsupported type, size exceeded), THEN THE Backend SHALL return a descriptive error to the frontend indicating which attachment failed and the reason.

### Requirement 9: Message Payload Construction

**User Story:** As a user, I want my text message and all attachments to be sent together as a single coherent message, so that Claude receives the full context of my request.

#### Acceptance Criteria

1. WHEN a user sends a message with attachments, THE Unified_Attachment_Pipeline SHALL construct a message payload containing the user text as a text Content_Block followed by all attachment Content_Blocks.
2. WHEN a user sends a message with multiple attachments of mixed types, THE Unified_Attachment_Pipeline SHALL include all Content_Blocks in a single message payload preserving the order in which files were attached.
3. WHEN a message is sent successfully, THE Unified_Attachment_Pipeline SHALL clear all Attachment_Chips from ChatInput.
4. IF any attachment in the message payload fails validation at send time, THEN THE Unified_Attachment_Pipeline SHALL prevent the message from being sent and display an error identifying the failing attachment.

### Requirement 10: Content Validation Limits

**User Story:** As a developer, I want the backend to enforce content validation limits on incoming messages, so that excessively large payloads do not cause timeouts or memory issues.

#### Acceptance Criteria

1. THE Backend SHALL enforce a maximum of 20 content blocks per message (including the user text block).
2. THE Backend SHALL enforce a maximum total payload size of 25 MB per message.
3. IF a message exceeds the content block count limit, THEN THE Backend SHALL return an error response with HTTP status 413 and a message indicating the block count limit.
4. IF a message exceeds the total payload size limit, THEN THE Backend SHALL return an error response with HTTP status 413 and a message indicating the size limit.

### Requirement 11: Text File Inline Delivery Optimization

**User Story:** As a user, I want small text and code files to be sent directly to Claude without requiring a tool call to read them, so that Claude can immediately see the file content and respond faster.

#### Acceptance Criteria

1. WHEN a text or code file at or below Size_Threshold is attached, THE Unified_Attachment_Pipeline SHALL read the file content and include it as a `text` Content_Block with a header indicating the filename and language.
2. WHEN a text Content_Block is delivered to Claude, THE Backend SHALL format the content with a filename header so Claude can identify the source file.
3. THE Unified_Attachment_Pipeline SHALL detect the file encoding and handle UTF-8 encoded text files correctly.
4. IF a text file contains non-UTF-8 content, THEN THE Unified_Attachment_Pipeline SHALL fall back to Path_Hint_Delivery for that file.

### Requirement 12: Attachment State Isolation Across Tabs

**User Story:** As a user, I want my file attachments to be scoped to the specific chat tab I'm working in, so that attachments in one tab do not leak into another tab's message.

#### Acceptance Criteria

1. WHEN a file is attached in one chat tab, THE Unified_Attachment_Pipeline SHALL associate the attachment with that specific tab only.
2. WHEN the user switches to a different chat tab, THE ChatInput SHALL display only the Attachment_Chips associated with the active tab.
3. WHEN a message is sent in one tab (clearing its attachments), THE attachments in other tabs SHALL remain unchanged.
4. WHEN a chat tab is closed, THE Unified_Attachment_Pipeline SHALL discard all attachments associated with that tab.

### Requirement 13: Multimodal SDK Verification

**User Story:** As a developer, I want to verify that the Claude SDK client correctly processes image and document content blocks via stdin JSON, so that multimodal attachments are not silently dropped.

#### Acceptance Criteria

1. WHEN an image Content_Block is sent via Claude_SDK_Client, THE Claude_SDK_Client SHALL include the image data in the stdin JSON payload in the format expected by the Claude Code CLI.
2. WHEN a document Content_Block is sent via Claude_SDK_Client, THE Claude_SDK_Client SHALL include the document data in the stdin JSON payload in the format expected by the Claude Code CLI.
3. IF the Claude Code CLI does not support a specific content block type via stdin, THEN THE Backend SHALL fall back to saving the file to the workspace and using Path_Hint_Delivery for that content type.
4. THE Backend SHALL log a warning when falling back from inline delivery to Path_Hint_Delivery due to SDK limitations.

### Requirement 14: Workspace File Path Safety

**User Story:** As a developer, I want all workspace file paths to be validated against path traversal attacks, so that malicious or malformed file paths cannot read files outside the agent's workspace directory.

#### Acceptance Criteria

1. WHEN a workspace file path is received from the Workspace_Explorer drag-drop, THE Unified_Attachment_Pipeline SHALL validate that the path is a relative path (does not start with `/`, `~`, or a drive letter).
2. WHEN a workspace file path contains path traversal sequences (`../` or `..\\`), THE Unified_Attachment_Pipeline SHALL reject the file and display an error message indicating the path is invalid.
3. WHEN a workspace file path passes frontend validation, THE Backend workspace API SHALL additionally validate the resolved path is within the agent's workspace directory before reading the file.
4. IF a workspace file path fails validation at any layer, THEN the file SHALL NOT be read and an error SHALL be returned to the user.
