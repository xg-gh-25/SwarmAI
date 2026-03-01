# Requirements Document

## Introduction

This document defines the requirements for redesigning the left navigation sidebar in the SwarmAI desktop application. The redesign introduces a new 5-item navigation structure with full-screen modal overlays, replacing the current smaller modal popups with larger, more informative displays.

## Glossary

- **Left_Sidebar**: The narrow vertical navigation column (56px width) on the left side of the application containing navigation icons
- **Navigation_Item**: A clickable icon button in the Left_Sidebar that opens a corresponding modal overlay
- **Modal_Overlay**: A full-screen overlay panel that displays content when a Navigation_Item is clicked
- **Full_Screen_Modal**: A modal that covers the entire viewport (minus safe margins) for maximum content display
- **LayoutContext**: The React context that manages modal state and navigation across the application
- **ModalType**: A TypeScript union type defining valid modal identifiers ('workspaces' | 'swarmcore' | 'agents' | 'skills' | 'mcp' | 'settings')

## Requirements

### Requirement 1: Navigation Structure

**User Story:** As a user, I want a clear and organized navigation sidebar, so that I can quickly access different sections of the application.

#### Acceptance Criteria

1. THE Left_Sidebar SHALL display exactly 5 main Navigation_Items in the following order from top to bottom: Workspaces, SwarmCore, Agents, Skills, MCP Servers
2. WHEN the Workspaces Navigation_Item is clicked, THE Left_Sidebar SHALL display the `workspaces` icon from Material Symbols
3. WHEN the SwarmCore Navigation_Item is clicked, THE Left_Sidebar SHALL display the `grid_view` icon from Material Symbols
4. WHEN the Agents Navigation_Item is clicked, THE Left_Sidebar SHALL display the `smart_toy` icon from Material Symbols
5. WHEN the Skills Navigation_Item is clicked, THE Left_Sidebar SHALL display the `auto_awesome` icon from Material Symbols
6. WHEN the MCP Servers Navigation_Item is clicked, THE Left_Sidebar SHALL display the `hub` icon from Material Symbols

### Requirement 2: Bottom Section Navigation

**User Story:** As a user, I want access to settings and external resources from the sidebar, so that I can configure the application and access help.

#### Acceptance Criteria

1. THE Left_Sidebar SHALL display a Settings button with the `settings` icon in the bottom section
2. THE Left_Sidebar SHALL display a GitHub link icon in the bottom section below the Settings button
3. WHEN the Settings button is clicked, THE Left_Sidebar SHALL open the SettingsModal
4. WHEN the GitHub link is clicked, THE Left_Sidebar SHALL open the SwarmAI GitHub repository in an external browser

### Requirement 3: Modal Type Registration

**User Story:** As a developer, I want the LayoutContext to support all navigation modal types, so that the modal system can manage all navigation destinations.

#### Acceptance Criteria

1. THE LayoutContext SHALL define ModalType to include 'workspaces' as a valid modal identifier
2. THE LayoutContext SHALL define ModalType to include 'swarmcore' as a valid modal identifier
3. THE LayoutContext SHALL maintain existing modal types: 'skills', 'mcp', 'agents', 'settings', 'file-editor'

### Requirement 4: Workspaces Modal

**User Story:** As a user, I want to manage workspaces in a full-screen modal, so that I have ample space to view and organize my workspaces.

#### Acceptance Criteria

1. WHEN the Workspaces Navigation_Item is clicked, THE system SHALL open the WorkspacesModal as a Full_Screen_Modal
2. THE WorkspacesModal SHALL render the existing WorkspacesPage content within the modal container
3. WHEN the WorkspacesModal close button is clicked, THE system SHALL close the modal and return to the main view
4. WHEN the Escape key is pressed while WorkspacesModal is open, THE system SHALL close the modal

### Requirement 5: SwarmCore Modal

**User Story:** As a user, I want to access the SwarmCore dashboard in a full-screen modal, so that I can view system statistics and quick actions with sufficient space.

#### Acceptance Criteria

1. WHEN the SwarmCore Navigation_Item is clicked, THE system SHALL open the SwarmCoreModal as a Full_Screen_Modal
2. THE SwarmCoreModal SHALL render the existing SwarmCorePage content within the modal container
3. WHEN the SwarmCoreModal close button is clicked, THE system SHALL close the modal and return to the main view
4. WHEN the Escape key is pressed while SwarmCoreModal is open, THE system SHALL close the modal

### Requirement 6: Full-Screen Modal Sizing

**User Story:** As a user, I want modals to use the full screen, so that I can see more information without scrolling.

#### Acceptance Criteria

1. THE Modal component SHALL support a 'fullscreen' size option that spans the full viewport with appropriate margins
2. WHEN a Full_Screen_Modal is rendered, THE Modal SHALL use width of 95vw and height of 90vh
3. WHEN a Full_Screen_Modal is rendered, THE Modal SHALL be centered in the viewport
4. THE Full_Screen_Modal SHALL maintain the existing modal header with title and close button

### Requirement 7: Existing Modal Enlargement

**User Story:** As a user, I want existing modals (Agents, Skills, MCP Servers, Settings) to be larger, so that I can view more content at once.

#### Acceptance Criteria

1. THE AgentsModal SHALL use the 'fullscreen' size option instead of '3xl'
2. THE SkillsModal SHALL use the 'fullscreen' size option instead of its current size
3. THE MCPServersModal SHALL use the 'fullscreen' size option instead of its current size
4. THE SettingsModal SHALL use the 'fullscreen' size option instead of its current size

### Requirement 8: Navigation Item Active State

**User Story:** As a user, I want to see which navigation item is currently active, so that I know which section I am viewing.

#### Acceptance Criteria

1. WHEN a Modal_Overlay is open, THE corresponding Navigation_Item SHALL display an active visual state
2. THE active Navigation_Item SHALL have a highlighted background color using the primary color with transparency
3. THE active Navigation_Item SHALL have a ring border indicator
4. WHEN no Modal_Overlay is open, THE system SHALL display no Navigation_Item in active state

### Requirement 9: Modal Export Registration

**User Story:** As a developer, I want new modals to be properly exported, so that they can be imported and used throughout the application.

#### Acceptance Criteria

1. THE modals index file SHALL export WorkspacesModal
2. THE modals index file SHALL export SwarmCoreModal
3. THE modals index file SHALL maintain exports for existing modals: AgentsModal, SkillsModal, MCPServersModal, SettingsModal
