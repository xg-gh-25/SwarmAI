"""Test fixtures and configuration for backend tests.

Single authority for all test infrastructure:

0. **Venv guard** — Refuses to run if the wrong Python interpreter is used.

1. **xdist enforcement** — pyproject.toml addopts has ``-n 0`` (serial); an
   explicit ``-n N`` opts in, and conftest clamps it to a memory-safe worker count.

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
# both sides resolved: a worktree's .venv is a symlink to the main checkout's, and the
# python started through it reports the link's path as sys.prefix
if _os.path.isdir(_VENV_DIR) and not _os.path.realpath(_sys.prefix).startswith(
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

# ---------------------------------------------------------------------------
# Inherited runtime-flag strip — must fire BEFORE any app module is imported
# ---------------------------------------------------------------------------
# A pytest launched from an agent shell inherits the DAEMON's environment
# (launchd plist): SWARM_RESUME_VIA_QUERY, SWARM_DESKTOP_PREWARM, SWARMAI_MODE,
# SWARMAI_SELF_HEAL, SWARMAI_OWNER_PID, ... Production code branches on these,
# so the same test went green on CI (no flags) and red on the dev box (flags
# inherited) — e.g. the abandon-continuation tests asserted the flag-OFF shape
# while the shell carried SWARM_RESUME_VIA_QUERY=true. A flag a test depends on
# must be SET BY THE TEST (monkeypatch.setenv), never inherited, so we drop every
# SWARM_* / SWARMAI_* var here and make local runs see what CI sees. Done at
# import time (not in a fixture) because some modules read flags at import.
# Kept: the suite's own control knobs, which are deliberately exported by the
# person running the tests.
_TEST_CONTROL_ENV = frozenset({
    "SWARMAI_SUITE",
    "SWARMAI_REGEN_TOOL_SUMMARY_FIXTURE",
    "SWARMAI_REGEN_PIPELINE_STAGE_FIXTURE",
})
for _k in [k for k in _os.environ if k.startswith(("SWARM_", "SWARMAI_"))]:
    if _k not in _TEST_CONTROL_ENV:
        del _os.environ[_k]
# The app's startup sweeps leftover claude CLIs SYSTEM-WIDE (pgrep), so any test
# that boots the app (30 files build their own TestClient) would reach the LIVE
# daemon's processes. Off for the whole suite (2026-10-20 13:22).
_os.environ["SWARMAI_NO_CLI_SWEEP"] = "1"

import atexit

import gc
import os
import resource
import signal
import tempfile

import pytest
import pytest_asyncio



@pytest.fixture(autouse=True)
def _gate_cwd_is_the_repo_root(monkeypatch):
    """run_owner_gate resolves a pipeline script against the Bash call's cwd (the hook
    passes it). Unit tests that call ``_decide`` directly with a relative
    ``backend/scripts/...`` path run as if from the repo root, like an agent session."""
    from pathlib import Path as _P
    from core import run_owner_gate as _og
    real = _og._decide
    root = str(_P(__file__).resolve().parents[2])

    def decide(sid, command, tool_input, phase="check", cwd=None):
        return real(sid, command, tool_input, phase, cwd or root)
    monkeypatch.setattr(_og, "_decide", decide)


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
# Kill guard — a test must never signal the test process, its group or ancestors
# ---------------------------------------------------------------------------
# Product kill paths (SessionUnit._force_kill, the PID watchdog, process-tree
# reapers) run in tests against mocked units. A MagicMock pid is 1 as an int, a
# fake pid can collide with a real process, and the "shared pgid" branch of
# _force_kill walks our own tree. Any of these SIGKILLs the xdist worker, which
# then shows up as "worker crashed" on whatever test was running. The guard turns
# that into a PermissionError (which the kill paths already handle) and appends
# the stack to SWARM_TEST_KILL_LOG so the offending test can be found.
_REAL_OS_KILL = os.kill
_REAL_OS_KILLPG = os.killpg


def _protected_pids() -> set:
    """Self, ancestors, and every process of the pytest run that is not ours.

    xdist workers are siblings: a kill path that walks a mocked pid's tree (pid 1
    is launchd, the whole user session) reaches the OTHER workers, which die as
    "worker crashed" without any trace in their own logs. Our own descendants
    (subprocesses this test started) stay killable.
    """
    pids = {os.getpid()}
    try:
        import psutil as _ps
        me = _ps.Process()
        parents = me.parents()
        pids.update(p.pid for p in parents)
        own = {c.pid for c in me.children(recursive=True)}
        if parents:  # the xdist controller (or the shell for a serial run)
            ctrl = parents[0]
            pids.update(c.pid for c in ctrl.children(recursive=True)
                        if c.pid not in own)
    except Exception:  # psutil missing or AccessDenied: own pid + parent only
        pids.add(os.getppid())
    return pids


def _refuse_kill(kind: str, target: int, sig: int) -> None:
    import traceback
    log = os.environ.get("SWARM_TEST_KILL_LOG") or os.path.join(
        tempfile.gettempdir(), f"swarmai_test_kill_refused_{os.getpid()}.log"
    )
    try:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"--- refused {kind}({target}, {sig}) in pid {os.getpid()} "
                     f"test={os.environ.get('PYTEST_CURRENT_TEST')}\n")
            fh.write("".join(traceback.format_stack(limit=25)))
    except OSError:
        pass
    raise PermissionError(f"test kill guard: refused {kind}({target}, {sig})")


def _guarded_kill(pid, sig):
    if sig != 0:
        try:
            ipid = int(pid)
        except (TypeError, ValueError):
            ipid = None
        if ipid is not None and (ipid <= 1 or ipid in _protected_pids()
                                 or (ipid < 0 and -ipid == os.getpgid(0))):
            _refuse_kill("os.kill", ipid, sig)
    return _REAL_OS_KILL(pid, sig)


def _guarded_killpg(pgid, sig):
    if sig != 0:
        try:
            ipg = int(pgid)
        except (TypeError, ValueError):
            ipg = None
        if ipg is not None and (ipg <= 1 or ipg == os.getpgid(0)
                                or ipg in _protected_pids()):
            _refuse_kill("os.killpg", ipg, sig)
    return _REAL_OS_KILLPG(pgid, sig)


os.kill = _guarded_kill
os.killpg = _guarded_killpg


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
    config.addinivalue_line(
        "markers",
        "real_credential_check: opt out of the autouse credential-preflight stub "
        "(_stub_credential_preflight) — for tests that must exercise the real "
        "CredentialValidator.check() dispatch",
    )

    # Plugins
    config.pluginmanager.register(MemoryWatchdogPlugin(), "memory_watchdog")

    # Workers don't manage xdist — master already decided worker count
    if is_xdist_worker:
        return

    # --- xdist enforcement ---
    # pyproject.toml addopts has -n 0 (serial); a caller opts in with -n N.
    # This block handles two cases:
    # 1. Memory pressure → downgrade to fewer workers or serial
    # 2. Explicit -n 0 → RESPECT it (the default; small agent test sets are also
    #    faster serial, ~8s worker startup overhead)
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
# A function-loop test's own DB pools are closed on its own loop
# ---------------------------------------------------------------------------

_SETTLE_FIXTURE = "_settle_test_loop"


def _runs_on_its_own_loop(item) -> bool:
    """An async test on a per-test (function-scoped) loop: the loops pytest-asyncio
    closes right after the test. Session/module-loop tests keep their loop, and
    reset_database closes the session loop's pools on that loop."""
    import inspect
    fn = getattr(item, "function", None)
    if fn is None or not inspect.iscoroutinefunction(fn):
        return False
    marker = item.get_closest_marker("asyncio")
    scope = (marker.kwargs.get("loop_scope") or marker.kwargs.get("scope")) if marker else None
    return (scope or "function") == "function"


@pytest_asyncio.fixture(loop_scope="function")
async def _settle_test_loop():
    """End what the test left running, then close this loop's DB pools, while the
    loop still runs (tests/test_conftest_test_loop_teardown.py).

    Without it, a task left by the test can have a read in flight when pytest-asyncio
    closes the loop: aiosqlite's worker then cannot hand the result back, dies, and
    the next close of that connection (reset_database, on the session loop) waits
    forever. Leftover tasks are cancelled, as closing the loop would do anyway, in
    rounds: a task can start another as it ends. Closing a pool queues ``close``
    behind any read still on its worker, so the read finishes on a live loop.
    Added by pytest_collection_modifyitems to function-loop async tests only.
    """
    yield
    import asyncio as _asyncio
    from database.sqlite import close_loop_pools
    current = _asyncio.current_task()
    for _ in range(10):
        pending = [t for t in _asyncio.all_tasks() if t is not current and not t.done()]
        if not pending:
            break
        for t in pending:
            t.cancel()
        await _asyncio.wait(pending, timeout=2)
    await close_loop_pools()


# ---------------------------------------------------------------------------
# Auto-mark tests for tiered execution
# ---------------------------------------------------------------------------

def pytest_collection_modifyitems(config, items):
    """Auto-mark tests: ``pbt`` for Hypothesis, ``slow`` for heavy tests.

    Also warns (not errors) when >300 tests are collected without a -m
    marker filter. This nudges human developers running pytest directly
    to use marker filters for faster feedback loops. The PreToolUse hook
    only protects Claude Code sessions; this covers manual runs.
    """
    _LARGE_COLLECTION_THRESHOLD = 300
    if len(items) > _LARGE_COLLECTION_THRESHOLD:
        marker_expr = config.getoption("-m", default="")
        if not marker_expr:
            import warnings
            warnings.warn(
                f"Collected {len(items)} tests without a -m marker filter. "
                f"Consider using '-m \"not slow\"' or '-m \"not pbt\"' for faster runs.",
                stacklevel=1,
            )

    pbt_marker = pytest.mark.pbt
    slow_marker = pytest.mark.slow
    _SLOW_PATTERNS = {"stress", "e2e"}

    for item in items:
        if _runs_on_its_own_loop(item) and _SETTLE_FIXTURE not in item.fixturenames:
            # Appended, not inserted first: set up ahead of the session loop, it made
            # the next test's reset_database run on a closed loop (seen).
            item.fixturenames.append(_SETTLE_FIXTURE)

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
# An operator's SWARM_GATE_FORCE in the shell must not open gates under test.
os.environ.pop("SWARM_GATE_FORCE", None)
# Gate decisions recorded by tests must not land in the user's real audit log.
os.environ["SWARM_GATE_LOG"] = os.path.join(
    tempfile.gettempdir(), f"swarmai_test_gate_decisions_{os.getpid()}.jsonl"
)
os.close(_test_db_fd)
atexit.register(lambda: os.unlink(_test_db_path) if os.path.exists(_test_db_path) else None)

_test_db = SQLiteDatabase(db_path=_test_db_path)
database_module.db = _test_db
database_module._db_instance = _test_db
_schema_initialized = False

_TABLES_TO_CLEAR = [
    "channel_messages", "channel_sessions", "channels",
    "hive_instances", "hive_accounts",
    "chat_messages", "thread_summaries", "chat_threads",
    "todos", "tasks", "messages", "sessions",
    "workspace_mcps", "workspace_knowledgebases", "workspace_audit_log",
    "mcp_servers", "agents", "plugins", "marketplaces",
    "users", "app_settings", "workspace_config",
    "token_usage", "usage_ledger", "compactions",
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
def _restore_streaming_count():
    """Give back the daemon-wide streaming counter after every test.

    core.session_unit._streaming_count is bumped by every SessionUnit that enters
    STREAMING and only decremented when it leaves. Tests routinely build a unit in
    STREAMING and drop it, so the count leaked across tests within one worker and
    later tests saw streams that were not theirs (the count feeds streaming
    admission and the admission-state endpoint). Production units always
    transition out, so this is test isolation only.
    """
    mod = _sys.modules.get("core.session_unit")
    before = getattr(mod, "_streaming_count", None) if mod else None
    yield
    mod = _sys.modules.get("core.session_unit")
    if mod is not None:
        mod._streaming_count = before or 0


@pytest.fixture(autouse=True)
def _reset_recall_health_counters():
    """Start every test with empty recall / DDD degradation counters.

    core.session_router keeps them module-global for the daemon's health check.
    Any test that fault-injects recall (an exception, a timeout) bumps them, and a
    later test that asserts "deep check passed" then saw a degradation it never
    caused -- order-dependent red (seen: TestDeepCheck::test_passes_when_healthy
    after test_session_router_pure's exception test). Test isolation only.
    """
    mod = _sys.modules.get("core.session_router")
    if mod is not None:
        getattr(mod, "_recall_degraded_count", {}).clear()
        getattr(mod, "_ddd_inject_count", {}).clear()
    yield

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """Put the real ``database.db`` back into every app module that kept a mock of it.

    ``from database import db`` binds the object at import time. A module first
    imported while a test had ``patch("database.db")`` active keeps the MagicMock
    after the patch exits, for the rest of the worker: core.session_manager did after
    test_sse_disconnect_chain, and every later test that stored a session failed
    (test_steer_send: 21 SESSION_BUSY). Runs after the test's own fixtures are torn
    down, so a test's monkeypatch of a module's ``db`` is already undone; only a Mock
    left behind is replaced. Guarded by tests/test_conftest_frozen_db.py.
    """
    yield
    from unittest.mock import NonCallableMock
    real = getattr(database_module, "db", None)
    if real is None or isinstance(real, NonCallableMock):
        return
    for name, mod in list(_sys.modules.items()):
        if mod is None or not name.startswith(_APP_MODULE_PREFIXES):
            continue
        held = getattr(mod, "__dict__", {}).get("db")
        if held is not None and held is not real and isinstance(held, NonCallableMock):
            mod.db = real


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Put back the thread's event loop when a test cleared it.

    asyncio.run() (Runner.close) and helpers that call set_event_loop(None) leave
    the main thread with no current loop. A live loop that was current before the
    test (pytest-asyncio's session loop) is then lost for the rest of the worker,
    and every later loop_scope="session" test fails with "There is no current event
    loop in thread 'MainThread'" (test_chat_scenarios_e2e under xdist). Only that
    case is repaired: a loop that was not current before is never installed, since
    an unclosed stray loop keeps aiosqlite's worker thread alive at exit.

    A call-phase hook, not an autouse fixture: "before" must be read after every
    fixture is set up. A sync autouse fixture runs before the async session-loop
    fixture, so on a worker's FIRST test it saw no loop yet; when that test called
    asyncio.run, the session loop was lost for the whole worker (which worker got
    such a first test decided which run went red).

    A FIXTURE that calls asyncio.run() clears the loop during setup, before this
    hook reads "before" (test_backup_manager, test_brain_bootstrap_apply,
    test_cdf_v2_e2e's cdf_env did; 13 more fixtures do). So the hook first puts back
    the newest still-open loop pytest-asyncio itself installed (_PLUGIN_LOOPS): only
    a loop the plugin owns, never a stray one a test left open.
    """
    import asyncio as _asyncio

    local = getattr(_asyncio.get_event_loop_policy(), "_local", None)
    if local is not None and getattr(local, "_loop", None) is None \
            and _runs_on_a_shared_loop(item):
        live = next((loop for loop in reversed(_PLUGIN_LOOPS) if not loop.is_closed()), None)
        if live is not None:
            _asyncio.set_event_loop(live)
    before = getattr(local, "_loop", None)
    yield
    if before is not None and not before.is_closed() \
            and getattr(local, "_loop", None) is None:
        _asyncio.set_event_loop(before)


# Loops pytest-asyncio installed as the thread's current loop, newest last. Recorded by
# wrapping asyncio.set_event_loop and keeping only calls made from the plugin, so the
# repair above can never install a loop some test left open.
_PLUGIN_LOOPS: list = []


def _runs_on_a_shared_loop(item) -> bool:
    """An async test on a session/package/module/class loop: the only tests that need
    the plugin's long-lived loop to be current. A sync test or a function-loop test is
    never handed it."""
    import inspect
    fn = getattr(item, "function", None)
    if fn is None or not inspect.iscoroutinefunction(fn):
        return False
    return not _runs_on_its_own_loop(item)


def _record_plugin_loops() -> None:
    import asyncio as _asyncio
    import sys as _sys

    original = _asyncio.set_event_loop
    if getattr(original, "_swarm_records_plugin_loops", False):
        return

    def set_event_loop(loop):
        if loop is not None:
            caller = _sys._getframe(1).f_globals.get("__name__", "")
            if caller.startswith("pytest_asyncio"):
                _PLUGIN_LOOPS[:] = [l for l in _PLUGIN_LOOPS if not l.is_closed()]
                if loop not in _PLUGIN_LOOPS:
                    _PLUGIN_LOOPS.append(loop)
        return original(loop)

    set_event_loop._swarm_records_plugin_loops = True
    _asyncio.set_event_loop = set_event_loop


_record_plugin_loops()


@pytest.fixture(autouse=True)
async def reset_database():
    """Reset database tables before each test."""
    global _schema_initialized
    if not _schema_initialized:
        await _test_db.initialize()
        _schema_initialized = True

    # Close any pooled connections from the PRIOR test's event loop. The pool is
    # keyed by (db_path, loop_id); aiosqlite conns bind to their creating loop, and
    # pytest-asyncio gives each test a fresh loop — a stale pooled read conn would
    # hold a WAL snapshot that never sees this fixture's truncation. close_all_pools
    # drains them so the next borrow creates fresh conns on the current loop that
    # see the reset state. (Skeptic R6 — test-isolation for the module-level pool.)
    from database.sqlite import close_all_pools
    await close_all_pools()

    import aiosqlite
    _delete_script = ";\n".join(f"DELETE FROM {t}" for t in _TABLES_TO_CLEAR)
    async with aiosqlite.connect(str(_test_db.db_path)) as conn:
        # Match production _WALConnection (database/sqlite.py): a raw aiosqlite
        # connection defaults to busy_timeout=0, so this autouse fixture fails
        # immediately with "database is locked" whenever it races the WAL-mode
        # connections the app-under-test opens on the same (isolated tempfile)
        # DB — intra-process contention, surfacing as repeated setup errors that
        # time the suite out. busy_timeout=5000 waits the lock out, exactly as
        # every production connection already does. Inert under no contention.
        await conn.execute("PRAGMA busy_timeout=5000")
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

    # Teardown: drain THIS test's pools while its event loop is STILL ALIVE.
    # The persistent connection pool binds each aiosqlite conn (and its non-daemon
    # worker thread) to the loop that created it. pytest-asyncio gives each test a
    # fresh loop, so the setup close_all_pools() above can only ever see the PRIOR
    # test's pools — from the wrong loop, where `await conn.close()` cannot join the
    # parked worker. Undrained workers accumulate and hang threading._shutdown() at
    # interpreter exit (every DB test "passes" then hangs forever). Draining here,
    # on the current live loop, joins this test's workers correctly. Best-effort:
    # close_all_pools swallows per-pool errors (sqlite.py) so teardown never masks
    # a test failure; a hard-crashing test may skip this, which only reintroduces
    # the (pre-existing) leak for that one aborted test — acceptable.
    await close_all_pools()


@pytest.fixture(autouse=True)
def _isolate_app_data_dir(tmp_path_factory, monkeypatch):
    """Point config.get_app_data_dir() at a per-test temp dir for EVERY test (P7:
    structural guard, not per-test discipline). get_app_data_dir() defaults to the LIVE
    production ~/.swarm-ai — any test that constructs SQLiteDatabase(db_path=None), or
    any code that self-opens get_app_data_dir()/"data.db" (skills.py, skill_metrics_hook,
    todo_db), would otherwise create/read/write the PRODUCTION store, and sqlite3.connect
    create-if-missing means it silently materializes ~/.swarm-ai/data.db. Setting the
    SWARM_DATA_DIR escape hatch immunizes every CALLER at the one chokepoint (upstream
    mechanism class behind the 8-12 data.db loss; STEERING #20 / #1).

    ⚠️ PER-TEST, NOT SESSION-SCOPED — tried session scope to cut the projection cost
    and it silently broke DB isolation: an app-data dir that outlives one test keeps
    the `<sandbox>/data.db` some code path incidentally created, so a LATER test
    inherits that half-initialized store and dies on `no such table: channel_messages`.
    A fresh dir per test is what keeps that from being possible. (The cost that
    motivated session scope is gone anyway: excluding node_modules from the skill
    projection took a fresh-dir projection from ~630MB to ~15MB.)

    A test that DELIBERATELY needs the raw default (e.g. test_config_data_dir asserting
    the unset→home behavior) opts out with `monkeypatch.delenv("SWARM_DATA_DIR")` in its
    own body — that override is process-global and takes precedence over this setenv.

    THE ENV VAR ALONE IS NOT ENOUGH — it only reaches CALLERS of get_app_data_dir().
    Modules that froze a path into a module-level CONSTANT at IMPORT time
    (jobs.paths.APP_DATA_DIR + everything derived from it, security_hooks._AGENT_AUDIT_DIR,
    heartbeat.HEARTBEAT_PATH) resolved before any fixture could set the var, so they
    stayed pinned to the live ~/.swarm-ai and tests kept writing production state
    (observed: real ~/.swarm-ai/state/pipeline_agent_audit/*.marker). So we ALSO re-root
    any already-imported production Path constant onto the session root.

    Deliberately done HERE and not by making those constants lazy in production code:
    ~18 existing tests monkeypatch them AS module attributes
    (`monkeypatch.setattr(security_hooks, "_AGENT_AUDIT_DIR", ...)` ×11,
    `heartbeat.HEARTBEAT_PATH` ×3+, `paths.APP_DATA_DIR` ×4). Converting them to
    accessors would silently defeat every one of those patches — trading a contained
    test-side gap for a broad, quiet loss of isolation. A test's own function-scoped
    setattr still wins over ours (same monkeypatch instance, LIFO undo).

    The module list below must be COMPLETE for sibling modules that share a path:
    re-rooting security_hooks but not runtime_hooks left the two rooted in different
    test sandboxes and broke the reader==writer invariant test_adversarial_commit_gate
    defends. Re-rooting BOTH on every test keeps them equal."""
    d = tmp_path_factory.mktemp("app_data")
    monkeypatch.setenv("SWARM_DATA_DIR", str(d))
    _reroot_frozen_app_data_constants(
        d, monkeypatch, basetemp=tmp_path_factory.getbasetemp()
    )


@pytest.fixture(autouse=True)
def _no_live_bound_repos_for_landings(monkeypatch):
    """core.session_landings reads the bound repos of get_swarmws(), which in tests is
    the LIVE ~/.swarm-ai/SwarmWS unless SWARMWS is set: every turn card and Canvas read
    in a test would run git in the user's real repos. Pinned to none; a test that
    reads landings calls read_repo on its own repo or patches this itself."""
    try:
        from core import session_landings
    except Exception:  # noqa: BLE001 — module absent in an old checkout
        return
    monkeypatch.setattr(session_landings, "_bound_checkouts", lambda: [])


# Top-level packages swept for frozen app-data paths. A NAME ALLOWLIST was tried and
# abandoned: `from .paths import CONFIG_FILE` binds a SEPARATE frozen copy into every
# importing module, so the leaky set is not "modules that call get_app_data_dir()" but
# "every module that ever from-imported such a constant" — jobs.paths, jobs.scheduler,
# jobs.self_tune, core.security_hooks, core.runtime_hooks, core.heartbeat, … An
# allowlist silently missed each new one (each miss = a desync or a production write),
# so the sweep is prefix-scoped instead and made safe by the VALUE filter: it only ever
# rewrites a Path that already lives under the production app-data root or under a test
# sandbox — never an unrelated path.
_APP_MODULE_PREFIXES = (
    "jobs", "core", "routers", "hooks", "database", "channels",
    "middleware", "utils", "config", "main",
)


def _reroot_frozen_app_data_constants(sandbox, monkeypatch, *, basetemp=None) -> None:
    """Re-root already-imported app-data Path constants onto the CURRENT *sandbox*.

    Two distinct stale roots have to be caught, and missing either one is a real bug:

    1. THE PRODUCTION ROOT (~/.swarm-ai) — a module imported at collection time froze
       before any fixture could set SWARM_DATA_DIR, so it points at the live store.
       This is the pollution the guard exists to stop (observed: real
       ~/.swarm-ai/state/pipeline_agent_audit/*.marker written by test runs).
    2. AN EARLIER TEST'S SANDBOX — a module first imported DURING some test resolved
       correctly for THAT test and then stayed frozen there forever. With a per-test
       root that silently DESYNCHRONIZES sibling modules: security_hooks (patched and
       undone every test, so always current) vs runtime_hooks (frozen on the sandbox of
       whichever test imported it) ended up on different roots, breaking the
       reader==writer invariant test_adversarial_commit_gate defends. A filter that
       only recognized the production root skipped case 2 entirely, because such a path
       is no longer under ~/.swarm-ai.

    Generic by construction: EVERY Path attribute under either stale root is rewritten,
    so constants DERIVED from APP_DATA_DIR (jobs.paths.SWARMWS, DB_PATH, JOBS_DATA_DIR,
    STATE_FILE, LOG_DIR, …) are covered without enumerating names — patching only
    APP_DATA_DIR would leave every derivative behind.

    KNOWN RESIDUAL (not fixable from here): main._BACKEND_JSON_DEFAULT is baked into
    three `def f(path: str = _BACKEND_JSON_DEFAULT)` signatures, and a default argument
    is bound at def time — patching the module attribute cannot move it. All three
    accept an explicit `path`, so a test that cares passes one.
    """
    from pathlib import Path, PosixPath  # conftest binds `sys as _sys`, no Path

    sandbox = Path(sandbox)
    real = Path.home() / ".swarm-ai"
    # The source tree itself is never app data, even when it lives under ~/.swarm-ai
    # (a pipeline worktree is ~/.swarm-ai/worktrees/<run>/…): re-rooting it would hide
    # files the code ships with (e.g. channels/install_backend_daemon.TEMPLATE).
    source_root = Path(__file__).resolve().parents[2]

    def _stale_suffix(val):
        """Path parts BELOW whichever stale root *val* sits under, or None."""
        if val.is_relative_to(source_root):
            return None
        try:
            return val.relative_to(real).parts
        except ValueError:
            pass
        if basetemp is None:
            return None
        try:
            parts = val.relative_to(basetemp).parts
        except ValueError:
            return None  # neither production nor a test sandbox — leave it alone
        # …/<basetemp>/app_dataN/<suffix> → return <suffix>
        for i, part in enumerate(parts):
            if part.startswith("app_data"):
                return parts[i + 1:]
        return None

    for mod_name, mod in list(_sys.modules.items()):
        if mod is None or mod_name.split(".", 1)[0] not in _APP_MODULE_PREFIXES:
            continue
        try:
            members = list(vars(mod).items())
        except TypeError:  # exotic module object without a __dict__
            continue
        for attr, val in members:
            if type(val) is not PosixPath and not isinstance(val, Path):
                continue
            suffix = _stale_suffix(val)
            if suffix is None:
                continue
            monkeypatch.setattr(
                mod, attr, sandbox.joinpath(*suffix) if suffix else sandbox,
                raising=False,
            )


@pytest.fixture(autouse=True)
def _no_aws_account_startup(request, monkeypatch):
    """The app's startup resolves the SwarmAI AWS account (it finds the machine's real
    ada) and runs its capability checks against AWS: never in a test. The tests of the
    startup itself (test_aws_account.py, "startup" in the name) call it directly."""
    if "startup" in request.node.name and request.module.__name__.endswith("test_aws_account"):
        return
    try:
        from core import aws_account as _aa
    except Exception:  # noqa: BLE001
        return
    monkeypatch.setattr(_aa, "startup", lambda port: None)


@pytest.fixture(autouse=True)
def _fresh_hive_locks(monkeypatch):
    """The Hive fleet lock, the merge lock and the account connect lock are module-level
    ``asyncio.Lock`` objects. A lock is bound to the event loop of the first task that
    waits on it, and every async test runs on its own loop, so a test that contends one
    would break every later test that contends it ("bound to a different event loop").
    The daemon has one loop; each test gets fresh locks."""
    import asyncio as _asyncio
    try:
        from hive import accounts as _ha
        from routers import aws_account as _ra
    except Exception:  # noqa: BLE001 — a test run without the app modules
        return
    monkeypatch.setattr(_ha, "fleet_lock", _asyncio.Lock())
    monkeypatch.setattr(_ha, "_merge_lock", _asyncio.Lock())
    monkeypatch.setattr(_ra, "_connect_lock", _asyncio.Lock())


@pytest.fixture(autouse=True)
def _stub_credential_preflight(request, monkeypatch):
    """Force the credential validator's check() to return 'valid' for EVERY test
    (P7: structural CI-env guard, not per-test discipline). session_unit._ensure_spawned
    runs a live AWS STS credential pre-flight; on a machine/CI with NO resolvable creds
    that STS call raises NoCredentialsError → check() returns 'expired' → _ensure_spawned
    ABORTS before _spawn. So any test that drives _ensure_spawned without mocking the
    pre-flight passes locally (creds present) but FAILS in CI (no creds) — an
    environment-dependent landmine (test_proactive_restart AC2 was exactly this: CI-only
    spawned_options==0). Defaulting the shared singleton's check() to 'valid' immunizes
    all such tests at one chokepoint.

    OPT-OUT: a test that must exercise the REAL check() (e.g. verifying WHICH executor
    pool STS runs on) marks itself `@pytest.mark.real_credential_check` — then this stub
    is skipped and the real method is left intact. Auth-specific tests
    (test_credential_preflight.py) patch get_credential_validator themselves (local patch
    wins); test_credential_validator.py uses fresh CredentialValidator() instances
    (untouched by this singleton stub). P7 pattern: one structural chokepoint."""
    if request.node.get_closest_marker("real_credential_check"):
        return
    try:
        from unittest.mock import AsyncMock
        from core import session_registry
        validator = session_registry.get_credential_validator()
        monkeypatch.setattr(
            validator, "check", AsyncMock(return_value="valid"), raising=False
        )
    except Exception:  # noqa: BLE001 — never let the credential stub break a test
        pass


@pytest.fixture(autouse=True)
def _pin_memory_pressure(monkeypatch):
    """Host memory pressure is "unknown" for EVERY test (P7: structural guard).
    classify_failure reads it to tell an OOM SIGKILL from someone else's kill; the
    real value (this laptop runs at ~70-85%) would make "-9 is OOM" host-dependent.
    A test of that rule patches core.session_utils._memory_pressure itself."""
    import core.session_utils as _su
    monkeypatch.setattr(_su, "_memory_pressure", lambda: (None, None))


@pytest.fixture(autouse=True)
def _isolate_turn_changes(monkeypatch):
    """No code trees for EVERY test (P7: structural guard). SessionUnit.send()
    starts core/turn_changes, which discovers the developer's REAL bound repos and
    runs `git add -u` on them (loose objects in their .git). A test that wants trees
    passes them explicitly or re-patches code_roots (tests/test_turn_changes.py)."""
    try:
        import core.turn_changes as _tc
        monkeypatch.setattr(_tc, "code_roots", lambda: [], raising=False)
    except Exception:  # noqa: BLE001 — never let the isolation break a test
        pass


@pytest.fixture(autouse=True)
def _isolate_ai_docs_refresh(monkeypatch):
    """No test rewrites the repo's own docs (P7: structural guard). ContextHealthHook
    ._deep_check step 9 calls scripts.refresh_ai_docs.refresh(), which writes the REAL
    docs/CODEBASE_METRICS.md / AGENTS.md of the checkout under test. In a pipeline run
    that changed the worktree mid-verify, so the next continuation call saw a new cache
    key and restarted all its files (run_bd60daff, 2026-10-19). Tests of the refresh
    itself call its helpers, or the script in a subprocess, not refresh()."""
    try:
        import scripts.refresh_ai_docs as _rad
        monkeypatch.setattr(_rad, "refresh",
                            lambda *a, **k: {"files_updated": [], "warnings": []},
                            raising=False)
    except Exception:  # noqa: BLE001 — never let the isolation break a test
        pass


@pytest.fixture(autouse=True)
def _isolate_permission_queues(tmp_path_factory):
    """Give EVERY test a clean permission_manager that cannot touch the live store.

    - The prompt board and its reader futures are dropped around each test (a
      future binds to the loop that created it; tests each run their own loop).
    - The durable pending-request store is pointed at a per-test tmp file and
      emptied. The singleton's default is ~/.swarm-ai/pending_approvals.json:
      tests that drove a real gate wrote hundreds of fake requests ("/x",
      "run_1", "perm_midstream01") into the user's live file (found 2026-10-22).
    """
    try:
        from core.permission_manager import permission_manager as _pm
    except Exception:  # noqa: BLE001 — never let isolation break a test
        yield
        return
    saved = (_pm._persist_path, _pm._pending_requests)
    perm_dir = tmp_path_factory.mktemp("perm")
    _pm._persist_path = perm_dir / "pending_approvals.json"
    # A PermissionManager() built by a test resolves its default path lazily:
    # point that at the tmp dir too.
    import core.permission_manager as _pm_mod
    saved_default = _pm_mod._default_persist_path
    _pm_mod._default_persist_path = lambda: perm_dir / "pending_approvals.json"
    _pm._pending_requests = {}
    _pm._prompts.clear()
    _pm._board_waiters.clear()
    _pm._permission_events.clear()
    _pm._permission_results.clear()
    yield
    _pm._prompts.clear()
    _pm._board_waiters.clear()
    _pm._persist_path, _pm._pending_requests = saved
    _pm_mod._default_persist_path = saved_default


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


@pytest.fixture
def own_local_root():
    """``own(project, path)``: make *path* a confirmed local root of *project* — what a
    person's Confirm in Brain Hub records — without the git-top checks (the app-data
    dir is already per test, so the registry is too)."""
    def _own(project: str, path) -> None:
        import json
        import os
        from core.code_intel.local_roots import registry_path
        reg = registry_path()
        reg.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(reg.read_text()) if reg.exists() else {"version": 1, "projects": {}}
        rows = data["projects"].setdefault(project, [])
        real = os.path.realpath(str(path))
        if not any(r["path"] == real for r in rows):
            rows.append({"path": real, "state": "confirmed", "added_at": "t", "source": "test"})
        reg.write_text(json.dumps(data))
    return _own



@pytest.fixture(autouse=True)
def _hive_model_already_aligned(request, monkeypatch):
    """A dispatch first makes the Hive's default model this host's
    (core/swarm_out_model.align, tested in test_swarm_out_spend.py). The other
    dispatch tests' Hive mocks serve no /api/settings; there the Hive is taken as
    already on this host's model."""
    if request.module.__name__.endswith("test_swarm_out_spend"):
        return
    try:
        from core import swarm_out_model
    except Exception:  # noqa: BLE001 — a test run without the backend on the path
        return

    async def _aligned(client, hive_name, model=None):
        return model
    monkeypatch.setattr(swarm_out_model, "align", _aligned)
