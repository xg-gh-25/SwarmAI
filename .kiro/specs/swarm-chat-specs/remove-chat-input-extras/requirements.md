# Requirements Document

## Introduction

Remove the Plugins, Skills, and MCPs indicator icons/buttons from the ChatInput component's bottom row, and remove the "IMMERSIVE WORKSPACE • POWERED BY CLAUDE CODE" branding text below the input box. These UI elements are not user-friendly for knowledge workers and add visual clutter to the chat input area. The backend APIs and services for plugins, skills, and MCPs must remain intact for future improvement — only the frontend UI elements in the chat input and any legacy frontend code exclusively supporting those UI elements should be removed.

## Glossary

- **ChatInput**: The React component (`ChatInput.tsx`) that renders the chat text input area, file attachment controls, and action buttons.
- **Bottom_Row**: The section below the text input area in ChatInput that currently displays Plugins, Skills, and MCPs indicators alongside the slash-command hint.
- **ReadOnlyChips**: A React component (`ReadOnlyChips.tsx`) that renders a compact badge with hover tooltip, used exclusively in ChatInput to display Plugins, Skills, and MCPs.
- **ChatPage**: The parent page component (`ChatPage.tsx`) that fetches skills, MCPs, and plugins data and passes it as props to ChatInput.
- **Plugin_Props**: The set of ChatInput props related to plugins/skills/MCPs display: `agentSkills`, `agentMCPs`, `agentPlugins`, `isLoadingSkills`, `isLoadingMCPs`, `isLoadingPlugins`, `allowAllSkills`.
- **Branding_Footer**: The `<p>` element in ChatInput that renders "IMMERSIVE WORKSPACE • POWERED BY CLAUDE CODE" below the input box.
- **Backend_Services**: The Python FastAPI backend endpoints and services for skills, MCPs, and plugins — these must NOT be modified.
- **Frontend_Services**: The TypeScript service modules (`skills.ts`, `mcp.ts`, `plugins.ts`) that call Backend_Services — these are used by other pages (SkillsPage, MCPPage, PluginsPage, AgentsPage, SwarmCorePage) and must NOT be deleted.

## Requirements

### Requirement 1: Remove Plugins/Skills/MCPs Indicators from ChatInput

**User Story:** As a knowledge worker, I want a clean chat input area without technical indicators for Plugins, Skills, and MCPs, so that I can focus on composing messages without distraction.

#### Acceptance Criteria

1. THE ChatInput SHALL render the Bottom_Row without any ReadOnlyChips components for Plugins, Skills, or MCPs.
2. THE ChatInput SHALL continue to display the slash-command hint ("Type / for commands") in the Bottom_Row.
3. WHEN a user views the chat input area, THE ChatInput SHALL display only the file attachment button, text input, send button, and slash-command hint as interactive elements.

### Requirement 2: Remove Plugin_Props from ChatInput Interface

**User Story:** As a developer, I want the ChatInput component interface to be clean and free of unused props, so that the component contract is clear and maintainable.

#### Acceptance Criteria

1. THE ChatInput SHALL NOT accept `agentSkills`, `agentMCPs`, `agentPlugins`, `isLoadingSkills`, `isLoadingMCPs`, `isLoadingPlugins`, or `allowAllSkills` as props.
2. THE ChatPage SHALL NOT pass Plugin_Props to the ChatInput component.
3. THE ChatPage SHALL continue to fetch and compute skills, MCPs, and plugins data for use in the `enableSkills` and `enableMCP` flags that are sent to Backend_Services during chat streaming.

### Requirement 3: Remove ReadOnlyChips Component

**User Story:** As a developer, I want unused components removed from the codebase, so that the codebase stays lean and free of dead code.

#### Acceptance Criteria

1. WHEN the ReadOnlyChips component is used exclusively by the removed ChatInput Bottom_Row indicators, THE System SHALL delete the ReadOnlyChips component file.
2. THE System SHALL remove the ReadOnlyChips export from the common components index file.
3. THE System SHALL remove the `ChipItem` type export from the common components index file.


### Requirement 4: Update Existing Tests

**User Story:** As a developer, I want tests to reflect the updated ChatInput interface, so that the test suite passes and accurately validates the component behavior.

#### Acceptance Criteria

1. THE ChatInput test suite SHALL NOT reference Plugin_Props (`agentSkills`, `agentMCPs`, `agentPlugins`, `isLoadingSkills`, `isLoadingMCPs`, `isLoadingPlugins`, `allowAllSkills`).
2. THE ChatInput test suite SHALL NOT import the `Skill`, `MCPServer`, or `Plugin` types when those types are only used for the removed Plugin_Props.
3. THE ChatInput test suite SHALL continue to pass for all existing file attachment and context file tests.

### Requirement 5: Remove Branding Footer from ChatInput

**User Story:** As a knowledge worker, I want the chat input area free of internal branding text, so that the interface feels clean and professional without developer-facing labels.

#### Acceptance Criteria

1. THE ChatInput SHALL NOT render the Branding_Footer text "IMMERSIVE WORKSPACE • POWERED BY CLAUDE CODE" below the input box.
2. THE ChatInput SHALL NOT render any replacement branding or tagline text in the footer area.
3. THE removal SHALL NOT affect the layout or spacing of the input box, Bottom_Row, or any other ChatInput elements.

### Requirement 6: Preserve Backend and Frontend Service Integrity

**User Story:** As a developer, I want the backend services and shared frontend service modules to remain untouched, so that Plugins, Skills, and MCPs management pages continue to work and the backend is ready for future UI improvements.

#### Acceptance Criteria

1. THE System SHALL NOT modify any files in the `backend/` directory.
2. THE System SHALL NOT modify the frontend service files (`skills.ts`, `mcp.ts`, `plugins.ts`) in `desktop/src/services/`.
3. THE ChatPage SHALL continue to use `skillsService`, `mcpService`, and `pluginsService` to compute `enableSkills` and `enableMCP` flags for chat streaming requests.
4. THE SkillsPage, MCPPage, PluginsPage, AgentsPage, and SwarmCorePage SHALL continue to function without any changes.

### Requirement 7: Enlarge Chat Input with Auto-Grow Behavior

**User Story:** As a knowledge worker, I want a taller default chat input area that grows as I type, so that I can see more of my message while composing without the input feeling cramped.

#### Acceptance Criteria

1. THE ChatInput textarea SHALL render with a default minimum height of 2 visible text lines when empty or when the content fits within 2 lines.
2. THE ChatInput textarea SHALL auto-grow in height as the user types content that exceeds the current visible area, up to a maximum height of 20 visible text lines.
3. WHEN the content exceeds 20 lines, THE ChatInput textarea SHALL stop growing and display a vertical scrollbar to access the overflow content.
4. WHEN the user sends a message (via Enter key or send button), THE ChatInput textarea SHALL reset its height back to the default 2-line minimum.
5. THE ChatInput textarea SHALL continue to support Shift+Enter for inserting newlines, and the auto-grow behavior SHALL apply to manually inserted newlines as well.

### Requirement 8: Preserve ChatInput Core Functionality

**User Story:** As a knowledge worker, I want the chat input to continue working exactly as before for message composition, file attachments, and sending, so that removing the indicators does not break any existing functionality.

#### Acceptance Criteria

1. THE ChatInput SHALL continue to support text input, Enter-to-send, and Shift+Enter for newlines.
2. THE ChatInput SHALL continue to support file attachments via the attachment button, paste, and drag-and-drop.
3. THE ChatInput SHALL continue to support slash command suggestions when the user types `/`.
4. THE ChatInput SHALL continue to display attached context files from the Workspace Explorer.
5. THE ChatInput SHALL continue to display the stop button during streaming and the send button when idle.
