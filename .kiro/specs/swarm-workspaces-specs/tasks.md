# Implementation Plan: Swarm Workspaces

## Overview

This implementation plan breaks down the Swarm Workspaces feature into incremental coding tasks. The approach follows existing SwarmAI patterns for entity management (similar to Agents, Skills) and builds from backend to frontend, ensuring each step produces working, testable code.

## Tasks

- [x] 1. Backend: Database schema and table setup
  - [x] 1.1 Add swarm_workspaces table schema to SQLite database
    - Add CREATE TABLE statement to `backend/database/sqlite.py` SCHEMA
    - Include columns: id, name, file_path, context, icon, is_default, created_at, updated_at
    - Add index on is_default column
    - _Requirements: 3.6, 9.1_
  
  - [x] 1.2 Create SQLiteSwarmWorkspacesTable class
    - Extend SQLiteTable with workspace-specific methods
    - Add `get_default()` method to retrieve default workspace
    - Add `list_non_default()` method for custom workspaces
    - _Requirements: 6.9_
  
  - [x] 1.3 Register swarm_workspaces table in SQLiteDatabase class
    - Add `_swarm_workspaces` table instance
    - Add `swarm_workspaces` property accessor
    - _Requirements: 9.1_

- [x] 2. Backend: Pydantic schemas for workspace API
  - [x] 2.1 Create `backend/schemas/swarm_workspace.py` with request/response models
    - SwarmWorkspaceCreate with name, file_path, context, icon fields
    - SwarmWorkspaceUpdate with optional fields
    - SwarmWorkspaceResponse with all fields including id, is_default, timestamps
    - Add Field validators for name length (max 100) and path format
    - _Requirements: 3.1, 3.2, 8.4, 8.5_

- [x] 3. Backend: SwarmWorkspaceManager for filesystem operations
  - [x] 3.1 Create `backend/core/swarm_workspace_manager.py`
    - Define FOLDER_STRUCTURE constant with all required directories
    - Implement `validate_path()` for path traversal and format validation
    - Implement `expand_path()` to handle ~ expansion
    - _Requirements: 8.1, 8.5_
  
  - [x] 3.2 Implement folder structure creation methods
    - `create_folder_structure(workspace_path)` creates all subdirectories
    - Handle case where root path doesn't exist (create it)
    - _Requirements: 2.1, 2.4_
  
  - [x] 3.3 Implement context file creation methods
    - `create_context_files(workspace_path, workspace_name)` creates template files
    - Define OVERALL_CONTEXT_TEMPLATE with placeholder sections
    - Create empty compressed-context.md
    - _Requirements: 2.2, 2.3, 7.1, 7.2, 7.3_
  
  - [x] 3.4 Implement context file reading method
    - `read_context_files(workspace_path)` returns combined context content
    - Handle missing files gracefully
    - _Requirements: 5.3_
  
  - [x] 3.5 Implement default workspace initialization
    - `ensure_default_workspace()` creates default if not exists
    - Use DEFAULT_WORKSPACE_CONFIG constant
    - _Requirements: 1.1, 1.2, 1.5_

- [x] 4. Backend: API router for workspace CRUD operations
  - [x] 4.1 Create `backend/routers/swarm_workspaces.py` with basic endpoints
    - GET /swarm-workspaces - list all workspaces
    - GET /swarm-workspaces/default - get default workspace
    - GET /swarm-workspaces/{id} - get workspace by ID
    - _Requirements: 6.1, 6.2, 6.9_
  
  - [x] 4.2 Implement POST /swarm-workspaces endpoint
    - Validate input using Pydantic schema
    - Call SwarmWorkspaceManager to create folders and context files
    - Store workspace in database
    - Return 201 with created workspace
    - _Requirements: 4.4, 6.4_
  
  - [x] 4.3 Implement PUT /swarm-workspaces/{id} endpoint
    - Validate workspace exists (404 if not)
    - Update allowed fields
    - Update updatedAt timestamp
    - _Requirements: 3.5, 6.6_
  
  - [x] 4.4 Implement DELETE /swarm-workspaces/{id} endpoint
    - Check if workspace is default (403 if yes)
    - Delete workspace from database
    - Return 204 on success
    - _Requirements: 1.3, 6.7, 6.8_
  
  - [x] 4.5 Implement POST /swarm-workspaces/{id}/init-folders endpoint
    - Retrieve workspace by ID
    - Call SwarmWorkspaceManager.create_folder_structure()
    - _Requirements: 6.10_
  
  - [x] 4.6 Register router in main.py
    - Import and include swarm_workspaces router
    - _Requirements: 6.1_

- [x] 5. Backend: Default workspace auto-creation on startup
  - [x] 5.1 Add default workspace initialization to app startup
    - Call swarm_workspace_manager.ensure_default_workspace() in main.py lifespan
    - Ensure it runs after database initialization
    - _Requirements: 1.1, 1.4_

- [x] 6. Checkpoint - Backend API complete
  - Ensure all backend tests pass
  - Verify API endpoints work via manual testing or curl
  - Ask the user if questions arise

- [x] 7. Frontend: TypeScript types and service
  - [x] 7.1 Add SwarmWorkspace types to `desktop/src/types/index.ts`
    - SwarmWorkspace interface with camelCase fields
    - SwarmWorkspaceCreateRequest interface
    - SwarmWorkspaceUpdateRequest interface
    - _Requirements: 3.1, 3.2_
  
  - [x] 7.2 Create `desktop/src/services/swarmWorkspaces.ts`
    - Implement toCamelCase() for API response conversion
    - Implement toSnakeCase() for API request conversion
    - Implement list(), get(), getDefault(), create(), update(), delete(), initFolders() methods
    - _Requirements: 9.5_
  
  - [x] 7.3 Write unit tests for swarmWorkspaces service
    - Test toCamelCase conversion
    - Test toSnakeCase conversion
    - _Requirements: 9.5_

- [x] 8. Frontend: WorkspacesPage component
  - [x] 8.1 Create `desktop/src/pages/WorkspacesPage.tsx` with list view
    - Use TanStack Query to fetch workspaces
    - Display workspace cards with name, icon, file path
    - Show visual indicator for default workspace
    - _Requirements: 4.1, 4.2_
  
  - [x] 8.2 Implement create workspace form
    - Form fields: name, file path (with folder picker), context, icon
    - Integrate FolderPickerModal for path selection
    - Validation for required fields
    - _Requirements: 4.3, 4.4, 4.8_
  
  - [x] 8.3 Implement edit workspace modal
    - Pre-populate form with current values
    - Allow editing name, context, icon (file_path read-only after creation)
    - _Requirements: 4.5_
  
  - [x] 8.4 Implement delete workspace functionality
    - Confirmation dialog before deletion
    - Disable delete button for default workspace with tooltip
    - _Requirements: 4.6, 4.7_

- [x] 9. Frontend: Navigation integration
  - [x] 9.1 Add "Workspaces" item to left navigation in App.tsx
    - Add route for /workspaces
    - Add navigation icon
    - _Requirements: 4.1_

- [x] 10. Checkpoint - Workspaces management complete
  - Ensure workspace CRUD operations work end-to-end
  - Verify default workspace appears in list
  - Ask the user if questions arise

- [x] 11. Frontend: WorkspaceSelector component for chat
  - [x] 11.1 Create `desktop/src/components/chat/WorkspaceSelector.tsx`
    - Dropdown component showing all workspaces
    - Display workspace name and icon
    - Highlight currently selected workspace
    - _Requirements: 5.1_
  
  - [x] 11.2 Implement workspace selection logic
    - Store selected workspace ID in component state
    - Trigger onSelect callback when workspace changes
    - _Requirements: 5.2_

- [x] 12. Frontend: Chat page integration
  - [x] 12.1 Replace folder picker with WorkspaceSelector in ChatPage
    - Remove current workDir state and folder picker
    - Add selectedWorkspace state
    - Use workspace.filePath as effectiveBasePath for file browser
    - _Requirements: 5.1, 5.4_
  
  - [x] 12.2 Implement workspace auto-selection
    - Auto-select default workspace when no workspace is selected
    - Persist selected workspace ID in localStorage per agent
    - _Requirements: 5.6, 5.7_
  
  - [x] 12.3 Implement context injection on workspace selection
    - Read workspace context files when workspace is selected
    - Pass context to chat request (or handle in backend)
    - _Requirements: 5.3_
  
  - [x] 12.4 Handle workspace switching during session
    - Update file browser to show new workspace contents
    - Optionally reset session or show context change message
    - _Requirements: 5.5_

- [x] 13. Backend: Session workspace tracking
  - [x] 13.1 Add workspace_id column to sessions table
    - Add migration for existing databases
    - Update session schema if needed
    - _Requirements: 5.7_
  
  - [x] 13.2 Update chat endpoint to accept workspace_id
    - Store workspace_id with session
    - Use workspace file_path for file access control
    - _Requirements: 5.7, 8.2_

- [x] 14. Checkpoint - Chat integration complete
  - Ensure workspace selector works in chat
  - Verify file browser shows workspace contents
  - Verify context is injected into chat
  - Ask the user if questions arise

- [x] 15. Backend: Property-based tests
  - [x] 15.1 Write property test for default workspace protection
    - **Property 1: Default Workspace Protection**
    - **Validates: Requirements 1.3, 6.8**
  
  - [x] 15.2 Write property test for workspace entity invariants
    - **Property 4: Workspace Entity Invariants**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
  
  - [x] 15.3 Write property test for CRUD round-trip
    - **Property 5: Workspace CRUD Round-Trip**
    - **Validates: Requirements 6.4, 6.6, 9.3**
  
  - [x] 15.4 Write property test for path security validation
    - **Property 6: Path Security Validation**
    - **Validates: Requirements 8.1, 8.5**
  
  - [x] 15.5 Write property test for name validation
    - **Property 7: Name Validation**
    - **Validates: Requirements 8.4**
  
  - [x] 15.6 Write property test for list completeness
    - **Property 9: List Completeness**
    - **Validates: Requirements 6.1, 4.2**

- [x] 16. Final checkpoint - All tests pass
  - Run all backend tests: `cd backend && pytest`
  - Run all frontend tests: `cd desktop && npm test`
  - Ensure all tests pass, ask the user if questions arise

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
- The implementation follows existing SwarmAI patterns from agents, skills, and MCP servers
