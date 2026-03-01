# ChatHeader Tabs Redesign - Design Document

## Overview
This document describes the technical design for redesigning the ChatHeader component to support browser-like session tabs and updated action buttons.

## Architecture

### Component Structure

```
ChatHeader (redesigned)
├── Left Section: SessionTabBar
│   └── SessionTab[] (scrollable)
│       ├── Tab title (truncated)
│       └── Close button (X)
└── Right Section: HeaderActions
    ├── NewSessionButton (+)
    ├── TodoRadarToggle (checklist)
    └── HistoryToggle (history)

TodoRadarSidebar (new mock component)
├── Header (title + close)
└── Content (mock ToDo items)
```

### State Management

#### New State in ChatPage
```typescript
// Open tabs state - array of session references
const [openTabs, setOpenTabs] = useState<OpenTab[]>([]);
const [activeTabId, setActiveTabId] = useState<string | null>(null);

// ToDo Radar sidebar state (using existing useSidebarState hook)
const todoRadarSidebar = useSidebarState({
  storageKey: 'todoRadarSidebarCollapsed',
  widthStorageKey: 'todoRadarSidebarWidth',
  defaultCollapsed: true,
  defaultWidth: 300,
  minWidth: 200,
  maxWidth: 500,
});
```

#### OpenTab Interface
```typescript
interface OpenTab {
  id: string;           // Unique tab ID (can match sessionId or be temporary)
  sessionId?: string;   // Backend session ID (undefined for new unsaved sessions)
  title: string;        // Display title
  agentId: string;      // Associated agent
  isNew: boolean;       // True if no messages sent yet
}
```

### Tab Persistence

Store open tabs in localStorage:
```typescript
const OPEN_TABS_STORAGE_KEY = 'swarmAI_openTabs';
const ACTIVE_TAB_STORAGE_KEY = 'swarmAI_activeTabId';

// On mount: restore tabs from localStorage
// On tab change: persist to localStorage
```

### Component APIs

#### ChatHeader Props (Updated)
```typescript
interface ChatHeaderProps {
  // Tab management
  openTabs: OpenTab[];
  activeTabId: string | null;
  onTabSelect: (tabId: string) => void;
  onTabClose: (tabId: string) => void;
  onNewSession: () => void;
  
  // Sidebar toggles
  chatSidebarCollapsed: boolean;
  todoRadarCollapsed: boolean;
  onToggleChatSidebar: () => void;
  onToggleTodoRadar: () => void;
}
```

#### SessionTabBar Props
```typescript
interface SessionTabBarProps {
  tabs: OpenTab[];
  activeTabId: string | null;
  onTabSelect: (tabId: string) => void;
  onTabClose: (tabId: string) => void;
  maxTitleLength?: number; // default 25
}
```

#### TodoRadarSidebar Props
```typescript
interface TodoRadarSidebarProps {
  width: number;
  isResizing: boolean;
  onClose: () => void;
  onMouseDown: (e: React.MouseEvent) => void;
}
```

## UI Layout

### Header Layout (h-12)
```
┌─────────────────────────────────────────────────────────────────────┐
│ [Tab1][Tab2][Tab3]...←scroll→        │  [+] [checklist] [history]  │
│ ◄─── SessionTabBar (flex-1) ───►     │  ◄─── HeaderActions ───►    │
└─────────────────────────────────────────────────────────────────────┘
```

### Tab Design
```
┌──────────────────────────┐
│ 💬 Session title...  [×] │  ← Active tab (highlighted bg)
└──────────────────────────┘

┌──────────────────────────┐
│ 💬 Another session   [×] │  ← Inactive tab (muted)
└──────────────────────────┘
```

### ToDo Radar Sidebar (Mock)
```
┌─────────────────────────────┐
│ ☑ ToDo Radar           [×] │  ← Header
├─────────────────────────────┤
│                             │
│ 🔴 Overdue (2)              │
│ ├─ Review PR #123           │
│ └─ Reply to email           │
│                             │
│ 🟡 Pending (3)              │
│ ├─ Update documentation     │
│ ├─ Schedule meeting         │
│ └─ Complete report          │
│                             │
│ (Mock data - not functional)│
└─────────────────────────────┘
```

## Implementation Details

### Tab Title Update Logic
```typescript
// In handleSendMessage, after first message:
if (activeTab?.isNew && messageText.trim()) {
  const newTitle = messageText.slice(0, 25) + (messageText.length > 25 ? '...' : '');
  updateTabTitle(activeTabId, newTitle);
  setTabIsNew(activeTabId, false);
}
```

### Tab Close Logic
```typescript
const handleTabClose = (tabId: string) => {
  const remainingTabs = openTabs.filter(t => t.id !== tabId);
  
  if (remainingTabs.length === 0) {
    // Auto-create new session tab
    const newTab = createNewSessionTab();
    setOpenTabs([newTab]);
    setActiveTabId(newTab.id);
  } else {
    setOpenTabs(remainingTabs);
    // If closing active tab, switch to adjacent tab
    if (activeTabId === tabId) {
      const closedIndex = openTabs.findIndex(t => t.id === tabId);
      const newActiveIndex = Math.min(closedIndex, remainingTabs.length - 1);
      setActiveTabId(remainingTabs[newActiveIndex].id);
    }
  }
};
```

### Horizontal Scroll Styling
```css
.session-tab-bar {
  display: flex;
  overflow-x: auto;
  scrollbar-width: thin;
  scroll-behavior: smooth;
}

.session-tab-bar::-webkit-scrollbar {
  height: 4px;
}
```

## Files to Modify

1. `desktop/src/pages/chat/components/ChatHeader.tsx` - Complete redesign
2. `desktop/src/pages/ChatPage.tsx` - Add tab state management
3. `desktop/src/pages/chat/components/index.ts` - Export new components

## Files to Create

1. `desktop/src/pages/chat/components/SessionTabBar.tsx` - Tab bar component
2. `desktop/src/pages/chat/components/SessionTab.tsx` - Individual tab component
3. `desktop/src/pages/chat/components/TodoRadarSidebar.tsx` - Mock sidebar
4. `desktop/src/pages/chat/types.ts` - Add OpenTab interface (or extend existing)

## Testing Considerations

### Property-Based Tests
- Tab title truncation always produces valid output ≤28 chars (25 + "...")
- Tab operations maintain invariant: always ≥1 tab exists
- Tab persistence round-trips correctly

### Unit Tests
- Tab creation with default title
- Tab title update on first message
- Tab close behavior (last tab, active tab, inactive tab)
- Sidebar toggle states

## Migration Notes

- Existing single-session behavior maps to single-tab
- ChatSidebar (history) functionality unchanged, just toggle moved
- FileBrowser and Settings buttons hidden but code preserved for future
