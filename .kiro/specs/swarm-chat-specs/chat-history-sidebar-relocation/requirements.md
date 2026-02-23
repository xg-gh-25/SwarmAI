# Requirements Document

## Introduction

This document specifies the requirements for relocating the Chat History sidebar from the left side to the right side of the Chat panel in SwarmAI. The Chat History sidebar currently appears on the left side of the main chat area, while the TodoRadarSidebar and FileBrowserSidebar are positioned on the right. This change will consolidate all sidebars on the right side, creating a cleaner layout with the main chat area occupying the left portion of the screen.

## Glossary

- **Chat_History_Sidebar**: The collapsible sidebar component (`ChatHistorySidebar`, renamed from `ChatSidebar`) that displays grouped chat session history, allowing users to browse, select, and delete previous conversations.
- **Chat_Panel**: The main chat interface container that includes the chat header, message area, input area, and sidebars.
- **Right_Sidebar_Area**: The region on the right side of the Chat_Panel where FileBrowserSidebar and TodoRadarSidebar are currently rendered.
- **Resize_Handle**: The draggable element that allows users to adjust sidebar width.
- **Sidebar_State**: The persisted state including collapsed status and width, stored in localStorage.

## Requirements

### Requirement 1: Relocate Chat History Sidebar to Right Side

**User Story:** As a user, I want the Chat History sidebar to appear on the right side of the chat panel, so that all sidebars are consolidated in one area and the main chat content has a cleaner left-aligned layout.

#### Acceptance Criteria

1. WHEN the Chat_History_Sidebar is expanded, THE Chat_Panel SHALL render the Chat_History_Sidebar on the right side of the main chat area.
2. THE Chat_History_Sidebar SHALL appear between the TodoRadarSidebar (to its left) and the FileBrowserSidebar (to its right) in the right sidebar area.
3. THE Chat_Panel SHALL no longer render any sidebar on the left side of the main chat area.

### Requirement 2: Update Resize Handle Position

**User Story:** As a user, I want the resize handle to be on the correct side of the sidebar, so that I can intuitively resize the Chat History sidebar from its new position.

#### Acceptance Criteria

1. THE Chat_History_Sidebar SHALL render its Resize_Handle on the left edge of the sidebar.
2. THE Chat_History_Sidebar SHALL use a left border instead of a right border to visually separate it from the main chat area.
3. WHEN the user drags the Resize_Handle, THE Chat_History_Sidebar SHALL resize correctly with the handle on the left side.

### Requirement 3: Maintain Sidebar State Persistence

**User Story:** As a user, I want my sidebar preferences to be preserved after the relocation, so that I do not lose my customized sidebar width and collapsed state.

#### Acceptance Criteria

1. THE Chat_History_Sidebar SHALL continue to use the existing localStorage keys (`chatSidebarCollapsed`, `chatSidebarWidth`) for state persistence.
2. WHEN the application loads, THE Chat_History_Sidebar SHALL restore the previously saved width and collapsed state.
3. WHEN the user resizes or toggles the Chat_History_Sidebar, THE Sidebar_State SHALL be persisted to localStorage.

### Requirement 4: Preserve Sidebar Functionality

**User Story:** As a user, I want all existing Chat History sidebar features to work correctly after relocation, so that I can continue to manage my chat sessions.

#### Acceptance Criteria

1. THE Chat_History_Sidebar SHALL display grouped chat sessions organized by time period.
2. WHEN the user clicks a chat session, THE Chat_Panel SHALL load and display that session's messages.
3. WHEN the user clicks the New Chat button, THE Chat_Panel SHALL create a new chat session.
4. WHEN the user clicks the delete button on a session, THE Chat_Panel SHALL prompt for confirmation and delete the session upon confirmation.
5. WHEN the user clicks the close button, THE Chat_History_Sidebar SHALL collapse.

### Requirement 5: Update Header Toggle Button Behavior

**User Story:** As a user, I want the Chat History toggle button in the header to correctly indicate the sidebar's new position, so that the UI remains intuitive.

#### Acceptance Criteria

1. WHEN the user clicks the Chat History toggle button in the header, THE Chat_History_Sidebar SHALL expand or collapse on the right side.
2. THE toggle button icon or visual indicator SHALL correctly reflect the sidebar's expanded or collapsed state.
