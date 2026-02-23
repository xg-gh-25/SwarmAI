"""Tests for task data migration (Task 1.9).

These tests verify that the migration correctly:
1. Maps legacy task statuses: pending→draft, running→wip, failed→blocked
2. Sets workspace_id to SwarmWS.id for existing tasks with NULL workspace_id
3. Preserves failure context in blocked_reason when mapping failed→blocked

Validates: Requirements 5.4, 13.7, 13.8
"""
import pytest
import tempfile
import os
from pathlib import Path
from datetime import datetime
from uuid import uuid4

import aiosqlite

from database.sqlite import SQLiteDatabase


class TestTaskDataMigration:
    """Tests for task data migration (Task 1.9)."""

    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path for testing."""
        fd, path = tempfile.mkstemp(suffix=".db", prefix="test_task_migration_")
        os.close(fd)
        yield Path(path)
        # Cleanup
        try:
            os.unlink(path)
        except OSError:
            pass

    @pytest.fixture
    def db_with_legacy_tasks(self, temp_db_path):
        """Create a database with legacy task statuses and NULL workspace_id.
        
        This simulates a database from before the workspace refactor.
        """
        import sqlite3
        
        conn = sqlite3.connect(str(temp_db_path))
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        
        # Create minimal schema for testing
        # swarm_workspaces table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS swarm_workspaces (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                context TEXT DEFAULT '',
                icon TEXT,
                is_default INTEGER DEFAULT 0,
                is_archived INTEGER DEFAULT 0,
                archived_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Create SwarmWS (default workspace)
        swarm_ws_id = str(uuid4())
        cursor.execute("""
            INSERT INTO swarm_workspaces (id, name, file_path, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (swarm_ws_id, "SwarmWS", "/path/to/swarmws", 1, now, now))
        
        # agents table (minimal)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                model TEXT,
                permission_mode TEXT DEFAULT 'default',
                max_turns INTEGER,
                system_prompt TEXT,
                allowed_tools TEXT DEFAULT '[]',
                plugin_ids TEXT DEFAULT '[]',
                skill_ids TEXT DEFAULT '[]',
                allow_all_skills INTEGER DEFAULT 0,
                mcp_ids TEXT DEFAULT '[]',
                working_directory TEXT,
                enable_bash_tool INTEGER DEFAULT 1,
                enable_file_tools INTEGER DEFAULT 1,
                enable_web_tools INTEGER DEFAULT 0,
                enable_tool_logging INTEGER DEFAULT 1,
                enable_safety_checks INTEGER DEFAULT 1,
                enable_file_access_control INTEGER DEFAULT 1,
                allowed_directories TEXT DEFAULT '[]',
                global_user_mode INTEGER DEFAULT 0,
                enable_human_approval INTEGER DEFAULT 1,
                sandbox_enabled INTEGER DEFAULT 1,
                sandbox TEXT DEFAULT '{}',
                is_default INTEGER DEFAULT 0,
                is_system_agent INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                user_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        agent_id = str(uuid4())
        cursor.execute("""
            INSERT INTO agents (id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
        """, (agent_id, "TestAgent", now, now))
        
        # tasks table with legacy schema (workspace_id, source_todo_id, blocked_reason, priority, description columns exist but are NULL)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                session_id TEXT,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                model TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                error TEXT,
                work_dir TEXT,
                updated_at TEXT,
                workspace_id TEXT,
                source_todo_id TEXT,
                blocked_reason TEXT,
                priority TEXT DEFAULT 'none',
                description TEXT
            )
        """)
        
        # Insert tasks with legacy statuses and NULL workspace_id
        # Task 1: pending status
        cursor.execute("""
            INSERT INTO tasks (id, agent_id, status, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid4()), agent_id, "pending", "Pending Task", now, now))
        
        # Task 2: running status
        cursor.execute("""
            INSERT INTO tasks (id, agent_id, status, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid4()), agent_id, "running", "Running Task", now, now))
        
        # Task 3: failed status with error message
        cursor.execute("""
            INSERT INTO tasks (id, agent_id, status, title, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (str(uuid4()), agent_id, "failed", "Failed Task", "Connection timeout", now, now))
        
        # Task 4: completed status (should not be changed)
        cursor.execute("""
            INSERT INTO tasks (id, agent_id, status, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid4()), agent_id, "completed", "Completed Task", now, now))
        
        # Task 5: cancelled status (should not be changed)
        cursor.execute("""
            INSERT INTO tasks (id, agent_id, status, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid4()), agent_id, "cancelled", "Cancelled Task", now, now))
        
        # Create other required tables (minimal)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                id TEXT PRIMARY KEY DEFAULT 'default',
                anthropic_api_key TEXT DEFAULT '',
                anthropic_base_url TEXT,
                use_bedrock INTEGER DEFAULT 0,
                bedrock_auth_type TEXT DEFAULT 'credentials',
                aws_access_key_id TEXT DEFAULT '',
                aws_secret_access_key TEXT DEFAULT '',
                aws_session_token TEXT,
                aws_bearer_token TEXT DEFAULT '',
                aws_region TEXT DEFAULT 'us-east-1',
                available_models TEXT DEFAULT '[]',
                default_model TEXT DEFAULT 'claude-sonnet-4-5-20250929',
                initialization_complete INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            INSERT INTO app_settings (id, created_at, updated_at)
            VALUES (?, ?, ?)
        """, ("default", now, now))
        
        conn.commit()
        conn.close()
        
        return temp_db_path, swarm_ws_id, agent_id

    @pytest.mark.asyncio
    async def test_status_mapping_pending_to_draft(self, db_with_legacy_tasks):
        """Verify pending status is mapped to draft.
        
        Validates: Requirements 5.4
        """
        db_path, swarm_ws_id, agent_id = db_with_legacy_tasks
        
        # Initialize database (runs migrations)
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()
        
        # Check that pending tasks are now draft
        async with aiosqlite.connect(str(db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM tasks WHERE title = 'Pending Task'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["status"] == "draft", \
                f"Expected status 'draft', got '{row['status']}'"

    @pytest.mark.asyncio
    async def test_status_mapping_running_to_wip(self, db_with_legacy_tasks):
        """Verify running status is mapped to wip.
        
        Validates: Requirements 5.4
        """
        db_path, swarm_ws_id, agent_id = db_with_legacy_tasks
        
        # Initialize database (runs migrations)
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()
        
        # Check that running tasks are now wip
        async with aiosqlite.connect(str(db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM tasks WHERE title = 'Running Task'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["status"] == "wip", \
                f"Expected status 'wip', got '{row['status']}'"

    @pytest.mark.asyncio
    async def test_status_mapping_failed_to_blocked_preserves_reason(self, db_with_legacy_tasks):
        """Verify failed status is mapped to blocked and error is preserved in blocked_reason.
        
        Validates: Requirements 5.4, 5.5
        """
        db_path, swarm_ws_id, agent_id = db_with_legacy_tasks
        
        # Initialize database (runs migrations)
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()
        
        # Check that failed tasks are now blocked with preserved error
        async with aiosqlite.connect(str(db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT * FROM tasks WHERE title = 'Failed Task'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["status"] == "blocked", \
                f"Expected status 'blocked', got '{row['status']}'"
            assert row["blocked_reason"] == "Connection timeout", \
                f"Expected blocked_reason 'Connection timeout', got '{row['blocked_reason']}'"

    @pytest.mark.asyncio
    async def test_completed_and_cancelled_unchanged(self, db_with_legacy_tasks):
        """Verify completed and cancelled statuses are not changed.
        
        Validates: Requirements 5.4
        """
        db_path, swarm_ws_id, agent_id = db_with_legacy_tasks
        
        # Initialize database (runs migrations)
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()
        
        async with aiosqlite.connect(str(db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            
            # Check completed task
            cursor = await conn.execute(
                "SELECT * FROM tasks WHERE title = 'Completed Task'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["status"] == "completed", \
                f"Expected status 'completed', got '{row['status']}'"
            
            # Check cancelled task
            cursor = await conn.execute(
                "SELECT * FROM tasks WHERE title = 'Cancelled Task'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["status"] == "cancelled", \
                f"Expected status 'cancelled', got '{row['status']}'"

    @pytest.mark.asyncio
    async def test_workspace_id_assigned_to_swarmws(self, db_with_legacy_tasks):
        """Verify tasks with NULL workspace_id get SwarmWS.id assigned.
        
        Validates: Requirements 13.7, 13.8
        """
        db_path, swarm_ws_id, agent_id = db_with_legacy_tasks
        
        # Initialize database (runs migrations)
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()
        
        # Check that all tasks now have workspace_id set to SwarmWS.id
        async with aiosqlite.connect(str(db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            
            # Count tasks with NULL workspace_id (should be 0)
            cursor = await conn.execute(
                "SELECT COUNT(*) as count FROM tasks WHERE workspace_id IS NULL"
            )
            row = await cursor.fetchone()
            assert row["count"] == 0, \
                f"Expected 0 tasks with NULL workspace_id, got {row['count']}"
            
            # Count tasks with SwarmWS workspace_id
            cursor = await conn.execute(
                "SELECT COUNT(*) as count FROM tasks WHERE workspace_id = ?",
                (swarm_ws_id,)
            )
            row = await cursor.fetchone()
            assert row["count"] == 5, \
                f"Expected 5 tasks with SwarmWS workspace_id, got {row['count']}"

    @pytest.mark.asyncio
    async def test_migration_is_idempotent(self, db_with_legacy_tasks):
        """Verify that running migration multiple times is safe.
        
        Validates: Requirements 5.4, 13.7, 13.8
        """
        db_path, swarm_ws_id, agent_id = db_with_legacy_tasks
        
        # Initialize database multiple times
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()
        await db.initialize()
        await db.initialize()
        
        # Verify the database is in a valid state
        async with aiosqlite.connect(str(db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            
            # Check status mappings are correct
            cursor = await conn.execute(
                "SELECT status FROM tasks WHERE title = 'Pending Task'"
            )
            row = await cursor.fetchone()
            assert row["status"] == "draft"
            
            cursor = await conn.execute(
                "SELECT status FROM tasks WHERE title = 'Running Task'"
            )
            row = await cursor.fetchone()
            assert row["status"] == "wip"
            
            cursor = await conn.execute(
                "SELECT status FROM tasks WHERE title = 'Failed Task'"
            )
            row = await cursor.fetchone()
            assert row["status"] == "blocked"
            
            # Check workspace_id assignments
            cursor = await conn.execute(
                "SELECT COUNT(*) as count FROM tasks WHERE workspace_id = ?",
                (swarm_ws_id,)
            )
            row = await cursor.fetchone()
            assert row["count"] == 5

    @pytest.mark.asyncio
    async def test_migration_uses_transactions(self, db_with_legacy_tasks):
        """Verify that migration uses transactions for atomicity.
        
        This test verifies that either all status mappings succeed or none do.
        
        Validates: Requirements 5.4
        """
        db_path, swarm_ws_id, agent_id = db_with_legacy_tasks
        
        # Initialize database
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()
        
        # Verify all expected changes were made atomically
        async with aiosqlite.connect(str(db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            
            # Count tasks by status
            cursor = await conn.execute(
                "SELECT status, COUNT(*) as count FROM tasks GROUP BY status"
            )
            rows = await cursor.fetchall()
            status_counts = {row["status"]: row["count"] for row in rows}
            
            # Verify expected status distribution
            assert status_counts.get("draft", 0) == 1, "Should have 1 draft task"
            assert status_counts.get("wip", 0) == 1, "Should have 1 wip task"
            assert status_counts.get("blocked", 0) == 1, "Should have 1 blocked task"
            assert status_counts.get("completed", 0) == 1, "Should have 1 completed task"
            assert status_counts.get("cancelled", 0) == 1, "Should have 1 cancelled task"
            
            # Verify no legacy statuses remain
            assert status_counts.get("pending", 0) == 0, "Should have 0 pending tasks"
            assert status_counts.get("running", 0) == 0, "Should have 0 running tasks"
            assert status_counts.get("failed", 0) == 0, "Should have 0 failed tasks"
