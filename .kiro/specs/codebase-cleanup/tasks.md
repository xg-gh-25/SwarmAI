# Implementation Plan: Systematic Codebase Cleanup

## Overview

Incremental removal of ~23K lines of dead code from the SwarmAI codebase, organized into five sequential cleanup categories. Each category is independently verifiable (build + tests pass after each). Deletion order: orphaned tests → unused frontend components → legacy migrations → legacy markers → dead import sweep.

## Tasks

- [ ] 1. Remove orphaned backend test files
  - [ ] 1.1 Identify and delete orphaned test files in `backend/tests/`
    - For each `test_*.py` file, strip the `test_` prefix and `_properties`/`_preservation`/`_exploration` suffixes, then check if a matching source module exists in `backend/core/`, `backend/routers/`, `backend/database/`, or `backend/schemas/`
    - Before deleting each candidate, grep all remaining test files for `from tests.<candidate>` or `import tests.<candidate>` to confirm no cross-imports exist
    - If a candidate IS imported by another active test, flag it for manual review instead of deleting
    - Delete all confirmed orphaned test files (property tests from past bugfix specs, exploration/seed tests, one-off investigation tests)
    - _Requirements: 1.1, 1.2, 1.4, 1.5_

  - [ ]* 1.2 Write property test for orphan detection logic
    - **Property 1: Orphan test detection correctness**
    - Test that for any test file name, set of source module names, and import graph, the detector flags a test as orphaned iff no source module matches AND no other active test imports it
    - Use `hypothesis` to generate random file name / source module / import graph combinations
    - **Validates: Requirements 1.1, 1.2, 1.5**

- [ ] 2. Checkpoint — Verify backend after orphaned test removal
  - Ensure `cd backend && pytest` passes with no errors
  - Ensure no test file references a deleted file
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 3. Remove unused frontend components
  - [ ] 3.1 Identify and delete unused React components in `desktop/src/components/`
    - For each component file, grep `desktop/src/` (excluding the file itself) for static imports of the component name
    - Also search for dynamic imports (`React.lazy`, dynamic `import()`) to avoid false positives
    - If a component has zero import hits across all mechanisms, delete the component file and any co-located test, style, or type files
    - If a component is referenced via dynamic import or string-based lookup, flag for manual review
    - _Requirements: 2.1, 2.2, 2.3, 2.5_

  - [ ]* 3.2 Write property test for unused component detection logic
    - **Property 2: Unused component detection correctness**
    - Test that for any component file and import graph (static + dynamic), the detector flags unused iff zero files reference it through any import mechanism
    - Use `fast-check` to generate component/import graph combinations
    - **Validates: Requirements 2.1, 2.5**

- [ ] 4. Checkpoint — Verify frontend after unused component removal
  - Ensure `cd desktop && npm run build` completes without errors
  - Ensure `cd desktop && npm test -- --run` passes
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Remove legacy migration files and call sites
  - [ ] 5.1 Delete migration source files and their test files
    - Delete `backend/core/mcp_migration.py`, `backend/core/skill_migration.py`, `backend/core/project_schema_migrations.py`
    - Delete associated test files: `test_project_schema_migrations.py`, `test_seed_database_migrations.py`
    - _Requirements: 3.1, 3.4_

  - [ ] 5.2 Clean up migration call sites in `initialization_manager.py`
    - Remove the `try/except` block that imports and calls `mcp_migration.migrate_if_needed()`
    - Remove the `try/except` block that imports and calls `skill_migration.migrate_skill_ids_to_allowed_skills()`
    - Remove any now-unused imports
    - _Requirements: 3.1, 3.2_

  - [ ] 5.3 Clean up migration call site in `main.py`
    - Remove the `try/except` block that imports and calls `mcp_migration.migrate_if_needed()`
    - Remove any now-unused imports
    - _Requirements: 3.1, 3.2_

  - [ ] 5.4 Clean up migration references in `swarm_workspace_manager.py`
    - Replace `from core.project_schema_migrations import CURRENT_SCHEMA_VERSION, migrate_if_needed` with an inline `CURRENT_SCHEMA_VERSION = "1.0.0"` constant
    - Remove all `migrate_if_needed()` calls (in `_read_project_metadata()` and elsewhere)
    - Verify the inlined constant is used correctly in all existing references
    - _Requirements: 3.1, 3.3_

  - [ ]* 5.5 Write property test for reference completeness after migration deletion
    - **Property 3: Reference completeness after deletion**
    - Test that for any deleted file or symbol, zero import statements, function calls, or re-exports referencing it remain in the codebase
    - Use `hypothesis` to generate deletion sets and verify no dangling references
    - **Validates: Requirements 3.1, 6.1, 6.2**

- [ ] 6. Checkpoint — Verify backend after migration removal
  - Ensure `cd backend && pytest` passes with no errors
  - Verify the application startup path works without migration imports (no missing-module exceptions)
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Audit and resolve legacy/deprecated markers
  - [ ] 7.1 Catalog all DEPRECATED, LEGACY, and TODO-remove markers across the codebase
    - Grep `backend/` and `desktop/src/` for comments matching DEPRECATED, LEGACY, or TODO-remove (case-insensitive)
    - Record each marker with file path, line number, and surrounding context
    - _Requirements: 4.1_

  - [ ] 7.2 Resolve markers for dead code
    - For each marker referencing code not called anywhere: remove the dead code, the marker, and all imports/re-exports of removed symbols
    - Specific targets from design audit:
      - `desktop/src/types/index.ts`: `@deprecated FileAttachment`, `@deprecated FILE_SIZE_LIMITS` — remove if not imported
      - `desktop/src/components/workspace-explorer/FileTreeNode.tsx`: `@deprecated` module — remove if not imported
      - `desktop/src/components/workspace-explorer/toFileTreeItem.ts`: deprecated FileTreeItem reference — remove if not imported
      - `desktop/src/pages/chat/components/MergedToolBlock.tsx`: `@deprecated isStreaming` — remove field if unused
    - _Requirements: 4.2, 4.4_

  - [ ] 7.3 Resolve markers for active code
    - For each marker referencing code that IS still actively called: remove the misleading marker (if code is not actually deprecated) or document a follow-up task
    - Specific targets: `backend/database/sqlite.py` "Legacy Data Cleanup" block — active startup code, clarify or remove misleading label
    - Keep `backend/tests/test_prompt_builder_properties.py` "deprecated no-op" merge test — tests active code path
    - _Requirements: 4.3_

  - [ ]* 7.4 Write property test for legacy marker detection completeness
    - **Property 4: Legacy marker detection completeness**
    - Test that for any source file containing DEPRECATED, LEGACY, or TODO-remove comments, the cataloger includes it with correct file path, line number, and context
    - Use `hypothesis` to generate file contents with various marker patterns
    - **Validates: Requirements 4.1**

- [ ] 8. Checkpoint — Verify after legacy marker resolution
  - Ensure `cd backend && pytest` passes
  - Ensure `cd desktop && npm run build` completes without errors
  - Ensure `cd desktop && npm test -- --run` passes
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Remove dead imports and unused symbols post-cleanup
  - [ ] 9.1 Sweep and fix Python dead imports
    - Run `ruff check --select F401` across `backend/` to find unused imports left behind by file deletions
    - Remove all flagged unused imports
    - Check `__init__.py` files for re-exports of deleted symbols and remove them
    - _Requirements: 6.1, 6.2, 6.3_

  - [ ] 9.2 Sweep and fix TypeScript dead imports
    - Run `tsc --noEmit` across `desktop/src/` to find unresolved imports left behind by component/file deletions
    - Remove all flagged unresolved imports
    - Check `index.ts` barrel files for re-exports of deleted symbols and remove them
    - _Requirements: 6.1, 6.2, 6.4_

- [ ] 10. Final checkpoint — Full build and test verification
  - Ensure `cd backend && pytest` passes
  - Ensure `cd desktop && npm run build` completes without errors
  - Ensure `cd desktop && npm test -- --run` passes
  - Ensure `cd backend && ruff check --select F401` reports no unused-import violations
  - Ensure `cd desktop && npx tsc --noEmit` reports no unresolved-import errors
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster execution
- Each cleanup category is independently verifiable — build + tests must pass after each
- Deletion order is intentional: orphaned tests (largest, zero coupling) → frontend components → migrations → markers → dead imports (final sweep)
- Requirement 5 (independent verifiability) is enforced by the checkpoint tasks after each category
- Property tests validate universal detection/classification logic; unit tests validate specific post-conditions
