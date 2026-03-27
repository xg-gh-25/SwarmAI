"""Test fixtures and configuration for backend tests.

Single authority for all test infrastructure:

0. **Venv guard** — Refuses to run if the wrong Python interpreter is used.
   System python is missing test deps (xdist, hypothesis, httpx).

1. **Concurrency guard** — File-based lock prevents multiple pytest runs.
   Concurrent runs from different tabs are the #1 cause of macOS crashes.

2. **Parallel execution** — Auto-injects pytest-xdist (-n N) when installed.
   Worker count is memory-adaptive: scales to 0 (serial) under pressure.

3. **Tiered test selection** — Three markers for selective running:
   - ``pbt`` — auto-applied to all Hypothesis property-based tests
   - ``slow`` — auto-applied to stress/e2e files and high-example PBT
   - ``integration`` — manually applied to tests needing external resources
   Run fast subset: ``pytest -m 'not pbt'`` (~1400 unit tests, <15s)
   Run PBT only:   ``pytest -m pbt`` (~470 tests)
   Run everything:  ``pytest`` (all ~2000 tests)

4. **Memory safety** — MemoryWatchdogPlugin checks RSS every test.
   GC at 512MB, abort at 1GB. System-level check every 5 tests.
   Also uses resource.getrusage() as fallback when psutil unavailable.

5. **Per-test timeout** — SIGALRM kills hanging tests at the OS level.
   Default 30s, override with @pytest.mark.timeout(N).
   Safe with xdist: each worker is its own process.

6. **Defaults in pyproject.toml** — addopts: --maxfail=3 --tb=short -q.

7. **DB isolation** — Temp SQLite DB created once per process, tables
   cleared between tests via a single executescript() call.
"""

# ---------------------------------------------------------------------------
# Venv guard — must fire BEFORE importing any venv-only packages
# ---------------------------------------------------------------------------
# System python (mise) doesn't have pytest-xdist, hypothesis, or httpx.
# Without this guard, tests silently run serial (no xdist) and skip PBT
# (no hypothesis). The error is invisible because conftest.py catches
# ImportError and falls back gracefully — which is exactly the wrong
# behavior when the real problem is "wrong python".

import sys as _sys
import os as _os

_VENV_DIR = _os.path.join(_os.path.dirname(__file__), "..", ".venv")
if _os.path.isdir(_VENV_DIR) and not _sys.prefix.startswith(
    _os.path.realpath(_VENV_DIR)
):
    print(
        f"\n{'=' * 60}\n"
        f"WRONG PYTHON: {_sys.executable}\n"
        f"  prefix: {_sys.prefix}\n"
        f"  expected venv: {_os.path.realpath(_VENV_DIR)}\n"
        f"{'=' * 60}\n"
        f"System python is missing test deps (xdist, hypothesis, httpx).\n"
        f"Fix:\n"
        f"  .venv/bin/python -m pytest          # explicit venv\n"
        f"  source .venv/bin/activate && pytest  # activate first\n"
        f"  make test                            # uses Makefile\n"
        f"{'=' * 60}",
        file=_sys.stderr,
    )
    _sys.exit(1)

import atexit
import fcntl
import gc
import logging
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

_logger = logging.getLogger("test")


# ---------------------------------------------------------------------------
# Concurrency guard — only one pytest run at a time
# ---------------------------------------------------------------------------
# Multiple Claude tabs launching pytest simultaneously is the #1 crash cause.
# Combined memory (4.6GB + 4.6GB) exceeds physical RAM → macOS jetsam kills
# everything. A file lock makes concurrent runs wait or fail fast.

# tempfile.gettempdir() resolves to /tmp/claude/ inside Claude sandbox.
# All Claude tabs share this sandbox tmpdir, so cross-tab locking works.
_LOCK_PATH = os.path.join(tempfile.gettempdir(), "swarmai_pytest.lock")
_lock_fd = None


def _is_pid_alive(pid_str: str) -> bool:
    """Check if a PID is still running."""
    try:
        pid = int(pid_str)
        os.kill(pid, 0)  # Signal 0 = existence check, no actual signal
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


def _acquire_pytest_lock():
    """Acquire exclusive file lock. Fails fast if another pytest is running.

    Handles stale locks: if the lock file exists but the PID is dead,
    the flock is already released by the OS — we just acquire normally.
    """
    global _lock_fd
    try:
        _lock_fd = open(_LOCK_PATH, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(f"{os.getpid()}\n")
        _lock_fd.flush()
    except (IOError, OSError):
        # Another pytest run holds the lock — check if it's actually alive
        other_pid = "unknown"
        try:
            with open(_LOCK_PATH) as f:
                other_pid = f.read().strip()
        except Exception:
            pass

        if other_pid != "unknown" and not _is_pid_alive(other_pid):
            # Stale lock — PID is dead, OS released flock but file remains.
            # Clean up and retry once.
            _logger.warning(f"Stale lock from dead PID {other_pid} — reclaiming")
            try:
                if _lock_fd:
                    _lock_fd.close()
                os.unlink(_LOCK_PATH)
                _lock_fd = open(_LOCK_PATH, "w")
                fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                _lock_fd.write(f"{os.getpid()}\n")
                _lock_fd.flush()
                return  # Successfully reclaimed
            except (IOError, OSError):
                pass  # Fall through to exit

        pytest.exit(
            f"\n{'='*60}\n"
            f"BLOCKED: Another pytest run is active (PID {other_pid})\n"
            f"{'='*60}\n"
            f"Concurrent test runs cause macOS memory crashes.\n"
            f"Wait for the other run to finish, or kill it:\n"
            f"  kill -9 {other_pid}\n"
            f"{'='*60}",
            returncode=1,
        )


def _release_pytest_lock():
    """Release file lock on exit."""
    global _lock_fd
    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
            os.unlink(_LOCK_PATH)
        except OSError:
            pass
        _lock_fd = None


atexit.register(_release_pytest_lock)


# ---------------------------------------------------------------------------
# Pre-flight: kill orphaned pytest/xdist processes from prior crashed runs
# ---------------------------------------------------------------------------
# When macOS jetsam kills a pytest parent, xdist workers become orphans
# (ppid=1) and keep consuming GB of RAM. Next run sees "enough memory"
# but the zombies are still eating it. Kill them before computing budget.

def _kill_orphaned_test_processes():
    """Kill orphaned pytest processes from prior crashed runs.

    Targets: processes with ppid=1 (orphaned) whose cmdline contains
    'pytest' or 'swarmai_test'. Skips our own process.
    """
    try:
        import psutil
        my_pid = os.getpid()
        killed = []
        for p in psutil.process_iter(["pid", "ppid", "cmdline", "memory_info"]):
            try:
                info = p.info
                if info["pid"] == my_pid:
                    continue
                if info["cmdline"] is None or info["memory_info"] is None:
                    continue
                cmd = " ".join(info["cmdline"])
                is_orphan = info["ppid"] == 1
                is_test_proc = "pytest" in cmd or "swarmai_test" in cmd
                if is_orphan and is_test_proc:
                    rss_mb = info["memory_info"].rss / 1024**2
                    p.kill()
                    killed.append((info["pid"], rss_mb))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if killed:
            total = sum(mb for _, mb in killed)
            pids = ", ".join(str(pid) for pid, _ in killed)
            _logger.warning(
                f"Killed {len(killed)} orphaned test process(es) "
                f"(pids: {pids}, freed ~{total:.0f}MB)"
            )
    except ImportError:
        pass


# Run immediately at import time — before worker count computation
_kill_orphaned_test_processes()


# ---------------------------------------------------------------------------
# System memory pre-flight — refuse to start if system is under pressure
# ---------------------------------------------------------------------------

def _check_system_memory_preflight():
    """Abort early if system memory is too low to safely run tests.

    This catches the case where the machine is already at 80%+ memory
    (e.g., Kiro + Chrome + Claude + Slack) before tests even start.
    """
    try:
        import psutil
        mem = psutil.virtual_memory()
        available_gb = mem.available / 1024**3

        if available_gb < 3.0:
            pytest.exit(
                f"\n{'='*60}\n"
                f"SYSTEM MEMORY TOO LOW TO RUN TESTS\n"
                f"{'='*60}\n"
                f"Available: {available_gb:.1f}GB (need >= 3.0GB)\n"
                f"Used: {mem.percent}%\n"
                f"Close some apps (browsers, Kiro, etc) and retry.\n"
                f"{'='*60}",
                returncode=1,
            )

        if available_gb < 5.0:
            _logger.warning(
                f"Low memory: {available_gb:.1f}GB available. "
                f"Tests will run serial with aggressive GC."
            )
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Memory-adaptive worker count — prevents macOS jetsam kills
# ---------------------------------------------------------------------------
# Each xdist worker costs ~150-300 MB (backend import + test DB + fixtures).
# On a 36GB machine running Kiro+Teams+Slack+Chrome+Zoom+Claude (~13GB
# baseline), workers can push total past physical RAM, triggering macOS
# memory pressure kills (jetsam / SIGKILL).
#
# Strategy: measure ACTUAL non-test memory usage (not a hardcoded constant),
# then budget remaining memory across workers conservatively.

_WORKER_MEMORY_BUDGET = int(1.0 * 1024**3)   # 1 GB per worker (headroom for spikes)
_MIN_AVAILABLE_FOR_PARALLEL = int(6.0 * 1024**3)  # need 6GB+ free to even try parallel
_MAX_WORKERS = 4  # hard cap — memory-adaptive calc still gates below this when tight


def _compute_safe_worker_count() -> int:
    """Compute xdist worker count based on available system memory.

    Returns 0 to disable xdist (run serial) when memory is tight.
    Uses actual available memory (not hardcoded reserves) to account for
    whatever apps are running (Kiro, Teams, Slack, Chrome, Claude, etc).
    """
    try:
        import psutil
        mem = psutil.virtual_memory()
        available = mem.available

        if available < _MIN_AVAILABLE_FOR_PARALLEL:
            _logger.warning(
                f"Only {available / 1024**3:.1f}GB available "
                f"(need {_MIN_AVAILABLE_FOR_PARALLEL / 1024**3:.1f}GB for parallel). "
                f"Running serial."
            )
            return 0

        # Reserve 4GB headroom for OS + memory pressure buffer, budget the rest
        headroom = int(4.0 * 1024**3)
        budget = max(0, available - headroom)
        max_by_memory = max(1, int(budget / _WORKER_MEMORY_BUDGET))
        max_by_cpu = min(os.cpu_count() or 2, _MAX_WORKERS)
        workers = min(max_by_memory, max_by_cpu)

        _logger.info(
            f"xdist: {available / 1024**3:.1f}GB available → "
            f"{workers} workers (budget {max_by_memory}, cpu-cap {max_by_cpu})"
        )
        return workers
    except ImportError:
        # No psutil — run serial (safe default)
        return 0


# ---------------------------------------------------------------------------
# Background memory watchdog thread
# ---------------------------------------------------------------------------
# Pytest hooks only fire between tests. A single test or module import that
# allocates 5GB will blow past the hook-based watchdog. This thread samples
# RSS every 2 seconds and hard-kills the process via os._exit() (uncatchable)
# if the limit is exceeded. Belt AND suspenders.

_THREAD_RSS_LIMIT = int(3 * 1024**3)    # 3 GB — catastrophic-only (hooks catch at 1 GB)
_THREAD_SYSTEM_FLOOR = int(3 * 1024**3)  # 3 GB system available


def _start_memory_watchdog_thread():
    """Start a daemon thread that monitors RSS every 2 seconds."""
    import threading

    def _watchdog():
        while True:
            import time
            time.sleep(2)
            try:
                rss = _get_rss()
                if rss > _THREAD_RSS_LIMIT:
                    _aggressive_gc()
                    rss_after = _get_rss()
                    if rss_after > _THREAD_RSS_LIMIT:
                        import sys
                        msg = (
                            f"\n{'='*60}\n"
                            f"WATCHDOG THREAD: HARD KILL\n"
                            f"{'='*60}\n"
                            f"RSS: {rss_after / 1024**2:.0f}MB exceeds "
                            f"{_THREAD_RSS_LIMIT / 1024**2:.0f}MB limit.\n"
                            f"This is a safety kill to prevent macOS crash.\n"
                            f"{'='*60}\n"
                        )
                        sys.stderr.write(msg)
                        sys.stderr.flush()
                        os._exit(137)

                # Also check system-level memory
                sys_avail = _get_system_available()
                if sys_avail > 0 and sys_avail < _THREAD_SYSTEM_FLOOR:
                    _aggressive_gc()
                    sys_avail2 = _get_system_available()
                    if sys_avail2 > 0 and sys_avail2 < _THREAD_SYSTEM_FLOOR:
                        import sys
                        msg = (
                            f"\n{'='*60}\n"
                            f"WATCHDOG THREAD: SYSTEM MEMORY LOW\n"
                            f"{'='*60}\n"
                            f"System available: {sys_avail2 / 1024**3:.1f}GB "
                            f"(< {_THREAD_SYSTEM_FLOOR / 1024**3:.0f}GB)\n"
                            f"Hard-killing to prevent macOS jetsam.\n"
                            f"{'='*60}\n"
                        )
                        sys.stderr.write(msg)
                        sys.stderr.flush()
                        os._exit(137)
            except Exception:
                pass  # Never let the watchdog thread crash

    t = threading.Thread(target=_watchdog, daemon=True, name="pytest-memory-watchdog")
    t.start()


# ---------------------------------------------------------------------------
# pytest_configure — markers, plugins, xdist auto-inject
# ---------------------------------------------------------------------------

def pytest_configure(config):
    """Register markers, memory safety plugins, and auto-inject xdist."""
    # Detect xdist worker — workers should skip master-only setup.
    # xdist sets workerinput attribute on the config of each worker.
    is_xdist_worker = hasattr(config, "workerinput")

    # Concurrency guard: only one pytest run at a time (master only).
    # Workers inherit the lock from the master process — don't re-acquire.
    if not is_xdist_worker:
        _acquire_pytest_lock()

    # System memory pre-flight: abort if < 3GB available (master only)
    if not is_xdist_worker:
        _check_system_memory_preflight()

    # Background memory watchdog thread — last line of defense.
    # Pytest hooks only fire between tests. If memory grows during test
    # collection, module imports, or a single long test, hooks can't help.
    # This thread checks RSS every 2 seconds and calls os._exit() (which
    # is NOT catchable by try/except) if the limit is exceeded.
    # Each worker gets its own watchdog (safe — each is a separate process).
    _start_memory_watchdog_thread()

    # Set process group so all child processes (xdist workers) die with parent.
    # Without this, macOS jetsam killing the parent leaves workers as orphans.
    # ONLY on master — workers must NOT call setpgrp() or they detach from
    # the master's process group, causing "Not properly terminated" crashes.
    if not is_xdist_worker:
        try:
            os.setpgrp()
        except OSError:
            pass  # Already a process group leader

    # Register custom markers
    config.addinivalue_line("markers", "pbt: property-based tests using Hypothesis")
    config.addinivalue_line("markers", "slow: marks tests as slow-running")
    config.addinivalue_line("markers", "integration: tests requiring external resources")

    # Register memory watchdog
    config.pluginmanager.register(MemoryWatchdogPlugin(), "memory_watchdog")

    # Register SIGALRM timeout (Unix only)
    # Each xdist worker is its own process → SIGALRM is per-worker safe.
    if hasattr(signal, "SIGALRM"):
        config.pluginmanager.register(SigalrmTimeoutPlugin(), "sigalrm_timeout")

    # --maxfail is now in pyproject.toml addopts (default=3).
    # No runtime injection needed — pytest handles it natively.

    # Enforce memory-adaptive worker cap — even when -n is passed explicitly.
    # The SwarmAI agent often generates `pytest -n auto` which uses ALL CPU
    # cores and crashes macOS via jetsam. We override any -n value that
    # exceeds the safe budget. This makes the wrong thing impossible.
    try:
        import xdist  # noqa: F401
        if not hasattr(config.option, "numprocesses"):
            return

        safe_count = _compute_safe_worker_count()
        user_specified_n = any(
            arg.startswith("-n") or arg == "--numprocesses"
            for arg in config.invocation_params.args
        )

        if user_specified_n:
            # User (or agent) passed -n explicitly.
            # "auto" resolves to os.cpu_count() by xdist — could be 8-16.
            # Any value > safe_count gets clamped.
            requested = config.option.numprocesses
            # xdist stores "auto" as the string "auto" or the cpu count int
            if requested == "auto" or (
                isinstance(requested, int) and requested > safe_count
            ):
                _logger.warning(
                    f"Overriding -n {requested} → {safe_count} "
                    f"(memory-adaptive cap, _MAX_WORKERS={_MAX_WORKERS})"
                )
                config.option.numprocesses = safe_count
        else:
            # No -n flag — inject our safe count.
            config.option.numprocesses = safe_count

        if config.option.numprocesses and config.option.numprocesses > 0:
            config.option.dist = "loadgroup"
        else:
            _logger.warning(
                "xdist disabled — insufficient available memory. "
                "Running serially."
            )
    except (ImportError, AttributeError):
        pass


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
        mod_name = getattr(module, "__name__", "")
        if any(p in mod_name for p in _SLOW_PATTERNS):
            item.add_marker(slow_marker)
            continue

        # PBT test with high max_examples (>= 100) — shouldn't exist if
        # files use helpers.PROPERTY_SETTINGS, but catch stragglers.
        if module._has_hypothesis:
            test_func = getattr(item, "obj", None)
            if test_func is not None:
                hyp_settings = getattr(test_func, "_hypothesis_internal_use_settings", None)
                if hyp_settings is not None and hyp_settings.max_examples >= 100:
                    item.add_marker(slow_marker)


# ---------------------------------------------------------------------------
# Per-test timeout — SIGALRM kills hanging tests at the OS level
# ---------------------------------------------------------------------------

_DEFAULT_TEST_TIMEOUT = 30


class _TestTimeoutError(Exception):
    """Raised by SIGALRM when a test exceeds its timeout."""
    pass


class SigalrmTimeoutPlugin:
    """Per-test timeout via SIGALRM.

    Why SIGALRM instead of cooperative timeouts:
    - Cooperative timeouts (time.monotonic() in teardown) NEVER fire if
      the test itself hangs — teardown only runs after the test returns.
    - SIGALRM fires regardless (blocked I/O, deadlock, infinite loop).
    - Each xdist worker is its own process → per-worker safe.
    """

    def __init__(self):
        self._previous_handler = None

    def pytest_runtest_setup(self, item):
        """Set SIGALRM before each test."""
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

_GC_THRESHOLD = int(512 * 1024**2)     # 512 MB — trigger aggressive GC
_ABORT_THRESHOLD = int(2 * 1024**3)    # 2 GB — abort before system crash
_SYSTEM_AVAIL_FLOOR = int(3 * 1024**3)  # 3 GB — minimum system available
_PROACTIVE_GC_INTERVAL = 25            # Force GC every 25 tests regardless of pressure

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


def _get_rss() -> int:
    """Get current process RSS in bytes.

    Primary: psutil (accurate). Fallback: resource.getrusage (always available
    on Unix, returns maxrss in bytes on macOS, KB on Linux).
    """
    if _PSUTIL_AVAILABLE:
        try:
            return psutil.Process().memory_info().rss
        except Exception:
            pass
    # Fallback: resource module (always available on Unix)
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF)
        maxrss = ru.ru_maxrss
        # macOS returns bytes, Linux returns KB
        import sys
        if sys.platform == "darwin":
            return maxrss
        return maxrss * 1024
    except Exception:
        return 0


def _get_system_available() -> int:
    """Get system available memory in bytes. Returns 0 if unavailable."""
    if _PSUTIL_AVAILABLE:
        try:
            return psutil.virtual_memory().available
        except Exception:
            pass
    return 0


def _aggressive_gc() -> int:
    """Force full GC cycle and return bytes freed (estimated)."""
    before = _get_rss()
    gc.collect(0)
    gc.collect(1)
    gc.collect(2)
    after = _get_rss()
    return max(0, before - after)


class MemoryWatchdogPlugin:
    """Pytest plugin that monitors RSS and prevents memory explosions.

    Two check points per test:
    - pytest_runtest_setup: checks BEFORE each test (catches cumulative bloat)
    - pytest_runtest_teardown: checks AFTER each test (catches per-test spikes)

    System-level check every 5 tests catches aggregate pressure from all
    processes (not just this one).
    """

    _DEEP_CHECK_INTERVAL = 5  # System check every 5 tests (was 10)

    def __init__(self):
        self._test_count = 0
        self._peak_rss = 0
        self._gc_trigger_count = 0
        self._last_warning_at = 0

    def _check_and_abort(self, context: str):
        """Core memory check. Called from both setup and teardown hooks."""
        rss = _get_rss()
        if rss == 0:
            return

        if rss > self._peak_rss:
            self._peak_rss = rss

        # Hard abort: per-process RSS exceeded
        if rss > _ABORT_THRESHOLD:
            _aggressive_gc()
            rss_after = _get_rss()
            if rss_after > _ABORT_THRESHOLD:
                pytest.exit(
                    f"\n{'='*60}\n"
                    f"MEMORY SAFETY ABORT ({context})\n"
                    f"{'='*60}\n"
                    f"RSS: {rss_after / 1024**2:.0f}MB exceeds "
                    f"{_ABORT_THRESHOLD / 1024**2:.0f}MB limit "
                    f"after {self._test_count} tests.\n"
                    f"Peak RSS: {self._peak_rss / 1024**2:.0f}MB\n"
                    f"GC triggers: {self._gc_trigger_count}\n"
                    f"{'='*60}",
                    returncode=137,
                )
            self._gc_trigger_count += 1

        elif rss > _GC_THRESHOLD:
            self._gc_trigger_count += 1
            freed = _aggressive_gc()
            if self._test_count - self._last_warning_at > 25:
                self._last_warning_at = self._test_count
                _logger.warning(
                    f"Memory pressure ({context}): RSS {rss / 1024**2:.0f}MB "
                    f"after {self._test_count} tests. GC freed {freed / 1024**2:.0f}MB."
                )

    def _check_system_memory(self):
        """System-level memory check — catches aggregate pressure from all workers."""
        sys_avail = _get_system_available()
        if sys_avail == 0:
            return
        if sys_avail < _SYSTEM_AVAIL_FLOOR:
            _aggressive_gc()
            sys_avail_after = _get_system_available()
            if sys_avail_after < _SYSTEM_AVAIL_FLOOR:
                rss = _get_rss()
                pytest.exit(
                    f"\n{'='*60}\n"
                    f"SYSTEM MEMORY SAFETY ABORT\n"
                    f"{'='*60}\n"
                    f"System available: {sys_avail_after / 1024**3:.1f}GB "
                    f"(< {_SYSTEM_AVAIL_FLOOR / 1024**3:.0f}GB floor)\n"
                    f"Worker RSS: {rss / 1024**2:.0f}MB after {self._test_count} tests\n"
                    f"Aborting to prevent macOS jetsam kill.\n"
                    f"{'='*60}",
                    returncode=137,
                )

    def pytest_runtest_setup(self, item):
        """Quick RSS check BEFORE each test — catches cumulative bloat early."""
        self._check_and_abort("pre-test")

    def pytest_runtest_teardown(self, item):
        """Post-test check: per-process RSS + periodic system-level check."""
        self._test_count += 1
        self._check_and_abort("post-test")

        # Proactive GC every N tests — prevents cumulative leak across modules.
        # TestClient/AsyncClient + module imports accumulate; periodic GC
        # frees unreferenced objects before they compound.
        if self._test_count % _PROACTIVE_GC_INTERVAL == 0:
            _aggressive_gc()

        # Deep system-level check every N tests (psutil.virtual_memory is ~1ms)
        if self._test_count % self._DEEP_CHECK_INTERVAL == 0:
            self._check_system_memory()

    def pytest_terminal_summary(self, terminalreporter):
        """Report peak memory usage in the test summary."""
        if self._peak_rss > 0:
            msg = (
                f"Peak RSS: {self._peak_rss / 1024**2:.0f} MB "
                f"({self._test_count} tests, "
                f"{self._gc_trigger_count} GC triggers)"
            )
            terminalreporter.write_line(
                msg,
                yellow=self._peak_rss > _GC_THRESHOLD,
                red=self._peak_rss > _ABORT_THRESHOLD,
            )


# ---------------------------------------------------------------------------
# Test database setup — once per process, cleared between tests
# ---------------------------------------------------------------------------

# Create a temp file for the test database (once per process).
_test_db_fd, _test_db_path = tempfile.mkstemp(suffix=".db", prefix="swarmai_test_")
os.close(_test_db_fd)

# Safety net: atexit cleanup ensures temp DB is removed even on crash.
atexit.register(lambda: os.unlink(_test_db_path) if os.path.exists(_test_db_path) else None)

# Replace the global db singleton with one pointing at the temp file.
_test_db = SQLiteDatabase(db_path=_test_db_path)
database_module.db = _test_db
database_module._db_instance = _test_db

# Track whether schema has been initialized (session-scoped, not per-test).
_schema_initialized = False


# Tables cleared between tests (order doesn't matter for DELETE).
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

    Schema initialization is session-scoped (runs once). Per-test work
    is just: DELETE all rows + seed default agent. No filesystem walks.
    """
    global _schema_initialized

    # Schema DDL: once per process, not per test.
    if not _schema_initialized:
        await _test_db.initialize()
        _schema_initialized = True

    # Clear all tables in a single batch (one round-trip instead of 20)
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
        "is_default": True,
        "is_system_agent": True,
        "created_at": now,
        "updated_at": now,
    })

    yield

    # NOTE: No filesystem cleanup here. Tests that create files in
    # ~/.swarm-ai/ should use tmp_path or mock the path. Walking
    # production directories 2000 times per test run is wasteful.


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
