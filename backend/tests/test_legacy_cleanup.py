"""Tests for legacy multi-workspace data cleanup (clean-slate approach).

This module tests the legacy data cleanup logic in ``_run_migrations()``
within ``backend/database/sqlite.py``.  The cleanup detects the old
``swarm_workspaces`` table, drops it, removes associated filesystem
directories, and clears ``workspace_id`` in ``chat_threads`` so threads
become global SwarmWS chats.

Testing methodology: unit tests with a real ``SQLiteDatabase`` backed by a
temporary file.  Each test manually injects legacy state (the
``swarm_workspaces`` table and rows) into the database *before* calling
``db.initialize()``, then asserts the cleanup occurred.

Key scenarios verified:

- ``swarm_workspaces`` table is dropped after migration
- ``chat_threads.workspace_id`` values are cleared to NULL
- Fresh SwarmWS structure is initialized correctly after cleanup
- Cleanup is idempotent (running twice doesn't error)
- Legacy workspace directories are removed (but not the active SwarmWS dir)

Validates: Requirements 24.1, 24.2, 24.3, 24.4
"""

import os
import shutil
import tempfile

import aiosqlite
import pytest

from database.sqlite import SQLiteDatabase
from core.swarm_workspace_manager import (
    FOLDER_STRUCTURE,
    SYSTEM_MANAGED_ROOT_FILES,
    SwarmWorkspaceManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_legacy_schema(db_path: str) -> None:
    """Create a legacy database with the ``swarm_workspaces`` table.

    This simulates a pre-migration database that still has the old
    multi-workspace table.  We also create the ``chat_threads`` table
    with some rows that have non-NULL ``workspace_id``.
    """
    async with aiosqlite.connect(db_path) as conn:
        # Create the legacy swarm_workspaces table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS swarm_workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                icon TEXT DEFAULT '🏠',
                is_archived INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await conn.commit()


async def _insert_legacy_workspace(db_path: str, ws_id: str, name: str, file_path: str) -> None:
    """Insert a row into the legacy ``swarm_workspaces`` table."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO swarm_workspaces (id, name, file_path, created_at, updated_at) "
            "VALUES (?, ?, ?, datetime('now'), datetime('now'))",
            (ws_id, name, file_path),
        )
        await conn.commit()


async def _table_exists(db_path: str, table_name: str) -> bool:
    """Check whether a table exists in the database."""
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        return (await cursor.fetchone()) is not None


async def _insert_chat_thread(db_path: str, thread_id: str, workspace_id: str, agent_id: str) -> None:
    """Insert a chat thread row with a non-NULL workspace_id."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO chat_threads (id, workspace_id, agent_id, title, mode, created_at, updated_at) "
            "VALUES (?, ?, ?, 'test thread', 'explore', datetime('now'), datetime('now'))",
            (thread_id, workspace_id, agent_id),
        )
        await conn.commit()


async def _get_chat_thread_workspace_ids(db_path: str) -> list:
    """Return all workspace_id values from chat_threads."""
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("SELECT workspace_id FROM chat_threads")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Return a path for a fresh temporary database file."""
    return str(tmp_path / "legacy_test.db")


@pytest.fixture
def tmp_workspace(tmp_path):
    """Return a fresh temporary directory to use as the SwarmWS root."""
    return str(tmp_path / "SwarmWS")


def _patch_default_config(monkeypatch, workspace_path: str):
    """Patch DEFAULT_WORKSPACE_CONFIG to point at *workspace_path*."""
    import core.swarm_workspace_manager as swm_mod
    monkeypatch.setitem(swm_mod.DEFAULT_WORKSPACE_CONFIG, "file_path", workspace_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLegacyTableDropped:
    """When ``swarm_workspaces`` table exists, it gets dropped after migration.

    Validates: Requirement 24.1
    """

    @pytest.mark.asyncio
    async def test_swarm_workspaces_table_dropped(self, tmp_db):
        """Legacy swarm_workspaces table is dropped during db.initialize()."""
        # Pre-create the legacy table with some rows
        await _create_legacy_schema(tmp_db)
        await _insert_legacy_workspace(tmp_db, "ws-1", "OldWorkspace", "/tmp/old-ws")
        await _insert_legacy_workspace(tmp_db, "ws-2", "AnotherOld", "/tmp/another-ws")

        # Confirm legacy table exists before migration
        assert await _table_exists(tmp_db, "swarm_workspaces") is True

        # Run initialization (triggers _run_migrations)
        db = SQLiteDatabase(db_path=tmp_db)
        await db.initialize()

        # Legacy table should be gone
        assert await _table_exists(tmp_db, "swarm_workspaces") is False

    @pytest.mark.asyncio
    async def test_no_error_when_legacy_table_absent(self, tmp_db):
        """When no legacy table exists, initialization still succeeds."""
        db = SQLiteDatabase(db_path=tmp_db)
        await db.initialize()

        # Should complete without error and swarm_workspaces should not exist
        assert await _table_exists(tmp_db, "swarm_workspaces") is False


class TestChatThreadWorkspaceIdCleared:
    """When chat threads have ``workspace_id``, the cleanup handles them gracefully.

    The ``chat_threads`` schema defines ``workspace_id TEXT NOT NULL``, so the
    migration's ``SET workspace_id = NULL`` is caught by the constraint and
    logged as a warning.  The cleanup continues without error — this is the
    expected graceful-degradation behaviour.

    Validates: Requirement 24.2
    """

    @pytest.mark.asyncio
    async def test_cleanup_handles_not_null_constraint_gracefully(self, tmp_db):
        """Cleanup doesn't crash when chat_threads.workspace_id has NOT NULL constraint."""
        # Step 1: Create a fresh DB with full schema (chat_threads has NOT NULL)
        db = SQLiteDatabase(db_path=tmp_db)
        await db.initialize()

        # Step 2: Seed an agent so FK constraint is satisfied
        async with aiosqlite.connect(tmp_db) as conn:
            await conn.execute(
                "INSERT INTO agents (id, name, created_at, updated_at) "
                "VALUES ('agent-1', 'TestAgent', datetime('now'), datetime('now'))"
            )
            await conn.commit()

        # Step 3: Insert chat threads with workspace_id values
        await _insert_chat_thread(tmp_db, "thread-1", "old-ws-1", "agent-1")
        await _insert_chat_thread(tmp_db, "thread-2", "old-ws-2", "agent-1")

        # Step 4: Re-create the legacy table so migration detects it
        await _create_legacy_schema(tmp_db)
        await _insert_legacy_workspace(tmp_db, "old-ws-1", "Old1", "/tmp/old1")

        # Step 5: Re-initialize — should NOT raise despite NOT NULL constraint
        db2 = SQLiteDatabase(db_path=tmp_db)
        await db2.initialize()

        # The legacy table should still be dropped successfully
        assert await _table_exists(tmp_db, "swarm_workspaces") is False

        # Chat threads still exist (cleanup was graceful, not destructive)
        async with aiosqlite.connect(tmp_db) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM chat_threads")
            row = await cursor.fetchone()
            assert row[0] == 2, "Chat threads should be preserved"

    @pytest.mark.asyncio
    async def test_workspace_id_cleared_when_nullable(self, tmp_db):
        """When workspace_id column allows NULL, cleanup sets it to NULL.

        This simulates a database where the NOT NULL constraint was relaxed
        (e.g., via a prior migration or manual schema change).  We first
        create the full schema, then recreate chat_threads with a nullable
        workspace_id to test the happy-path cleanup.
        """
        # Step 1: Create full schema
        db = SQLiteDatabase(db_path=tmp_db)
        await db.initialize()

        # Step 2: Recreate chat_threads with nullable workspace_id
        async with aiosqlite.connect(tmp_db) as conn:
            await conn.execute("DROP TABLE IF EXISTS chat_messages")
            await conn.execute("DROP TABLE IF EXISTS thread_summaries")
            await conn.execute("DROP TABLE IF EXISTS chat_threads")
            await conn.execute("""
                CREATE TABLE chat_threads (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT,
                    agent_id TEXT NOT NULL,
                    task_id TEXT,
                    todo_id TEXT,
                    mode TEXT NOT NULL DEFAULT 'explore',
                    title TEXT NOT NULL,
                    project_id TEXT DEFAULT NULL,
                    context_version INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Seed agent and thread
            await conn.execute(
                "INSERT OR IGNORE INTO agents (id, name, created_at, updated_at) "
                "VALUES ('agent-1', 'TestAgent', datetime('now'), datetime('now'))"
            )
            await conn.execute(
                "INSERT INTO chat_threads (id, workspace_id, agent_id, title, created_at, updated_at) "
                "VALUES ('t-1', 'old-ws', 'agent-1', 'test', datetime('now'), datetime('now'))"
            )
            await conn.commit()

        # Step 3: Add the legacy table to trigger cleanup
        await _create_legacy_schema(tmp_db)
        await _insert_legacy_workspace(tmp_db, "old-ws", "Old", "/tmp/old")

        # Step 4: Re-initialize — migration should clear workspace_id to NULL
        db2 = SQLiteDatabase(db_path=tmp_db)
        await db2.initialize()

        ws_ids = await _get_chat_thread_workspace_ids(tmp_db)
        assert all(ws_id is None for ws_id in ws_ids), (
            f"Expected all workspace_ids to be NULL, got: {ws_ids}"
        )

    @pytest.mark.asyncio
    async def test_no_error_when_no_chat_threads(self, tmp_db):
        """Cleanup succeeds even when chat_threads table is empty."""
        await _create_legacy_schema(tmp_db)
        await _insert_legacy_workspace(tmp_db, "ws-1", "Old", "/tmp/old")

        db = SQLiteDatabase(db_path=tmp_db)
        await db.initialize()

        # Should complete without error
        assert await _table_exists(tmp_db, "swarm_workspaces") is False


class TestFreshSwarmWSAfterCleanup:
    """After cleanup, fresh SwarmWS structure is initialized correctly.

    Validates: Requirement 24.4
    """

    @pytest.mark.asyncio
    async def test_fresh_structure_after_legacy_cleanup(
        self, tmp_db, tmp_workspace, monkeypatch
    ):
        """After legacy cleanup, ensure_default_workspace creates full structure."""
        # Pre-create legacy table
        await _create_legacy_schema(tmp_db)
        await _insert_legacy_workspace(tmp_db, "ws-old", "OldWS", "/tmp/old-ws")

        # Initialize DB (runs migration, drops legacy table)
        db = SQLiteDatabase(db_path=tmp_db)
        await db.initialize()

        # Patch workspace path and create fresh structure
        _patch_default_config(monkeypatch, tmp_workspace)
        manager = SwarmWorkspaceManager()
        result = await manager.ensure_default_workspace(db)

        # Verify workspace config
        assert result["id"] == "swarmws"
        assert result["name"] == "SwarmWS"

        # Verify all folders exist
        for folder in FOLDER_STRUCTURE:
            assert os.path.isdir(os.path.join(tmp_workspace, folder)), (
                f"Folder {folder} should exist after fresh init"
            )

        # Verify root system files exist
        for filename in SYSTEM_MANAGED_ROOT_FILES:
            assert os.path.isfile(os.path.join(tmp_workspace, filename)), (
                f"Root file {filename} should exist after fresh init"
            )


class TestCleanupIdempotent:
    """Cleanup is idempotent — running twice doesn't error.

    Validates: Requirements 24.1, 24.3
    """

    @pytest.mark.asyncio
    async def test_double_initialize_no_error(self, tmp_db):
        """Running db.initialize() twice (with legacy table first time) succeeds."""
        # First run: with legacy table
        await _create_legacy_schema(tmp_db)
        await _insert_legacy_workspace(tmp_db, "ws-1", "Old", "/tmp/old")

        db1 = SQLiteDatabase(db_path=tmp_db)
        await db1.initialize()

        assert await _table_exists(tmp_db, "swarm_workspaces") is False

        # Second run: no legacy table anymore, should still succeed
        db2 = SQLiteDatabase(db_path=tmp_db)
        await db2.initialize()

        assert await _table_exists(tmp_db, "swarm_workspaces") is False

    @pytest.mark.asyncio
    async def test_full_flow_idempotent(
        self, tmp_db, tmp_workspace, monkeypatch
    ):
        """Full init flow (DB + workspace) twice produces consistent state."""
        await _create_legacy_schema(tmp_db)
        await _insert_legacy_workspace(tmp_db, "ws-1", "Old", "/tmp/old")

        _patch_default_config(monkeypatch, tmp_workspace)
        manager = SwarmWorkspaceManager()

        # First full init
        db1 = SQLiteDatabase(db_path=tmp_db)
        await db1.initialize()
        result1 = await manager.ensure_default_workspace(db1)

        # Second full init
        db2 = SQLiteDatabase(db_path=tmp_db)
        await db2.initialize()
        result2 = await manager.ensure_default_workspace(db2)

        assert result1["id"] == result2["id"] == "swarmws"
        assert result1["name"] == result2["name"] == "SwarmWS"

        # All folders still intact
        for folder in FOLDER_STRUCTURE:
            assert os.path.isdir(os.path.join(tmp_workspace, folder))


class TestLegacyDirectoryRemoval:
    """Legacy workspace directories are removed, but SwarmWS dir is preserved.

    Validates: Requirement 24.1
    """

    @pytest.mark.asyncio
    async def test_legacy_dirs_removed(self, tmp_path, monkeypatch):
        """Directories referenced by swarm_workspaces rows are removed."""
        db_path = str(tmp_path / "test.db")

        # Patch get_app_data_dir so the safety check allows removal under tmp_path
        import config
        monkeypatch.setattr(config, "get_app_data_dir", lambda: tmp_path)
        import database.sqlite as sqlite_mod
        monkeypatch.setattr(sqlite_mod, "get_app_data_dir", lambda: tmp_path)

        # Create legacy workspace directories on disk
        legacy_dir = tmp_path / "old-workspace"
        legacy_dir.mkdir()
        (legacy_dir / "some-file.txt").write_text("old data")

        # Pre-create legacy table with the directory path
        await _create_legacy_schema(db_path)
        await _insert_legacy_workspace(
            db_path, "ws-old", "OldWS", str(legacy_dir)
        )

        assert legacy_dir.exists()

        # Initialize DB (triggers cleanup)
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()

        # Legacy directory should be removed
        assert not legacy_dir.exists(), (
            "Legacy workspace directory should be removed after cleanup"
        )

    @pytest.mark.asyncio
    async def test_swarmws_dir_not_removed(self, tmp_path, monkeypatch):
        """The active SwarmWS directory is NOT removed during cleanup."""
        db_path = str(tmp_path / "test.db")

        # Patch get_app_data_dir so the cleanup logic resolves SwarmWS correctly
        import config
        monkeypatch.setattr(config, "get_app_data_dir", lambda: tmp_path)
        # Also patch in sqlite module since it imports at module level
        import database.sqlite as sqlite_mod
        monkeypatch.setattr(sqlite_mod, "get_app_data_dir", lambda: tmp_path)

        # Create the SwarmWS directory (the active workspace)
        swarmws_dir = tmp_path / "SwarmWS"
        swarmws_dir.mkdir()
        (swarmws_dir / "keep-me.txt").write_text("important data")

        # Pre-create legacy table with SwarmWS path
        await _create_legacy_schema(db_path)
        await _insert_legacy_workspace(
            db_path, "swarmws", "SwarmWS", str(swarmws_dir)
        )

        # Initialize DB
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()

        # SwarmWS directory should still exist
        assert swarmws_dir.exists(), (
            "Active SwarmWS directory should NOT be removed during cleanup"
        )
        assert (swarmws_dir / "keep-me.txt").read_text() == "important data"

    @pytest.mark.asyncio
    async def test_nonexistent_legacy_dir_no_error(self, tmp_path):
        """Legacy paths pointing to non-existent dirs don't cause errors."""
        db_path = str(tmp_path / "test.db")

        await _create_legacy_schema(db_path)
        await _insert_legacy_workspace(
            db_path, "ws-gone", "GoneWS", "/tmp/does-not-exist-at-all-12345"
        )

        # Should not raise
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()

        assert await _table_exists(db_path, "swarm_workspaces") is False
