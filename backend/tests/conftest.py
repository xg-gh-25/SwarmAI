"""Test fixtures and configuration for backend tests."""
import pytest
import asyncio
import tempfile
import os
from pathlib import Path
from typing import Generator, AsyncGenerator

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

import database as database_module
from database.sqlite import SQLiteDatabase


# ---------------------------------------------------------------------------
# Test database setup
# ---------------------------------------------------------------------------

# Create a temp file for the test database (once per process).
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix=".db", prefix="owork_test_")
os.close(_test_db_fd)

# Replace the global db singleton with one pointing at the temp file.
_test_db = SQLiteDatabase(db_path=_test_db_path)
database_module.db = _test_db
database_module._db_instance = _test_db


# Tables that are cleared between tests (order doesn't matter for DELETE).
_TABLES_TO_CLEAR = [
    "channel_messages",
    "channel_sessions",
    "channels",
    "tasks",
    "permission_requests",
    "messages",
    "sessions",
    "skill_versions",
    "skills",
    "mcp_servers",
    "agents",
    "plugins",
    "marketplaces",
    "users",
    "app_settings",
]


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
    # Clean up temp database file
    try:
        os.unlink(_test_db_path)
    except OSError:
        pass


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Create a synchronous test client.

    The TestClient context manager triggers the app lifespan which calls
    ``initialize_database()`` — this creates all tables in the temp db.
    """
    from main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client for streaming tests."""
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def reset_database():
    """Reset database tables before each test.

    Deletes all rows from every application table so tests start with a
    clean slate, then seeds the default agent that the app expects to
    exist.  Runs as an async fixture so it can use aiosqlite.
    """
    # Ensure schema exists (idempotent)
    await _test_db.initialize()

    # Clear all tables before the test
    import aiosqlite
    async with aiosqlite.connect(str(_test_db.db_path)) as conn:
        for table in _TABLES_TO_CLEAR:
            await conn.execute(f"DELETE FROM {table}")
        await conn.commit()

    # Seed the default agent (the app and tests expect it to exist)
    from datetime import datetime
    now = datetime.now().isoformat()
    await _test_db.agents.put({
        "id": "default",
        "name": "Default Agent",
        "description": "Default system agent",
        "model": "claude-sonnet-4-20250514",
        "permission_mode": "default",
        "created_at": now,
        "updated_at": now,
    })

    yield

    # No teardown needed — next test will clear tables again.


# ---------------------------------------------------------------------------
# Sample test data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_agent_data():
    """Sample agent data for tests."""
    return {
        "name": "Test Agent",
        "description": "A test agent for unit tests",
        "model": "claude-sonnet-4-20250514",
        "permission_mode": "default",
        "max_turns": 10,
        "system_prompt": "You are a helpful test agent.",
        "skill_ids": [],
        "mcp_ids": [],
        "enable_bash_tool": False,
        "enable_file_tools": True,
        "enable_web_tools": False,
    }


@pytest.fixture
def sample_skill_data():
    """Sample skill data for tests."""
    return {
        "name": "TestSkill",
        "description": "A test skill for unit tests",
        "version": "1.0.0",
        "is_system": False,
    }


@pytest.fixture
def sample_mcp_data():
    """Sample MCP server data for tests."""
    return {
        "name": "Test MCP Server",
        "description": "A test MCP server",
        "connection_type": "stdio",
        "config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-test"]
        },
        "allowed_tools": [],
        "rejected_tools": [],
    }


@pytest.fixture
def sample_chat_request():
    """Sample chat request data for tests."""
    return {
        "agent_id": "default",
        "message": "Hello, this is a test message.",
        "session_id": None,
        "enable_skills": False,
        "enable_mcp": False,
    }


# Error testing fixtures
@pytest.fixture
def invalid_agent_id():
    """Invalid agent ID for error tests."""
    return "nonexistent-agent-id-12345"


@pytest.fixture
def invalid_skill_id():
    """Invalid skill ID for error tests."""
    return "nonexistent-skill-id-12345"


@pytest.fixture
def invalid_mcp_id():
    """Invalid MCP server ID for error tests."""
    return "nonexistent-mcp-id-12345"


@pytest.fixture
def invalid_session_id():
    """Invalid session ID for error tests."""
    return "nonexistent-session-id-12345"
