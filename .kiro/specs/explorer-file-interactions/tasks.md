# Implementation Plan: Explorer File Interactions

## Overview

Implement binary file preview (images, PDFs) and drag-to-chat support for the Workspace Explorer. The plan proceeds bottom-up: file classification utility → backend binary serving → BinaryPreviewModal component → ThreeColumnLayout routing → TreeNodeRow drag support → integration wiring. TypeScript (frontend) and Python (backend).

## Tasks

- [x] 1. Add file classification utility
  - [x] 1.1 Add `FilePreviewType` type and `classifyFileForPreview()` function to `desktop/src/utils/fileUtils.ts`
    - Add `FilePreviewType = 'image' | 'pdf' | 'text' | 'unsupported'` type export
    - Add `IMAGE_EXTENSIONS`, `PDF_EXTENSIONS`, `UNSUPPORTED_BINARY` sets per design
    - Implement `classifyFileForPreview(fileName: string): FilePreviewType` — extract extension, match against sets, default to `'text'`
    - Remove `TEXT_EXTENSIONS` set if it exists (dead code per design)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [ ]* 1.2 Write property test for file classification (Property 1)
    - **Property 1: File classification correctness**
    - **Validates: Requirements 1.1, 1.4**
    - Add test to `desktop/src/utils/__tests__/fileUtils.test.ts`
    - Use `fast-check` to generate random file names with extensions from each category
    - Verify image extensions → `'image'`, pdf → `'pdf'`, unsupported binary → `'unsupported'`, all others → `'text'`
    - Verify case-insensitivity (e.g., `.PNG`, `.Jpg`)

- [x] 2. Extend backend to serve binary files
  - [x] 2.1 Modify `GET /workspace/file` in `backend/routers/workspace_api.py` to handle binary content
    - Add `import base64, mimetypes` and `MAX_PREVIEW_SIZE = 50 * 1024 * 1024` constant
    - Add file size check BEFORE reading — return HTTP 413 if file exceeds 50 MB
    - Wrap existing `read_text(encoding="utf-8")` in try/except `UnicodeDecodeError`
    - Add `encoding: "utf-8"` field to existing text response
    - On `UnicodeDecodeError`, fall back to `read_bytes()` → `base64.b64encode()` → return with `encoding: "base64"`, `mime_type` from `mimetypes.guess_type()` (default `"application/octet-stream"`), and `size` in bytes
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [ ]* 2.2 Write property test for binary file round-trip (Property 2)
    - **Property 2: Binary file serving round-trip**
    - **Validates: Requirements 2.1, 2.4**
    - Add test to `backend/tests/test_workspace_file_binary.py`
    - Use `hypothesis` to generate random byte arrays (1–10KB) with non-UTF-8 sequences
    - Write bytes to temp file, call endpoint, verify `encoding == "base64"` and `base64.b64decode(content)` equals original bytes
    - Verify `mime_type` matches `mimetypes.guess_type()` for the file extension

  - [ ]* 2.3 Write property test for text file round-trip (Property 3)
    - **Property 3: Text file serving round-trip**
    - **Validates: Requirements 2.2**
    - Add test to `backend/tests/test_workspace_file_binary.py`
    - Use `hypothesis.strategies.text()` to generate valid UTF-8 strings
    - Write to temp file, call endpoint, verify `encoding == "utf-8"` and `content` equals original string

- [x] 3. Checkpoint - Verify classification and backend
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Install react-pdf dependency and create BinaryPreviewModal
  - [x] 4.1 Install `react-pdf` npm package in `desktop/`
    - Run `npm install react-pdf` in the `desktop/` directory
    - Verify package is added to `package.json` dependencies
    - _Requirements: 4.1_

  - [x] 4.2 Create `desktop/src/components/common/BinaryPreviewModal.tsx` with image, PDF, and unsupported modes
    - Add module-level docstring per dev rules
    - Implement `BinaryPreviewModalProps` interface: `isOpen`, `fileName`, `filePath`, `mode`, `onClose`
    - Fetch file content from `GET /workspace/file` when modal opens (use existing API service)
    - Update `toCamelCase()` in the relevant service file to map `mime_type` → `mimeType` if not already handled
    - **Image mode**: Render `<img src="data:{mimeType};base64,{content}">` with `alt={fileName}`, `object-fit: contain` initial fit, zoom via CSS `transform: scale()` on mouse wheel, pan via `transform: translate()` on mouse drag when zoomed, display dimensions (width × height) and file size
    - **PDF mode**: Use `react-pdf` `Document` + `Page` components, pass `{ data: atob(base64Content) }`, vertical scroll through pages, "Page X of Y" counter via `onLoadSuccess`, error fallback with message + "Reveal in Finder" button for corrupted/password-protected PDFs
    - **Unsupported mode**: Show file name, extension badge, "This file type cannot be previewed" message, "Reveal in Finder" button
    - **Shared**: Escape key closes modal, focus trap with Tab cycling, `role="dialog"`, `aria-modal="true"`, `aria-label={fileName}`, `aria-live="polite"` region for unsupported announcement
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 8.1, 8.2, 8.3, 8.4, 8.6_

  - [ ]* 4.3 Write property test for BinaryPreviewModal accessibility (Property 7)
    - **Property 7: BinaryPreviewModal accessibility attributes**
    - **Validates: Requirements 8.2, 8.3**
    - Add test to `desktop/src/components/common/__tests__/BinaryPreviewModal.test.tsx`
    - Use `fast-check` to generate random file names × preview modes (`'image'`, `'pdf'`, `'unsupported'`)
    - Verify root element has `role="dialog"`, `aria-modal="true"`, `aria-label` containing file name
    - Verify image mode `<img>` has `alt` equal to file name

- [x] 5. Route double-clicks through file classifier in ThreeColumnLayout
  - [x] 5.1 Modify `handleFileDoubleClick` in `desktop/src/components/layout/ThreeColumnLayout.tsx`
    - Import `classifyFileForPreview` from `fileUtils.ts`
    - Add `binaryPreviewState` state: `{ isOpen, fileName, filePath, mode }` (initially null)
    - In `handleFileDoubleClick`: preserve Swarm workspace warning check, then call `classifyFileForPreview(file.name)` — if `'text'` open FileEditorModal (existing path), otherwise set `binaryPreviewState` with the file info and preview mode
    - Render `<BinaryPreviewModal>` conditionally when `binaryPreviewState` is non-null, pass `onClose` to reset state
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 7.1, 7.2_

  - [ ]* 5.2 Write unit tests for ThreeColumnLayout routing
    - Add tests to `desktop/src/components/layout/__tests__/ThreeColumnLayout.test.tsx`
    - Test: double-click `.png` opens BinaryPreviewModal in image mode
    - Test: double-click `.pdf` opens BinaryPreviewModal in PDF mode
    - Test: double-click `.ts` opens FileEditorModal (backward compat)
    - Test: double-click Swarm workspace file shows warning dialog (backward compat)
    - _Requirements: 1.2, 1.3, 1.4, 7.1, 7.2_

- [x] 6. Checkpoint - Verify binary preview end-to-end
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Add drag-to-chat support on TreeNodeRow
  - [x] 7.1 Add `draggable` attribute and `onDragStart` handler to `desktop/src/components/workspace-explorer/TreeNodeRow.tsx`
    - Set `draggable={!isDirectory}` on the root `<div>` — files are draggable, directories are not
    - Implement `handleDragStart` callback: if directory, `e.preventDefault()` and return; otherwise serialize `FileTreeItem` JSON payload with `id`, `name`, `type`, `path`, `workspaceId: ''`, `workspaceName: ''`, `gitStatus` to `application/json` on `dataTransfer`
    - Set `e.dataTransfer.effectAllowed = 'copy'`
    - Create custom drag ghost element: temporary DOM div with file icon + file name using `textContent` (not `innerHTML`) to prevent XSS, style with theme CSS variables, set as drag image, remove on next `requestAnimationFrame`
    - Preserve all existing `onClick`, `onDoubleClick`, `onContextMenu`, `onKeyDown` handlers — no interference
    - _Requirements: 5.1, 5.2, 5.3, 5.6, 6.1, 6.2, 7.3, 7.4_

  - [ ]* 7.2 Write property test for draggable attribute correctness (Property 4)
    - **Property 4: Draggable attribute correctness**
    - **Validates: Requirements 5.1**
    - Add test to `desktop/src/components/workspace-explorer/__tests__/TreeNodeRow.test.tsx`
    - Use `fast-check` to generate `TreeNode` objects with random type (`'file'` | `'directory'`)
    - Render `TreeNodeRow`, verify `draggable` is `true` for files, `false` for directories

  - [ ]* 7.3 Write property test for drag payload completeness (Property 5)
    - **Property 5: Drag payload completeness**
    - **Validates: Requirements 5.2**
    - Add test to `desktop/src/components/workspace-explorer/__tests__/TreeNodeRow.test.tsx`
    - Use `fast-check` to generate file `TreeNode` objects with random name, path, gitStatus
    - Simulate drag start, parse `application/json` from `dataTransfer`, verify all required fields present and values match

  - [ ]* 7.4 Write property test for drag non-interference (Property 6)
    - **Property 6: Drag non-interference with existing handlers**
    - **Validates: Requirements 5.6, 7.4**
    - Add test to `desktop/src/components/workspace-explorer/__tests__/TreeNodeRow.test.tsx`
    - Use `fast-check` to generate `TreeNode` objects, fire click/dblclick/contextmenu/keydown events
    - Verify all existing callback props are still invoked with correct arguments

- [x] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use `fast-check` (TypeScript frontend) and `hypothesis` (Python backend)
- Checkpoints ensure incremental validation after each major phase
- Always update `toCamelCase()` functions in `desktop/src/services/*.ts` when adding new API response fields (`mime_type` → `mimeType`)
- Drag ghost uses `textContent` (not `innerHTML`) to prevent XSS per design
- `workspaceId`/`workspaceName` set to empty strings in drag payload — attachment pipeline only uses `name`, `type`, `path`
- Backend tries UTF-8 first, falls back to base64 — preserves backward compatibility for all existing text file consumers
