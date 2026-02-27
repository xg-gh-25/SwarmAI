# Implementation Plan

- [x] 1. Write bug condition exploration test
  - **Property 1: Fault Condition** — Seed-Available Startup Runs Redundant Init Pipeline
  - **CRITICAL**: This test MUST FAIL on unfixed code — failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior — it will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: Scope the property to concrete startup scenarios: seed DB available with and without existing `data.db`
  - Create a temporary directory with a valid `seed.db` (use `generate_seed_db.py` logic or a minimal SQLite file with `initialization_complete = 1`)
  - Test file: `backend/tests/test_seed_startup_exploration.py`
  - **Test Case 1 — First Launch**: Seed DB available, no `data.db` exists. Mock/patch `SQLiteDatabase.initialize` and `run_full_initialization` to track calls. Run `_ensure_database_initialized()` + simulate `lifespan()` init path. Assert that `SQLiteDatabase.initialize()` schema DDL is NOT called and `run_full_initialization()` is NOT called (from Fault Condition in design: `schema_ddl_executed == false AND migrations_executed == false AND full_init_executed == false`)
  - **Test Case 2 — Returning User**: `data.db` already exists (simulating returning user). Assert that `data.db` is preserved (not overwritten), and init pipeline is skipped
  - **Test Case 3 — Pragma Setup**: After seed copy (first launch), assert WAL mode and busy_timeout are set on the copied DB
  - **Test Case 4 — Seed Copy Failure Recovery (Property 4)**: Simulate seed copy failure (e.g., mock `shutil.copy2` to raise IOError). Assert that no partial `data.db` file is left behind, and system falls back to runtime init
  - **Test Case 5 — Pragma Failure Graceful Degradation (Property 5)**: Simulate pragma failure after successful seed copy. Assert that startup continues (non-fatal), warning is logged
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Tests 1-3 FAIL (proves the bug exists), Tests 4-5 may pass or fail depending on current error handling
  - Document counterexamples: e.g., "`SQLiteDatabase.initialize()` called 1 time even though seed DB was copied", "init pipeline runs for returning user"
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.6, 2.1, 2.2, 2.6, 2.7, 2.8_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** — Dev-Mode Fallback Unchanged
  - **Property 3: Data Preservation** — Returning User Data Preserved
  - **IMPORTANT**: Follow observation-first methodology
  - Test file: `backend/tests/test_seed_startup_preservation.py`
  - **Observe on UNFIXED code first**:
  - Observe: When no `seed.db` is available, `_ensure_database_initialized()` returns without copying anything
  - Observe: When no `seed.db` is available, `lifespan()` runs `initialize_database()` (schema DDL + migrations)
  - Observe: When no `seed.db` is available and `initialization_complete` is not set, `run_full_initialization()` is called
  - Observe: `initialization_manager.reset_to_defaults()` performs full re-initialization regardless of seed DB
  - **Write property-based tests capturing observed behavior** (from Preservation Requirements in design):
  - **Test Case 1 — Dev-Mode Full Init**: For all startup contexts where `seed.db` is NOT available, assert `SQLiteDatabase.initialize()` IS called with full schema DDL, `_run_migrations()` IS called, and `run_full_initialization()` IS called when `initialization_complete` is not set
  - **Test Case 2 — Reset to Defaults**: Assert `reset_to_defaults()` continues to perform full re-initialization of default agent, workspace, skills, and MCP servers
  - **Test Case 3 — Seed DB Content Consistency**: Assert `generate_seed_db.py` uses the same skill definitions from `backend/resources/skills/`, MCP configs from `backend/resources/config/`, and agent defaults from `agent_defaults.py`
  - **Test Case 4 — Returning User Data Preservation**: For startup contexts where `data.db` already exists, assert that `data.db` is NOT overwritten (user data preserved)
  - Property-based: generate random (seed_available=False, data_db_exists=random) contexts and verify the full init pipeline always runs for dev-mode
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 2.2_

- [x] 3. Implement the seed-copy startup fix

  - [x] 3.1 Update `_ensure_database_initialized()` in `backend/main.py`
    - Keep the `if user_db_path.exists()` check — return `True` to indicate "skip init pipeline" (preserves user data for returning users)
    - When `seed.db` is available AND `data.db` doesn't exist, copy seed to `data.db` using atomic copy pattern:
      - Copy to a temp file first (e.g., `data.db.tmp`)
      - On success, rename temp file to `data.db` (atomic on POSIX)
      - On failure, remove the temp file to avoid leaving partial/corrupted files
    - After successful seed copy, open the DB with `sqlite3.connect()`, execute `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`, then close
    - Log warnings but continue if pragma setting fails (non-fatal per Property 5)
    - If seed copy fails mid-operation, remove partial file and fall back to runtime init with warning log
    - Change return type to `bool`: return `True` when seed was successfully copied OR when `data.db` already exists (both skip init), `False` when seed not available and no `data.db` (needs runtime init)
    - When seed is not available, log a warning: `"Seed database not found, falling back to runtime initialization"`
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.7, 2.8_

  - [x] 3.2 Update `lifespan()` in `backend/main.py`
    - Capture skip-init flag: `skip_init_pipeline = _ensure_database_initialized()`
    - When `skip_init_pipeline is True`: skip `await asyncio.wait_for(initialize_database(), timeout=45.0)` and skip the `initialization_manager` check/init block entirely. Call `await initialize_database(skip_schema=True)` to create the DB instance without running DDL, then jump straight to channel gateway startup
    - When `skip_init_pipeline is False`: run the existing init pipeline unchanged (schema DDL + migrations + full init with timeout) — preserve the current fallback path exactly
    - Log which startup path was taken for observability: "Fast startup (seed-sourced)" vs "Full initialization (runtime)"
    - _Requirements: 2.1, 2.2, 2.5, 2.6, 3.1_

  - [x] 3.3 Add `skip_init` parameter to `SQLiteDatabase.initialize()` in `backend/database/sqlite.py`
    - Add optional parameter `skip_init: bool = False` to `initialize()` method
    - When `skip_init is True`: set `self._initialized = True` and return immediately without running schema DDL or `_run_migrations()`
    - When `skip_init is False`: run existing logic unchanged (schema DDL + migrations)
    - _Bug_Condition: initialize() always runs full schema DDL + migrations_
    - _Expected_Behavior: skip_init=True skips DDL and migrations, skip_init=False preserves current behavior_
    - _Preservation: Default behavior (skip_init=False) must be identical to current implementation_
    - _Requirements: 2.5, 3.1_

  - [x] 3.4 Add `skip_schema` parameter to `initialize_database()` in `backend/database/__init__.py`
    - Add optional parameter `skip_schema: bool = False` to `initialize_database()` function
    - Pass through to `SQLiteDatabase.initialize(skip_init=skip_schema)`
    - When `skip_schema is True`: DB instance is created but no schema DDL or migrations run
    - When `skip_schema is False`: existing behavior unchanged
    - _Bug_Condition: initialize_database() always triggers full schema init_
    - _Expected_Behavior: skip_schema=True passes through to skip DDL, skip_schema=False preserves current behavior_
    - _Preservation: Default behavior (skip_schema=False) must be identical to current implementation_
    - _Requirements: 2.5, 3.1_

  - [x] 3.5 Verify `generate_seed_db.py` output in `backend/scripts/generate_seed_db.py`
    - Verify the seed DB has `PRAGMA journal_mode = DELETE` (not WAL) so it's a single portable file
    - Verify the seed DB has `initialization_complete = 1` in `app_settings`
    - If either check fails, add the missing pragma/setting to the script
    - _Requirements: 2.3_

  - [x] 3.6 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** — Seed-Sourced Startup Skips Init Pipeline
    - **IMPORTANT**: Re-run the SAME test from task 1 — do NOT write a new test
    - The test from task 1 encodes the expected behavior (seed copy → pragmas → no DDL/migrations/init)
    - When this test passes, it confirms the expected behavior is satisfied
    - Run bug condition exploration test from step 1: `cd backend && pytest tests/test_seed_startup_exploration.py -v`
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2, 2.5_

  - [x] 3.7 Verify preservation tests still pass
    - **Property 2: Preservation** — Dev-Mode Fallback Unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 — do NOT write new tests
    - Run preservation property tests from step 2: `cd backend && pytest tests/test_seed_startup_preservation.py -v`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - Confirm all preservation tests still pass after fix (no regressions to dev-mode fallback, reset-to-defaults, or seed DB content)
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 4. Checkpoint — Ensure all tests pass
  - Run full test suite: `cd backend && pytest -v`
  - Ensure all exploration tests (task 1) pass — confirms bug is fixed
  - Ensure all preservation tests (task 2) pass — confirms no regressions
  - Ensure existing backend tests still pass — no unintended side effects
  - If any test fails, investigate and fix before proceeding
  - Ask the user if questions arise
