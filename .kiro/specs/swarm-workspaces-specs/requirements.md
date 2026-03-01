# Requirements Document

## Introduction

This document defines the requirements for the Swarm Workspaces feature in SwarmAI. Workspaces are persistent, structured memory containers that organize work by domain or project. They provide context boundaries ensuring work compounds over time rather than resetting each session. This V1 implementation establishes the foundational workspace infrastructure including the default system workspace, workspace management UI, chat integration, and backend API.

## Glossary

- **Workspace**: A persistent memory container that defines context boundaries for organizing work by domain or project
- **Default_Workspace**: The built-in system workspace ("SwarmWS-Default") that cannot be deleted and is auto-created on first app launch
- **Workspace_Context**: Free text description of a workspace's purpose, injected as system prompt prefix during chat sessions
- **Context_Files**: Markdown files (`overall-context.md`, `compressed-context.md`) stored in a workspace's Context folder that provide persistent memory
- **Workspace_Selector**: UI dropdown component that allows users to select which workspace to use during a chat session
- **File_Path**: The root folder path on the local filesystem where a workspace's folder structure is stored
- **Folder_Structure**: The standardized set of directories auto-created for each workspace (Context, Docs, Projects, Tasks, ToDos, Plans, Historical-Chats, Reports)
- **System**: The SwarmAI application backend and frontend components
- **API**: The FastAPI backend REST endpoints for workspace operations
- **Database**: The SQLite database storing workspace metadata

## Requirements

### Requirement 1: Default System Workspace

**User Story:** As a user, I want a default workspace to be automatically available when I first launch the app, so that I can start working immediately without manual setup.

#### Acceptance Criteria

1. WHEN the application launches for the first time, THE System SHALL create a default workspace named "SwarmWS-Default" with path `{app_data_dir}/swarm-workspaces/SwarmWS` (expanded at runtime to `~/.swarm-ai/swarm-workspaces/SwarmWS`)
2. WHEN the default workspace is created, THE System SHALL set the `isDefault` flag to true
3. WHEN a user attempts to delete the default workspace, THE System SHALL reject the deletion and return an error message
4. WHEN a user opens the chat page without a previously selected workspace, THE System SHALL auto-select the default workspace
5. THE Default_Workspace SHALL persist across application restarts and remain available at all times
6. THE Default_Workspace SHALL be fully initialized before the application displays the main UI
7. THE System_Status_API SHALL report the default workspace readiness status including name and path

### Requirement 2: Workspace Folder Structure

**User Story:** As a user, I want each workspace to have a standardized folder structure, so that my work is organized consistently across all workspaces.

#### Acceptance Criteria

1. WHEN a new workspace is created, THE System SHALL create the following subdirectories within the workspace File_Path: Context, Docs, Projects, Tasks, ToDos, Plans, Historical-Chats, Reports
2. WHEN the Context folder is created, THE System SHALL create an `overall-context.md` file with a template based on the workspace name
3. WHEN the Context folder is created, THE System SHALL create an empty `compressed-context.md` file
4. IF the workspace File_Path does not exist, THEN THE System SHALL create the root directory before creating subdirectories
5. IF folder creation fails due to filesystem permissions, THEN THE System SHALL return an error message indicating the specific failure reason

### Requirement 3: Workspace Entity Model

**User Story:** As a developer, I want a well-defined workspace data model, so that workspace data is stored and retrieved consistently.

#### Acceptance Criteria

1. THE Workspace entity SHALL contain the following required fields: id (UUID), name (string), filePath (string), context (string), createdAt (timestamp), updatedAt (timestamp)
2. THE Workspace entity SHALL contain the following optional fields: icon (string), isDefault (boolean defaulting to false)
3. WHEN a workspace is created, THE System SHALL generate a unique UUID for the id field
4. WHEN a workspace is created, THE System SHALL set createdAt to the current timestamp
5. WHEN a workspace is updated, THE System SHALL update the updatedAt field to the current timestamp
6. THE Database SHALL store workspaces in a `workspaces` table with columns: id, name, file_path, context, icon, is_default, created_at, updated_at

### Requirement 4: Workspaces Management Page

**User Story:** As a user, I want a dedicated page to manage my workspaces, so that I can create, view, edit, and delete workspaces easily.

#### Acceptance Criteria

1. THE System SHALL display a "Workspaces" item in the left navigation menu
2. WHEN a user navigates to the Workspaces page, THE System SHALL display a list of all workspaces showing name, icon, and file path
3. WHEN a user clicks "Create Workspace", THE System SHALL display a form with fields for name, folder path picker, context, and icon
4. WHEN a user submits a valid workspace creation form, THE System SHALL create the workspace and display it in the list
5. WHEN a user clicks edit on a workspace, THE System SHALL display a form pre-populated with the workspace's current values
6. WHEN a user clicks delete on a custom workspace, THE System SHALL prompt for confirmation and delete the workspace upon confirmation
7. WHEN a user attempts to delete the default workspace, THE System SHALL disable the delete action and display a tooltip explaining the default workspace cannot be deleted
8. IF a workspace creation form is submitted with missing required fields, THEN THE System SHALL display validation errors for each missing field

### Requirement 5: Chat Integration

**User Story:** As a user, I want to select a workspace when chatting with agents, so that the agent has the right context and file access for my work.

#### Acceptance Criteria

1. THE System SHALL display a Workspace_Selector dropdown in the chat interface replacing the current folder selector
2. WHEN a user selects a workspace from the dropdown, THE System SHALL update the active workspace for the current session
3. WHEN a workspace is selected, THE System SHALL read the workspace's Context_Files and append their contents to the system prompt
4. WHEN a workspace is selected, THE System SHALL display the workspace's folder contents in the Files panel (right sidebar)
5. WHEN a user switches workspaces during a session, THE System SHALL update the file context to reflect the new workspace's File_Path
6. WHEN a chat session starts without a selected workspace, THE System SHALL auto-select the Default_Workspace
7. THE System SHALL persist the selected workspace ID with the chat session for session continuity

### Requirement 6: Backend API Endpoints

**User Story:** As a developer, I want RESTful API endpoints for workspace operations, so that the frontend can perform CRUD operations on workspaces.

#### Acceptance Criteria

1. WHEN a GET request is made to `/workspaces`, THE API SHALL return a list of all workspaces
2. WHEN a GET request is made to `/workspaces/{id}`, THE API SHALL return the workspace with the specified id
3. IF a GET request is made to `/workspaces/{id}` with a non-existent id, THEN THE API SHALL return a 404 error
4. WHEN a POST request is made to `/workspaces` with valid data, THE API SHALL create a new workspace and return it with status 201
5. IF a POST request is made to `/workspaces` with invalid data, THEN THE API SHALL return a 422 error with validation details
6. WHEN a PUT request is made to `/workspaces/{id}` with valid data, THE API SHALL update the workspace and return the updated entity
7. WHEN a DELETE request is made to `/workspaces/{id}` for a custom workspace, THE API SHALL delete the workspace and return status 204
8. IF a DELETE request is made to `/workspaces/{id}` for the default workspace, THEN THE API SHALL return a 403 error with message "Cannot delete default workspace"
9. WHEN a GET request is made to `/workspaces/default`, THE API SHALL return the default workspace
10. WHEN a POST request is made to `/workspaces/{id}/init-folders`, THE API SHALL create the Folder_Structure for the specified workspace

### Requirement 7: Context Files Auto-Creation

**User Story:** As a user, I want context files to be automatically created with helpful templates, so that I can start documenting my workspace context immediately.

#### Acceptance Criteria

1. WHEN a workspace is created, THE System SHALL create `Context/overall-context.md` with a template containing the workspace name and placeholder sections
2. THE overall-context.md template SHALL include sections for: Workspace Purpose, Key Goals, Important Context, and Notes
3. WHEN a workspace is created, THE System SHALL create `Context/compressed-context.md` as an empty file
4. IF context file creation fails, THEN THE System SHALL log the error but not fail the workspace creation

### Requirement 8: Security and Validation

**User Story:** As a user, I want my workspaces to be secure and isolated, so that agents can only access files within the designated workspace paths.

#### Acceptance Criteria

1. WHEN a workspace File_Path is provided, THE System SHALL validate that the path does not contain path traversal sequences (e.g., `..`)
2. WHEN an agent accesses files during a chat session, THE System SHALL restrict file access to within the active workspace's File_Path
3. IF a file operation attempts to access a path outside the workspace, THEN THE System SHALL reject the operation and log a security warning
4. WHEN a workspace is created, THE System SHALL validate that the name is non-empty and does not exceed 100 characters
5. WHEN a workspace is created, THE System SHALL validate that the File_Path is an absolute path or starts with `~`

### Requirement 9: Data Persistence

**User Story:** As a user, I want my workspace data to persist reliably, so that I don't lose my workspace configurations.

#### Acceptance Criteria

1. THE System SHALL store all workspace metadata in the SQLite database
2. WHEN the application starts, THE System SHALL load workspace data from the database
3. WHEN a workspace is created, updated, or deleted, THE System SHALL immediately persist the change to the database
4. IF a database operation fails, THEN THE System SHALL return an error to the user and not leave the data in an inconsistent state
5. THE System SHALL serialize workspace data to JSON format for API responses using camelCase field names
6. THE System SHALL deserialize workspace data from JSON format using snake_case field names for database storage
