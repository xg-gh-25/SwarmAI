# Implementation Plan: Swarm Radar WIP & Completed Tasks (Sub-Spec 4 of 5)

## Overview

Build the WIP Tasks and Completed Tasks layers of the Swarm Radar — backend task query extensions (status filtering, `completed_after`, `review_required`/`review_risk_level` fields), frontend radar.ts service additions, the `useTaskZone` state management hook with React Query polling and optimistic updates, radar constants, and the WipTaskList/WipTaskItem/CompletedTaskList/CompletedTaskItem UI components. Backend first, then service layer, then constants and hook, then components, then wiring and SSE integration.

## Tasks

- [ ] 1. Backend schema extensions and query support
  - [ ] 1.1 Extend `TaskResponse` model in `backend/schemas/task.py`
    - Add `review_required: bool = False` field (always false in initial release)
    - Add `review_risk_level: Optional[str] = None` field (always null in initial release)
    - Use snake_case field names per backend convention
    - Include module-level docstring update per dev rules
    - _Requirements: 6.2, 6.3, 6.5_

  - [ ] 1.2 Extend `list_tasks` endpoint in `backend/routers/tasks.py`
    - Add `workspace_id: Optional[str]` query parameter
    - Add `completed_after: Optional[str]` query parameter (ISO 8601 date string)
    - Support comma-separated `status` values with OR semantics (e.g., `status=wip,draft,blocked`)
    - AND semantics across different parameter types (e.g., `status=completed&workspace_id=abc`)
    - Pass new parameters to `task_manager.list_tasks`
    - _Requirements: 6.1, 6.4, 6.7_

  - [ ] 1.3 Implement filtering logic in task manager
    - Extend `task_manager.list_tasks` to support `workspace_id` filtering
    - Extend `task_manager.list_tasks` to support `completed_after` filtering (return only tasks with `completed_at` after the specified ISO 8601 date)
    - Extend `task_manager.list_tasks` to split comma-separated `status` string and filter with OR semantics
    - _Requirements: 6.1, 6.4, 6.7_


  - [ ]* 1.4 Write property test for backend task filtering (Property 4)
    - **Property 4: Backend task filtering returns only matching records**
    - Create `backend/tests/test_task_filtering.py`
    - Use `hypothesis` to generate random task sets with all 5 statuses and random completion dates
    - Verify comma-separated status filter uses OR semantics
    - Verify `completed_after` filter uses AND semantics with status filter
    - Verify no task outside filter criteria appears in the result
    - Minimum 100 iterations
    - **Validates: Requirements 6.1, 6.4, 6.7**

- [ ] 2. Checkpoint — Backend extensions
  - Ensure all backend tests pass (`cd backend && pytest`), ask the user if questions arise.

- [ ] 3. Frontend type extensions
  - [ ] 3.1 Update `Task` interface in `desktop/src/types/index.ts`
    - Add `reviewRequired: boolean` field (always false in initial release)
    - Add `reviewRiskLevel: string | null` field (always null in initial release)
    - _Requirements: 6.6_

  - [ ] 3.2 Update `toCamelCase` in `desktop/src/services/tasks.ts`
    - Add `reviewRequired` mapping from `review_required` (default `false`)
    - Add `reviewRiskLevel` mapping from `review_risk_level` (default `null`)
    - _Requirements: 6.6_

- [ ] 4. Radar constants and service layer
  - [ ] 4.1 Create `desktop/src/pages/chat/components/radar/radarConstants.ts`
    - Define `ARCHIVE_WINDOW_DAYS = 7` constant
    - Define `TASK_POLLING_INTERVAL_MS = 30_000` constant
    - Include module-level docstring per dev rules
    - _Requirements: 5.4, 9.4_

  - [ ] 4.2 Add task functions to `desktop/src/services/radar.ts`
    - Implement `taskToCamelCase(task)` mapping all snake_case fields to camelCase, setting `hasWaitingInput: false`
    - Implement `completedTaskToCamelCase(task)` mapping all snake_case fields including `reviewRequired` and `reviewRiskLevel`
    - Add `fetchWipTasks(workspaceId?)` to `radarService` — fetches with `status=wip,draft,blocked`
    - Add `fetchCompletedTasks(workspaceId?, completedAfter?)` to `radarService` — fetches with `status=completed` and optional `completed_after`
    - Add `cancelTask(taskId)` to `radarService` — POST to `/tasks/{id}/cancel`
    - Use existing HTTP client pattern from `desktop/src/services/tasks.ts`
    - Include module-level docstring update per dev rules
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

- [ ] 5. Implement `useTaskZone` hook
  - [ ] 5.1 Create `desktop/src/pages/chat/components/radar/hooks/useTaskZone.ts`
    - Implement React Query data fetching for WIP tasks with key `['radar', 'wipTasks']`, polling at `TASK_POLLING_INTERVAL_MS`, gated by `enabled: isVisible`
    - Implement React Query data fetching for completed tasks with key `['radar', 'completedTasks']`, polling at `TASK_POLLING_INTERVAL_MS`, gated by `enabled: isVisible`
    - Compute `completedAfterISO` from `ARCHIVE_WINDOW_DAYS` for server-side pre-filtering
    - Apply `sortWipTasks` from Spec 1 to WIP query results in `useMemo`
    - Apply client-side archive window filtering (`filterByArchiveWindow`) then `sortCompletedTasks` from Spec 1 to completed query results in `useMemo`
    - Export `filterByArchiveWindow` pure function for direct testing
    - Implement `viewThread(taskId)` — find task by id, use `useTabState` to switch to chat thread tab via `sessionId`
    - Implement `cancelTask(taskId)` with optimistic updates: `onMutate` calls `cancelQueries` + snapshot + remove from cache; `onError` restores snapshot; `onSettled` invalidates both caches (PE Finding #5)
    - Implement `resumeCompleted(taskId)` — create new chat thread seeded with completion context, navigate via `useTabState`
    - Return `{ wipTasks, completedTasks, isLoading, viewThread, cancelTask, resumeCompleted }`
    - Include module-level docstring per dev rules
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 9.1, 9.2, 9.5_


  - [ ]* 5.2 Write property test for WIP task filtering and sorting (Property 1)
    - **Property 1: WIP task filtering shows only active execution states and sorts correctly**
    - Create `desktop/src/pages/chat/components/radar/__tests__/wipTaskFilter.property.test.ts`
    - Use `fast-check` to generate random task arrays with all 5 statuses
    - Verify only wip/draft/blocked pass filter; count matches input subset
    - Verify `hasWaitingInput === false` on all returned tasks
    - Verify sort order: blocked → wip → draft, then startedAt desc, then id asc (PE Finding #6)
    - Verify idempotence: `sort(sort(x))` deep-equals `sort(x)`
    - Minimum 100 iterations
    - **Validates: Requirements 1.1, 1.4, 7.6**

  - [ ]* 5.3 Write property tests for archive window filtering (Properties 2, 7)
    - **Property 2: Archive window filtering excludes tasks older than 7 days**
    - **Property 7: Archive window constant controls filtering boundary**
    - Create `desktop/src/pages/chat/components/radar/__tests__/archiveWindow.property.test.ts`
    - Property 2: Generate random completed tasks with varying `completedAt` timestamps; verify only tasks within `ARCHIVE_WINDOW_DAYS` pass; boundary inclusion (exactly 7 days) and exclusion (7 days + 1ms); sort order completedAt desc → id asc; idempotence. Min 100 iterations.
    - Property 7: Generate random `ARCHIVE_WINDOW_DAYS` values (1–30) and random completed tasks; verify boundary shifts with constant; filter is parameterized, not hardcoded to 7. Min 100 iterations.
    - **Validates: Requirements 3.1, 3.3, 5.1, 5.2, 5.4**

  - [ ]* 5.4 Write property tests for task transitions, optimistic cancel, and polling visibility (Properties 3, 5, 6)
    - **Property 3: Task status changes produce correct zone placement (WIP to Completed transition)**
    - **Property 5: Optimistic cancel removes task from WIP and invalidates both caches**
    - **Property 6: Polling is gated by visibility — zero queries when hidden**
    - Create `desktop/src/pages/chat/components/radar/__tests__/taskTransitions.property.test.ts`
    - Property 3: Generate random WIP tasks, simulate transitions to completed/cancelled; verify completed→Completed zone, cancelled→neither zone, WIP and completed filters mutually exclusive. Min 100 iterations.
    - Property 5: Generate random WIP task arrays, pick random task to cancel; verify task removed from cache, all others preserved, length = original - 1, snapshot restore produces original array. Min 100 iterations.
    - Property 6: Generate random `isVisible` state sequences; verify isVisible=false → zero queries, transitions resume/stop polling immediately. Min 100 iterations.
    - **Validates: Requirements 2.4, 2.5, 8.3, 8.8, 8.9, 9.5, 9.6**

- [ ] 6. Checkpoint — Service layer, hook, and property tests
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [ ] 7. WIP task UI components
  - [ ] 7.1 Create `desktop/src/pages/chat/components/radar/WipTaskItem.tsx`
    - Accept `WipTaskItemProps`: `task: RadarWipTask`, `onViewThread: () => void`, `onCancel: () => void`
    - Render as `<li role="listitem" className="radar-wip-item">` with conditional `radar-wip-item--blocked` class
    - Display task title (truncated 1 line), status indicator (🔄 WIP, 📋 Draft, 🚫 Blocked), elapsed time from `startedAt`, progress hint from `description`
    - When `hasWaitingInput === true`: display ⏳ "Waiting" badge
    - Click on item body triggers `onViewThread`
    - Show `⋯` overflow button on hover with `aria-label="Actions for {task.title}"`
    - Overflow menu with "View Thread" and "Cancel" buttons
    - "Cancel" triggers inline confirmation: "Cancel this task?" with Confirm/Back buttons
    - Focusable via Tab key, action menu accessible via Enter or Space
    - Use `--color-*` CSS variables only
    - Include module-level docstring per dev rules
    - _Requirements: 1.2, 1.3, 1.8, 2.1, 2.2, 2.3, 2.4, 2.5, 10.1_

  - [ ] 7.2 Create `desktop/src/pages/chat/components/radar/WipTaskList.tsx`
    - Accept `WipTaskListProps`: `tasks: RadarWipTask[]`, `onViewThread: (taskId) => void`, `onCancel: (taskId) => void`
    - Render `<ul role="list">` containing one `WipTaskItem` per entry
    - Render nothing when `tasks.length === 0` (parent RadarZone handles empty state)
    - Pass per-item action callbacks to each `WipTaskItem`
    - Include module-level docstring per dev rules
    - _Requirements: 1.1, 1.5, 1.6, 1.7_

  - [ ]* 7.3 Write unit tests for WipTaskList and WipTaskItem
    - Create `desktop/src/pages/chat/components/radar/__tests__/WipTaskList.test.tsx`
    - Test: renders correct number of items
    - Test: status indicators (🔄, 📋, 🚫) display correctly per status
    - Test: ⏳ badge shown when `hasWaitingInput=true`
    - Test: overflow menu appears on hover with View Thread and Cancel
    - Test: Cancel inline confirmation flow
    - Test: click on item body triggers onViewThread
    - Test: Tab key focuses items, Enter/Space activates action menu
    - _Requirements: 1.2, 1.3, 1.8, 2.1, 2.2, 2.4_

- [ ] 8. Completed task UI components
  - [ ] 8.1 Create `desktop/src/pages/chat/components/radar/CompletedTaskItem.tsx`
    - Accept `CompletedTaskItemProps`: `task: RadarCompletedTask`, `onViewThread: () => void`, `onResume: () => void`
    - Render as `<li role="listitem" className="radar-completed-item">`
    - Display task title (truncated 1 line), relative completion timestamp (e.g., "2h ago", "Yesterday", "3d ago"), agent name from `agentId`, brief outcome summary from `description` (truncated 1 line)
    - Click on item body triggers `onViewThread`
    - Show `⋯` overflow button on hover with `aria-label="Actions for {task.title}"`
    - Overflow menu with "View Thread" and "Resume" buttons (no confirmation needed for Resume)
    - Focusable via Tab key, action menu accessible via Enter or Space
    - Use `--color-*` CSS variables only
    - Include module-level docstring per dev rules
    - _Requirements: 3.2, 3.8, 4.1, 4.2, 4.3, 4.4, 4.5, 10.2, 10.3_

  - [ ] 8.2 Create `desktop/src/pages/chat/components/radar/CompletedTaskList.tsx`
    - Accept `CompletedTaskListProps`: `tasks: RadarCompletedTask[]`, `onViewThread: (taskId) => void`, `onResume: (taskId) => void`
    - Render `<ul role="list">` containing one `CompletedTaskItem` per entry
    - Render nothing when `tasks.length === 0` (parent RadarZone handles empty state)
    - Pass per-item action callbacks to each `CompletedTaskItem`
    - Include module-level docstring per dev rules
    - _Requirements: 3.1, 3.5, 3.6, 3.7_

  - [ ]* 8.3 Write unit tests for CompletedTaskList and CompletedTaskItem
    - Create `desktop/src/pages/chat/components/radar/__tests__/CompletedTaskList.test.tsx`
    - Test: renders correct number of items
    - Test: relative completion timestamp displays correctly
    - Test: overflow menu appears on hover with View Thread and Resume
    - Test: click on item body triggers onViewThread
    - Test: Resume action does not require confirmation
    - Test: Tab key focuses items, Enter/Space activates action menu
    - _Requirements: 3.2, 3.8, 4.1, 4.2, 4.4_

- [ ] 9. Add CSS styles for WIP and completed task components
  - [ ] 9.1 Add WIP and completed task styles to `desktop/src/pages/chat/components/radar/SwarmRadar.css`
    - Define `.radar-wip-item` layout: title, status indicator, elapsed time, progress hint
    - Define `.radar-wip-item--blocked` for visual emphasis on blocked tasks
    - Define `.radar-wip-item-waiting` for ⏳ waiting badge
    - Define `.radar-wip-item-overflow` for hover-only `⋯` button and positioned menu
    - Define `.radar-completed-item` layout: title, timestamp, agent name, outcome summary
    - Define `.radar-completed-item-overflow` for hover-only `⋯` button and positioned menu
    - Define `.radar-confirm-inline` for inline confirmation prompt (shared by WipTaskItem)
    - Use only `--color-*` CSS variables — no hardcoded colors
    - Match font sizes, weights, spacing of existing radar components
    - _Requirements: 1.2, 1.3, 2.1, 3.2, 4.1_

- [ ] 10. Checkpoint — UI components and styles
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [ ] 11. Wire WIP and Completed components into SwarmRadar
  - [ ] 11.1 Integrate `useTaskZone` into `SwarmRadar.tsx`
    - Import and call `useTaskZone` hook with `workspaceId` and `isVisible` from sidebar state
    - Replace mock WIP task `<li>` elements in the In Progress zone with `<WipTaskList>` component
    - Replace mock completed task `<li>` elements in the Completed zone with `<CompletedTaskList>` component
    - Pass `useTaskZone` action handlers (`viewThread`, `cancelTask`, `resumeCompleted`) to list components
    - Update In Progress badge count to use `wipTasks.length` from the hook
    - Update Completed badge count to use `completedTasks.length` from the hook
    - _Requirements: 1.1, 1.5, 1.6, 3.1, 3.5, 8.1, 8.4_

  - [ ] 11.2 Add SSE-triggered cache invalidation in ChatPage
    - When ChatPage receives SSE events affecting task state (completion, status change), invalidate `['radar', 'wipTasks']` and `['radar', 'completedTasks']` React Query cache keys
    - This triggers immediate refresh rather than waiting for next 30s polling interval
    - _Requirements: 9.3, 9.6_

- [ ] 12. Final checkpoint — Full integration
  - Ensure all tests pass (`cd desktop && npm test -- --run` and `cd backend && pytest`).
  - Ensure no TypeScript compilation errors (`cd desktop && npx tsc --noEmit`).
  - Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Backend tasks (1.x) run first so the API is ready before frontend integration
- Property tests validate universal correctness properties from the design document
- Checkpoints ensure incremental validation at natural break points
- This spec builds on Spec 1 (types, sort utils, RadarZone, CSS), Spec 2 (radar.ts service, useTodoZone, SwarmRadar integration), and Spec 3 (hasWaitingInput derivation, activeSessionId wiring)
- `review_required` and `review_risk_level` are always false/null in initial release — population mechanism deferred to future spec
- `hasWaitingInput` is set to `false` at the service layer; actual value computed by Spec 3's `useWaitingInputZone` hook at the composition layer
- Optimistic cancel uses React Query's `onMutate`/`onError`/`onSettled` with `cancelQueries` to handle concurrent mutations (PE Finding #5)
- All sort functions use `id` as ultimate tiebreaker for deterministic ordering (PE Finding #6)
