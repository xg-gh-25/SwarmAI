"""Database layer for Agent Platform.

This module provides a database abstraction layer supporting:
- DynamoDB for cloud deployment
- SQLite for desktop application

The database type is determined by the DATABASE_TYPE environment variable:
- "dynamodb": Uses DynamoDB (default for cloud)
- "sqlite": Uses SQLite (default for desktop)

Usage:
    from database import db, get_database, initialize_database

    # Initialize database (required for SQLite)
    await initialize_database()

    # Then use normally
    agents = await db.agents.list()
    agent = await db.agents.get("agent-id")
"""
from database.base import BaseDatabase, BaseTable
from config import settings

_db_instance: BaseDatabase | None = None


def _create_database() -> BaseDatabase:
    """Create the appropriate database instance based on configuration."""
    if settings.database_type == "sqlite":
        from database.sqlite import SQLiteDatabase
        return SQLiteDatabase(db_path=settings.sqlite_db_path)
    else:
        from database.dynamodb import DynamoDBDatabase
        return DynamoDBDatabase()


def get_database() -> BaseDatabase:
    """Get the database instance.

    Returns:
        BaseDatabase: Database instance (DynamoDB or SQLite based on config).
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = _create_database()
    return _db_instance


async def initialize_database() -> None:
    """Initialize the database.

    This is required for SQLite to create the schema.
    For DynamoDB, this is a no-op (tables are created via infrastructure).
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = _create_database()

    # SQLite needs schema initialization
    if settings.database_type == "sqlite":
        from database.sqlite import SQLiteDatabase
        if isinstance(_db_instance, SQLiteDatabase):
            await _db_instance.initialize()


# Convenience alias for direct access
# Note: For SQLite, you must call initialize_database() first
db = get_database()

__all__ = [
    "BaseDatabase",
    "BaseTable",
    "get_database",
    "initialize_database",
    "db",
]
