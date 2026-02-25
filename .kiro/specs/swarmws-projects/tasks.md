# Implementation Plan: SwarmWS Projects (Cadence 2 of 4)

## Overview

Extend the Cadence 1 foundation with full project lifecycle management: enriched `.project.json` metadata (description, priority, schema_version, version counter, update_history), schema migration engine, project CRUD REST API, and frontend TypeScript types + service layer (workspaceService, projectsService).

Implementation proceeds bottom-up: Pydantic schemas → schema migration module → SwarmWorkspaceManager extensions → API router → frontend types → frontend services → integration verification.

## Tasks

- [x] 1. Extend backend Pydantic schemas for enriched project metadata
  - [x] 1.1 Update `backend/schemas/project.py` with extended models
    - Add `ProjectHistoryEntry` model: version (int), timestamp (str), action (str), changes (dict), source (str)
    - Extend `ProjectMetadata` with: description (str, default ""), priority (Optional[str], default None), schema_version (str, default "1.0.0"), version (int, default 1), update_history (list[ProjectHistoryEntry], default [])
    - Extend `ProjectUpdate` with: description (Optional[str]), priority (Optional[str])
    - Add `ProjectResponse` with: path (str), context_l0 (Optional[str]), context_l1 (Optional[str])
    - Include module-level docstring per code documentation standards
    - _Requirements: 27.1, 27.2, 27.3, 27.4, 18.8_

- [x] 2. Create project schema migrations module
  - [x] 2.1 Create `backend/core/project_schema_migrations.py`
    - Define `CURRENT_SCHEMA_VERSION = "1.0.0"`
    - Define `MIGRATION_REGISTRY` as OrderedDict of `(from_ver, to_ver) → migration_fn`
    - Implement `register_migration(from_ver, to_ver)` decorator
    - Implement `migrate_if_needed(metadata)` → `(migrated_metadata, was_migrated)` tuple
    - Implement `get_migration_chain(from_version, to_version)` for introspection
    - Handle forward-compatibility: do not downgrade files with newer schema_version
    - Include module-level docstring per code documentation standards
    - _Requirements: 32.1, 32.2, 32.3, 32.4, 32.5, 32.6_

  - [ ]* 2.2 Write property test for schema migration correctness
    - **Property 10: Schema Migration Correctness**
    - Use Hypothesis to generate `ProjectMetadata` dicts at older schema versions, verify `migrate_if_needed()` produces valid metadata at CURRENT_SCHEMA_VERSION with correct defaults and `schema_migrated` history entry
    - Create `backend/tests/test_project_schema_migrations.py`
    - **Validates: Requirements 27.10, 32.2, 32.3, 32.6**

  - [x] 2.3 Write property test for schema version forward-compatibility
    - **Property 10 (cont.): Schema Version Forward-Compatibility**
    - Use Hypothesis to generate metadata dicts with schema_version > CURRENT_SCHEMA_VERSION, verify `migrate_if_needed()` returns unchanged with `was_migrated=False`
    - Add to `backend/tests/test_project_schema_migrations.py`
    - **Validates: Requirements 32.4**

- [x] 3. Checkpoint — Ensure schema and migration module tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Extend SwarmWorkspaceManager with enriched project methods
  - [x] 4.1 Add private helper methods to `backend/core/swarm_workspace_manager.py`
    - Implement `_read_project_metadata(project_dir)`: read `.project.json`, call `migrate_if_needed()`, write back if migrated, return metadata dict
    - Implement `_write_project_metadata(project_dir, metadata)`: serialize to JSON with 2-space indent
    - Implement `_find_project_dir(project_id, workspace_path)`: look up UUID in `_uuid_index` (in-memory dict), fall back to full Projects/ scan on index miss, raise ValueError if not found
    - Implement `_compute_action_type(changes)`: determine history action using priority mapping — if "name" in changes → renamed; elif "status" → status_changed; elif "tags" → tags_modified; elif "priority" → priority_changed; else → updated. First match wins.
    - Implement `_compute_changes_diff(old, new_updates)`: compute `{"field": {"from": old, "to": new}}` for changed fields only
    - Implement `_enforce_history_cap(metadata, cap=50)`: trim update_history to most recent `cap` entries in-place
    - Implement `_project_locks: dict[str, asyncio.Lock]` as class-level attribute in `__init__`
    - Implement `_get_project_lock(project_id)`: return or create `asyncio.Lock` for a project UUID
    - Implement `_rebuild_uuid_index(workspace_path)`: scan Projects/ subdirs, read `.project.json`, populate `_uuid_index: dict[str, Path]`
    - _Requirements: 27.5, 27.7, 27.8, 31.1, 31.2_

  - [x] 4.2 Extend `create_project()` for enriched metadata
    - Update `.project.json` creation to include: description (""), priority (None), schema_version ("1.0.0"), version (1), update_history with initial `created` entry
    - Use `_write_project_metadata()` for consistent serialization
    - Accept optional `source` parameter (default "user") for the history entry
    - Update `_uuid_index` after successful creation
    - _Requirements: 5.1, 5.5, 18.1, 27.1, 27.2, 27.3, 31.3_

  - [x] 4.3 Implement `update_project()` method
    - Find project dir by UUID via `_find_project_dir()`
    - Read metadata via `_read_project_metadata()`
    - Compute changes diff via `_compute_changes_diff()`
    - Determine action type via `_compute_action_type()`
    - Increment version, update `updated_at`
    - Append history entry with version, timestamp, action, changes, source
    - Enforce history cap via `_enforce_history_cap()`
    - Acquire per-project `asyncio.Lock` via `_get_project_lock()` before reading/writing `.project.json`
    - Follow atomic rename strategy: (1) write updated metadata to existing dir, (2) rename dir, (3) revert metadata on rename failure
    - If name changed: validate new name, rename project directory
    - Update `_uuid_index` after successful rename
    - Write back via `_write_project_metadata()`
    - _Requirements: 18.5, 27.4, 27.8, 31.1, 31.2, 31.4, 31.5, 31.8_

  - [x] 4.4 Extend `get_project()` and `list_projects()` with migration support
    - Update `get_project()` to use `_read_project_metadata()` (applies migration on read)
    - Acquire per-project `asyncio.Lock` before reading `.project.json`
    - Update `list_projects()` to use `_read_project_metadata()` for each project, sort by `created_at` descending
    - _Requirements: 18.3, 18.4, 27.7, 27.10_

  - [x] 4.5 Implement `get_project_by_name()` and `get_project_history()` methods
    - `get_project_by_name(name)`: scan Projects/ for matching directory name, read metadata, raise ValueError if not found
    - `get_project_history(project_id)`: find project, read metadata, return `update_history` array
    - _Requirements: 18.9, 31.6_

  - [x] 4.6 Write property test for project creation produces complete scaffold
    - **Property 1: Project Creation Produces Complete Scaffold**
    - Use Hypothesis to generate valid project names, verify all template items exist with correct defaults after `create_project()`
    - Verify `.project.json` has all required fields, `version=1`, `schema_version="1.0.0"`, exactly one `created` history entry
    - Create `backend/tests/test_project_crud_properties.py`
    - **Validates: Requirements 4.2, 4.3, 5.1, 5.5, 18.1, 27.1, 27.2, 27.3, 31.3, 32.1**

  - [x] 4.7 Write property test for project CRUD round-trip
    - **Property 5: Project CRUD Round-Trip**
    - Use Hypothesis to generate valid project names, test create→get(id), create→list includes it, get_by_name returns same, delete→get raises
    - Add to `backend/tests/test_project_crud_properties.py`
    - **Validates: Requirements 4.6, 18.3, 18.4, 18.6, 18.9, 31.6**

  - [ ]* 4.8 Write property test for project name validation
    - **Property 4: Project Name Validation**
    - Use Hypothesis to generate strings with unsafe chars, empty strings, strings >100 chars, reserved names — verify rejection
    - Test duplicate name creation raises conflict error
    - Add to `backend/tests/test_project_crud_properties.py`
    - **Validates: Requirements 18.2**

  - [x] 4.9 Fix `delete_project()` concurrency and UUID index usage
    - Acquire per-project `asyncio.Lock` before deleting to prevent races with concurrent reads/writes
    - Use `_find_project_dir()` (UUID index) instead of `_scan_all_project_metadata()` (full scan) for lookup
    - _Requirements: 18.10_

  - [x] 4.10 Fix `_get_project_lock()` TOCTOU race
    - Replace check-then-set pattern with `dict.setdefault(project_id, asyncio.Lock())` for atomic insertion
    - _Requirements: 18.10_

  - [x] 4.11 Fix `create_project()` name validation consistency
    - Replace simple `project_dir.exists()` check with `_validate_project_name()` call
    - Ensures case-insensitive collision detection, character validation, and reserved name checks — consistent with `update_project()`
    - _Requirements: 18.12_

  - [x] 4.12 Fix `_atomic_rename_project()` error diagnostics
    - Log original OS error at ERROR level before reverting metadata on rename failure
    - Chain original exception via `from exc` for full traceback preservation
    - _Requirements: 31.9_

- [x] 5. Checkpoint — Ensure extended manager methods and property tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement update history tracking and cap enforcement
  - [x] 6.1 Write unit tests for update history correctness
    - Test single update appends correct history entry with version, timestamp, action, changes, source
    - Test action type priority: rename > status_changed > tags_modified > priority_changed > updated
    - Test multi-field update records all changed fields in `changes` dict
    - Test source parameter propagation ("user", "agent", "system")
    - Test existing history entries remain unchanged after new update
    - Add to `backend/tests/test_project_history.py`
    - _Requirements: 27.4, 31.1, 31.2, 31.4, 31.5, 31.8_

  - [ ]* 6.2 Write property test for update history correctness
    - **Property 9: Update History Correctness**
    - Use Hypothesis to generate sequences of metadata updates with varying sources, verify after each update: (a) history grows by 1, (b) new entry version == project version, (c) action matches change type, (d) changes has before/after values, (e) source matches parameter, (f) prior entries unchanged
    - Add to `backend/tests/test_project_history.py`
    - **Validates: Requirements 27.4, 27.8, 31.1, 31.2, 31.4, 31.5, 31.8**

  - [x] 6.3 Write property test for update history cap enforcement
    - **Property 8: Update History Cap Enforcement**
    - Use Hypothesis to generate projects with 51–100 updates, verify history has exactly 50 entries, all are the most recent by version number
    - Add to `backend/tests/test_project_history.py`
    - **Validates: Requirements 27.5**

- [x] 7. Checkpoint — Ensure history tracking tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Create projects API router
  - [x] 8.1 Create `backend/routers/projects.py` with project CRUD endpoints
    - `POST /api/projects` → validate name, call `create_project()`, return 201 with ProjectResponse
    - `GET /api/projects` → call `list_projects()`, support optional `?name={name}` query param for name-based lookup via `get_project_by_name()`
    - `GET /api/projects/{project_id}` → call `get_project()`, return ProjectResponse or 404
    - `PUT /api/projects/{project_id}` → validate updates, call `update_project()`, return updated ProjectResponse
    - `DELETE /api/projects/{project_id}` → call `delete_project()`, return 204
    - `GET /api/projects/{project_id}/history` → call `get_project_history()`, return history array
    - Map ValueError to 404, duplicate name to 409, validation errors to 422, filesystem errors to 500
    - Include module-level docstring per code documentation standards
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8, 18.9, 31.6_

  - [x] 8.2 Register projects router in `backend/main.py`
    - Import `router` from `backend/routers/projects`
    - Add `app.include_router(router)` to the FastAPI app
    - _Requirements: 18.1_

  - [x] 8.3 Write integration tests for projects API endpoints
    - Test POST /api/projects returns 201 with full metadata
    - Test GET /api/projects returns list sorted by created_at desc
    - Test GET /api/projects?name={name} returns matching project
    - Test GET /api/projects/{id} returns project or 404
    - Test PUT /api/projects/{id} updates fields and tracks history
    - Test PUT /api/projects/{id} with name change renames directory
    - Test DELETE /api/projects/{id} returns 204, subsequent GET returns 404
    - Test GET /api/projects/{id}/history returns update_history array
    - Test error cases: duplicate name (409), invalid name (400), not found (404)
    - Create `backend/tests/test_projects_api.py`
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.8, 18.9, 31.6_

  - [x] 8.4 Fix `update_project` endpoint nullable field handling
    - Replace `model_dump()` + None filtering with `model_dump(exclude_unset=True)`
    - Correctly handles nullable fields: sending `{"priority": null}` clears it, omitting `priority` leaves it unchanged
    - _Requirements: 18.11_

- [x] 9. Checkpoint — Ensure API router tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Update frontend TypeScript type definitions
  - [x] 10.1 Update `desktop/src/types/index.ts` with enriched Project types
    - Extend `Project` interface with: description (string), priority (string | null), schemaVersion (string), version (number), contextL0 (string | undefined), contextL1 (string | undefined)
    - Add `ProjectHistoryEntry` interface: version (number), timestamp (string), action (string), changes (Record<string, {from: unknown, to: unknown}>), source ('user' | 'agent' | 'system' | 'migration')
    - Update `ProjectUpdateRequest` with: description? (string), priority? (string | null)
    - Ensure `WorkspaceConfig` interface exists: name, filePath, icon?, context?
    - Remove any remaining `SwarmWorkspace`, `SwarmWorkspaceCreateRequest`, `SwarmWorkspaceUpdateRequest` types if still present
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6_

- [x] 11. Create frontend service layer
  - [x] 11.1 Create `desktop/src/services/workspace.ts` — workspaceService
    - Implement `getConfig()`: GET /api/workspace → WorkspaceConfig
    - Implement `updateConfig(data)`: PUT /api/workspace → WorkspaceConfig
    - Include `toCamelCase()` mapping: file_path → filePath
    - Include `toSnakeCase()` mapping: filePath → file_path
    - Include `/** */` block comment per code documentation standards
    - _Requirements: 22.1, 22.4_

  - [x] 11.2 Create `desktop/src/services/projects.ts` — projectsService
    - Implement `list()`: GET /api/projects → Project[]
    - Implement `get(id)`: GET /api/projects/{id} → Project
    - Implement `create(data)`: POST /api/projects → Project
    - Implement `update(id, data)`: PUT /api/projects/{id} → Project
    - Implement `delete(id)`: DELETE /api/projects/{id} → void
    - Implement `getHistory(id)`: GET /api/projects/{id}/history → ProjectHistoryEntry[]
    - Include `toCamelCase()` mapping: created_at → createdAt, updated_at → updatedAt, schema_version → schemaVersion, context_l0 → contextL0, context_l1 → contextL1
    - Include `toSnakeCase()` for ProjectCreateRequest and ProjectUpdateRequest
    - Include `historyToCamelCase()` for ProjectHistoryEntry conversion
    - Include `/** */` block comment per code documentation standards
    - _Requirements: 22.2, 22.3, 22.4_

  - [x] 11.3 Update components consuming old swarmWorkspacesService
    - Search for remaining imports of `swarmWorkspacesService` across desktop/src/
    - Replace with `workspaceService` or `projectsService` as appropriate
    - Ensure no broken imports remain
    - _Requirements: 22.3_

  - [x] 11.4 Write property test for frontend case conversion round-trip
    - **Property 7: Frontend Case Conversion Round-Trip**
    - Use fast-check to generate snake_case project API response dicts, verify `toCamelCase()` maps all fields correctly (created_at → createdAt, schema_version → schemaVersion, etc.)
    - Create `desktop/src/services/__tests__/projects.property.test.ts`
    - **Validates: Requirements 21.6, 22.4**

  - [x] 11.5 Write unit tests for projectsService and workspaceService
    - Test projectsService.list() returns camelCase Project array
    - Test projectsService.create() sends snake_case, returns camelCase
    - Test projectsService.update() sends snake_case, returns camelCase
    - Test projectsService.getHistory() returns camelCase history entries
    - Test workspaceService.getConfig() returns camelCase WorkspaceConfig
    - Create `desktop/src/services/__tests__/projects.test.ts` and `desktop/src/services/__tests__/workspace.test.ts`
    - _Requirements: 22.2, 22.4_

- [x] 12. Checkpoint — Ensure frontend types, services, and tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Implement project rename with identity preservation
  - [x] 13.1 Write unit tests for project rename preserving identity
    - Test rename updates directory name on filesystem
    - Test rename updates `name` in `.project.json`
    - Test rename preserves `id` (UUID) unchanged
    - Test rename preserves `created_at` unchanged
    - Test rename increments `version`
    - Test rename updates `updated_at`
    - Test rename appends history entry with action="renamed" and changes={"name": {"from": old, "to": new}}
    - Test rename to duplicate name returns 409
    - Test rename to invalid name returns 400
    - Add to `backend/tests/test_project_rename.py`
    - _Requirements: 4.7, 18.5_

  - [x] 13.2 Write property test for project rename preserves identity
    - **Property 3: Project Rename Preserves Identity**
    - Use Hypothesis to generate project + valid new name pairs, verify UUID preserved, directory renamed, history entry correct
    - Add to `backend/tests/test_project_rename.py`
    - **Validates: Requirements 4.7, 18.5**

- [x] 14. Final integration verification
  - [x] 14.1 Verify backend end-to-end project lifecycle
    - Run full project lifecycle: create → get → update (status, tags, priority, description) → rename → get history → delete
    - Verify all history entries are correct after each operation
    - Verify schema migration applies on read for projects with older schema_version
    - Run `cd backend && pytest` to confirm all backend tests pass
    - _Requirements: 18.1, 18.3, 18.4, 18.5, 18.6, 27.4, 27.8, 31.1, 31.6, 32.2_

  - [x] 14.2 Verify frontend builds cleanly
    - Run `cd desktop && npm test -- --run` to confirm all frontend tests pass
    - Ensure no broken imports or references to removed modules
    - Verify toCamelCase/toSnakeCase conversions handle all new fields
    - _Requirements: 21.6, 22.4_

- [x] 15. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after each major phase
- Property tests validate universal correctness properties from the design document (Properties 1–10)
- Unit tests validate specific examples and edge cases
- Cadence 2 builds on Cadence 1's `SwarmWorkspaceManager`, `create_project()`, `delete_project()`, `get_project()`, `list_projects()` — extending rather than replacing
- The `swarm_workspaces` table removal and frontend dead code cleanup were completed in Cadence 1
- Knowledge/ CRUD is handled by Cadence 1 — no Knowledge-related tasks in Cadence 2
- Project `id` (UUID) is the primary API path parameter; `name` is a mutable display field used only for query-param lookup