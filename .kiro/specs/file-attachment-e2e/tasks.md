# Implementation Plan: File Attachment End-to-End Pipeline

## Overview

Unify all file attachment input paths (File Picker, Workspace Explorer drag-drop, OS Finder drag-drop, clipboard paste) into a single `useUnifiedAttachments` hook that produces typed content blocks. The implementation proceeds bottom-up: types → pure utilities → hook → UI wiring → backend validation → SDK fallback → cleanup.

## Tasks

- [x] 1. Define types and constants
  - [x] 1.1 Add `UnifiedAttachment`, `AttachmentType`, `DeliveryStrategy` types and size limit constants to `desktop/src/types/index.ts`
    - Add `AttachmentType = 'image' | 'pdf' | 'text' | 'csv'`
    - Add `DeliveryStrategy = 'base64_image' | 'base64_document' | 'inline_text' | 'path_hint'`
    - Add `UnifiedAttachment` interface with all fields from design
    - Add `SIZE_LIMITS`, `SIZE_THRESHOLD`, `MAX_ATTACHMENTS` constants
    - Expand `SUPPORTED_FILE_TYPES` to include all recognized extensions
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 6.1, 6.2, 6.3, 6.4, 6.6_

  - [x] 1.2 Extend `TabState` in `useUnifiedTabState` to include `attachments: UnifiedAttachment[]`
    - Add `attachments` field to the tab state interface
    - Initialize to empty array on tab creation
    - _Requirements: 12.1, 12.2_

- [x] 2. Implement file classification module
  - [x] 2.1 Create `desktop/src/utils/fileClassification.ts` with pure classification functions
    - Implement `MIME_TYPE_MAP` and `EXTENSION_TYPE_MAP` lookup tables
    - Implement `classifyFile(file: { name: string; type: string }): AttachmentType | null`
    - Implement `isGenericMimeType(mimeType: string): boolean`
    - Implement `determineDeliveryStrategy(type: AttachmentType, size: number): DeliveryStrategy`
    - Implement `validateFileSize(type: AttachmentType, size: number): string | null`
    - Implement `validateWorkspacePath(path: string): string | null`
    - Add module-level docstring per dev rules
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 6.4, 6.5, 14.1, 14.2_

  - [ ]* 2.2 Write property test: Classification Correctness (Property 2)
    - **Property 2: Classification Correctness**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5**
    - Create `desktop/src/__tests__/fileAttachment.property.test.ts`
    - Use `fast-check` to generate files with known MIME types and extensions
    - Verify MIME type takes precedence; extension fallback used for generic/missing MIME

  - [ ]* 2.3 Write property test: Delivery Strategy Correctness (Property 3)
    - **Property 3: Delivery Strategy Correctness**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 6.4**
    - Generate classified files with random sizes, verify strategy matches type+size rules

  - [ ]* 2.4 Write property test: Size Validation (Property 4)
    - **Property 4: Size Validation**
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.5**
    - Generate files with sizes around type limits, verify accept/reject and error message content

  - [ ]* 2.5 Write property test: Unsupported File Rejection (Property 6)
    - **Property 6: Unsupported File Rejection**
    - **Validates: Requirements 4.4, 5.6**
    - Generate files with unrecognized MIME types and extensions, verify null classification

  - [ ]* 2.6 Write property test: Workspace Path Safety (Property 17)
    - **Property 17: Workspace Path Safety**
    - **Validates: Requirements 14.1, 14.2**
    - Generate paths with `../`, absolute prefixes (`/`, `~`, drive letters), verify rejection
    - Generate safe relative paths, verify acceptance

- [x] 3. Checkpoint - Verify classification module
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement `useUnifiedAttachments` hook
  - [x] 4.1 Create `desktop/src/hooks/useUnifiedAttachments.ts`
    - Implement hook signature: `useUnifiedAttachments(tabId, tabMapRef)`
    - Store attachments in `tabMapRef.current.get(tabId).attachments` (authoritative)
    - Use React `useState` as display mirror only, sync on `tabId` change
    - Implement `addFiles()` for native File objects (File Picker, OS drop, clipboard)
      - Classify → validate size → validate count → encode (base64 or read text) → store
    - Implement `addWorkspaceFiles()` for workspace FileTreeItem objects
      - Validate workspace path → classify by extension → validate size → store path (content read at send time)
    - Implement `removeAttachment(id)`, `clearAll()`
    - Implement `canAddMore` computed from current count vs MAX_ATTACHMENTS
    - Generate text preview (first 200 chars + ellipsis) for text files
    - Generate image thumbnail preview via `URL.createObjectURL`
    - Handle loading states and error states per attachment
    - Add module-level docstring per dev rules
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 3.1, 3.2, 3.3, 3.4, 4.2, 4.3, 6.6, 6.7, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 12.1, 12.2, 12.3, 12.4_

  - [ ]* 4.2 Write property test: Source Invariance (Property 1)
    - **Property 1: Source Invariance**
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**
    - Generate files with same name/MIME/size, verify classifyFile + determineDeliveryStrategy produce identical output regardless of source

  - [ ]* 4.3 Write property test: Attachment Count Limit (Property 5)
    - **Property 5: Attachment Count Limit**
    - **Validates: Requirements 6.6, 6.7**
    - Generate file counts 1–20, verify cap at 10 and rejection of excess

  - [ ]* 4.4 Write property test: Attachment Removal (Property 8)
    - **Property 8: Attachment Removal**
    - **Validates: Requirements 7.4**
    - Generate attachment lists, pick random ID to remove, verify list shrinks by 1

  - [ ]* 4.5 Write property test: Text Preview Truncation (Property 9)
    - **Property 9: Text Preview Truncation**
    - **Validates: Requirements 7.3**
    - Generate random strings of varying length, verify preview ≤200 chars with ellipsis

  - [ ]* 4.6 Write property test: Tab Isolation (Property 11)
    - **Property 11: Tab Isolation**
    - **Validates: Requirements 12.1, 12.2, 12.3**
    - Generate two distinct tab IDs and attachment operations, verify cross-tab independence

  - [ ]* 4.7 Write property test: Tab Close Cleanup (Property 12)
    - **Property 12: Tab Close Cleanup**
    - **Validates: Requirements 12.4**
    - Generate tab with attachments, simulate close, verify no references remain

- [x] 5. Checkpoint - Verify hook and property tests
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Wire `ChatDropZone` and `ChatPage`
  - [x] 6.1 Modify `desktop/src/components/chat/ChatDropZone.tsx` to accept props and handle native drops
    - Change interface to accept `addFiles` and `addWorkspaceFiles` as props (not LayoutContext)
    - Add native OS file drop handling via `e.dataTransfer.files`
    - Keep workspace explorer JSON drop handling via `e.dataTransfer.getData('application/json')`
    - Add visual drop overlay for both drag types
    - Wrap JSON parse in try/catch for error resilience
    - _Requirements: 3.1, 4.1, 4.2, 4.3_

  - [x] 6.2 Modify `desktop/src/pages/ChatPage.tsx` to use `useUnifiedAttachments` and update `buildContentArray`
    - Replace `useFileAttachment` usage with `useUnifiedAttachments(tabId, tabMapRef)`
    - Pass `addFiles` and `addWorkspaceFiles` as props to `ChatDropZone`
    - Update `buildContentArray` to handle all four delivery strategies
    - Read workspace file content at send time for `inline_text` strategy
    - Wire attachment chips display in ChatInput from hook's `attachments` state
    - Wire `removeAttachment` and `clearAll` to ChatInput
    - Call `clearAll()` after successful message send
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4, 3.3, 9.1, 9.2, 9.3, 9.4, 11.1_

  - [ ]* 6.3 Write property test: Content Block Ordering (Property 7)
    - **Property 7: Content Block Ordering**
    - **Validates: Requirements 9.1, 9.2**
    - Generate text + random attachment lists, verify user text is first block, attachments follow in order

  - [ ]* 6.4 Write property test: Send Clears Attachments (Property 10)
    - **Property 10: Send Clears Attachments**
    - **Validates: Requirements 9.3**
    - Generate non-empty attachment lists, simulate send, verify list is empty after

  - [ ]* 6.5 Write property test: Inline Text Header Format (Property 15)
    - **Property 15: Inline Text Header Format**
    - **Validates: Requirements 11.1**
    - Generate random filenames and content, verify `--- File: {name} ---` header and `--- End: {name} ---` footer

  - [ ]* 6.6 Write property test: UTF-8 Round-Trip (Property 16)
    - **Property 16: UTF-8 Round-Trip**
    - **Validates: Requirements 11.3**
    - Generate random UTF-8 strings via `fc.fullUnicodeString()`, verify content preservation in text block

- [x] 7. Checkpoint - Verify frontend wiring
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Implement backend content validation and SDK fallback
  - [x] 8.1 Add `validate_content()` and `_estimate_block_size()` to `backend/routers/chat.py`
    - Implement `_estimate_block_size(block)` for base64, text, image, document blocks
    - Implement `validate_content(content)` with block count limit (20) and payload size limit (25MB)
    - Raise `HTTPException(413)` with descriptive detail messages
    - Call `validate_content()` before `run_conversation()` in the chat endpoint
    - Add module-level docstring updates per dev rules
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 10.1, 10.2, 10.3, 10.4_

  - [x] 8.2 Add `_SDK_SUPPORTS_MULTIMODAL` flag and fallback logic to `backend/core/agent_manager.py`
    - Add `_SDK_SUPPORTS_MULTIMODAL: bool | None = None` feature flag
    - Implement `_convert_unsupported_blocks_to_path_hints()` to save image/document data to `~/.swarm-ai/attachments/{session_id}/{uuid}.{ext}` and replace with text path hint
    - Modify `multimodal_message_generator()` to check flag and convert blocks if needed
    - Log warnings when falling back from inline to path hint delivery
    - _Requirements: 13.1, 13.2, 13.3, 13.4_

  - [ ]* 8.3 Write property test: Backend Block Count Limit (Property 13)
    - **Property 13: Backend Block Count Limit**
    - **Validates: Requirements 10.1, 10.3**
    - Create `backend/tests/test_content_validation.py`
    - Use `hypothesis` to generate content arrays with 1–30 blocks
    - Verify `validate_content` raises for >20 blocks, passes for ≤20

  - [ ]* 8.4 Write property test: Backend Payload Size Limit (Property 14)
    - **Property 14: Backend Payload Size Limit**
    - **Validates: Requirements 10.2, 10.4**
    - Use `hypothesis` to generate content arrays with varying base64 sizes
    - Verify `validate_content` raises for >25MB total, passes for ≤25MB

- [x] 9. Checkpoint - Verify backend validation
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Clean up deprecated code and finalize
  - [x] 10.1 Remove `LayoutContext` attachment state
    - Remove `attachedFiles`, `attachFile`, `removeAttachedFile`, `clearAttachedFiles` from `desktop/src/contexts/LayoutContext.tsx`
    - Update any remaining consumers of these fields to use `useUnifiedAttachments` instead
    - _Requirements: 1.5 (single pipeline — no parallel state)_

  - [x] 10.2 Deprecate or remove `desktop/src/hooks/useFileAttachment.ts`
    - Remove the hook if no other consumers exist, or mark as deprecated with a comment pointing to `useUnifiedAttachments`
    - Update imports in ChatPage if not already done in task 6.2
    - _Requirements: 1.5 (single pipeline)_

  - [x] 10.3 Add workspace path validation to backend
    - Add path traversal validation in the backend workspace API (resolve path, check it's within workspace dir)
    - _Requirements: 14.3, 14.4_

- [x] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Tauri native drag-drop event handling (Path D fix)
  - [x] 12.1 Investigate Tauri 2.0 drag-drop behavior in WebView
    - **Finding**: Tauri's built-in drag-drop system (enabled by default) INTERCEPTS OS file drops, making browser `e.dataTransfer.files` empty on macOS
    - **Fix**: Set `dragDropEnabled: false` in `tauri.conf.json` window config — disables Tauri's interception, lets browser `ondrop` work normally
    - **Applied**: Added `"dragDropEnabled": false` to the window config
    - No `@tauri-apps/plugin-drag-drop` needed — browser native API is sufficient once Tauri stops intercepting
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 12.2 ~~Add Tauri drag-drop event listener~~ — NOT NEEDED (Option A: `dragDropEnabled: false` makes browser `ondrop` work)

  - [x] 12.3 ~~Handle Tauri drag-drop for multiple files~~ — NOT NEEDED (browser `e.dataTransfer.files` handles multiple files natively)

- [ ] 13. Final checkpoint - Verify Tauri drag-drop
  - Test OS file drop from Finder/Explorer into the chat panel in the Tauri app
  - Verify files appear as attachment chips and are included in the message payload
  - Test with images, PDFs, and text files

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use `fast-check` (TypeScript) and `hypothesis` (Python)
- Checkpoints ensure incremental validation after each major phase
- Workspace file content is read at send time (task 6.2), not attach time, per design decision 9
- Tab isolation uses `tabMapRef` as authoritative source — never `useState` for cross-tab decisions
- Always update `toCamelCase()` functions in `desktop/src/services/*.ts` if adding new API fields
