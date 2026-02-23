# Design Document: Three-Column Layout

## Overview

This design document describes the technical implementation of SwarmAI's 3-column IDE-like layout redesign. The new architecture transforms the current single-sidebar layout into a modern three-panel interface consisting of a Left Sidebar for navigation, a Workspace Explorer for file browsing, and a Main Chat Panel for SwarmAgent interaction.

The design prioritizes:
- **Unified Experience**: Single SwarmAgent interface that orchestrates custom agents
- **Context Awareness**: Visual indicators for workspace scope and attached files
- **Flexibility**: Resizable and collapsible panels for different workflows
- **Protection**: Safeguards for the system Swarm Workspace

## Architecture

### High-Level Component Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              App Shell                                   │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                         Top Bar (Draggable)                       │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│  ┌────────┬─────────────────────┬───────────────────────────────────┐   │
│  │        │                     │                                    │   │
│  │  Left  │    Workspace        │         Main Chat Panel            │   │
│  │ Sidebar│     Explorer        │                                    │   │
│  │        │                     │    ┌─────────────────────────┐     │   │
│  │ [Nav]  │  [Scope Dropdown]   │    │   Context Indicators    │     │   │
│  │        │  [Toolbar]          │    └─────────────────────────┘     │   │
│  │        │  [File Tree]        │    ┌─────────────────────────┐     │   │
│  │        │                     │    │                         │     │   │
│  │        │                     │    │    Chat Messages        │     │   │
│  │        │                     │    │                         │     │   │
│  │        │                     │    └─────────────────────────┘     │   │
│  │        │                     │    ┌─────────────────────────┐     │   │
│  │        │                     │    │    Input Area           │     │   │
│  │        │                     │    └─────────────────────────┘     │   │
│  └────────┴─────────────────────┴───────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### State Management Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          React Query Cache                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │   Workspaces │  │    Agents    │  │    Skills    │  │ MCP Servers │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Layout Context Provider                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  - workspaceExplorerCollapsed: boolean                            │   │
│  │  - workspaceExplorerWidth: number                                 │   │
│  │  - selectedWorkspaceScope: 'all' | workspaceId                    │   │
│  │  - attachedFiles: FileAttachment[]                                │   │
│  │  - activeModal: 'skills' | 'mcp' | 'agents' | 'settings' | null   │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Chat Context Provider                           │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  - sessionId: string | null                                       │   │
│  │  - messages: Message[]                                            │   │
│  │  - isStreaming: boolean                                           │   │
│  │  - chatContext: { files: string[], workspaceScope: string }       │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

## Components and Interfaces

### Component Hierarchy

```
App
├── ThemeProvider
├── QueryClientProvider
├── LayoutProvider
│   └── ThreeColumnLayout
│       ├── TopBar
│       ├── LeftSidebar
│       │   ├── SwarmAILogo
│       │   ├── NavIconButton (Workspaces)
│       │   ├── NavIconButton (SwarmCore)
│       │   ├── NavIconButton (Agents)
│       │   ├── NavIconButton (Skills)
│       │   ├── NavIconButton (MCP Servers)
│       │   ├── NavIconButton (Settings)
│       │   └── GitHubLink
│       ├── WorkspaceExplorer
│       │   ├── ScopeDropdown
│       │   ├── ExplorerToolbar
│       │   │   ├── NewFileButton
│       │   │   ├── NewFolderButton
│       │   │   └── UploadButton
│       │   ├── FileTree
│       │   │   └── FileTreeNode (recursive)
│       │   └── ResizeHandle
│       └── MainChatPanel (ChatPage)
│           ├── ChatHeader
│           │   ├── SessionTabBar
│           │   └── Header Actions (New Session, ToDo Radar, Chat History)
│           ├── ChatMessages
│           ├── ChatInput
│           └── Right Sidebars (order: TodoRadar → ChatHistory → FileBrowser)
│               ├── TodoRadarSidebar (conditional)
│               ├── ChatHistorySidebar (conditional)
│               └── FileBrowserSidebar (conditional)
├── ModalOverlays
│   ├── WorkspacesModal
│   ├── SwarmCoreModal
│   ├── SkillsModal
│   ├── MCPServersModal
│   ├── AgentsModal
│   ├── SettingsModal
│   └── FileEditorModal
└── ConfirmDialogs
    └── SwarmWorkspaceWarningDialog
```

### Key Component Interfaces

```typescript
// Layout Context
interface LayoutContextValue {
  // Workspace Explorer state
  workspaceExplorerCollapsed: boolean;
  setWorkspaceExplorerCollapsed: (collapsed: boolean) => void;
  workspaceExplorerWidth: number;
  setWorkspaceExplorerWidth: (width: number) => void;
  
  // Workspace scope
  selectedWorkspaceScope: 'all' | string; // 'all' or workspace ID
  setSelectedWorkspaceScope: (scope: 'all' | string) => void;
  
  // Modal management
  activeModal: ModalType | null;
  openModal: (modal: ModalType) => void;
  closeModal: () => void;
}

type ModalType = 'skills' | 'mcp' | 'agents' | 'settings' | 'file-editor';

// Workspace Explorer Props
interface WorkspaceExplorerProps {
  collapsed: boolean;
  width: number;
  onCollapsedChange: (collapsed: boolean) => void;
  onWidthChange: (width: number) => void;
  onFileSelect: (file: WorkspaceFile) => void;
  onFileAttach: (file: WorkspaceFile) => void;
}

// File Tree Node
interface FileTreeNodeProps {
  node: FileTreeItem;
  depth: number;
  isExpanded: boolean;
  onToggle: () => void;
  onSelect: () => void;
  onContextMenu: (event: React.MouseEvent) => void;
  onDragStart: (event: React.DragEvent) => void;
}

interface FileTreeItem {
  id: string;
  name: string;
  type: 'file' | 'directory';
  path: string;
  workspaceId: string;
  workspaceName: string;
  children?: FileTreeItem[];
  isSwarmWorkspace?: boolean;
}

// Chat Context Bar
interface ChatContextBarProps {
  workspaceScope: 'all' | string;
  workspaceName: string;
  attachedFiles: AttachedFile[];
  onRemoveFile: (fileId: string) => void;
  onClearAll: () => void;
}

interface AttachedFile {
  id: string;
  name: string;
  path: string;
  workspaceId: string;
}

// File Editor Modal
interface FileEditorModalProps {
  isOpen: boolean;
  filePath: string;
  fileName: string;
  workspaceId: string;
  onSave: (content: string) => Promise<void>;
  onClose: () => void;
}

// Swarm Workspace Warning Dialog
interface SwarmWorkspaceWarningProps {
  isOpen: boolean;
  action: 'edit' | 'delete';
  fileName?: string;
  onConfirm: () => void;
  onCancel: () => void;
}
```

### UI Wireframes

#### Main Layout (Desktop - Full Width)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ○ ○ ○                        SwarmAI                                         │
├────┬─────────────────────────┬───────────────────────────────────────────────┤
│    │ ▼ All Workspaces        │ 📁 All Workspaces │ 📎 main.py, utils.ts      │
│ 📁 │ ─────────────────────── │───────────────────────────────────────────────│
│    │ [+📄] [+📁] [⬆️ Upload]  │                                               │
│ 🔲 │ ─────────────────────── │  🤖 SwarmAI                                   │
│    │ 📁 Swarm Workspace 🔒   │  Hello! I'm SwarmAI — Your AI Team, 24/7!!!   │
│ 🤖 │   └── 📄 config.json    │                                               │
│    │ 📁 my-project           │  ─────────────────────────────────────────    │
│ ✨ │   ├── 📁 src            │                                               │
│    │   │   ├── 📄 main.py    │  👤 You                                       │
│ 🔌 │   │   └── 📄 utils.ts   │  Can you help me refactor the main.py file?  │
│    │   └── 📄 README.md      │                                               │
│ ⚙️ │ 📁 another-project      │  ─────────────────────────────────────────    │
│    │   └── 📄 index.js       │                                               │
│ 🔗 │                         │  🤖 SwarmAI                                   │
│    │                         │  I'll analyze main.py and suggest...          │
│    │                         │                                               │
│    │                         │                                               │
│    │                         │ ┌───────────────────────────────────────────┐ │
│    │                         │ │ Type a message...                    [📎] │ │
│    │                         │ └───────────────────────────────────────────┘ │
└────┴─────────────────────────┴───────────────────────────────────────────────┘
 56px        280px (resizable)              Remaining space (flex-1)

Legend: 📁=Workspaces, 🔲=SwarmCore, 🤖=Agents, ✨=Skills, 🔌=MCP, ⚙️=Settings, 🔗=GitHub
```

#### Workspace Explorer Collapsed

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ○ ○ ○                        SwarmAI                                         │
├────┬──┬──────────────────────────────────────────────────────────────────────┤
│    │  │ 📁 All Workspaces │ 📎 main.py, utils.ts                             │
│ 📁 │◀ │──────────────────────────────────────────────────────────────────────│
│    │  │                                                                      │
│ 🔲 │  │  🤖 SwarmAI                                                          │
│    │  │  Hello! I'm SwarmAI — Your AI Team, 24/7!!!                          │
│ 🤖 │  │                                                                      │
│    │  │  ─────────────────────────────────────────────────────────────────   │
│ ✨ │  │                                                                      │
│    │  │  👤 You                                                              │
│ 🔌 │  │  Can you help me refactor the main.py file?                          │
│    │  │                                                                      │
│ ⚙️ │  │  ─────────────────────────────────────────────────────────────────   │
│ 🔗 │  │                                                                      │
│    │  │  🤖 SwarmAI                                                          │
│    │  │  I'll analyze main.py and suggest improvements...                    │
│    │  │                                                                      │
│    │  │ ┌──────────────────────────────────────────────────────────────────┐ │
│    │  │ │ Type a message...                                           [📎] │ │
│    │  │ └──────────────────────────────────────────────────────────────────┘ │
└────┴──┴──────────────────────────────────────────────────────────────────────┘
 56px 24px                        Remaining space (flex-1)
```

#### File Editor Modal

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│   ┌────────────────────────────────────────────────────────────────────┐     │
│   │ 📄 my-project/src/main.py                              [✕]        │     │
│   ├────────────────────────────────────────────────────────────────────┤     │
│   │  1 │ import os                                                     │     │
│   │  2 │ import sys                                                    │     │
│   │  3 │                                                               │     │
│   │  4 │ def main():                                                   │     │
│   │  5 │     """Main entry point."""                                   │     │
│   │  6 │     print("Hello, World!")                                    │     │
│   │  7 │                                                               │     │
│   │  8 │ if __name__ == "__main__":                                    │     │
│   │  9 │     main()                                                    │     │
│   │    │                                                               │     │
│   ├────────────────────────────────────────────────────────────────────┤     │
│   │                                    [Cancel]  [Save]                │     │
│   └────────────────────────────────────────────────────────────────────┘     │
│                                                                              │
│   (Chat conversation visible but dimmed in background)                       │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

#### Swarm Workspace Warning Dialog

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│         ┌─────────────────────────────────────────────────────┐              │
│         │  ⚠️  System Workspace Warning                        │              │
│         ├─────────────────────────────────────────────────────┤              │
│         │                                                     │              │
│         │  You are about to edit a file in the Swarm          │              │
│         │  Workspace. This is a protected system workspace    │              │
│         │  used by SwarmAI for internal operations.           │              │
│         │                                                     │              │
│         │  Modifying these files may affect SwarmAI's         │              │
│         │  functionality.                                     │              │
│         │                                                     │              │
│         │  Are you sure you want to continue?                 │              │
│         │                                                     │              │
│         ├─────────────────────────────────────────────────────┤              │
│         │                      [Cancel]  [Continue Anyway]    │              │
│         └─────────────────────────────────────────────────────┘              │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

#### Mobile/Narrow Layout (< 768px)

```
┌────────────────────────────────────┐
│ ○ ○ ○        SwarmAI               │
├────┬───────────────────────────────┤
│    │ 📁 All Workspaces │ 📎 2 files│
│ 📁 │───────────────────────────────│
│    │                               │
│ 🔲 │  🤖 SwarmAI                   │
│    │  Hello! I'm SwarmAI...        │
│ 🤖 │                               │
│    │  ───────────────────────────  │
│ ✨ │                               │
│    │  👤 You                       │
│ 🔌 │  Help me with main.py         │
│    │                               │
│ ⚙️ │  ───────────────────────────  │
│ 🔗 │                               │
│    │ ┌───────────────────────────┐ │
│    │ │ Type a message...    [📎] │ │
│    │ └───────────────────────────┘ │
└────┴───────────────────────────────┘
 56px    Remaining (Workspace Explorer
         auto-collapsed, toggle via ◀)
```

## Data Models

### New TypeScript Types

```typescript
// Workspace scope for filtering
type WorkspaceScope = 'all' | string; // 'all' or specific workspace ID

// Extended workspace with UI metadata
interface WorkspaceWithMeta extends SwarmWorkspace {
  isSwarmWorkspace: boolean;
  fileCount?: number;
}

// File tree structure for explorer
interface FileTreeState {
  items: FileTreeItem[];
  expandedPaths: Set<string>;
  selectedPath: string | null;
}

// Chat context with attached files
interface ChatContextState {
  workspaceScope: WorkspaceScope;
  attachedFiles: AttachedFile[];
}

// Layout persistence state
interface LayoutPersistence {
  workspaceExplorerCollapsed: boolean;
  workspaceExplorerWidth: number;
  lastWorkspaceScope: WorkspaceScope;
}

// File editor state
interface FileEditorState {
  isOpen: boolean;
  filePath: string | null;
  fileName: string | null;
  workspaceId: string | null;
  content: string;
  originalContent: string;
  isDirty: boolean;
  language: string;
}

// Swarm Workspace status for system initialization display
interface SwarmWorkspaceStatus {
  ready: boolean;
  name: string;
  path: string;
}

// Extended System Status (adds to existing swarm-init-status-display)
interface SystemStatus {
  database: DatabaseStatus;
  agent: AgentStatus;
  channelGateway: ChannelGatewayStatus;
  swarmWorkspace: SwarmWorkspaceStatus;  // NEW
  initialized: boolean;
  timestamp: string;
}
```

### Backend Schema Updates

The existing `SystemStatusResponse` model in `backend/routers/system.py` needs to be extended:

```python
class SwarmWorkspaceStatus(BaseModel):
    ready: bool
    name: str = ""
    path: str = ""

class SystemStatusResponse(BaseModel):
    database: DatabaseStatus
    agent: AgentStatus
    channel_gateway: ChannelGatewayStatus
    swarm_workspace: SwarmWorkspaceStatus  # NEW
    initialized: bool
    timestamp: str
```

### LocalStorage Keys

```typescript
const STORAGE_KEYS = {
  WORKSPACE_EXPLORER_COLLAPSED: 'workspaceExplorerCollapsed',
  WORKSPACE_EXPLORER_WIDTH: 'workspaceExplorerWidth',
  LAST_WORKSPACE_SCOPE: 'lastWorkspaceScope',
} as const;
```



## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Layout Structure Maintained on Resize

*For any* window resize event within valid bounds (minimum 320px width), the three-column layout structure SHALL be maintained with Left_Sidebar, Workspace_Explorer (if not collapsed), and Main_Chat_Panel all present and correctly ordered.

**Validates: Requirements 1.5**

### Property 2: Workspace Explorer Collapse Toggle

*For any* toggle action on the Workspace_Explorer collapse button, the collapsed state SHALL invert (collapsed becomes expanded, expanded becomes collapsed) and the Main_Chat_Panel SHALL expand to fill the freed space.

**Validates: Requirements 1.6**

### Property 3: Workspace Explorer Resize Constraints

*For any* drag resize operation on the Workspace_Explorer, the resulting width SHALL be clamped between the minimum (200px) and maximum (500px) constraints.

**Validates: Requirements 1.7, 11.5**

### Property 4: Navigation Modal Opening

*For any* navigation icon click in the Left_Sidebar, the corresponding modal (Skills, MCP Servers, Agents, or Settings) SHALL open as an overlay while preserving the underlying layout.

**Validates: Requirements 2.2**

### Property 5: Active Navigation Indicator

*For any* active modal state, the corresponding navigation icon in the Left_Sidebar SHALL display the active visual indicator (highlighted state).

**Validates: Requirements 2.5**

### Property 6: Workspace Dropdown Population

*For any* set of workspaces in the system, the Workspace_Explorer scope dropdown SHALL contain "All Workspaces" plus all workspace names as selectable options.

**Validates: Requirements 3.3**

### Property 7: Workspace Scope Filtering

*For any* workspace scope selection (other than "All Workspaces"), the file tree SHALL display only files and folders belonging to the selected workspace.

**Validates: Requirements 3.4**

### Property 8: Folder Expand/Collapse Toggle

*For any* folder click in the file tree, the folder's expanded state SHALL toggle (expanded becomes collapsed, collapsed becomes expanded) and child items SHALL be shown or hidden accordingly.

**Validates: Requirements 3.6**

### Property 9: File Creation in Current Directory

*For any* New File button click when a directory is selected, a new file SHALL be created within that directory and appear in the file tree.

**Validates: Requirements 3.8**

### Property 10: Folder Creation in Current Directory

*For any* New Folder button click when a directory is selected, a new folder SHALL be created within that directory and appear in the file tree.

**Validates: Requirements 3.9**

### Property 11: Drag-Drop File Attachment

*For any* file dragged from Workspace_Explorer and dropped on Main_Chat_Panel, that file SHALL be added to the Chat_Context attachments list.

**Validates: Requirements 3.12, 6.2**

### Property 12: Swarm Workspace Invariant

*For any* application state, the Swarm_Workspace SHALL always be present in the workspace list and SHALL NOT be deletable.

**Validates: Requirements 4.1, 4.4, 10.3**

### Property 13: Swarm Workspace Edit Protection

*For any* edit attempt on a file within Swarm_Workspace, a confirmation dialog SHALL be displayed before the edit is allowed to proceed.

**Validates: Requirements 4.3, 4.5**

### Property 14: Workspace Path Validation

*For any* workspace add operation with an invalid or inaccessible path, the operation SHALL fail with an appropriate error message and the workspace SHALL NOT be added.

**Validates: Requirements 5.5**

### Property 15: Workspace Persistence Round-Trip

*For any* workspace configuration (name, path), after adding the workspace and restarting the application, the workspace SHALL be restored with identical configuration.

**Validates: Requirements 5.6**

### Property 16: Chat Context File Indicators

*For any* file attached to Chat_Context, the Main_Chat_Panel SHALL display a visual indicator showing the file name and a remove button.

**Validates: Requirements 6.3, 6.7**

### Property 17: Workspace Scope Change Clears Context

*For any* workspace scope change, the Chat_Context SHALL be cleared (all attached files removed) and a new conversation session SHALL begin.

**Validates: Requirements 6.5**

### Property 18: Cross-Workspace File Attachment

*For any* two files from different workspaces, both files SHALL be attachable to the same Chat_Context simultaneously.

**Validates: Requirements 6.6**

### Property 19: File Removal from Context

*For any* remove button click on an attached file, that file SHALL be removed from Chat_Context and the indicator SHALL disappear.

**Validates: Requirements 6.8**

### Property 20: SwarmAgent Always Active

*For any* application state during normal operation, SwarmAgent SHALL be displayed as the active agent in Main_Chat_Panel.

**Validates: Requirements 7.1**

### Property 21: Agent CRUD Round-Trip

*For any* Custom_Agent with valid configuration (name, description, model, skills, MCP servers), creating, updating, and then reading the agent SHALL return the updated configuration, and deleting SHALL remove it from the list.

**Validates: Requirements 8.3, 8.4, 8.5, 8.6**

### Property 22: Agent List Display

*For any* set of Custom_Agents in the database, the Agents management page SHALL display all agents in the list.

**Validates: Requirements 8.2**

### Property 23: File Editor Opens on Double-Click

*For any* file double-click in Workspace_Explorer, the File_Editor_Modal SHALL open with that file's content loaded.

**Validates: Requirements 9.1**

### Property 24: File Editor Save Persistence

*For any* file edit in File_Editor_Modal followed by Save, the file content on disk SHALL match the edited content and the modal SHALL close.

**Validates: Requirements 9.6**

### Property 25: File Editor Cancel Discards Changes

*For any* file edit in File_Editor_Modal followed by Cancel, the file content on disk SHALL remain unchanged and the modal SHALL close.

**Validates: Requirements 9.7**

### Property 26: Unsaved Changes Warning

*For any* attempt to close File_Editor_Modal with unsaved changes (dirty state), a confirmation dialog SHALL be displayed before closing.

**Validates: Requirements 9.8**

### Property 27: Collapse State Persistence

*For any* Workspace_Explorer collapsed state change, after application restart, the collapsed state SHALL be restored to the last saved value.

**Validates: Requirements 11.3**

### Property 28: Width Persistence

*For any* Workspace_Explorer width change via resize, after application restart, the width SHALL be restored to the last saved value.

**Validates: Requirements 11.4**

### Property 29: Collapse Toggle Button Visibility

*For any* collapsed state of Workspace_Explorer, a toggle button SHALL be visible to allow expanding the explorer.

**Validates: Requirements 11.2**

### Property 30: Swarm Workspace Status Response Schema

*For any* call to the `/api/system/status` endpoint, the response SHALL contain a valid `swarm_workspace` object with `ready` boolean, `name` string, and `path` string fields.

**Validates: Requirements 12.1, 12.2**

### Property 31: Swarm Workspace Status Case Conversion

*For any* valid API response from `/api/system/status`, the frontend service's `getStatus()` function SHALL return an object where the `swarm_workspace` key is converted to camelCase (`swarmWorkspace`).

**Validates: Requirements 12.3**

### Property 32: Swarm Workspace Initialization Display

*For any* system status where `swarmWorkspace.ready` is `true`, the Backend_Startup_Overlay SHALL display a green checkmark next to "Swarm Workspace initialized" and show the workspace path as a nested item.

**Validates: Requirements 12.4, 12.5**

## Error Handling

### File System Errors

| Error Scenario | Handling Strategy |
|----------------|-------------------|
| Workspace path inaccessible | Display error toast, prevent workspace addition |
| File read permission denied | Display error in file tree node, disable file operations |
| File write permission denied | Display error toast on save attempt, keep modal open |
| Disk full on file creation | Display error toast with disk space warning |
| File deleted externally | Refresh file tree, show notification if file was open |

### Network/Backend Errors

| Error Scenario | Handling Strategy |
|----------------|-------------------|
| Backend unavailable | Show connection error overlay, retry with exponential backoff |
| Chat stream interrupted | Display error message in chat, offer retry button |
| Agent API failure | Display error toast, maintain current UI state |

### State Consistency Errors

| Error Scenario | Handling Strategy |
|----------------|-------------------|
| LocalStorage corrupted | Reset to defaults, log warning |
| Invalid workspace scope | Reset to "All Workspaces" |
| Missing Swarm Workspace | Auto-create on startup |

### User Input Validation

| Input | Validation | Error Message |
|-------|------------|---------------|
| Workspace name | Non-empty, no special chars | "Workspace name cannot be empty or contain special characters" |
| Workspace path | Valid path, accessible | "The selected path is not accessible" |
| File name | Valid filename chars | "Invalid characters in file name" |
| Folder name | Valid folder name chars | "Invalid characters in folder name" |

## Testing Strategy

### Unit Testing

Unit tests will focus on:
- Component rendering with various props
- State management logic (context providers)
- Utility functions (path validation, file type detection)
- LocalStorage persistence helpers

### Property-Based Testing

Property-based tests will use **fast-check** library for TypeScript to validate the correctness properties defined above. Each property test will:
- Run minimum 100 iterations with randomized inputs
- Be tagged with the corresponding property number
- Reference the requirements being validated

**Tag Format**: `Feature: three-column-layout, Property {N}: {property_title}`

Example test structure:
```typescript
import fc from 'fast-check';

describe('Workspace Explorer', () => {
  // Feature: three-column-layout, Property 3: Workspace Explorer Resize Constraints
  it('should enforce width constraints on resize', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 1000 }), (dragWidth) => {
        const result = clampWidth(dragWidth, MIN_WIDTH, MAX_WIDTH);
        return result >= MIN_WIDTH && result <= MAX_WIDTH;
      }),
      { numRuns: 100 }
    );
  });
});
```

### Integration Testing

Integration tests will verify:
- Modal opening/closing flows
- File attachment drag-drop end-to-end
- Workspace scope change and context clearing
- File editor save/cancel workflows

### Visual Regression Testing

Consider using Playwright or Cypress for:
- Layout structure at various viewport sizes
- Collapsed/expanded states
- Modal overlay positioning

### Test File Organization

```
desktop/src/
├── components/
│   ├── layout/
│   │   ├── ThreeColumnLayout.tsx
│   │   ├── ThreeColumnLayout.test.tsx
│   │   ├── ThreeColumnLayout.property.test.tsx
│   │   └── ...
│   ├── workspace-explorer/
│   │   ├── WorkspaceExplorer.tsx
│   │   ├── WorkspaceExplorer.test.tsx
│   │   ├── WorkspaceExplorer.property.test.tsx
│   │   └── ...
│   └── ...
├── contexts/
│   ├── LayoutContext.tsx
│   ├── LayoutContext.test.tsx
│   └── ...
└── ...
```
