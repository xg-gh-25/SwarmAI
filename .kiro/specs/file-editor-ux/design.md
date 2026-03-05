# Design Document: File Editor UX Enhancements

## Overview

This design covers six UX improvements to the existing `FileEditorModal` component in SwarmAI's desktop app. The modal currently provides basic file editing with syntax highlighting (via highlight.js), a textarea+pre overlay pattern, save/cancel flow, and unsaved-changes confirmation. The enhancements add:

1. File-type icons and git status badges in the title bar (reusing `fileIcon()`, `fileIconColor()`, `gitStatusBadge()` from TreeNodeRow)
2. An "Attach to Chat" button wired to `LayoutContext.attachFile()`
3. Breadcrumb path display (visual only, no navigation)
4. Inline diff view (line-based, read-only when active)
5. Line numbers in a synchronized gutter
6. In-file search triggered by Cmd/Ctrl+F

All changes stay within the existing modal architecture — no Monaco, no full-page takeover, no new heavy dependencies. Styling uses CSS variables for light/dark theme compatibility.

## Architecture

### Current Component Structure

```
ThreeColumnLayout
├── ExplorerProvider (wraps explorer + chat)
│   ├── WorkspaceExplorer
│   └── MainChatPanel
├── FileEditorModal          ← rendered OUTSIDE ExplorerProvider
├── SwarmWorkspaceWarningDialog
└── Management Modals (Workspaces, Skills, MCP, etc.)
```

The `FileEditorModal` receives its state from `ThreeColumnLayout` via `fileEditorState` (a local `useState`). It is rendered outside `ExplorerProvider`, so it cannot use `useExplorer()` directly. The parent (`ThreeColumnLayout`) already has access to `useLayout()` for `attachFile` and `attachedFiles`.

### Enhanced Architecture

The modal's internal structure changes from a simple textarea+pre overlay to a layered editor with optional sub-components:

```
FileEditorModal
├── Header
│   ├── FileIcon + GitStatusBadge (Req 1)
│   ├── BreadcrumbBar (Req 3)
│   ├── AttachToChatButton (Req 2)
│   └── CloseButton
├── SearchBar (Req 6, conditionally rendered)
├── EditorArea
│   ├── LineGutter (Req 5)
│   ├── SyntaxHighlightPre (existing)
│   └── Textarea (existing)
│   OR
│   └── DiffView (Req 4, replaces editor when active)
└── Footer
    ├── LanguageBadge (existing)
    ├── ShowChangesToggle (Req 4)
    ├── Cancel button (existing)
    └── Save button (existing)
```

### Data Flow for New Props

```
ThreeColumnLayout
  │
  ├─ fileEditorState (existing: isOpen, filePath, fileName, workspaceId, content)
  │   + gitStatus (new: from TreeNode.gitStatus when file is opened)
  │
  ├─ useLayout() → attachFile, attachedFiles
  │
  └─ FileEditorModal
       ├─ gitStatus prop → renders badge
       ├─ onAttachToChat prop → calls attachFile via parent
       └─ isAttached prop → disables button when file already attached
```

The parent (`ThreeColumnLayout`) already stores the file info when opening the editor. We extend `fileEditorState` to also capture `gitStatus` from the `TreeNode` at open time, and pass `onAttachToChat` / `isAttached` by deriving them from `useLayout()`.


## Components and Interfaces

### 1. Extracted Utility Module: `fileUtils.ts`

The `fileIcon()`, `fileIconColor()`, and `gitStatusBadge()` functions currently live inside `TreeNodeRow.tsx` as module-scoped functions. To reuse them in `FileEditorModal` without creating a dependency on the explorer component, extract them into a shared utility:

```
desktop/src/utils/fileUtils.ts
  ├── fileIcon(name: string): string
  ├── fileIconColor(name: string): string
  ├── gitStatusBadge(status?: GitStatus): { label, color, bg } | null
  └── gitStatusColor(status?: GitStatus): string | undefined
```

`TreeNodeRow.tsx` re-imports from `fileUtils.ts` to avoid duplication. This is a pure refactor with no behavior change.

### 2. BreadcrumbBar Component

A small, self-contained presentational component:

```typescript
interface BreadcrumbBarProps {
  filePath: string;
}
```

- Splits `filePath` on `/`, renders segments separated by `›` chevrons
- Last segment styled with primary/bold, parent segments in muted color
- Truncates from the left with `…` when overflowing, preserving the filename
- Uses `overflow: hidden`, `text-overflow: ellipsis`, `direction: rtl` trick for left-truncation, with an inner `direction: ltr` span to keep text readable

Location: inline within `FileEditorModal.tsx` (small enough to not warrant a separate file) or as a local component at the top of the file.

### 3. DiffView Component

Renders the inline diff output:

```typescript
interface DiffLine {
  type: 'added' | 'removed' | 'unchanged';
  content: string;
  oldLineNumber?: number;
  newLineNumber?: number;
}

interface DiffViewProps {
  lines: DiffLine[];
}
```

- Renders a `<pre>` block with line-by-line coloring
- Added lines: green background at low opacity (`var(--color-git-added)` with ~15% opacity)
- Removed lines: red background at low opacity (`var(--color-git-deleted)` with ~15% opacity)
- Unchanged lines: no background
- Each line has a gutter showing old/new line numbers
- Read-only (no textarea)

Location: inline within `FileEditorModal.tsx` or a local sub-component.

### 4. Line-Based Diff Algorithm: `lineDiff.ts`

A pure utility function:

```typescript
function computeLineDiff(oldText: string, newText: string): DiffLine[]
```

- Splits both texts by `\n`
- Uses a simple LCS (Longest Common Subsequence) algorithm on the line arrays
- Returns an array of `DiffLine` objects
- No external dependency — the line-level granularity keeps the algorithm simple and fast
- For files up to ~10K lines this is more than adequate

Location: `desktop/src/utils/lineDiff.ts`

Decision rationale: We chose a custom LCS over `diff-match-patch` because (a) we only need line-level granularity, (b) it avoids a ~50KB dependency, and (c) the implementation is ~40 lines of code for the core algorithm.

### 5. SearchBar Sub-Component

```typescript
interface SearchBarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  currentMatch: number;
  totalMatches: number;
  onNext: () => void;
  onPrevious: () => void;
  onClose: () => void;
}
```

- Floating bar positioned at the top of the editor area (absolute positioning within the editor container)
- Input field with match counter ("3 of 12"), up/down navigation buttons, close button
- Escape closes the bar
- Does not intercept Cmd+S / Ctrl+S

### 6. LineGutter Sub-Component

```typescript
interface LineGutterProps {
  lineCount: number;
  scrollTop: number;
  activeLineNumber?: number;
}
```

- Renders sequential line numbers 1..N
- Syncs vertical scroll with the textarea via `scrollTop` prop
- Fixed width based on digit count of max line number (e.g., `ch` units: `Math.max(3, String(lineCount).length) + 1`)
- Active line highlighted with slightly brighter color
- Right border separator

### 7. Updated FileEditorModalProps

```typescript
interface FileEditorModalProps {
  // Existing
  isOpen: boolean;
  filePath: string;
  fileName: string;
  workspaceId: string;
  initialContent?: string;
  onSave: (content: string) => Promise<void>;
  onClose: () => void;
  // New
  gitStatus?: GitStatus;
  onAttachToChat?: (item: FileTreeItem) => void;
  isAttached?: boolean;
}
```

### 8. Updated fileEditorState in ThreeColumnLayout

```typescript
const [fileEditorState, setFileEditorState] = useState<{
  isOpen: boolean;
  filePath: string;
  fileName: string;
  workspaceId: string;
  content: string;
  gitStatus?: GitStatus;  // NEW: captured from TreeNode at open time
} | null>(null);
```


## Data Models

### Internal State (FileEditorModal)

The component's internal state expands to manage the new features:

```typescript
// Existing state
const [content, setContent] = useState(initialContent);
const [originalContent, setOriginalContent] = useState(initialContent);
const [isSaving, setIsSaving] = useState(false);
const [showUnsavedWarning, setShowUnsavedWarning] = useState(false);

// New state
const [showDiff, setShowDiff] = useState(false);
const [showSearch, setShowSearch] = useState(false);
const [searchQuery, setSearchQuery] = useState('');
const [currentMatchIndex, setCurrentMatchIndex] = useState(0);
const [activeLineNumber, setActiveLineNumber] = useState<number | undefined>(undefined);
const [attachFeedback, setAttachFeedback] = useState(false); // true for 2s after attach
```

### DiffLine Model

```typescript
interface DiffLine {
  type: 'added' | 'removed' | 'unchanged';
  content: string;
  oldLineNumber?: number;  // line number in original (undefined for added lines)
  newLineNumber?: number;  // line number in current (undefined for removed lines)
}
```

The diff is computed on-demand via `useMemo` when `showDiff` is true:

```typescript
const diffLines = useMemo(() => {
  if (!showDiff) return [];
  return computeLineDiff(originalContent, content);
}, [showDiff, originalContent, content]);
```

### Search Match Model

Search matches are computed reactively:

```typescript
interface SearchMatch {
  lineIndex: number;    // 0-based line index
  startOffset: number;  // character offset within the line
  length: number;       // length of the match
}
```

Matches are computed via `useMemo` on `searchQuery` + `content`:

```typescript
const searchMatches = useMemo(() => {
  if (!searchQuery) return [];
  return findAllMatches(content, searchQuery); // case-insensitive
}, [content, searchQuery]);
```

### No Backend Changes

All new features are purely frontend. No API changes, no new endpoints, no database schema changes. The `gitStatus` is already available on `TreeNode` objects from the existing workspace file tree API.


## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: File icon and color determinism

*For any* filename string, `fileIcon(name)` and `fileIconColor(name)` shall always return the same icon name and CSS variable respectively, and the icon name shall be a non-empty string from the known Material Symbols set.

**Validates: Requirements 1.1, 1.2**

### Property 2: Git status badge completeness

*For any* valid `GitStatus` value (added, modified, deleted, renamed, untracked, conflicting, ignored), `gitStatusBadge(status)` shall return a non-null object with non-empty `label`, `color`, and `bg` strings. For `undefined` input, it shall return `null`.

**Validates: Requirements 1.3, 1.4**

### Property 3: Attach produces valid FileTreeItem

*For any* combination of filePath, fileName, and workspaceId strings, the FileTreeItem constructed by the attach handler shall have `id` equal to the filePath, `name` equal to the fileName, `type` equal to `'file'`, `path` equal to the filePath, and `workspaceId` equal to the workspaceId.

**Validates: Requirements 2.2**

### Property 4: Breadcrumb path splitting

*For any* file path string containing at least one `/` separator, splitting the path into breadcrumb segments and joining them with ` › ` shall produce a string where the last segment equals the original filename (basename), and the total number of segments equals the number of `/`-separated parts in the original path.

**Validates: Requirements 3.1**

### Property 5: Diff algorithm round-trip

*For any* two strings (originalContent and currentContent), computing `computeLineDiff(original, current)` and then applying the resulting diff (keeping 'unchanged' and 'added' lines, dropping 'removed' lines) shall reconstruct the currentContent exactly. Conversely, keeping 'unchanged' and 'removed' lines shall reconstruct the originalContent.

**Validates: Requirements 4.3, 4.8**

### Property 6: Diff mode state machine

*For any* editor state where content differs from originalContent, toggling diff mode on shall set `showDiff=true` (read-only, save disabled), and toggling it off shall restore `showDiff=false` (editable, save enabled if dirty). The content shall remain unchanged through the toggle cycle.

**Validates: Requirements 4.5, 4.6, 8.5**

### Property 7: Line count consistency

*For any* content string, the number of line numbers displayed in the gutter shall equal `content.split('\n').length`, and line numbers shall be sequential starting from 1.

**Validates: Requirements 5.1**

### Property 8: Cursor position to line number mapping

*For any* content string and any valid cursor position (0 ≤ pos ≤ content.length), the active line number shall equal the number of newline characters before the cursor position, plus 1.

**Validates: Requirements 5.5**

### Property 9: Search match completeness and case-insensitivity

*For any* content string and any non-empty search query, `findAllMatches(content, query)` shall return every occurrence of the query in the content using case-insensitive comparison. The total match count shall equal the number of non-overlapping case-insensitive occurrences. For an empty query, it shall return zero matches.

**Validates: Requirements 6.2, 6.3, 6.9**

### Property 10: Search navigation wrapping

*For any* positive match count N and any current match index (0 ≤ i < N), navigating "next" from index i shall yield `(i + 1) % N`, and navigating "previous" shall yield `(i - 1 + N) % N`. When N is 0, navigation shall have no effect.

**Validates: Requirements 6.4, 6.5**


## Error Handling

### Diff Computation Errors

- If `computeLineDiff` receives empty strings, it should return an empty diff (no lines) or a single unchanged empty line. No crash.
- For very large files (>10K lines), the LCS algorithm may be slow. Mitigation: add a line-count threshold (~5000 lines) and fall back to a simpler sequential comparison or show a warning. This is a graceful degradation, not a hard error.

### Search Edge Cases

- Empty search query: return zero matches, no highlights. Already handled by the property.
- Regex-special characters in search query: the search uses plain string matching (not regex), so special characters are treated literally. No escaping needed.
- Search on empty content: zero matches.

### Syntax Highlighting Failures

- The existing `hljs.highlight()` call already has a try/catch that falls back to plain text. No change needed.

### Attach to Chat Errors

- If `onAttachToChat` is not provided (undefined), the Attach button should not render. This is a simple conditional render.
- If `attachFile` throws (unlikely since it's a state setter), the feedback timer should not start. Wrap in try/catch.

### File Icon/Color Fallbacks

- `fileIcon()` returns `'draft'` for unknown extensions — this is the existing fallback.
- `fileIconColor()` returns `'var(--color-icon-default)'` for unknown extensions.
- `gitStatusBadge(undefined)` returns `null` — no badge rendered.

### Keyboard Shortcut Conflicts

- Cmd+F for search must not conflict with browser's native find. Since this is a Tauri app (not a browser), the native find is not present. The modal captures the event with `e.preventDefault()`.
- Cmd+S is already handled. The search bar must not intercept it — the keydown handler checks for `e.key === 's'` specifically and lets other shortcuts pass through.

## Testing Strategy

### Property-Based Testing

Use `fast-check` (already a project dependency) for property-based tests. Each property test runs a minimum of 100 iterations.

All property tests go in a single file: `desktop/src/components/common/FileEditorModal.editor-ux.property.test.tsx`

Each test is tagged with a comment referencing the design property:

```typescript
// Feature: file-editor-ux, Property 5: Diff algorithm round-trip
```

**Properties to implement as PBT:**

| Property | What to generate | What to assert |
|----------|-----------------|----------------|
| 1: File icon determinism | Random filename strings with various extensions | Same input → same output, output is non-empty |
| 2: Git status badge completeness | All 7 GitStatus values + undefined | Non-null for valid status, null for undefined |
| 3: Attach FileTreeItem | Random filePath, fileName, workspaceId | Constructed item has correct fields |
| 4: Breadcrumb splitting | Random file paths with `/` separators | Segment count matches, last segment is basename |
| 5: Diff round-trip | Random pairs of multiline strings | Apply diff → reconstruct both original and current |
| 6: Diff mode state machine | Random content + originalContent pairs | Toggle on/off preserves content, controls editability |
| 7: Line count | Random multiline strings | Line count = split('\n').length |
| 8: Cursor to line number | Random content + random cursor position | Line number = newlines before cursor + 1 |
| 9: Search completeness | Random content + random substring | All occurrences found, case-insensitive |
| 10: Search navigation | Random N (match count) + random index | Next/prev wrap correctly |

### Unit Tests

Unit tests complement property tests for specific examples and edge cases. File: `desktop/src/components/common/FileEditorModal.editor-ux.test.tsx`

**Key unit test cases:**

- Breadcrumb with single segment (just a filename, no directory)
- Breadcrumb with deeply nested path (5+ levels)
- Diff with identical content (no changes)
- Diff with completely different content (all removed + all added)
- Diff with empty original (all added)
- Diff with empty current (all removed)
- Search with no matches → "0 of 0"
- Search with query longer than content → no matches
- Search bar does not intercept Cmd+S
- Attach button disabled when `isAttached=true`
- Attach button not rendered when `onAttachToChat` is undefined
- Git status badge not rendered when `gitStatus` is undefined
- Line gutter width for 1-line file vs 10000-line file
- Diff mode disables Save button

### Test Runner

```bash
cd desktop && npm test -- --run
```

Tests use Vitest (already configured in the project). Property tests use `fast-check` with `fc.assert(fc.property(...), { numRuns: 100 })`.
