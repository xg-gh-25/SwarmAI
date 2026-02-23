# Design Document: Chat History Sidebar Relocation

## Overview

This design document describes the changes required to relocate the Chat History sidebar (`ChatSidebar`) from the left side to the right side of the Chat panel. The goal is to consolidate all sidebars on the right side, creating a cleaner layout where the main chat area occupies the left portion of the screen.

### Current State

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           ChatHeader                                     │
├──────────┬────────────────────────────────┬──────────────┬──────────────┤
│ ChatSidebar │      Main Chat Area         │ FileBrowser  │ TodoRadar    │
│  (LEFT)     │                             │   (RIGHT)    │  (RIGHT)     │
│  border-r   │                             │   border-l   │  border-l    │
│  resize→    │                             │   ←resize    │  ←resize     │
└──────────┴────────────────────────────────┴──────────────┴──────────────┘
```

### Target State

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           ChatHeader                                     │
├────────────────────────────────┬──────────────┬──────────────┬──────────┤
│        Main Chat Area          │  TodoRadar   │ChatHistory   │FileBrowser│
│                                │   (RIGHT)    │  Sidebar     │  (RIGHT) │
│                                │   border-l   │   border-l   │  border-l│
│                                │   ←resize    │   ←resize    │  ←resize │
└────────────────────────────────┴──────────────┴──────────────┴──────────┘
```

## Architecture

The relocation involves minimal architectural changes since the sidebar functionality remains identical. The changes are purely positional and visual:

1. **Layout Change**: Move `ChatHistorySidebar` rendering from before the main chat area to after it (in the right sidebar region)
2. **Visual Change**: Update border and resize handle positioning from right-edge to left-edge
3. **Resize Logic**: Update the `useSidebarState` hook to calculate width correctly for right-side positioning
4. **Rename**: Rename `ChatSidebar` component to `ChatHistorySidebar` for clarity

### Component Hierarchy (Updated)

```
ChatPage
├── ChatHeader (toggle buttons)
├── Main Content Area (flex container)
│   ├── Main Chat Area (flex-1)
│   ├── TodoRadarSidebar (first in right sidebar area)
│   ├── ChatHistorySidebar (NEW POSITION & NAME - second in right sidebar area)
│   └── FileBrowserSidebar (last in right sidebar area)
└── Modals
```

## Components and Interfaces

### 1. ChatPage.tsx Changes

**File**: `desktop/src/pages/ChatPage.tsx`

The main layout change occurs in the render section. The `ChatHistorySidebar` component (renamed from `ChatSidebar`) will be moved from before the main chat area to after it, positioned between TodoRadarSidebar and FileBrowserSidebar.

**Current Order** (lines ~777-920):
```tsx
<div className="flex flex-1 overflow-hidden">
  {/* Chat History Sidebar - LEFT */}
  {!chatSidebar.collapsed && <ChatSidebar ... />}
  
  {/* Main Chat Area */}
  <div className="flex-1 flex flex-col ...">...</div>
  
  {/* Right Sidebars */}
  {!rightSidebar.collapsed && <FileBrowserSidebar ... />}
  {!todoRadarSidebar.collapsed && <TodoRadarSidebar ... />}
</div>
```

**New Order**:
```tsx
<div className="flex flex-1 overflow-hidden">
  {/* Main Chat Area */}
  <div className="flex-1 flex flex-col ...">...</div>
  
  {/* Right Sidebars - Order: TodoRadar, ChatHistory, FileBrowser */}
  {!todoRadarSidebar.collapsed && <TodoRadarSidebar ... />}
  {!chatSidebar.collapsed && <ChatHistorySidebar ... />}
  {!rightSidebar.collapsed && <FileBrowserSidebar ... />}
</div>
```

### 2. ChatSidebar.tsx → ChatHistorySidebar.tsx (Rename + Changes)

**File**: `desktop/src/pages/chat/components/ChatSidebar.tsx` → `ChatHistorySidebar.tsx`

Rename the component file and update the component's visual styling to match right-side positioning:

| Property | Current (Left) | New (Right) |
|----------|---------------|-------------|
| Border | `border-r` | `border-l` |
| Resize Handle Position | `right-0` | `left-0` |
| Resize Handle Hitbox | `-right-1` | `-left-1` |

**Current Implementation**:
```tsx
<div className="... border-r border-[var(--color-border)] ...">
  {/* ... content ... */}
  
  {/* Resize Handle - on right edge */}
  <div className="absolute top-0 right-0 w-1 h-full cursor-ew-resize ...">
    <div className="absolute inset-y-0 -right-1 w-3" />
  </div>
</div>
```

**New Implementation**:
```tsx
<div className="... border-l border-[var(--color-border)] ...">
  {/* ... content ... */}
  
  {/* Resize Handle - on left edge */}
  <div className="absolute top-0 left-0 w-1 h-full cursor-ew-resize ...">
    <div className="absolute inset-y-0 -left-1 w-3" />
  </div>
</div>
```

### 3. useSidebarState.ts Changes

**File**: `desktop/src/hooks/useSidebarState.ts`

The hook currently uses a heuristic based on `storageKey.includes('right')` to determine resize direction. Since `chatSidebarWidth` doesn't contain "right", the resize calculation needs adjustment.

**Current Logic** (line ~52):
```typescript
const handleMouseMove = (e: MouseEvent) => {
  const newWidth = storageKey.includes('right')
    ? window.innerWidth - e.clientX
    : e.clientX;
  // ...
};
```

**Options for Fix**:

**Option A (Recommended)**: Add a `position` config parameter to explicitly specify sidebar position:
```typescript
interface SidebarConfig {
  // ... existing props
  position?: 'left' | 'right'; // New: explicit position
}

// In handleMouseMove:
const newWidth = config.position === 'right'
  ? window.innerWidth - e.clientX
  : e.clientX;
```

**Option B**: Update the storage key to include "right" (e.g., `chatSidebarRightWidth`), but this would break existing user preferences.

**Recommendation**: Use Option A to maintain backward compatibility with existing localStorage keys.

### Interface Changes

**useSidebarState Config Interface**:
```typescript
interface SidebarConfig {
  storageKey: string;
  widthStorageKey: string;
  defaultCollapsed: boolean;
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
  position?: 'left' | 'right'; // NEW: defaults to 'left' for backward compatibility
}
```

**ChatPage Usage Update**:
```typescript
const chatSidebar = useSidebarState({
  storageKey: 'chatSidebarCollapsed',
  widthStorageKey: 'chatSidebarWidth',
  defaultCollapsed: true,
  defaultWidth: DEFAULT_SIDEBAR_WIDTH,
  minWidth: MIN_SIDEBAR_WIDTH,
  maxWidth: MAX_SIDEBAR_WIDTH,
  position: 'right', // NEW: specify right-side positioning
});
```

### 4. Component Barrel Export Update

**File**: `desktop/src/pages/chat/components/index.ts`

Update the barrel export to use the new component name:

**Current**:
```typescript
export { ChatSidebar } from './ChatSidebar';
```

**New**:
```typescript
export { ChatHistorySidebar } from './ChatHistorySidebar';
```

### 5. ChatPage Import Update

**File**: `desktop/src/pages/ChatPage.tsx`

Update the import statement:

**Current**:
```typescript
import { ChatHeader, ChatInput, ChatSidebar, FileBrowserSidebar, MessageBubble, TodoRadarSidebar } from './chat/components';
```

**New**:
```typescript
import { ChatHeader, ChatInput, ChatHistorySidebar, FileBrowserSidebar, MessageBubble, TodoRadarSidebar } from './chat/components';
```

## Data Models

No data model changes required. The feature uses existing localStorage keys:
- `chatSidebarCollapsed`: boolean string ('true'/'false')
- `chatSidebarWidth`: number string (pixel width)

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system—essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Right-Side Resize Direction

*For any* mouse drag event on the ChatHistorySidebar resize handle when positioned on the right side, moving the mouse left (decreasing clientX) should increase the sidebar width, and moving the mouse right (increasing clientX) should decrease the sidebar width.

**Validates: Requirements 2.3**

### Property 2: Sidebar State Persistence Round-Trip

*For any* valid sidebar state (collapsed boolean and width within min/max bounds), saving the state to localStorage and then loading a new component instance should restore the exact same collapsed state and width value.

**Validates: Requirements 3.2, 3.3**

## Error Handling

### Resize Boundary Handling
- If resize drag attempts to set width below `MIN_SIDEBAR_WIDTH`, clamp to minimum
- If resize drag attempts to set width above `MAX_SIDEBAR_WIDTH`, clamp to maximum
- If localStorage contains invalid width value, fall back to `DEFAULT_SIDEBAR_WIDTH`

### State Persistence Errors
- If localStorage is unavailable (private browsing), sidebar still functions with in-memory state
- If localStorage contains corrupted data, use default values

## Testing Strategy

### Unit Tests (Examples and Edge Cases)

The following should be verified with specific example tests:

1. **Layout Structure Tests** (Requirements 1.1, 1.2, 1.3)
   - Verify ChatHistorySidebar renders after main chat area in DOM
   - Verify sidebar order is: TodoRadarSidebar → ChatHistorySidebar → FileBrowserSidebar
   - Verify no sidebar elements before main chat area

2. **Visual Styling Tests** (Requirements 2.1, 2.2)
   - Verify ChatHistorySidebar has `border-l` class (not `border-r`)
   - Verify resize handle has `left-0` class (not `right-0`)

3. **Functionality Preservation Tests** (Requirements 4.1-4.5)
   - Verify session groups render correctly
   - Verify session click loads messages
   - Verify New Chat button creates session
   - Verify delete button shows confirmation
   - Verify close button collapses sidebar

4. **Toggle Button Tests** (Requirements 5.1, 5.2)
   - Verify toggle expands/collapses sidebar
   - Verify button visual state matches sidebar state

### Property-Based Tests

Using a property-based testing library (e.g., fast-check for TypeScript):

1. **Property 1: Resize Direction**
   - Generate random mouse positions
   - Verify width changes in correct direction for right-side positioning
   - Minimum 100 iterations
   - Tag: **Feature: chat-history-sidebar-relocation, Property 1: Right-Side Resize Direction**

2. **Property 2: State Persistence Round-Trip**
   - Generate random valid states (collapsed: boolean, width: number in valid range)
   - Save to localStorage, create new hook instance, verify state matches
   - Minimum 100 iterations
   - Tag: **Feature: chat-history-sidebar-relocation, Property 2: Sidebar State Persistence Round-Trip**

### Integration Tests

- Verify sidebar works correctly with other right-side sidebars (FileBrowser, TodoRadar)
- Verify multiple sidebars can be open simultaneously without layout issues
- Verify resize handles don't interfere with each other

