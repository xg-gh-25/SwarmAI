# Requirements Document

## Introduction

This feature redesigns the right sidebar system in the ChatPage to implement mutual exclusion behavior. Currently, three independent sidebars (TodoRadarSidebar, ChatHistorySidebar, and FileBrowserSidebar) can be opened simultaneously, consuming valuable screen real estate. This feature ensures only one sidebar can be visible at a time, maximizing the chat window space while maintaining easy access to all sidebar functionality through toggle buttons.

## Glossary

- **Right_Sidebar_Group**: The collection of three mutually exclusive sidebars: TodoRadarSidebar, ChatHistorySidebar, and FileBrowserSidebar
- **Active_Sidebar**: The currently visible sidebar within the Right_Sidebar_Group (exactly one or none)
- **Sidebar_Toggle_Button**: A button in ChatHeader that controls the visibility of a specific sidebar
- **Mutual_Exclusion**: A constraint ensuring only one sidebar from the Right_Sidebar_Group can be visible at any time

## Requirements

### Requirement 1: Mutual Exclusion Behavior

**User Story:** As a user, I want only one right sidebar visible at a time, so that I have maximum space for the chat window.

#### Acceptance Criteria

1. WHEN the user opens TodoRadarSidebar, THE Right_Sidebar_Group SHALL close ChatHistorySidebar and FileBrowserSidebar if either is open
2. WHEN the user opens ChatHistorySidebar, THE Right_Sidebar_Group SHALL close TodoRadarSidebar and FileBrowserSidebar if either is open
3. WHEN the user opens FileBrowserSidebar, THE Right_Sidebar_Group SHALL close TodoRadarSidebar and ChatHistorySidebar if either is open
4. WHILE a sidebar is open, THE Right_Sidebar_Group SHALL maintain exactly one Active_Sidebar

### Requirement 2: Toggle Button Behavior

**User Story:** As a user, I want consistent toggle button behavior, so that I can predictably control sidebar visibility.

#### Acceptance Criteria

1. WHEN the user clicks a Sidebar_Toggle_Button for a closed sidebar, THE Right_Sidebar_Group SHALL open that sidebar and close any other open sidebar
2. WHEN the user clicks a Sidebar_Toggle_Button for the currently Active_Sidebar, THE Right_Sidebar_Group SHALL keep that sidebar open (no-op behavior)
3. THE Sidebar_Toggle_Button SHALL display a highlighted state when its corresponding sidebar is the Active_Sidebar
4. THE Sidebar_Toggle_Button SHALL display a non-highlighted state when its corresponding sidebar is not the Active_Sidebar

### Requirement 3: Default State on App Startup

**User Story:** As a user, I want a consistent starting state when I open the app, so that I have a predictable experience.

#### Acceptance Criteria

1. WHEN the application starts, THE Right_Sidebar_Group SHALL display TodoRadarSidebar as the Active_Sidebar
2. WHEN the application starts, THE ChatHistorySidebar SHALL be in collapsed state
3. WHEN the application starts, THE FileBrowserSidebar SHALL be in collapsed state
4. THE Right_Sidebar_Group SHALL ignore any previously persisted sidebar state from localStorage

### Requirement 4: No State Persistence

**User Story:** As a user, I want the app to always start with the same sidebar configuration, so that I have a consistent experience across sessions.

#### Acceptance Criteria

1. THE Right_Sidebar_Group SHALL start with TodoRadarSidebar open regardless of the last session's Active_Sidebar
2. THE Right_Sidebar_Group SHALL remove or ignore localStorage entries for sidebar collapsed states
3. WHEN the user closes the application, THE Right_Sidebar_Group SHALL discard the current Active_Sidebar state

### Requirement 5: Visual Indicator for Active Sidebar

**User Story:** As a user, I want to see which sidebar is currently active, so that I know which toggle button corresponds to the visible sidebar.

#### Acceptance Criteria

1. WHILE TodoRadarSidebar is the Active_Sidebar, THE TodoRadar Sidebar_Toggle_Button SHALL display with primary color background and text
2. WHILE ChatHistorySidebar is the Active_Sidebar, THE ChatHistory Sidebar_Toggle_Button SHALL display with primary color background and text
3. WHILE FileBrowserSidebar is the Active_Sidebar, THE FileBrowser Sidebar_Toggle_Button SHALL display with primary color background and text
4. WHILE a sidebar is not the Active_Sidebar, THE corresponding Sidebar_Toggle_Button SHALL display with muted text color and no background highlight
