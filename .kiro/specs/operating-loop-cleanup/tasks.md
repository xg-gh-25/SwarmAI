# Implementation Plan: Operating Loop Cleanup

## Overview

Remove all legacy Operating Loop code (5 section entities + Sections aggregator) from the backend and frontend, following a leaf-to-root deletion order to prevent broken imports at any intermediate state. The Tasks Subsystem and ToDos Subsystem are explicitly preserved throughout.

## Tasks

- [x] 1. Delete backend test files for Operating Loop entities
  - [x] 1.1 Delete direct entity test files (10 files)
    - Delete `backend/tests/test_artifact_manager.py`
    - Delete `backend/tests/test_artifacts_router.py`
    - Delete `backend/tests/test_communication_manager.py`
    - Delete `backend/tests/test_communications_router.py`
    - Delete `backend/tests/test_plan_item_manager.py`
    - Delete `backend/tests/test_plan_items_router.py`
    - Delete `backend/tests/test_reflection_manager.py`
    - Delete `backend/tests/test_reflections_router.py`
    - Delete `backend/tests/test_section_manager.py`
    - Delete `backend/tests/test_sections_router.py`
    - Preserve `backend/tests/test_todos_router.py` (needed by ToDos Subsystem)
    - _Requirements: 10.1, 10.2_

  - [x] 1.2 Delete property-based test files for Operating Loop entities (9 files)
    - Delete `backend/tests/test_property_archived_aggregation.py`
    - Delete `backend/tests/test_property_artifact_hybrid.py`
    - Delete `backend/tests/test_property_artifact_versioning.py`
    - Delete `backend/tests/test_property_blocked_reason.py`
    - Delete `backend/tests/test_property_communication_sent.py`
    - Delete `backend/tests/test_property_global_view.py`
    - Delete `backend/tests/test_property_plan_item_cascade.py`
    - Delete `backend/tests/test_property_reflection_hybrid.py`
    - Delete `backend/tests/test_property_section_contract.py`
    - Preserve `backend/tests/test_property_todo_task_conversion.py` (needed by ToDos Subsystem)
    - Preserve `backend/tests/test_property_overdue_detection.py` (needed by ToDos Subsystem)
    - _Requirements: 10.3, 10.4_

  - [x] 1.3 Update `backend/tests/test_search_manager.py` to remove Operating Loop entity references
    - Remove test cases for `plan_item`, `communication`, `artifact`, `reflection` entity types
    - Keep test cases for `task`, `thread`, and `todo` entity types
    - _Requirements: 10.6_

  - [x] 1.4 Update `backend/tests/test_mock_data.py` to remove Operating Loop generator references
    - Remove assertions for `_generate_plan_items`, `_generate_communications`, `_generate_artifacts`, `_generate_reflections`
    - Keep assertions for `_generate_tasks` and `_generate_todos`
    - _Requirements: 10.7_

  - [x] 1.5 Update `backend/tests/test_property_search_scope.py` to remove Operating Loop entity types
    - Remove `plan_item`, `communication`, `artifact`, `reflection` from search scope property tests
    - Keep `task`, `thread`, and `todo` entity types
    - _Requirements: 10.8_

- [x] 2. Delete frontend pages, services, types and update App.tsx / ThreeColumnLayout
  - [x] 2.1 Delete frontend page files (6 files) and remove their imports/routes from App.tsx
    - Delete `desktop/src/pages/SignalsPage.tsx`
    - Delete `desktop/src/pages/PlanPage.tsx`
    - Delete `desktop/src/pages/ExecutePage.tsx`
    - Delete `desktop/src/pages/CommunicatePage.tsx`
    - Delete `desktop/src/pages/ArtifactsPage.tsx`
    - Delete `desktop/src/pages/ReflectionPage.tsx`
    - In `desktop/src/App.tsx`: remove the 6 page imports and 6 `<Route>` definitions for `/signals`, `/plan`, `/execute`, `/communicate`, `/artifacts`, `/reflection`
    - Preserve the `/tasks` route and `TasksPage` import
    - _Requirements: 7.1, 7.2, 7.3, 7.4_

  - [x] 2.2 Remove Operating Loop navigation from ThreeColumnLayout
    - Remove `sectionNavItems` array from `desktop/src/components/layout/ThreeColumnLayout.tsx`
    - Remove the rendering loop for `sectionNavItems`
    - Remove the divider between modal nav and section nav
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 2.3 Delete frontend service files (1 file)
    - Delete `desktop/src/services/sections.ts`
    - Preserve `desktop/src/services/tasks.ts`
    - Preserve `desktop/src/services/todos.ts` (needed by ToDos Subsystem)
    - _Requirements: 9.1, 9.2, 9.3_

  - [x] 2.4 Delete frontend type files (5 files) and clean up shared type definitions
    - Delete `desktop/src/types/plan-item.ts`
    - Delete `desktop/src/types/section.ts`
    - Delete `desktop/src/types/artifact.ts`
    - Delete `desktop/src/types/reflection.ts`
    - Delete `desktop/src/types/communication.ts`
    - Preserve `desktop/src/types/todo.ts` (needed by ToDos Subsystem)
    - In `desktop/src/types/index.ts`: preserve `TodoItem` interface, preserve `sourceTodoId` in `Task` interface, preserve `todoId` in `ThreadBindRequest` and `ThreadBindResponse`
    - In `desktop/src/types/chat-thread.ts`: preserve `todoId` in `ChatThread` interface
    - Preserve `Task`, `TaskCreateRequest`, `TaskMessageRequest`, `RunningTaskCount` interfaces
    - _Requirements: 9.4, 9.5, 9.6, 9.7, 9.8, 9.10, 13.7, 13.8_

  - [x] 2.5 Verify frontend context service todoId references are preserved
    - In `desktop/src/services/context.ts`: verify `todoId` parameter in `bindThread` function and its snake_case mapping are preserved (no changes needed)
    - _Requirements: 9.9_

  - [x] 2.6 Delete frontend test files for Operating Loop pages (2 files)
    - Delete `desktop/src/pages/__tests__/SectionPages.test.tsx`
    - Delete `desktop/src/pages/__tests__/WorkspaceScopedRouting.test.tsx`
    - _Requirements: 10.1 (frontend equivalent)_

- [x] 3. Checkpoint - Frontend and test deletions complete
  - Ensure frontend compiles without errors (`cd desktop && npm test -- --run`)
  - Ensure no broken imports remain from deleted pages, services, or types
  - Ask the user if questions arise

- [x] 4. Delete backend routers and update router registration
  - [x] 4.1 Delete backend router files (5 files) and clean up `__init__.py` and `main.py`
    - Delete `backend/routers/sections.py`
    - Delete `backend/routers/plan_items.py`
    - Delete `backend/routers/communications.py`
    - Delete `backend/routers/artifacts.py`
    - Delete `backend/routers/reflections.py`
    - Preserve `backend/routers/todos.py` (needed by ToDos Subsystem)
    - In `backend/routers/__init__.py`: remove imports and `__all__` entries for `sections_router`, `plan_items_router`, `communications_router`, `artifacts_router`, `reflections_router` (preserve `todos_router`)
    - In `backend/main.py`: remove imports and `include_router` calls for the 5 removed routers (preserve `todos_router` at `/api/todos`)
    - Preserve `tasks_router` registration at `/api/tasks`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10_

- [x] 5. Delete backend managers and update core `__init__.py`
  - [x] 5.1 Delete backend manager files (5 files) and clean up `__init__.py`
    - Delete `backend/core/section_manager.py`
    - Delete `backend/core/plan_item_manager.py`
    - Delete `backend/core/communication_manager.py`
    - Delete `backend/core/artifact_manager.py`
    - Delete `backend/core/reflection_manager.py`
    - Preserve `backend/core/todo_manager.py` (needed by ToDos Subsystem)
    - In `backend/core/__init__.py`: remove references to deleted managers (preserve `todo_manager` references)
    - Update comment in `backend/core/context_snapshot_cache.py` referencing `todo_manager` (line ~119)
    - Preserve `backend/core/task_manager.py`
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

- [x] 6. Delete backend schemas and update schemas `__init__.py`
  - [x] 6.1 Delete backend schema files (5 files) and clean up `__init__.py`
    - Delete `backend/schemas/section.py`
    - Delete `backend/schemas/plan_item.py`
    - Delete `backend/schemas/communication.py`
    - Delete `backend/schemas/artifact.py`
    - Delete `backend/schemas/reflection.py`
    - Preserve `backend/schemas/todo.py` (needed by ToDos Subsystem)
    - In `backend/schemas/__init__.py`: remove all imports from deleted schema files and their `__all__` entries (preserve `todo.py` imports). Remove `Priority` enum export if only used by deleted schemas.
    - Preserve `backend/schemas/task.py`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 7. Modify database schema, search manager, chat thread manager, and chat router
  - [x] 7.1 Remove Operating Loop table DDLs and classes from `backend/database/sqlite.py`
    - Remove `plan_items`, `communications`, `artifacts`, `artifact_tags`, `reflections` table DDLs from `SCHEMA` string (including all indexes)
    - Remove table class definitions: `SQLitePlanItemsTable`, `SQLiteCommunicationsTable`, `SQLiteArtifactsTable`, `SQLiteArtifactTagsTable`, `SQLiteReflectionsTable`
    - Remove instance variables and property accessors for the 5 removed tables from `SQLiteDatabase`
    - Preserve `todos` table DDL, `SQLiteToDosTable` class, `db.todos` accessor (needed by ToDos Subsystem)
    - Preserve `todo_id` column and FK in `chat_threads` DDL (needed by ToDos Subsystem)
    - Preserve `list_by_todo` method in `SQLiteChatThreadsTable` (needed by ToDos Subsystem)
    - Preserve `todo_id` in `bind_thread` method in `SQLiteChatThreadsTable` (needed by ToDos Subsystem)
    - Preserve `source_todo_id` column AND FK `REFERENCES todos(id)` in `tasks` DDL (needed by ToDos Subsystem)
    - Preserve `tasks` table, `SQLiteTasksTable`, and `WorkspaceScopedTable` base class
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 12.4, 12.5, 12.6, 12.7_

  - [x] 7.2 Clean up search manager
    - In `backend/core/search_manager.py`: remove `plan_item`, `communication`, `artifact`, `reflection` from `SEARCHABLE_ENTITY_TYPES`
    - Remove corresponding entries from `_ENTITY_TABLE_CONFIG`
    - Preserve `todo` in `SEARCHABLE_ENTITY_TYPES` and `_ENTITY_TABLE_CONFIG` (needed by ToDos Subsystem)
    - Keep `db.todos._get_connection()` as-is (todos table is preserved)
    - Preserve `task` and `thread` in `SEARCHABLE_ENTITY_TYPES`
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [x] 7.3 Verify todoId references are preserved in chat thread binding (backend)
    - In `backend/schemas/context.py`: verify `todo_id` field is preserved in `ThreadBindRequest` and `ThreadBindResponse` (no changes needed)
    - In `backend/schemas/chat_thread.py`: verify `todo_id` field is preserved in `ChatThreadCreate`, `ChatThreadUpdate`, `ChatThreadResponse` (no changes needed)
    - In `backend/core/chat_thread_manager.py`: verify `todo_id` handling is preserved in `bind_thread()`, `_resolve_workspace_from_todo()`, `create_thread()`, `list_threads()`, `update_thread()` (no changes needed)
    - In `backend/routers/chat.py`: verify `todo_id` references are preserved in `bind_thread` endpoint (no changes needed)
    - Preserve `task_id` field throughout
    - _Requirements: 12.8, 12.9, 12.10, 12.11_

- [x] 8. Update mock data script and documentation
  - [x] 8.1 Clean up mock data generation script
    - In `backend/scripts/generate_mock_data.py`: delete functions `_generate_plan_items`, `_generate_communications`, `_generate_artifacts`, `_generate_reflections`
    - Remove calls to deleted generator functions from `generate_mock_data()`
    - Preserve `_generate_tasks`
    - Preserve `_generate_todos` (needed by ToDos Subsystem)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 8.2 Update architecture documentation
    - In `.kiro/specs/ARCHITECTURE.md`: remove Operating Loop section references, update to reflect `Knowledge/` and `Projects/` zones only
    - In `.kiro/specs/AGENT_ARCHITECTURE_DEEP_DIVE.md`: remove Operating Loop section references
    - Delete entire `.kiro/specs/TODO-swarm-signals-ingestion-and-auto-reply-specs/` directory
    - _Requirements: 11.1, 11.2, 11.3_

- [x] 9. Regenerate seed.db and final verification
  - [x] 9.1 Regenerate seed database
    - Delete existing `seed.db` file
    - Run `python backend/scripts/generate_seed_db.py` to regenerate from updated SCHEMA DDL
    - Verify new `seed.db` contains only preserved tables (no `plan_items`, `communications`, `artifacts`, `artifact_tags`, `reflections`; `todos` and `tasks` must be present)
    - _Requirements: 4.10, 14.1, 14.5_

  - [ ]* 9.2 Write property test for fresh DB schema (Property 1 + Property 6)
    - Create `backend/tests/test_property_cleanup_db_schema.py`
    - **Property 1: Fresh database excludes removed tables and preserves required tables (including todos)**
    - **Property 6: Fresh database preserves todos table with todo_id columns in related tables**
    - **Validates: Requirements 4.1-4.7, 12.4-12.7, 13.4**

  - [ ]* 9.3 Write property test for ToDos subsystem preservation (Property 3)
    - Create `backend/tests/test_property_cleanup_todos_preserved.py`
    - **Property 3: ToDos subsystem preserved with full thread binding**
    - **Validates: Requirements 12.1-12.15**

  - [ ]* 9.4 Write property test for removed API routes (Property 5)
    - Create `backend/tests/test_property_cleanup_removed_routes.py`
    - **Property 5: Removed API routes return 404**
    - **Validates: Requirements 1.1-1.6**

- [x] 10. Final checkpoint - Full test suite verification
  - Run `cd backend && pytest` — all remaining tests must pass with zero failures
  - Run `cd desktop && npm test -- --run` — all remaining tests must pass with zero failures
  - Verify backend starts without import errors
  - Verify no references to deleted Operating Loop modules remain in preserved code
  - Ensure all tests pass, ask the user if questions arise
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Deletion order follows leaf-to-root: tests → frontend → routers → managers → schemas → DB/search/chat → mock data/docs → seed DB
- Import cleanups (`__init__.py`, `main.py`, `App.tsx`) happen in the same task as corresponding file deletions to avoid broken intermediate states
- The Tasks Subsystem (`tasks` router, manager, schema, DB table, frontend page/service) is preserved throughout all phases
- The ToDos Subsystem (`todos` router, manager, schema, DB table, frontend service/types, and all `todo_id`/`source_todo_id` cross-references) is preserved throughout all phases for the upcoming Swarm Radar feature
- Property tests validate correctness properties from the design document using `hypothesis`
