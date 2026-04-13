"""SQLite database client for local desktop storage."""
from __future__ import annotations

import aiosqlite
import json
import logging
import shutil
import time
from abc import abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional, TypeVar, Generic, ClassVar
from uuid import uuid4

from config import get_app_data_dir
from database.base import BaseTable, BaseDatabase

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=dict)

# Constants
DEFAULT_AUDIT_LOG_LIMIT: int = 100


class _WALConnection:
    """Wrapper around aiosqlite.connect that enables WAL mode and busy timeout.

    WAL (Write-Ahead Logging) mode allows readers and writers to operate
    concurrently without blocking each other — critical when multiple
    chat sessions save messages in parallel.

    WAL mode is set once per db_path per process (it persists in the DB file).
    Busy timeout (100ms) is short — just enough to handle brief lock
    contention.  Application-level retry (SQLiteTable.put: 50+200+500ms)
    controls the actual backoff strategy.  Previously this was 5000ms which
    meant SQLite's internal wait dominated and app retries never fired.
    Total worst-case: 100ms (SQLite) × 3 attempts + 750ms (app sleep) ≈ 1050ms.
    """

    # Class-level set: tracks which db_paths have had WAL mode enabled
    # this process.  WAL persists in the DB file, so this is idempotent
    # across restarts.
    _wal_initialized: ClassVar[set] = set()

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._conn = None

    async def __aenter__(self):
        self._conn = await aiosqlite.connect(self._db_path)
        # Enable WAL mode if not already done for this db_path in this process.
        if self._db_path not in _WALConnection._wal_initialized:
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=100")
            # Checkpoint every 1000 pages (~4MB) to prevent WAL bloat.
            # Without this, WAL grows unbounded until a reader closes.
            await self._conn.execute("PRAGMA wal_autocheckpoint=1000")
            _WALConnection._wal_initialized.add(self._db_path)
            logger.info("SQLite WAL mode enabled for %s", self._db_path)
        else:
            # Still set busy_timeout per connection (not persisted in DB file)
            await self._conn.execute("PRAGMA busy_timeout=100")
        return self._conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._conn:
            await self._conn.__aexit__(exc_type, exc_val, exc_tb)


class SQLiteTable(BaseTable[T], Generic[T]):
    """SQLite table implementation of BaseTable interface."""

    def __init__(self, table_name: str, db_path: Path):
        self.table_name = table_name
        self.db_path = db_path

    def _get_connection(self) -> _WALConnection:
        """Get an async SQLite connection context manager with WAL mode.

        Each connection is configured with WAL journal mode and busy timeout
        for safe concurrent access from parallel chat sessions.

        Usage:
            async with self._get_connection() as conn:
                conn.row_factory = aiosqlite.Row
                # use conn
        """
        return _WALConnection(str(self.db_path))

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

    _PUT_MAX_RETRIES = 3
    _PUT_RETRY_DELAYS = (0.05, 0.2, 0.5)  # exponential-ish, total < 1s

    async def put(self, item: T) -> T:
        """Insert or update an item with retry on transient DB errors.

        Retries up to 3 times on SQLITE_BUSY / OperationalError with
        exponential backoff (50ms → 200ms → 500ms).  Total worst-case
        wait: ~750ms.  Non-transient errors propagate immediately.
        """
        import asyncio

        if "id" not in item:
            item["id"] = str(uuid4())
        if "created_at" not in item:
            item["created_at"] = datetime.now().isoformat()
        item["updated_at"] = datetime.now().isoformat()

        last_error: Exception | None = None
        for attempt in range(self._PUT_MAX_RETRIES):
            try:
                # Refresh updated_at on retry so timestamp reflects actual write time
                if attempt > 0:
                    item["updated_at"] = datetime.now().isoformat()
                return await self._put_once(item)
            except Exception as exc:
                last_error = exc
                err_str = str(exc).lower()
                # Only retry on transient SQLite locking errors.
                # Disk I/O errors are NOT transient (hardware/corruption).
                is_transient = (
                    "database is locked" in err_str
                    or "busy" in err_str
                )
                if not is_transient or attempt >= self._PUT_MAX_RETRIES - 1:
                    raise
                delay = self._PUT_RETRY_DELAYS[min(attempt, len(self._PUT_RETRY_DELAYS) - 1)]
                logger.info(
                    "DB put retry %d/%d for %s (table=%s): %s",
                    attempt + 1, self._PUT_MAX_RETRIES,
                    item.get("id", "?")[:12], self.table_name,
                    str(exc)[:80],
                )
                await asyncio.sleep(delay)

        # Should not reach here, but satisfy type checker
        raise last_error  # type: ignore[misc]

    async def _put_once(self, item: T) -> T:
        """Single put attempt (insert or update)."""
        existing = await self.get(item["id"])

        async with self._get_connection() as conn:
            if existing:
                columns = [k for k in item.keys() if k != "id"]
                set_clause = ", ".join(f"{col} = ?" for col in columns)
                values = [self._serialize_value(item[col]) for col in columns]
                values.append(item["id"])
                await conn.execute(
                    f"UPDATE {self.table_name} SET {set_clause} WHERE id = ?",
                    values
                )
            else:
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

    async def list_by_session_paginated(
        self,
        session_id: str,
        limit: Optional[int] = None,
        before_id: Optional[str] = None,
    ) -> list[T]:
        """List messages for a session with optional cursor-based pagination.

        Supports three modes:
        - **Both limit and before_id**: Return up to ``limit`` messages older
          than the message identified by ``before_id``, using
          ``(created_at, rowid)`` as a stable cursor for tie-breaking when
          multiple messages share the same timestamp.
        - **Only limit**: Return the ``limit`` most recent messages.
        - **Neither** (backward compat): Return all messages in chronological
          order, identical to ``list_by_session()``.

        In all paginated modes the result is returned in chronological
        (ascending) order so callers can append/prepend without re-sorting.

        Args:
            session_id: The session to query.
            limit: Max number of messages to return (most recent first when
                paginating).  Must be between 1 and 200 inclusive.
            before_id: Return only messages created before the message with
                this ID.  Uses ``(created_at, rowid)`` for deterministic
                cursor positioning even when timestamps collide.

        Returns:
            Messages ordered by ``created_at ASC`` (chronological).
        """
        # --- Neither param: backward-compatible full fetch ---
        if limit is None and before_id is None:
            return await self.list_by_session(session_id)

        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row

            if before_id is not None and limit is not None:
                # Both limit and before_id: cursor-based page of older messages.
                # SQLite doesn't support tuple comparison directly, so we
                # expand (created_at, rowid) < (cursor_ca, cursor_rowid) into:
                #   (created_at < cursor_ca) OR
                #   (created_at = cursor_ca AND rowid < cursor_rowid)
                query = (
                    f"SELECT *, rowid FROM {self.table_name} "
                    f"WHERE session_id = ? "
                    f"  AND ("
                    f"    created_at < (SELECT created_at FROM {self.table_name} WHERE id = ?)"
                    f"    OR ("
                    f"      created_at = (SELECT created_at FROM {self.table_name} WHERE id = ?)"
                    f"      AND rowid < (SELECT rowid FROM {self.table_name} WHERE id = ?)"
                    f"    )"
                    f"  ) "
                    f"ORDER BY created_at DESC, rowid DESC "
                    f"LIMIT ?"
                )
                params = (session_id, before_id, before_id, before_id, limit)
            elif limit is not None:
                # Only limit: most recent N messages.
                query = (
                    f"SELECT *, rowid FROM {self.table_name} "
                    f"WHERE session_id = ? "
                    f"ORDER BY created_at DESC, rowid DESC "
                    f"LIMIT ?"
                )
                params = (session_id, limit)
            else:
                # Only before_id without limit — fetch all older messages.
                query = (
                    f"SELECT *, rowid FROM {self.table_name} "
                    f"WHERE session_id = ? "
                    f"  AND ("
                    f"    created_at < (SELECT created_at FROM {self.table_name} WHERE id = ?)"
                    f"    OR ("
                    f"      created_at = (SELECT created_at FROM {self.table_name} WHERE id = ?)"
                    f"      AND rowid < (SELECT rowid FROM {self.table_name} WHERE id = ?)"
                    f"    )"
                    f"  ) "
                    f"ORDER BY created_at ASC"
                )
                params = (session_id, before_id, before_id, before_id)

            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                results = [self._row_to_dict(row) for row in rows]

            # When fetching with DESC ordering (limit provided), reverse to
            # return chronological order.
            if limit is not None:
                results.reverse()

            return results


    async def delete_by_session(self, session_id: str) -> int:
        """Delete all messages for a session. Returns count of deleted items."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE session_id = ?",
                (session_id,)
            )
            await conn.commit()
            return cursor.rowcount

    async def delete_last_user_message(self, session_id: str) -> bool:
        """Delete the most recent user message for a session.

        Used to clean up orphaned messages when SessionBusyError is raised
        after the user message was already persisted but before it was sent
        to the agent.

        Returns True if a message was deleted, False if no user message found.
        """
        async with self._get_connection() as conn:
            # Find the rowid of the most recent user message
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE rowid = ("
                f"  SELECT rowid FROM {self.table_name}"
                f"  WHERE session_id = ? AND role = 'user'"
                f"  ORDER BY created_at DESC LIMIT 1"
                f")",
                (session_id,),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def count_by_session(self, session_id: str) -> int:
        """Count messages for a session without loading them into memory."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"SELECT COUNT(*) FROM {self.table_name} WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_last_by_session(
        self, session_id: str, role: str | None = None,
    ) -> dict | None:
        """Return the most recent message for a session, optionally filtered by role."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            if role:
                query = (
                    f"SELECT * FROM {self.table_name} "
                    "WHERE session_id = ? AND role = ? "
                    "ORDER BY created_at DESC LIMIT 1"
                )
                params = (session_id, role)
            else:
                query = (
                    f"SELECT * FROM {self.table_name} "
                    "WHERE session_id = ? ORDER BY created_at DESC LIMIT 1"
                )
                params = (session_id,)
            async with conn.execute(query, params) as cursor:
                row = await cursor.fetchone()
                return self._row_to_dict(row) if row else None

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


class SQLiteMCPServersTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for MCP servers with system resource querying support."""

    async def list_by_system(self) -> list[T]:
        """List all MCP servers where is_system=True."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE is_system = 1 ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]


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

    async def list_all(
        self,
        status: Optional[str] = None,
        statuses: Optional[list[str]] = None,
        agent_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        completed_after: Optional[str] = None,
    ) -> list[T]:
        """List all tasks, optionally filtered.

        Args:
            status: Single status filter (legacy, use ``statuses`` for multi).
            statuses: List of statuses with OR semantics.
            agent_id: Filter by agent ID.
            workspace_id: Filter by workspace ID.
            completed_after: ISO 8601 date string; return only tasks with
                ``completed_at`` after this value.
        """
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            query = "SELECT * FROM tasks WHERE 1=1"
            params: list[str] = []
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                query += f" AND status IN ({placeholders})"
                params.extend(statuses)
            elif status:
                query += " AND status = ?"
                params.append(status)
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            if workspace_id:
                query += " AND workspace_id = ?"
                params.append(workspace_id)
            if completed_after:
                query += " AND completed_at > ?"
                params.append(completed_after)
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

    async def delete_by_agent_id(self, agent_id: str) -> int:
        """Delete all tasks for a given agent (cascade delete).

        Returns the number of tasks deleted.
        """
        async with self._get_connection() as conn:
            async with conn.execute(
                "DELETE FROM tasks WHERE agent_id = ?", (agent_id,)
            ) as cursor:
                await conn.commit()
                return cursor.rowcount

    async def list_by_agent_id(self, agent_id: str) -> list[T]:
        """List all tasks for a given agent."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM tasks WHERE agent_id = ? ORDER BY created_at DESC",
                (agent_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]


class SQLiteChannelSessionsTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for channel sessions with lookup support."""

    async def find_by_external(
        self, channel_id: str, external_chat_id: str, external_thread_id: Optional[str] = None
    ) -> Optional[T]:
        """Find a channel session by external identifiers."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            if external_thread_id:
                query = (
                    f"SELECT * FROM {self.table_name} "
                    "WHERE channel_id = ? AND external_chat_id = ? AND external_thread_id = ?"
                )
                params = (channel_id, external_chat_id, external_thread_id)
            else:
                query = (
                    f"SELECT * FROM {self.table_name} "
                    "WHERE channel_id = ? AND external_chat_id = ? AND external_thread_id IS NULL"
                )
                params = (channel_id, external_chat_id)
            async with conn.execute(query, params) as cursor:
                row = await cursor.fetchone()
                return self._row_to_dict(row) if row else None

    async def list_by_channel(self, channel_id: str) -> list[T]:
        """List all sessions for a channel."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE channel_id = ? ORDER BY last_message_at DESC",
                (channel_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def count_by_channel(self, channel_id: str) -> int:
        """Count sessions for a channel."""
        async with self._get_connection() as conn:
            async with conn.execute(
                f"SELECT COUNT(*) FROM {self.table_name} WHERE channel_id = ?",
                (channel_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def delete_by_channel(self, channel_id: str) -> int:
        """Delete all sessions for a channel."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE channel_id = ?",
                (channel_id,)
            )
            await conn.commit()
            return cursor.rowcount

    async def find_stale(self, ttl_seconds: int) -> list[T]:
        """Find all channel sessions idle longer than *ttl_seconds*.

        Used by LifecycleManager to clean up stale channel sessions in
        the background — prevents unbounded accumulation when a user
        never messages a conversation again.
        """
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            # datetime('now', 'localtime') matches the datetime.now().isoformat()
            # writes in gateway._resolve_session (both use local time).
            query = (
                f"SELECT * FROM {self.table_name} "
                "WHERE last_message_at IS NOT NULL "
                "AND datetime(last_message_at) < datetime('now', 'localtime', ?)"
            )
            modifier = f"-{ttl_seconds} seconds"
            async with conn.execute(query, (modifier,)) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def find_by_user_key(
        self, user_key: str, exclude_threaded: bool = False,
    ) -> Optional[T]:
        """Find a channel session by user_key (cross-channel sharing).

        When *exclude_threaded* is True, only returns sessions without
        a thread ID (top-level conversations).
        """
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            if exclude_threaded:
                query = (
                    f"SELECT * FROM {self.table_name} "
                    "WHERE user_key = ? AND external_thread_id IS NULL "
                    "ORDER BY last_message_at DESC LIMIT 1"
                )
            else:
                query = (
                    f"SELECT * FROM {self.table_name} "
                    "WHERE user_key = ? ORDER BY last_message_at DESC LIMIT 1"
                )
            async with conn.execute(query, (user_key,)) as cursor:
                row = await cursor.fetchone()
                return self._row_to_dict(row) if row else None


class SQLiteChannelUserIdentitiesTable(SQLiteTable[T], Generic[T]):
    """Maps platform-specific sender IDs to unified user keys."""

    async def resolve_user_key(
        self, platform: str, external_sender_id: str,
    ) -> Optional[str]:
        """Resolve an external sender ID to a user_key.

        Returns the user_key string or None if no mapping exists.
        """
        async with self._get_connection() as conn:
            async with conn.execute(
                f"SELECT user_key FROM {self.table_name} "
                "WHERE platform = ? AND external_sender_id = ?",
                (platform, external_sender_id),
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None


class SQLiteChannelMessagesTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for channel messages."""

    async def list_by_session(self, channel_session_id: str) -> list[T]:
        """List all messages for a channel session."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE channel_session_id = ? ORDER BY created_at ASC",
                (channel_session_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]


class SQLiteWorkspaceConfigTable(SQLiteTable[T], Generic[T]):
    """Single-row workspace configuration table for the SwarmWS singleton.

    The workspace_config table always has exactly one row with id='swarmws'.
    This replaces the multi-workspace table for the new
    single-workspace model.
    """

    async def get_config(self) -> Optional[T]:
        """Get the singleton workspace config row."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE id = 'swarmws'"
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_dict(row) if row else None

    async def update_config(self, updates: dict) -> Optional[T]:
        """Update the singleton workspace config."""
        return await self.update('swarmws', updates)


class WorkspaceScopedTable(SQLiteTable[T], Generic[T]):
    """Base class for tables with workspace_id filtering.
    
    Subclasses should set:
    - filter_field: The column name to filter by (e.g., 'status', 'focus_type', 'artifact_type')
    - order_by: The ORDER BY clause (default: 'created_at DESC')
    
    This eliminates code duplication across workspace-scoped tables.
    """
    
    filter_field: ClassVar[Optional[str]] = None
    order_by: ClassVar[str] = "created_at DESC"
    
    async def list_by_workspace(
        self, 
        workspace_id: str, 
        filter_value: Optional[str] = None
    ) -> list[T]:
        """List all items for a workspace, optionally filtered by filter_field."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            if filter_value and self.filter_field:
                query = f"SELECT * FROM {self.table_name} WHERE workspace_id = ? AND {self.filter_field} = ? ORDER BY {self.order_by}"
                params = (workspace_id, filter_value)
            else:
                query = f"SELECT * FROM {self.table_name} WHERE workspace_id = ? ORDER BY {self.order_by}"
                params = (workspace_id,)
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def count_by_workspace_and_filter(self, workspace_id: str, filter_value: str) -> int:
        """Count items by workspace and filter_field value."""
        if not self.filter_field:
            raise ValueError(f"filter_field not set for {self.__class__.__name__}")
        async with self._get_connection() as conn:
            async with conn.execute(
                f"SELECT COUNT(*) FROM {self.table_name} WHERE workspace_id = ? AND {self.filter_field} = ?",
                (workspace_id, filter_value)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def delete_by_workspace(self, workspace_id: str) -> int:
        """Delete all items for a workspace. Returns count of deleted items."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE workspace_id = ?",
                (workspace_id,)
            )
            await conn.commit()
            return cursor.rowcount


class SQLiteToDosTable(WorkspaceScopedTable[T], Generic[T]):
    """Specialized SQLite table for ToDos with workspace and status filtering."""
    
    filter_field: ClassVar[str] = "status"
    order_by: ClassVar[str] = "created_at DESC"

    async def list_by_status(self, status: str) -> list[T]:
        """List all ToDos with a specific status (across all workspaces)."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE status = ? ORDER BY created_at DESC",
                (status,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    # Backward-compatible alias
    async def count_by_workspace_and_status(self, workspace_id: str, status: str) -> int:
        """Count ToDos by workspace and status."""
        return await self.count_by_workspace_and_filter(workspace_id, status)


class SQLiteWorkspaceMcpsTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for Workspace MCP server configuration."""

    async def list_by_workspace(self, workspace_id: str) -> list[T]:
        """List all MCP configurations for a workspace."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE workspace_id = ? ORDER BY created_at ASC",
                (workspace_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def get_by_workspace_and_mcp(self, workspace_id: str, mcp_server_id: str) -> Optional[T]:
        """Get a specific MCP configuration for a workspace."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE workspace_id = ? AND mcp_server_id = ?",
                (workspace_id, mcp_server_id)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_dict(row) if row else None

    async def delete_by_workspace(self, workspace_id: str) -> int:
        """Delete all MCP configurations for a workspace."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE workspace_id = ?",
                (workspace_id,)
            )
            await conn.commit()
            return cursor.rowcount


class SQLiteWorkspaceKnowledgebasesTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for Workspace Knowledgebases."""

    async def list_by_workspace(self, workspace_id: str) -> list[T]:
        """List all knowledgebase sources for a workspace."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE workspace_id = ? ORDER BY created_at ASC",
                (workspace_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]


class SQLiteWorkspaceAuditLogTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for Workspace Audit Log.

    Overrides put() because audit_log uses changed_at instead of
    created_at/updated_at.
    """

    async def put(self, item: T) -> T:
        """Insert an audit log entry (audit logs are append-only)."""
        if "id" not in item:
            item["id"] = str(uuid4())

        async with self._get_connection() as conn:
            columns = list(item.keys())
            placeholders = ", ".join("?" for _ in columns)
            values = [self._serialize_value(item[col]) for col in columns]

            await conn.execute(
                f"INSERT INTO {self.table_name} ({', '.join(columns)}) VALUES ({placeholders})",
                values
            )
            await conn.commit()
        return item

    async def list_by_workspace(self, workspace_id: str, limit: int = 100) -> list[T]:
        """List audit log entries for a workspace, ordered by most recent first."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE workspace_id = ? ORDER BY changed_at DESC LIMIT ?",
                (workspace_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def list_by_workspace_paginated(self, workspace_id: str, limit: int = 50, offset: int = 0) -> list[T]:
        """List audit log entries for a workspace with offset pagination."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE workspace_id = ? ORDER BY changed_at DESC LIMIT ? OFFSET ?",
                (workspace_id, limit, offset)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def count_by_workspace(self, workspace_id: str) -> int:
        """Count audit log entries for a workspace."""
        async with self._get_connection() as conn:
            async with conn.execute(
                f"SELECT COUNT(*) FROM {self.table_name} WHERE workspace_id = ?",
                (workspace_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def list_by_entity(self, entity_type: str, entity_id: str) -> list[T]:
        """List audit log entries for a specific entity."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE entity_type = ? AND entity_id = ? ORDER BY changed_at DESC",
                (entity_type, entity_id)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]


class SQLiteChatThreadsTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for Chat Threads with workspace and project filtering.

    Supports project-scoped thread queries, global (unassociated) thread
    listing, mid-session thread binding, and context version tracking.

    Validates: Requirements 26.1, 26.4, 26.5, 26.6, 35.1, 35.2, 35.3, 35.4
    """

    async def list_by_workspace(self, workspace_id: str) -> list[T]:
        """List all chat threads for a workspace."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE workspace_id = ? ORDER BY updated_at DESC",
                (workspace_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def list_by_project(self, project_id: str) -> list[T]:
        """List all chat threads associated with a specific project.

        Validates: Requirement 26.1
        """
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE project_id = ? ORDER BY updated_at DESC",
                (project_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def list_global(self) -> list[T]:
        """List all chat threads not associated with any project (project_id IS NULL).

        Validates: Requirement 26.4
        """
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE project_id IS NULL ORDER BY updated_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def bind_thread(
        self, thread_id: str, task_id: Optional[str], todo_id: Optional[str], mode: str
    ) -> Optional[T]:
        """Bind or rebind a thread to a task/todo mid-session.

        Args:
            thread_id: The thread to bind.
            task_id: Task ID to bind (or None to leave unchanged in 'add' mode).
            todo_id: ToDo ID to bind (or None to leave unchanged in 'add' mode).
            mode: 'replace' overwrites existing task_id/todo_id.
                  'add' only sets fields that are currently NULL.

        Returns:
            The updated thread dict, or None if thread not found.

        Validates: Requirements 35.1, 35.2, 35.3, 35.4
        """
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row

            # Fetch current thread state
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE id = ?", (thread_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                current = dict(row)

            if mode == "replace":
                # Overwrite existing task_id/todo_id with new values
                new_task_id = task_id
                new_todo_id = todo_id
            else:
                # 'add' mode: only set fields that are currently NULL
                new_task_id = task_id if current["task_id"] is None else current["task_id"]
                new_todo_id = todo_id if current["todo_id"] is None else current["todo_id"]

            now = datetime.now().isoformat()
            await conn.execute(
                f"UPDATE {self.table_name} "
                f"SET task_id = ?, todo_id = ?, context_version = context_version + 1, updated_at = ? "
                f"WHERE id = ?",
                (new_task_id, new_todo_id, now, thread_id),
            )
            await conn.commit()

        # Return the updated thread
        return await self.get(thread_id)

    async def increment_context_version(self, thread_id: str) -> int:
        """Increment and return the new context_version for a thread.

        Uses atomic SQL ``context_version + 1`` to avoid race conditions.

        Returns:
            The new context_version value, or -1 if thread not found.

        Validates: Requirement 26.6
        """
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            now = datetime.now().isoformat()
            cursor = await conn.execute(
                f"UPDATE {self.table_name} "
                f"SET context_version = context_version + 1, updated_at = ? "
                f"WHERE id = ?",
                (now, thread_id),
            )
            await conn.commit()

            if cursor.rowcount == 0:
                return -1

            # Read back the new value
            async with conn.execute(
                f"SELECT context_version FROM {self.table_name} WHERE id = ?",
                (thread_id,),
            ) as cur:
                row = await cur.fetchone()
                return row[0] if row else -1

    async def list_by_task(self, task_id: str) -> list[T]:
        """List all chat threads for a task."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE task_id = ? ORDER BY updated_at DESC",
                (task_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def list_by_todo(self, todo_id: str) -> list[T]:
        """List all chat threads for a todo."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE todo_id = ? ORDER BY updated_at DESC",
                (todo_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]


class SQLiteThreadSummariesTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for Thread Summaries.
    
    Thread summaries only have updated_at (no created_at) since they are
    rolling summaries that get updated over time.
    """

    async def put(self, item: T) -> T:
        """Insert or update a thread summary."""
        if "id" not in item:
            item["id"] = str(uuid4())
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

    async def get_by_thread(self, thread_id: str) -> Optional[T]:
        """Get the summary for a thread."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE thread_id = ? ORDER BY updated_at DESC LIMIT 1",
                (thread_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return self._row_to_dict(row) if row else None

    async def delete_by_thread(self, thread_id: str) -> int:
        """Delete all summaries for a thread."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE thread_id = ?",
                (thread_id,)
            )
            await conn.commit()
            return cursor.rowcount


class SQLiteChatMessagesTable(SQLiteTable[T], Generic[T]):
    """Specialized SQLite table for Chat Messages.
    
    Chat messages are immutable once created, so they don't have an updated_at column.
    """

    async def put(self, item: T) -> T:
        """Insert a chat message (messages are immutable, no updates)."""
        if "id" not in item:
            item["id"] = str(uuid4())
        if "created_at" not in item:
            item["created_at"] = datetime.now().isoformat()

        async with self._get_connection() as conn:
            columns = list(item.keys())
            placeholders = ", ".join("?" for _ in columns)
            values = [self._serialize_value(item[col]) for col in columns]

            await conn.execute(
                f"INSERT OR REPLACE INTO {self.table_name} ({', '.join(columns)}) VALUES ({placeholders})",
                values
            )
            await conn.commit()
        return item

    async def list_by_thread(self, thread_id: str) -> list[T]:
        """List all messages for a thread, ordered by creation time."""
        async with self._get_connection() as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM {self.table_name} WHERE thread_id = ? ORDER BY created_at ASC",
                (thread_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_dict(row) for row in rows]

    async def delete_by_thread(self, thread_id: str) -> int:
        """Delete all messages for a thread."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                f"DELETE FROM {self.table_name} WHERE thread_id = ?",
                (thread_id,)
            )
            await conn.commit()
            return cursor.rowcount


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
        is_system_agent INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        user_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_agents_user_id ON agents(user_id);

    -- MCP servers table
    -- Validates: Requirements 19.4
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
        source_type TEXT DEFAULT 'user',
        is_active INTEGER DEFAULT 1,
        is_system INTEGER DEFAULT 0,
        is_privileged INTEGER DEFAULT 0,
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
        workspace_id TEXT,
        last_accessed TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_agent_id ON sessions(agent_id);

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
    CREATE INDEX IF NOT EXISTS idx_messages_session_created ON messages(session_id, created_at);

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
    -- Non-credential config moved to SwarmWS/config.json (AppConfigManager)
    -- Credentials delegated to AWS credential chain (never stored in DB)
    CREATE TABLE IF NOT EXISTS app_settings (
        id TEXT PRIMARY KEY DEFAULT 'default',
        initialization_complete INTEGER DEFAULT 0,
        onboarding_complete INTEGER DEFAULT 0,
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
    -- Validates: Requirements 5.1, 13.2, 13.3, 13.4
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        session_id TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        title TEXT NOT NULL,
        description TEXT,
        priority TEXT DEFAULT 'none' CHECK (priority IN ('high', 'medium', 'low', 'none')),
        model TEXT,
        workspace_id TEXT,
        source_todo_id TEXT,
        blocked_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        error TEXT,
        work_dir TEXT,
        FOREIGN KEY (workspace_id) REFERENCES swarm_workspaces(id) ON DELETE SET NULL,
        FOREIGN KEY (source_todo_id) REFERENCES todos(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_agent_id ON tasks(agent_id);
    CREATE INDEX IF NOT EXISTS idx_tasks_workspace_id ON tasks(workspace_id);

    -- Channels table
    CREATE TABLE IF NOT EXISTS channels (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        channel_type TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        config TEXT NOT NULL DEFAULT '{}',
        status TEXT DEFAULT 'inactive',
        error_message TEXT,
        access_mode TEXT DEFAULT 'allowlist',
        allowed_senders TEXT DEFAULT '[]',
        blocked_senders TEXT DEFAULT '[]',
        api_keys TEXT DEFAULT '[]',
        rate_limit_per_minute INTEGER DEFAULT 10,
        enable_skills INTEGER DEFAULT 0,
        enable_mcp INTEGER DEFAULT 0,
        user_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_channels_agent_id ON channels(agent_id);
    CREATE INDEX IF NOT EXISTS idx_channels_status ON channels(status);

    -- Channel sessions table (maps external conversations to internal sessions)
    CREATE TABLE IF NOT EXISTS channel_sessions (
        id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        external_chat_id TEXT NOT NULL,
        external_sender_id TEXT,
        external_thread_id TEXT,
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        sender_display_name TEXT,
        user_key TEXT,
        last_message_at TEXT,
        message_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
        UNIQUE(channel_id, external_chat_id, external_thread_id)
    );
    CREATE INDEX IF NOT EXISTS idx_channel_sessions_lookup
        ON channel_sessions(channel_id, external_chat_id, external_thread_id);
    CREATE INDEX IF NOT EXISTS idx_channel_sessions_user_key
        ON channel_sessions(user_key);

    -- Channel messages table (audit log)
    CREATE TABLE IF NOT EXISTS channel_messages (
        id TEXT PRIMARY KEY,
        channel_session_id TEXT NOT NULL,
        direction TEXT NOT NULL,
        external_message_id TEXT,
        content TEXT NOT NULL,
        content_type TEXT DEFAULT 'text',
        metadata TEXT DEFAULT '{}',
        status TEXT DEFAULT 'sent',
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (channel_session_id) REFERENCES channel_sessions(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_channel_messages_session ON channel_messages(channel_session_id);

    -- Channel user identity mapping (cross-channel session sharing)
    CREATE TABLE IF NOT EXISTS channel_user_identities (
        user_key TEXT NOT NULL,
        platform TEXT NOT NULL,
        external_sender_id TEXT NOT NULL,
        display_name TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (platform, external_sender_id)
    );
    CREATE INDEX IF NOT EXISTS idx_channel_uid_user ON channel_user_identities(user_key);

    -- Workspace Config table (singleton SwarmWS configuration)
    -- Validates: SwarmWS Foundation Requirements 19.1, 19.2, 19.5
    CREATE TABLE IF NOT EXISTS workspace_config (
        id TEXT PRIMARY KEY DEFAULT 'swarmws',
        name TEXT NOT NULL DEFAULT 'SwarmWS',
        file_path TEXT NOT NULL,
        icon TEXT DEFAULT '🏠',
        context TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    -- ToDos table (Signals in Daily Work Operating Loop)
    CREATE TABLE IF NOT EXISTS todos (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        source TEXT,
        source_type TEXT NOT NULL DEFAULT 'manual' CHECK (source_type IN ('manual', 'email', 'slack', 'meeting', 'integration', 'chat', 'ai_detected')),
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'overdue', 'in_discussion', 'handled', 'cancelled', 'deleted')),
        priority TEXT NOT NULL DEFAULT 'none' CHECK (priority IN ('high', 'medium', 'low', 'none')),
        due_date TEXT,
        linked_context TEXT,
        task_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (workspace_id) REFERENCES swarm_workspaces(id) ON DELETE CASCADE,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_todos_workspace_id ON todos(workspace_id);
    CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
    CREATE INDEX IF NOT EXISTS idx_todos_due_date ON todos(due_date);
    CREATE INDEX IF NOT EXISTS idx_todos_workspace_status ON todos(workspace_id, status);

    -- Workspace MCPs junction table (MCP server configuration per workspace)
    -- Validates: Requirements 19.3
    CREATE TABLE IF NOT EXISTS workspace_mcps (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        mcp_server_id TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (workspace_id) REFERENCES swarm_workspaces(id) ON DELETE CASCADE,
        UNIQUE(workspace_id, mcp_server_id)
    );
    CREATE INDEX IF NOT EXISTS idx_workspace_mcps_workspace_id ON workspace_mcps(workspace_id);

    -- Workspace Knowledgebases table (Knowledge sources per workspace)
    -- Validates: Requirements 19.5
    CREATE TABLE IF NOT EXISTS workspace_knowledgebases (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        source_type TEXT NOT NULL CHECK (source_type IN ('local_file', 'url', 'indexed_document', 'context_file', 'vector_index')),
        source_path TEXT NOT NULL,
        display_name TEXT NOT NULL,
        metadata TEXT DEFAULT '{}',
        excluded_sources TEXT DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (workspace_id) REFERENCES swarm_workspaces(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_workspace_kbs_workspace_id ON workspace_knowledgebases(workspace_id);

    -- Workspace Audit Log table (Configuration change tracking)
    -- Validates: Requirements 25.2
    CREATE TABLE IF NOT EXISTS workspace_audit_log (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        change_type TEXT NOT NULL CHECK (change_type IN ('enabled', 'disabled', 'added', 'removed', 'updated')),
        entity_type TEXT NOT NULL CHECK (entity_type IN ('skill', 'mcp', 'knowledgebase', 'workspace_setting')),
        entity_id TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        changed_by TEXT NOT NULL,
        changed_at TEXT NOT NULL,
        FOREIGN KEY (workspace_id) REFERENCES swarm_workspaces(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_audit_log_workspace_id ON workspace_audit_log(workspace_id);
    CREATE INDEX IF NOT EXISTS idx_audit_log_changed_at ON workspace_audit_log(changed_at);

    -- Chat Threads table (Workspace-bound conversations)
    -- Validates: Requirements 30.1, 30.3, 30.9, 26.5, 26.6
    CREATE TABLE IF NOT EXISTS chat_threads (
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        task_id TEXT,
        todo_id TEXT,
        mode TEXT NOT NULL DEFAULT 'explore' CHECK (mode IN ('explore', 'execute')),
        title TEXT NOT NULL,
        project_id TEXT DEFAULT NULL,
        context_version INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (workspace_id) REFERENCES swarm_workspaces(id) ON DELETE CASCADE,
        FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL,
        FOREIGN KEY (todo_id) REFERENCES todos(id) ON DELETE SET NULL
    );
    CREATE INDEX IF NOT EXISTS idx_chat_threads_workspace_id ON chat_threads(workspace_id);
    CREATE INDEX IF NOT EXISTS idx_chat_threads_agent_id ON chat_threads(agent_id);
    CREATE INDEX IF NOT EXISTS idx_chat_threads_task_id ON chat_threads(task_id);
    CREATE INDEX IF NOT EXISTS idx_chat_threads_project_id ON chat_threads(project_id);

    -- Thread Summaries table (Rolling summaries for chat threads)
    -- Validates: Requirements 30.9
    CREATE TABLE IF NOT EXISTS thread_summaries (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        summary_type TEXT NOT NULL DEFAULT 'rolling' CHECK (summary_type IN ('rolling', 'final')),
        summary_text TEXT NOT NULL,
        key_decisions TEXT DEFAULT '[]',
        open_questions TEXT DEFAULT '[]',
        updated_at TEXT NOT NULL,
        FOREIGN KEY (thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_thread_summaries_thread_id ON thread_summaries(thread_id);
    CREATE INDEX IF NOT EXISTS idx_thread_summaries_text ON thread_summaries(summary_text);

    -- Chat Messages table (Messages within chat threads)
    -- Validates: Requirements 30.3
    CREATE TABLE IF NOT EXISTS chat_messages (
        id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool', 'system')),
        content TEXT NOT NULL,
        tool_calls TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_id ON chat_messages(thread_id);
    """

    def __init__(self, db_path: str | Path | None = None):
        """Initialize SQLite database.

        Args:
            db_path: Path to the SQLite database file. If None, uses default location.
        """
        if db_path is None:
            # Default to user data directory
            data_dir = get_app_data_dir()
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "data.db"
        else:
            db_path = Path(db_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self._initialized = False

        # Initialize tables
        self._agents = SQLiteTable[dict]("agents", self.db_path)
        self._mcp_servers = SQLiteMCPServersTable[dict]("mcp_servers", self.db_path)
        self._sessions = SQLiteTable[dict]("sessions", self.db_path)
        self._messages = SQLiteMessagesTable[dict]("messages", self.db_path)
        self._users = SQLiteTable[dict]("users", self.db_path)
        self._app_settings = SQLiteTable[dict]("app_settings", self.db_path)
        self._marketplaces = SQLiteTable[dict]("marketplaces", self.db_path)
        self._plugins = SQLitePluginsTable[dict]("plugins", self.db_path)
        self._tasks = SQLiteTasksTable[dict]("tasks", self.db_path)
        self._channels = SQLiteTable[dict]("channels", self.db_path)
        self._channel_sessions = SQLiteChannelSessionsTable[dict]("channel_sessions", self.db_path)
        self._channel_messages = SQLiteChannelMessagesTable[dict]("channel_messages", self.db_path)
        self._channel_user_identities = SQLiteChannelUserIdentitiesTable[dict]("channel_user_identities", self.db_path)
        self._workspace_config = SQLiteWorkspaceConfigTable[dict]("workspace_config", self.db_path)
        # Daily Work Operating Loop tables
        self._todos = SQLiteToDosTable[dict]("todos", self.db_path)
        # Workspace configuration tables
        self._workspace_mcps = SQLiteWorkspaceMcpsTable[dict]("workspace_mcps", self.db_path)
        self._workspace_knowledgebases = SQLiteWorkspaceKnowledgebasesTable[dict]("workspace_knowledgebases", self.db_path)
        self._workspace_audit_log = SQLiteWorkspaceAuditLogTable[dict]("workspace_audit_log", self.db_path)
        # Chat thread tables
        self._chat_threads = SQLiteChatThreadsTable[dict]("chat_threads", self.db_path)
        self._thread_summaries = SQLiteThreadSummariesTable[dict]("thread_summaries", self.db_path)
        self._chat_messages = SQLiteChatMessagesTable[dict]("chat_messages", self.db_path)

    async def initialize(self, skip_init: bool = False) -> None:
        """Initialize database schema.

        Args:
            skip_init: When True, mark the database as initialized without
                running schema DDL or migrations.  Used for seed-sourced
                databases that already contain the complete schema and data.
        """
        if self._initialized:
            return

        if skip_init:
            logger.info("Skipping schema DDL (seed-sourced) — running migrations only")
            # Even seed-sourced / returning-user databases need migrations:
            # new tables (skill_metrics, messages_fts) added after the seed was
            # built must be created.  Migrations are all idempotent (IF NOT EXISTS).
            async with aiosqlite.connect(str(self.db_path)) as conn:
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.execute("PRAGMA busy_timeout=100")
                _WALConnection._wal_initialized.add(str(self.db_path))
                await self._run_migrations(conn)
            self._initialized = True
            return

        import time
        t0 = time.monotonic()
        logger.info("DB init: opening connection to %s", self.db_path)

        async with aiosqlite.connect(str(self.db_path)) as conn:
            t1 = time.monotonic()
            logger.info("DB init: connection opened in %.2fs, executing schema...", t1 - t0)

            # Enable WAL mode for concurrent read/write from parallel chat sessions.
            # WAL persists in the DB file, so this is idempotent across restarts.
            # busy_timeout: short (100ms) — app-level retry handles longer waits.
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=100")
            _WALConnection._wal_initialized.add(str(self.db_path))
            logger.info("DB init: WAL mode enabled")

            await conn.executescript(self.SCHEMA)
            await conn.commit()
            t2 = time.monotonic()
            logger.info("DB init: schema executed in %.2fs, running migrations...", t2 - t1)

            # Run migrations for existing databases
            await self._run_migrations(conn)
            t3 = time.monotonic()
            logger.info("DB init: migrations completed in %.2fs (total: %.2fs)", t3 - t2, t3 - t0)

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

        # Migration: Add workspace_id column to sessions table (added 2026-02-15)
        # Stores the Swarm Workspace ID for session workspace tracking (Requirement 5.7)
        if "workspace_id" not in session_column_names:
            logger.info("Running migration: Adding workspace_id column to sessions table")
            await conn.execute("ALTER TABLE sessions ADD COLUMN workspace_id TEXT")
            await conn.commit()
            logger.info("Migration complete: workspace_id column added")

        # Migration: Add updated_at column to tasks table (added 2026-02-03)
        # Required by base SQLiteTable.put() method
        cursor = await conn.execute("PRAGMA table_info(tasks)")
        tasks_columns = await cursor.fetchall()
        tasks_column_names = [col[1] for col in tasks_columns]

        if "updated_at" not in tasks_column_names:
            logger.info("Running migration: Adding updated_at column to tasks table")
            # Set default to current timestamp for existing rows (datetime already imported at module level)
            await conn.execute(f"ALTER TABLE tasks ADD COLUMN updated_at TEXT DEFAULT '{datetime.now().isoformat()}'")
            await conn.commit()
            logger.info("Migration complete: updated_at column added")

        # Migration: Add sandbox_enabled column to agents table (added 2026-02-10)
        # Per-agent sandbox toggle, defaults to enabled
        if "sandbox_enabled" not in column_names:
            logger.info("Running migration: Adding sandbox_enabled column to agents table")
            await conn.execute("ALTER TABLE agents ADD COLUMN sandbox_enabled INTEGER DEFAULT 1")
            await conn.commit()
            logger.info("Migration complete: sandbox_enabled column added")

        # Migration: Add is_default column to agents table (added 2026-02-13)
        # Marks the protected default agent that cannot be deleted
        if "is_default" not in column_names:
            logger.info("Running migration: Adding is_default column to agents table")
            await conn.execute("ALTER TABLE agents ADD COLUMN is_default INTEGER DEFAULT 0")
            await conn.commit()
            logger.info("Migration complete: is_default column added")

        # Migration: Add is_system_agent column to agents table (added 2026-02-15)
        # Marks the protected system agent (SwarmAgent) that cannot be deleted or renamed
        if "is_system_agent" not in column_names:
            logger.info("Running migration: Adding is_system_agent column to agents table")
            await conn.execute("ALTER TABLE agents ADD COLUMN is_system_agent INTEGER DEFAULT 0")
            await conn.commit()
            logger.info("Migration complete: is_system_agent column added")

        # Migration: Add is_system column to mcp_servers table (added 2026-02-15)
        # Marks system MCP servers that are automatically bound to SwarmAgent
        cursor = await conn.execute("PRAGMA table_info(mcp_servers)")
        mcp_columns = await cursor.fetchall()
        mcp_column_names = [col[1] for col in mcp_columns]

        if "is_system" not in mcp_column_names:
            logger.info("Running migration: Adding is_system column to mcp_servers table")
            await conn.execute("ALTER TABLE mcp_servers ADD COLUMN is_system INTEGER DEFAULT 0")
            await conn.commit()
            logger.info("Migration complete: is_system column added to mcp_servers")

        # Migration: Add excluded_sources column to workspace_knowledgebases table (added 2026-02-25)
        # Stores JSON array of KnowledgebaseSource IDs to exclude from inheritance
        # Validates: Requirements 19.5
        cursor = await conn.execute("PRAGMA table_info(workspace_knowledgebases)")
        kb_columns = await cursor.fetchall()
        kb_column_names = [col[1] for col in kb_columns]

        if "excluded_sources" not in kb_column_names:
            logger.info("Running migration: Adding excluded_sources column to workspace_knowledgebases table")
            await conn.execute("ALTER TABLE workspace_knowledgebases ADD COLUMN excluded_sources TEXT DEFAULT '[]'")
            await conn.commit()
            logger.info("Migration complete: excluded_sources column added")

        # ============================================================================
        # Chat Thread Project Association Migrations (Cadence 4 — SwarmWS Intelligence)
        # Validates: Requirements 26.5, 26.6, 37.1, 37.2, 37.3
        # Safe schema evolution: ADD COLUMN IF NOT EXISTS for existing DBs.
        # On clean installs these columns already exist in the CREATE TABLE.
        # ============================================================================
        cursor = await conn.execute("PRAGMA table_info(chat_threads)")
        chat_thread_columns = await cursor.fetchall()
        chat_thread_column_names = [col[1] for col in chat_thread_columns]

        if "project_id" not in chat_thread_column_names:
            logger.info("Running migration: Adding project_id column to chat_threads table")
            await conn.execute("ALTER TABLE chat_threads ADD COLUMN project_id TEXT DEFAULT NULL")
            await conn.commit()
            logger.info("Migration complete: project_id column added to chat_threads")

        if "context_version" not in chat_thread_column_names:
            logger.info("Running migration: Adding context_version column to chat_threads table")
            await conn.execute("ALTER TABLE chat_threads ADD COLUMN context_version INTEGER DEFAULT 0")
            await conn.commit()
            logger.info("Migration complete: context_version column added to chat_threads")

        # Ensure index exists (safe: CREATE INDEX IF NOT EXISTS)
        try:
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_threads_project_id ON chat_threads(project_id)")
            await conn.commit()
        except Exception:
            pass  # Index may already exist

        # ============================================================================
        # Workspace Refactor Migrations (Task 1.8)
        # Validates: Requirements 5.1, 13.2, 13.3, 13.4, 19.2, 19.4
        # ============================================================================

        # Migration: Add workspace_id, source_todo_id, blocked_reason, priority, description columns to tasks table
        # Validates: Requirements 5.1, 13.2, 13.3, 13.4
        # Re-fetch tasks columns in case they were updated earlier in this migration run
        cursor = await conn.execute("PRAGMA table_info(tasks)")
        tasks_columns = await cursor.fetchall()
        tasks_column_names = [col[1] for col in tasks_columns]

        if "workspace_id" not in tasks_column_names:
            logger.info("Running migration: Adding workspace_id column to tasks table")
            await conn.execute("ALTER TABLE tasks ADD COLUMN workspace_id TEXT")
            await conn.commit()
            logger.info("Migration complete: workspace_id column added to tasks")

        if "source_todo_id" not in tasks_column_names:
            logger.info("Running migration: Adding source_todo_id column to tasks table")
            await conn.execute("ALTER TABLE tasks ADD COLUMN source_todo_id TEXT")
            await conn.commit()
            logger.info("Migration complete: source_todo_id column added to tasks")

        if "blocked_reason" not in tasks_column_names:
            logger.info("Running migration: Adding blocked_reason column to tasks table")
            await conn.execute("ALTER TABLE tasks ADD COLUMN blocked_reason TEXT")
            await conn.commit()
            logger.info("Migration complete: blocked_reason column added to tasks")

        if "priority" not in tasks_column_names:
            logger.info("Running migration: Adding priority column to tasks table")
            await conn.execute("ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'none'")
            await conn.commit()
            logger.info("Migration complete: priority column added to tasks")

        if "description" not in tasks_column_names:
            logger.info("Running migration: Adding description column to tasks table")
            await conn.execute("ALTER TABLE tasks ADD COLUMN description TEXT")
            await conn.commit()
            logger.info("Migration complete: description column added to tasks")

        # Create index on tasks.workspace_id if it doesn't exist
        try:
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_workspace_id ON tasks(workspace_id)")
            await conn.commit()
        except Exception:
            pass  # Index may already exist

        # Migration: Add is_privileged column to skills table (if table exists)
        # Validates: Requirements 19.2
        # Note: Skills table may not exist if using filesystem-only skills
        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='skills'")
        skills_table_exists = await cursor.fetchone() is not None
        
        if skills_table_exists:
            cursor = await conn.execute("PRAGMA table_info(skills)")
            skills_columns = await cursor.fetchall()
            skills_column_names = [col[1] for col in skills_columns]

            if "is_privileged" not in skills_column_names:
                logger.info("Running migration: Adding is_privileged column to skills table")
                await conn.execute("ALTER TABLE skills ADD COLUMN is_privileged INTEGER DEFAULT 0")
                await conn.commit()
                logger.info("Migration complete: is_privileged column added to skills")

        # Migration: Add is_privileged column to mcp_servers table
        # Validates: Requirements 19.4
        # Re-fetch mcp_servers columns
        cursor = await conn.execute("PRAGMA table_info(mcp_servers)")
        mcp_columns = await cursor.fetchall()
        mcp_column_names = [col[1] for col in mcp_columns]

        if "is_privileged" not in mcp_column_names:
            logger.info("Running migration: Adding is_privileged column to mcp_servers table")
            await conn.execute("ALTER TABLE mcp_servers ADD COLUMN is_privileged INTEGER DEFAULT 0")
            await conn.commit()
            logger.info("Migration complete: is_privileged column added to mcp_servers")

        # Migration: Add source_type column to mcp_servers table (added 2026-02-22)
        # Consistent with skills table source tracking
        # Re-fetch mcp_servers columns in case they were updated earlier
        cursor = await conn.execute("PRAGMA table_info(mcp_servers)")
        mcp_columns = await cursor.fetchall()
        mcp_column_names = [col[1] for col in mcp_columns]

        if "source_type" not in mcp_column_names:
            logger.info("Running migration: Adding source_type column to mcp_servers table")
            await conn.execute("ALTER TABLE mcp_servers ADD COLUMN source_type TEXT DEFAULT 'user'")
            await conn.commit()
            logger.info("Migration complete: source_type column added to mcp_servers")

        # ============================================================================
        # Workspace Refactor Data Migration (Task 1.9)
        # Validates: Requirements 5.4, 13.7, 13.8
        # ============================================================================

        # ============================================================================
        # Legacy Data Cleanup: Clean-slate approach (Task 18.1)
        # Detects the legacy swarm_workspaces table, reads workspace paths for
        # filesystem cleanup, drops the table, removes legacy workspace dirs,
        # and clears workspace_id in chat_threads so threads become global.
        # Validates: SwarmWS Foundation Requirements 24.1, 24.2, 24.3
        # ============================================================================

        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='swarm_workspaces'"
        )
        legacy_table_exists = await cursor.fetchone()

        if legacy_table_exists:
            logger.info("Legacy cleanup: Detected swarm_workspaces table, starting clean-slate removal")

            # Step 1: Read legacy workspace file_path values before dropping
            # so we can remove their filesystem directories.
            legacy_paths: list[str] = []
            try:
                cursor = await conn.execute("SELECT file_path FROM swarm_workspaces")
                rows = await cursor.fetchall()
                legacy_paths = [row[0] for row in rows if row[0]]
                logger.info("Legacy cleanup: Found %d workspace path(s) to evaluate for removal", len(legacy_paths))
            except Exception as e:
                logger.warning("Legacy cleanup: Could not read legacy workspace paths: %s", e)

            # Step 2: DROP the swarm_workspaces table entirely
            try:
                await conn.execute("DROP TABLE IF EXISTS swarm_workspaces")
                await conn.commit()
                logger.info("Legacy cleanup: Dropped swarm_workspaces table")
            except Exception as e:
                logger.warning("Legacy cleanup: Failed to drop swarm_workspaces table: %s", e)

            # Step 3: Remove legacy workspace directories from filesystem.
            # Only remove dirs that are NOT the current SwarmWS directory.
            # Paths stored in DB use {app_data_dir} placeholder or ~ prefix.
            app_data_dir = str(get_app_data_dir())
            swarmws_dir = str(Path(app_data_dir) / "SwarmWS")

            for raw_path in legacy_paths:
                # Expand {app_data_dir} placeholder and ~ home dir
                expanded = raw_path
                if "{app_data_dir}" in expanded:
                    expanded = expanded.replace("{app_data_dir}", app_data_dir)
                expanded = str(Path(expanded).expanduser())

                # Skip the current SwarmWS directory — it's the active workspace
                if Path(expanded).resolve() == Path(swarmws_dir).resolve():
                    logger.info("Legacy cleanup: Skipping active SwarmWS directory: %s", expanded)
                    continue

                # Safety: only remove directories under the app data directory
                # to prevent a tampered DB from deleting arbitrary paths
                try:
                    resolved = Path(expanded).resolve()
                    if not str(resolved).startswith(str(Path(app_data_dir).resolve())):
                        logger.warning(
                            "Legacy cleanup: Skipping directory outside app data dir: %s",
                            expanded,
                        )
                        continue
                except (OSError, ValueError):
                    logger.warning(
                        "Legacy cleanup: Could not resolve path, skipping: %s",
                        expanded,
                    )
                    continue

                if Path(expanded).exists() and Path(expanded).is_dir():
                    try:
                        shutil.rmtree(expanded)
                        logger.info("Legacy cleanup: Removed legacy workspace directory: %s", expanded)
                    except Exception as e:
                        logger.warning("Legacy cleanup: Failed to remove directory %s: %s", expanded, e)
                else:
                    logger.debug("Legacy cleanup: Legacy directory does not exist, skipping: %s", expanded)

            # Step 4: Clear workspace_id in chat_threads so threads become global SwarmWS chats
            try:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM chat_threads WHERE workspace_id IS NOT NULL"
                )
                row = await cursor.fetchone()
                thread_count = row[0] if row else 0

                if thread_count > 0:
                    await conn.execute("UPDATE chat_threads SET workspace_id = NULL")
                    await conn.commit()
                    logger.info("Legacy cleanup: Cleared workspace_id on %d chat thread(s)", thread_count)
                else:
                    logger.debug("Legacy cleanup: No chat threads with workspace_id to clear")
            except Exception as e:
                logger.warning("Legacy cleanup: Failed to clear chat_threads workspace_id: %s", e)

            logger.info("Legacy cleanup: Clean-slate removal complete")

        # ============================================================================
        # ToDo Schema Extensions (Swarm Radar ToDos — Sub-Spec 2)
        # Validates: Requirements 5.6
        # Step 1: Add linked_context column (safe ALTER TABLE)
        # Step 2: Update source_type CHECK constraint via table-rebuild
        # ============================================================================

        # Migration Step 1: Add linked_context column to todos table
        cursor = await conn.execute("PRAGMA table_info(todos)")
        todo_columns = await cursor.fetchall()
        todo_column_names = [col[1] for col in todo_columns]

        if "linked_context" not in todo_column_names:
            logger.info("Running migration: Adding linked_context column to todos table")
            await conn.execute("ALTER TABLE todos ADD COLUMN linked_context TEXT")
            await conn.commit()
            logger.info("Migration complete: linked_context column added to todos")

        # Migration Step 2: Update source_type CHECK constraint to include 'chat' and 'ai_detected'
        # SQLite cannot ALTER CHECK constraints, so we use the table-rebuild pattern.
        # Wrapped in BEGIN IMMEDIATE ... COMMIT for crash safety (PE Finding #7).
        # Idempotency: skip if 'chat' is already in the CREATE TABLE SQL.
        cursor = await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='todos'"
        )
        row = await cursor.fetchone()
        create_sql = row[0] if row else ""

        if "'chat'" not in create_sql:
            logger.info("Running migration: Updating source_type CHECK constraint in todos table")
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute("""
                    CREATE TABLE todos_new (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT,
                        source TEXT,
                        source_type TEXT NOT NULL DEFAULT 'manual'
                            CHECK (source_type IN ('manual','email','slack','meeting','integration','chat','ai_detected')),
                        status TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','overdue','in_discussion','handled','cancelled','deleted')),
                        priority TEXT NOT NULL DEFAULT 'none'
                            CHECK (priority IN ('high','medium','low','none')),
                        due_date TEXT,
                        linked_context TEXT,
                        task_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                await conn.execute("""
                    INSERT INTO todos_new
                    SELECT id, workspace_id, title, description, source, source_type,
                           status, priority, due_date, linked_context, task_id,
                           created_at, updated_at
                    FROM todos
                """)
                await conn.execute("DROP TABLE todos")
                await conn.execute("ALTER TABLE todos_new RENAME TO todos")
                # Recreate indexes lost during table rebuild
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_workspace_id ON todos(workspace_id)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_due_date ON todos(due_date)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_workspace_status ON todos(workspace_id, status)")
                await conn.execute("COMMIT")
                logger.info("Migration complete: source_type CHECK constraint updated in todos")
            except Exception as e:
                await conn.execute("ROLLBACK")
                logger.error("Migration failed: source_type CHECK update in todos: %s", e)
                raise

        # Migration: Map existing task statuses and assign workspace_id to existing tasks
        # Status mapping: pending→draft, running→wip, failed→blocked
        # Assign SwarmWS.id to tasks with NULL workspace_id
        await self._migrate_existing_task_data(conn)

        # Migration: Create skill_metrics table (added 2026-04-09)
        # Tracks skill invocation outcomes for evolution candidate detection.
        # Idempotent: IF NOT EXISTS.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_name TEXT NOT NULL,
                invocation_date TEXT NOT NULL,
                session_id TEXT,
                outcome TEXT NOT NULL CHECK(outcome IN ('success', 'partial', 'failure', 'abandoned')),
                duration_seconds REAL DEFAULT 0.0,
                user_satisfaction TEXT DEFAULT 'unknown' CHECK(user_satisfaction IN ('correction', 'accepted', 'unknown'))
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_skill_metrics_name ON skill_metrics(skill_name)
        """)
        await conn.commit()

        # ============================================================================
        # FTS5 Full-Text Search on messages (Session Recall — Phase 2)
        # Creates a content-synced FTS5 virtual table for fast full-text search
        # across session messages. The messages table uses TEXT PRIMARY KEY (id)
        # but SQLite maintains an implicit rowid which FTS5 uses.
        # ============================================================================
        try:
            await conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    content,
                    content=messages,
                    content_rowid=rowid
                )
            """)
            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS messages_fts_insert
                AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
                END
            """)
            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS messages_fts_delete
                AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES('delete', old.rowid, old.content);
                END
            """)
            await conn.execute("""
                CREATE TRIGGER IF NOT EXISTS messages_fts_update
                AFTER UPDATE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES('delete', old.rowid, old.content);
                    INSERT INTO messages_fts(rowid, content)
                    VALUES (new.rowid, new.content);
                END
            """)
            await conn.commit()
            # Rebuild index from existing data — only needed on first creation.
            # Check if the FTS5 table has any rows; if it does, the index is
            # already populated (triggers keep it in sync). Rebuilding a large
            # messages table takes 30-50s and was blocking every daemon restart.
            try:
                cursor = await conn.execute("SELECT COUNT(*) FROM messages_fts LIMIT 1")
                fts_count = (await cursor.fetchone())[0]
                if fts_count == 0:
                    msg_cursor = await conn.execute("SELECT COUNT(*) FROM messages")
                    msg_count = (await msg_cursor.fetchone())[0]
                    if msg_count > 0:
                        logger.info("FTS5 index empty but %d messages exist — rebuilding (one-time)...", msg_count)
                        await conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
                        await conn.commit()
                        logger.info("FTS5 rebuild complete")
                    else:
                        logger.info("FTS5 index and messages table both empty — no rebuild needed")
                else:
                    logger.debug("FTS5 index already populated (%d rows) — skipping rebuild", fts_count)
            except Exception as exc:
                logger.debug("FTS5 rebuild check skipped: %s", exc)
            logger.info("Migration complete: messages_fts FTS5 table and triggers created")
        except Exception as exc:
            logger.warning("FTS5 migration skipped (may already exist): %s", exc)

    async def _migrate_existing_task_data(self, conn: aiosqlite.Connection) -> None:
        """Migrate existing task data for workspace refactor.
        
        This migration:
        1. Maps legacy task statuses to new values: pending→draft, running→wip, failed→blocked
        2. Sets workspace_id to SwarmWS.id for existing tasks with NULL workspace_id
        
        Uses transactions for atomicity.
        Validates: Requirements 5.4, 13.7, 13.8
        """
        # Check if migration has already been run by looking for any tasks with new status values
        # If we find tasks with 'draft', 'wip', or 'blocked' status, migration was already done
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('draft', 'wip', 'blocked')"
        )
        row = await cursor.fetchone()
        new_status_count = row[0] if row else 0

        # Also check if there are any tasks with old status values that need migration
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('pending', 'running', 'failed')"
        )
        row = await cursor.fetchone()
        old_status_count = row[0] if row else 0

        # Only run status migration if there are old status values to migrate
        if old_status_count > 0:
            logger.info(f"Running migration: Mapping {old_status_count} tasks with legacy status values")
            
            try:
                # Map pending → draft
                cursor = await conn.execute(
                    "UPDATE tasks SET status = 'draft' WHERE status = 'pending'"
                )
                pending_count = cursor.rowcount
                
                # Map running → wip
                cursor = await conn.execute(
                    "UPDATE tasks SET status = 'wip' WHERE status = 'running'"
                )
                running_count = cursor.rowcount
                
                # Map failed → blocked (preserve failure context in blocked_reason if not already set)
                # First, copy error message to blocked_reason for failed tasks that don't have blocked_reason set
                await conn.execute(
                    """
                    UPDATE tasks 
                    SET blocked_reason = COALESCE(blocked_reason, error, 'Task failed (migrated from legacy status)')
                    WHERE status = 'failed' AND (blocked_reason IS NULL OR blocked_reason = '')
                    """
                )
                
                # Then update the status
                cursor = await conn.execute(
                    "UPDATE tasks SET status = 'blocked' WHERE status = 'failed'"
                )
                failed_count = cursor.rowcount
                
                await conn.commit()
                logger.info(
                    f"Migration complete: Status mapping done - "
                    f"pending→draft: {pending_count}, running→wip: {running_count}, failed→blocked: {failed_count}"
                )
            except Exception as e:
                logger.error(f"Migration failed during status mapping: {e}")
                raise
        else:
            logger.debug("Status migration skipped: No tasks with legacy status values found")

        # Migration: Set workspace_id to SwarmWS.id for existing tasks with NULL workspace_id
        # Always use 'swarmws' as the workspace ID (legacy swarm_workspaces table is dropped)
        swarm_ws_id = 'swarmws'
        
        if swarm_ws_id:
            
            # Check how many tasks need workspace_id assignment
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE workspace_id IS NULL"
            )
            row = await cursor.fetchone()
            null_workspace_count = row[0] if row else 0
            
            if null_workspace_count > 0:
                logger.info(f"Running migration: Assigning workspace_id to {null_workspace_count} tasks with NULL workspace_id")
                
                try:
                    cursor = await conn.execute(
                        "UPDATE tasks SET workspace_id = ? WHERE workspace_id IS NULL",
                        (swarm_ws_id,)
                    )
                    updated_count = cursor.rowcount
                    await conn.commit()
                    logger.info(f"Migration complete: Assigned workspace_id '{swarm_ws_id}' to {updated_count} tasks")
                except Exception as e:
                    logger.error(f"Migration failed during workspace_id assignment: {e}")
                    raise
            else:
                logger.debug("Workspace assignment migration skipped: No tasks with NULL workspace_id found")
        else:
            # SwarmWS doesn't exist yet - this is fine, it will be created during initialization
            # and new tasks will get the workspace_id assigned properly
            logger.debug("Workspace assignment migration skipped: SwarmWS not found (will be created during initialization)")

        # Migration: Add onboarding_complete column to app_settings table (added 2026-03-26)
        app_settings_cols = [row[1] for row in await conn.execute_fetchall("PRAGMA table_info(app_settings)")]
        if "onboarding_complete" not in app_settings_cols:
            logger.info("Running migration: Adding onboarding_complete column to app_settings table")
            await conn.execute("ALTER TABLE app_settings ADD COLUMN onboarding_complete INTEGER DEFAULT 0")
            # Existing users who already completed initialization should skip onboarding.
            # ALTER TABLE ADD COLUMN gives existing rows DEFAULT 0, but these users have
            # been using the app — don't make them re-run the wizard.
            await conn.execute(
                "UPDATE app_settings SET onboarding_complete = 1 WHERE initialization_complete = 1"
            )
            await conn.commit()
            logger.info("Migration complete: onboarding_complete column added to app_settings")

        # Migration: Add user_key column to channel_sessions (added 2026-03-26)
        # Required for L2 cross-channel session sharing (Swarm Brain model)
        cs_cols = [row[1] for row in await conn.execute_fetchall("PRAGMA table_info(channel_sessions)")]
        if "user_key" not in cs_cols:
            logger.info("Running migration: Adding user_key column to channel_sessions table")
            await conn.execute("ALTER TABLE channel_sessions ADD COLUMN user_key TEXT")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_channel_sessions_user_key ON channel_sessions(user_key)")
            await conn.commit()
            logger.info("Migration complete: user_key column added to channel_sessions")

    @property
    def agents(self) -> SQLiteTable:
        """Get the agents table."""
        return self._agents

    @property
    def mcp_servers(self) -> SQLiteMCPServersTable:
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
    def tasks(self) -> SQLiteTasksTable:
        """Get the tasks table."""
        return self._tasks

    @property
    def channels(self) -> SQLiteTable:
        """Get the channels table."""
        return self._channels

    @property
    def channel_sessions(self) -> SQLiteChannelSessionsTable:
        """Get the channel sessions table."""
        return self._channel_sessions

    @property
    def channel_messages(self) -> SQLiteChannelMessagesTable:
        """Get the channel messages table."""
        return self._channel_messages

    @property
    def channel_user_identities(self) -> SQLiteChannelUserIdentitiesTable:
        """Get the channel user identities table (cross-channel session sharing)."""
        return self._channel_user_identities

    @property
    def workspace_config(self) -> SQLiteWorkspaceConfigTable:
        """Get the workspace_config table (singleton SwarmWS config)."""
        return self._workspace_config

    @property
    def todos(self) -> SQLiteToDosTable:
        """Get the todos table."""
        return self._todos

    @property
    def workspace_mcps(self) -> SQLiteWorkspaceMcpsTable:
        """Get the workspace MCPs table."""
        return self._workspace_mcps

    @property
    def workspace_knowledgebases(self) -> SQLiteWorkspaceKnowledgebasesTable:
        """Get the workspace knowledgebases table."""
        return self._workspace_knowledgebases

    @property
    def workspace_audit_log(self) -> SQLiteWorkspaceAuditLogTable:
        """Get the workspace audit log table."""
        return self._workspace_audit_log

    @property
    def chat_threads(self) -> SQLiteChatThreadsTable:
        """Get the chat threads table."""
        return self._chat_threads

    @property
    def thread_summaries(self) -> SQLiteThreadSummariesTable:
        """Get the thread summaries table."""
        return self._thread_summaries

    @property
    def chat_messages(self) -> SQLiteChatMessagesTable:
        """Get the chat messages table."""
        return self._chat_messages

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
