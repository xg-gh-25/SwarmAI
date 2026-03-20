# Requirements: Systematic Codebase Cleanup

## Introduction

The SwarmAI codebase has accumulated ~25K lines of dead code, legacy artifacts, duplicate modules, and orphaned tests across 138K total lines. An audit identified five categories of cleanup targets:

1. **Orphaned test files** (~21,500 lines): 20+ test files in `backend/tests/` with no matching source module — leftover property tests and exploration tests from bugfix specs.
2. **Unused frontend components** (~695 lines): Production React components not imported anywhere in the codebase.
3. **Legacy migration files** (~1,006 lines): Migration scripts (`mcp_migration.py`, `skill_migration.py`, `project_schema_migrations.py`) and their tests. No external users exist — the app is under development with a single developer. These can be safely removed along with all call sites, provided no functional impact.
4. **Legacy/deprecated markers** (~20 files): Code annotated with DEPRECATED, LEGACY, or TODO-remove comments that should be cleaned up or formally documented.

Each cleanup step must be independently verifiable — the build and test suite must pass after every change. No existing functionality may break.

## Glossary

- **Cleanup_Tool**: The set of manual or scripted operations used to identify and remove dead code
- **Orphaned_Test**: A test file in `backend/tests/` that does not correspond to any current source module or feature
- **Legacy_Migration**: A migration file that was needed for schema/data transitions but is no longer required since the app has no external users and is under active development
- **Dead_Import**: An import statement referencing a symbol that no longer exists or is never used
- **Legacy_Marker**: A code comment containing DEPRECATED, LEGACY, or TODO-remove annotations

## Requirements

### Requirement 1: Remove Orphaned Backend Test Files

**User Story:** As a developer, I want orphaned test files removed from the test suite, so that the codebase is smaller, test discovery is faster, and new contributors are not confused by tests for nonexistent modules.

#### Acceptance Criteria

1. WHEN a test file in `backend/tests/` has no corresponding source module in `backend/core/`, `backend/routers/`, or `backend/database/` THEN the Cleanup_Tool SHALL flag the test file as an Orphaned_Test candidate.
2. WHEN an Orphaned_Test candidate is identified THEN the developer SHALL verify that no other test file imports from the candidate before deletion.
3. WHEN an Orphaned_Test is confirmed and deleted THEN the full test suite (`pytest`) SHALL pass without errors.
4. WHEN all Orphaned_Tests are removed THEN the total line reduction SHALL be approximately 21,500 lines across the identified files.
5. IF an Orphaned_Test candidate is found to be imported by an active test file THEN the Cleanup_Tool SHALL flag it for manual review instead of deletion.

### Requirement 2: Remove Unused Frontend Components

**User Story:** As a frontend developer, I want unused React components removed, so that the bundle is smaller and the component inventory reflects actual usage.

#### Acceptance Criteria

1. WHEN a production component file in `desktop/src/components/` is not imported by any other file in `desktop/src/` THEN the Cleanup_Tool SHALL flag the component as unused.
2. WHEN an unused component is confirmed THEN the developer SHALL remove the component file and any co-located test, style, or type files specific to that component.
3. WHEN unused components are removed THEN the TypeScript build (`npm run build`) SHALL complete without errors.
4. WHEN unused components are removed THEN the frontend test suite (`npm test -- --run`) SHALL pass without errors.
5. IF a component flagged as unused is referenced in a dynamic import, lazy load, or string-based lookup THEN the Cleanup_Tool SHALL flag it for manual review instead of deletion.

### Requirement 3: Remove Legacy Migration Files and Call Sites

**User Story:** As the sole developer of an app under active development with no external users, I want legacy migration files removed along with their call sites, so that the codebase does not carry dead migration logic for transitions that no longer apply.

#### Scope

Files to remove:
- `backend/core/mcp_migration.py` (137 lines) — one-time DB→file MCP config migration
- `backend/core/skill_migration.py` (209 lines) — one-time UUID→folder-name skill reference migration
- `backend/core/project_schema_migrations.py` (189 lines) — empty migration framework (CURRENT_SCHEMA_VERSION=1.0.0, no registered migrations)
- Associated test files: `test_project_schema_migrations.py`, `test_seed_database_migrations.py`

Call sites to clean up:
- `initialization_manager.py` — imports and calls `mcp_migration.migrate_if_needed()` and `skill_migration.migrate_skill_ids_to_allowed_skills()`
- `main.py` — imports and calls `mcp_migration.migrate_if_needed()`
- `swarm_workspace_manager.py` — imports `CURRENT_SCHEMA_VERSION` and `migrate_if_needed()` from `project_schema_migrations`

#### Acceptance Criteria

1. WHEN a Legacy_Migration file is deleted THEN all import statements and function calls referencing that migration SHALL also be removed from the codebase (`initialization_manager.py`, `main.py`, `swarm_workspace_manager.py`).
2. WHEN migration call sites are removed from the startup path THEN the application SHALL start successfully without errors or missing-module exceptions.
3. WHEN `project_schema_migrations.py` is removed THEN any code referencing `CURRENT_SCHEMA_VERSION` SHALL be updated to use an inline constant or removed if no longer needed.
4. WHEN all Legacy_Migration files and their test files are removed THEN the backend test suite (`pytest`) SHALL pass without errors.
5. THE developer SHALL verify that removing these migrations does not break the database initialization path — if the DB is created fresh (no legacy tables), startup must still work cleanly.

### Requirement 4: Audit and Resolve Legacy/Deprecated Markers

**User Story:** As a developer, I want legacy and deprecated code markers resolved, so that the codebase does not accumulate stale TODO comments and deprecated code paths that are never cleaned up.

#### Acceptance Criteria

1. WHEN a source file contains a DEPRECATED, LEGACY, or TODO-remove comment THEN the Cleanup_Tool SHALL catalog the marker with file path, line number, and surrounding context.
2. WHEN a Legacy_Marker references code that is no longer called anywhere in the codebase THEN the developer SHALL remove the dead code and the marker.
3. WHEN a Legacy_Marker references code that is still actively called THEN the developer SHALL either remove the marker (if the code is not actually deprecated) or create a follow-up task to complete the deprecation.
4. WHEN legacy code is removed THEN all imports, re-exports, and references to the removed symbols SHALL also be removed.
5. WHEN all Legacy_Marker resolutions are complete THEN both the backend test suite (`pytest`) and frontend build (`npm run build`) SHALL pass without errors.

### Requirement 5: Ensure Independent Verifiability of Each Cleanup Step

**User Story:** As a developer, I want each cleanup category to be an independent, atomic change, so that regressions can be bisected and individual cleanups can be reverted without affecting others.

#### Acceptance Criteria

1. THE cleanup work SHALL be organized into separate commits (or commit groups), one per cleanup category (orphaned tests, duplicate code, unused components, stale migrations, legacy markers).
2. WHEN a cleanup commit is applied THEN the full build (`npm run build:all`) and test suite (backend `pytest` + frontend `npm test -- --run`) SHALL pass.
3. WHEN a cleanup commit is reverted THEN the build and test suite SHALL still pass (no forward dependency between cleanup commits).
4. IF a cleanup in one category requires a change in another category THEN both changes SHALL be included in the same commit with a clear commit message explaining the cross-dependency.

### Requirement 6: Remove Dead Imports and Unused Symbols Post-Cleanup

**User Story:** As a developer, I want any dead imports or unreferenced symbols left behind after file deletions cleaned up, so that linters and type checkers report a clean codebase.

#### Acceptance Criteria

1. WHEN a file is deleted as part of cleanup THEN all import statements referencing that file across the codebase SHALL be removed.
2. WHEN a symbol (function, class, constant) is removed THEN all re-exports of that symbol from `__init__.py` or `index.ts` barrel files SHALL also be removed.
3. WHEN dead import cleanup is complete THEN the Python linter (`ruff check`) SHALL report no unused-import violations related to the deleted modules.
4. WHEN dead import cleanup is complete THEN the TypeScript compiler (`tsc --noEmit`) SHALL report no unresolved-import errors related to the deleted components.
