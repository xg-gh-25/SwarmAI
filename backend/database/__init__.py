"""Database layer for Agent Platform.

This module provides a database abstraction layer using SQLite for the desktop application.

Usage:
    from database import db, get_database, initialize_database

    # Initialize database (required for SQLite)
    await initialize_database()

    # Then use normally
    agents = await db.agents.list()
    agent = await db.agents.get("agent-id")
"""
from database.base import BaseDatabase, BaseTable
from database.sqlite import SQLiteDatabase
from config import settings

_db_instance: SQLiteDatabase | None = None


def _create_database() -> SQLiteDatabase:
    """Create the SQLite database instance."""
    return SQLiteDatabase(db_path=settings.sqlite_db_path)


def get_database() -> SQLiteDatabase:
    """Get the database instance.

    Returns:
        SQLiteDatabase: The SQLite database instance.
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = _create_database()
    return _db_instance


async def initialize_database(skip_schema: bool = False) -> None:
    """Initialize the database schema.

    Args:
        skip_schema: When True, creates the DB instance but skips schema DDL
            and migrations (used for seed-sourced databases that already have
            a complete schema). When False (default), runs the full schema
            initialization — preserving existing behavior.
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = _create_database()
    await _db_instance.initialize(skip_init=skip_schema)


# Convenience alias for direct access
# Note: You must call initialize_database() first
db = get_database()

__all__ = [
    "BaseDatabase",
    "BaseTable",
    "get_database",
    "initialize_database",
    "db",
]
