# Requirements Document

## Introduction

The SwarmAI application contains 6 legacy "Operating Loop" section entities (Signals/ToDos, Plan/PlanItems, Execute/Tasks, Communicate/Communications, Artifacts, Reflections) plus a Sections aggregator that no longer align with the redesigned SwarmWS workspace model. The new model uses only two semantic zones: `Knowledge/` and `Projects/`. This feature removes the Operating Loop code for 5 of the 6 entities (Plan/PlanItems, Communicate/Communications, Artifacts, Reflections, and the Sections aggregator) — including API endpoints, database tables, frontend pages, navigation items, type definitions, services, tests, and documentation references — while carefully preserving both the `tasks` subsystem (used by the chat/agent background execution system) and the `todos` subsystem (needed by the upcoming Swarm Radar feature).

## Glossary

- **Operating_Loop**: The legacy "Daily Work Operating Loop" feature comprising 6 section entities (Signals, Plan, Execute, Communicate, Artifacts, Reflection) and a Sections aggregator
- **Backend**: The Python FastAPI sidecar application under `backend/`
- **Frontend**: The React/TypeScript desktop application under `desktop/src/`
- **Router**: A FastAPI APIRouter module that defines HTTP endpoints for an entity
- **Manager**: A Python module in `backend/core/` containing business logic for an entity
- **Schema**: A Pydantic model module in `backend/schemas/` defining request/response shapes
- **Tasks_Subsystem**: The `tasks` router, manager, schema, database table, and frontend service used by the chat/agent system for background task execution — distinct from the Operating Loop "Execute" section
- **ToDos_Subsystem**: The `todos` router, manager, schema, database table, frontend service, and type definitions used by the upcoming Swarm Radar feature for ToDo tracking, click-to-chat, and convert-to-task workflows
- **Search_Manager**: The `backend/core/search_manager.py` module that indexes entities for cross-type search
- **Section_Manager**: The `backend/core/section_manager.py` aggregator that queries counts across all 6 Operating Loop sections
- **Seed_DB**: The pre-built `seed.db` SQLite database shipped with the application for fast startup
- **Mock_Data_Script**: The `backend/scripts/generate_mock_data.py` script that populates test data


## Requirements

### Requirement 1: Remove Backend Routers for Operating Loop Entities

**User Story:** As a developer, I want the legacy Operating Loop API endpoints removed, so that the backend only exposes endpoints aligned with the new SwarmWS model.

#### Acceptance Criteria

1. WHEN the Backend starts, THE Backend SHALL NOT register the `sections_router` at `/api/workspaces` (sections tag)
2. WHEN the Backend starts, THE Backend SHALL NOT register the `plan_items_router` at `/api/workspaces` (plan-items tag)
3. WHEN the Backend starts, THE Backend SHALL NOT register the `communications_router` at `/api/workspaces` (communications tag)
4. WHEN the Backend starts, THE Backend SHALL NOT register the `artifacts_router` at `/api/workspaces` (artifacts tag)
5. WHEN the Backend starts, THE Backend SHALL NOT register the `reflections_router` at `/api/workspaces` (reflections tag)
6. THE Backend SHALL preserve the `tasks_router` registration at `/api/tasks` for the Tasks_Subsystem
7. THE Backend SHALL preserve the `todos_router` registration at `/api/todos` for the ToDos_Subsystem
8. THE Backend SHALL delete the following router files: `backend/routers/sections.py`, `backend/routers/plan_items.py`, `backend/routers/communications.py`, `backend/routers/artifacts.py`, `backend/routers/reflections.py`
9. WHEN the router imports are updated in `backend/routers/__init__.py`, THE Backend SHALL remove exports for `sections_router`, `plan_items_router`, `communications_router`, `artifacts_router`, `reflections_router`
10. WHEN the router imports are updated in `backend/main.py`, THE Backend SHALL remove imports and `include_router` calls for the 5 removed routers


### Requirement 2: Remove Backend Managers for Operating Loop Entities

**User Story:** As a developer, I want the legacy Operating Loop business logic removed, so that the codebase only contains managers aligned with the new SwarmWS model.

#### Acceptance Criteria

1. THE Backend SHALL delete the following manager files: `backend/core/section_manager.py`, `backend/core/plan_item_manager.py`, `backend/core/communication_manager.py`, `backend/core/artifact_manager.py`, `backend/core/reflection_manager.py`
2. THE Backend SHALL preserve `backend/core/task_manager.py` for the Tasks_Subsystem
3. THE Backend SHALL preserve `backend/core/todo_manager.py` for the ToDos_Subsystem
4. WHEN `backend/core/__init__.py` references any removed manager, THE Backend SHALL remove those references

### Requirement 3: Remove Backend Schemas for Operating Loop Entities

**User Story:** As a developer, I want the legacy Operating Loop Pydantic models removed, so that the schema layer only defines models aligned with the new SwarmWS model.

#### Acceptance Criteria

1. THE Backend SHALL delete the following schema files: `backend/schemas/section.py`, `backend/schemas/plan_item.py`, `backend/schemas/communication.py`, `backend/schemas/artifact.py`, `backend/schemas/reflection.py`
2. THE Backend SHALL preserve `backend/schemas/task.py` for the Tasks_Subsystem
3. THE Backend SHALL preserve `backend/schemas/todo.py` for the ToDos_Subsystem
4. WHEN `backend/schemas/__init__.py` imports from any removed schema file, THE Backend SHALL remove those imports and `__all__` entries
5. THE Backend SHALL preserve the `Priority` enum if it is used by any non-Operating-Loop schema; IF `Priority` is only used by Operating Loop schemas, THEN THE Backend SHALL remove it

### Requirement 4: Remove Database Tables for Operating Loop Entities

**User Story:** As a developer, I want the legacy Operating Loop database tables removed, so that the SQLite schema only contains tables aligned with the new SwarmWS model.

#### Acceptance Criteria

1. THE Backend SHALL remove the `plan_items` table definition from the database schema
2. THE Backend SHALL remove the `communications` table definition from the database schema
3. THE Backend SHALL remove the `artifacts` table definition from the database schema
4. THE Backend SHALL remove the `artifact_tags` table definition from the database schema
5. THE Backend SHALL remove the `reflections` table definition from the database schema
6. THE Backend SHALL preserve the `tasks` table for the Tasks_Subsystem
7. THE Backend SHALL preserve the `todos` table for the ToDos_Subsystem
8. WHEN the database table references (e.g., `db.plan_items`, `db.communications`, `db.artifacts`, `db.reflections`) exist in the SQLite database module, THE Backend SHALL remove those table accessors
9. THE Backend SHALL preserve the `db.todos` table accessor for the ToDos_Subsystem
10. WHEN cleanup is complete, THE Seed_DB SHALL be regenerated from scratch to exclude the removed tables


### Requirement 5: Clean Up Search Manager

**User Story:** As a developer, I want the search indexing for removed entities cleaned up, so that the search system only indexes entities that still exist.

#### Acceptance Criteria

1. WHEN the Search_Manager searches entities, THE Search_Manager SHALL NOT include `plan_item`, `communication`, `artifact`, or `reflection` in `SEARCHABLE_ENTITY_TYPES`
2. WHEN the Search_Manager searches entities, THE Search_Manager SHALL NOT include table entries for `plan_items`, `communications`, `artifacts`, or `reflections` in `_ENTITY_TABLE_CONFIG`
3. THE Search_Manager SHALL preserve `task`, `thread`, and `todo` in `SEARCHABLE_ENTITY_TYPES`
4. WHEN the Search_Manager uses `db.todos._get_connection()` as a database connection accessor, THE Search_Manager SHALL continue to use it (the `todos` table is preserved)

### Requirement 6: Clean Up Mock Data Generation Script

**User Story:** As a developer, I want the mock data generation script updated to stop generating data for removed entities, so that test data only covers entities that still exist.

#### Acceptance Criteria

1. THE Mock_Data_Script SHALL NOT generate data for `plan_items`, `communications`, `artifacts`, or `reflections`
2. THE Mock_Data_Script SHALL remove the functions `_generate_plan_items`, `_generate_communications`, `_generate_artifacts`, `_generate_reflections`
3. THE Mock_Data_Script SHALL remove calls to the deleted generator functions from `generate_mock_data()`
4. THE Mock_Data_Script SHALL preserve `_generate_tasks` for the Tasks_Subsystem
5. THE Mock_Data_Script SHALL preserve `_generate_todos` for the ToDos_Subsystem

### Requirement 7: Remove Frontend Pages for Operating Loop Sections

**User Story:** As a user, I want the legacy Operating Loop section pages removed from the UI, so that the application only shows pages aligned with the new SwarmWS model.

#### Acceptance Criteria

1. THE Frontend SHALL delete the following page files: `desktop/src/pages/SignalsPage.tsx`, `desktop/src/pages/PlanPage.tsx`, `desktop/src/pages/ExecutePage.tsx`, `desktop/src/pages/CommunicatePage.tsx`, `desktop/src/pages/ArtifactsPage.tsx`, `desktop/src/pages/ReflectionPage.tsx`
2. THE Frontend SHALL preserve `desktop/src/pages/TasksPage.tsx` for the Tasks_Subsystem
3. WHEN the page imports are removed from `desktop/src/App.tsx`, THE Frontend SHALL remove the corresponding `<Route>` definitions for `/signals`, `/plan`, `/execute`, `/communicate`, `/artifacts`, `/reflection`
4. THE Frontend SHALL preserve the `/tasks` route in `desktop/src/App.tsx`

### Requirement 8: Remove Frontend Navigation Items for Operating Loop Sections

**User Story:** As a user, I want the legacy Operating Loop navigation items removed from the sidebar, so that the navigation only shows items aligned with the new SwarmWS model.

#### Acceptance Criteria

1. WHEN the `LeftSidebar` component renders, THE Frontend SHALL NOT display navigation items for Signals, Plan, Execute, Communicate, Artifacts, or Reflection in the `sectionNavItems` array
2. WHEN the `LeftSidebar` component renders, THE Frontend SHALL NOT display the divider between modal nav and section nav (since the entire section nav group is removed)
3. THE Frontend SHALL remove the `sectionNavItems` array and its rendering loop from `desktop/src/components/layout/ThreeColumnLayout.tsx`


### Requirement 9: Remove Frontend Services and Type Definitions for Operating Loop Entities

**User Story:** As a developer, I want the legacy Operating Loop frontend services and types removed, so that the frontend codebase only contains services and types aligned with the new SwarmWS model.

#### Acceptance Criteria

1. THE Frontend SHALL delete the following service files: `desktop/src/services/sections.ts`
2. THE Frontend SHALL preserve `desktop/src/services/tasks.ts` for the Tasks_Subsystem
3. THE Frontend SHALL preserve `desktop/src/services/todos.ts` for the ToDos_Subsystem
4. THE Frontend SHALL delete the following type definition files: `desktop/src/types/plan-item.ts`, `desktop/src/types/section.ts`, `desktop/src/types/artifact.ts`, `desktop/src/types/reflection.ts`, `desktop/src/types/communication.ts`
5. THE Frontend SHALL preserve `desktop/src/types/todo.ts` for the ToDos_Subsystem
6. THE Frontend SHALL preserve the `TodoItem` interface in `desktop/src/types/index.ts` for the ToDos_Subsystem
7. THE Frontend SHALL preserve `sourceTodoId` in the `Task` interface in `desktop/src/types/index.ts` (links Tasks back to originating ToDos)
8. THE Frontend SHALL preserve `todoId` in the `ChatThread` interface in `desktop/src/types/chat-thread.ts` (links threads to ToDos via click-to-chat)
9. THE Frontend SHALL preserve `todoId` in `desktop/src/services/context.ts` `bindThread` function and its snake_case mapping
10. THE Frontend SHALL preserve `todoId` in `ThreadBindRequest` and `ThreadBindResponse` interfaces in `desktop/src/types/index.ts`

### Requirement 10: Remove Backend Test Files for Operating Loop Entities

**User Story:** As a developer, I want the legacy Operating Loop test files removed, so that the test suite only covers entities that still exist.

#### Acceptance Criteria

1. THE Backend SHALL delete the following test files that directly test removed entities:
   - `backend/tests/test_artifact_manager.py`
   - `backend/tests/test_artifacts_router.py`
   - `backend/tests/test_communication_manager.py`
   - `backend/tests/test_communications_router.py`
   - `backend/tests/test_plan_item_manager.py`
   - `backend/tests/test_plan_items_router.py`
   - `backend/tests/test_reflection_manager.py`
   - `backend/tests/test_reflections_router.py`
   - `backend/tests/test_section_manager.py`
   - `backend/tests/test_sections_router.py`
2. THE Backend SHALL preserve `backend/tests/test_todos_router.py` for the ToDos_Subsystem
3. THE Backend SHALL delete the following property-based test files that test removed entity behaviors:
   - `backend/tests/test_property_archived_aggregation.py`
   - `backend/tests/test_property_artifact_hybrid.py`
   - `backend/tests/test_property_artifact_versioning.py`
   - `backend/tests/test_property_blocked_reason.py`
   - `backend/tests/test_property_communication_sent.py`
   - `backend/tests/test_property_global_view.py`
   - `backend/tests/test_property_plan_item_cascade.py`
   - `backend/tests/test_property_reflection_hybrid.py`
   - `backend/tests/test_property_section_contract.py`
4. THE Backend SHALL preserve the following property-based test files for the ToDos_Subsystem:
   - `backend/tests/test_property_todo_task_conversion.py`
   - `backend/tests/test_property_overdue_detection.py`
5. THE Backend SHALL preserve test files for the Tasks_Subsystem: `backend/tests/test_task_manager_updates.py`, `backend/tests/test_task_data_migration.py`, `backend/tests/test_property_task_status_compat.py`
6. WHEN `backend/tests/test_search_manager.py` references removed entity types, THE Backend SHALL update the test to only cover `task`, `thread`, and `todo` entity types
7. WHEN `backend/tests/test_mock_data.py` references removed entity generators, THE Backend SHALL update the test to only cover remaining generators
8. WHEN `backend/tests/test_property_search_scope.py` references removed entity types, THE Backend SHALL update the test to only cover remaining entity types


### Requirement 11: Update Documentation to Remove Operating Loop References

**User Story:** As a developer, I want the architecture documentation updated to remove Operating Loop references, so that documentation accurately reflects the current SwarmWS model.

#### Acceptance Criteria

1. WHEN `.kiro/specs/ARCHITECTURE.md` references Operating Loop sections (Signals, Plan, Execute, Communicate, Artifacts, Reflection), THE Backend SHALL remove or update those references to reflect the new SwarmWS model with only `Knowledge/` and `Projects/` zones
2. WHEN `.kiro/specs/AGENT_ARCHITECTURE_DEEP_DIVE.md` references Operating Loop sections, THE Backend SHALL remove or update those references
3. THE Backend SHALL delete the entire `.kiro/specs/TODO-swarm-signals-ingestion-and-auto-reply-specs/` directory since it describes a feature built on the removed Signals/ToDos entity

### Requirement 12: Preserve ToDos Subsystem for Swarm Radar Dependency

**User Story:** As a developer, I want the ToDos subsystem preserved during the Operating Loop cleanup, so that the upcoming Swarm Radar feature can extend it with Radar ToDos functionality.

#### Acceptance Criteria

1. THE Backend SHALL preserve `backend/routers/todos.py` and its registration in `main.py` at `/api/todos`
2. THE Backend SHALL preserve `backend/core/todo_manager.py` and all its public methods including `convert_to_task`
3. THE Backend SHALL preserve `backend/schemas/todo.py` and all its Pydantic models
4. THE Backend SHALL preserve the `todos` database table, `SQLiteToDosTable` class, and `db.todos` accessor
5. THE Backend SHALL preserve the `source_todo_id` column AND its foreign key constraint `REFERENCES todos(id)` in the `tasks` table
6. THE Backend SHALL preserve the `task_id` column in the `todos` table
7. THE Backend SHALL preserve the `todo_id` column and its foreign key in the `chat_threads` table
8. THE Backend SHALL preserve all `todo_id` handling in `backend/core/chat_thread_manager.py` including `_resolve_workspace_from_todo()` logic
9. THE Backend SHALL preserve `todo_id` field in `ThreadBindRequest` and `ThreadBindResponse` schemas in `backend/schemas/context.py`
10. THE Backend SHALL preserve `todo_id` field in `ChatThreadCreate`, `ChatThreadUpdate`, `ChatThreadResponse` schemas in `backend/schemas/chat_thread.py`
11. THE Backend SHALL preserve `todo_id` references in `backend/routers/chat.py` bind_thread endpoint
12. THE Frontend SHALL preserve `desktop/src/services/todos.ts` and `desktop/src/types/todo.ts`
13. THE Frontend SHALL preserve `TodoItem` interface and `sourceTodoId` field in `Task` interface in `desktop/src/types/index.ts`
14. THE Frontend SHALL preserve `todoId` in `ChatThread`, `ThreadBindRequest`, and `ThreadBindResponse` interfaces
15. THE Frontend SHALL preserve `todoId` in `desktop/src/services/context.ts` `bindThread` function

### Requirement 13: Preserve Tasks and ToDos Subsystem Integrity

**User Story:** As a developer, I want the Tasks_Subsystem and ToDos_Subsystem to remain fully functional after the Operating Loop cleanup, so that background agent task execution in the chat system and the upcoming Swarm Radar ToDos feature continue to work.

#### Acceptance Criteria

1. THE Backend SHALL preserve `backend/routers/tasks.py` and its registration in `main.py` at `/api/tasks`
2. THE Backend SHALL preserve `backend/core/task_manager.py` and all its public methods
3. THE Backend SHALL preserve `backend/schemas/task.py` and all its Pydantic models
4. THE Backend SHALL preserve the `tasks` database table and all its columns including `source_todo_id` and its FK to `todos`
5. THE Frontend SHALL preserve `desktop/src/pages/TasksPage.tsx` and its `/tasks` route
6. THE Frontend SHALL preserve `desktop/src/services/tasks.ts`
7. THE Frontend SHALL preserve the `Task`, `TaskCreateRequest`, `TaskMessageRequest`, `RunningTaskCount` interfaces in `desktop/src/types/index.ts`
8. THE Frontend SHALL preserve `sourceTodoId` in the `Task` interface (links Tasks back to originating ToDos)

### Requirement 14: Application Startup After Cleanup

**User Story:** As a user, I want the application to start successfully after the Operating Loop cleanup, so that I can use the application without errors.

#### Acceptance Criteria

1. WHEN the Backend starts after cleanup, THE Backend SHALL initialize the database without errors related to missing Operating Loop tables
2. WHEN the Backend starts after cleanup, THE Backend SHALL register all preserved routers without import errors
3. WHEN the Frontend loads after cleanup, THE Frontend SHALL render the main layout without errors related to missing Operating Loop pages or components
4. WHEN the Frontend loads after cleanup, THE Frontend SHALL display the left sidebar navigation without Operating Loop section items
5. WHEN the old `data.db` is deleted and the Seed_DB is regenerated, THE Backend SHALL start with a clean database containing only the preserved tables (including `todos` and `tasks`)
6. WHEN the Backend test suite runs after cleanup (`pytest`), THE Backend SHALL pass all remaining tests without import errors or missing module references
7. WHEN the Frontend test suite runs after cleanup (`npm test -- --run`), THE Frontend SHALL pass all remaining tests without import errors or missing module references
