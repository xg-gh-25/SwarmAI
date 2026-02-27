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

- [ ] 3. Fix for fast startup path missing workspace integrity verification

  - [ ] 3.1 Add `set_cached_workspace_path()` public setter to `InitializationManager`
    - Add `set_cached_workspace_path(self, path: str) -> None` method to `InitializationManager` in `backend/core/initialization_manager.py`
    - Method sets `self._cached_workspace_path = path`
    - Update `run_full_initialization()` to use `self.set_cached_workspace_path()` instead of direct `self._cached_workspace_path = ...` assignment
    - _Bug_Condition: `_cached_workspace_path` remains `None` on fast path because no setter exists for external callers_
    - _Expected_Behavior: Public setter allows `lifespan()` to cache the workspace path on the fast path_
    - _Preservation: `run_full_initialization()` behavior unchanged — just uses setter instead of direct assignment_
    - _Requirements: 2.1, 2.2, 2.6_

  - [ ] 3.2 Add `ensure_default_workspace()` call, skill/template setup, and workspace path caching to fast path in `lifespan()`
    - In `backend/main.py` `lifespan()`, after `initialize_database(skip_schema=True)` and before `channel_gateway.startup()`
    - Import `swarm_workspace_manager` from `core.swarm_workspace_manager`, `agent_sandbox_manager` from `core.agent_sandbox_manager`, and `db` from `database`
    - Call `workspace = await swarm_workspace_manager.ensure_default_workspace(db)`
    - Expand path: `workspace_path = swarm_workspace_manager.expand_path(workspace["file_path"])`
    - Cache path: `initialization_manager.set_cached_workspace_path(workspace_path)`
    - Call `await agent_sandbox_manager.setup_workspace_skills(Path(workspace_path))` (inner try/except, non-fatal)
    - Call `agent_sandbox_manager.ensure_templates_in_directory(Path(workspace_path))` (inner try/except, non-fatal)
    - Wrap outer block in try/except: log error on failure but do NOT block startup (PE Fix: Property 4 graceful degradation)
    - Add `logger.info("Workspace integrity verified on fast path")` on success
    - _Bug_Condition: `isBugCondition(input)` where `input.data_db_exists == True AND input.startup_path == "fast" AND verify_integrity_was_NOT_called()`_
    - _Expected_Behavior: `ensure_default_workspace()` called on fast path, `verify_integrity()` heals missing items, skills symlinked, templates copied, workspace path cached_
    - _Preservation: Fast path still skips schema DDL, migrations, agent/skill/MCP registration. Only adds lightweight idempotent workspace operations_
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 3.2, 3.6_

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
