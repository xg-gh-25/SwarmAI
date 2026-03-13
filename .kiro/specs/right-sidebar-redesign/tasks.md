# Implementation Plan: Right Sidebar Redesign

## Overview

Replace the multi-panel toggle sidebar (SwarmRadar / ChatHistory / FileBrowser) with a persistent HUD-style Radar sidebar. Implementation proceeds bottom-up: shared primitives → backend API → sections → shell → drag-drop → ChatInput integration → legacy cleanup. TypeScript (frontend) and Python (backend).

## Tasks

- [x] 1. Create shared primitives and types
  - [x] 1.1 Create `DropPayload` type, `RadarArtifact` interface, and sidebar prop interfaces in `desktop/src/pages/chat/components/RightSidebar/types.ts`
    - Define `DropPayload` union type (`file | radar-todo | radar-artifact`)
    - Define `RadarArtifact`, `RadarSidebarProps`, `CollapsibleSectionProps`, `HistoryViewProps`
    - Define localStorage key constants for `radar-sidebar-width`, `radar-section-{name}`, `radar-tip-dismissed`
    - _Requirements: 8.6, 8.7, 6.4, 12.1_

  - [x] 1.2 Create `CollapsibleSection.tsx` in `desktop/src/pages/chat/components/RightSidebar/shared/`
    - Render header row with icon, label, count badge, and one-line status hint
    - Toggle expand/collapse on header click
    - Read/write expand/collapse state to `localStorage` keyed by `radar-section-{name}`
    - Accept `defaultExpanded` prop; ToDo defaults to `true`, others to `false`
    - Handle missing/corrupt localStorage gracefully (fall back to defaults)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 11.1, 11.2, 11.3_

  - [ ]* 1.3 Write property tests for CollapsibleSection (fast-check)
    - **Property P3: Collapse State Round-Trip** — For any section name and boolean, localStorage round-trip preserves value. Missing/corrupt uses defaults.
    - **Validates: Requirements 3.4, 3.5, 3.6, 11.1, 11.2, 11.3, 11.4**
    - **Property P6: Badge Count Accuracy** — For any section data, badge count matches item count.
    - **Validates: Requirements 4.5, 5.8, 7.6, 10.4**
    - Test file: `CollapsibleSection.pbt.test.tsx`

  - [x] 1.4 Create `DragHandle.tsx` in `desktop/src/pages/chat/components/RightSidebar/shared/`
    - Render vertical grip icon (⋮⋮), visible on parent row hover only (CSS `:hover` on parent)
    - Set `draggable="true"`, `aria-label="Drag to chat"`, `role="button"`
    - Use HTML5 `dataTransfer.setData('application/json', JSON.stringify(payload))` to transfer `DropPayload`
    - Show semi-transparent ghost preview while dragging
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ]* 1.5 Write property test for DragHandle (fast-check)
    - **Property P12: Drag Payload Correctness** — For any draggable item, DragHandle sets correct dataTransfer payload with proper type discriminator and fields.
    - **Validates: Requirements 8.1, 8.3, 8.4, 8.6, 8.7**
    - Test file: `DragHandle.pbt.test.tsx`


- [x] 2. Implement backend Artifacts API
  - [x] 2.1 Create `backend/routers/artifacts.py` with `GET /api/artifacts/recent` endpoint
    - Accept `workspace_id` (required) and `limit` (default 20, max 50) query params
    - Resolve `workspace_id` to filesystem path via DB lookup (same pattern as other workspace-scoped endpoints)
    - Run `git log --diff-filter=ACMR --name-only --format=%aI --since=30.days -n{limit*3} --no-merges` via `anyio.to_thread.run_sync` with `timeout=5`
    - Parse output, deduplicate by path (keep most recent), derive type from `EXTENSION_TYPE_MAP`
    - Return `ArtifactResponse` list with snake_case fields: `path`, `title`, `type`, `modified_at`
    - Handle errors: non-repo returns empty list, timeout returns empty list, missing workspace returns 404, invalid params returns 422
    - Register router in `backend/main.py`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [ ]* 2.2 Write property tests for Artifacts API (hypothesis)
    - **Property P8: Artifact File Type Classification** — For any file path, type classification is deterministic and case-insensitive on extension.
    - **Validates: Requirements 6.4**
    - **Property P9: Artifact Deduplication** — For any git log with duplicate paths, only one entry per path (most recent) returned.
    - **Validates: Requirements 6.2**
    - Test files: `backend/tests/test_property_artifact_type.py`, `backend/tests/test_property_artifact_dedup.py`

  - [x] 2.3 Add `fetchRecentArtifacts` and `artifactToCamelCase` to `desktop/src/services/radar.ts`
    - `fetchRecentArtifacts(workspaceId: string, limit?: number)` calls `GET /api/artifacts/recent`
    - `artifactToCamelCase` converts snake_case backend response to camelCase `RadarArtifact`
    - _Requirements: 6.5, 7.4_

  - [ ]* 2.4 Write property test for artifactToCamelCase (fast-check)
    - **Property P11: Artifact snake_case to camelCase** — For any backend record, `artifactToCamelCase` preserves all values with camelCase keys.
    - **Validates: Requirements 6.5**
    - Test file: `backend/tests/test_property_artifact_conversion.py` (backend) and inline fast-check in service test (frontend)

- [x] 3. Checkpoint — Ensure shared primitives and backend API tests pass
  - Ensure all tests pass, ask the user if questions arise.


- [x] 4. Implement Radar sections
  - [x] 4.1 Create `TodoSection.tsx` in `desktop/src/pages/chat/components/RightSidebar/`
    - Display active ToDo items (status `pending` or `overdue`) as read-only list via `radarService.fetchActiveTodos`
    - Each item: title, priority indicator, DragHandle (type `radar-todo`)
    - No action buttons (no start, edit, complete, cancel, delete)
    - Display limit: top 5 by default, sorted by priority (high→low) then creation date (newest first)
    - "See more ({remaining} more)" / "Show less" links; state resets on mount (not persisted)
    - Empty-state message when no active ToDos
    - Count badge = number of active ToDo items
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 2.6_

  - [ ]* 4.2 Write property tests for TodoSection (fast-check)
    - **Property P5: Active ToDo Filtering** — For any ToDo list, only pending/overdue items displayed.
    - **Validates: Requirements 4.1**
    - **Property P15: ToDo Display Limit and Sort** — For any N>5 active ToDos, exactly 5 shown, sorted by priority then date.
    - **Validates: Requirements 14.1, 14.2, 14.5**
    - Test file: `TodoSection.pbt.test.tsx`

  - [x] 4.3 Create `ArtifactsSection.tsx` in `desktop/src/pages/chat/components/RightSidebar/`
    - Fetch from `radarService.fetchRecentArtifacts` on mount and when sidebar becomes visible
    - Display artifacts in reverse chronological order (most recently modified first)
    - Each item: title (filename), type icon (based on extension), relative timestamp, DragHandle (type `radar-artifact`)
    - Click opens file preview (existing mechanism)
    - Empty-state message when no artifacts
    - Count badge = number of artifacts
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [ ]* 4.4 Write property test for ArtifactsSection (fast-check)
    - **Property P10: Artifacts Reverse Chronological Order** — For any artifact list, sorted by modifiedAt descending.
    - **Validates: Requirements 7.1**
    - Test file: `ArtifactsSection.pbt.test.tsx`

  - [x] 4.5 Create `SessionsSection.tsx` in `desktop/src/pages/chat/components/RightSidebar/`
    - Display open chat tabs derived from `tabStatuses` prop (reactive, not polling)
    - Each session: title, agent name, status (idle, streaming, waiting for input, error)
    - Visual indicator for pending question/permission request
    - Click switches active tab via `onTabSelect`
    - "Chat History" link in header area; clicking switches sidebar to History mode
    - Count badge = number of open tabs
    - Fold `WaitingInputList` display logic into this section (Req 2.7)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 2.7_

  - [ ]* 4.6 Write property test for SessionsSection (fast-check)
    - **Property P7: Sessions Reflects tabMapRef** — For any open tabs, Sessions_Section renders one entry per tab with matching status.
    - **Validates: Requirements 5.1, 5.2, 5.4**
    - Test file: `SessionsSection.pbt.test.tsx`

  - [x] 4.7 Create `JobsSection.tsx` in `desktop/src/pages/chat/components/RightSidebar/`
    - Fetch from `radarService.fetchAutonomousJobs`
    - Each job: name, status indicator (running, paused, error, completed), category
    - Empty-state message when no jobs
    - Count badge = number of active (non-completed) jobs
    - _Requirements: 10.1, 10.2, 10.3, 10.4_


- [x] 5. Implement RadarView, HistoryView, and RadarSidebar shell
  - [x] 5.1 Create `RadarView.tsx` in `desktop/src/pages/chat/components/RightSidebar/`
    - Compose `TodoSection`, `ArtifactsSection`, `SessionsSection`, `JobsSection` inside `CollapsibleSection` wrappers
    - Pass through props from `RadarSidebar`
    - _Requirements: 12.2, 12.4_

  - [x] 5.2 Create `HistoryView.tsx` in `desktop/src/pages/chat/components/RightSidebar/`
    - Search input at top; filter sessions case-insensitive by title
    - Display sessions grouped by time period (Today, Yesterday, This Week, This Month, Older) using existing `groupSessionsByTime` and `formatTimestamp` from `desktop/src/pages/chat/utils.ts`
    - Each session: title, agent name, relative timestamp
    - Click session → switch to Radar mode and activate session tab
    - Back arrow / Mode_Toggle returns to Radar mode
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

  - [ ]* 5.3 Write property test for HistoryView search filtering (fast-check)
    - **Property P17: History Search Filtering** — For any search string, only sessions with matching title (case-insensitive) shown, time-grouped.
    - **Validates: Requirements 9.2, 9.3**
    - Test file: `HistoryView.pbt.test.tsx`

  - [x] 5.4 Create `RadarSidebar.tsx` shell in `desktop/src/pages/chat/components/RightSidebar/`
    - Persistent right-side panel, always visible when chat page is mounted
    - Header: mode label ("Radar" / "History"), Mode_Toggle icon button, 💡 Feature Tip icon
    - Local `useState` for mode, defaults to `'radar'` — NOT persisted to localStorage
    - Render `RadarView` or `HistoryView` based on mode
    - Left-edge drag handle for horizontal resizing
    - Persist width to `localStorage` key `radar-sidebar-width`, restore on mount (default 320)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 12.1, 12.4_

  - [ ]* 5.5 Write property tests for RadarSidebar (fast-check)
    - **Property P1: Mode Toggle Alternation** — For any sequence of N toggle clicks, mode is history if N odd, radar if N even. Always starts radar.
    - **Validates: Requirements 1.3, 1.4, 1.5, 9.6, 12.4**
    - **Property P2: Sidebar Width Round-Trip** — For any width in [200,600], localStorage round-trip preserves exact value.
    - **Validates: Requirements 1.7**
    - Test file: `RadarSidebar.pbt.test.tsx`

  - [x] 5.6 Implement consolidated Feature Tip in RadarSidebar header
    - 💡 icon next to mode label
    - Hover/click shows popover with consolidated feature overview text (per Req 15.3)
    - "Don't show again" option persists dismissal to `localStorage` key `radar-tip-dismissed`
    - After dismissal: icon still visible, popover only on explicit click (no auto-show)
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5_

  - [ ]* 5.7 Write property test for Feature Tip dismissal (fast-check)
    - **Property P16: Feature Tip Dismissal** — Dismissing writes true to `radar-tip-dismissed`. Subsequent mounts: popover only on explicit click.
    - **Validates: Requirements 15.1, 15.4, 15.5**
    - Test file: `RadarSidebar.pbt.test.tsx`

  - [x] 5.8 Create `index.ts` barrel export in `desktop/src/pages/chat/components/RightSidebar/`
    - Export `RadarSidebar` as the public API of the directory
    - _Requirements: 12.1_

- [x] 6. Checkpoint — Ensure all section and sidebar tests pass
  - Ensure all tests pass, ask the user if questions arise.


- [x] 7. Extend ChatInput for tab-scoped radar drops
  - [x] 7.1 Add new props to `ChatInput.tsx`: `activeTabIdRef`, `inputValueMapRef`, `onInputValueChange`
    - Update ChatInput props interface
    - _Requirements: 8.13_

  - [x] 7.2 Extend `handleDragOver` in `ChatInput.tsx` to accept `application/json` dataTransfer type
    - Currently only accepts `Files`; add `application/json` so drop zone activates for radar drag items
    - _Requirements: 8.12_

  - [x] 7.3 Extend `handleDrop` in `ChatInput.tsx` for radar DropPayload processing
    - Parse `application/json` from `dataTransfer.getData`
    - Read active tab ID synchronously from `activeTabIdRef` at drop time (no async gap)
    - Write populated text to `inputValueMapRef` keyed by active tab ID (single synchronous read-and-write)
    - Only update visible textarea if tab ID matches currently rendered tab
    - For `radar-todo`: populate with ToDo title and context
    - For `radar-artifact`: populate with artifact title and path reference
    - Focus input cursor after population
    - Do NOT auto-send — user decides when to send
    - Existing file-drop behavior (`Files` type) must continue to work unchanged
    - _Requirements: 8.8, 8.9, 8.10, 8.11, 8.12, 8.13, 13.1, 13.2, 13.3, 13.4, 13.5_

  - [ ]* 7.4 Write property tests for ChatInput drop handling (fast-check)
    - **Property P13: Drop Populates Without Auto-Send** — For any valid DropPayload, ChatInput populated and focused, send not invoked.
    - **Validates: Requirements 8.8, 8.9, 8.10**
    - **Property P14: Tab-Scoped Drop Isolation** — For any N drops interleaved with tab switches, each drop targets only the tab active at drop time.
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.6**
    - Test file: `ChatInput.pbt.test.tsx`

- [x] 8. Checkpoint — Ensure ChatInput drop tests pass
  - Ensure all tests pass, ask the user if questions arise.


- [x] 9. Wire RadarSidebar into ChatPage and pass props
  - [x] 9.1 Import `RadarSidebar` in `ChatPage.tsx` and render it in place of the three conditional sidebar blocks
    - Replace the `{rightSidebars.isActive('todoRadar') && <SwarmRadar ...>}`, `{rightSidebars.isActive('chatHistory') && <ChatHistorySidebar ...>}`, and `{rightSidebars.isActive('fileBrowser') && <FileBrowserSidebar ...>}` blocks with a single `<RadarSidebar ... />` component
    - Pass required props: `tabMapRef`, `activeTabIdRef`, `openTabs`, `tabStatuses`, `onTabSelect`, `inputValueMapRef`, `onInputValueChange`, `groupedSessions`, `agents`, `onSelectSession`, `onDeleteSession`, `workspaceId`
    - Remove `useRightSidebarGroup` hook call and `rightSidebars` usage from ChatPage
    - _Requirements: 1.1, 2.11, 2.12, 12.1_

  - [x] 9.2 Pass `activeTabIdRef`, `inputValueMapRef`, and `onInputValueChange` props to `ChatInput` in `ChatPage.tsx`
    - Wire the new ChatInput props from ChatPage's existing refs
    - _Requirements: 8.13_

  - [x] 9.3 Remove sidebar toggle buttons and related props from `ChatHeader.tsx`
    - Remove the three right-sidebar toggle icon buttons (checklist, history, folder)
    - Remove `activeSidebar` and `onOpenSidebar` props from ChatHeader interface and usage
    - _Requirements: 2.1_


- [x] 10. Legacy cleanup — delete removed components, hooks, constants, and tests
  - [x] 10.1 Delete `useRightSidebarGroup` hook and its tests
    - Delete `desktop/src/hooks/useRightSidebarGroup.ts`
    - Delete `desktop/src/hooks/useRightSidebarGroup.test.ts`
    - Delete `desktop/src/hooks/useRightSidebarGroup.property.test.ts`
    - Remove `useRightSidebarGroup` export from `desktop/src/hooks/index.ts`
    - _Requirements: 2.2_

  - [x] 10.2 Delete legacy sidebar constants from `desktop/src/pages/chat/constants.ts`
    - Remove `RIGHT_SIDEBAR_IDS`, `RightSidebarId` type, `RIGHT_SIDEBAR_WIDTH_CONFIGS`, `SidebarWidthConfig` interface, `DEFAULT_ACTIVE_SIDEBAR`
    - _Requirements: 2.3_

  - [x] 10.3 Delete `FileBrowserSidebar` component
    - Delete `desktop/src/pages/chat/components/FileBrowserSidebar.tsx` and any associated test files
    - _Requirements: 2.4_

  - [x] 10.4 Delete `QuickAddTodo` component
    - Delete `desktop/src/pages/chat/components/radar/QuickAddTodo.tsx` and any associated files
    - _Requirements: 2.5_

  - [x] 10.5 Delete `WaitingInputList` standalone component
    - Delete `desktop/src/pages/chat/components/radar/WaitingInputList.tsx` (display logic folded into SessionsSection)
    - _Requirements: 2.7_

  - [x] 10.6 Delete `ChatHistorySidebar` component
    - Delete `desktop/src/pages/chat/components/ChatHistorySidebar.tsx` and any associated test files
    - Session-fetching and time-grouping logic already reused in HistoryView via `utils.ts`
    - _Requirements: 2.8_

  - [x] 10.7 Delete `SwarmRadar` component and radar directory legacy files
    - Delete `desktop/src/pages/chat/components/radar/SwarmRadar.tsx` and `SwarmRadar.css`
    - Delete other legacy radar files no longer needed: `RadarZone.tsx`, `TodoItem.tsx`, `TodoList.tsx`, `WipTaskItem.tsx`, `WipTaskList.tsx`, `CompletedTaskItem.tsx`, `CompletedTaskList.tsx`, `AutonomousJobItem.tsx`, `AutonomousJobList.tsx`, `radarConstants.ts`, `radarIndicators.ts`, `mockData.ts`
    - Preserve `radarSortUtils.ts` and `hooks/` subdirectory if reused by new sections; delete if not
    - _Requirements: 2.9_

  - [x] 10.8 Add legacy localStorage key cleanup to RadarSidebar first-mount logic
    - On first mount, remove keys: `todoRadarSidebarWidth`, `chatSidebarWidth`, `rightSidebarWidth`, `chatSidebarCollapsed`, `rightSidebarCollapsed`, `todoRadarSidebarCollapsed`
    - _Requirements: 2.10_

  - [x] 10.9 Remove legacy imports from `ChatPage.tsx` and component barrel `index.ts`
    - Remove `SwarmRadar`, `ChatHistorySidebar`, `FileBrowserSidebar` imports from `ChatPage.tsx`
    - Remove `useRightSidebarGroup` import from `ChatPage.tsx`
    - Update `desktop/src/pages/chat/components/index.ts` to remove deleted component exports and add `RadarSidebar` export
    - _Requirements: 2.11, 2.12_

  - [x] 10.10 Delete test files that exclusively test removed components
    - Delete `ChatHeader.property.test.tsx` (tests sidebar toggle buttons being removed) or update to remove sidebar toggle assertions
    - Update `desktop/src/pages/chat/components/__tests__/memoryRelocation.preservation.property.test.tsx` to remove `RightSidebarId` / sidebar toggle references
    - Preserve `desktop/src/pages/chat/utils.test.ts` (time-grouping tests reused by HistoryView)
    - _Requirements: 2.13_

- [x] 11. Checkpoint — Ensure all tests pass after legacy cleanup
  - Ensure all tests pass, ask the user if questions arise.


- [ ] 12. Final integration and remaining property tests
  - [ ]* 12.1 Write property test for section item display completeness (fast-check)
    - **Property P4: Section Item Display Completeness** — For any valid item, rendered row contains all required fields per section type.
    - **Validates: Requirements 3.1, 4.2, 5.3, 7.2, 10.2**
    - Test file: `SectionItems.pbt.test.tsx`

  - [ ]* 12.2 Write unit tests for key integration scenarios
    - Sidebar renders on mount (Req 1.1)
    - Header has mode label + toggle (Req 1.2)
    - ChatHeader has no toggle buttons (Req 2.1)
    - ToDo has no action buttons (Req 2.6)
    - Empty states for ToDo, Artifacts, Jobs (Req 4.4, 7.5, 10.3)
    - Corrupt localStorage uses defaults (Req 3.6, 11.3)
    - Click session switches tab (Req 5.5)
    - Chat History link switches to History mode (Req 5.6)
    - Click artifact opens preview (Req 7.3)
    - See more / Show less toggle (Req 14.3, 14.4)
    - Display limit resets on mount (Req 14.6)
    - Feature tip text matches spec (Req 15.3)
    - File drop still works (Req 8.11)
    - History mode has search input (Req 9.1)
    - Click session in History activates tab (Req 9.5)
    - git log timeout returns empty (Req 6.2)

- [x] 13. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- Legacy cleanup (task 10) is sequenced after new sidebar is wired in, so the app is never in a broken state
- `radarSortUtils.ts` and `hooks/useTodoZone.ts`, `hooks/useJobZone.ts`, `hooks/useTaskZone.ts` may be reused or adapted by new sections — evaluate during implementation before deleting
- Consult `multi-tab-isolation-principles.md` steering file before modifying ChatInput drop handlers (task 7)
