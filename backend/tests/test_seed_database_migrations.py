"""Tests for seed database migration handling.

These tests verify that migrations run correctly on copied seed databases,
ensuring backward compatibility when the seed database has an older schema.

Validates: Requirements 5.3, 5.4
"""
import pytest
import asyncio
import tempfile
import os
import shutil
from pathlib import Path
from datetime import datetime

import aiosqlite

from database.sqlite import SQLiteDatabase


class TestSeedDatabaseMigrations:
    """Tests for migration handling on seed databases."""

    @pytest.fixture
    def temp_db_path(self):
        """Create a temporary database path for testing."""
        fd, path = tempfile.mkstemp(suffix=".db", prefix="test_seed_")
        os.close(fd)
        yield Path(path)
        # Cleanup
        try:
            os.unlink(path)
        except OSError:
            pass

    @pytest.fixture
    def old_schema_db_path(self, temp_db_path):
        """Create a database with an older schema (missing some columns).
        
        This simulates a seed database that was created before certain
        migrations were added.
        """
        import sqlite3
        
        # Create a database with an older schema (missing initialization_complete)
        conn = sqlite3.connect(str(temp_db_path))
        cursor = conn.cursor()
        
        # Create app_settings table WITHOUT initialization_complete column
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                id TEXT PRIMARY KEY DEFAULT 'default',
                initialization_complete INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Create agents table WITHOUT is_system_agent column
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
                allowed_skills TEXT DEFAULT '[]',
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
                status TEXT DEFAULT 'active',
                user_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Insert a sample agent record
        now = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO agents (id, name, description, model, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("default", "SwarmAgent", "Test agent", "claude-opus-4-5-20250514", now, now))
        
        # Insert app_settings record (without initialization_complete)
        cursor.execute("""
            INSERT INTO app_settings (id, created_at, updated_at)
            VALUES (?, ?, ?)
        """, ("default", now, now))
        
        conn.commit()
        conn.close()
        
        return temp_db_path

    @pytest.mark.asyncio
    async def test_migrations_run_after_initialize(self, old_schema_db_path):
        """Verify _run_migrations() is called when initialize() is called.
        
        This test creates a database with an older schema (missing columns)
        and verifies that after calling initialize(), the migrations add
        the missing columns.
        
        Validates: Requirements 5.3, 5.4
        """
        # Verify the old schema is missing the initialization_complete column
        async with aiosqlite.connect(str(old_schema_db_path)) as conn:
            # Verify is_system_agent is missing from agents (old schema)
            cursor = await conn.execute("PRAGMA table_info(agents)")
            agent_columns = await cursor.fetchall()
            agent_column_names = [col[1] for col in agent_columns]
            assert "is_system_agent" not in agent_column_names, \
                "Test setup error: old schema should not have is_system_agent"
        
        # Create SQLiteDatabase instance and initialize (this should run migrations)
        db = SQLiteDatabase(db_path=old_schema_db_path)
        await db.initialize()
        
        # Verify migrations added the missing columns
        async with aiosqlite.connect(str(old_schema_db_path)) as conn:
            # Check agents has is_system_agent
            cursor = await conn.execute("PRAGMA table_info(agents)")
            agent_columns = await cursor.fetchall()
            agent_column_names = [col[1] for col in agent_columns]
            assert "is_system_agent" in agent_column_names, \
                "Migration should have added is_system_agent column"

    @pytest.mark.asyncio
    async def test_existing_data_preserved_after_migration(self, old_schema_db_path):
        """Verify that existing data is preserved when migrations run.
        
        This ensures that running migrations on a copied seed database
        doesn't corrupt or lose the pre-seeded data.
        
        Validates: Requirements 5.3, 5.4
        """
        # Create SQLiteDatabase instance and initialize
        db = SQLiteDatabase(db_path=old_schema_db_path)
        await db.initialize()
        
        # Verify the pre-existing agent record is still there
        agent = await db.agents.get("default")
        assert agent is not None, "Pre-existing agent should be preserved"
        assert agent["name"] == "SwarmAgent", "Agent name should be preserved"
        assert agent["model"] == "claude-opus-4-5-20250514", "Agent model should be preserved"
        
        # Verify the pre-existing app_settings record is still there
        settings = await db.app_settings.get("default")
        assert settings is not None, "Pre-existing app_settings should be preserved"

    @pytest.mark.asyncio
    async def test_migration_adds_default_values(self, old_schema_db_path):
        """Verify that migrations add appropriate default values for new columns.
        
        When a migration adds a new column, existing rows should get the
        default value specified in the migration.
        
        Validates: Requirements 5.3, 5.4
        """
        # Create SQLiteDatabase instance and initialize
        db = SQLiteDatabase(db_path=old_schema_db_path)
        await db.initialize()
        
        # Check that the new columns have appropriate default values
        async with aiosqlite.connect(str(old_schema_db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            
            # Check app_settings.initialization_complete defaults to 0
            cursor = await conn.execute(
                "SELECT initialization_complete FROM app_settings WHERE id = 'default'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["initialization_complete"] == 0, \
                "initialization_complete should default to 0 for existing records"
            
            # Check agents.is_system_agent defaults to 0
            cursor = await conn.execute(
                "SELECT is_system_agent FROM agents WHERE id = 'default'"
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row["is_system_agent"] == 0, \
                "is_system_agent should default to 0 for existing records"

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self, temp_db_path):
        """Verify that calling initialize() multiple times is safe.
        
        The initialize() method should be idempotent - calling it multiple
        times should not cause errors or duplicate migrations.
        
        Validates: Requirements 5.3, 5.4
        """
        db = SQLiteDatabase(db_path=temp_db_path)
        
        # Call initialize multiple times
        await db.initialize()
        await db.initialize()
        await db.initialize()
        
        # Verify the database is in a valid state
        assert await db.health_check(), "Database should be healthy after multiple initializations"
        
        # Verify schema is correct (tables exist)
        async with aiosqlite.connect(str(temp_db_path)) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agents'"
            )
            row = await cursor.fetchone()
            assert row is not None, "agents table should exist"

    @pytest.fixture
    def user_db_path(self):
        """Create a separate temporary database path for user database."""
        fd, path = tempfile.mkstemp(suffix=".db", prefix="test_user_")
        os.close(fd)
        # Remove the file so we can test the copy operation
        os.unlink(path)
        yield Path(path)
        # Cleanup
        try:
            os.unlink(path)
        except OSError:
            pass

    @pytest.mark.asyncio
    async def test_seed_db_copy_then_initialize_flow(self, old_schema_db_path, user_db_path):
        """Simulate the full flow: copy seed DB then initialize.
        
        This test simulates what happens at app startup:
        1. Seed database is copied to user data directory
        2. initialize_database() is called
        3. Migrations run on the copied database
        
        Validates: Requirements 5.3, 5.4
        """
        # Step 1: Copy the "seed" database (old schema) to "user" location
        shutil.copy2(old_schema_db_path, user_db_path)
        
        # Verify the copy has the old schema (missing is_system_agent on agents)
        async with aiosqlite.connect(str(user_db_path)) as conn:
            cursor = await conn.execute("PRAGMA table_info(agents)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            assert "is_system_agent" not in column_names, \
                "Copied database should have old schema (missing is_system_agent)"
        
        # Step 2: Initialize the database (simulates initialize_database() call)
        db = SQLiteDatabase(db_path=user_db_path)
        await db.initialize()
        
        # Step 3: Verify migrations ran and schema is updated
        async with aiosqlite.connect(str(user_db_path)) as conn:
            cursor = await conn.execute("PRAGMA table_info(agents)")
            columns = await cursor.fetchall()
            column_names = [col[1] for col in columns]
            assert "is_system_agent" in column_names, \
                "Migrations should have updated the schema"
        
        # Verify data is preserved
        agent = await db.agents.get("default")
        assert agent is not None, "Agent data should be preserved after migration"
        assert agent["name"] == "SwarmAgent"
