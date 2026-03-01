# ChatHeader Tabs Redesign - Implementation Tasks

## Task 1: Add OpenTab Type and Tab State Management
- [x] 1.1 Add `OpenTab` interface to `desktop/src/pages/chat/types.ts`
  ```typescript
  interface OpenTab {
    id: string;
    sessionId?: string;
    title: string;
    agentId: string;
    isNew: boolean;
  }
  ```
- [x] 1.2 Add localStorage constants for tab persistence in `desktop/src/pages/chat/constants.ts`
- [x] 1.3 Create `useTabState` hook in `desktop/src/hooks/useTabState.ts` for managing open tabs with localStorage persistence
- [x] 1.4 Export new hook from `desktop/src/hooks/index.ts`

## Task 2: Create SessionTab Component
- [x] 2.1 Create `desktop/src/pages/chat/components/SessionTab.tsx`
  - Props: tab, isActive, onSelect, onClick, onClose, maxTitleLength
  - Display chat icon, truncated title, close button (X)
  - Active state styling with primary color
  - Hover states for inactive tabs
- [x] 2.2 Add unit tests for title truncation logic

## Task 3: Create SessionTabBar Component
- [x] 3.1 Create `desktop/src/pages/chat/components/SessionTabBar.tsx`
  - Props: tabs, activeTabId, onTabSelect, onTabClose
  - Horizontal scrollable container with smooth scroll
  - Render SessionTab for each open tab
  - Custom scrollbar styling (thin, 4px height)
- [x] 3.2 Add CSS for horizontal scroll behavior

## Task 4: Create TodoRadarSidebar Component (Mock)
- [x] 4.1 Create `desktop/src/pages/chat/components/TodoRadarSidebar.tsx`
  - Props: width, isResizing, onClose, onMouseDown
  - Header with "ToDo Radar" title and close button
  - Mock content with Overdue and Pending sections
  - Resizable left edge (similar to FileBrowserSidebar)
- [x] 4.2 Add mock ToDo data (hardcoded for this phase)

## Task 5: Redesign ChatHeader Component
- [x] 5.1 Update `ChatHeaderProps` interface with new props:
  - openTabs, activeTabId, onTabSelect, onTabClose, onNewSession
  - todoRadarCollapsed, onToggleTodoRadar
- [x] 5.2 Replace left section (remove SwarmAI logo/title, history toggle)
- [x] 5.3 Add SessionTabBar to left section
- [x] 5.4 Replace right section buttons:
  - Add "+" button for new session
  - Add "checklist" button for ToDo Radar toggle
  - Keep "history" button for Chat History toggle
- [x] 5.5 Remove Model Badge, File Browser toggle, Agent Settings button

## Task 6: Update ChatPage for Tab Management
- [x] 6.1 Add `useTabState` hook to ChatPage
- [x] 6.2 Add `todoRadarSidebar` state using `useSidebarState` hook
- [x] 6.3 Implement `handleNewSession` - creates new tab with "New Session" title
- [x] 6.4 Implement `handleTabSelect` - switches active tab and loads session messages
- [x] 6.5 Implement `handleTabClose` - removes tab, handles last-tab case
- [x] 6.6 Update `handleSendMessage` to update tab title on first message
- [x] 6.7 Update ChatHeader props to pass new handlers
- [x] 6.8 Add TodoRadarSidebar to render (conditionally based on collapsed state)

## Task 7: Tab Persistence and Restoration
- [x] 7.1 Implement tab state save to localStorage on tab changes
- [x] 7.2 Implement tab state restore on app mount
- [x] 7.3 Handle edge case: no saved tabs → create "New Session" tab
- [x] 7.4 Handle edge case: saved tabs reference deleted sessions → filter invalid

## Task 8: Export New Components
- [x] 8.1 Update `desktop/src/pages/chat/components/index.ts` to export:
  - SessionTab
  - SessionTabBar
  - TodoRadarSidebar

## Task 9: Integration Testing
- [x] 9.1 Test tab creation flow (+ button creates new tab)
- [x] 9.2 Test tab switching (clicking inactive tab loads session)
- [x] 9.3 Test tab close (X button, last tab behavior)
- [x] 9.4 Test tab title update (first message updates title)
- [x] 9.5 Test persistence (refresh restores tabs)
- [x] 9.6 Test sidebar toggles (ToDo Radar, Chat History)

## Task 10: Cleanup and Polish
- [x] 10.1 Remove unused props from ChatHeader (if any)
- [x] 10.2 Add i18n translations for new UI strings
- [x] 10.3 Verify theme compatibility (CSS variables, no hardcoded colors)
- [x] 10.4 Add keyboard navigation for tabs (optional enhancement)
