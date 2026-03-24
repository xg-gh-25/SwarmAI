"""Test fixtures and configuration for backend tests.

Memory management: 1,759 tests in a single process can spike to 9GB+ RSS
because Python's allocator doesn't eagerly return pages to the OS. Four
structural defenses prevent this from crashing the system during peak usage:

1. Auto-xdist injection — if pytest-xdist is installed and user didn't pass
   -n explicitly, auto-inject -n auto to split across CPU cores. Each worker
   handles ~440 tests at ~2GB peak instead of 9GB total.
2. GC after every test — prevents monotonic RSS growth from accumulated objects
3. Memory watchdog plugin — monitors RSS every test, triggers aggressive GC
   at 1.5GB, aborts the session at 2.5GB before macOS jetsam kills processes
4. Subprocess fallback — when xdist isn't available, the safe_test_runner.py
   script splits tests into batches of 200 via subprocess invocations

Thresholds are tuned for a 36GB machine running 2 chat sessions + MCPs (~3GB)
+ browser/IDE (~5GB). The 2.5GB abort leaves 25GB+ for the rest of the system.
"""
import gc
import logging
import pytest
import tempfile
import os
import shutil
from pathlib import Path
from typing import Generator, AsyncGenerator
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

import database as database_module
from database.sqlite import SQLiteDatabase

_logger = logging.getLogger("test_memory")

# ---------------------------------------------------------------------------
# Auto-inject xdist when available (structural memory prevention)
# ---------------------------------------------------------------------------

def pytest_configure(config):
    """Register memory safety plugins and auto-inject xdist."""
    # Register memory watchdog first (works with or without xdist)
    config.pluginmanager.register(MemoryWatchdogPlugin(), "memory_watchdog")

    # Auto-inject -n auto if xdist is available and user didn't specify -n
    try:
        import xdist  # noqa: F401
        # Verify xdist actually registered its options
        if not hasattr(config.option, "numprocesses"):
            return
        # Check if user already passed -n (don't override explicit choice)
        if not any(
            arg.startswith("-n") or arg == "--numprocesses"
            for arg in config.invocation_params.args
        ):
            # Inject parallel execution — split tests across CPU cores
            # Each worker gets its own process with isolated memory
            workercount = os.cpu_count() or 4
            # Cap at 4 workers to avoid DB contention and diminishing returns
            workercount = min(workercount, 4)
            config.option.numprocesses = workercount
            config.option.dist = "loadgroup"
            _logger.info(
                f"Memory safety: auto-injecting -n {workercount} "
                f"(pytest-xdist detected, user didn't specify -n)"
            )
    except (ImportError, AttributeError):
        # xdist not installed — watchdog is our defense
        pass


# ---------------------------------------------------------------------------
# Memory watchdog — prevents runaway RSS from crashing the system
# ---------------------------------------------------------------------------

# Thresholds in bytes — tuned for 36GB machine during peak usage
_GC_THRESHOLD = int(1.5 * 1024**3)   # 1.5 GB — trigger aggressive GC
_ABORT_THRESHOLD = int(2.5 * 1024**3)  # 2.5 GB — abort before system crash

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


def _get_rss() -> int:
    """Get current process RSS in bytes. Returns 0 if unavailable."""
    if _PSUTIL_AVAILABLE:
        return psutil.Process().memory_info().rss
    return 0


def _aggressive_gc() -> int:
    """Force full GC cycle and return bytes freed (estimated)."""
    before = _get_rss()
    # Collect all generations, youngest to oldest
    gc.collect(0)
    gc.collect(1)
    gc.collect(2)
    after = _get_rss()
    freed = max(0, before - after)
    if freed > 50 * 1024**2:  # Log if freed >50MB
        _logger.info(f"GC freed {freed / 1024**2:.0f} MB (RSS: {after / 1024**2:.0f} MB)")
    return freed


class MemoryWatchdogPlugin:
    """Pytest plugin that monitors RSS and prevents memory explosions.

    Checks after EVERY test (not every 50) — psutil.Process().memory_info()
    costs ~50μs which adds <0.1s per 1759 tests. The cost of NOT checking
    is a 9GB spike that crashes the system.

    Defense layers:
    - Every test: gc.collect(2) to prevent RSS pile-up
    - Every test: RSS check against thresholds
    - At 1.5GB: aggressive multi-generation GC
    - At 2.5GB: abort with clear error + xdist recommendation
    """

    def __init__(self):
        self._test_count = 0
        self._peak_rss = 0
        self._gc_trigger_count = 0
        self._last_warning_at = 0  # test count at last warning (rate-limit logs)

    def pytest_runtest_teardown(self, item):
        """Run after every test teardown — GC + RSS check."""
        gc.collect(2)  # Full collection, all generations
        self._test_count += 1

        rss = _get_rss()
        if rss == 0:
            return  # psutil unavailable, can't monitor

        if rss > self._peak_rss:
            self._peak_rss = rss

        if rss > _ABORT_THRESHOLD:
            # Last-ditch GC attempt
            _aggressive_gc()
            rss_after = _get_rss()
            if rss_after > _ABORT_THRESHOLD:
                pytest.exit(
                    f"\n{'='*60}\n"
                    f"MEMORY SAFETY ABORT\n"
                    f"{'='*60}\n"
                    f"RSS: {rss_after / 1024**3:.1f}GB exceeds "
                    f"{_ABORT_THRESHOLD / 1024**3:.1f}GB limit "
                    f"after {self._test_count} tests.\n"
                    f"Peak RSS: {self._peak_rss / 1024**3:.1f}GB\n"
                    f"GC triggers: {self._gc_trigger_count}\n\n"
                    f"Fix: install pytest-xdist and run with -n auto\n"
                    f"  pip install pytest-xdist\n"
                    f"  pytest -n auto\n"
                    f"{'='*60}",
                    returncode=137,
                )
            # GC brought us back under — continue but warn
            self._gc_trigger_count += 1

        elif rss > _GC_THRESHOLD:
            self._gc_trigger_count += 1
            freed = _aggressive_gc()
            # Rate-limit warnings: at most once per 100 tests
            if self._test_count - self._last_warning_at > 100:
                self._last_warning_at = self._test_count
                _logger.warning(
                    f"Memory pressure: RSS {rss / 1024**2:.0f}MB after "
                    f"{self._test_count} tests. GC freed {freed / 1024**2:.0f}MB. "
                    f"({self._gc_trigger_count} GC triggers total)"
                )

    def pytest_terminal_summary(self, terminalreporter):
        """Report peak memory usage in the test summary."""
        if self._peak_rss > 0:
            msg = (
                f"Peak RSS: {self._peak_rss / 1024**2:.0f} MB "
                f"({self._test_count} tests, "
                f"{self._gc_trigger_count} GC triggers)"
            )
            is_warning = self._peak_rss > _GC_THRESHOLD
            is_error = self._peak_rss > _ABORT_THRESHOLD
            terminalreporter.write_line(
                msg,
                yellow=is_warning and not is_error,
                red=is_error,
            )
            if self._gc_trigger_count > 10:
                terminalreporter.write_line(
                    "  Recommend: pytest -n auto (splits tests across workers)",
                    yellow=True,
                )


# ---------------------------------------------------------------------------
# Test database setup
# ---------------------------------------------------------------------------

# Create a temp file for the test database (once per process).
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix=".db", prefix="swarmai_test_")
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
    "chat_messages",
    "thread_summaries",
    "chat_threads",
    "todos",
    "tasks",
    "messages",
    "sessions",
    "workspace_mcps",
    "workspace_knowledgebases",
    "workspace_audit_log",
    "mcp_servers",
    "agents",
    "plugins",
    "marketplaces",
    "users",
    "app_settings",
    "workspace_config",
]


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    """Clean up temp database file after the test session."""
    yield
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
        "name": "SwarmAgent",
        "description": "Default system agent",
        "model": "claude-sonnet-4-20250514",
        "permission_mode": "default",
        "is_default": True,  # Mark as default agent
        "is_system_agent": True,  # Mark as protected system agent
        "created_at": now,
        "updated_at": now,
    })

    yield

    # Cleanup: Remove any test skills created in ~/.swarm-ai/skills/
    # This prevents test pollution of the user's real skills directory
    skills_dir = Path.home() / ".swarm-ai" / "skills"
    if skills_dir.exists():
        for item in skills_dir.iterdir():
            if item.is_dir():
                try:
                    shutil.rmtree(item)
                except Exception:
                    pass  # Best effort cleanup

    # Cleanup: Remove any test skills created in ~/.swarm-ai/built-in-skills/
    builtin_dir = Path.home() / ".swarm-ai" / "built-in-skills"
    if builtin_dir.exists():
        for item in builtin_dir.iterdir():
            if item.is_dir():
                try:
                    shutil.rmtree(item)
                except Exception:
                    pass  # Best effort cleanup


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
        "allowed_skills": [],
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
