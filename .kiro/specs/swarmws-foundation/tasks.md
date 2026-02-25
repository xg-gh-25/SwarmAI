# Implementation Plan: SwarmWS Foundation (Cadence 1 of 4)

## Overview

Transform SwarmAI from a multi-workspace model to a single, persistent SwarmWS workspace. This cadence implements the foundational breaking changes: single workspace enforcement, new hierarchical folder structure, depth guardrails, context layering, system-managed item registry, backend data model simplification, SwarmWorkspaceManager refactor, sample data, dead code removal, system prompts, and initialization integrity.

Implementation proceeds bottom-up: data models and schemas first, then core manager logic, then API layer, then frontend cleanup, then tests and integrity verification.

## Tasks

- [x] 1. Create project metadata schema and workspace config models
  - [x] 1.1 Create `backend/schemas/project.py` with Pydantic models
    - Define `ProjectMetadata`, `ProjectCreate`, `ProjectUpdate`, `ProjectResponse` models
    - `ProjectMetadata`: id (UUID default), name (1-100 chars), status, tags, created_at, updated_at
    - `ProjectCreate`: name field with validation
    - `ProjectUpdate`: optional name, status, tags fields
    - Include module-level docstring per code documentation standards
    - _Requirements: 19.7, 22.4, 32.4_

  - [x] 1.2 Update `backend/schemas/workspace_config.py` for single-workspace model
    - Ensure `WorkspaceConfigResponse` model has: id, name, file_path, icon, context, created_at, updated_at
    - Add `WorkspaceConfigUpdate` model with optional icon and context fields
    - _Requirements: 19.2, 19.4, 19.5_

  - [ ]* 1.3 Write property test for ProjectMetadata serialization round-trip
    - **Property 8: Project Metadata Serialization Round-Trip**
    - Use Hypothesis to generate arbitrary `ProjectMetadata` objects, serialize to JSON, parse back, assert equivalence
    - Create `backend/tests/test_project_metadata.py`
    - **Validates: Requirements 32.4**

- [x] 2. Update database schema and migration
  - [x] 2.1 Add `workspace_config` table to `backend/database/sqlite.py`
    - Add `CREATE TABLE IF NOT EXISTS workspace_config` to `SCHEMA` string with columns: id (TEXT PK default 'swarmws'), name, file_path, icon, context, created_at, updated_at
    - Create `SQLiteWorkspaceConfigTable` class with `get_config()` and `update_config()` methods
    - Add `workspace_config` property to `SQLiteDatabase` class returning `SQLiteWorkspaceConfigTable` instance
    - _Requirements: 19.1, 19.2, 19.5_

  - [x] 2.2 Add database migration for `swarm_workspaces` → `workspace_config`
    - Add migration in `_run_migrations()` that:
      - Creates `workspace_config` table if not exists
      - Copies default workspace data from `swarm_workspaces` (if it exists) into `workspace_config`
      - Updates all `workspace_id` columns in scoped tables to `'swarmws'`
    - Migration must be idempotent (safe to run multiple times)
    - _Requirements: 1.5, 1.7, 19.1, 19.6_

  - [x] 2.3 Write property test for singleton workspace invariant
    - **Property 1: Singleton Workspace Invariant**
    - Use Hypothesis to generate sequences of API-like operations, verify `workspace_config` always has exactly one row
    - Create `backend/tests/test_workspace_singleton.py`
    - **Validates: Requirements 1.1, 1.3**

- [x] 3. Checkpoint — Ensure schema and migration tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Refactor SwarmWorkspaceManager — constants and system-managed registry
  - [x] 4.1 Update constants in `backend/core/swarm_workspace_manager.py`
    - Replace `FOLDER_STRUCTURE` with new hierarchical list: `Signals`, `Plan`, `Execute`, `Communicate`, `Reflection`, `Artifacts`, `Notebooks`, `Projects`
    - Add `SYSTEM_MANAGED_FOLDERS` set
    - Add `SYSTEM_MANAGED_ROOT_FILES` set: `system-prompts.md`, `context-L0.md`, `context-L1.md`
    - Add `SYSTEM_MANAGED_SECTION_FILES` set for section-level context files
    - Add `PROJECT_SYSTEM_FILES` and `PROJECT_SYSTEM_FOLDERS` sets
    - Add `DEPTH_LIMITS` dict: operating_loop=2, shared_knowledge=3, project_system=2, project_user=3
    - Update `DEFAULT_WORKSPACE_CONFIG` to use `{app_data_dir}/SwarmWS` path (remove `swarm-workspaces/` intermediate dir)
    - _Requirements: 2.1, 2.3, 10.1, 10.2, 10.3, 10.4, 22.1_

  - [x] 4.2 Implement `is_system_managed()` method
    - Check path against `SYSTEM_MANAGED_FOLDERS`, `SYSTEM_MANAGED_ROOT_FILES`, `SYSTEM_MANAGED_SECTION_FILES`
    - For project paths, check against `PROJECT_SYSTEM_FILES` and `PROJECT_SYSTEM_FOLDERS` patterns
    - Return `True` for system-managed items, `False` for user-managed items
    - _Requirements: 2.3, 8.7, 9.3, 9.4, 21.4, 21.5_

  - [x] 4.3 Implement `validate_depth()` method
    - Normalize target_path relative to workspace root
    - Identify section context from first path component (operating_loop, shared_knowledge, project_system, project_user)
    - Count depth relative to section root
    - Return `(is_valid: bool, error_message: str)` tuple
    - _Requirements: 5.3, 5.4, 10.1, 10.2, 10.3, 10.4, 10.5, 21.1, 21.2, 21.3, 22.8_

  - [ ]* 4.4 Write property test for system-managed registry correctness
    - **Property 5: System-Managed Registry Correctness**
    - Use Hypothesis to generate system paths and user paths, verify `is_system_managed()` returns correct boolean
    - Create `backend/tests/test_system_managed_registry.py`
    - **Validates: Requirements 2.3, 8.7, 9.3, 9.4**

  - [ ]* 4.5 Write property test for depth guardrail enforcement
    - **Property 6: Depth Guardrail Enforcement**
    - Use Hypothesis to generate paths at various depths in various sections, verify `validate_depth()` accepts/rejects correctly
    - Create `backend/tests/test_depth_guardrails.py`
    - **Validates: Requirements 5.3, 5.4, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 21.1, 21.2, 21.3, 22.8**

- [x] 5. Refactor SwarmWorkspaceManager — core methods
  - [x] 5.1 Remove multi-workspace methods from `SwarmWorkspaceManager`
    - Remove `archive()`, `unarchive()`, `delete()`, `list_non_archived()`, `list_all()`
    - Remove `_migrate_default_workspace_path()`
    - Remove `ensure_workspace_folders_exist()` (replaced by idempotent `ensure_default_workspace`)
    - _Requirements: 22.2, 27.1_

  - [x] 5.2 Add context file templates and sample data content
    - Define `CONTEXT_L0_TEMPLATE` and `CONTEXT_L1_TEMPLATE` string constants with `{section_name}` placeholder
    - Define `SYSTEM_PROMPTS_TEMPLATE` default content
    - Define sample data content for Operating Loop READMEs, sample artifact, sample notebook, sample project
    - _Requirements: 8.4, 8.5, 25.1, 25.2, 25.3, 25.4, 25.5, 25.6, 30.2_

  - [x] 5.3 Rewrite `create_folder_structure()` for new hierarchy
    - Create all folders from `FOLDER_STRUCTURE`
    - Create root-level system files: `system-prompts.md`, `context-L0.md`, `context-L1.md`
    - Create section-level context files in `Artifacts/`, `Notebooks/`, `Projects/`
    - Only create files/dirs that don't already exist (no overwrite)
    - _Requirements: 2.1, 2.2, 2.4, 8.1, 8.2, 30.1_

  - [x] 5.4 Rewrite `ensure_default_workspace()` for single-workspace model
    - Check `workspace_config` table for existing row
    - If missing: insert row, call `create_folder_structure()`, populate sample data
    - If present: call `verify_integrity()` to check/recreate missing system items
    - Return workspace config dict
    - Skip sample data if workspace already has user content (check for non-system files)
    - _Requirements: 1.1, 1.2, 2.5, 22.3, 25.7, 31.1, 31.2, 31.5_

  - [x] 5.5 Implement `verify_integrity()` method
    - Walk all system-managed items (folders, root files, section context files)
    - For each missing item, recreate with default content
    - Log each recreated item
    - Return list of recreated item paths
    - Do NOT overwrite existing files
    - _Requirements: 31.1, 31.2, 31.3, 31.4, 32.2_

  - [ ]* 5.6 Write property test for initialization produces complete structure
    - **Property 2: Initialization Produces Complete Structure**
    - Use Hypothesis with `tmp_path`, run `ensure_default_workspace()` on empty dir, verify all system items exist
    - Create `backend/tests/test_swarm_workspace_manager.py` (replace existing file)
    - **Validates: Requirements 2.1, 2.2, 5.1, 8.1, 8.2, 22.3, 30.1, 31.1**

  - [x] 5.7 Write property test for initialization idempotence
    - **Property 3: Initialization Idempotence**
    - Use Hypothesis to generate workspace states with random user files, run init twice, verify equivalence
    - Add to `backend/tests/test_swarm_workspace_manager.py`
    - **Validates: Requirements 2.5, 25.7, 31.2, 32.1, 32.2, 32.3**

- [x] 6. Implement project CRUD methods in SwarmWorkspaceManager
  - [x] 6.1 Implement `create_project()` method
    - Generate UUID, create `Projects/{project_name}/` directory
    - Create `.project.json` with `ProjectMetadata` (id, name, status="active", tags=[], timestamps)
    - Create system files: `context-L0.md`, `context-L1.md`, `instructions.md`
    - Create system folders: `chats/`, `research/`, `reports/`
    - Return project metadata dict
    - _Requirements: 22.4_

  - [x] 6.2 Implement `delete_project()` method
    - Scan `Projects/` for `.project.json` files to find matching UUID
    - Remove entire project directory (shutil.rmtree)
    - Return True if deleted, raise ValueError if not found
    - _Requirements: 22.5_

  - [x] 6.3 Implement `get_project()` and `list_projects()` methods
    - `get_project(project_id)`: scan Projects/ dirs, read `.project.json`, match UUID, return metadata
    - `list_projects()`: scan all Projects/ subdirs with `.project.json`, return list of metadata dicts
    - _Requirements: 22.6, 22.7_

  - [ ]* 6.4 Write property test for project CRUD round-trip
    - **Property 7: Project CRUD Round-Trip**
    - Use Hypothesis to generate valid project names, test create→get, create→list, delete→get-raises
    - Create `backend/tests/test_project_crud.py`
    - **Validates: Requirements 8.3, 22.4, 22.5, 22.6, 22.7**

- [x] 7. Checkpoint — Ensure manager refactor and property tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Create new backend API endpoints
  - [x] 8.1 Create `backend/routers/workspace_api.py` with workspace and project endpoints
    - `GET /api/workspace` → return singleton workspace config from DB
    - `PUT /api/workspace` → update workspace context/icon
    - `GET /api/projects` → call `list_projects()`
    - `POST /api/projects` → call `create_project()`, return 201
    - `GET /api/projects/{id}` → call `get_project()`, return 404 if not found
    - `PUT /api/projects/{id}` → update `.project.json` fields, return updated metadata
    - `DELETE /api/projects/{id}` → call `delete_project()`, return 204
    - Include module-level docstring per code documentation standards
    - _Requirements: 19.3, 19.4, 19.5, 19.7_

  - [x] 8.2 Add folder CRUD endpoints to `backend/routers/workspace_api.py`
    - `POST /api/workspace/folders` → validate depth via `validate_depth()`, check not system-managed, create dir, return 201
    - `DELETE /api/workspace/folders` → check `is_system_managed()` returns False, delete dir/file, return 204; return 403 if system-managed
    - `PUT /api/workspace/rename` → check `is_system_managed()` returns False for old_path, rename, return 200; return 403 if system-managed
    - Return HTTP 400 for depth violations, HTTP 403 for system-managed violations, HTTP 404 for not found
    - Validate path traversal (reject `..` in paths)
    - _Requirements: 10.5, 10.6, 21.1, 21.2, 21.3, 21.4, 21.5_

  - [x] 8.3 Register new router in `backend/main.py`
    - Import `workspace_api_router` from `backend/routers/workspace_api`
    - Add `app.include_router(workspace_api_router, prefix="/api", tags=["workspace"])`
    - _Requirements: 19.3, 19.4_

  - [ ]* 8.4 Write property test for system-managed item protection via API
    - **Property 4: System-Managed Item Protection**
    - Use Hypothesis to generate system-managed paths, verify delete/rename returns 403
    - Create `backend/tests/test_workspace_api.py`
    - **Validates: Requirements 1.3, 5.5, 8.6, 9.3, 9.4, 21.4, 21.5, 30.4**

- [x] 9. Update initialization manager
  - [x] 9.1 Update `backend/core/initialization_manager.py` for single-workspace model
    - Update `run_quick_validation()` to check `workspace_config` table instead of `swarm_workspaces`
    - Update `run_full_initialization()` to use refactored `ensure_default_workspace()`
    - Update `get_cached_workspace_path()` to read from `workspace_config`
    - Ensure workspace integrity verification completes before returning
    - _Requirements: 31.1, 31.4, 31.5_

  - [x] 9.2 Write unit tests for initialization flow
    - Test first-launch creates full structure with sample data
    - Test subsequent launch verifies integrity without overwriting
    - Test missing system items are recreated
    - Create `backend/tests/test_initialization.py`
    - _Requirements: 31.1, 31.2, 31.3, 31.4, 31.5_

- [x] 10. Checkpoint — Ensure backend API and initialization tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Dead code removal — Backend
  - [x] 11.1 Remove `backend/routers/swarm_workspaces.py`
    - Delete the entire file
    - Remove router import and registration from `backend/main.py` (the `swarm_workspaces_router` line)
    - _Requirements: 1.4, 19.3, 27.1_

  - [x] 11.2 Remove `backend/schemas/swarm_workspace.py`
    - Delete the entire file
    - Update `backend/schemas/__init__.py` if it re-exports these models
    - _Requirements: 27.1_

  - [x] 11.3 Remove `SQLiteSwarmWorkspacesTable` from `backend/database/sqlite.py`
    - Remove the `SQLiteSwarmWorkspacesTable` class
    - Remove the `swarm_workspaces` property from `SQLiteDatabase`
    - Keep the `swarm_workspaces` table in SCHEMA for migration compatibility (migration reads from it)
    - _Requirements: 1.5, 19.1, 27.1_

  - [x] 11.4 Remove or update obsolete backend test files
    - Remove `backend/tests/test_swarm_workspaces_router.py`
    - Remove `backend/tests/test_swarm_workspace_properties.py`
    - Remove `backend/tests/test_property_swarmws_deletion.py`
    - Remove `backend/tests/test_property_custom_workspace_deletion.py`
    - Remove `backend/tests/test_property_swarmws_first.py`
    - Update `backend/tests/test_property_workspace_migration.py` if it references removed code
    - Update `backend/tests/test_archive_router.py` if it references workspace archive endpoints
    - Update any other test files that import from removed modules
    - _Requirements: 27.3, 27.6_

  - [x] 11.5 Update all backend import statements referencing removed modules
    - Search for imports of `swarm_workspaces`, `SwarmWorkspaceCreate`, `SwarmWorkspaceUpdate`, `SwarmWorkspaceResponse`
    - Search for imports of `SQLiteSwarmWorkspacesTable`
    - Fix or remove all broken references
    - _Requirements: 27.5_

- [x] 12. Dead code removal — Frontend
  - [x] 12.1 Remove `desktop/src/services/swarmWorkspaces.ts` and `desktop/src/services/swarmWorkspaces.test.ts`
    - Delete both files
    - _Requirements: 27.4_

  - [x] 12.2 Remove `desktop/src/pages/WorkspacesPage.tsx`
    - Delete the file
    - Remove the `/workspaces` route from `desktop/src/App.tsx`
    - _Requirements: 1.6, 27.2_

  - [x] 12.3 Update `desktop/src/components/modals/WorkspacesModal.tsx`
    - Remove or repurpose this component (it wraps the deleted WorkspacesPage)
    - Remove the import of WorkspacesPage
    - Update `desktop/src/components/modals/WorkspacesModal.property.test.tsx` accordingly
    - _Requirements: 1.6, 27.2_

  - [x] 12.4 Update `desktop/src/components/chat/WorkspaceSelector.tsx`
    - Remove import of `swarmWorkspacesService`
    - Refactor to work with singleton workspace (no multi-workspace dropdown)
    - _Requirements: 1.6, 27.2_

  - [x] 12.5 Update `desktop/src/components/workspace-explorer/WorkspaceExplorer.tsx`
    - Remove import of `swarmWorkspacesService`
    - Remove multi-workspace listing, archive/unarchive/delete logic
    - Remove `showArchived` toggle and workspace dropdown
    - Simplify to always show the single SwarmWS workspace
    - _Requirements: 1.6, 27.2_

  - [x] 12.6 Update `desktop/src/components/workspace-explorer/AddWorkspaceDialog.tsx`
    - Remove or repurpose — no longer needed for creating new workspaces
    - Remove import of `swarmWorkspacesService`
    - _Requirements: 1.6, 27.2_

  - [x] 12.7 Update `desktop/src/components/modals/ConvertToTaskModal.tsx`
    - Remove import of `swarmWorkspacesService`
    - Update workspace fetching to use singleton workspace config
    - _Requirements: 27.2, 27.5_

  - [x] 12.8 Update `desktop/src/hooks/useWorkspaceSelection.ts`
    - Remove import of `swarmWorkspacesService`
    - Refactor to return singleton workspace config instead of multi-workspace selection
    - _Requirements: 27.2, 27.5_

  - [x] 12.9 Update frontend test files that mock `swarmWorkspacesService`
    - Update `desktop/src/pages/__tests__/SectionPages.test.tsx`
    - Update `desktop/src/pages/__tests__/WorkspaceScopedRouting.test.tsx`
    - Update `desktop/src/components/layout/ThreeColumnLayout.test.tsx`
    - Update `desktop/src/components/layout/ThreeColumnLayout.property.test.tsx`
    - Update `desktop/src/components/workspace-explorer/WorkspaceExplorer.test.tsx`
    - Remove mocks of `swarmWorkspacesService`, update to match new singleton model
    - _Requirements: 27.6_

- [x] 13. Update frontend types and create new service
  - [x] 13.1 Update `desktop/src/types/index.ts`
    - Remove `SwarmWorkspace`, `SwarmWorkspaceCreateRequest`, `SwarmWorkspaceUpdateRequest` interfaces
    - Add `WorkspaceConfig` interface: name, filePath, icon?, context?
    - Add `Project` interface: id, name, status, tags, createdAt, updatedAt
    - Add `ProjectCreateRequest` interface: name
    - Add `ProjectUpdateRequest` interface: name?, status?, tags?
    - _Requirements: 19.4, 19.5, 19.7_

  - [x] 13.2 Create `desktop/src/services/workspace.ts` updates (or new service file)
    - Add workspace config service functions: `getConfig()`, `updateConfig()`
    - Add project service functions: `listProjects()`, `createProject()`, `getProject()`, `updateProject()`, `deleteProject()`
    - Add folder CRUD functions: `createFolder()`, `deleteFolder()`, `renameItem()`
    - Include `toCamelCase()` and `toSnakeCase()` conversion functions per API naming convention
    - Include `/** */` block comment per code documentation standards
    - _Requirements: 19.4, 19.5, 19.7_

- [x] 14. Checkpoint — Ensure all dead code removal is clean and tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 15. Final integration verification
  - [x] 15.1 Verify end-to-end initialization flow
    - Ensure `run_full_initialization()` creates complete SwarmWS structure on fresh start
    - Ensure re-initialization preserves existing content and recreates missing system items
    - Verify sample data is populated on first launch only
    - Run `cd backend && pytest` to confirm all backend tests pass
    - _Requirements: 31.1, 31.2, 31.3, 31.4, 31.5, 32.1, 32.2, 32.3_

  - [x] 15.2 Verify frontend builds cleanly
    - Run `cd desktop && npm test -- --run` to confirm all frontend tests pass
    - Ensure no broken imports or references to removed modules
    - _Requirements: 27.5, 27.6_

- [x] 16. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 17. Delta: Add Memory/ subfolder support to workspace structure
  - [x] 17.1 Update `FOLDER_STRUCTURE` in `backend/core/swarm_workspace_manager.py`
    - Add `Knowledge/Memory/` to the `FOLDER_STRUCTURE` list
    - Add `Knowledge/Memory/` to `SYSTEM_MANAGED_FOLDERS` set
    - _Requirements: 2.1, 2.4, 3.2, 20.1_

  - [x] 17.2 Update `create_folder_structure()` to create `Knowledge/Memory/` directory
    - Ensure `Knowledge/Memory/` is created alongside `Knowledge Base/` and `Notes/`
    - Only create if it doesn't already exist (idempotent)
    - _Requirements: 2.1, 3.2, 29.2, 30.1_

  - [x] 17.3 Update `is_system_managed()` to recognize `Knowledge/Memory/` as system-managed
    - Add `Knowledge/Memory/` path to system-managed checks
    - Ensure deletion and rename are blocked for `Memory/` folder
    - _Requirements: 3.6, 3.7, 7.3, 7.4_

  - [x] 17.4 Update `verify_integrity()` to check and recreate `Knowledge/Memory/` if missing
    - Add `Knowledge/Memory/` to the integrity verification walk
    - Recreate with default content if missing, log the action
    - _Requirements: 29.1, 29.2, 29.3_

  - [x] 17.5 Add sample Memory/ content to onboarding sample data
    - Define sample memory item content (e.g., a user preference or recurring theme)
    - Create sample file in `Knowledge/Memory/` during first-time initialization
    - _Requirements: 23.4_

  - [x] 17.6 Update existing tests to include Memory/ folder assertions
    - Update `backend/tests/test_swarm_workspace_manager.py` to verify `Knowledge/Memory/` exists after init
    - Update `backend/tests/test_initialization.py` to verify Memory/ is created and integrity-checked
    - Update any system-managed registry tests to include Memory/ paths
    - _Requirements: 2.4, 3.2, 29.1, 30.1_

- [x] 18. Delta: Implement Legacy Data Cleanup (clean-slate approach)
  - [x] 18.1 Update database migration in `backend/database/sqlite.py`
    - In `_run_migrations()`, add logic to detect existing `swarm_workspaces` table
    - If detected: DROP the `swarm_workspaces` table entirely
    - Remove associated legacy workspace directories from filesystem (scan for non-SwarmWS workspace dirs)
    - Clear `workspace_id` fields in chat thread records (set to NULL)
    - Log all cleanup actions
    - _Requirements: 24.1, 24.2, 24.3_

  - [x] 18.2 Update `ensure_default_workspace()` to call legacy cleanup before fresh init
    - Ensure legacy data cleanup runs BEFORE workspace structure initialization
    - After cleanup, initialize fresh SwarmWS structure per Requirement 2
    - _Requirements: 24.4, 1.7_

  - [x] 18.3 Remove migration-based data preservation logic
    - Remove any code that copies data from `swarm_workspaces` into `workspace_config` (replaced by clean-slate)
    - Remove any code that converts old workspaces into Projects (no longer needed)
    - Simplify migration to: detect legacy → drop → init fresh
    - _Requirements: 24.1, 1.7_

  - [x] 18.4 Write tests for legacy data cleanup
    - Test: when `swarm_workspaces` table exists, it gets dropped after migration
    - Test: when chat threads have `workspace_id`, they get cleared to NULL
    - Test: after cleanup, fresh SwarmWS structure is initialized correctly
    - Test: cleanup is idempotent (running twice doesn't error)
    - Add to `backend/tests/test_initialization.py` or create `backend/tests/test_legacy_cleanup.py`
    - _Requirements: 24.1, 24.2, 24.3, 24.4_

- [x] 19. Delta checkpoint — Verify Memory/ and Legacy Cleanup changes
  - Run `cd backend && pytest` to confirm all backend tests pass
  - Run `cd desktop && npm test -- --run` to confirm frontend tests still pass
  - Verify `Knowledge/Memory/` appears in initialized workspace structure
  - Verify legacy data cleanup works correctly on fresh and existing databases
  - _Requirements: 2.1, 3.2, 24.1, 24.4, 29.1, 30.1_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation after each major phase
- Property tests validate universal correctness properties from the design document (Properties 1-8)
- Unit tests validate specific examples and edge cases
- The `swarm_workspaces` table DDL is kept in SCHEMA for migration compatibility — the migration reads from it to seed `workspace_config`
- Frontend dead code removal (task 12) has many sub-tasks due to widespread `swarmWorkspacesService` usage across components
- The `WorkspaceScopedTable` base class continues to work with `workspace_id = 'swarmws'` — full column removal is deferred to a future cadence
- Tasks 17-19 are delta tasks added after the initial implementation to align with parent spec changes: Memory/ subfolder support and Legacy Data Cleanup (clean-slate approach replacing data migration)
