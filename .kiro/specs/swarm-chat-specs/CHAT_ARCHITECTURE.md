<!-- STALE REFERENCES: This spec references code that has since been refactored or removed:
- ContextPreviewPanel → REMOVED (was planned for future project detail view, never rendered in production)
- useTabState / tabStateRef / saveTabState → SUPERSEDED by useUnifiedTabState hook
- saveCurrentTab → REMOVED (was a no-op in useUnifiedTabState)
This spec is preserved as a historical record of the design decisions made at the time. -->

# Chat Page Architecture

This document describes the architecture and file structure of the SwarmAI Chat feature after the refactoring from a monolithic 2341-line component to a modular, maintainable structure.

## Overview

The Chat feature is the primary user interface for interacting with SwarmAI agents. It provides:
- Real-time streaming chat with AI agents
- Browser-like session tabs for managing multiple conversations
- File attachments (images, PDFs, text files)
- Workspace selection and context injection
- Chat history management
- Slash commands for plugins
- Permission request handling (HITL - Human-in-the-Loop)
- ToDo Radar sidebar (mock) for task tracking

## File Structure

```
desktop/src/
├── pages/
│   ├── ChatPage.tsx              # Main orchestrator component (~900 lines)
│   └── chat/
│       ├── index.ts              # Barrel export
│       ├── constants.ts          # Constants and message generators
│       ├── constants.test.ts     # Tests for constants
│       ├── types.ts              # Type definitions (OpenTab, PendingQuestion)
│       ├── utils.ts              # Utility functions
│       ├── utils.test.ts         # Tests for utils
│       └── components/
│           ├── index.ts          # Component barrel export
│           ├── ChatHeader.tsx    # Header with session tabs and action buttons
│           ├── ChatInput.tsx     # Input area with attachments and file chips
│           ├── ChatInput.test.tsx        # Tests for ChatInput
│           ├── ChatHistorySidebar.tsx    # Chat history sidebar (right)
│           ├── ContentBlockRenderer.tsx  # Content block rendering
│           ├── FileBrowserSidebar.tsx    # File browser sidebar (right)
│           ├── MessageBubble.tsx         # Message display
│           ├── SessionTab.tsx            # Individual session tab component
│           ├── SessionTab.test.ts        # Tests for SessionTab
│           ├── SessionTabBar.tsx         # Horizontal scrollable tab bar
│           ├── SessionTabBar.test.tsx    # Tests for SessionTabBar
│           ├── TodoRadarSidebar.tsx      # ToDo Radar sidebar (right, mock)
│           └── ToolUseBlock.tsx          # Tool use display
│
├── components/
│   └── chat/                     # Shared chat components (reusable)
│       ├── index.ts              # Barrel export
│       ├── AttachedFileChips.tsx         # Compact Slack-style file chips
│       ├── AttachedFileChips.test.tsx    # Tests for AttachedFileChips
│       ├── AttachmentPreviewCard.tsx     # Attachment preview card
│       ├── ChatDropZone.tsx              # Drag-and-drop file zone
│       ├── FileAttachmentButton.tsx      # File attachment button
│       ├── FileAttachmentPreview.tsx     # File attachment preview
│       ├── PermissionRequestModal.tsx    # HITL permission modal
│       └── WorkspaceSelector.tsx         # Workspace selection dropdown
│
└── hooks/
    ├── index.ts                  # Hooks barrel export
    ├── useSidebarState.ts        # Reusable sidebar state management
    ├── useSidebarState.test.ts   # Tests for useSidebarState
    ├── useTabState.ts            # Session tab state with localStorage persistence
    ├── useTabState.test.ts       # Tests for useTabState
    ├── useChatSession.ts         # Chat session state (optional)
    ├── useWorkspaceSelection.ts  # Workspace selection with persistence
    └── useWorkspaceSelection.test.ts  # Tests for useWorkspaceSelection
```

## Component Hierarchy

```
ChatPage (Orchestrator)
├── ChatHeader
│   ├── SessionTabBar (left section)
│   │   └── SessionTab[] (scrollable, keyboard navigable)
│   │       ├── Chat icon
│   │       ├── Truncated title
│   │       └── Close button (X)
│   └── Header Actions (right section)
│       ├── New Session Button (+)
│       ├── ToDo Radar Toggle (checklist)
│       └── Chat History Toggle (history)
├── Main Chat Area
│   ├── Messages List
│   │   └── MessageBubble[]
│   │       └── ContentBlockRenderer[]
│   │           ├── MarkdownRenderer (text)
│   │           ├── ToolUseBlock (tool_use)
│   │           ├── Tool Result (tool_result)
│   │           └── AskUserQuestion (ask_user_question)
│   └── ChatInput
│       ├── AttachedFileChips (compact file display)
│       ├── FileAttachmentButton
│       ├── WorkspaceSelector
│       ├── Slash Command Suggestions
│       └── Send/Stop Button
├── TodoRadarSidebar (conditional, right)
│   ├── Header (title + close)
│   └── Mock ToDo items (Overdue, Pending)
├── ChatHistorySidebar (conditional, right)
│   └── Session list grouped by time
├── FileBrowserSidebar (conditional, right)
│   └── FileBrowser
└── Modals
    ├── FilePreviewModal
    ├── PermissionRequestModal
    └── AgentFormModal
```

## Key Files Description

### constants.ts
Contains all constants and message generators:
- `MS_PER_DAY` - Time constant for date calculations
- `TOOL_INPUT_COLLAPSE_LENGTH` - Threshold for collapsing tool inputs
- Sidebar width constants (default, min, max)
- `OPEN_TABS_STORAGE_KEY` - localStorage key for tab persistence
- `ACTIVE_TAB_STORAGE_KEY` - localStorage key for active tab
- `SLASH_COMMANDS` - Available slash commands configuration
- `TIME_GROUP_LABEL_KEYS` - i18n keys for session grouping
- `createWelcomeMessage()` - Single source of truth for welcome messages
- `createWorkspaceChangeMessage()` - Workspace change notification

### types.ts
Type definitions specific to chat:
- `PendingQuestion` - State for user question interactions
- `OpenTab` - Session tab state (id, sessionId, title, agentId, isNew)

### utils.ts
Utility functions:
- `groupSessionsByTime()` - Groups chat sessions by time periods
- `formatTimestamp()` - Formats timestamps for display

## Shared Chat Components

Located in `desktop/src/components/chat/`, these are reusable components used across the chat feature:

### AttachedFileChips
Displays attached files as compact, Slack-style removable chips above the input area:
- Horizontal scrolling when files overflow
- File type icons based on extension
- Keyboard navigation (Arrow keys, Delete/Backspace to remove)
- Full ARIA accessibility support
- Tooltip showing full file path on hover

### WorkspaceSelector
Dropdown for selecting the active workspace context:
- Persists selection per agent to localStorage
- Auto-selects default workspace
- Triggers session reset on workspace change

### FileAttachmentButton
Button to trigger file attachment dialog:
- Supports images, PDFs, text files
- Integrates with ChatDropZone for drag-and-drop

### PermissionRequestModal
Modal for Human-in-the-Loop (HITL) permission requests:
- Displays tool name and arguments
- Allow/Deny actions
- Blocks chat until decision made

### Custom Hooks

#### useSidebarState
Reusable hook for managing sidebar state with localStorage persistence:
```typescript
const sidebar = useSidebarState({
  storageKey: 'chatSidebarCollapsed',
  widthStorageKey: 'chatSidebarWidth',
  defaultCollapsed: true,
  defaultWidth: 256,
  minWidth: 200,
  maxWidth: 600,
});
// Returns: { collapsed, width, isResizing, setCollapsed, toggle, handleMouseDown }
```

#### useTabState
Manages browser-like session tabs with localStorage persistence:
```typescript
const {
  openTabs,
  activeTabId,
  addTab,
  closeTab,
  selectTab,
  updateTabTitle,
  updateTabSessionId,
  setTabIsNew,
  removeInvalidTabs,
} = useTabState(defaultAgentId);
```

Features:
- Persists open tabs and active tab to localStorage
- Auto-creates "New Session" tab when no tabs exist
- Auto-creates new tab when closing last tab
- Updates tab title on first message
- Filters out tabs referencing deleted sessions

#### useWorkspaceSelection
Manages workspace selection with:
- localStorage persistence per agent
- Auto-selection of default workspace
- Callback on workspace change for session reset

#### useChatSession (Optional)
Encapsulates chat session state management (available but ChatPage manages state directly for more control).

## Data Flow

```
User Input → ChatInput
    ↓
handleSendMessage()
    ↓
buildContentArray() (process attachments)
    ↓
chatService.streamChat() (SSE)
    ↓
createStreamHandler() (process events)
    ↓
setMessages() (update UI)
```

## State Management

The ChatPage manages these primary states:
- `messages` - Array of chat messages
- `sessionId` - Current session identifier
- `selectedAgentId` - Active agent
- `isStreaming` - Streaming status
- `pendingQuestion` - Awaiting user input
- `pendingPermission` - Awaiting permission decision

### Tab State (via useTabState hook)
- `openTabs` - Array of open session tabs
- `activeTabId` - Currently active tab ID
- Tab operations: addTab, closeTab, selectTab, updateTabTitle, etc.

### Sidebar States (via useSidebarState hook)
- `chatSidebar` - Chat history sidebar (right)
- `rightSidebar` - File browser sidebar (right)
- `todoRadarSidebar` - ToDo Radar sidebar (right)

## Streaming Event Types

The chat handles these SSE event types:
- `session_start` - New session created
- `session_cleared` - Session cleared (/clear command)
- `assistant` - Assistant message content
- `ask_user_question` - Requires user input
- `permission_request` - Requires permission decision
- `result` - Final result
- `error` - Error occurred

## Slash Commands

Available commands (defined in constants.ts):
- `/clear` - Clear conversation context
- `/compact` - Compact conversation history
- `/plugin list` - List installed plugins
- `/plugin install {name}@{marketplace}` - Install plugin
- `/plugin uninstall {id}` - Uninstall plugin
- `/plugin marketplace list` - List marketplaces

## Testing

Property-based tests have been implemented for core chat functionality:

### Page-level Tests
- `desktop/src/pages/chat/utils.test.ts` - Tests for `groupSessionsByTime` and `formatTimestamp`
- `desktop/src/pages/chat/constants.test.ts` - Tests for message generators and constants
- `desktop/src/pages/chat/components/ChatInput.test.tsx` - Tests for ChatInput component
- `desktop/src/pages/chat/components/SessionTab.test.ts` - Tests for title truncation logic
- `desktop/src/pages/chat/components/SessionTabBar.test.tsx` - Tests for keyboard navigation and ARIA

### Shared Component Tests
- `desktop/src/components/chat/AttachedFileChips.test.tsx` - Tests for file chips display and interaction

### Hook Tests
- `desktop/src/hooks/useSidebarState.test.ts` - Tests for sidebar state management
- `desktop/src/hooks/useTabState.test.ts` - Tests for tab state management (30 tests)
- `desktop/src/hooks/useWorkspaceSelection.test.ts` - Tests for workspace selection logic

Run tests with: `cd desktop && npm test -- --run`

## Session Tab System

The chat header uses a browser-like tab interface for managing multiple sessions:

### Tab Lifecycle
1. **Creation**: Click "+" button or app starts with no saved tabs
2. **Title Update**: First message updates tab title (truncated to 25 chars)
3. **Session Linking**: Tab gets linked to backend session on first message
4. **Switching**: Click inactive tab to switch sessions
5. **Closing**: Click X to close; last tab auto-creates new "New Session" tab
6. **Persistence**: Tabs persist to localStorage across app restarts

### Keyboard Navigation (WAI-ARIA Tabs Pattern)
- `ArrowLeft/ArrowRight` - Navigate between tabs (wraps around)
- `Home/End` - Jump to first/last tab
- `Enter/Space` - Select focused tab

### Tab State Structure
```typescript
interface OpenTab {
  id: string;           // Unique tab ID
  sessionId?: string;   // Backend session ID (undefined for new tabs)
  title: string;        // Display title (max 25 chars + "...")
  agentId: string;      // Associated agent
  isNew: boolean;       // True until first message sent
}
```

## Future Improvements

1. **Performance**: Consider virtualizing the message list for long conversations
2. **State**: Could extract more state into custom hooks (useChatMessages, useStreamHandler)
3. **Accessibility**: Further enhance screen reader support
4. **Offline**: Add offline message queueing capability
5. **ToDo Radar**: Implement full functionality (currently mock)
