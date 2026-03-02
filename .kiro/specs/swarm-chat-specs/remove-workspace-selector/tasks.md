# Implementation Plan: Remove Workspace Selector

## Overview

Incrementally delete the legacy workspace-selector UI artifacts (component, hook, props, indicator JSX) from the chat interface, clean up barrel exports and ChatPage plumbing, update tests, and add property-based tests to verify no workspace UI leaks back in. Each deletion step is followed by build verification to catch missed references early.

## Tasks

- [x] 1. Delete WorkspaceSelector component and clean barrel export
  - [x] 1.1 Delete `desktop/src/components/chat/WorkspaceSelector.tsx`
    - Remove the file entirely
    - _Requirements: 1.1, 1.3_

  - [x] 1.2 Remove WorkspaceSelector export from `desktop/src/components/chat/index.ts`
    - Delete the `export { WorkspaceSelector } from './WorkspaceSelector'` line
    - _Requirements: 1.2_

  - [x] 1.3 Verify build passes after WorkspaceSelector removal
    - Run `cd desktop && npx tsc --noEmit` to confirm no dangling imports
    - _Requirements: 1.1, 1.2, 1.3_

- [x] 2. Delete useWorkspaceSelection hook and clean barrel export
  - [x] 2.1 Delete `desktop/src/hooks/useWorkspaceSelection.ts`
    - Remove the hook file entirely
    - _Requirements: 3.1, 3.4_

  - [x] 2.2 Delete `desktop/src/hooks/useWorkspaceSelection.test.ts`
    - Remove the test file for the deleted hook
    - _Requirements: 3.2_

  - [x] 2.3 Remove useWorkspaceSelection export from `desktop/src/hooks/index.ts`
    - Delete the `export { useWorkspaceSelection } from './useWorkspaceSelection'` line
    - _Requirements: 3.3_

- [x] 3. Clean up ChatPage workspace plumbing
  - [x] 3.1 Remove workspace hook usage and prop threading from `desktop/src/pages/ChatPage.tsx`
    - Remove `import { useWorkspaceSelection }` (or destructured import from hooks barrel)
    - Remove `const { selectedWorkspace, workDir } = useWorkspaceSelection()` call
    - Remove `selectedWorkspace={selectedWorkspace}` prop from `<ChatInput>` JSX
    - Remove `selectedWorkspace` from `handleSendMessage` dependency array if present
    - Remove `workspaceContext: selectedWorkspace?.context` from the `streamChat` call arguments
    - Simplify `effectiveBasePath` from `workDir || agentWorkDir?.path` to `agentWorkDir?.path`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

- [x] 4. Remove workspace indicator from ChatInput
  - [x] 4.1 Remove selectedWorkspace prop and indicator JSX from `desktop/src/pages/chat/components/ChatInput.tsx`
    - Remove `selectedWorkspace: WorkspaceConfig | null` from `ChatInputProps` interface
    - Remove `selectedWorkspace` from destructured props
    - Delete the entire `{/* Workspace Indicator */}` JSX block
    - Remove `WorkspaceConfig` type import if no longer used elsewhere in the file
    - _Requirements: 2.1, 2.2, 2.3_

- [x] 5. Checkpoint - Verify build and existing tests
  - Run `cd desktop && npx tsc --noEmit` to confirm clean compilation
  - Run `cd desktop && npm test -- --run` to confirm no test failures
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Update tests for workspace removal
  - [x] 6.1 Update ChatInput tests in `desktop/src/pages/chat/components/ChatInput.test.tsx`
    - Remove `selectedWorkspace` from default props / test render calls
    - Remove any assertions referencing workspace indicator rendering
    - Remove `WorkspaceConfig` type import if no longer needed
    - _Requirements: 5.2, 5.4_

  - [x] 6.2 Update ChatPage tests in `desktop/src/pages/ChatPage.test.tsx`
    - Remove any mock of `useWorkspaceSelection`
    - Remove `selectedWorkspace` from any mock props
    - Remove `SwarmWorkspace` type import if no longer needed
    - _Requirements: 5.3, 5.4_

- [x] 7. Add property-based tests for workspace removal
  - [x] 7.1 Write property test: ChatInput never renders workspace indicator
    - **Property 1: ChatInput never renders workspace indicator**
    - Generate random valid ChatInput props (inputValue: string, isStreaming: boolean, selectedAgentId: string, attachments: array of 0-3 items) using fast-check
    - Assert rendered output contains no workspace name label, icon, or file path indicator block
    - Minimum 100 iterations
    - **Validates: Requirements 2.1**

  - [x] 7.2 Write property test: ChatInput preserves all non-workspace UI elements
    - **Property 2: ChatInput preserves all non-workspace UI elements**
    - Same generator strategy as Property 1
    - Assert rendered output contains text input field, send/stop button, and file attachment button
    - Minimum 100 iterations
    - **Validates: Requirements 2.3**

  - [x] 7.3 Write property test: streamChat call never includes workspaceContext
    - **Property 3: streamChat call never includes workspaceContext**
    - Mock `chatService.streamChat`, generate random non-empty message strings
    - Trigger send and assert `workspaceContext` is NOT present in the call arguments
    - Minimum 100 iterations
    - **Validates: Requirements 4.4**

- [x] 8. Final checkpoint - Ensure all tests pass
  - Run `cd desktop && npm test -- --run` — all tests pass with zero failures
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 5.1_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after deletions
- Property tests use fast-check (already available in the project's Vitest setup)
- No backend changes are needed — legacy backend cleanup is documented in the design for future follow-up
- The design uses TypeScript throughout — all code examples and implementations use TypeScript/React
