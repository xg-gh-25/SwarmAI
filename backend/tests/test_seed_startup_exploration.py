"""Bug condition exploration tests for app startup DB initialization hang.

This module tests the fault condition where the app startup runs redundant
database initialization pipeline even when a pre-built seed.db is available.

Testing methodology:
- Scoped property-based exploration to surface counterexamples demonstrating
  the bug on UNFIXED code
- Tests 1-3 are EXPECTED TO FAIL on unfixed code (failure confirms bug exists)
- Tests 4-5 verify error-handling paths (may pass or fail on unfixed code)
- DO NOT attempt to fix the test or code when tests fail

Key properties being verified:
- Property 1 (Fault Condition): Seed-Available Startup Runs Redundant Init
  - Test 1: First launch — seed copy should skip init pipeline
  - Test 2: Returning user — data.db preserved, init pipeline skipped
  - Test 3: Pragma setup — WAL mode and busy_timeout set after seed copy
- Property 4: Seed Copy Failure Recovery
  - Test 4: No partial data.db left behind on copy failure
- Property 5: Pragma Failure Graceful Degradation
  - Test 5: Startup continues if pragma fails after seed copy

Validates: Requirements 1.1, 1.2, 1.3, 1.6, 2.1, 2.2, 2.6, 2.7, 2.8
"""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_app_data_dir(tmp_path):
    """Create a temporary app data directory for testing."""
    app_data = tmp_path / ".swarm-ai"
    app_data.mkdir(parents=True, exist_ok=True)
    return app_data


@pytest.fixture
def seed_db_path(tmp_path):
    """Create a minimal valid seed.db with initialization_complete=1."""
    seed_path = tmp_path / "seed.db"

    conn = sqlite3.connect(str(seed_path))
    cursor = conn.cursor()

    # Create minimal app_settings table with initialization_complete
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            id TEXT PRIMARY KEY,
            anthropic_api_key TEXT,
            initialization_complete INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("""
        INSERT INTO app_settings
            (id, anthropic_api_key, initialization_complete, created_at, updated_at)
        VALUES ('default', '', 1, datetime('now'), datetime('now'))
    """)

    # Create minimal agents table (required by schema)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT,
            is_system_agent INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    """)

    conn.commit()
    conn.close()
    return seed_path


# ---------------------------------------------------------------------------
# Test Case 1 — First Launch: Seed DB available, no data.db exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_launch_seed_available_skips_init_pipeline(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """Test Case 1: First launch with seed DB should skip init pipeline.

    **Validates: Requirements 1.1, 1.2, 2.1, 2.6**

    Bug Condition: When seed.db is available and data.db does NOT exist,
    the system currently copies seed then STILL runs SQLiteDatabase.initialize()
    (full schema DDL + migrations) and run_full_initialization().

    Expected Behavior: Copy seed to data.db, set pragmas, skip all
    schema/migration/init work.

    EXPECTED OUTCOME ON UNFIXED CODE: Test FAILS
    - SQLiteDatabase.initialize() IS called (bug: should NOT be called)
    """
    # Patch get_app_data_dir to use our temp directory
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    # Patch _get_seed_database_path to return our seed DB
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)

    from main import _ensure_database_initialized

    user_db_path = temp_app_data_dir / "data.db"
    assert not user_db_path.exists(), "data.db should not exist for first launch"

    # Run the seed-copy function and capture the skip flag (as lifespan does)
    skip_init_pipeline = _ensure_database_initialized()

    # Verify seed was copied
    assert user_db_path.exists(), "data.db should be created from seed"

    # Now test the downstream init path — track SQLiteDatabase.initialize calls
    # Simulate what lifespan() does: when skip_init_pipeline is True, it calls
    # initialize_database(skip_schema=True) instead of initialize_database()
    ddl_executed = []

    async def mock_initialize(self, skip_init=False):
        if skip_init:
            self._initialized = True
            return  # skip_init=True → no DDL, no migrations
        ddl_executed.append("schema_ddl")

    with patch("database.sqlite.SQLiteDatabase.initialize", mock_initialize):
        from database import initialize_database
        if skip_init_pipeline:
            await initialize_database(skip_schema=True)
        else:
            await initialize_database()

    # ASSERTION: On FIXED code, DDL should NOT be executed (skip_init=True)
    # On UNFIXED code, this FAILS — proving the bug exists
    assert len(ddl_executed) == 0, (
        f"BUG CONFIRMED: Schema DDL was executed "
        f"{len(ddl_executed)} time(s) even though seed DB was copied. "
        "Expected: 0 DDL executions (seed DB has complete schema)."
    )


# ---------------------------------------------------------------------------
# Test Case 2 — Returning User: data.db already exists, init skipped
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returning_user_preserves_data_and_skips_init(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """Test Case 2: Returning user — data.db preserved, init pipeline skipped.

    **Validates: Requirements 1.3, 1.6, 2.1, 2.2**

    Bug Condition: When data.db already exists (returning user), the system
    still runs SQLiteDatabase.initialize() with full schema DDL and all
    migration checks, even when the schema is already current.

    Expected Behavior: Preserve existing data.db (no overwrite), skip the
    expensive init pipeline entirely, proceed directly to serving.

    EXPECTED OUTCOME ON UNFIXED CODE: Test FAILS
    - SQLiteDatabase.initialize() IS called (bug: should be skipped)
    """
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)

    # Create an existing data.db with a marker table (simulating returning user)
    user_db_path = temp_app_data_dir / "data.db"
    conn = sqlite3.connect(str(user_db_path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_marker (
            id TEXT PRIMARY KEY, marker TEXT
        )
    """)
    cursor.execute(
        "INSERT INTO user_marker (id, marker) VALUES ('test', 'user_data')"
    )
    conn.commit()
    conn.close()

    from main import _ensure_database_initialized
    skip_init_pipeline = _ensure_database_initialized()

    # Verify user data is preserved (marker table still exists)
    conn = sqlite3.connect(str(user_db_path))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='user_marker'"
    )
    assert cursor.fetchone() is not None, (
        "User data was overwritten — user_marker table is missing"
    )
    conn.close()

    # Track initialize() calls on the downstream path
    # Simulate what lifespan() does: when skip_init_pipeline is True, it calls
    # initialize_database(skip_schema=True) instead of initialize_database()
    ddl_executed = []

    async def mock_initialize(self, skip_init=False):
        if skip_init:
            self._initialized = True
            return  # skip_init=True → no DDL, no migrations
        ddl_executed.append("schema_ddl")

    with patch("database.sqlite.SQLiteDatabase.initialize", mock_initialize):
        from database import initialize_database
        if skip_init_pipeline:
            await initialize_database(skip_schema=True)
        else:
            await initialize_database()

    # ASSERTION: On FIXED code, DDL should NOT be executed (skip_init=True)
    # On UNFIXED code, this FAILS — proving the bug exists
    assert len(ddl_executed) == 0, (
        f"BUG CONFIRMED: Schema DDL was executed "
        f"{len(ddl_executed)} time(s) for a returning user whose "
        "data.db already exists. Expected: 0 DDL executions (init pipeline skipped)."
    )


# ---------------------------------------------------------------------------
# Test Case 3 — Pragma Setup: WAL mode and busy_timeout after seed copy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pragma_setup_after_seed_copy(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """Test Case 3: After seed copy, WAL mode and busy_timeout should be set.

    **Validates: Requirements 2.2, 2.8**

    Expected Behavior: After a successful seed copy (first launch), the
    system SHALL set PRAGMA journal_mode=WAL and PRAGMA busy_timeout=5000
    on the copied database.

    EXPECTED OUTCOME ON UNFIXED CODE: Test FAILS
    - WAL mode is NOT set after seed copy
    - busy_timeout is NOT set after seed copy
    """
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)

    user_db_path = temp_app_data_dir / "data.db"
    if user_db_path.exists():
        user_db_path.unlink()

    from main import _ensure_database_initialized
    _ensure_database_initialized()

    assert user_db_path.exists(), "data.db should be created from seed"

    # Check pragma settings on the copied DB
    conn = sqlite3.connect(str(user_db_path))
    cursor = conn.cursor()

    cursor.execute("PRAGMA journal_mode")
    journal_mode = cursor.fetchone()[0].lower()

    cursor.execute("PRAGMA busy_timeout")
    busy_timeout = cursor.fetchone()[0]

    conn.close()

    # ASSERTION: On FIXED code, WAL mode should be set
    assert journal_mode == "wal", (
        f"BUG CONFIRMED: journal_mode is '{journal_mode}', expected 'wal'. "
        "_ensure_database_initialized() does not set WAL mode after seed copy."
    )

    # ASSERTION: On FIXED code, busy_timeout should be 5000
    assert busy_timeout == 5000, (
        f"BUG CONFIRMED: busy_timeout is {busy_timeout}, expected 5000. "
        "_ensure_database_initialized() does not set busy_timeout after "
        "seed copy."
    )


# ---------------------------------------------------------------------------
# Test Case 4 — Seed Copy Failure Recovery (Property 4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_seed_copy_failure_no_partial_db(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """Test Case 4: Seed copy failure should not leave partial data.db.

    **Validates: Requirements 2.7**

    Expected Behavior: When the seed copy operation fails (e.g., IOError),
    no partial or corrupted data.db SHALL be left behind, and the system
    SHALL fall back to runtime initialization.

    EXPECTED OUTCOME ON UNFIXED CODE: May pass or fail depending on
    current error handling in _ensure_database_initialized().
    """
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)

    user_db_path = temp_app_data_dir / "data.db"
    if user_db_path.exists():
        user_db_path.unlink()

    # Mock shutil.copy2 to raise IOError mid-copy
    original_copy2 = __import__("shutil").copy2

    def failing_copy2(src, dst, *args, **kwargs):
        # Write a partial file to simulate mid-copy failure
        Path(dst).write_bytes(b"partial corrupt data")
        raise IOError("Simulated disk full error during seed copy")

    with patch("shutil.copy2", side_effect=failing_copy2):
        from main import _ensure_database_initialized
        # Should not raise — should handle the error gracefully
        _ensure_database_initialized()

    # ASSERTION: No partial data.db should be left behind
    assert not user_db_path.exists(), (
        "BUG CONFIRMED: Partial data.db file was left behind after seed "
        "copy failure. Expected: no data.db (cleaned up on failure)."
    )


# ---------------------------------------------------------------------------
# Test Case 5 — Pragma Failure Graceful Degradation (Property 5)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pragma_failure_continues_startup(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """Test Case 5: Pragma failure after seed copy should not block startup.

    **Validates: Requirements 2.8**

    Expected Behavior: When pragma operations (WAL mode, busy_timeout)
    fail after a successful seed copy, the system SHALL log a warning
    but continue startup — pragma failures are non-fatal.

    EXPECTED OUTCOME ON UNFIXED CODE: May pass or fail depending on
    current error handling. The unfixed code does not set pragmas at all,
    so there is no pragma failure path to test. This test validates the
    FIXED code's graceful degradation.
    """
    import logging

    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)

    user_db_path = temp_app_data_dir / "data.db"
    if user_db_path.exists():
        user_db_path.unlink()

    # Patch sqlite3.connect to return a connection whose execute raises
    # on PRAGMA calls but allows normal operation
    original_connect = sqlite3.connect
    warning_logged = []

    class PragmaFailingCursor:
        """Cursor that fails on PRAGMA statements."""

        def __init__(self, real_cursor):
            self._real = real_cursor

        def execute(self, sql, *args, **kwargs):
            if "PRAGMA" in sql.upper():
                raise sqlite3.OperationalError(
                    "Simulated pragma failure"
                )
            return self._real.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    class PragmaFailingConnection:
        """Connection wrapper that fails on PRAGMA statements."""

        def __init__(self, real_conn):
            self._real = real_conn

        def cursor(self):
            return PragmaFailingCursor(self._real.cursor())

        def execute(self, sql, *args, **kwargs):
            if "PRAGMA" in sql.upper():
                raise sqlite3.OperationalError(
                    "Simulated pragma failure"
                )
            return self._real.execute(sql, *args, **kwargs)

        def close(self):
            return self._real.close()

        def __getattr__(self, name):
            return getattr(self._real, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._real.close()

    # We need the seed copy to succeed first, then pragma to fail.
    # Patch sqlite3.connect to return our failing wrapper AFTER copy.
    copy_done = {"value": False}
    original_copy2 = __import__("shutil").copy2

    def tracking_copy2(src, dst, *args, **kwargs):
        result = original_copy2(src, dst, *args, **kwargs)
        copy_done["value"] = True
        return result

    def patched_connect(path, *args, **kwargs):
        conn = original_connect(path, *args, **kwargs)
        if copy_done["value"]:
            return PragmaFailingConnection(conn)
        return conn

    with patch("shutil.copy2", side_effect=tracking_copy2):
        with patch("sqlite3.connect", side_effect=patched_connect):
            from main import _ensure_database_initialized

            # Should NOT raise — pragma failure is non-fatal
            _ensure_database_initialized()

    # ASSERTION: data.db should exist (seed copy succeeded)
    assert user_db_path.exists(), (
        "data.db should exist after seed copy even if pragmas failed"
    )

    # ASSERTION: The function should have completed without raising
    # (If we got here, the function didn't crash — that's the test)
    # On UNFIXED code, this may pass because pragmas aren't set at all.
    # On FIXED code, this validates graceful degradation.
