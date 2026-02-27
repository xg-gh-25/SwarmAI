# Implementation Plan: SwarmWS Explorer UX (Cadence 3 of 4)

## Overview

Transform the Workspace Explorer from a multi-workspace file browser into a semantically-zoned, single-workspace explorer with progressive disclosure, focus mode, global search, and virtualized rendering. Implementation proceeds bottom-up: backend tree endpoint → frontend context/state → leaf components → container components → search → focus mode → virtualization → integration verification.

Cadence 1 (foundation) and Cadence 2 (projects) must be completed first. This cadence builds the visual layer on top of those foundations.

## Tasks

- [x] 1. Backend workspace tree endpoint
  - [x] 1.1 Create `TreeNodeResponse` Pydantic model in `backend/schemas/workspace_config.py`
    - Define `TreeNodeResponse` with fields: name (str), path (str), type (Literal["file", "directory"]), is_system_managed (bool), children (Optional[list["TreeNodeResponse"]])
    - Include module-level docstring per code documentation standards
    - _Requirements: 10.1, 15.1_

  - [x] 1.2 Implement `GET /api/workspace/tree` endpoint in `backend/routers/workspace_api.py`
    - Add `get_workspace_tree(depth: int = Query(default=3, ge=1, le=5))` endpoint
    - Walk workspace root using `os.walk()` bounded by `depth` parameter
    - Call `is_system_managed()` from SwarmWorkspaceManager for each path
    - Exclude hidden files (starting with `.`) except `.project.json`
    - Sort directories first, then files, both alphabetically
    - Return full tree as nested JSON in a single response
    - Compute ETag from recursive max mtime across workspace entries up to depth
    - Accept `If-None-Match` header; return 304 Not Modified if ETag matches
    - Import `Header` from fastapi
    - _Requirements: 10.1, 11.5, 15.1_

  - [x] 1.3 Write property test for tree endpoint structure
    - **Property: Tree endpoint returns valid nested JSON with correct system-managed annotations**
    - Use Hypothesis with `tmp_path` to generate filesystem structures, verify response shape
    - Create `backend/tests/test_workspace_tree_endpoint.py`
    - **Validates: Requirements 10.1, 15.1**

- [x] 2. Checkpoint — Ensure backend tree endpoint tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Frontend types and workspace service extension
  - [x] 3.1 Add `TreeNode` interface to `desktop/src/types/index.ts`
    - Define `TreeNode`: name (string), path (string), type ('file' | 'directory'), isSystemManaged (boolean), children? (TreeNode[])
    - _Requirements: 10.1, 15.1_

  - [x] 3.2 Add `getTree()` method to `desktop/src/services/workspace.ts`
    - Implement `getTree(depth?: number): Promise<TreeNode[]>` calling `GET /api/workspace/tree`
    - Implement `treeNodeToCamelCase()` recursive converter (snake_case → camelCase)
    - Store last ETag from response headers; send `If-None-Match` on subsequent requests
    - On `refreshTree()`, fetch without cached ETag to force fresh response
    - Include `/** */` block comment per code documentation standards
    - _Requirements: 10.1, 11.5_

- [x] 4. ExplorerContext — state management
  - [x] 4.1 Create `desktop/src/contexts/ExplorerContext.tsx`
    - Define `ExplorerState` interface with: treeData, isLoading, error, expandedPaths (Set<string>), toggleExpand, expandAll, collapseAll, selectedPath, setSelectedPath, searchQuery, setSearchQuery, matchedPaths, highlightedPaths, focusMode, toggleFocusMode, activeProjectId, setActiveProjectId, refreshTree
    - Implement `ExplorerProvider` component with `useReducer` or `useState` for state
    - Fetch tree data on mount via `workspaceService.getTree()`
    - Persist `expandedPaths`, `focusMode`, `activeProjectId` to `sessionStorage` under key `swarmws-explorer-state`
    - Restore from `sessionStorage` on mount; silently fall back to defaults on read failure
    - Split provider into three sub-contexts for render performance: `TreeDataContext` (treeData, isLoading, error, refreshTree), `SelectionContext` (expandedPaths, selectedPath, matchedPaths, highlightedPaths, focusMode, activeProjectId), `SearchContext` (searchQuery, setSearchQuery)
    - Export individual hooks: `useTreeData`, `useSelection`, `useSearch` for performance-sensitive components
    - Use `React.memo` boundaries between sub-contexts
    - Include module-level `/** */` docstring per code documentation standards
    - _Requirements: 10.4, 10.5, 11.1, 11.2_

  - [x] 4.2 Implement search state logic in ExplorerContext
    - When `searchQuery` changes, compute `matchedPaths` via `React.startTransition` (or `useDeferredValue`) using case-insensitive substring match on node names
    - Compute `highlightedPaths` as `matchedPaths` union all ancestor paths of matched nodes
    - Snapshot `expandedPaths` before first search, temporarily override with `highlightedPaths`
    - When `searchQuery` is cleared, restore pre-search `expandedPaths` snapshot
    - Show no auto-expand changes when search produces no matches
    - _Requirements: 13.2, 13.3, 13.4, 13.5_

  - [x] 4.3 Implement focus mode state logic in ExplorerContext
    - On toggle ON: snapshot current `expandedPaths`, collapse all non-active project trees under Projects/, expand active project path recursively, keep Knowledge/ visible but collapsed (in flattened list but not in expandedPaths)
    - On toggle OFF: restore the snapshot exactly
    - Disable toggle when `activeProjectId` is null (return early, no-op)
    - _Requirements: 12.1, 12.2, 12.3, 12.5_

  - [x] 4.4 Write property test for toggle expand/collapse
    - **Property 4: Toggle Expand/Collapse**
    - Use fast-check to generate random path strings and expandedPaths sets, verify toggleExpand adds/removes exactly one element
    - Create `desktop/src/contexts/ExplorerContext.property.test.tsx`
    - **Validates: Requirements 11.2**

  - [x] 4.5 Write property test for focus mode state transformation
    - **Property 6: Focus Mode State Transformation**
    - Use fast-check to generate random tree structures with project paths under Projects/, verify focus mode collapses non-active projects, expands active project, keeps Knowledge/ visible but collapsed
    - Add to `desktop/src/contexts/ExplorerContext.property.test.tsx`
    - **Validates: Requirements 12.1, 12.2, 12.3**

  - [ ]* 4.6 Write property test for focus mode round-trip restore
    - **Property 7: Focus Mode Round-Trip Restore**
    - Use fast-check to generate random expandedPaths sets, verify enable→disable restores original state exactly
    - Add to `desktop/src/contexts/ExplorerContext.property.test.tsx`
    - **Validates: Requirements 12.5**

  - [x] 4.7 Write property test for search match, expand, and highlight
    - **Property 8: Search Match, Expand, and Highlight**
    - Use fast-check to generate random trees and substring queries, verify matchedPaths contains all matching nodes, expandedPaths includes all ancestors, matched rows have isMatched=true
    - Add to `desktop/src/contexts/ExplorerContext.property.test.tsx`
    - **Validates: Requirements 13.2, 13.3, 13.4**

  - [ ]* 4.8 Write property test for search clear restores state
    - **Property 9: Search Clear Restores State**
    - Use fast-check to generate random expandedPaths and queries, verify set→clear restores original expandedPaths
    - Add to `desktop/src/contexts/ExplorerContext.property.test.tsx`
    - **Validates: Requirements 13.5**

  - [x] 4.9 Write property test for session state round-trip
    - **Property 10: Session State Round-Trip**
    - Use fast-check to generate random ExplorerSessionState objects, verify serialize→deserialize produces identical state
    - Add to `desktop/src/contexts/ExplorerContext.property.test.tsx`
    - **Validates: Requirements 10.5**

- [x] 5. Checkpoint — Ensure context state management and property tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. CSS variable extensions
  - [x] 6.1 Add explorer CSS variables to `desktop/src/index.css`
    - Add to `:root` (light theme): `--color-explorer-zone-label`, `--color-explorer-zone-separator`, `--color-explorer-indent-guide`, `--color-explorer-search-highlight`, `--color-explorer-accent`, `--color-explorer-system-badge`, `--color-explorer-focus-indicator`
    - Add matching variables to `:root.dark` (dark theme) block
    - Use values from design document; never hardcode colors in components
    - _Requirements: 14.3, 14.4, 14.6_

- [x] 7. Leaf components — TreeNodeRow and ZoneSeparator
  - [x] 7.1 Create `desktop/src/components/workspace-explorer/TreeNodeRow.tsx`
    - Implement `TreeNodeRow` component with props: node, depth, isExpanded, isSelected, isMatched, isSystemManaged, onToggle, onSelect, onContextMenu, onDoubleClick, style (from react-window)
    - Indentation: `depth * 16px` left padding with optional vertical indentation guides (1px lines using `--color-explorer-indent-guide`)
    - Font weight: depth 0 = `font-medium` (500), depth 1+ = `font-normal` (400)
    - System-managed items: lock icon badge, muted text color (`--color-text-muted`), no delete/rename actions
    - User-managed items: accent color on hover (`--color-explorer-accent`), CRUD action icons (`+`, `⋯`) shown only on hover
    - Search match: background `--color-explorer-search-highlight`
    - Selected state: background `--color-primary` at 20% opacity
    - Hover state: background `--color-hover`
    - Expand/collapse chevron: animated 150ms CSS transition on `transform: rotate()`
    - Add ARIA attributes: `role="treeitem"`, `aria-level={depth + 1}`, `aria-expanded={isExpanded}` (directories only), `aria-selected={isSelected}`, `tabIndex={isSelected ? 0 : -1}` for roving tabindex keyboard navigation
    - Include `/** */` block comment per code documentation standards
    - _Requirements: 11.2, 11.3, 14.1, 14.2, 14.4, 14.5, 14.6_

  - [x] 7.2 Create `ZoneSeparator` row renderer (inline in VirtualizedTree or separate small component)
    - Render subtle zone label text with horizontal line separator
    - Non-interactive: no onClick handler, appropriate ARIA role (`role="separator"`)
    - Add `aria-orientation="horizontal"` to zone separator `role="separator"`
    - Use `--color-explorer-zone-label` and `--color-explorer-zone-separator` CSS variables
    - Fixed 32px height matching tree row height
    - _Requirements: 10.1, 10.2, 14.3_

  - [ ]* 7.3 Write property test for system-managed CRUD and accent suppression
    - **Property 2: System-Managed Items Suppress CRUD Actions and Accent Colors**
    - Use fast-check to generate TreeNode with random isSystemManaged boolean, verify system-managed rows have no delete/rename controls and no accent color styling, user-managed rows have CRUD controls and accent color on hover
    - Create `desktop/src/components/workspace-explorer/TreeNodeRow.property.test.tsx`
    - **Validates: Requirements 14.4, 14.5**

  - [x] 7.4 Write property test for depth-based visual properties
    - **Property 12: Depth-Based Visual Properties**
    - Use fast-check to generate random depth values (0–5), verify left padding = depth * 16px, font-weight 500 at depth 0, 400 at depth 1+
    - Add to `desktop/src/components/workspace-explorer/TreeNodeRow.property.test.tsx`
    - **Validates: Requirements 14.1, 14.2**

- [x] 8. VirtualizedTree component
  - [x] 8.1 Install `react-window` and `react-virtualized-auto-sizer` dependencies
    - Run `npm install react-window react-virtualized-auto-sizer` in `desktop/`
    - Install type definitions: `npm install -D @types/react-window @types/react-virtualized-auto-sizer`
    - _Requirements: 15.1, 15.2_

  - [x] 8.2 Create `desktop/src/components/workspace-explorer/VirtualizedTree.tsx`
    - Implement tree flattening algorithm:
      1. Root-level files (system-prompts.md, context-L0.md, context-L1.md) first
      2. Zone separator "Shared Knowledge" before Knowledge/
      3. Shared Knowledge folder: Knowledge/
      4. Zone separator "Active Work" before Projects/
      5. Active Work: Projects/
    - Recursively include children for directories in `expandedPaths`
    - Define `SEMANTIC_ZONES` and `ROOT_FILES` constants per design:
      - `SEMANTIC_ZONES`: two zones — "Shared Knowledge" (paths: ['Knowledge']), "Active Work" (paths: ['Projects'])
      - `ROOT_FILES`: ['system-prompts.md', 'context-L0.md', 'context-L1.md']
    - Define `FlattenedRow` type: `{ kind: 'zone-separator', zoneLabel } | { kind: 'node', node, depth, isMatched, isExpanded }`
    - Use `react-window` `FixedSizeList` with fixed 32px row height
    - Add `role="tree"` and `aria-label="Workspace Explorer"` to `FixedSizeList` container
    - Use `itemKey` callback returning node path for `kind:'node'` rows and `zone:{zoneLabel}` for `kind:'zone-separator'` rows for stable React keys
    - Render `TreeNodeRow` for node rows, `ZoneSeparator` for separator rows
    - Read `expandedPaths`, `matchedPaths`, `selectedPath` from `ExplorerContext`
    - Include `/** */` block comment per code documentation standards
    - _Requirements: 10.1, 10.2, 10.3, 11.1, 11.4, 15.1, 15.2, 15.3_

  - [x] 8.3 Write property test for semantic zone grouping correctness
    - **Property 1: Semantic Zone Grouping Correctness**
    - Use fast-check to generate random TreeNode[] with varying folder structures, verify: root files before first separator, exactly two zone separators in order ("Shared Knowledge", "Active Work"), Knowledge/ in Shared Knowledge zone, Projects/ in Active Work zone, correct ordering within zones
    - Create `desktop/src/components/workspace-explorer/VirtualizedTree.property.test.tsx`
    - **Validates: Requirements 10.1, 10.3**

  - [ ]* 8.4 Write property test for default collapsed view
    - **Property 3: Default Collapsed View**
    - Use fast-check to generate random TreeNode[] with empty expandedPaths, verify flattened list contains only root files, zone separators, and top-level section folders (Knowledge, Projects) — no child nodes
    - Add to `desktop/src/components/workspace-explorer/VirtualizedTree.property.test.tsx`
    - **Validates: Requirements 10.4, 11.1**

  - [ ]* 8.5 Write property test for flattening respects expand state
    - **Property 5: Flattening Respects Expand State**
    - Use fast-check to generate random TreeNode[] and random expandedPaths subsets, verify children appear iff parent is in expandedPaths
    - Add to `desktop/src/components/workspace-explorer/VirtualizedTree.property.test.tsx`
    - **Validates: Requirements 11.2**

  - [x] 8.6 Write property test for virtualization renders fewer DOM nodes
    - **Property 11: Virtualization Renders Fewer DOM Nodes**
    - Use fast-check to generate large TreeNode[] (500+ nodes), verify rendered DOM row count < total item count, bounded by ceil(containerHeight / rowHeight) + overscanCount
    - Add to `desktop/src/components/workspace-explorer/VirtualizedTree.property.test.tsx`
    - **Validates: Requirements 15.1, 15.2**

- [x] 9. Checkpoint — Ensure tree rendering and virtualization tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. ExplorerHeader and GlobalSearchBar
  - [x] 10.1 Create `desktop/src/components/workspace-explorer/ExplorerHeader.tsx`
    - Display "SwarmWS" as static header title (`font-medium`, `text-sm`)
    - Include collapse toggle button (chevron, same pattern as current explorer)
    - Include Focus Mode toggle: small icon button with tooltip "Focus on Current Project"
    - Disable Focus Mode toggle when `activeProjectId` is null (grayed out, tooltip: "Select a project first")
    - Add manual refresh button as fallback for external filesystem changes (until SSE is wired in Cadence 4)
    - Use `toggleFocusMode` and `activeProjectId` from `ExplorerContext`
    - Remove all old controls: workspace dropdown, Global/SwarmWS toggle, "Show Archived" checkbox, "New Workspace" button, add-context area, inline search bar
    - Include `/** */` block comment per code documentation standards
    - _Requirements: 9.1, 9.3, 9.4, 9.5, 9.6, 9.7, 12.4_

  - [x] 10.2 Create `desktop/src/components/layout/GlobalSearchBar.tsx`
    - Render search input with search icon and placeholder "Search files and folders..."
    - Use `searchQuery` and `setSearchQuery` from `ExplorerContext`
    - Debounce input by 150ms before updating context
    - Centered in TopBar, full-width within content area (leaving space for macOS traffic lights)
    - _Requirements: 9.2, 13.1_

  - [x] 10.3 Integrate `GlobalSearchBar` into `TopBar` in `desktop/src/components/layout/ThreeColumnLayout.tsx`
    - Add `<GlobalSearchBar />` centered in the TopBar
    - Ensure TopBar remains draggable (Tauri window drag) except over the search input
    - _Requirements: 9.2, 13.1_

  - [x] 10.4 Write unit tests for ExplorerHeader
    - Verify "SwarmWS" title renders
    - Verify old controls are absent (no dropdown, no toggle, no checkbox, no "New Workspace" button, no add-context area)
    - Verify Focus Mode toggle disabled when no project selected
    - Create `desktop/src/components/workspace-explorer/ExplorerHeader.test.tsx`
    - **Validates: Requirements 9.1, 9.3, 9.4, 9.5, 9.6, 9.7, 12.4**

  - [x] 10.5 Write unit tests for GlobalSearchBar
    - Verify renders in TopBar with correct placeholder
    - Verify debounce behavior (150ms delay before context update)
    - Create `desktop/src/components/layout/GlobalSearchBar.test.tsx`
    - **Validates: Requirements 9.2, 13.1**

- [x] 11. Redesign WorkspaceExplorer container
  - [x] 11.1 Rewrite `desktop/src/components/workspace-explorer/WorkspaceExplorer.tsx`
    - Replace entire component internals with new structure:
      - `ExplorerHeader` at top
      - `AutoSizer` wrapping `VirtualizedTree` for dynamic sizing
    - Fetch tree data on mount via `workspaceService.getTree()`, store in `ExplorerContext`
    - Re-fetch on `refreshTree()` (triggered after folder/file CRUD operations)
    - Remove old `WorkspaceHeader`, `SectionNavigation`, `FileTree`, `FileTreeNode` usage
    - Remove multi-workspace listing, archive/unarchive/delete logic
    - Remove `showArchived` toggle and workspace dropdown
    - Handle error state: "Failed to load workspace tree. [Retry]"
    - Handle empty state: "SwarmWS is empty. Initialize your workspace to get started."
    - Include `/** */` block comment per code documentation standards
    - _Requirements: 9.1, 9.3, 9.4, 9.5, 9.6, 9.7, 10.1, 10.4, 11.1, 15.1_

  - [x] 11.2 Wrap explorer tree in `ExplorerProvider` in `ThreeColumnLayout.tsx`
    - Wrap the `WorkspaceExplorer` and `TopBar` (which contains `GlobalSearchBar`) in `<ExplorerProvider>` so both can access shared state
    - _Requirements: 9.2, 13.1_

- [x] 12. Checkpoint — Ensure header, search, and explorer container tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Remove dead explorer code
  - [x] 13.1 Remove or deprecate old explorer components
    - Remove `WorkspaceHeader` component (replaced by `ExplorerHeader`)
    - Remove `SectionNavigation` component (replaced by semantic zones in `VirtualizedTree`)
    - Remove `FileTree` and `FileTreeNode` components (replaced by `VirtualizedTree` and `TreeNodeRow`)
    - Remove inline search bar from old explorer (replaced by `GlobalSearchBar` in TopBar)
    - Update or remove any imports referencing these deleted components
    - _Requirements: 9.3, 9.4, 9.5, 9.6, 9.7_

  - [x] 13.2 Update or remove old explorer test files
    - Update `desktop/src/components/workspace-explorer/WorkspaceExplorer.test.tsx` to test new component structure
    - Remove tests for deleted components (WorkspaceHeader, SectionNavigation, FileTree, FileTreeNode)
    - Ensure no broken imports or references to removed modules
    - _Requirements: 9.3, 9.7_

- [x] 14. Checkpoint — Ensure dead code removal is clean and all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Final integration verification
  - [x] 15.1 Verify end-to-end explorer flow
    - Ensure `GET /api/workspace/tree` returns correct nested JSON for a populated SwarmWS
    - Ensure `ExplorerContext` fetches tree, populates state, and renders `VirtualizedTree`
    - Verify semantic zones display in correct order: root files → "Shared Knowledge" (Knowledge/) → "Active Work" (Projects/)
    - Verify expand/collapse persists across session navigation
    - Verify search highlights matched nodes and auto-expands ancestors
    - Verify focus mode collapses non-active projects, expands active project, keeps Knowledge/ visible but collapsed
    - Verify focus mode restore returns to pre-focus state
    - Run `cd backend && pytest` to confirm all backend tests pass
    - Run `cd desktop && npm test -- --run` to confirm all frontend tests pass
    - _Requirements: 10.1, 10.5, 11.2, 12.1, 12.5, 13.2, 13.5, 15.1_

- [x] 16. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after each major phase
- Property tests validate universal correctness properties from the design document (Properties 1–12)
- Unit tests validate specific examples and edge cases
- Frontend property tests use fast-check; backend property tests use Hypothesis
- All CSS colors use `--color-*` variables — never hardcode color values in components
- The search bar lives in the TopBar (full-width, above all three columns), not in the Workspace Explorer
- `react-window` with `FixedSizeList` (32px row height) handles virtualization; `react-virtualized-auto-sizer` provides dynamic container sizing
- Two semantic zones: "Shared Knowledge" (Knowledge/) and "Active Work" (Projects/) — no Operating Loop zone
- Focus Mode collapses non-active project trees and keeps Knowledge/ visible (collapsed but accessible)
- This cadence depends on Cadence 1 (foundation) and Cadence 2 (projects) being completed first
