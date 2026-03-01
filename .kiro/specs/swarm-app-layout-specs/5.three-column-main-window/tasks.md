# Implementation Plan: Three-Column Layout

## Overview

This implementation plan transforms SwarmAI's desktop application from its current single-sidebar layout to a modern 3-column IDE-like interface. The implementation follows an incremental approach, starting with the core layout structure, then adding the Workspace Explorer, updating the chat panel, and finally implementing the file editor and protection features.

## Tasks

- [x] 1. Create Layout Context and Provider
  - [x] 1.1 Create LayoutContext with state for workspace explorer collapsed/width, workspace scope, and active modal
    - Create `desktop/src/contexts/LayoutContext.tsx`
    - Define `LayoutContextValue` interface with all state and setters
    - Implement localStorage persistence for collapsed state and width
    - _Requirements: 11.3, 11.4_
  
  - [x] 1.2 Write property tests for layout state persistence
    - **Property 27: Collapse State Persistence**
    - **Property 28: Width Persistence**
    - **Validates: Requirements 11.3, 11.4**

- [x] 2. Implement ThreeColumnLayout Component
  - [x] 2.1 Create ThreeColumnLayout component replacing current Layout
    - Create `desktop/src/components/layout/ThreeColumnLayout.tsx`
    - Implement flex container with three columns
    - Add TopBar with window dragging support
    - Wire up LayoutContext provider
    - _Requirements: 1.1, 1.2, 1.3, 1.4_
  
  - [x] 2.2 Implement responsive behavior and auto-collapse
    - Add window resize listener
    - Auto-collapse Workspace Explorer below 768px
    - Maintain minimum widths for each column
    - _Requirements: 1.5, 1.8, 11.1_
  
  - [x] 2.3 Write property test for layout structure on resize
    - **Property 1: Layout Structure Maintained on Resize**
    - **Validates: Requirements 1.5**

- [x] 3. Update Left Sidebar for Navigation
  - [x] 3.1 Modify Sidebar component for icon-only navigation with modal triggers
    - Update `desktop/src/components/common/Sidebar.tsx`
    - Add navigation icons for Skills, MCP Servers, Agents, Settings
    - Implement click handlers to open modals via LayoutContext
    - Add SwarmAI logo at top
    - Keep GitHub link
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6_
  
  - [x] 3.2 Implement active state visual indicators
    - Add CSS styling for active modal state
    - Highlight corresponding nav icon when modal is open
    - _Requirements: 2.5_
  
  - [x] 3.3 Write property test for navigation modal opening
    - **Property 4: Navigation Modal Opening**
    - **Property 5: Active Navigation Indicator**
    - **Validates: Requirements 2.2, 2.5**

- [x] 4. Checkpoint - Core Layout Structure
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Implement Workspace Explorer Component
  - [x] 5.1 Create WorkspaceExplorer component with scope dropdown
    - Create `desktop/src/components/workspace-explorer/WorkspaceExplorer.tsx`
    - Implement scope dropdown with "All Workspaces" default
    - Fetch and display workspaces from swarmWorkspacesService
    - _Requirements: 3.1, 3.2, 3.3_
  
  - [x] 5.2 Implement file tree with hierarchical display
    - Create `desktop/src/components/workspace-explorer/FileTree.tsx`
    - Create `desktop/src/components/workspace-explorer/FileTreeNode.tsx`
    - Implement recursive tree rendering
    - Add expand/collapse functionality for folders
    - _Requirements: 3.5, 3.6_
  
  - [x] 5.3 Implement workspace scope filtering
    - Filter file tree based on selected scope
    - Show all workspaces when "All Workspaces" selected
    - Show single workspace files when specific workspace selected
    - _Requirements: 3.4_
  
  - [x] 5.4 Write property tests for workspace explorer
    - **Property 6: Workspace Dropdown Population**
    - **Property 7: Workspace Scope Filtering**
    - **Property 8: Folder Expand/Collapse Toggle**
    - **Validates: Requirements 3.3, 3.4, 3.6**

- [x] 6. Implement Explorer Toolbar and File Operations
  - [x] 6.1 Create ExplorerToolbar with New File, New Folder, Upload buttons
    - Create `desktop/src/components/workspace-explorer/ExplorerToolbar.tsx`
    - Implement New File button with file creation dialog
    - Implement New Folder button with folder creation dialog
    - Implement Upload button with file picker
    - _Requirements: 3.7, 3.8, 3.9, 3.10_
  
  - [x] 6.2 Implement right-click context menu
    - Create `desktop/src/components/workspace-explorer/FileContextMenu.tsx`
    - Add Rename, Delete, Copy Path, Attach to Chat options
    - _Requirements: 3.11, 6.1_
  
  - [x] 6.3 Write property tests for file operations
    - **Property 9: File Creation in Current Directory**
    - **Property 10: Folder Creation in Current Directory**
    - **Validates: Requirements 3.8, 3.9**

- [x] 7. Implement Resize and Collapse Functionality
  - [x] 7.1 Add resize handle to WorkspaceExplorer
    - Create `desktop/src/components/workspace-explorer/ResizeHandle.tsx`
    - Implement drag-to-resize with mouse events
    - Enforce min/max width constraints (200px - 500px)
    - _Requirements: 1.7, 11.5_
  
  - [x] 7.2 Implement collapse toggle button
    - Add collapse button to WorkspaceExplorer header
    - Show expand button when collapsed
    - Animate collapse/expand transitions
    - _Requirements: 1.6, 11.2_
  
  - [x] 7.3 Write property tests for resize and collapse
    - **Property 2: Workspace Explorer Collapse Toggle**
    - **Property 3: Workspace Explorer Resize Constraints**
    - **Property 29: Collapse Toggle Button Visibility**
    - **Validates: Requirements 1.6, 1.7, 11.2, 11.5**

- [x] 8. Checkpoint - Workspace Explorer Complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Update Main Chat Panel
  - [x] 9.1 Create ChatContextBar component for context indicators
    - Create `desktop/src/components/chat/ChatContextBar.tsx`
    - Display workspace scope badge
    - Display attached files list with remove buttons
    - _Requirements: 6.3, 6.4, 6.7_
  
  - [x] 9.2 Implement drag-drop file attachment
    - Add drop zone to MainChatPanel
    - Handle file drops from WorkspaceExplorer
    - Update ChatContext with attached files
    - _Requirements: 3.12, 6.2_
  
  - [x] 9.3 Implement workspace scope change behavior
    - Clear ChatContext when scope changes
    - Start fresh conversation session
    - Display scope change notification
    - _Requirements: 6.5_
  
  - [x] 9.4 Write property tests for chat context
    - **Property 11: Drag-Drop File Attachment**
    - **Property 16: Chat Context File Indicators**
    - **Property 17: Workspace Scope Change Clears Context**
    - **Property 18: Cross-Workspace File Attachment**
    - **Property 19: File Removal from Context**
    - **Validates: Requirements 3.12, 6.2, 6.3, 6.5, 6.6, 6.7, 6.8**

- [x] 10. Implement SwarmAgent as Single Agent
  - [x] 10.1 Update ChatPage to always use SwarmAgent
    - Remove agent selector from chat interface
    - Always initialize with SwarmAgent (default agent)
    - Display SwarmAI branded welcome message
    - _Requirements: 7.1, 7.2, 7.5, 7.6_
  
  - [x] 10.2 Update application initialization
    - Set SwarmAgent as active on startup
    - Set workspace scope to "All Workspaces" on startup
    - Pre-load chat session with context
    - _Requirements: 10.1, 10.2, 10.4_
  
  - [x] 10.3 Write property test for SwarmAgent invariant
    - **Property 20: SwarmAgent Always Active**
    - **Validates: Requirements 7.1**

- [x] 11. Checkpoint - Chat Panel Complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. Implement Swarm Workspace Protection
  - [x] 12.1 Create SwarmWorkspaceWarningDialog component
    - Create `desktop/src/components/common/SwarmWorkspaceWarningDialog.tsx`
    - Display warning message about system workspace
    - Require explicit confirmation before proceeding
    - _Requirements: 4.3, 4.5_
  
  - [x] 12.2 Implement Swarm Workspace visual distinction
    - Add lock icon/badge to Swarm Workspace in file tree
    - Apply distinct styling to Swarm Workspace node
    - _Requirements: 4.2_
  
  - [x] 12.3 Implement deletion prevention for Swarm Workspace
    - Block delete operations on Swarm Workspace
    - Display error message on delete attempt
    - Ensure Swarm Workspace is always present
    - _Requirements: 4.1, 4.4, 10.3_
  
  - [x] 12.4 Write property tests for Swarm Workspace protection
    - **Property 12: Swarm Workspace Invariant**
    - **Property 13: Swarm Workspace Edit Protection**
    - **Validates: Requirements 4.1, 4.3, 4.4, 4.5, 10.3**

- [x] 13. Implement Workspace Management
  - [x] 13.1 Create AddWorkspaceDialog component
    - Create `desktop/src/components/workspace-explorer/AddWorkspaceDialog.tsx`
    - Implement "Point to existing folder" option with directory picker
    - Implement "Create new folder" option with name/location prompt
    - _Requirements: 5.1, 5.2, 5.3, 5.4_
  
  - [x] 13.2 Implement workspace path validation
    - Validate path exists and is accessible
    - Display error for invalid paths
    - Prevent adding invalid workspaces
    - _Requirements: 5.5_
  
  - [x] 13.3 Handle empty workspace state
    - Detect when only Swarm Workspace exists
    - Prompt user to add a workspace
    - _Requirements: 10.5_
  
  - [x] 13.4 Write property tests for workspace management
    - **Property 14: Workspace Path Validation**
    - **Property 15: Workspace Persistence Round-Trip**
    - **Validates: Requirements 5.5, 5.6**

- [x] 14. Checkpoint - Workspace Management Complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Implement File Editor Modal
  - [x] 15.1 Create FileEditorModal component
    - Create `desktop/src/components/common/FileEditorModal.tsx`
    - Implement modal overlay preserving chat underneath
    - Display file path in header
    - Add Save and Cancel buttons
    - _Requirements: 9.1, 9.2, 9.4, 9.5_
  
  - [x] 15.2 Integrate syntax highlighting
    - Add code editor library (Monaco or CodeMirror)
    - Configure syntax highlighting for common languages
    - _Requirements: 9.3_
  
  - [x] 15.3 Implement save and cancel functionality
    - Save changes to file on Save click
    - Discard changes on Cancel click
    - Close modal after either action
    - _Requirements: 9.6, 9.7_
  
  - [x] 15.4 Implement unsaved changes warning
    - Track dirty state (content changed)
    - Show confirmation dialog on close with unsaved changes
    - _Requirements: 9.8_
  
  - [x] 15.5 Write property tests for file editor
    - **Property 23: File Editor Opens on Double-Click**
    - **Property 24: File Editor Save Persistence**
    - **Property 25: File Editor Cancel Discards Changes**
    - **Property 26: Unsaved Changes Warning**
    - **Validates: Requirements 9.1, 9.6, 9.7, 9.8**

- [x] 16. Create Management Page Modals
  - [x] 16.1 Create SkillsModal component
    - Create `desktop/src/components/modals/SkillsModal.tsx`
    - Wrap existing SkillsPage content in modal
    - _Requirements: 2.2_
  
  - [x] 16.2 Create MCPServersModal component
    - Create `desktop/src/components/modals/MCPServersModal.tsx`
    - Wrap existing MCPPage content in modal
    - _Requirements: 2.2_
  
  - [x] 16.3 Create AgentsModal component
    - Create `desktop/src/components/modals/AgentsModal.tsx`
    - Wrap existing AgentsPage content in modal
    - Display list of Custom_Agents with CRUD operations
    - _Requirements: 2.2, 8.1, 8.2_
  
  - [x] 16.4 Create SettingsModal component
    - Create `desktop/src/components/modals/SettingsModal.tsx`
    - Wrap existing SettingsPage content in modal
    - _Requirements: 2.2_
  
  - [x] 16.5 Write property tests for agent management
    - **Property 21: Agent CRUD Round-Trip**
    - **Property 22: Agent List Display**
    - **Validates: Requirements 8.2, 8.3, 8.4, 8.5, 8.6**

- [x] 17. Update App Routing and Integration
  - [x] 17.1 Update App.tsx to use ThreeColumnLayout
    - Replace Layout with ThreeColumnLayout
    - Remove page-based routing for Skills, MCP, Agents, Settings
    - Keep ChatPage as main content
    - _Requirements: 1.1_
  
  - [x] 17.2 Wire up modal system
    - Connect LayoutContext modal state to modal components
    - Implement modal open/close handlers
    - _Requirements: 2.2_

- [x] 18. Final Checkpoint - All Features Complete
  - Ensure all tests pass, ask the user if questions arise.
  - Verify all requirements are implemented
  - Test responsive behavior at various screen sizes

- [x] 19. Implement Swarm Workspace Initialization Status Display
  - [x] 19.1 Update backend system status API to include Swarm Workspace status
    - Update `backend/routers/system.py` to add `SwarmWorkspaceStatus` model
    - Add `swarm_workspace` field to `SystemStatusResponse` with `ready`, `name`, and `path`
    - Query the default Swarm Workspace from the database and populate status
    - _Requirements: 12.1, 12.2_
  
  - [ ]* 19.2 Write property test for Swarm Workspace status response schema
    - **Property 30: Swarm Workspace Status Response Schema**
    - **Validates: Requirements 12.1, 12.2**
  
  - [x] 19.3 Update frontend system service for Swarm Workspace status
    - Update `desktop/src/services/system.ts` to add `SwarmWorkspaceStatus` interface
    - Update `SystemStatus` interface to include `swarmWorkspace` field
    - Update `toCamelCase()` function to handle `swarm_workspace` → `swarmWorkspace` conversion
    - _Requirements: 12.3_
  
  - [ ]* 19.4 Write property test for Swarm Workspace status case conversion
    - **Property 31: Swarm Workspace Status Case Conversion**
    - **Validates: Requirements 12.3**
  
  - [x] 19.5 Update BackendStartupOverlay to display Swarm Workspace status
    - Update `desktop/src/components/common/BackendStartupOverlay.tsx`
    - Add "Swarm Workspace initialized" status item with checkmark when ready
    - Add nested item showing workspace path (e.g., "└─ ~/.swarm-ai/swarm-workspaces/SwarmWS")
    - Display red X with error message if Swarm Workspace is not ready
    - _Requirements: 12.4, 12.5, 12.6_
  
  - [ ]* 19.6 Write property test for Swarm Workspace initialization display
    - **Property 32: Swarm Workspace Initialization Display**
    - **Validates: Requirements 12.4, 12.5**
  
  - [x] 19.7 Add i18n translation keys for Swarm Workspace status
    - Update `desktop/src/i18n/locales/en/translation.json`
    - Add keys for "Swarm Workspace initialized" and workspace path display
    - _Requirements: 12.7_

- [x] 20. Final Checkpoint - Swarm Workspace Status Complete
  - Ensure all tests pass, ask the user if questions arise.
  - Verify Swarm Workspace status displays correctly on startup overlay
  - Test with both ready and not-ready Swarm Workspace states

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
- The implementation uses TypeScript with React and follows the existing codebase patterns
- CSS should use `--color-*` variables for theming consistency
- Task 19 adds Swarm Workspace initialization status display to the startup overlay, following the pattern from the swarm-init-status-display spec
