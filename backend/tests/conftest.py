"""Test fixtures and configuration for backend tests.

Single authority for all test infrastructure:

0. **Venv guard** — Refuses to run if the wrong Python interpreter is used.

1. **xdist enforcement** — pyproject.toml addopts has ``-n 4``. Conftest
   clamps to memory-safe worker count, respects explicit ``-n 0`` for serial.

2. **Tiered test selection** — Three markers for selective running:
   - ``pbt`` — auto-applied to all Hypothesis property-based tests
   - ``slow`` — auto-applied to stress/e2e files and high-example PBT
   - ``integration`` — manually applied to tests needing external resources
   Run fast subset: ``pytest -m 'not pbt and not slow'``
   Run PBT only:   ``pytest -m pbt``

3. **Memory safety** — MemoryWatchdogPlugin checks RSS every test.
   GC at 512MB, abort at 2GB. System-level check every 5 tests.

4. **Per-test timeout** — via pytest-timeout (``--timeout=120``).
   Structurally prevents hangs. No custom SIGALRM needed.

5. **DB isolation** — Temp SQLite DB created once per process, tables
   cleared between tests via a single executescript() call.
"""

# ---------------------------------------------------------------------------
# Venv guard — must fire BEFORE importing any venv-only packages
# ---------------------------------------------------------------------------
import sys as _sys
import os as _os

_VENV_DIR = _os.path.join(_os.path.dirname(__file__), "..", ".venv")
if _os.path.isdir(_VENV_DIR) and not _sys.prefix.startswith(
    _os.path.realpath(_VENV_DIR)
):
    print(
        f"\n{'=' * 60}\n"
        f"WRONG PYTHON: {_sys.executable}\n"
        f"  expected venv: {_os.path.realpath(_VENV_DIR)}\n"
        f"{'=' * 60}\n"
        f"Fix: cd backend && python -m pytest  (from activated venv)\n"
        f"{'=' * 60}",
        file=_sys.stderr,
    )
    _sys.exit(1)

import atexit

import gc
import os
import resource
import signal
import tempfile

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from typing import Generator, AsyncGenerator

import database as database_module
from database.sqlite import SQLiteDatabase



# Concurrency guard REMOVED (2026-04-02): Each pytest process creates its own
# temp DB via tempfile.mkstemp() — no shared state, no conflict. Two tabs
# running different test files concurrently is safe and expected. The lock was
# based on a stale assumption that tests shared a single DB file.

# ---------------------------------------------------------------------------
# Child process cleanup — kill xdist workers on master exit
# ---------------------------------------------------------------------------
def _kill_child_processes():
    """Kill all child processes on exit to prevent orphans.

    When pytest master exits (normal, timeout, SIGTERM), this ensures xdist
    workers don't survive as orphans. Uses os.getpid() to find our children
    via psutil if available, falls back to SIGTERM to process group.
    """
    try:
        import psutil
        current = psutil.Process()
        children = current.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        # Wait briefly then force-kill survivors
        _, alive = psutil.wait_procs(children, timeout=2)
        for child in alive:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        # No psutil — best effort: send SIGTERM to our process group
        try:
            os.killpg(os.getpid(), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

atexit.register(_kill_child_processes)


# ---------------------------------------------------------------------------
# Memory-adaptive worker count
# ---------------------------------------------------------------------------
_WORKER_MEMORY_BUDGET = int(1.0 * 1024**3)        # 1 GB per worker
_MIN_AVAILABLE_FOR_PARALLEL = int(6.0 * 1024**3)   # need 6GB+ for parallel
_MIN_AVAILABLE_TO_START = int(3.0 * 1024**3)        # refuse below 3GB
_MAX_WORKERS = 4

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


def _compute_safe_worker_count() -> int:
    """Compute xdist worker count based on available memory.

    Returns 0 for serial when memory is tight. Also acts as pre-flight:
    refuses to start at all if system memory is critically low (<3GB).
    """
    if not _PSUTIL_AVAILABLE:
        return 0

    try:
        available = psutil.virtual_memory().available

        # Pre-flight: refuse if critically low
        if available < _MIN_AVAILABLE_TO_START:
            pytest.exit(
                f"\nSYSTEM MEMORY TOO LOW: {available / 1024**3:.1f}GB "
                f"(need >= {_MIN_AVAILABLE_TO_START / 1024**3:.0f}GB)\n"
                f"Close some apps and retry.",
                returncode=1,
            )

        if available < _MIN_AVAILABLE_FOR_PARALLEL:
            return 0

        headroom = int(4.0 * 1024**3)
        budget = max(0, available - headroom)
        max_by_memory = max(1, int(budget / _WORKER_MEMORY_BUDGET))
        max_by_cpu = min(os.cpu_count() or 2, _MAX_WORKERS)
        return min(max_by_memory, max_by_cpu)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# pytest_configure — markers, plugins, xdist enforcement
# ---------------------------------------------------------------------------

def pytest_configure(config):
    """Register markers, safety plugins, and enforce xdist worker count."""
    is_xdist_worker = hasattr(config, "workerinput")

    # Master-only setup
    if not is_xdist_worker:
        # NOTE: os.setpgrp() was here but REMOVED (2026-04-02). It detached
        # pytest from the shell's process group, making it invisible to the
        # Bash tool's SIGTERM cleanup. Result: Bash timeout kills the shell
        # but pytest + xdist workers survive as orphans (the 30-min hang bug).
        # Without setpgrp, pytest stays in the shell's group and dies with it.
        pass

    # Markers
    config.addinivalue_line("markers", "pbt: property-based tests using Hypothesis")
    config.addinivalue_line("markers", "slow: marks tests as slow-running")
    config.addinivalue_line("markers", "integration: tests requiring external resources")

    # Plugins
    config.pluginmanager.register(MemoryWatchdogPlugin(), "memory_watchdog")

    # Workers don't manage xdist — master already decided worker count
    if is_xdist_worker:
        return

    # --- xdist enforcement ---
    # pyproject.toml addopts has -n 4. This block handles two cases:
    # 1. Memory pressure → downgrade to fewer workers or serial
    # 2. Explicit -n 0 → RESPECT it (agent coding runs small test sets where
    #    serial is faster due to ~8s worker startup overhead)
    # Never force xdist back on when explicitly disabled.
    try:
        import xdist  # noqa: F401
    except ImportError:
        print(
            "\n⚠️  pytest-xdist NOT INSTALLED — running serial. "
            "Fix: uv sync --group dev",
            file=_sys.stderr,
        )
        return

    safe_count = _compute_safe_worker_count()

    if not hasattr(config.option, "numprocesses"):
        # --override-ini="addopts=" stripped -n flag entirely.
        # Don't re-inject — the caller explicitly removed it.
        print("ℹ️  xdist: -n flag stripped by caller. Serial.", file=_sys.stderr)
        return

    requested = config.option.numprocesses or 0

    if safe_count == 0:
        config.option.numprocesses = 0
        config.option.dist = "no"
        print("⚠️  xdist DISABLED — insufficient memory. Serial.", file=_sys.stderr)
    elif requested == 0:
        # Explicit -n 0 — respect the caller's choice. Serial is faster
        # for small test sets (<50 tests) due to worker startup overhead.
        print("ℹ️  xdist: -n 0 respected. Serial.", file=_sys.stderr)
    elif requested > safe_count:
        config.option.numprocesses = safe_count
        print(f"⚠️  xdist: clamped {requested} → {safe_count} workers", file=_sys.stderr)
    else:
        print(f"✓ xdist: {requested} workers (budget: {safe_count})", file=_sys.stderr)


# ---------------------------------------------------------------------------
# Auto-mark tests for tiered execution
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(config, items):
    """Auto-mark tests: ``pbt`` for Hypothesis, ``slow`` for heavy tests."""
    pbt_marker = pytest.mark.pbt
    slow_marker = pytest.mark.slow
    _SLOW_PATTERNS = {"stress", "e2e"}

    for item in items:
        module = item.module
        if module is None:
            continue

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

        mod_name = getattr(module, "__name__", "")
        if any(p in mod_name for p in _SLOW_PATTERNS):
            item.add_marker(slow_marker)
            continue

        if module._has_hypothesis:
            test_func = getattr(item, "obj", None)
            if test_func is not None:
                hyp_settings = getattr(test_func, "_hypothesis_internal_use_settings", None)
                if hyp_settings is not None and hyp_settings.max_examples >= 100:
                    item.add_marker(slow_marker)


# ---------------------------------------------------------------------------
# Memory watchdog plugin — per-test RSS monitoring
# ---------------------------------------------------------------------------

_GC_THRESHOLD = int(512 * 1024**2)      # 512 MB — trigger GC
_ABORT_THRESHOLD = int(2 * 1024**3)     # 2 GB — abort
_SYSTEM_AVAIL_FLOOR = int(3 * 1024**3)  # 3 GB system minimum
_PROACTIVE_GC_INTERVAL = 25


def _get_rss() -> int:
    """Get current process RSS in bytes. psutil primary, resource fallback."""
    if _PSUTIL_AVAILABLE:
        try:
            return psutil.Process().memory_info().rss
        except Exception:
            pass
    try:
        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return maxrss if _sys.platform == "darwin" else maxrss * 1024
    except Exception:
        return 0


def _get_system_available() -> int:
    if _PSUTIL_AVAILABLE:
        try:
            return psutil.virtual_memory().available
        except Exception:
            pass
    return 0


def _aggressive_gc() -> int:
    before = _get_rss()
    gc.collect(0)
    gc.collect(1)
    gc.collect(2)
    return max(0, before - _get_rss())


class MemoryWatchdogPlugin:
    """Monitors RSS per-test. GC at 512MB, abort at 2GB, system check every 5."""

    _DEEP_CHECK_INTERVAL = 5

    def __init__(self):
        self._test_count = 0
        self._peak_rss = 0
        self._gc_trigger_count = 0

    def _check_and_abort(self, context: str):
        rss = _get_rss()
        if rss == 0:
            return
        if rss > self._peak_rss:
            self._peak_rss = rss

        if rss > _ABORT_THRESHOLD:
            _aggressive_gc()
            rss_after = _get_rss()
            if rss_after > _ABORT_THRESHOLD:
                pytest.exit(
                    f"\nMEMORY ABORT ({context}): "
                    f"{rss_after / 1024**2:.0f}MB > {_ABORT_THRESHOLD / 1024**2:.0f}MB "
                    f"after {self._test_count} tests",
                    returncode=137,
                )
            self._gc_trigger_count += 1
        elif rss > _GC_THRESHOLD:
            self._gc_trigger_count += 1
            _aggressive_gc()

    def _check_system_memory(self):
        sys_avail = _get_system_available()
        if 0 < sys_avail < _SYSTEM_AVAIL_FLOOR:
            _aggressive_gc()
            if _get_system_available() < _SYSTEM_AVAIL_FLOOR:
                pytest.exit(
                    f"\nSYSTEM MEMORY LOW: {sys_avail / 1024**3:.1f}GB. Aborting.",
                    returncode=137,
                )

    def pytest_runtest_setup(self, item):
        self._check_and_abort("pre-test")

    def pytest_runtest_teardown(self, item):
        self._test_count += 1
        self._check_and_abort("post-test")
        if self._test_count % _PROACTIVE_GC_INTERVAL == 0:
            _aggressive_gc()
        if self._test_count % self._DEEP_CHECK_INTERVAL == 0:
            self._check_system_memory()

    def pytest_terminal_summary(self, terminalreporter):
        if self._peak_rss > 0:
            terminalreporter.write_line(
                f"Peak RSS: {self._peak_rss / 1024**2:.0f} MB "
                f"({self._test_count} tests, {self._gc_trigger_count} GC triggers)",
                yellow=self._peak_rss > _GC_THRESHOLD,
                red=self._peak_rss > _ABORT_THRESHOLD,
            )


# ---------------------------------------------------------------------------
# Test database setup
# ---------------------------------------------------------------------------

_test_db_fd, _test_db_path = tempfile.mkstemp(suffix=".db", prefix="swarmai_test_")
os.close(_test_db_fd)
atexit.register(lambda: os.unlink(_test_db_path) if os.path.exists(_test_db_path) else None)

_test_db = SQLiteDatabase(db_path=_test_db_path)
database_module.db = _test_db
database_module._db_instance = _test_db
_schema_initialized = False

_TABLES_TO_CLEAR = [
    "channel_messages", "channel_sessions", "channels",
    "chat_messages", "thread_summaries", "chat_threads",
    "todos", "tasks", "messages", "sessions",
    "workspace_mcps", "workspace_knowledgebases", "workspace_audit_log",
    "mcp_servers", "agents", "plugins", "marketplaces",
    "users", "app_settings", "workspace_config",
]


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_db():
    yield
    try:
        os.unlink(_test_db_path)
    except OSError:
        pass


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    from main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def reset_database():
    """Reset database tables before each test."""
    global _schema_initialized
    if not _schema_initialized:
        await _test_db.initialize()
        _schema_initialized = True

    import aiosqlite
    _delete_script = ";\n".join(f"DELETE FROM {t}" for t in _TABLES_TO_CLEAR)
    async with aiosqlite.connect(str(_test_db.db_path)) as conn:
        await conn.executescript(_delete_script)

    from datetime import datetime
    now = datetime.now().isoformat()
    await _test_db.agents.put({
        "id": "default",
        "name": "SwarmAgent",
        "description": "Default system agent",
        "model": "claude-sonnet-4-20250514",
        "permission_mode": "default",
        "is_default": True,
        "is_system_agent": True,
        "created_at": now,
        "updated_at": now,
    })
    yield


# ---------------------------------------------------------------------------
# Sample test data fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_agent_data():
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
    return {
        "name": "TestSkill",
        "description": "A test skill for unit tests",
        "version": "1.0.0",
        "is_system": False,
    }


@pytest.fixture
def sample_mcp_data():
    return {
        "name": "Test MCP Server",
        "description": "A test MCP server",
        "connection_type": "stdio",
        "config": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-test"]},
        "allowed_tools": [],
        "rejected_tools": [],
    }


@pytest.fixture
def sample_chat_request():
    return {
        "agent_id": "default",
        "message": "Hello, this is a test message.",
        "session_id": None,
        "enable_skills": False,
        "enable_mcp": False,
    }


@pytest.fixture
def invalid_agent_id():
    return "nonexistent-agent-id-12345"


@pytest.fixture
def invalid_skill_id():
    return "nonexistent-skill-id-12345"


@pytest.fixture
def invalid_mcp_id():
    return "nonexistent-mcp-id-12345"


@pytest.fixture
def invalid_session_id():
    return "nonexistent-session-id-12345"
