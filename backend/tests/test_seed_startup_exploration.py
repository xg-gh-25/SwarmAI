"""Bug condition exploration tests for app startup DB initialization hang.

This module tests the fault condition where the app startup runs redundant
initialization pipeline even when a pre-built seed.db is available.

Testing methodology:
- Property-based exploration to surface counterexamples demonstrating the bug
- Tests are EXPECTED TO FAIL on unfixed code (failure confirms bug exists)
- DO NOT attempt to fix the test or code when tests fail

Key properties being verified:
- Property 1: Seed-Available Startup Skips Init Pipeline
  - When seed.db is available, startup SHALL copy it to data.db
  - SQLiteDatabase.initialize() schema DDL SHALL NOT be called
  - run_full_initialization() SHALL NOT be called
  - WAL mode and busy_timeout pragmas SHALL be set

Validates: Requirements 1.1, 1.2, 1.3, 1.6, 2.1, 2.2, 2.5
"""
import asyncio
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    
    # Create a minimal SQLite database with the required structure
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
    
    # Insert settings with initialization_complete = 1
    cursor.execute("""
        INSERT INTO app_settings (id, anthropic_api_key, initialization_complete, created_at, updated_at)
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
# Test Case 1: First Launch — Seed DB available, no data.db exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_launch_seed_available_skips_init_pipeline(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """Test Case 1: First launch with seed DB should skip init pipeline.
    
    **Validates: Requirements 1.1, 1.2, 2.1, 2.2, 2.5**
    
    Bug Condition (C): App startup when seed.db is available — the system
    currently runs full schema DDL, migrations, and initialization even
    though the seed DB already contains everything needed.
    
    Expected Behavior (P): When seed.db is available, startup SHALL copy
    it to data.db, set pragmas, and skip all schema/migration/init work.
    
    **EXPECTED OUTCOME ON UNFIXED CODE**: Test FAILS
    - SQLiteDatabase.initialize() IS called (bug: should NOT be called)
    - run_full_initialization() IS called (bug: should NOT be called)
    """
    # Track function calls
    initialize_calls = []
    run_full_init_calls = []
    
    # Patch get_app_data_dir to return our temp directory
    monkeypatch.setattr(
        "config.get_app_data_dir",
        lambda: temp_app_data_dir
    )
    
    # Patch _get_seed_database_path to return our seed DB
    monkeypatch.setattr(
        "main._get_seed_database_path",
        lambda: seed_db_path
    )
    
    # Import after patching
    from main import _ensure_database_initialized
    
    # Ensure no data.db exists (first launch scenario)
    user_db_path = temp_app_data_dir / "data.db"
    assert not user_db_path.exists(), "data.db should not exist for first launch test"
    
    # Run the function under test
    _ensure_database_initialized()
    
    # Verify seed was copied
    assert user_db_path.exists(), "data.db should be created from seed"
    
    # Now test the lifespan init path
    # Patch SQLiteDatabase.initialize to track calls
    original_initialize = None
    
    async def mock_initialize(self):
        initialize_calls.append("initialize_called")
        # Call original to avoid breaking the flow
        if original_initialize:
            await original_initialize(self)
    
    # Patch run_full_initialization to track calls
    async def mock_run_full_init():
        run_full_init_calls.append("run_full_initialization_called")
        return True
    
    with patch("database.sqlite.SQLiteDatabase.initialize", mock_initialize):
        with patch(
            "core.initialization_manager.InitializationManager.run_full_initialization",
            mock_run_full_init
        ):
            # Import and run initialize_database
            from database import initialize_database
            await initialize_database()
    
    # ASSERTION: On FIXED code, initialize() should NOT be called
    # On UNFIXED code, this assertion FAILS (proving the bug exists)
    assert len(initialize_calls) == 0, (
        f"BUG CONFIRMED: SQLiteDatabase.initialize() was called {len(initialize_calls)} time(s) "
        "even though seed DB was copied. Expected: 0 calls (seed DB has complete schema)."
    )


# ---------------------------------------------------------------------------
# Test Case 2: Returning User — Seed DB available, data.db already exists
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returning_user_seed_overwrites_existing_db(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """Test Case 2: Returning user with seed DB should overwrite existing data.db.
    
    **Validates: Requirements 1.3, 1.6, 2.1, 2.5**
    
    Bug Condition (C): When data.db already exists, the current code skips
    the seed copy entirely and runs the full init pipeline on the existing DB.
    
    Expected Behavior (P): When seed.db is available, ALWAYS copy it to
    data.db (overwriting any existing file), then skip init pipeline.
    
    **EXPECTED OUTCOME ON UNFIXED CODE**: Test FAILS
    - Seed DB is NOT copied (bug: should overwrite existing data.db)
    - Init pipeline runs on existing data.db (bug: should be skipped)
    """
    # Patch get_app_data_dir to return our temp directory
    monkeypatch.setattr(
        "config.get_app_data_dir",
        lambda: temp_app_data_dir
    )
    
    # Patch _get_seed_database_path to return our seed DB
    monkeypatch.setattr(
        "main._get_seed_database_path",
        lambda: seed_db_path
    )
    
    # Create an existing data.db (simulating returning user)
    user_db_path = temp_app_data_dir / "data.db"
    existing_db = sqlite3.connect(str(user_db_path))
    existing_cursor = existing_db.cursor()
    existing_cursor.execute("""
        CREATE TABLE IF NOT EXISTS test_marker (
            id TEXT PRIMARY KEY,
            marker TEXT
        )
    """)
    existing_cursor.execute(
        "INSERT INTO test_marker (id, marker) VALUES ('test', 'existing_db')"
    )
    existing_db.commit()
    existing_db.close()
    
    # Record the original file modification time
    original_mtime = user_db_path.stat().st_mtime
    
    # Import after patching
    from main import _ensure_database_initialized
    
    # Run the function under test
    _ensure_database_initialized()
    
    # Check if the file was overwritten by comparing content
    # If seed was copied, the test_marker table should NOT exist
    conn = sqlite3.connect(str(user_db_path))
    cursor = conn.cursor()
    
    # Check if test_marker table exists (it shouldn't if seed was copied)
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='test_marker'"
    )
    marker_exists = cursor.fetchone() is not None
    conn.close()
    
    # ASSERTION: On FIXED code, seed should overwrite existing DB
    # On UNFIXED code, this assertion FAILS (proving the bug exists)
    assert not marker_exists, (
        "BUG CONFIRMED: Existing data.db was NOT overwritten with seed.db. "
        "The test_marker table still exists, meaning _ensure_database_initialized() "
        "skipped the seed copy because data.db already existed. "
        "Expected: seed.db should ALWAYS be copied, overwriting existing data.db."
    )


# ---------------------------------------------------------------------------
# Test Case 3: Pragma Setup — After seed copy, WAL mode and busy_timeout set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pragma_setup_after_seed_copy(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """Test Case 3: After seed copy, WAL mode and busy_timeout should be set.
    
    **Validates: Requirements 2.2**
    
    Expected Behavior (P): After a successful seed copy, the system SHALL
    set PRAGMA journal_mode=WAL and PRAGMA busy_timeout=5000 on the copied DB.
    
    **EXPECTED OUTCOME ON UNFIXED CODE**: Test FAILS
    - WAL mode is NOT set (bug: should be set after seed copy)
    - busy_timeout is NOT set (bug: should be set after seed copy)
    """
    # Patch get_app_data_dir to return our temp directory
    monkeypatch.setattr(
        "config.get_app_data_dir",
        lambda: temp_app_data_dir
    )
    
    # Patch _get_seed_database_path to return our seed DB
    monkeypatch.setattr(
        "main._get_seed_database_path",
        lambda: seed_db_path
    )
    
    # Import after patching
    from main import _ensure_database_initialized
    
    # Ensure no data.db exists
    user_db_path = temp_app_data_dir / "data.db"
    if user_db_path.exists():
        user_db_path.unlink()
    
    # Run the function under test
    _ensure_database_initialized()
    
    # Verify seed was copied
    assert user_db_path.exists(), "data.db should be created from seed"
    
    # Check pragma settings
    conn = sqlite3.connect(str(user_db_path))
    cursor = conn.cursor()
    
    # Check journal_mode
    cursor.execute("PRAGMA journal_mode")
    journal_mode = cursor.fetchone()[0].lower()
    
    # Check busy_timeout
    cursor.execute("PRAGMA busy_timeout")
    busy_timeout = cursor.fetchone()[0]
    
    conn.close()
    
    # ASSERTION: On FIXED code, WAL mode should be set
    # On UNFIXED code, this assertion FAILS (proving the bug exists)
    assert journal_mode == "wal", (
        f"BUG CONFIRMED: journal_mode is '{journal_mode}', expected 'wal'. "
        "_ensure_database_initialized() does not set WAL mode after seed copy."
    )
    
    # ASSERTION: On FIXED code, busy_timeout should be set to 5000
    # On UNFIXED code, this assertion FAILS (proving the bug exists)
    assert busy_timeout == 5000, (
        f"BUG CONFIRMED: busy_timeout is {busy_timeout}, expected 5000. "
        "_ensure_database_initialized() does not set busy_timeout after seed copy."
    )


# ---------------------------------------------------------------------------
# Test Case 4: Return Value — _ensure_database_initialized should return bool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_database_initialized_returns_seed_sourced_flag(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """Test Case 4: _ensure_database_initialized should return True when seed copied.
    
    **Validates: Requirements 2.1, 2.4**
    
    Expected Behavior (P): _ensure_database_initialized() SHALL return True
    when seed was successfully copied, False when seed was not available.
    
    **EXPECTED OUTCOME ON UNFIXED CODE**: Test FAILS
    - Function returns None (bug: should return True when seed copied)
    """
    # Patch get_app_data_dir to return our temp directory
    monkeypatch.setattr(
        "config.get_app_data_dir",
        lambda: temp_app_data_dir
    )
    
    # Patch _get_seed_database_path to return our seed DB
    monkeypatch.setattr(
        "main._get_seed_database_path",
        lambda: seed_db_path
    )
    
    # Import after patching
    from main import _ensure_database_initialized
    
    # Ensure no data.db exists
    user_db_path = temp_app_data_dir / "data.db"
    if user_db_path.exists():
        user_db_path.unlink()
    
    # Run the function under test
    result = _ensure_database_initialized()
    
    # ASSERTION: On FIXED code, should return True when seed copied
    # On UNFIXED code, this assertion FAILS (proving the bug exists)
    assert result is True, (
        f"BUG CONFIRMED: _ensure_database_initialized() returned {result!r}, expected True. "
        "The function does not return a seed-sourced flag to signal downstream code "
        "that the init pipeline should be skipped."
    )
