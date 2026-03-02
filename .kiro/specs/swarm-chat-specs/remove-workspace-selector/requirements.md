# Requirements Document

## Introduction

SwarmAI has transitioned to a single-workspace model (SwarmWS). Several UI artifacts from the former multi-workspace design remain in the codebase: the `WorkspaceSelector` component rendered in the chat input area, the `useWorkspaceSelection` hook, the `selectedWorkspace` prop threaded through `ChatPage` → `ChatInput`, and the "Workspace Indicator" block inside `ChatInput`. This feature removes all workspace-selector UI and plumbing from the chat input box, cleans up associated code, and ensures no regressions in chat functionality.

## Glossary

- **Chat_Input**: The `ChatInput` React component (`desktop/src/pages/chat/components/ChatInput.tsx`) responsible for composing and sending messages.
- **ChatPage**: The `ChatPage` React component (`desktop/src/pages/ChatPage.tsx`) that orchestrates the chat experience and passes props to Chat_Input.
- **WorkspaceSelector_Component**: The `WorkspaceSelector` React component (`desktop/src/components/chat/WorkspaceSelector.tsx`) that displays a static SwarmWS indicator button.
- **useWorkspaceSelection_Hook**: The `useWorkspaceSelection` React hook (`desktop/src/hooks/useWorkspaceSelection.ts`) that returns hardcoded singleton workspace data.
- **Workspace_Indicator**: The inline UI block inside Chat_Input that renders workspace name, icon, and file path when `selectedWorkspace` is non-null.
- **Chat_Service**: The chat service layer (`desktop/src/services/chat.ts`) that sends messages to the backend, including `workspaceContext`.
- **Frontend_Test_Suite**: The collection of Vitest tests for desktop components and hooks.

## Requirements

### Requirement 1: Remove WorkspaceSelector Component

**User Story:** As a developer, I want the WorkspaceSelector component deleted from the codebase, so that dead code from the multi-workspace era is eliminated.

#### Acceptance Criteria

1. THE Build_System SHALL produce a successful build after the WorkspaceSelector_Component file (`desktop/src/components/chat/WorkspaceSelector.tsx`) is deleted.
2. WHEN the chat barrel export (`desktop/src/components/chat/index.ts`) is inspected, THE Build_System SHALL confirm that no export referencing `WorkspaceSelector` exists.
3. IF any file imports `WorkspaceSelector`, THEN THE Build_System SHALL report a compile error (i.e., no remaining imports should exist).

### Requirement 2: Remove Workspace Indicator from Chat Input

**User Story:** As a user, I want the workspace indicator banner removed from the chat input area, so that the UI is cleaner and does not display redundant single-workspace information.

#### Acceptance Criteria

1. WHEN Chat_Input renders, THE Chat_Input SHALL NOT display a workspace name, icon, or file path indicator block.
2. THE Chat_Input SHALL remove the `selectedWorkspace` prop from its interface (`ChatInputProps`).
3. WHEN Chat_Input renders with all other valid props, THE Chat_Input SHALL continue to display the text input, send button, file attachment button, slash commands, and bottom toolbar without regression.

### Requirement 3: Remove useWorkspaceSelection Hook

**User Story:** As a developer, I want the useWorkspaceSelection hook removed, so that the codebase no longer contains single-purpose singleton workspace plumbing.

#### Acceptance Criteria

1. THE Build_System SHALL produce a successful build after the useWorkspaceSelection_Hook file (`desktop/src/hooks/useWorkspaceSelection.ts`) is deleted.
2. THE Build_System SHALL produce a successful build after the useWorkspaceSelection_Hook test file (`desktop/src/hooks/useWorkspaceSelection.test.ts`) is deleted.
3. WHEN the hooks barrel export (`desktop/src/hooks/index.ts`) is inspected, THE Build_System SHALL confirm that no export referencing `useWorkspaceSelection` exists.
4. IF any file imports `useWorkspaceSelection`, THEN THE Build_System SHALL report a compile error (i.e., no remaining imports should exist).

### Requirement 4: Clean Up ChatPage Workspace Plumbing

**User Story:** As a developer, I want ChatPage to stop importing and threading workspace selection data, so that the component is simpler and free of dead code paths.

#### Acceptance Criteria

1. THE ChatPage SHALL NOT import `useWorkspaceSelection` from the hooks module.
2. THE ChatPage SHALL NOT destructure or reference `selectedWorkspace` or `workDir` variables.
3. THE ChatPage SHALL NOT pass a `selectedWorkspace` prop to Chat_Input.
4. WHEN ChatPage sends a chat message, THE ChatPage SHALL NOT pass `workspaceContext` to the Chat_Service call, because the backend assembles workspace context independently from the filesystem via `context_assembler.py` and the frontend-supplied `workspace_context` field was always dead code (empty string, never used by the backend).
5. WHEN ChatPage renders the chat interface, THE ChatPage SHALL continue to function correctly for message sending, streaming, agent selection, and file attachments.

### Requirement 5: Update Tests

**User Story:** As a developer, I want all tests updated to reflect the removal of workspace selector code, so that the Frontend_Test_Suite passes cleanly.

#### Acceptance Criteria

1. WHEN the Frontend_Test_Suite runs (`cd desktop && npm test -- --run`), THE Frontend_Test_Suite SHALL pass with zero failures related to workspace selector removal.
2. THE Frontend_Test_Suite SHALL NOT contain any test referencing `selectedWorkspace` as a prop of Chat_Input.
3. THE Frontend_Test_Suite SHALL NOT contain any test importing `useWorkspaceSelection`.
4. WHEN existing tests for ChatPage and Chat_Input execute, THE Frontend_Test_Suite SHALL validate that chat functionality (sending messages, streaming, file attachments) remains intact.
