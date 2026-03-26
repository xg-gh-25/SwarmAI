"""Test fixtures and configuration for backend tests.

Test infrastructure optimization layers:

1. **Parallel execution** -- Auto-injects pytest-xdist (-n 4) when installed.
   Each worker handles ~460 tests at ~2GB peak instead of 9GB total.
   Install: ``pip install pytest-xdist``

2. **Tiered test selection** -- Three markers for selective running:
   - ``pbt`` -- auto-applied to all Hypothesis property-based tests (46 files)
   - ``slow`` -- manually applied to known heavy tests
   - ``integration`` -- tests needing external resources
   Run fast subset: ``pytest -m 'not pbt'`` (~1200 unit tests, <15s)
   Run PBT only:   ``pytest -m pbt`` (~650 tests)
   Run everything:  ``pytest`` (default, all ~1850 tests)

3. **Profile-aware PBT** -- Hypothesis settings centralized in helpers.py
   (PROPERTY_SETTINGS / PROPERTY_SETTINGS_MINIMAL). max_examples inherits
   from the active profile:
   - default (local dev): 30 examples -- ~70% faster
   - ci (CI pipeline):   100 examples -- full coverage
   Switch: ``HYPOTHESIS_PROFILE=ci pytest``

4. **Memory safety** -- MemoryWatchdogPlugin samples RSS every 25 tests.
   Triggers GC at 1.5GB, aborts at 2.5GB before macOS jetsam kills.
   Thresholds tuned for 36GB machine with SwarmAI + browser overhead.

DB reset uses a single executescript() call to clear all 21 tables in one
round-trip instead of 21 individual execute() + commit() calls.
"""
import gc
import logging
import signal
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
    """Register markers, memory safety plugins, and auto-inject xdist."""
    # Register custom markers
    config.addinivalue_line("markers", "pbt: property-based tests using Hypothesis")
    config.addinivalue_line("markers", "slow: marks tests as slow-running")
    config.addinivalue_line("markers", "integration: tests requiring external resources")

    # Register memory watchdog (works with or without xdist)
    config.pluginmanager.register(MemoryWatchdogPlugin(), "memory_watchdog")

    # Register per-test timeout (SIGALRM — works on macOS/Linux).
    # Each xdist worker is its own process, so SIGALRM is per-worker safe.
    # This is the ONLY reliable way to kill a hanging test — cooperative
    # timeouts in teardown never fire if the test itself hangs.
    if hasattr(signal, "SIGALRM"):
        config.pluginmanager.register(SigalrmTimeoutPlugin(), "sigalrm_timeout")

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
                f"Auto-injecting -n {workercount} "
                f"(pytest-xdist detected, user didn't specify -n)"
            )
    except (ImportError, AttributeError):
        # xdist not installed — watchdog is our only memory defense.
        # Install it: pip install pytest-xdist
        pass


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests for tiered execution.

    Markers applied:
    - ``pbt``: any test whose module uses Hypothesis ``@given``.
    - ``slow``: any test in a module whose filename contains "stress" or
      "e2e", OR any PBT test with ``max_examples >= 100`` in its settings.

    Usage during dev iteration::

        pytest -m "not slow"       # skip stress + heavy PBT
        pytest -m "not pbt"        # skip all property-based tests
        pytest --lf                # re-run only last failures
    """
    pbt_marker = pytest.mark.pbt
    slow_marker = pytest.mark.slow
    # Module names that are inherently slow
    _SLOW_PATTERNS = {"stress", "e2e"}

    for item in items:
        module = item.module
        if module is None:
            continue

        # --- PBT detection (cached per module) ---
        if not hasattr(module, "_has_hypothesis"):
            module._has_hypothesis = hasattr(module, "__hypothesis_pytestplugin_setup")
            if not module._has_hypothesis:
                try:
                    import inspect
                    src = inspect.getsource(module)
                    module._has_hypothesis = "@given(" in src
                except (TypeError, OSError):
                    module._has_hypothesis = False
        if module._has_hypothesis:
            item.add_marker(pbt_marker)

        # --- Slow detection ---
        # 1. Module filename contains "stress" or "e2e"
        mod_name = getattr(module, "__name__", "")
        if any(p in mod_name for p in _SLOW_PATTERNS):
            item.add_marker(slow_marker)
            continue

        # 2. PBT test with high max_examples (>= 100)
        if module._has_hypothesis:
            # Check if the test function itself has @settings(max_examples>=100)
            test_func = getattr(item, "obj", None)
            if test_func is not None:
                hyp_settings = getattr(test_func, "_hypothesis_internal_use_settings", None)
                if hyp_settings is not None and hyp_settings.max_examples >= 100:
                    item.add_marker(slow_marker)


# ---------------------------------------------------------------------------
# Per-test timeout — SIGALRM kills hanging tests at the OS level
# ---------------------------------------------------------------------------

# Default timeout in seconds. Override per-test with @pytest.mark.timeout(N).
_DEFAULT_TEST_TIMEOUT = 30


class _TestTimeoutError(Exception):
    """Raised by SIGALRM when a test exceeds its timeout."""
    pass


class SigalrmTimeoutPlugin:
    """Pytest plugin that enforces per-test timeouts via SIGALRM.

    Why SIGALRM instead of cooperative timeouts:
    - Cooperative timeouts (checking time.monotonic() in teardown) NEVER fire
      if the test itself hangs — teardown only runs after the test returns.
    - SIGALRM fires regardless of what the test is doing (blocked I/O, deadlock,
      infinite loop).
    - Each xdist worker is its own process with its own signal handlers,
      so SIGALRM is safe with -n 4.

    Limitation: only works on Unix (macOS/Linux). Windows has no SIGALRM.
    """

    def __init__(self):
        self._previous_handler = None

    def pytest_runtest_setup(self, item):
        """Set SIGALRM before each test."""
        # Check for @pytest.mark.timeout(N) override
        marker = item.get_closest_marker("timeout")
        timeout = marker.args[0] if marker and marker.args else _DEFAULT_TEST_TIMEOUT

        def _alarm_handler(signum, frame):
            raise _TestTimeoutError(
                f"Test timed out after {timeout}s: {item.nodeid}"
            )

        self._previous_handler = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(timeout)

    def pytest_runtest_teardown(self, item):
        """Cancel the alarm after the test completes."""
        signal.alarm(0)
        if self._previous_handler is not None:
            signal.signal(signal.SIGALRM, self._previous_handler)
            self._previous_handler = None


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

    Samples RSS every ``_CHECK_INTERVAL`` tests (not every test) to avoid
    the ~85s cumulative overhead of calling ``gc.collect(2)`` 1700+ times.
    Full GC only fires when RSS exceeds the 1.5GB threshold.

    Defense layers:
    - Every _CHECK_INTERVAL tests: RSS check against thresholds
    - At 1.5GB: aggressive multi-generation GC
    - At 2.5GB: abort with clear error + xdist recommendation
    """

    _CHECK_INTERVAL = 25  # sample RSS every N tests (psutil ~50μs, negligible)

    def __init__(self):
        self._test_count = 0
        self._peak_rss = 0
        self._gc_trigger_count = 0
        self._last_warning_at = 0  # test count at last warning (rate-limit logs)

    def pytest_runtest_teardown(self, item):
        """Run after every test teardown — periodic RSS check + GC."""
        self._test_count += 1

        # Only sample RSS periodically — avoids gc.collect overhead on every test
        if self._test_count % self._CHECK_INTERVAL != 0:
            return

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

# Safety net: atexit cleanup ensures temp DB is removed even when the test
# process is killed by pytest-xdist, ResourceWatchdog abort, or OOM.
# The scope="session" fixture cleanup only runs on graceful shutdown.
import atexit
atexit.register(lambda: os.unlink(_test_db_path) if os.path.exists(_test_db_path) else None)

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

    # Clear all tables in a single batch (one round-trip instead of 21)
    import aiosqlite
    _delete_script = ";\n".join(f"DELETE FROM {t}" for t in _TABLES_TO_CLEAR)
    async with aiosqlite.connect(str(_test_db.db_path)) as conn:
        await conn.executescript(_delete_script)

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
