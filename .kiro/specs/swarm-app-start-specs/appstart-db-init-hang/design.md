# App Startup DB Init Hang — Bugfix Design

## Overview

The SwarmAI desktop app hangs during startup because `lifespan()` runs an expensive, redundant database initialization pipeline on every launch: full schema DDL via `executescript(SCHEMA)`, 20+ migration checks (each doing `PRAGMA table_info` + conditional `ALTER TABLE`), legacy data cleanup, and `run_full_initialization()` (skill scanning, MCP registration, agent/workspace creation). On slower machines or locked SQLite files this exceeds the 45-second `asyncio.wait_for` timeout, crashing the app.

The fix replaces this pipeline with a conditional seed-copy strategy: on first launch (when `data.db` doesn't exist), copy the pre-built `seed.db` → `~/.swarm-ai/data.db`, set WAL mode + busy_timeout pragmas, and serve. For returning users (when `data.db` exists), skip the expensive init pipeline entirely — the database is already initialized. A build-time script (`generate_seed_db.py`, already exists) produces the seed DB with complete schema and default data. The runtime init path is retained only as a dev-mode fallback when `seed.db` is missing.

## Glossary

- **Bug_Condition (C)**: App startup when `seed.db` is available — the system currently runs full schema DDL, migrations, and initialization even though the seed DB already contains everything needed
- **Property (P)**: When `seed.db` is available and `data.db` doesn't exist (first launch), startup SHALL copy seed to `data.db`, set pragmas, and skip all schema/migration/init work. When `data.db` already exists (returning user), startup SHALL skip the expensive init pipeline entirely and proceed directly to serving requests.
- **Preservation**: Dev-mode fallback (no `seed.db`), "Reset to Defaults" UI flow, and Tauri bundling of `seed.db` must continue to work unchanged
- **`_ensure_database_initialized()`**: Function in `backend/main.py` that currently copies seed DB only when `data.db` doesn't exist — needs to return a flag indicating whether to skip the init pipeline (True when seed copied OR existing DB, False when dev-mode fallback needed)
- **`SQLiteDatabase.initialize()`**: Method in `backend/database/sqlite.py` that runs full SCHEMA DDL + `_run_migrations()` — needs to be skipped when seed-sourced
- **`run_full_initialization()`**: Method in `backend/core/initialization_manager.py` that registers skills, MCPs, agent, workspace — needs to be skipped when seed-sourced
- **`generate_seed_db.py`**: Build-time script in `backend/scripts/` that produces `seed.db` with complete schema and default data

## Bug Details

### Fault Condition

The bug manifests on every app startup when a `seed.db` is available (production/bundled mode). The current code either skips the seed copy entirely (when `data.db` exists) or copies the seed but then redundantly runs the full init pipeline on top of it. In both cases, `SQLiteDatabase.initialize()` executes the full SCHEMA DDL and 20+ migration checks, and `run_full_initialization()` re-scans skills, re-registers MCPs, and re-creates agent/workspace — all unnecessary work that causes timeouts on slower machines.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type AppStartupContext
  OUTPUT: boolean

  RETURN input.seed_db_available == true
         AND input.data_db_exists == false  -- first launch only
         AND (schema_ddl_executed OR migrations_executed OR full_init_executed)
END FUNCTION

FUNCTION isReturningUserBugCondition(input)
  INPUT: input of type AppStartupContext
  OUTPUT: boolean

  RETURN input.data_db_exists == true  -- returning user
         AND (schema_ddl_executed OR migrations_executed)  -- expensive init still runs
END FUNCTION
```

### Examples

- **First launch (seed available)**: `seed.db` exists in `desktop/resources/`, no `data.db` yet. Current behavior: copies seed → `data.db`, then runs `SQLiteDatabase.initialize()` (full SCHEMA DDL + 20+ migrations) + `run_full_initialization()` (skill scan, MCP registration, agent/workspace creation). Expected: copy seed → `data.db`, set WAL + busy_timeout, serve. No DDL, no migrations, no init.
- **Returning user (seed available)**: `data.db` already exists, `seed.db` available. Current behavior: skips seed copy, runs full `initialize()` + checks `initialization_complete` flag + possibly runs `run_full_initialization()`. Expected: skip seed copy (preserve user data), skip expensive init pipeline, proceed directly to serving requests.
- **Slow machine / locked DB**: Same as above but `initialize()` takes >45s due to SQLite lock contention or slow I/O. Current behavior: `asyncio.wait_for` raises `TimeoutError`, app crashes with `RuntimeError("Database initialization timed out")`. Expected: fast startup path completes quickly, no timeout.
- **Dev mode (no seed.db)**: Developer runs `python main.py` directly, no `seed.db` bundled. Current behavior: runs full init pipeline. Expected: same — fall back to runtime init with a warning log.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Dev-mode startup without `seed.db` must continue to work via runtime initialization fallback (schema DDL + migrations + `run_full_initialization()`)
- "Reset to Defaults" UI action (`initialization_manager.reset_to_defaults()`) must continue to perform full re-initialization of default agent, workspace, skills, and MCP servers
- `generate_seed_db.py` must continue to use the same skill definitions from `backend/resources/skills/`, MCP configs from `backend/resources/config/`, and agent defaults from `agent_defaults.py`
- Tauri desktop build must continue to bundle `seed.db` in `desktop/resources/` so it is available via `_get_seed_database_path()`

**Scope:**
All startup paths where `seed.db` is NOT available should be completely unaffected by this fix. This includes:
- Direct `python main.py` execution without a bundled seed DB
- Test environments that create in-memory or temporary databases
- The `reset_to_defaults()` flow triggered from the UI

## Hypothesized Root Cause

Based on the bug description and code analysis, the root causes are:

1. **Unconditional schema DDL execution**: `SQLiteDatabase.initialize()` always runs `conn.executescript(self.SCHEMA)` — a massive multi-table DDL string — even when the database already has the complete schema (either from a previous run or from the seed DB). This is the single most expensive operation.

2. **Unconditional migration checks**: `_run_migrations()` runs 20+ `PRAGMA table_info` queries followed by conditional `ALTER TABLE` statements on every startup. Each PRAGMA query opens a read transaction, and each ALTER TABLE requires a write lock. On a seed-sourced DB where all columns already exist, this is pure waste.

3. **Conditional seed copy (only on first launch)**: `_ensure_database_initialized()` checks `if user_db_path.exists(): return` — meaning returning users never get a fresh seed copy. Their `data.db` goes through the full init pipeline every time.

4. **Redundant full initialization after seed copy**: Even when the seed DB is successfully copied (first launch), `lifespan()` still calls `initialize_database()` (schema DDL + migrations) and then checks `initialization_complete` / runs `run_full_initialization()`. The seed DB already has `initialization_complete = true` and all default data, making this entirely redundant.

5. **No seed-sourced flag**: There is no mechanism to signal to downstream code that the DB was seed-sourced and therefore needs no initialization. The `lifespan()` function always follows the same code path regardless of DB origin.

## Correctness Properties

Property 1: Fault Condition — First-Launch Seed-Sourced Startup Skips Init Pipeline

_For any_ app startup where `seed.db` is available AND `data.db` does NOT exist (first launch), the fixed startup flow SHALL copy `seed.db` to `~/.swarm-ai/data.db`, set WAL mode and busy_timeout pragmas, and proceed directly to serving requests — without executing `SQLiteDatabase.initialize()` schema DDL, `_run_migrations()`, or `run_full_initialization()`.

**Validates: Requirements 2.1, 2.2, 2.5**

Property 2: Data Preservation — Returning User Startup Preserves User Data

_For any_ app startup where `data.db` already exists (returning user), the fixed startup flow SHALL NOT overwrite `data.db` with `seed.db`. The existing database SHALL be preserved, and the expensive init pipeline (`SQLiteDatabase.initialize()` schema DDL, `_run_migrations()`) SHALL be skipped. User data (agents, workspaces, chat threads, tasks) SHALL remain intact.

**Validates: Requirements 2.1, 2.5, 3.1**

Property 3: Preservation — Dev-Mode Fallback Unchanged

_For any_ app startup where `seed.db` is NOT available (isBugCondition returns false), the fixed code SHALL produce exactly the same behavior as the original code: runtime schema creation via `SQLiteDatabase.initialize()`, migration checks via `_run_migrations()`, and full initialization via `run_full_initialization()` — preserving the dev-mode startup path.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

Property 4: Error Handling — Seed Copy Failure Recovery

_For any_ seed copy operation that fails (disk full, permissions error, I/O error), the system SHALL NOT leave a partial or corrupted `data.db`. If the copy fails mid-operation, any partial file SHALL be removed, and the system SHALL fall back to runtime initialization with a warning log.

**Validates: Requirements 2.4**

Property 5: Error Handling — Pragma Failure Graceful Degradation

_For any_ pragma operation (WAL mode, busy_timeout) that fails after a successful seed copy, the system SHALL log a warning but continue startup. Pragma failures are non-fatal — the database will function correctly with default settings.

**Validates: Requirements 2.2**

## Fix Implementation

### Changes Required

Assuming our root cause analysis is correct:

**File**: `backend/main.py`

**Function**: `_ensure_database_initialized()`

**Specific Changes**:
1. **Conditional seed copy (first launch only)**: Keep the `if user_db_path.exists(): return True` check — but change the return value to indicate "database already exists, skip init pipeline". When `data.db` exists, we preserve user data and skip the expensive init.
2. **Return seed-sourced flag**: Change return type to `bool`. Return `True` when seed was successfully copied OR when `data.db` already exists (both cases skip init pipeline). Return `False` when seed was not available and `data.db` doesn't exist (dev-mode fallback needs full init).
3. **Set pragmas after copy**: After a successful seed copy, open the DB with `sqlite3`, set `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`, then close. Log warnings but don't fail if pragmas fail.
4. **Atomic copy with rollback**: Use a temporary file for the copy operation. If copy fails, remove the partial file and fall back to runtime init.

**Function**: `lifespan()`

**Specific Changes**:
5. **Capture seed-sourced flag**: `skip_init_pipeline = _ensure_database_initialized()`. Use this flag to conditionally skip the expensive init pipeline.
6. **Skip init pipeline when flag is True**: When `skip_init_pipeline is True` (seed copied OR existing DB), skip `await asyncio.wait_for(initialize_database(), timeout=45.0)` and skip the `initialization_manager` check/init block entirely. Call `await initialize_database(skip_schema=True)` to create the DB instance without running DDL, then jump straight to channel gateway startup.
7. **Preserve fallback path**: When `skip_init_pipeline is False`, run the existing init pipeline unchanged (schema DDL + migrations + full init with timeout).

**File**: `backend/database/sqlite.py`

**Class**: `SQLiteDatabase`

**Specific Changes**:
7. **Add `skip_init` parameter**: Add an optional `skip_init: bool = False` parameter to `initialize()`. When `True`, set `self._initialized = True` without running schema DDL or migrations. This allows the database object to be used without running the expensive init path.

**File**: `backend/database/__init__.py`

**Function**: `initialize_database()`

**Specific Changes**:
8. **Add `skip_schema` parameter**: Add an optional `skip_schema: bool = False` parameter that passes through to `SQLiteDatabase.initialize(skip_init=skip_schema)`. When seed-sourced, `lifespan()` calls `await initialize_database(skip_schema=True)` to create the DB instance without running DDL.

**File**: `backend/scripts/generate_seed_db.py`

**Specific Changes**:
9. **Ensure WAL mode disabled in output**: The seed DB must be in DELETE journal mode (not WAL) so it's a single portable file. Add `PRAGMA journal_mode=DELETE` before closing. (The existing script may already do this — verify and add if missing.)
10. **Ensure `initialization_complete = true`**: Verify the seed DB has `initialization_complete = 1` in `app_settings`. (The existing script already does this via `_insert_app_settings()`.)

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

### Exploratory Fault Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write tests that verify which init functions are called during `lifespan()` when a seed DB is available. Run these tests on the UNFIXED code to observe the redundant init pipeline execution.

**Test Cases**:
1. **Seed-Available First Launch Test**: Create a seed DB, ensure no `data.db` exists, run `lifespan()`. Assert that `SQLiteDatabase.initialize()` is called (will pass on unfixed code — demonstrating the bug)
2. **Returning User Test**: Create an existing `data.db` (simulating returning user), run `lifespan()`. Assert that `initialize()` schema DDL still runs (will pass on unfixed code — demonstrating the bug)
3. **Redundant Full Init Test**: Create a seed DB with `initialization_complete = true`, run `lifespan()`. Assert that `run_full_initialization()` is NOT called but `initialize()` IS called (will demonstrate partial redundancy on unfixed code)

**Expected Counterexamples**:
- `SQLiteDatabase.initialize()` is always called regardless of seed DB availability or existing `data.db`
- Full init pipeline runs even when seed DB has `initialization_complete = true`

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
-- First launch scenario (seed available, no data.db)
FOR ALL input WHERE isBugCondition(input) DO
  result := lifespan_fixed(input)
  ASSERT result.seed_copied == true
  ASSERT result.schema_ddl_executed == false
  ASSERT result.migrations_executed == false
  ASSERT result.full_init_executed == false
  ASSERT result.wal_mode_set == true
  ASSERT result.busy_timeout_set == true
END FOR

-- Returning user scenario (data.db exists)
FOR ALL input WHERE isReturningUserBugCondition(input) DO
  result := lifespan_fixed(input)
  ASSERT result.seed_copied == false  -- preserve user data
  ASSERT result.schema_ddl_executed == false
  ASSERT result.migrations_executed == false
  ASSERT result.user_data_preserved == true
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT lifespan_original(input) = lifespan_fixed(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for dev-mode startup (no seed DB), then write property-based tests capturing that behavior.

**Test Cases**:
1. **Dev-Mode Fallback Preservation**: Verify that when no `seed.db` exists, the full init pipeline runs exactly as before (schema DDL + migrations + full init)
2. **Reset to Defaults Preservation**: Verify that `initialization_manager.reset_to_defaults()` continues to work correctly after the fix
3. **Seed DB Content Preservation**: Verify that `generate_seed_db.py` produces a DB with the same schema and default data as runtime initialization would

### Unit Tests

- Test `_ensure_database_initialized()` copies seed DB when available AND `data.db` doesn't exist (first launch)
- Test `_ensure_database_initialized()` returns `True` and skips seed copy when `data.db` already exists (returning user)
- Test `_ensure_database_initialized()` returns `True` when seed copied, `False` when seed not available
- Test `_ensure_database_initialized()` sets WAL mode and busy_timeout after seed copy
- Test `_ensure_database_initialized()` logs warning but continues if pragma setting fails
- Test `_ensure_database_initialized()` removes partial file and falls back if seed copy fails mid-operation
- Test `lifespan()` skips `initialize_database()` and `run_full_initialization()` when `skip_init_pipeline` is True
- Test `lifespan()` runs full init pipeline when `skip_init_pipeline` is False (dev-mode)
- Test `lifespan()` logs startup path taken (seed-sourced vs runtime-init) for observability
- Test `SQLiteDatabase.initialize(skip_init=True)` sets `_initialized = True` without running DDL
- Test `generate_seed_db.py` output has `journal_mode = DELETE` (not WAL)
- Test `generate_seed_db.py` output has `initialization_complete = 1` in `app_settings`

### Property-Based Tests

- Generate random combinations of (seed_available, data_db_exists) and verify the correct code path is taken for each:
  - (seed_available=True, data_db_exists=False) → seed copy, skip init
  - (seed_available=True, data_db_exists=True) → no seed copy (preserve data), skip init
  - (seed_available=False, data_db_exists=False) → runtime init
  - (seed_available=False, data_db_exists=True) → runtime init (existing DB needs validation)
- Generate random startup contexts and verify that seed-sourced startups never call `initialize()` schema DDL
- Generate random startup contexts and verify that returning-user startups preserve `data.db` (no overwrite)
- Generate random startup contexts and verify that non-seed startups always follow the original init pipeline

### Integration Tests

- Test full startup flow with seed DB (first launch): copy → pragmas → serve (end-to-end)
- Test full startup flow with existing DB (returning user): skip copy → skip init → serve (end-to-end, verify user data preserved)
- Test full startup flow without seed DB: runtime init pipeline completes successfully
- Test that a seed-copied DB is fully functional: can read/write agents, skills, MCPs, workspaces, tasks, chat threads
- Test that `generate_seed_db.py` → seed copy → app startup → API queries all return expected default data
- Test error recovery: simulate disk-full during seed copy, verify partial file is cleaned up and fallback to runtime init works
