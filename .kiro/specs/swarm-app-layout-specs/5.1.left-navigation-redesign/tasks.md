# Implementation Plan: Left Navigation Redesign

## Overview

This implementation plan converts the left navigation redesign design into actionable coding tasks. The approach is incremental: first updating the shared infrastructure (LayoutContext, Modal), then creating new modals, updating existing modals, and finally wiring everything together in the layout.

## Tasks

- [x] 1. Update LayoutContext with new modal types
  - Add 'workspaces' and 'swarmcore' to the ModalType union type
  - Maintain existing modal types: 'skills', 'mcp', 'agents', 'settings', 'file-editor'
  - _Requirements: 3.1, 3.2, 3.3_

- [x] 2. Enhance Modal component with fullscreen size
  - [x] 2.1 Add 'fullscreen' to the size prop type
    - Update ModalProps interface to include 'fullscreen' in size union
    - _Requirements: 6.1_
  
  - [x] 2.2 Implement fullscreen size CSS classes
    - Add 'fullscreen' key to sizeClasses object with value 'w-[95vw] h-[90vh] max-w-none'
    - Ensure modal remains centered with existing flex centering
    - _Requirements: 6.2, 6.3, 6.4_

- [x] 3. Create WorkspacesModal component
  - [x] 3.1 Create WorkspacesModal.tsx file
    - Import Modal component and WorkspacesPage
    - Create WorkspacesModal component with isOpen and onClose props
    - Render WorkspacesPage inside Modal with size="fullscreen"
    - Apply overflow styling for scrollable content
    - _Requirements: 4.1, 4.2, 4.3, 4.4_
  
  - [x] 3.2 Write property test for WorkspacesModal
    - **Property 3: Close Button Closes Any Modal**
    - **Validates: Requirements 4.3**

- [x] 4. Create SwarmCoreModal component
  - [x] 4.1 Create SwarmCoreModal.tsx file
    - Import Modal component and SwarmCorePage
    - Create SwarmCoreModal component with isOpen and onClose props
    - Render SwarmCorePage inside Modal with size="fullscreen"
    - Apply overflow styling for scrollable content
    - _Requirements: 5.1, 5.2, 5.3, 5.4_
  
  - [x] 4.2 Write property test for SwarmCoreModal
    - **Property 4: Escape Key Closes Any Modal**
    - **Validates: Requirements 5.4**

- [x] 5. Update existing modals to fullscreen size
  - [x] 5.1 Update AgentsModal to use fullscreen size
    - Change size prop from '3xl' to 'fullscreen'
    - Adjust content container height classes for fullscreen
    - _Requirements: 7.1_
  
  - [x] 5.2 Update SkillsModal to use fullscreen size
    - Change size prop to 'fullscreen'
    - Adjust content container height classes for fullscreen
    - _Requirements: 7.2_
  
  - [x] 5.3 Update MCPServersModal to use fullscreen size
    - Change size prop to 'fullscreen'
    - Adjust content container height classes for fullscreen
    - _Requirements: 7.3_
  
  - [x] 5.4 Update SettingsModal to use fullscreen size
    - Change size prop to 'fullscreen'
    - Adjust content container height classes for fullscreen
    - _Requirements: 7.4_

- [x] 6. Update modals index exports
  - Add export for WorkspacesModal
  - Add export for SwarmCoreModal
  - Maintain existing exports
  - _Requirements: 9.1, 9.2, 9.3_

- [x] 7. Checkpoint - Verify modal infrastructure
  - Ensure all modal components compile without errors
  - Ensure all tests pass, ask the user if questions arise

- [x] 8. Update LeftSidebar navigation items
  - [x] 8.1 Update navItems array configuration
    - Replace current navItems with new 5-item configuration
    - Order: Workspaces (workspaces icon), SwarmCore (grid_view), Agents (smart_toy), Skills (auto_awesome), MCP Servers (hub)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_
  
  - [x] 8.2 Write property test for navigation item order
    - **Property 1: Navigation Item Order Consistency**
    - **Validates: Requirements 1.1**
  
  - [x] 8.3 Write property test for navigation click behavior
    - **Property 2: Navigation Click Opens Corresponding Modal**
    - **Validates: Requirements 2.3, 4.1, 5.1**

- [x] 9. Update ThreeColumnLayout modal rendering
  - [x] 9.1 Import new modal components
    - Import WorkspacesModal and SwarmCoreModal from modals index
    - _Requirements: 4.1, 5.1_
  
  - [x] 9.2 Add modal render conditions
    - Add WorkspacesModal render with isOpen={activeModal === 'workspaces'}
    - Add SwarmCoreModal render with isOpen={activeModal === 'swarmcore'}
    - _Requirements: 4.1, 5.1_
  
  - [x] 9.3 Write property test for active state
    - **Property 5: Active State Reflects Open Modal**
    - **Validates: Requirements 8.1, 8.4**

- [x] 10. Final checkpoint - Full integration verification
  - Ensure all tests pass
  - Verify navigation flow works end-to-end
  - Ask the user if questions arise

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
- The implementation order ensures dependencies are satisfied before dependent code
