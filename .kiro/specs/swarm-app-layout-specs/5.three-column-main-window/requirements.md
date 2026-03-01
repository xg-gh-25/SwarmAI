# Requirements Document

## Introduction

This document specifies the requirements for redesigning SwarmAI's desktop application from its current layout to a modern 3-column IDE-like interface. The redesign introduces a unified SwarmAgent experience where users interact with a single intelligent agent that autonomously orchestrates custom agents behind the scenes. The new layout consists of a left navigation sidebar, a central workspace explorer, and a main chat panel, providing an intuitive and efficient workflow for AI-assisted development.

## Glossary

- **SwarmAgent**: The single user-facing AI agent that users interact with. SwarmAgent autonomously orchestrates custom agents to complete tasks.
- **Custom_Agent**: User-created agents with specific configurations (skills, MCP servers, system prompts) that SwarmAgent can orchestrate.
- **Workspace**: A directory on the local filesystem that contains project files. Users can have multiple workspaces.
- **Swarm_Workspace**: A protected system workspace that is always present and cannot be deleted. Used for SwarmAI's internal operations.
- **Workspace_Explorer**: The middle column UI component that displays a file tree for browsing workspace contents.
- **Workspace_Scope**: The current workspace context filter. Can be "All Workspaces" or a specific workspace.
- **Chat_Context**: The set of files and workspace information currently attached to the chat conversation.
- **Left_Sidebar**: The narrow navigation column containing icons for Skills, MCP Servers, Agents, and Settings.
- **Main_Chat_Panel**: The right-most column where users interact with SwarmAgent via chat.
- **File_Editor_Modal**: A modal/drawer overlay for editing files with syntax highlighting, preserving the chat conversation underneath.
- **System_Status_API**: The backend endpoint that provides system initialization status information including Swarm Workspace status.
- **Backend_Startup_Overlay**: The React component (BackendStartupOverlay.tsx) that displays during app startup showing initialization status.
- **Frontend_System_Service**: The TypeScript service that fetches system status from the backend API.
- **Translation_File**: The i18n translation JSON file containing localized strings for the UI.

## Requirements

### Requirement 1: Three-Column Layout Structure

**User Story:** As a user, I want a modern IDE-like 3-column layout, so that I can efficiently navigate, browse files, and chat with SwarmAgent in a unified interface.

#### Acceptance Criteria

1. THE Layout SHALL display three distinct columns: Left_Sidebar, Workspace_Explorer, and Main_Chat_Panel
2. THE Left_Sidebar SHALL be positioned as the leftmost column with a fixed narrow width
3. THE Workspace_Explorer SHALL be positioned as the middle column between Left_Sidebar and Main_Chat_Panel
4. THE Main_Chat_Panel SHALL be positioned as the rightmost column and occupy remaining horizontal space
5. WHEN the application window is resized, THE Layout SHALL maintain the three-column structure with appropriate minimum widths
6. THE Workspace_Explorer SHALL be collapsible to maximize Main_Chat_Panel space
7. THE Workspace_Explorer SHALL be resizable by dragging its right edge
8. WHEN screen width falls below 768 pixels, THE Workspace_Explorer SHALL auto-collapse to preserve usability

### Requirement 2: Left Sidebar Navigation

**User Story:** As a user, I want a compact navigation sidebar, so that I can quickly access different management sections without leaving the chat context.

#### Acceptance Criteria

1. THE Left_Sidebar SHALL display navigation icons for: Skills, MCP Servers, Agents, and Settings
2. WHEN a user clicks a navigation icon, THE System SHALL open the corresponding management page in a modal or drawer overlay
3. THE Left_Sidebar SHALL remain visible and accessible at all times during application use
4. THE Left_Sidebar SHALL display the SwarmAI logo or brand icon at the top
5. WHEN a navigation item is active, THE Left_Sidebar SHALL display a visual indicator on that icon
6. THE Left_Sidebar SHALL include a link to the GitHub repository

### Requirement 3: Workspace Explorer

**User Story:** As a user, I want to browse and manage files across my workspaces, so that I can easily attach files to chat context and navigate project structures.

#### Acceptance Criteria

1. THE Workspace_Explorer SHALL display a scope dropdown at the top showing the current Workspace_Scope
2. THE Workspace_Explorer scope dropdown SHALL offer "All Workspaces" as the default option
3. THE Workspace_Explorer scope dropdown SHALL list all available workspaces as selectable options
4. WHEN a user selects a different Workspace_Scope, THE System SHALL update the file tree to show only files from the selected scope
5. THE Workspace_Explorer SHALL display files and folders in a hierarchical tree structure
6. WHEN a user clicks on a folder, THE Workspace_Explorer SHALL expand or collapse that folder
7. THE Workspace_Explorer SHALL display a toolbar with New File, New Folder, and Upload buttons
8. WHEN a user clicks the New File button, THE System SHALL create a new file in the current directory
9. WHEN a user clicks the New Folder button, THE System SHALL create a new folder in the current directory
10. WHEN a user clicks the Upload button, THE System SHALL open a file picker to upload files to the current directory
11. THE Workspace_Explorer SHALL support right-click context menu for file operations (rename, delete, copy path)
12. THE Workspace_Explorer SHALL support drag-and-drop to attach files to Chat_Context

### Requirement 4: Swarm Workspace Protection

**User Story:** As a user, I want the Swarm_Workspace to be protected from accidental modifications, so that SwarmAI's internal operations remain stable.

#### Acceptance Criteria

1. THE System SHALL always display Swarm_Workspace in the workspace list
2. THE Swarm_Workspace SHALL be visually distinguished from user workspaces with a special icon or badge
3. WHEN a user attempts to edit files in Swarm_Workspace, THE System SHALL display a confirmation dialog warning about system workspace modification
4. WHEN a user attempts to delete Swarm_Workspace, THE System SHALL prevent the deletion and display an error message
5. THE Swarm_Workspace confirmation dialog SHALL require explicit user confirmation before allowing edits

### Requirement 5: Workspace Management

**User Story:** As a user, I want to add and manage workspaces, so that I can organize my projects and control which files SwarmAgent can access.

#### Acceptance Criteria

1. THE System SHALL provide an "Add Workspace" option in the Workspace_Explorer
2. WHEN a user adds a workspace, THE System SHALL offer two options: point to an existing folder OR create a new empty folder
3. WHEN pointing to an existing folder, THE System SHALL open a directory picker dialog
4. WHEN creating a new folder, THE System SHALL prompt for folder name and location
5. THE System SHALL validate that workspace paths are valid and accessible before adding
6. THE System SHALL persist workspace configurations across application restarts

### Requirement 6: Chat Context Management

**User Story:** As a user, I want to attach files to my chat context and see what's currently attached, so that SwarmAgent has the right information to assist me.

#### Acceptance Criteria

1. WHEN a user right-clicks a file in Workspace_Explorer, THE System SHALL show an "Attach to Chat" option
2. WHEN a user drags a file from Workspace_Explorer to Main_Chat_Panel, THE System SHALL attach that file to Chat_Context
3. THE Main_Chat_Panel SHALL display visual indicators showing which files are currently in Chat_Context
4. THE Main_Chat_Panel SHALL display a breadcrumb or badge showing the active Workspace_Scope
5. WHEN a user changes Workspace_Scope, THE System SHALL clear the current Chat_Context and start a fresh conversation
6. THE System SHALL allow attaching files from any workspace to the same chat session
7. WHEN a file is attached to Chat_Context, THE System SHALL display the file name and a remove button
8. WHEN a user clicks the remove button on an attached file, THE System SHALL remove that file from Chat_Context

### Requirement 7: SwarmAgent as Single User-Facing Agent

**User Story:** As a user, I want to interact with a single intelligent agent (SwarmAgent), so that I have a simple and consistent experience without needing to manually switch between agents.

#### Acceptance Criteria

1. THE Main_Chat_Panel SHALL always display SwarmAgent as the active agent
2. THE System SHALL NOT provide UI controls for users to switch between agents in the chat interface
3. THE SwarmAgent SHALL autonomously orchestrate Custom_Agents based on task requirements
4. THE System SHALL NOT expose manual orchestration controls to users
5. WHEN the application starts, THE System SHALL initialize with SwarmAgent as the active agent
6. THE SwarmAgent chat interface SHALL display the SwarmAI branded welcome message

### Requirement 8: Custom Agent Management

**User Story:** As a user, I want to create and configure custom agents, so that SwarmAgent can leverage specialized agents for different tasks.

#### Acceptance Criteria

1. THE Agents management page SHALL be accessible from the Left_Sidebar
2. THE Agents page SHALL display a list of all Custom_Agents
3. THE System SHALL support creating new Custom_Agents with name, description, model, skills, and MCP server configurations
4. THE System SHALL support editing existing Custom_Agent configurations
5. THE System SHALL support deleting Custom_Agents
6. THE System SHALL persist Custom_Agent configurations to the database

### Requirement 9: File Editor

**User Story:** As a user, I want to edit files without losing my chat conversation, so that I can make quick changes while maintaining context.

#### Acceptance Criteria

1. WHEN a user double-clicks a file in Workspace_Explorer, THE System SHALL open the File_Editor_Modal
2. THE File_Editor_Modal SHALL display as a modal or drawer overlay, preserving the chat conversation underneath
3. THE File_Editor_Modal SHALL provide syntax highlighting for common programming languages
4. THE File_Editor_Modal SHALL display the file path in the header
5. THE File_Editor_Modal SHALL provide Save and Cancel buttons
6. WHEN a user clicks Save, THE System SHALL persist changes to the file and close the modal
7. WHEN a user clicks Cancel, THE System SHALL discard changes and close the modal
8. IF a user has unsaved changes and attempts to close, THEN THE System SHALL display a confirmation dialog

### Requirement 10: Application Initialization

**User Story:** As a user, I want the application to start with sensible defaults, so that I can begin working immediately without configuration.

#### Acceptance Criteria

1. WHEN the application starts, THE System SHALL initialize with SwarmAgent as the active agent
2. WHEN the application starts, THE System SHALL set Workspace_Scope to "All Workspaces"
3. THE System SHALL ensure Swarm_Workspace is always present as a system workspace
4. WHEN the application starts, THE System SHALL pre-load the chat session with SwarmAgent and workspace context
5. IF no workspaces exist besides Swarm_Workspace, THE System SHALL prompt the user to add a workspace

### Requirement 11: Responsive Layout Behavior

**User Story:** As a user, I want the layout to adapt to different screen sizes, so that I can use SwarmAI effectively on various displays.

#### Acceptance Criteria

1. WHEN screen width is below 768 pixels, THE Workspace_Explorer SHALL auto-collapse
2. WHEN Workspace_Explorer is collapsed, THE System SHALL display a toggle button to expand it
3. THE System SHALL persist the collapsed/expanded state of Workspace_Explorer across sessions
4. THE System SHALL persist the width of Workspace_Explorer when resized by the user
5. WHEN the user resizes Workspace_Explorer, THE System SHALL enforce minimum and maximum width constraints

### Requirement 12: Swarm Workspace Initialization Status Display

**User Story:** As a user, I want to see the Swarm Workspace initialization status on the app startup screen, so that I know the system workspace is ready for use.

#### Acceptance Criteria

1. THE System_Status_API SHALL return the Swarm_Workspace status including ready state, name, and path
2. THE System_Status_API SHALL return a JSON response with a `swarm_workspace` object containing `ready` boolean, `name` string, and `path` string
3. THE Frontend_System_Service SHALL convert the `swarm_workspace` snake_case response fields to camelCase (`swarmWorkspace`)
4. WHEN the system status is received, THE Backend_Startup_Overlay SHALL display a green checkmark (✓) next to "Swarm Workspace initialized" when ready
5. WHEN the system status is received, THE Backend_Startup_Overlay SHALL display the workspace path as a nested item (e.g., "└─ ~/.swarm-ai/swarm-workspaces/SwarmWS")
6. IF the Swarm_Workspace is not ready, THEN THE Backend_Startup_Overlay SHALL display a red X (✗) next to "Swarm Workspace initialized" with an error message
7. THE Translation_File SHALL include keys for "Swarm Workspace initialized" and the workspace path display
