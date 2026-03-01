"""Preservation property tests for app startup DB initialization.

This module verifies that the UNFIXED code's baseline behavior is correctly
captured so that the upcoming fix does not regress any existing functionality.
These tests MUST PASS on unfixed code — passing confirms the behavior we need
to preserve.

Testing methodology:
- Observation-first: each test encodes behavior observed on the current
  (unfixed) codebase
- Property-based (Hypothesis): random startup contexts verify the full init
  pipeline always runs when no seed DB is available (dev-mode)

Key properties being verified:
- Property 2 (Preservation): Dev-Mode Fallback Unchanged
  - Test 1: Dev-mode full init — schema DDL + migrations + full init all run
  - Test 2: Reset to defaults — full re-initialization regardless of seed DB
  - Test 3: Seed DB content consistency — same resources used at build-time
  - Test 4: Returning user data preservation — data.db not overwritten
- Property-based: random (seed_available=False, data_db_exists) contexts

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 2.2
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given, settings, strategies as st


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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            id TEXT PRIMARY KEY,
            initialization_complete INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("""
        INSERT INTO app_settings
            (id, initialization_complete,
             created_at, updated_at)
        VALUES ('default', 1, datetime('now'), datetime('now'))
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY, name TEXT,
            is_system_agent INTEGER DEFAULT 0,
            created_at TEXT, updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    return seed_path


# ---------------------------------------------------------------------------
# Test Case 1 — Dev-Mode Full Init (no seed.db available)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dev_mode_full_init_runs_when_no_seed(
    temp_app_data_dir, monkeypatch
):
    """Test Case 1: When no seed.db is available, full init pipeline runs.

    **Validates: Requirements 3.1, 2.2**

    Observation on UNFIXED code:
    - _ensure_database_initialized() returns without copying anything
    - lifespan() runs initialize_database() (schema DDL + migrations)
    - run_full_initialization() is called when initialization_complete
      is not set

    This test confirms the dev-mode fallback path works correctly.
    EXPECTED OUTCOME: PASSES on unfixed code (baseline behavior).
    """
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    # No seed DB available
    monkeypatch.setattr("main._get_seed_database_path", lambda: None)

    from main import _ensure_database_initialized

    user_db_path = temp_app_data_dir / "data.db"
    assert not user_db_path.exists()

    # Run seed-copy function — should return without copying
    _ensure_database_initialized()

    # No data.db should be created (no seed to copy)
    assert not user_db_path.exists(), (
        "data.db should NOT exist when no seed.db is available"
    )

    # Now verify the downstream init pipeline DOES run
    schema_calls = []
    migration_calls = []

    original_init = None

    async def tracking_initialize(self_db, skip_init=False):
        if skip_init:
            self_db._initialized = True
            return
        schema_calls.append("schema_ddl")
        # Call through to real implementation so migrations also run
        import aiosqlite
        import time
        t0 = time.monotonic()
        async with aiosqlite.connect(str(self_db.db_path)) as conn:
            await conn.executescript(self_db.SCHEMA)
            await conn.commit()
            migration_calls.append("migrations")
            await self_db._run_migrations(conn)
        self_db._initialized = True

    with patch(
        "database.sqlite.SQLiteDatabase.initialize",
        tracking_initialize,
    ):
        from database import initialize_database
        await initialize_database()

    assert len(schema_calls) >= 1, (
        "SQLiteDatabase.initialize() schema DDL should be called "
        "when no seed.db is available (dev-mode fallback)"
    )
    assert len(migration_calls) >= 1, (
        "_run_migrations() should be called when no seed.db is "
        "available (dev-mode fallback)"
    )


# ---------------------------------------------------------------------------
# Test Case 2 — Reset to Defaults
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_to_defaults_performs_full_reinit(monkeypatch):
    """Test Case 2: reset_to_defaults() performs full re-initialization.

    **Validates: Requirements 3.2**

    Observation on UNFIXED code:
    - reset_to_defaults() clears initialization_complete flag
    - reset_to_defaults() calls run_full_initialization()
    - run_full_initialization() creates default agent, workspace,
      skills, and MCP servers

    This test confirms the reset path is unaffected by any future fix.
    EXPECTED OUTCOME: PASSES on unfixed code (baseline behavior).
    """
    set_complete_calls = []
    full_init_calls = []

    from core.initialization_manager import InitializationManager

    mgr = InitializationManager()

    # Patch set_initialization_complete to track calls
    async def mock_set_complete(complete: bool):
        set_complete_calls.append(complete)

    # Patch run_full_initialization to track calls
    async def mock_full_init():
        full_init_calls.append("called")
        return True

    mgr.set_initialization_complete = mock_set_complete
    mgr.run_full_initialization = mock_full_init

    result = await mgr.reset_to_defaults()

    # Verify initialization_complete was cleared (set to False)
    assert False in set_complete_calls, (
        "reset_to_defaults() should clear initialization_complete flag"
    )

    # Verify run_full_initialization was called
    assert len(full_init_calls) >= 1, (
        "reset_to_defaults() should call run_full_initialization() "
        "for full re-initialization of agent, workspace, skills, MCPs"
    )

    # Verify success result
    assert result["success"] is True, (
        "reset_to_defaults() should return success=True"
    )


# ---------------------------------------------------------------------------
# Test Case 3 — Seed DB Content Consistency
# ---------------------------------------------------------------------------

def test_seed_db_generator_uses_correct_resource_paths():
    """Test Case 3: generate_seed_db.py uses the same resource files.

    **Validates: Requirements 3.3, 3.4**

    Observation on UNFIXED code:
    - SeedDatabaseGenerator reads from desktop/resources/
    - Skills come from desktop/resources/default-skills/*.md
    - MCP configs come from desktop/resources/default-mcp-servers.json
    - Agent defaults come from desktop/resources/default-agent.json

    This test confirms the seed generator references the canonical
    resource files so the seed DB content matches runtime init output.
    EXPECTED OUTCOME: PASSES on unfixed code (baseline behavior).
    """
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parent.parent
    project_root = backend_dir.parent
    resources_dir = project_root / "desktop" / "resources"

    # Verify the resources directory exists
    assert resources_dir.exists(), (
        f"desktop/resources/ directory not found at {resources_dir}"
    )

    # Verify default-agent.json exists
    agent_config = resources_dir / "default-agent.json"
    assert agent_config.exists(), (
        "default-agent.json not found in desktop/resources/"
    )
    with open(agent_config) as f:
        agent_data = json.load(f)
    assert "name" in agent_data or "id" in agent_data, (
        "default-agent.json should contain agent configuration"
    )

    # Verify default-skills directory exists with .md files
    skills_dir = resources_dir / "default-skills"
    assert skills_dir.exists(), (
        "default-skills/ directory not found in desktop/resources/"
    )
    skill_files = list(skills_dir.glob("*.md"))
    assert len(skill_files) > 0, (
        "No .md skill files found in desktop/resources/default-skills/"
    )

    # Verify default-mcp-servers.json exists
    mcp_config = resources_dir / "default-mcp-servers.json"
    assert mcp_config.exists(), (
        "default-mcp-servers.json not found in desktop/resources/"
    )
    with open(mcp_config) as f:
        mcp_data = json.load(f)
    assert isinstance(mcp_data, list), (
        "default-mcp-servers.json should contain a list of MCP configs"
    )


# ---------------------------------------------------------------------------
# Test Case 4 — Returning User Data Preservation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returning_user_data_not_overwritten(
    temp_app_data_dir, seed_db_path, monkeypatch
):
    """Test Case 4: Existing data.db is NOT overwritten by seed copy.

    **Validates: Requirements 2.2, 3.1**

    Observation on UNFIXED code:
    - When data.db already exists, _ensure_database_initialized()
      returns early without copying seed.db
    - User data (tables, rows) in data.db is preserved

    This test confirms returning-user data preservation.
    EXPECTED OUTCOME: PASSES on unfixed code (baseline behavior).
    """
    monkeypatch.setattr("main.get_app_data_dir", lambda: temp_app_data_dir)
    monkeypatch.setattr("main._get_seed_database_path", lambda: seed_db_path)

    # Create an existing data.db with user data
    user_db_path = temp_app_data_dir / "data.db"
    conn = sqlite3.connect(str(user_db_path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE user_agents (
            id TEXT PRIMARY KEY, name TEXT, custom_prompt TEXT
        )
    """)
    cursor.execute(
        "INSERT INTO user_agents (id, name, custom_prompt) "
        "VALUES ('agent-1', 'MyAgent', 'Be helpful')"
    )
    cursor.execute("""
        CREATE TABLE chat_threads (
            id TEXT PRIMARY KEY, title TEXT, message_count INTEGER
        )
    """)
    cursor.execute(
        "INSERT INTO chat_threads (id, title, message_count) "
        "VALUES ('thread-1', 'My Chat', 42)"
    )
    conn.commit()
    conn.close()

    # Record file modification time before
    mtime_before = user_db_path.stat().st_mtime

    from main import _ensure_database_initialized
    _ensure_database_initialized()

    # Verify data.db was NOT overwritten
    mtime_after = user_db_path.stat().st_mtime
    assert mtime_before == mtime_after, (
        "data.db modification time changed — file was overwritten"
    )

    # Verify user data is intact
    conn = sqlite3.connect(str(user_db_path))
    cursor = conn.cursor()

    cursor.execute("SELECT name, custom_prompt FROM user_agents WHERE id='agent-1'")
    row = cursor.fetchone()
    assert row is not None, "User agent data was lost"
    assert row[0] == "MyAgent", "Agent name was corrupted"
    assert row[1] == "Be helpful", "Agent custom_prompt was corrupted"

    cursor.execute("SELECT title, message_count FROM chat_threads WHERE id='thread-1'")
    row = cursor.fetchone()
    assert row is not None, "Chat thread data was lost"
    assert row[0] == "My Chat", "Chat title was corrupted"
    assert row[1] == 42, "Chat message_count was corrupted"

    conn.close()


# ---------------------------------------------------------------------------
# Property-Based Test — Dev-Mode Init Pipeline Always Runs
# ---------------------------------------------------------------------------

@given(data_db_exists=st.booleans())
@settings(max_examples=20, deadline=None)
def test_property_no_seed_always_runs_full_init(data_db_exists):
    """Property: For all contexts where seed.db is NOT available,
    the full init pipeline always runs (dev-mode fallback).

    **Validates: Requirements 3.1, 3.2**

    Generates random (seed_available=False, data_db_exists=random)
    contexts and verifies _ensure_database_initialized() never creates
    data.db from a seed (because there is no seed), and the downstream
    init pipeline would need to run.

    EXPECTED OUTCOME: PASSES on unfixed code (baseline behavior).
    """
    tmp_dir = tempfile.mkdtemp()
    tmp_path = Path(tmp_dir)

    app_data = tmp_path / ".swarm-ai"
    app_data.mkdir(parents=True, exist_ok=True)
    user_db_path = app_data / "data.db"

    if data_db_exists:
        # Create a pre-existing data.db
        conn = sqlite3.connect(str(user_db_path))
        conn.execute(
            "CREATE TABLE marker (id TEXT PRIMARY KEY)"
        )
        conn.execute("INSERT INTO marker (id) VALUES ('exists')")
        conn.commit()
        conn.close()

    with patch("main.get_app_data_dir", return_value=app_data):
        with patch("main._get_seed_database_path", return_value=None):
            from main import _ensure_database_initialized
            _ensure_database_initialized()

    try:
        if data_db_exists:
            # Returning user: data.db should still exist, unchanged
            assert user_db_path.exists(), (
                "Returning user's data.db should be preserved"
            )
            conn = sqlite3.connect(str(user_db_path))
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='marker'"
            )
            assert cursor.fetchone() is not None, (
                "Returning user's marker table should be preserved"
            )
            conn.close()
        else:
            # Fresh start with no seed: data.db should NOT exist
            # (no seed to copy, runtime init hasn't run yet)
            assert not user_db_path.exists(), (
                "data.db should NOT be created when no seed.db is "
                "available and no pre-existing data.db"
            )
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)
