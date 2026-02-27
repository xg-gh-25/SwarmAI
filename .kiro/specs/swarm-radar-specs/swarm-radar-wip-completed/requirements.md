# Requirements Document — Swarm Radar WIP & Completed Tasks (Sub-Spec 4 of 5)

## Introduction

This document defines the requirements for the **Swarm Radar WIP & Completed Tasks** — the fourth sub-spec of the Swarm Radar Redesign. It covers the WIP Tasks display (In Progress zone), Completed Tasks display (Completed zone), the 7-day archive window, backend task query support and schema extensions, the frontend task service layer and `useTaskZone` hook, polling-based real-time updates, and the optimistic update pattern for task actions.

This spec builds on the foundation established in Spec 1 (`swarm-radar-foundation`), the ToDo infrastructure from Spec 2 (`swarm-radar-todos`), and the Waiting Input derivation from Spec 3 (`swarm-radar-waiting-input`).

### Scope

- WipTaskList and WipTaskItem components for the In Progress zone
- CompletedTaskList and CompletedTaskItem components for the Completed zone
- Click-to-Chat actions: "View Thread" (WIP and Completed), "Cancel" (WIP), "Resume" (Completed)
- Backend task query support: status filtering, `completed_after` parameter, `review_required`/`review_risk_level` fields
- Frontend service layer additions to `radar.ts`: `fetchWipTasks`, `fetchCompletedTasks`, `cancelTask`
- Frontend `useTaskZone` hook with React Query polling (30s, gated by `isVisible`)
- 7-day archive window enforcement (client-side filtering + server-side `completed_after` support)
- `ARCHIVE_WINDOW_DAYS` constant
- Optimistic updates for task cancel action using React Query's `onMutate`/`onError`/`onSettled` pattern (PE Finding #5 fix)
- Polling intervals as configurable constants
- SSE-triggered cache invalidation

### Out of Scope (Handled by Other Sub-Specs)

- SwarmRadar shell, RadarZone, shared types, sorting utilities, mock data, CSS, empty states (Spec 1 — done)
- ToDo inbox, quick-add, lifecycle actions, radar.ts service layer (ToDo functions), old component deletion (Spec 2 — done)
- Waiting input derivation, hasWaitingInput, activeSessionId, WaitingInputList component (Spec 3 — done)
- Autonomous jobs API and zone (Spec 5)

### Parent Spec

The overall Swarm Radar Redesign spec is at `.kiro/specs/swarm-radar-redesign/`. This sub-spec extracts and adapts Requirements 9, 10, 13 (View Thread and Resume actions), 17, 18 (task functions), 19 (useTaskZone), 21, and 22 from that parent.

### Dependencies

- **Spec 1 (`swarm-radar-foundation`)**: SwarmRadar shell, RadarZone component, shared types (`RadarWipTask`, `RadarCompletedTask`, `RadarZoneId`), sorting utilities (`sortWipTasks`, `sortCompletedTasks`), mock data (`getMockWipTasks()`, `getMockCompletedTasks()`), CSS styles, empty state support.
- **Spec 2 (`swarm-radar-todos`)**: `radar.ts` service layer (this spec adds task-related functions to it), `useTodoZone` hook pattern (this spec follows the same hook pattern for `useTaskZone`), old component deletion (SwarmRadar is already wired into ChatPage).
- **Spec 3 (`swarm-radar-waiting-input`)**: `hasWaitingInput` derivation (this spec's WipTaskItem component displays the `hasWaitingInput` indicator), `activeSessionId` prop (already wired).

### Design Principles Alignment

- **Visible Planning Builds Trust** — WIP Tasks show execution state, agent name, elapsed time transparently
- **Chat is the Command Surface** — "View Thread" and "Resume" navigate to chat threads for deep work
- **Progressive Disclosure** — Hover-only action menus on task items, archive window auto-hides old completions
- **Glanceable Awareness** — Status indicators, elapsed time, completion timestamps provide instant context
- **Human Review Gates** — "Cancel" action on WIP tasks gives users control over agent execution

### PE Review Findings Addressed

1. **Finding #5 (Error Handling, Medium)**: The optimistic update rollback strategy uses React Query's built-in `onMutate`/`onError`/`onSettled` pattern with `queryClient.cancelQueries` before mutation. This handles concurrent mutations correctly by canceling in-flight queries and using the `previousData` from `onMutate` context. Documented in Requirement 7 AC 6.

2. **Finding #6 (Determinism, Medium)**: All sort functions use `id` as the ultimate tiebreaker (defined in Spec 1 sorting utilities). The property tests in this spec validate the WIP task sort and completed task sort with the `id` tiebreaker.


## Glossary

- **Swarm_Radar**: The unified attention & action control panel rendered as the right sidebar in the ChatPage. Defined in Spec 1.
- **In_Progress_Zone**: The Radar_Zone containing WIP Tasks currently being executed. Indicated by 🟡. Defined in Spec 1.
- **Completed_Zone**: The Radar_Zone containing recently completed tasks within the archive window. Indicated by 🟢. Defined in Spec 1.
- **WIP_Task**: A Task entity in an active execution state (`wip`, `draft`, `blocked`). Displayed in the In_Progress_Zone. These are the actual frontend `TaskStatus` values — there is no `running`, `pending`, `waiting_for_input`, or `paused` status.
- **RadarWipTask**: Frontend TypeScript type representing a WIP task. Defined in Spec 1 at `desktop/src/types/radar.ts`. Uses `Pick<Task, ...>` plus `hasWaitingInput: boolean`. The `hasWaitingInput` flag is derived by Spec 3's `useWaitingInputZone` hook.
- **Completed_Task**: A Task entity that has finished execution. Displayed temporarily in the Completed_Zone before archival.
- **RadarCompletedTask**: Frontend TypeScript type representing a completed task in the archive window. Defined in Spec 1 at `desktop/src/types/radar.ts`. Includes `reviewRequired` (always `false`) and `reviewRiskLevel` (always `null`) in the initial release.
- **Archive_Window**: The time period (default 7 days) after which completed tasks are removed from the Completed_Zone. Defined as the `ARCHIVE_WINDOW_DAYS` constant.
- **ARCHIVE_WINDOW_DAYS**: A configurable constant (default: 7) defining the number of days completed tasks remain visible in the Completed_Zone.
- **TaskStatus**: The frontend task status type: `'draft' | 'wip' | 'blocked' | 'completed' | 'cancelled'`. There is no `running`, `pending`, `waiting_for_input`, or `paused` status.
- **Click_Action**: A user interaction model where Radar items are acted upon via click-based buttons and menus rather than drag-and-drop.
- **Optimistic_Update**: A UI pattern where lifecycle actions update the React Query cache immediately, then revert on API failure. Uses React Query's `onMutate`/`onError`/`onSettled` pattern with `queryClient.cancelQueries` before mutation (PE Finding #5).
- **ChatPage**: The main orchestrator component at `desktop/src/pages/ChatPage.tsx`.
- **Total_Order_Tiebreaker**: All sort functions use `id` (string comparison) as the ultimate tiebreaker after all other sort keys to guarantee deterministic ordering (PE Finding #6). Sort utilities are defined in Spec 1.
- **Polling_Interval**: The configurable interval at which React Query refetches data. Tasks use 30 seconds. Gated by `enabled: isVisible`.
- **SSE_Cache_Invalidation**: When ChatPage receives SSE events that affect Radar state (e.g., task completion), the relevant React Query cache is invalidated to trigger an immediate refresh rather than waiting for the next polling interval.
- **useTaskZone**: The per-zone React hook managing WIP and completed task data, actions, and polling. Composed into the main `useSwarmRadar` hook.

## Requirements

### Requirement 1: WIP Tasks — In Progress Zone Display

**User Story:** As a knowledge worker, I want to see all currently executing tasks in the In Progress zone, so that I know what the AI is working on at any moment.

#### Acceptance Criteria

1. THE In_Progress_Zone SHALL display all WIP_Tasks with active execution states: `wip`, `draft`, or `blocked` (the actual frontend `TaskStatus` values).
2. EACH WIP_Task item SHALL display: task title, agent name, execution status indicator (🔄 WIP (active), 📋 Draft (queued), 🚫 Blocked), elapsed time since start (computed from `startedAt`), and a progress hint (if available from the `description` field).
3. WHEN a WIP_Task has `hasWaitingInput` equal to `true` (derived by Spec 3), THE WipTaskItem SHALL display a visual indicator (e.g., ⏳ or a "Waiting" badge) showing that the agent is blocked waiting for user input.
4. THE WIP_Task list SHALL sort items using the `sortWipTasks` function from Spec 1: `blocked` first (needs attention), then `wip` (active), then `draft` (queued), then by start time (most recent first), then by `id` ascending as the ultimate tiebreaker (PE Finding #6).
5. THE WIP_Task list SHALL fetch data from the existing `GET /api/tasks` backend endpoint, filtered by active statuses (`status=wip,draft,blocked`).
6. THE WipTaskList component SHALL be implemented at `desktop/src/pages/chat/components/radar/WipTaskList.tsx`.
7. THE WipTaskItem component SHALL be implemented at `desktop/src/pages/chat/components/radar/WipTaskItem.tsx`.
8. EACH WipTaskItem SHALL be focusable via Tab key navigation, with the action menu accessible via Enter or Space.

### Requirement 2: WIP Task Actions — Click-Based

**User Story:** As a knowledge worker, I want to view the thread or cancel a WIP task directly from the Radar, so that I can monitor or control AI execution without navigating away.

#### Acceptance Criteria

1. EACH WIP_Task item SHALL display a compact action menu (accessible via a `⋯` overflow button shown on hover) with the following Click_Actions: "View Thread" and "Cancel".
2. WHEN the user clicks on a WIP_Task item (outside the action menu), THE System SHALL navigate to or open the associated chat thread for that task (same behavior as "View Thread").
3. WHEN the user clicks "View Thread" on a WIP_Task, THE System SHALL switch to the associated chat thread tab using the existing tab management system (`useTabState` hook).
4. WHEN the user clicks "Cancel" on a WIP_Task, THE System SHALL display a brief inline confirmation, then cancel the task via the backend API, and remove the task from the In_Progress_Zone.
5. THE "Cancel" action SHALL use Optimistic_Updates: update the React Query cache immediately via `onMutate`, revert on API failure via `onError`, and invalidate the cache on completion via `onSettled` (PE Finding #5).

### Requirement 3: Completed Tasks — Recently Completed Zone Display

**User Story:** As a knowledge worker, I want to see recently completed tasks in a lightweight closure zone, so that I can review outcomes and track what has been accomplished.

#### Acceptance Criteria

1. THE Completed_Zone SHALL display all Completed_Tasks that finished within the Archive_Window (default 7 days).
2. EACH Completed_Task item SHALL display: task title, completion timestamp (relative, e.g., "2h ago", "Yesterday"), agent name, and a brief outcome summary (truncated to 1 line from the `description` field).
3. THE Completed_Zone SHALL sort items using the `sortCompletedTasks` function from Spec 1: `completedAt` descending (most recent first), then by `id` ascending as the ultimate tiebreaker (PE Finding #6).
4. THE Completed_Zone SHALL fetch data from the existing `GET /api/tasks` endpoint, filtered by `completed` status and `completed_after` within the Archive_Window.
5. THE Completed_Zone header SHALL display the count of recently completed tasks as a Zone_Badge with green tint (defined in Spec 1).
6. THE CompletedTaskList component SHALL be implemented at `desktop/src/pages/chat/components/radar/CompletedTaskList.tsx`.
7. THE CompletedTaskItem component SHALL be implemented at `desktop/src/pages/chat/components/radar/CompletedTaskItem.tsx`.
8. EACH CompletedTaskItem SHALL be focusable via Tab key navigation, with the action menu accessible via Enter or Space.

### Requirement 4: Completed Task Actions — Click-Based

**User Story:** As a knowledge worker, I want to view the execution history or resume a completed task from the Radar, so that I can review outcomes and continue work.

#### Acceptance Criteria

1. EACH Completed_Task item SHALL display a compact action menu (accessible via a `⋯` overflow button shown on hover) with the following Click_Actions: "View Thread" and "Resume".
2. WHEN the user clicks on a Completed_Task item (outside the action menu), THE System SHALL navigate to the associated chat thread to review the full execution history (same behavior as "View Thread").
3. WHEN the user clicks "View Thread" on a Completed_Task, THE System SHALL switch to the associated chat thread tab using the existing tab management system (`useTabState` hook).
4. WHEN the user clicks "Resume" on a Completed_Task, THE System SHALL create a new chat thread seeded with the completion context from the original thread, and navigate the user to the new thread tab.
5. THE "Resume" action SHALL use the existing tab management system (`useTabState` hook) to open the new chat thread tab.

### Requirement 5: Completed Tasks — Archive Window Enforcement

**User Story:** As a knowledge worker, I want completed tasks to automatically disappear from the Radar after 7 days, so that the completed zone stays clean and focused on recent work.

#### Acceptance Criteria

1. THE Completed_Zone SHALL display only tasks where `completedAt` is within the last 7 days (Archive_Window).
2. THE Frontend SHALL filter completed tasks client-side based on the Archive_Window, using the `completedAt` timestamp from the Task response. Tasks exactly at the 7-day boundary SHALL be included. Tasks one millisecond past the boundary SHALL be excluded.
3. THE Backend SHALL support an optional `completed_after` query parameter on the `GET /api/tasks` endpoint to allow server-side pre-filtering by completion date, reducing payload size.
4. THE Archive_Window value (7 days) SHALL be defined as a constant `ARCHIVE_WINDOW_DAYS` in the radar configuration module (e.g., `desktop/src/pages/chat/components/radar/radarConstants.ts`), allowing future configurability.
5. WHEN a Completed_Task exceeds the 7-day Archive_Window, THE item SHALL be automatically removed from the Completed_Zone display on the next client-side filter pass. THE task remains in the database for full traceability.

### Requirement 6: Backend — Task Query Support and Schema Extensions

**User Story:** As a developer, I want backend query support for filtering tasks by actual status values and completion date, so that the Radar can populate the WIP and Completed zones.

#### Acceptance Criteria

1. THE Backend SHALL support filtering tasks by actual `TaskStatus` values via the existing `GET /api/tasks` endpoint. The actual frontend statuses are: `draft`, `wip`, `blocked`, `completed`, `cancelled`. There is NO `waiting_for_input` status — waiting state is detected via SSE events passed as props from ChatPage.
2. THE Backend SHALL add a `review_required` boolean field (default `False`) to the Task response model. This field is always `false` in the initial release — the population mechanism for risk-assessment is deferred to a future spec.
3. THE Backend SHALL add a `review_risk_level` optional field (enum: "low", "medium", "high", "critical") to the Task response model. This field is always `null` in the initial release — deferred to a future spec.
4. THE Backend SHALL support filtering tasks by `completed_after=<ISO8601>` via the existing `GET /api/tasks` endpoint, for archive window filtering. THE filter SHALL return only tasks with `completed_at` after the specified date.
5. THE Backend SHALL use snake_case field names for all new fields (`review_required`, `review_risk_level`).
6. THE Frontend SHALL update the Task TypeScript interface and `toCamelCase()` conversion in `desktop/src/services/tasks.ts` to include the new fields: `reviewRequired` (boolean) and `reviewRiskLevel` (string | null).
7. THE `GET /api/tasks` endpoint SHALL use comma-separated query parameter format with OR semantics within the same parameter and AND semantics across different parameter types. Example: `GET /api/tasks?status=wip,draft,blocked&workspace_id=abc` means `(status=wip OR status=draft OR status=blocked) AND workspace_id=abc`.

### Requirement 7: Frontend — Swarm Radar Service Layer (Task Functions)

**User Story:** As a developer, I want task-related service functions in the radar service module, so that WIP and completed task API calls are centralized alongside the existing ToDo functions.

#### Acceptance Criteria

1. THE Frontend SHALL add task-related functions to the existing `desktop/src/services/radar.ts` service module (created in Spec 2).
2. THE radar service SHALL include: `fetchWipTasks(workspaceId?: string): Promise<RadarWipTask[]>` — fetches tasks with `status=wip,draft,blocked`.
3. THE radar service SHALL include: `fetchCompletedTasks(workspaceId?: string, completedAfter?: string): Promise<RadarCompletedTask[]>` — fetches tasks with `status=completed` and optional `completed_after` filter.
4. THE radar service SHALL include: `cancelTask(taskId: string): Promise<void>` — cancels a WIP task via the backend API.
5. THE radar service SHALL implement `toCamelCase()` conversion for all backend task responses, mapping snake_case fields (e.g., `workspace_id`, `agent_id`, `session_id`, `started_at`, `completed_at`, `review_required`, `review_risk_level`, `source_todo_id`) to camelCase.
6. THE `fetchWipTasks` function SHALL set `hasWaitingInput` to `false` on all returned tasks. THE actual `hasWaitingInput` value is computed by Spec 3's `useWaitingInputZone` hook at the composition layer, not at the service layer.
7. THE radar service SHALL use the existing HTTP client pattern consistent with `desktop/src/services/tasks.ts`.

### Requirement 8: Frontend — useTaskZone State Management Hook

**User Story:** As a developer, I want a dedicated React hook for task zone state management, so that WIP and completed task data fetching, actions, and optimistic updates are cleanly encapsulated.

#### Acceptance Criteria

1. THE Frontend SHALL implement a `useTaskZone` hook at `desktop/src/pages/chat/components/radar/hooks/useTaskZone.ts`.
2. THE `useTaskZone` hook SHALL accept parameters: `workspaceId: string` and `isVisible: boolean`.
3. THE `useTaskZone` hook SHALL use React Query for data fetching with a 30-second polling interval for both WIP and completed tasks, gated by `enabled: isVisible` where `isVisible` is derived from `rightSidebars.isActive('todoRadar')`.
4. THE `useTaskZone` hook SHALL return: `wipTasks: RadarWipTask[]` (sorted, active statuses only), `completedTasks: RadarCompletedTask[]` (sorted, within archive window), `isLoading: boolean`, and action handlers: `viewThread(taskId: string)`, `cancelTask(taskId: string)`, `resumeCompleted(taskId: string)`.
5. THE `useTaskZone` hook SHALL apply the `sortWipTasks` function from Spec 1 to the WIP task results.
6. THE `useTaskZone` hook SHALL apply the `sortCompletedTasks` function from Spec 1 to the completed task results, after filtering by the Archive_Window client-side.
7. THE `useTaskZone` hook SHALL use React Query cache keys: `['radar', 'wipTasks']` for WIP tasks and `['radar', 'completedTasks']` for completed tasks.
8. WHEN `isVisible` is false, THE `useTaskZone` hook SHALL execute zero polling queries.
9. THE `cancelTask` action SHALL implement Optimistic_Updates using React Query's built-in `onMutate`/`onError`/`onSettled` pattern:
   - `onMutate`: Call `queryClient.cancelQueries(['radar', 'wipTasks'])` to cancel in-flight queries, snapshot the current cache via `queryClient.getQueryData`, then optimistically remove the cancelled task from the cache.
   - `onError`: Restore the `previousData` snapshot from the `onMutate` context to revert the optimistic update.
   - `onSettled`: Call `queryClient.invalidateQueries(['radar', 'wipTasks'])` and `queryClient.invalidateQueries(['radar', 'completedTasks'])` to fetch fresh data.
   - This pattern handles concurrent mutations correctly because `cancelQueries` prevents stale in-flight responses from overwriting the optimistic state, and each mutation's `onMutate` captures its own independent snapshot (PE Finding #5).
10. THE `viewThread` action SHALL use the existing tab management system (`useTabState` hook) to switch to the associated chat thread tab.
11. THE `resumeCompleted` action SHALL create a new chat thread seeded with the completion context from the original thread, and navigate to the new thread tab using `useTabState`.

### Requirement 9: Real-Time Updates via Polling and SSE Cache Invalidation

**User Story:** As a knowledge worker, I want the Radar to reflect changes in near real-time, so that task status changes and completions appear without manual refresh.

#### Acceptance Criteria

1. THE `useTaskZone` hook SHALL use React Query polling to refresh WIP and completed task data at 30-second intervals.
2. WHEN a user performs a task action (e.g., cancel task), THE `useTaskZone` hook SHALL optimistically update the UI and invalidate the relevant React Query cache via the `onSettled` callback.
3. WHEN the ChatPage receives SSE events that affect task state (e.g., task completion event, task status change), THE Swarm_Radar SHALL invalidate the relevant React Query cache keys (`['radar', 'wipTasks']` and `['radar', 'completedTasks']`) to trigger an immediate refresh rather than waiting for the next polling interval.
4. THE polling intervals SHALL be defined as constants in the radar configuration module (e.g., `TASK_POLLING_INTERVAL_MS = 30000` in `radarConstants.ts`), allowing future tuning.
5. ALL React Query polling hooks within `useTaskZone` SHALL be gated by `enabled: isVisible`. WHEN the sidebar is hidden, zero polling queries SHALL execute.
6. WHEN a WIP_Task transitions to `completed` status (detected via polling or SSE-triggered cache invalidation), THE item SHALL move from the In_Progress_Zone to the Completed_Zone on the next data refresh.

### Requirement 10: Click-to-Chat Action Model (Task Actions)

**User Story:** As a knowledge worker, I want to act on WIP and completed tasks via click actions that navigate to chat threads, so that all deep work happens in the conversational command surface.

#### Acceptance Criteria

1. WHEN the user clicks "View Thread" on a WIP_Task, THE System SHALL switch to the associated chat thread tab using the `sessionId` to identify the correct thread.
2. WHEN the user clicks "View Thread" on a Completed_Task, THE System SHALL switch to the associated chat thread tab using the `sessionId` to identify the correct thread.
3. WHEN the user clicks "Resume" on a Completed_Task, THE System SHALL create a new chat thread seeded with the completion context from the original thread, and navigate the user to the new thread tab.
4. ALL click actions SHALL use the existing tab management system (`useTabState` hook) to open or switch to the appropriate chat thread tab.
5. THE click-to-chat model SHALL be the primary interaction pattern for task actions. Drag-and-drop is not supported.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: WIP task filtering shows only active execution states and sorts correctly

*For any* set of tasks with mixed statuses (`draft`, `wip`, `blocked`, `completed`, `cancelled`), the WIP filter function SHALL return only tasks with status `wip`, `draft`, or `blocked`. No task with status `completed` or `cancelled` SHALL appear in the result. The count of returned tasks SHALL equal the count of `wip` + `draft` + `blocked` tasks in the input. The `sortWipTasks` function SHALL order them: `blocked` first (needs attention), then `wip` (active), then `draft` (queued), with ties broken by start time (most recent first), then by `id` ascending as the ultimate tiebreaker (PE Finding #6). Sorting the same input twice SHALL produce identical output (idempotence).

**Validates:** Requirement 1.1, 1.4
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/wipTaskFilter.property.test.ts`

### Property 2: Archive window filtering excludes tasks older than 7 days

*For any* set of completed tasks with varying `completedAt` timestamps, the archive window filter function SHALL return only tasks where `completedAt` is within the last 7 days (`ARCHIVE_WINDOW_DAYS` constant). Tasks exactly at the 7-day boundary SHALL be included. Tasks one millisecond past the boundary SHALL be excluded. The result SHALL be sorted by `completedAt` descending (most recent first), then by `id` ascending as the ultimate tiebreaker (PE Finding #6). The count of returned tasks SHALL equal the count of tasks within the archive window in the input. Sorting the same input twice SHALL produce identical output (idempotence).

**Validates:** Requirement 3.1, 3.3, 5.1, 5.2
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/archiveWindow.property.test.ts`

### Property 3: Task status changes produce correct zone placement (WIP to Completed transition)

*For any* WIP task that transitions from an active status (`wip`, `draft`, `blocked`) to `completed` status, the task SHALL no longer appear in the WIP filter result and SHALL appear in the completed filter result (assuming `completedAt` is within the archive window). *For any* WIP task that transitions to `cancelled` status, the task SHALL no longer appear in the WIP filter result and SHALL NOT appear in the completed filter result. The WIP filter and completed filter SHALL be mutually exclusive — no task SHALL appear in both results simultaneously.

**Validates:** Requirement 9.6, 2.4
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/taskTransitions.property.test.ts`

### Property 4: Backend task filtering returns only matching records

*For any* set of tasks in the database with mixed statuses and completion dates, querying with `status=wip,draft,blocked` SHALL return only tasks with those statuses. Querying with `status=completed&completed_after=<date>` SHALL return only tasks with `completed` status AND `completed_at` after the specified date. The comma-separated status filter SHALL use OR semantics (a task matches if its status is any of the listed values). The `completed_after` filter SHALL use AND semantics with the status filter. No task outside the filter criteria SHALL appear in the result.

**Validates:** Requirement 6.1, 6.4, 6.7
**Test type:** Property-based (pytest + hypothesis), min 100 iterations
**Test file:** `backend/tests/test_task_filtering.py`

### Property 5: Optimistic cancel removes task from WIP and invalidates both caches

*For any* WIP task that is cancelled via the `cancelTask` action, the optimistic update SHALL immediately remove the task from the `['radar', 'wipTasks']` cache. On successful API response, both `['radar', 'wipTasks']` and `['radar', 'completedTasks']` caches SHALL be invalidated. On API failure, the task SHALL be restored to the `['radar', 'wipTasks']` cache from the `previousData` snapshot captured in `onMutate`. The `queryClient.cancelQueries` call in `onMutate` SHALL prevent stale in-flight responses from overwriting the optimistic state (PE Finding #5).

**Validates:** Requirement 2.4, 2.5, 8.9
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/taskTransitions.property.test.ts`

### Property 6: Polling is gated by visibility — zero queries when hidden

*For any* state where `isVisible` is `false`, the `useTaskZone` hook SHALL execute zero polling queries for both `['radar', 'wipTasks']` and `['radar', 'completedTasks']`. When `isVisible` transitions from `false` to `true`, polling SHALL resume at the configured interval. When `isVisible` transitions from `true` to `false`, polling SHALL stop immediately.

**Validates:** Requirement 8.3, 8.8, 9.5
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/taskTransitions.property.test.ts`

### Property 7: Archive window constant controls filtering boundary

*For any* value of `ARCHIVE_WINDOW_DAYS` (positive integer), the archive window filter SHALL include only completed tasks where `completedAt` is within the last `ARCHIVE_WINDOW_DAYS` days. Changing `ARCHIVE_WINDOW_DAYS` from 7 to N SHALL change the filtering boundary accordingly. The filter SHALL be parameterized by this constant, not hardcoded to 7.

**Validates:** Requirement 5.4
**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/archiveWindow.property.test.ts`
