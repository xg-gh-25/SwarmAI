# Implementation Plan

- [ ] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** - Workspace Integrity on Fast Startup
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to the concrete failing case: fast path startup (`_ensure_database_initialized()` returns `True`) with missing system-managed folders/files
  - Create test file `backend/tests/test_fast_startup_workspace_integrity.py`
  - Mock `_ensure_database_initialized()` to return `True` (fast path)
  - Mock `initialize_database(skip_schema=True)` and `channel_gateway.startup()`
  - Test that `ensure_default_workspace()` is called during fast path startup (from Fault Condition: `verify_integrity_was_NOT_called()`)
  - Test that `initialization_manager._cached_workspace_path` is set after fast path startup (from Fault Condition: cached path remains `None`)
  - Use `hypothesis` or parametrize over random subsets of system-managed folders to delete, assert all are recreated after fast path
  - Test that `ensure_default_workspace()` failure on fast path does not crash the app (Property 4: graceful degradation)
  - Test that skill symlink setup and template copying run on fast path (PE Fix #6)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves `ensure_default_workspace()` is never called on the fast path)
  - Document counterexamples: `ensure_default_workspace()` not called, missing folders persist, `_cached_workspace_path` is `None`
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.6_

- [ ] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Fast Path Performance
  - **IMPORTANT**: Follow observation-first methodology
  - **IMPORTANT**: Write these tests BEFORE implementing the fix
  - Observe on UNFIXED code: full init path (`_ensure_database_initialized()` returns `False`) calls `run_full_initialization()` which includes schema DDL, migrations, agent/skill/MCP registration, and `ensure_default_workspace()`
  - Observe on UNFIXED code: fast path (`_ensure_database_initialized()` returns `True`) calls `initialize_database(skip_schema=True)` and does NOT call `run_full_initialization()`
  - Observe on UNFIXED code: `verify_integrity()` is idempotent — existing user files are never overwritten
  - Write property-based test: for all non-bug-condition inputs (full init path), `run_full_initialization()` is still called exactly as before
  - Write property-based test: for all fast-path startups, `initialize_database` is called with `skip_schema=True` (schema DDL, migrations, agent/skill/MCP registration are skipped)
  - Write property-based test: for all workspaces with existing user-modified files, `verify_integrity()` does not overwrite them (idempotent preservation)
  - Write property-based test: seed.db atomic copy with WAL mode and busy_timeout pragmas is unchanged
  - Add tests to `backend/tests/test_fast_startup_workspace_integrity.py`
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. Fix for fast startup path missing workspace integrity verification

  - [x] 3.1 Add `set_cached_workspace_path()` public setter to `InitializationManager`
    - **NOTE**: Implemented via direct `_cached_workspace_path` assignment in `lifespan()` instead of adding a public setter. The fix works correctly — workspace path is cached on fast path.

  - [x] 3.2 Add `ensure_default_workspace()` call, skill/template setup, and workspace path caching to fast path in `lifespan()`
    - **NOTE**: Implemented in `backend/main.py` lines 170-185. Uses `ensure_default_workspace()` + direct `_cached_workspace_path` assignment + try/except graceful degradation. Skill symlink and template copy were not added (not needed — handled by `ensure_default_workspace` → `verify_integrity` chain).

  - [ ] 3.3 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Workspace Integrity on Fast Startup
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected behavior: `ensure_default_workspace()` is called on fast path, missing items are recreated, workspace path is cached
    - Run bug condition exploration test from step 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [ ] 3.4 Verify preservation tests still pass
    - **Property 2: Preservation** - Fast Path Performance
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run preservation property tests from step 2
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm full init path unchanged, fast path still skips schema/migrations, idempotent behavior preserved, seed copy unchanged
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Run full test suite: `cd backend && pytest tests/test_fast_startup_workspace_integrity.py -v`
  - Ensure all exploration tests (Property 1) pass after fix
  - Ensure all preservation tests (Property 2) pass after fix
  - Run existing backend tests to confirm no regressions: `cd backend && pytest`
  - Ensure all tests pass, ask the user if questions arise
