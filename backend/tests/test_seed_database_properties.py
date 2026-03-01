"""Property-based tests for Seed Database generation.

Uses Hypothesis to verify universal properties across all valid inputs.

**Feature: pre-seeded-database**
"""
import pytest
import asyncio
import tempfile
import os
from pathlib import Path
from typing import Dict, List, Tuple

import aiosqlite
from hypothesis import given, strategies as st, settings, HealthCheck

from database.sqlite import SQLiteDatabase


# Suppress function-scoped fixture warning since we're testing with isolated
# database instances across iterations
PROPERTY_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)


async def get_schema_info(db_path: Path) -> Dict[str, List[Tuple[str, str, str]]]:
    """Extract schema information from a SQLite database.
    
    Returns a dictionary mapping table names to lists of (column_name, column_type, notnull) tuples.
    """
    schema = {}
    async with aiosqlite.connect(str(db_path)) as conn:
        # Get all table names
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = await cursor.fetchall()
        
        for (table_name,) in tables:
            # Get column info for each table
            cursor = await conn.execute(f"PRAGMA table_info({table_name})")
            columns = await cursor.fetchall()
            # columns format: (cid, name, type, notnull, dflt_value, pk)
            schema[table_name] = [
                (col[1], col[2], str(col[3]))  # name, type, notnull
                for col in columns
            ]
    
    return schema


async def get_index_info(db_path: Path) -> Dict[str, List[str]]:
    """Extract index information from a SQLite database.
    
    Returns a dictionary mapping table names to lists of index names.
    """
    indexes = {}
    async with aiosqlite.connect(str(db_path)) as conn:
        cursor = await conn.execute(
            "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        rows = await cursor.fetchall()
        
        f