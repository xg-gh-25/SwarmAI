# ChatHeader Tabs Redesign - Requirements

## Overview
Redesign the ChatHeader component to use a browser-like tab interface for managing multiple simultaneous chat sessions, replacing the current SwarmAI branding and single-session design.

## User Stories

### 1. Session Tab Management
**As a user, I want to manage multiple chat sessions as tabs so that I can work on different conversations simultaneously.**

#### Acceptance Criteria
- 1.1 Each open chat session is displayed as a tab in the header
- 1.2 Tabs are displayed in a horizontal scrollable container when overflow occurs
- 1.3 Each tab shows the session title, truncated to 25 characters with "..." if longer
- 1.4 The active tab is visually highlighted/distinguished from inactive tabs
- 1.5 Each tab has an X button to close the session
- 1.6 Clicking an inactive tab switches to that session
- 1.7 Tab state (open sessions) persists across app restarts

### 2. New Session Creation
**As a user, I want to create new chat sessions quickly so that I can start fresh conversations.**

#### Acceptance Criteria
- 2.1 A "+" icon button in the right section creates a new chat session
- 2.2 New sessions open with default title "New Session"
- 2.3 The new session tab becomes the active tab immediately
- 2.4 When user sends first message, tab title updates to that message (truncated to 25 chars)

### 3. Session Tab Lifecycle
**As a user, I want predictable behavior when opening and closing tabs so that I never lose my work context.**

#### Acceptance Criteria
- 3.1 On app load, previously open sessions are restored as tabs
- 3.2 If no previous sessions exist, a single "New Session" tab is shown
- 3.3 Closing the last remaining tab auto-creates a new "New Session" tab
- 3.4 Closing a tab removes it from the tab bar and clears its session data

### 4. Header Right Section Actions
**As a user, I want quick access to key features from the header so that I can toggle panels efficiently.**

#### Acceptance Criteria
- 4.1 "+" icon creates new session (see User Story 2)
- 4.2 "checklist" icon toggles the ToDo Radar right sidebar
- 4.3 "history" icon toggles the existing Chat History left sidebar
- 4.4 Active toggle states are visually indicated (highlighted when panel is open)

### 5. ToDo Radar Sidebar (Mock)
**As a user, I want to see a ToDo Radar panel so that I can track pending work items.**

#### Acceptance Criteria
- 5.1 ToDo Radar opens as a right sidebar panel
- 5.2 Sidebar has header with "ToDo Radar" title and close button
- 5.3 Sidebar displays placeholder/mock ToDo items (Pending, Overdue states)
- 5.4 Sidebar is resizable like other sidebars
- 5.5 Sidebar state (open/closed, width) persists in localStorage

## Non-Functional Requirements

### Performance
- Tab switching should be instant (<100ms perceived latency)
- Horizontal scroll should be smooth (60fps)

### Accessibility
- Tabs should be keyboard navigable
- Active tab should have appropriate ARIA attributes
- Close buttons should have accessible labels

## Out of Scope (This Phase)
- Model Badge display
- File Browser toggle
- Agent Settings button
- Full ToDo Radar implementation (mock only)
