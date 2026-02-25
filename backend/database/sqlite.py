"""SQLite database client for local desktop storage."""
from __future__ import annotations

import aiosqlite
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, TypeVar, Generic
from uuid import uuid4

from database.base import BaseTable, BaseDatabase

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=dict)


class SQLiteTable(BaseTable[T], Generic[T]):
    """SQLite table implementation of BaseTable interface."""

    def __init__(self, table_name: str, db_path: Path):
        self.table_name = table_name
        self.db_path = db_path

    def _get_connection(self) -> aiosqlite.Connection:
        """Get an async SQLite connection context manager.

        Usage:
            async with self._get_connection() as conn:
                conn.row_factory = aiosqlite.Row
                # use conn
        """
        return aiosqlite.connect(str(self.db_path))

    def _row_to_dict(self, row: aiosqlite.Row) -> dict:
        """Convert a SQLite row to a dictionary, parsing JSON fields."""
        if row is None:
            return None
        result = dict(row)
        # Parse JSON fields (lists and nested objects)
        for key, value in result.items():
            if isinstance(value, str) and (value.startswith('[') or value.startswith('{')):
                try:
                    result[key] = json.loads(value)
                except json.JSONDecodeError:
                    pass
        return result

    def _serialize_value(self, value) -> str | int | float | None:
        """Serialize a value for SQLite storage."""
        if isinstance(value, (list, dict)):
            return json.dumps(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, bool):
            return 1 if value else 0
        return value

    async def put(self, item: T) -> T:
        """Insert or update an item."""
        if "id" not in item:
            item["id"] = str(uuid4())
        if "created_at" not in item:
            item["created_at"] = datetime.now().isoformat()
        item["updated_at"] = datetime.now().isoformat()

        # Get existing item to decide insert vs update
        existing = await self.get(item["id"])

        async with self._get_connection() as conn:
            if existing:
                # Update
                columns = [k for k in item.keys() if k != "id"]
                set_clause = ", ".join(f"{col} = ?" for col in columns)
                values = [self._serialize_value(item[col]) for col in columns]
                values.append(item["id"])

                await conn.execute(
                    f"UPDATE {self.table_name} SET {set_clause} WHERE id = ?",
                    values
                )
            else:
                # Insert
                columns = list(item.keys())
                placeholders = ", ".join("?" for _ in columns)
                values = [self._serialize_value(item[col]) for col in columns]

                await conn.execute(
                    f"INSERT INTO {self.table_name} ({', '.join(columns)}) VALUES ({placeholders})",
                    values
                )

            await conn.commit()
        return item

    async def get(self, item_id: str) -> Optional[T]:
        """Get an item by ID."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE id = ?",
                (item_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_dict(row) if row else None

    async def list(self, user_id: Optional[str] = None) -> list[T]:
        """List all items, optionally filtered by user_id."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            if user_id:
                query = f"SELECT * FROM {self.table_name} WHERE user_id = ? ORDER BY created_at DESC"
                params = (user_id,)
            else:
                query = f"SELECT * FROM {self.table_name} ORDER BY created_at DESC"
                params = ()

            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def delete(self, item_id: str) -> bool:
        """Delete an item by ID."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE id = ?",
                (item_id,)
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def update(self, item_id: str, updates: dict) -> Optional[T]:
        """Update an item."""
        if not updates:
            return await self.get(item_id)

        updates["updated_at"] = datetime.now().isoformat()

        async with self._get_connection() as conn:
            columns = list(updates.keys())
            set_clause = ", ".join(f"{col} = ?" for col in columns)
            values = [self._serialize_value(updates[col]) for col in columns]
            values.append(item_id)

            cursor = await conn.execute(
                f"UPDATE {self.table_name} SET {set_clause} WHERE id = ?",
                values
            )
            await conn.commit()

            if cursor.rowcount == 0:
                return None

        return await self.get(item_id)


class SQLiteMessagesTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for messages with session_id querying support and TTL."""

    # TTL duration in seconds (7 days)
    TTL_SECONDS = 7 * 24 * 60 * 60  # 604800 seconds

    async def put(self, item: T) -> T:
        """Insert or update a message with TTL expiration (7 days)."""
        if "id" not in item:
            item["id"] = str(uuid4())
        if "created_at" not in item:
            item["created_at"] = datetime.now().isoformat()
        item["updated_at"] = datetime.now().isoformat()

        # Set TTL: expires 7 days from now (Unix epoch timestamp in seconds)
        item["expires_at"] = int(time.time()) + self.TTL_SECONDS

        return await super().put(item)

    async def list_by_session(self, session_id: str) -> list[T]:
        """List all messages for a session, ordered by timestamp."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def delete_by_session(self, session_id: str) -> int:
        """Delete all messages for a session. Returns count of deleted items."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE session_id = ?",
                (session_id,)
            )
            await conn.commit()
            return cursor.rowcount

    async def cleanup_expired(self) -> int:
        """Delete expired messages based on TTL. Returns count of deleted items."""
        current_time = int(time.time())
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE expires_at < ?",
                (current_time,)
            )
            await conn.commit()
            return cursor.rowcount


class SQLiteSkillVersionsTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for skill versions with skill_id querying support."""

    async def list_by_skill(self, skill_id: str) -> list[T]:
        """List all versions for a skill, ordered by version number descending."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE skill_id = ? ORDER BY version DESC",
                (skill_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def get_by_skill_and_version(self, skill_id: str, version: int) -> Optional[T]:
        """Get a specific version of a skill."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE skill_id = ? AND version = ?",
                (skill_id, version)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_dict(row) if row else None

    async def delete_by_skill(self, skill_id: str) -> int:
        """Delete all versions for a skill. Returns count of deleted items."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE skill_id = ?",
                (skill_id,)
            )
            await conn.commit()
            return cursor.rowcount


class SQLitePluginsTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for plugins with installed_at ordering.

    Plugins use installed_at instead of created_at.
    """

    async def put(self, item: T) -> T:
        """Insert or update a plugin (uses installed_at, not created_at)."""
        if "id" not in item:
            item["id"] = str(uuid4())
        if "installed_at" not in item:
            item["installed_at"] = datetime.now().isoformat()
        item["updated_at"] = datetime.now().isoformat()

        # Get existing item to decide insert vs update
        existing = await self.get(item["id"])

        async with self._get_connection() as conn:
            if existing:
                # Update existing
                set_clause = ", ".join(f"{k} = ?" for k in item.keys() if k != "id")
                values = [v for k, v in item.items() if k != "id"]
                values.append(item["id"])
                await conn.execute(
                    f"UPDATE {self.table_name} SET {set_clause} WHERE id = ?",
                    values
                )
            else:
                # Insert new
                columns = ", ".join(item.keys())
                placeholders = ", ".join("?" * len(item))
                values = list(item.values())
                await conn.execute(
                    f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})",
                    values
                )
            await conn.commit()

        return item

    async def list(self, user_id: Optional[str] = None) -> list[T]:
        """List all plugins, optionally filtered by user_id, ordered by installed_at."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            if user_id:
                query = f"SELECT * FROM {self.table_name} WHERE user_id = ? ORDER BY installed_at DESC"
                params = (user_id,)
            else:
                query = f"SELECT * FROM {self.table_name} ORDER BY installed_at DESC"
                params = ()

            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def list_by_marketplace(self, marketplace_id: str) -> list[T]:
        """List all plugins for a specific marketplace."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE marketplace_id = ? ORDER BY installed_at DESC",
                (marketplace_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]


class SQLiteTasksTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for tasks with status filtering and counting."""

    async def list_all(self, status: Optional[str] = None, agent_id: Optional[str] = None) -> list[T]:
        """List all tasks, optionally filtered by status or agent_id."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            query = "SELECT * FROM tasks WHERE 1=1"
            params = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            query += " ORDER BY created_at DESC"
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def count_by_status(self, status: str) -> int:
        """Count tasks by status."""
        async with self._get_connection() as conn:
            async with conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = ?", (status,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0


class SQLiteDatabase(BaseDatabase):
    """SQLite database client implementing BaseDatabase interface."""

    # SQL Schema for all tables
    SCHEMA = """
    -- Agents table
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
        sandbox TEXT DEFAULT '{}',
        status TEXT DEFAULT 'active',
        user_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_agents_user_id ON agents(user_id);

    -- Skills table
    CREATE TABLE IF NOT EXISTS skills (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        folder_name TEXT UNIQUE,
        local_path TEXT,
        -- Source tracking
        source_type TEXT DEFAULT 'user',
        source_plugin_id TEXT,
        source_marketplace_id TEXT,
        -- Git tracking
        git_url TEXT,
        git_branch TEXT DEFAULT 'main',
        git_commit TEXT,
        -- Metadata
        created_by TEXT,
        version TEXT DEFAULT '1.0.0',
        is_system INTEGER DEFAULT 0,
        current_version INTEGER DEFAULT 0,
        has_draft INTEGER DEFAULT 0,
        user_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (source_plugin_id) REFERENCES plugins(id) ON DELETE SET NULL,
        FOREIGN KEY (source_marketplace_id) REFERENCES marketplaces(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_skills_user_id ON skills(user_id);
    CREATE INDEX IF NOT EXISTS idx_skills_folder_name ON skills(folder_name);
    CREATE INDEX IF NOT EXISTS idx_skills_source_plugin ON skills(source_plugin_id);
    CREATE INDEX IF NOT EXISTS idx_skills_source_marketplace ON skills(source_marketplace_id);

    -- Skill versions table
    CREATE TABLE IF NOT EXISTS skill_versions (
        id TEXT PRIMARY KEY,
        skill_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        git_commit TEXT,
        local_path TEXT,
        change_summary TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (skill_id) REFERENCES skills(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_skill_versions_skill_id ON skill_versions(skill_id);

    -- MCP servers table
    CREATE TABLE IF NOT EXISTS mcp_servers (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        connection_type TEXT NOT NULL,
        config TEXT NOT NULL DEFAULT '{}',
        allowed_tools TEXT DEFAULT '[]',
        rejected_tools TEXT DEFAULT '[]',
        endpoint TEXT,
        version TEXT,
        is_active INTEGER DEFAULT 1,
        user_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_mcp_servers_user_id ON mcp_servers(user_id);

    -- Sessions table
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        agent_id TEXT,
        user_id TEXT,
        title TEXT,
        status TEXT DEFAULT 'active',
        metadata TEXT DEFAULT '{}',
        work_dir TEXT,
        last_accessed TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_agent_id ON sessions(agent_id);

    -- Permission requests table (Human-in-the-Loop)
    CREATE TABLE IF NOT EXISTS permission_requests (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        tool_input TEXT NOT NULL,
        reason TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        decided_at TEXT,
        user_feedback TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_permission_requests_session_id ON permission_requests(session_id);
    CREATE INDEX IF NOT EXISTS idx_permission_requests_status ON permission_requests(status);

    -- Messages table (with TTL support)
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        model TEXT,
        metadata TEXT DEFAULT '{}',
        expires_at INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
    CREATE INDEX IF NOT EXISTS idx_messages_expires_at ON messages(expires_at);

    -- Users table (for local single-user, may only have one record)
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE,
        email TEXT UNIQUE,
        name TEXT,
        password_hash TEXT,
        preferences TEXT DEFAULT '{}',
        last_login TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    -- App settings table (single row for app-wide settings)
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
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    -- Marketplaces table (plugin sources)
    CREATE TABLE IF NOT EXISTS marketplaces (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        type TEXT NOT NULL,
        url TEXT NOT NULL,
        branch TEXT DEFAULT 'main',
        is_active INTEGER DEFAULT 1,
        last_synced_at TEXT,
        cached_plugins TEXT DEFAULT '[]',
        user_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_marketplaces_user_id ON marketplaces(user_id);

    -- Plugins table (installed plugins)
    CREATE TABLE IF NOT EXISTS plugins (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        version TEXT NOT NULL,
        marketplace_id TEXT NOT NULL,
        author TEXT,
        license TEXT,
        homepage TEXT,
        repository TEXT,
        keywords TEXT DEFAULT '[]',
        installed_skills TEXT DEFAULT '[]',
        installed_commands TEXT DEFAULT '[]',
        installed_agents TEXT DEFAULT '[]',
        installed_hooks TEXT DEFAULT '[]',
        installed_mcp_servers TEXT DEFAULT '[]',
        status TEXT DEFAULT 'installed',
        install_path TEXT,
        user_id TEXT,
        installed_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (marketplace_id) REFERENCES marketplaces(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_plugins_marketplace_id ON plugins(marketplace_id);
    CREATE INDEX IF NOT EXISTS idx_plugins_user_id ON plugins(user_id);
    CREATE INDEX IF NOT EXISTS idx_plugins_name ON plugins(name);

    -- Tasks table (background agent tasks)
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        session_id TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        title TEXT NOT NULL,
        model TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        error TEXT,
        work_dir TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_agent_id ON tasks(agent_id);
    """

    def __init__(self, db_path: str | Path | None = None):
        """Initialize SQLite database.

        Args:
            db_path: Path to the SQLite database file. If None, uses default location.
        """
        if db_path is None:
            # Default to user data directory
            import platform
            if platform.system() == "Darwin":
                data_dir = Path.home() / "Library" / "Application Support" / "Owork"
            elif platform.system() == "Windows":
                data_dir = Path.home() / "AppData" / "Local" / "Owork"
            else:
                data_dir = Path.home() / ".local" / "share" / "owork"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "data.db"
        else:
            db_path = Path(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self._initialized = False

        # Initialize tables
        self._agents = SQLiteTable[dict]("agents", self.db_path)
        self._skills = SQLiteTable[dict]("skills", self.db_path)
        self._mcp_servers = SQLiteTable[dict]("mcp_servers", self.db_path)
        self._sessions = SQLiteTable[dict]("sessions", self.db_path)
        self._messages = SQLiteMessagesTable[dict]("messages", self.db_path)
        self._users = SQLiteTable[dict]("users", self.db_path)
        self._skill_versions = SQLiteSkillVersionsTable[dict]("skill_versions", self.db_path)
        self._app_settings = SQLiteTable[dict]("app_settings", self.db_path)
        self._marketplaces = SQLiteTable[dict]("marketplaces", self.db_path)
        self._plugins = SQLitePluginsTable[dict]("plugins", self.db_path)
        self._permission_requests = SQLiteTable[dict]("permission_requests", self.db_path)
        self._tasks = SQLiteTasksTable[dict]("tasks", self.db_path)

    async def initialize(self) -> None:
        """Initialize database schema."""
        if self._initialized:
            return

        async with aiosqlite.connect(str(self.db_path)) as conn:
            await conn.executescript(self.SCHEMA)
            await conn.commit()

            # Run migrations for existing databases
            await self._run_migrations(conn)

        self._initialized = True

    async def _run_migrations(self, conn: aiosqlite.Connection) -> None:
        """Run database migrations for existing databases.

        These migrations are temporary compatibility fixes for databases created
        before certain schema changes. New deployments don't need them since
        the SCHEMA already includes all columns.
        """
        # Migration: Add plugin_ids column to agents table (added 2026-01-19)
        # Can be removed after all existing deployments are migrated
        cursor = await conn.execute("PRAGMA table_info(agents)")
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]

        if "plugin_ids" not in column_names:
            logger.info("Running migration: Adding plugin_ids column to agents table")
            await conn.execute("ALTER TABLE agents ADD COLUMN plugin_ids TEXT DEFAULT '[]'")
            await conn.commit()
            logger.info("Migration complete: plugin_ids column added")

        # Migration: Add work_dir column to sessions table (added 2026-01-25)
        # Stores the working directory for session continuity (e.g., when answering AskUserQuestion)
        cursor = await conn.execute("PRAGMA table_info(sessions)")
        session_columns = await cursor.fetchall()
        session_column_names = [col[1] for col in session_columns]

        if "work_dir" not in session_column_names:
            logger.info("Running migration: Adding work_dir column to sessions table")
            await conn.execute("ALTER TABLE sessions ADD COLUMN work_dir TEXT")
            await conn.commit()
            logger.info("Migration complete: work_dir column added")

        # Migration: Add available_models and default_model columns to app_settings table (added 2026-02-02)
        # Stores model configuration for agent creation
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        app_settings_columns = await cursor.fetchall()
        app_settings_column_names = [col[1] for col in app_settings_columns]

        if "available_models" not in app_settings_column_names:
            logger.info("Running migration: Adding available_models column to app_settings table")
            await conn.execute("ALTER TABLE app_settings ADD COLUMN available_models TEXT DEFAULT '[]'")
            await conn.commit()
            logger.info("Migration complete: available_models column added")

        if "default_model" not in app_settings_column_names:
            logger.info("Running migration: Adding default_model column to app_settings table")
            await conn.execute("ALTER TABLE app_settings ADD COLUMN default_model TEXT DEFAULT 'claude-sonnet-4-5-20250929'")
            await conn.commit()
            logger.info("Migration complete: default_model column added")

    @property
    def agents(self) -> SQLiteTable:
        """Get the agents table."""
        return self._agents

    @property
    def skills(self) -> SQLiteTable:
        """Get the skills table."""
        return self._skills

    @property
    def mcp_servers(self) -> SQLiteTable:
        """Get the MCP servers table."""
        return self._mcp_servers

    @property
    def sessions(self) -> SQLiteTable:
        """Get the sessions table."""
        return self._sessions

    @property
    def messages(self) -> SQLiteMessagesTable:
        """Get the messages table."""
        return self._messages

    @property
    def users(self) -> SQLiteTable:
        """Get the users table."""
        return self._users

    @property
    def skill_versions(self) -> SQLiteSkillVersionsTable:
        """Get the skill versions table."""
        return self._skill_versions

    @property
    def app_settings(self) -> SQLiteTable:
        """Get the app settings table."""
        return self._app_settings

    @property
    def marketplaces(self) -> SQLiteTable:
        """Get the marketplaces table."""
        return self._marketplaces

    @property
    def plugins(self) -> SQLiteTable:
        """Get the plugins table."""
        return self._plugins

    @property
    def permission_requests(self) -> SQLiteTable:
        """Get the permission requests table."""
        return self._permission_requests

    @property
    def tasks(self) -> SQLiteTasksTable:
        """Get the tasks table."""
        return self._tasks

    async def health_check(self) -> bool:
        """Check if the database is healthy."""
        try:
            async with aiosqlite.connect(str(self.db_path)) as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def cleanup_expired_messages(self) -> int:
        """Clean up expired messages. Returns count of deleted messages."""
        return await self._messages.cleanup_expired()
