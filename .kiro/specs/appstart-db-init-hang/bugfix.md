# Bugfix Requirements Document

## Introduction

The SwarmAI desktop app hangs or times out during startup because the backend runs expensive database initialization logic at every launch — schema creation via `executescript(SCHEMA)`, a long chain of column-existence migrations in `_run_migrations()`, and full default-data population via `run_full_initialization()` (agent, workspace, skills, MCP servers). On slower machines or locked SQLite files, this routinely exceeds the 45-second timeout in `lifespan()`, leaving the app in a broken state.

The fix is dead simple: on first launch (when `data.db` doesn't exist), copy the pre-built `seed.db` to `~/.swarm-ai/data.db`, set WAL mode + busy_timeout pragmas, and serve. For returning users (when `data.db` exists), skip the expensive init pipeline entirely — the database is already initialized. No migration logic, no schema checks, no conditional paths on the hot path. The `seed.db` contains the complete schema and all default data. Migration and user data preservation are handled by preserving the existing `data.db` for returning users.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN the app starts for the first time (no `~/.swarm-ai/data.db` exists) AND the seed DB copy succeeds THEN the system still runs `SQLiteDatabase.initialize()` which re-executes the full SCHEMA DDL and all migrations against the already-complete seed database, wasting startup time

1.2 WHEN the app starts for the first time AND the seed DB copy succeeds THEN the system still runs `run_full_initialization()` which re-scans skill directories, re-registers default MCP servers, re-creates the default agent, and re-creates the default workspace — all of which already exist in the seed database

1.3 WHEN the app starts with an existing `data.db` (returning user) THEN the system runs `SQLiteDatabase.initialize()` which executes the full SCHEMA DDL via `executescript()` and then runs every migration check (20+ PRAGMA table_info queries with individual ALTER TABLE statements), even when the schema is already current

1.4 WHEN the app starts on a slow machine or when the SQLite file is temporarily locked THEN the combined time of schema execution + migrations + full initialization exceeds the 45-second `asyncio.wait_for` timeout in `lifespan()`, causing a `RuntimeError("Database initialization timed out")` that crashes the app

1.5 WHEN the app starts and `run_full_initialization()` fails partway through (e.g., skill directory scan error) THEN `initialization_complete` is never set to `True`, causing the app to retry full initialization on every subsequent startup — a persistent failure loop

1.6 WHEN the app starts with a seed-database-sourced `data.db` that already has `initialization_complete = true` THEN the system still runs `SQLiteDatabase.initialize()` with full schema DDL before checking the flag, adding unnecessary startup latency

### Expected Behavior (Correct)

2.1 WHEN the app starts for the first time (no `~/.swarm-ai/data.db` exists) AND a pre-built `seed.db` is available THEN the system SHALL copy `seed.db` to `~/.swarm-ai/data.db`, skipping all schema creation, migrations, and full initialization entirely

2.2 WHEN the app starts and `data.db` already exists (returning user) THEN the system SHALL preserve the existing `data.db` (no overwrite), skip the expensive init pipeline entirely (`SQLiteDatabase.initialize()` schema DDL, `_run_migrations()`, and `run_full_initialization()`), and proceed directly to serving requests — user data (agents, workspaces, chat threads, tasks) SHALL remain intact

2.3 WHEN the seed DB copy succeeds (first launch) THEN the system SHALL set WAL mode and busy_timeout pragmas on the freshly copied database and proceed directly to serving requests

2.4 WHEN a developer runs `python scripts/generate_seed_db.py` (or equivalent build-time script) THEN the system SHALL produce a `seed.db` file containing the complete schema (all tables, indexes), the default agent record, the default workspace record, all default skills registered, all default MCP servers registered, default app settings with `initialization_complete = true`, and WAL mode disabled (so the file is a single portable file suitable for bundling)

2.5 WHEN the seed database is not available at startup (development mode or missing bundle) THEN the system SHALL fall back to runtime schema creation and full initialization as a graceful degradation path, logging a warning that seed DB was not found

2.6 WHEN the app starts and `seed.db` is available AND `data.db` doesn't exist (first launch) THEN the system SHALL NOT run `SQLiteDatabase.initialize()` schema DDL, `_run_migrations()`, or `run_full_initialization()` — the seed DB is the sole source of truth for a fresh database

2.7 WHEN the seed copy operation fails (disk full, permissions error, I/O error) THEN the system SHALL NOT leave a partial or corrupted `data.db` — any partial file SHALL be removed, and the system SHALL fall back to runtime initialization with a warning log

2.8 WHEN pragma operations (WAL mode, busy_timeout) fail after a successful seed copy THEN the system SHALL log a warning but continue startup — pragma failures are non-fatal

### Future Work (Out of Scope)

- Incremental schema migrations for existing databases when schema changes between versions
- Schema version tracking and conditional migration paths
- "Reset to Defaults" should offer option to preserve user data vs full reset

### Unchanged Behavior (Regression Prevention)

3.1 WHEN the app is running in development mode (`python main.py` directly) and no `seed.db` is available THEN the system SHALL CONTINUE TO function correctly by falling back to runtime initialization

3.2 WHEN the user triggers "Reset to Defaults" from the UI THEN the system SHALL CONTINUE TO perform full re-initialization of default agent, workspace, skills, and MCP servers as it does today

3.3 WHEN the `generate_seed_db.py` script is run THEN the system SHALL CONTINUE TO use the same skill definitions from `backend/resources/skills/`, MCP server configs from `backend/resources/config/`, and agent defaults from `agent_defaults.py` — ensuring the seed DB content matches what runtime initialization would produce

3.4 WHEN the Tauri desktop build packages the app THEN the system SHALL CONTINUE TO bundle `seed.db` in `desktop/resources/` so it is available at the expected path via `_get_seed_database_path()`
