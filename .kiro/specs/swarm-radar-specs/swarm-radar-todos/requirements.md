# Requirements Document — Swarm Radar ToDos (Sub-Spec 2 of 5)

## Introduction

This document defines the requirements for the **Swarm Radar ToDos** — the second sub-spec of the Swarm Radar Redesign. It covers the ToDo unified inbox, quick-add input, lifecycle actions (start, edit, complete, cancel, delete), the frontend service layer and state management for ToDos, backend schema extensions, and deletion of the old mock component.

This spec builds on the foundation established in Spec 1 (`swarm-radar-foundation`), which provides the SwarmRadar shell, RadarZone component, shared TypeScript types (`RadarTodo`, `RadarZoneId`), sorting utilities (`sortTodos`), indicator functions, mock data module, and CSS styles.

### Scope

- ToDo unified inbox display and sorting within the Needs Attention zone
- Quick-Add ToDo inline input
- ToDo lifecycle actions (Start, Edit, Complete, Cancel, Delete) via click-based overflow menu
- Click-to-Chat action model for ToDo "Start" action (creates thread with ToDo context)
- Backend ToDo schema extensions: `source_type` enum additions (`chat`, `ai_detected`), `linked_context` field, SQLite migration
- Frontend service layer (`radar.ts`) for ToDo-related API calls with `toCamelCase`/`toSnakeCase` conversion
- Frontend state management (`useTodoZone` hook) with React Query polling and optimistic updates
- Deletion of old `TodoRadarSidebar.tsx` and import replacement in `ChatPage.tsx`

### Out of Scope (Handled by Later Sub-Specs)

- Waiting input / pending question handling from SSE props (Spec 3)
- WIP tasks, completed tasks, archive window logic (Spec 4)
- Autonomous jobs API and zone (Spec 5)

### Parent Spec

The overall Swarm Radar Redesign spec is at `.kiro/specs/swarm-radar-redesign/`. This sub-spec extracts and adapts Requirements 4, 5, 6, 13 (ToDo parts), 15, 18 (ToDo parts), 19 (useTodoZone), and 23 from that parent.

### Dependencies

- **Spec 1 (`swarm-radar-foundation`)**: SwarmRadar shell, RadarZone component, shared types (`RadarTodo`, `RadarZoneId`), sorting utilities (`sortTodos`), indicator functions (`getPriorityIndicator`, `getTimelineIndicator`, `getSourceTypeLabel`), mock data module (`getMockTodos()`), CSS styles, empty state support.

### Design Principles Alignment

- **Signals First: Separate Intent From Execution** — ToDos are structured intent signals, separate from Tasks
- **Chat is the Command Surface** — "Start" on a ToDo creates a chat thread for execution
- **Progressive Disclosure** — Hover-only action menus, inline confirmation for destructive actions
- **Glanceable Awareness** — Priority indicators, source type labels, sorted inbox

### PE Review Findings Addressed

1. **Finding #4 (API Design, Medium)**: The "Start" action on a ToDo calls `convertTodoToTask(todoId, agentId)`. The `agentId` parameter is resolved by looking up the workspace's default agent before calling the API. This is documented in Requirement 2 AC 2.
2. **Finding #6 (Determinism, Medium)**: The ToDo sort uses `id` (string comparison) as the ultimate tiebreaker after `createdAt` to guarantee a total order. The sort utility is defined in Spec 1, but the property test validating ToDo-specific sort correctness is in this spec.
3. **Finding #7 (Schema Evolution, Medium)**: The SQLite table-rebuild migration for `source_type` CHECK constraint is wrapped in a single transaction (`BEGIN IMMEDIATE ... COMMIT`) for crash safety, with a version check for idempotency. Documented in Requirement 5 AC 6.

## Glossary

- **Swarm_Radar**: The unified attention & action control panel rendered as the right sidebar in the ChatPage. Defined in Spec 1.
- **Needs_Attention_Zone**: The top Radar_Zone containing ToDos and Waiting Input / ToReview items. Indicated by 🔴. Defined in Spec 1.
- **ToDo**: A structured intent signal representing incoming work. DB-canonical entity with fields: id, workspace_id, title, description, source, source_type, status, priority, due_date, linked_context, task_id, created_at, updated_at. Existing schema at `backend/schemas/todo.py`.
- **ToDo_Source_Type**: The origin of a ToDo: manual, email, slack, meeting, integration, chat, ai_detected.
- **ToDo_Priority**: Priority level of a ToDo: high, medium, low, none.
- **ToDo_Status**: Lifecycle state of a ToDo: pending, overdue, in_discussion, handled, cancelled, deleted.
- **Quick_Add**: A simple inline input within the Needs_Attention_Zone for creating ToDos manually without leaving the Radar.
- **Click_Action**: A user interaction model where Radar items are acted upon via click-based buttons and menus rather than drag-and-drop.
- **Priority_Indicator**: Visual emoji/icon indicators for ToDo priority: 🔴 High, 🟡 Medium, 🔵 Low, ⏰ Due Today, ⚠️ Overdue. Defined in Spec 1.
- **RadarTodo**: Frontend TypeScript type representing a ToDo item in the Radar. Defined in Spec 1 at `desktop/src/types/radar.ts`.
- **Default_Agent**: The workspace's default agent, used as the `agentId` when converting a ToDo to a Task via the "Start" action (PE Finding #4).
- **Optimistic_Update**: A UI pattern where lifecycle actions update the React Query cache immediately, then revert on API failure. Uses React Query's `onMutate`/`onError`/`onSettled` pattern.
- **ChatPage**: The main orchestrator component at `desktop/src/pages/ChatPage.tsx`.
- **TodoRadarSidebar**: The old mock component at `desktop/src/pages/chat/components/TodoRadarSidebar.tsx` to be deleted and replaced by SwarmRadar.
- **Total_Order_Tiebreaker**: All sort functions use `id` (string comparison) as the ultimate tiebreaker after all other sort keys to guarantee deterministic ordering (PE Finding #6).

## Requirements

### Requirement 1: ToDo Unified Inbox — Display and Sorting

**User Story:** As a knowledge worker, I want to see all my ToDos in a unified inbox within the Needs Attention zone, so that I can triage and prioritize incoming work signals.

#### Acceptance Criteria

1. THE Needs_Attention_Zone SHALL display a "ToDos" sub-section listing all active ToDo items (status: pending or overdue).
2. EACH ToDo item SHALL display: title, source type icon/label (using `getSourceTypeLabel` from Spec 1), Priority_Indicator (using `getPriorityIndicator` from Spec 1), and due date (if set).
3. THE ToDo list SHALL sort items using the `sortTodos` function from Spec 1: overdue items first, then by priority (high → medium → low → none), then by due date (earliest first, null due dates last), then by creation date (newest first), then by `id` ascending as the ultimate tiebreaker (PE Finding #6).
4. WHEN a ToDo has status `overdue`, THE ToDo item SHALL display the ⚠️ Overdue indicator prominently.
5. WHEN a ToDo has a due date matching today, THE ToDo item SHALL display the ⏰ Due Today indicator.
6. THE ToDo list SHALL display the source type for each item using a compact label: 📧 Email, 💬 Slack, 📅 Meeting, 🔗 Integration, 💭 Chat, 🤖 AI-detected, ✏️ Manual.
7. THE ToDo list SHALL fetch data from the existing `GET /api/todos` backend endpoint (DB-canonical, query via API).
8. THE TodoList component SHALL be implemented at `desktop/src/pages/chat/components/radar/TodoList.tsx` and accept a `todos: RadarTodo[]` prop plus action handler props.
9. THE TodoItem component SHALL be implemented at `desktop/src/pages/chat/components/radar/TodoItem.tsx` and render a single ToDo with its indicators and action menu.

### Requirement 2: ToDo Lifecycle Actions — Click-Based

**User Story:** As a knowledge worker, I want to start, edit, complete, cancel, or delete ToDos directly from the Radar via click actions, so that I can manage my work without navigating away from chat.

#### Acceptance Criteria

1. EACH ToDo item SHALL display a compact action menu (accessible via a `⋯` overflow button shown on hover) with the following Click_Actions: Start, Edit, Complete, Cancel, Delete.
2. WHEN the user clicks "Start" on a ToDo, THE System SHALL resolve the workspace's Default_Agent, then convert the ToDo to a Task using the existing `convert_to_task` API endpoint with the resolved `agentId` (PE Finding #4), update the ToDo status to `handled`, and navigate the user to the new chat thread with the ToDo context pre-loaded.
3. WHEN the user clicks "Edit" on a ToDo, THE System SHALL display an inline edit form allowing modification of title, description, priority, and due date.
4. WHEN the user clicks "Complete" on a ToDo, THE System SHALL update the ToDo status to `handled` without creating a Task (resolved without execution).
5. WHEN the user clicks "Cancel" on a ToDo, THE System SHALL update the ToDo status to `cancelled` and remove the ToDo from the active list.
6. WHEN the user clicks "Delete" on a ToDo, THE System SHALL update the ToDo status to `deleted` and remove the ToDo from the active list. THE deletion SHALL be traceable in history.
7. THE action menu SHALL use minimal icons shown only on hover, consistent with the visual design principles from Spec 1.
8. WHEN a destructive action (Cancel, Delete) is selected, THE System SHALL display a brief inline confirmation before executing.
9. ALL lifecycle actions SHALL use Optimistic_Updates: update the React Query cache immediately via `onMutate`, revert on API failure via `onError`, and invalidate the cache on completion via `onSettled`.
10. THE "Start" action SHALL use the existing tab management system (`useTabState` hook) to open or switch to the new chat thread tab after conversion.

### Requirement 3: Quick-Add ToDo

**User Story:** As a knowledge worker, I want to quickly create a new ToDo directly from the Radar without opening a modal, so that I can capture work signals with minimal friction.

#### Acceptance Criteria

1. THE Needs_Attention_Zone SHALL display a Quick_Add input at the top of the ToDo sub-section.
2. THE Quick_Add input SHALL be a single-line text field with a placeholder "Add a ToDo..." and a submit button (or Enter key).
3. WHEN the user submits a Quick_Add input, THE System SHALL create a new ToDo via the existing `POST /api/todos` endpoint with: the entered text as title, source_type as `manual`, priority as `none`, and status as `pending`.
4. WHEN a Quick_Add ToDo is created successfully, THE new ToDo SHALL appear at the appropriate position in the sorted ToDo list.
5. THE Quick_Add input SHALL clear after successful submission.
6. IF the Quick_Add submission fails, THEN THE System SHALL display a brief inline error message below the input field. THE input SHALL retain the entered text for retry. THE error SHALL auto-dismiss after 5 seconds.
7. THE QuickAddTodo component SHALL be implemented at `desktop/src/pages/chat/components/radar/QuickAddTodo.tsx`.
8. THE Quick_Add input SHALL have an accessible label: `aria-label="Add a new ToDo"`.

### Requirement 4: Click-to-Chat Action Model (ToDo Actions)

**User Story:** As a knowledge worker, I want the "Start" action on a ToDo to create a chat thread with the ToDo context, so that deep work happens in the conversational command surface.

#### Acceptance Criteria

1. WHEN the user clicks "Start" on a ToDo, THE System SHALL create a new chat thread (or reuse an existing one) with the ToDo context pre-loaded, and navigate the user to that thread.
2. THE "Start" action SHALL use the existing tab management system (`useTabState` hook) to open or switch to the appropriate chat thread tab.
3. THE click-to-chat model SHALL be the primary interaction pattern for ToDo actions. Drag-and-drop is not supported.
4. WHEN the "Start" action creates a new Task, THE System SHALL pass the ToDo's title, description, and linked_context as initial context for the chat thread.

### Requirement 5: Backend — ToDo Schema Extensions

**User Story:** As a developer, I want the ToDo schema to support additional source types and linked context, so that the Radar can display rich ToDo metadata from all sources.

#### Acceptance Criteria

1. THE Backend SHALL extend the `ToDoSourceType` enum in `backend/schemas/todo.py` to include two additional values: `chat` and `ai_detected`.
2. THE Backend SHALL add an optional `linked_context` field to the `ToDoCreate`, `ToDoUpdate`, and `ToDoResponse` models. THE `linked_context` field SHALL be a JSON string containing reference metadata (e.g., `{"type": "thread", "thread_id": "abc123"}` or `{"type": "message", "message_id": "xyz789"}`).
3. THE Backend SHALL use snake_case field names for all new and existing fields.
4. THE Frontend SHALL define corresponding camelCase TypeScript interfaces and update `toCamelCase()` / `toSnakeCase()` conversion functions in the ToDo service layer.
5. THE existing `ToDoStatus` enum values (pending, overdue, in_discussion, handled, cancelled, deleted) SHALL remain unchanged.
6. THE Backend SHALL implement a SQLite migration strategy for the schema changes:
   - The `linked_context` column SHALL be added via `ALTER TABLE todos ADD COLUMN linked_context TEXT` (safe, non-destructive).
   - The `source_type` CHECK constraint update SHALL use the standard SQLite table-rebuild pattern (create new table with updated CHECK → copy data → drop old → rename new) to add `chat` and `ai_detected` to the allowed values.
   - THE table-rebuild migration SHALL be wrapped in a single transaction (`BEGIN IMMEDIATE ... COMMIT`) for crash safety (PE Finding #7).
   - THE migration SHALL include a version check (e.g., check if `chat` is already in the CHECK constraint or if the column already exists) so that the migration is idempotent and does not re-run if already applied (PE Finding #7).
   - Pydantic enum validation (`ToDoSourceType`) SHALL serve as the primary enforcement layer for allowed `source_type` values, with the SQLite CHECK as a secondary safeguard.

### Requirement 6: Frontend — Swarm Radar Service Layer (ToDo Functions)

**User Story:** As a developer, I want a dedicated frontend service module for ToDo-related API calls, so that API calls are centralized and follow the established service pattern.

#### Acceptance Criteria

1. THE Frontend SHALL create a `desktop/src/services/radar.ts` service module that centralizes Swarm Radar API calls.
2. THE radar service SHALL include ToDo-related functions: `fetchActiveTodos(workspaceId)`, `createTodo(data)`, `updateTodoStatus(todoId, status)`, `convertTodoToTask(todoId, agentId)`.
3. THE radar service SHALL implement `toCamelCase()` conversion for all backend ToDo responses, mapping snake_case fields (e.g., `source_type`, `due_date`, `linked_context`, `created_at`, `updated_at`, `workspace_id`, `task_id`) to camelCase (e.g., `sourceType`, `dueDate`, `linkedContext`, `createdAt`, `updatedAt`, `workspaceId`, `taskId`).
4. THE radar service SHALL implement `toSnakeCase()` conversion for all request payloads sent to the backend.
5. THE radar service SHALL use the existing HTTP client pattern consistent with `desktop/src/services/tasks.ts`.
6. THE `convertTodoToTask` function SHALL accept `todoId` and `agentId` parameters and call the existing `POST /api/todos/{todoId}/convert-to-task` endpoint.

### Requirement 7: Frontend — useTodoZone State Management Hook

**User Story:** As a developer, I want a dedicated React hook for ToDo zone state management, so that ToDo data fetching, lifecycle actions, and optimistic updates are cleanly encapsulated.

#### Acceptance Criteria

1. THE Frontend SHALL implement a `useTodoZone` hook that manages state for the ToDo sub-section of the Needs Attention zone.
2. THE `useTodoZone` hook SHALL use React Query for data fetching with a 30-second polling interval, gated by `enabled: isVisible` where `isVisible` is derived from `rightSidebars.isActive('todoRadar')`.
3. THE `useTodoZone` hook SHALL return: `todos: RadarTodo[]` (sorted, active only), `isLoading: boolean`, and action handlers: `quickAddTodo`, `startTodo`, `editTodo`, `completeTodo`, `cancelTodo`, `deleteTodo`.
4. THE `useTodoZone` hook SHALL filter the API response to include only active ToDos (status: `pending` or `overdue`) before returning.
5. THE `useTodoZone` hook SHALL apply the `sortTodos` function from Spec 1 to the filtered results.
6. THE `useTodoZone` hook SHALL implement Optimistic_Updates for all lifecycle actions using React Query's `onMutate`/`onError`/`onSettled` pattern.
7. THE `startTodo` action SHALL resolve the workspace's Default_Agent (via the existing agent lookup mechanism), then call `convertTodoToTask(todoId, agentId)`, and navigate to the new chat thread.
8. THE `useTodoZone` hook SHALL use the React Query cache key `['radar', 'todos']`.
9. WHEN `isVisible` is false, THE `useTodoZone` hook SHALL execute zero polling queries.

### Requirement 8: Delete Existing Mock Component

**User Story:** As a developer, I want the old mock TodoRadarSidebar completely removed, so that there is no dead code or confusion about which component is active.

#### Acceptance Criteria

1. THE file `desktop/src/pages/chat/components/TodoRadarSidebar.tsx` SHALL be deleted.
2. ALL imports of `TodoRadarSidebar` in `ChatPage.tsx` and any other files SHALL be replaced with imports of the new `SwarmRadar` component from `desktop/src/pages/chat/components/radar/SwarmRadar.tsx`.
3. THE `RIGHT_SIDEBAR_WIDTH_CONFIGS` entry for `todoRadar` SHALL remain unchanged (the new SwarmRadar uses the same width constraints).
4. ALL existing tests referencing `TodoRadarSidebar` SHALL be updated to reference `SwarmRadar`.
5. THE `ChatPage.tsx` SHALL pass the existing `pendingQuestion` and `pendingPermission` props to the new `SwarmRadar` component (these are consumed by Spec 3, but the prop wiring is done here).

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: ToDo sort ordering is total and consistent (with id tiebreaker)

*For any* list of active ToDo items with arbitrary priorities, statuses, due dates, creation dates, and ids, the `sortTodos` function SHALL produce a list where every adjacent pair (a, b) satisfies the sort contract: overdue before non-overdue, then higher priority before lower, then earlier due date before later (null last), then newer creation date before older, then lexicographically smaller `id` before larger. Sorting the same input twice SHALL produce identical output (idempotence). No two distinct items SHALL have ambiguous relative ordering — the `id` tiebreaker guarantees a total order (PE Finding #6).

**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/todoSort.property.test.ts`
**Validates:** Requirement 1.3

### Property 2: ToDo active filtering shows only pending and overdue items

*For any* set of ToDo items with mixed statuses (pending, overdue, in_discussion, handled, cancelled, deleted), the active filter function SHALL return only items with status `pending` or `overdue`. The count of returned items SHALL equal the count of pending + overdue items in the input. No item with status `in_discussion`, `handled`, `cancelled`, or `deleted` SHALL appear in the result.

**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/todoSort.property.test.ts`
**Validates:** Requirement 1.1, Requirement 7.4

### Property 3: Priority and timeline indicator mapping is consistent

*For any* `RadarTodo` item, the priority indicator function SHALL map: `high` → 🔴, `medium` → 🟡, `low` → 🔵, `none` → no indicator (empty string or null). The timeline indicator function SHALL map: status `overdue` → ⚠️, due date equal to today → ⏰. The source type label function SHALL map each of the 7 source types to exactly one emoji label. No source type SHALL be unmapped. No two source types SHALL map to the same emoji. The mapping is: manual → ✏️, email → 📧, slack → 💬, meeting → 📅, integration → 🔗, chat → 💭, ai_detected → 🤖.

**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/todoSort.property.test.ts`
**Validates:** Requirement 1.2, 1.4, 1.5, 1.6

### Property 4: ToDo lifecycle state transitions produce correct status and zone placement

*For any* active ToDo (status: pending or overdue), performing a lifecycle action SHALL result in the correct status transition:
- Start → status becomes `handled` and a new WIP task is created (via `convert_to_task` API)
- Complete → status becomes `handled` with no task created
- Cancel → status becomes `cancelled`
- Delete → status becomes `deleted`

After any of Complete, Cancel, or Delete, the ToDo SHALL no longer appear in the active ToDo list (filtered out by the active filter). After Start, the ToDo SHALL no longer appear in the active list (status is `handled`).

**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/todoLifecycle.property.test.ts`
**Validates:** Requirement 2.2, 2.4, 2.5, 2.6

### Property 5: Quick-add creates ToDo with correct defaults and clears input

*For any* non-empty, non-whitespace string submitted via Quick-Add, a new ToDo SHALL be created with `source_type=manual`, `priority=none`, `status=pending`, and the submitted string as the title. After successful creation, the input field value SHALL be empty. The created ToDo SHALL appear in the active ToDo list (since its status is `pending`).

**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/todoLifecycle.property.test.ts`
**Validates:** Requirement 3.3, 3.5

### Property 6: toCamelCase and toSnakeCase are inverse operations (ToDo service layer)

*For any* valid backend ToDo response object with snake_case field names, applying `toCamelCase` then `toSnakeCase` SHALL produce an object with the same field values (round-trip). Specifically, `toSnakeCase(toCamelCase(backendResponse))` SHALL have equivalent field values to the original `backendResponse` for all mapped fields. This covers the new `linked_context` ↔ `linkedContext` mapping.

**Test type:** Property-based (fast-check), min 100 iterations
**Test file:** `desktop/src/pages/chat/components/radar/__tests__/caseConversion.property.test.ts`
**Validates:** Requirement 6.3, 6.4

### Property 7: linked_context round-trip through create and read

*For any* valid JSON string used as `linked_context` when creating a ToDo via `POST /api/todos`, reading that ToDo back via `GET /api/todos` SHALL return the identical `linked_context` string. The round-trip SHALL preserve the exact JSON content without modification.

**Test type:** Property-based (pytest + hypothesis), min 100 iterations
**Test file:** `backend/tests/test_todo_linked_context.py`
**Validates:** Requirement 5.2
