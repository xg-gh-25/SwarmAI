# Implementation Plan: Swarm Radar ToDos (Sub-Spec 2 of 5)

## Overview

Build the ToDo layer of the Swarm Radar — backend schema extensions, frontend service layer, state management hook, UI components (TodoList, TodoItem, QuickAddTodo), lifecycle actions with optimistic updates, and cleanup of the old mock component. Backend first, then service layer, then hooks, then components, then wiring and cleanup.

## Tasks

- [x] 1. Backend schema extensions and migration
  - [x] 1.1 Extend `ToDoSourceType` enum and add `linked_context` field in `backend/schemas/todo.py`
    - Add `CHAT = "chat"` and `AI_DETECTED = "ai_detected"` to `ToDoSourceType` enum
    - Add `linked_context: Optional[str] = Field(None, max_length=10000)` to `ToDoCreate`, `ToDoUpdate`, and `ToDoResponse` models
    - Keep `ToDoStatus` enum unchanged (pending, overdue, in_discussion, handled, cancelled, deleted)
    - Include module-level docstring update per dev rules
    - _Requirements: 5.1, 5.2, 5.3, 5.5_

  - [x] 1.2 Implement SQLite migration in `backend/database/sqlite.py`
    - Add migration step 1: `ALTER TABLE todos ADD COLUMN linked_context TEXT` with idempotency check via `PRAGMA table_info`
    - Add migration step 2: Table-rebuild for `source_type` CHECK constraint to include `chat` and `ai_detected`
    - Wrap table-rebuild in `BEGIN IMMEDIATE ... COMMIT` transaction for crash safety (PE Finding #7)
    - Include idempotency check: skip if `'chat'` already in `CREATE TABLE` SQL from `sqlite_master` (PE Finding #7)
    - On failure, execute `ROLLBACK` and re-raise exception
    - _Requirements: 5.6_

  - [ ]* 1.3 Write property test for linked_context round-trip (Property 7)
    - **Property 7: linked_context round-trip through create and read**
    - Create `backend/tests/test_todo_linked_context.py`
    - Use `hypothesis` to generate random valid JSON strings
    - Create a ToDo via `POST /api/todos` with the generated `linked_context`, read it back via `GET /api/todos`, verify identical content
    - Minimum 100 iterations
    - **Validates: Requirements 5.2**

- [x] 2. Checkpoint — Backend schema and migration
  - Ensure all backend tests pass (`cd backend && pytest`), ask the user if questions arise.

- [x] 3. Frontend service layer — `radar.ts`
  - [x] 3.1 Create `desktop/src/services/radar.ts`
    - Implement `toCamelCase(todo)` converting all 13 snake_case fields to camelCase (including `linked_context` → `linkedContext`)
    - Implement `toSnakeCase(todo)` converting camelCase fields back to snake_case
    - Implement `radarService` object with: `fetchActiveTodos(workspaceId)`, `createTodo(data)`, `updateTodoStatus(todoId, status)`, `convertTodoToTask(todoId, agentId)`
    - Use existing HTTP client pattern from `desktop/src/services/tasks.ts`
    - Include module-level docstring per dev rules
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

  - [ ]* 3.2 Write property test for case conversion round-trip (Property 6)
    - **Property 6: toCamelCase and toSnakeCase are inverse operations**
    - Create `desktop/src/pages/chat/components/radar/__tests__/caseConversion.property.test.ts`
    - Use `fast-check` to generate random backend ToDo response objects with all 13 snake_case fields
    - Verify `toSnakeCase(toCamelCase(backendResponse))` has equivalent field values to the original
    - Minimum 100 iterations
    - **Validates: Requirements 6.3, 6.4**

- [x] 4. Frontend state management — `useTodoZone` hook
  - [x] 4.1 Create `desktop/src/pages/chat/components/radar/hooks/useTodoZone.ts`
    - Implement React Query data fetching with key `['radar', 'todos']`, 30s polling interval, gated by `enabled: isVisible`
    - Implement active filtering (`status === 'pending' || status === 'overdue'`) and sorting via `sortTodos` from Spec 1 in a `useMemo`
    - Implement `quickAddTodo(title)` mutation calling `radarService.createTodo` with defaults: `sourceType: 'manual'`, `priority: 'none'`
    - Implement `startTodo(todoId)` — resolve default agent, call `convertTodoToTask`, navigate to chat thread via `useTabState`
    - Implement `completeTodo(todoId)` — call `updateTodoStatus(todoId, 'handled')`
    - Implement `cancelTodo(todoId)` — call `updateTodoStatus(todoId, 'cancelled')`
    - Implement `deleteTodo(todoId)` — call `updateTodoStatus(todoId, 'deleted')`
    - Implement `editTodo(todoId)` — no API call, signals inline edit mode in TodoItem
    - Implement optimistic updates for all mutations: `onMutate` (snapshot + cache update), `onError` (restore snapshot), `onSettled` (invalidate queries)
    - Handle `startTodo` error case: if no default agent found, show inline error "No default agent configured."
    - Include module-level docstring per dev rules
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 2.2, 2.4, 2.5, 2.6, 2.9, 2.10, 4.1, 4.2_

- [x] 5. Checkpoint — Service layer and hook
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [x] 6. ToDo UI components
  - [x] 6.1 Create `desktop/src/pages/chat/components/radar/TodoItem.tsx`
    - Accept `TodoItemProps`: `todo: RadarTodo`, `onStart`, `onEdit`, `onComplete`, `onCancel`, `onDelete`
    - Render as `<li role="listitem" className="radar-todo-item">` with conditional `radar-todo-item--overdue` class
    - Display title (truncated 1 line), priority indicator via `getPriorityIndicator`, timeline indicator via `getTimelineIndicator`, source type label via `getSourceTypeLabel`, formatted due date
    - Render `⋯` overflow button on hover with `aria-label="Actions for {todo.title}"`
    - Overflow menu: positioned `<div>` with Start, Edit, Complete, Cancel, Delete buttons
    - Cancel and Delete trigger inline confirmation: "Cancel this ToDo?" / "Delete this ToDo?" with Confirm (red) and Back buttons
    - Close menu on outside click or Escape key
    - Use `--color-*` CSS variables only
    - Include module-level docstring per dev rules
    - _Requirements: 1.2, 1.4, 1.5, 1.6, 1.9, 2.1, 2.7, 2.8_

  - [x] 6.2 Create `desktop/src/pages/chat/components/radar/TodoList.tsx`
    - Accept `TodoListProps`: `todos: RadarTodo[]`, `onStart`, `onEdit`, `onComplete`, `onCancel`, `onDelete`
    - Render `<ul role="list">` containing one `TodoItem` per entry
    - Render nothing when `todos.length === 0` (parent RadarZone handles empty state)
    - Pass per-item action callbacks to each `TodoItem`
    - Include module-level docstring per dev rules
    - _Requirements: 1.1, 1.7, 1.8_

  - [x] 6.3 Create `desktop/src/pages/chat/components/radar/QuickAddTodo.tsx`
    - Accept `QuickAddTodoProps`: `onAdd: (title: string) => Promise<void>`
    - Render `<form>` with single-line `<input type="text" placeholder="Add a ToDo..." aria-label="Add a new ToDo">` and submit `<button>` with `add` material icon
    - Submit on Enter or button click; trim input; reject empty/whitespace-only strings
    - On success: clear input
    - On failure: show inline error `<span className="radar-quick-add-error">` "Failed to add ToDo. Try again.", retain text, auto-dismiss after 5s
    - Use `--color-*` CSS variables only
    - Include module-level docstring per dev rules
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [ ]* 6.4 Write property tests for sort, filtering, and indicators (Properties 1, 2, 3)
    - **Property 1: ToDo sort ordering is total and consistent (with id tiebreaker)**
    - **Property 2: ToDo active filtering shows only pending and overdue items**
    - **Property 3: Priority and timeline indicator mapping is consistent**
    - Create `desktop/src/pages/chat/components/radar/__tests__/todoSort.property.test.ts`
    - Property 1: Generate random `RadarTodo` arrays, verify adjacent-pair ordering, idempotence, purity, totality via id tiebreaker. Min 100 iterations.
    - Property 2: Generate random ToDo arrays with all 6 statuses, verify only pending/overdue pass filter, count matches. Min 100 iterations.
    - Property 3: Generate random priorities/statuses/source types, verify correct emoji mapping, totality, injectivity. Min 100 iterations.
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 7.4**

  - [ ]* 6.5 Write property tests for lifecycle and quick-add (Properties 4, 5)
    - **Property 4: ToDo lifecycle state transitions produce correct status and zone placement**
    - **Property 5: Quick-add creates ToDo with correct defaults and clears input**
    - Create `desktop/src/pages/chat/components/radar/__tests__/todoLifecycle.property.test.ts`
    - Property 4: Generate random active ToDos and lifecycle actions, verify correct status transitions (Start→handled, Complete→handled, Cancel→cancelled, Delete→deleted), verify all removed from active filter. Min 100 iterations.
    - Property 5: Generate random non-whitespace strings, verify created ToDo has source_type=manual, priority=none, status=pending. Min 100 iterations.
    - **Validates: Requirements 2.2, 2.4, 2.5, 2.6, 3.3, 3.5**

- [x] 7. Checkpoint — ToDo components and property tests
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [x] 8. Wire ToDo components into SwarmRadar and integrate useTodoZone
  - [x] 8.1 Update `desktop/src/pages/chat/components/radar/SwarmRadar.tsx` to use real ToDo data
    - Import and call `useTodoZone` hook with `workspaceId` and `isVisible` from sidebar state
    - Replace mock ToDo `<li>` elements in the Needs Attention zone with `<QuickAddTodo>` and `<TodoList>` components
    - Pass `useTodoZone` action handlers (`startTodo`, `editTodo`, `completeTodo`, `cancelTodo`, `deleteTodo`) to `TodoList`
    - Pass `quickAddTodo` to `QuickAddTodo`
    - Update Needs Attention badge count to use `todos.length` from the hook (plus waitingItems count from Spec 3 when available)
    - _Requirements: 1.1, 1.7, 1.8, 2.1, 3.1, 7.1_

- [x] 9. Delete old mock component and rewire imports
  - [x] 9.1 Delete `desktop/src/pages/chat/components/TodoRadarSidebar.tsx`
    - _Requirements: 8.1_

  - [x] 9.2 Update `ChatPage.tsx` and any other files importing `TodoRadarSidebar`
    - Replace all `TodoRadarSidebar` imports with `SwarmRadar` from `desktop/src/pages/chat/components/radar/SwarmRadar.tsx`
    - Keep `RIGHT_SIDEBAR_WIDTH_CONFIGS` entry for `todoRadar` unchanged
    - Ensure `pendingQuestion` and `pendingPermission` props are passed to `SwarmRadar` (consumed by Spec 3)
    - _Requirements: 8.2, 8.3, 8.5_

  - [x] 9.3 Update any existing tests referencing `TodoRadarSidebar` to reference `SwarmRadar`
    - _Requirements: 8.4_

- [x] 10. Final checkpoint — Full integration
  - Ensure all tests pass (`cd desktop && npm test -- --run` and `cd backend && pytest`).
  - Ensure no TypeScript compilation errors (`cd desktop && npx tsc --noEmit`).
  - Ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Backend tasks (1.x) run first so the API is ready before frontend integration
- Property tests validate universal correctness properties from the design document
- Checkpoints ensure incremental validation at natural break points
- This spec builds on Spec 1 (`swarm-radar-foundation`) — shared types, sort utilities, indicators, RadarZone, and SwarmRadar shell must exist before starting
- The `editTodo` action opens inline edit mode in TodoItem — no separate edit API endpoint is needed (uses existing `PATCH /api/todos/{id}`)
