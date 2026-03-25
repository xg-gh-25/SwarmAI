"""Root-level pytest configuration for the SwarmAI backend test suite.

Provides:
1. Hypothesis profiles (default=30 examples, ci=100) for PBT speed control
2. Resource leak detection (processes, FDs, memory) — periodic, not per-test
3. xdist-safe: no SIGALRM, no child-kill, no event-loop tampering
4. Per-test timeout (xdist-compatible, no pytest-timeout needed)

Auto-marking of PBT tests and tiered test selection live in tests/conftest.py.

Usage:
    make test          # fast: skip PBT+slow, 4 workers
    make test-all      # full suite, 4 workers
    make test-pbt      # PBT only
    make test-lf       # last failures
    make test-ci       # CI: 100 Hypothesis examples
"""

import gc
import os
import threading
import time
from typing import Optional

import psutil
import pytest
from hypothesis import HealthCheck, settings as hypothesis_settings

# NOTE: pytest_collection_modifyitems (auto-mark pbt/slow) lives in
# tests/conftest.py — not here. Keep one copy to avoid double-marking.


# ---------------------------------------------------------------------------
# Hypothesis profiles
# ---------------------------------------------------------------------------

hypothesis_settings.register_profile(
    "default",
    max_examples=30,
    deadline=5000,
    suppress_health_check=[HealthCheck.too_slow],
)

hypothesis_settings.register_profile(
    "ci",
    max_examples=100,
    deadline=5000,
    suppress_health_check=[HealthCheck.too_slow],
)

_profile = os.environ.get("HYPOTHESIS_PROFILE", "default")
hypothesis_settings.load_profile(_profile)


# ---------------------------------------------------------------------------
# Resource watchdog — runs every CHECK_INTERVAL tests, not every single test.
# Avoids 7000+ psutil syscalls per run while still catching leaks.
# ---------------------------------------------------------------------------

_CHECK_INTERVAL = 50          # check every N tests
_GC_RSS_THRESHOLD_MB = 1500   # trigger GC above this
_ABORT_RSS_THRESHOLD_MB = 3000  # abort run above this (likely leak)
_FD_LEAK_THRESHOLD = 50       # warn if FDs grew by this much since start


class ResourceWatchdog:
    """Periodic resource health check — process count, RSS, FD count.

    Designed for xdist safety: no signal handlers, no child killing,
    no event-loop manipulation. Just observe and warn/abort.
    """

    def __init__(self):
        self._test_count = 0
        self._initial_fds: Optional[int] = None
        self._initial_children: Optional[int] = None
        self._proc = psutil.Process(os.getpid())

    def _get_fd_count(self) -> int:
        """macOS-compatible FD count (no /proc on macOS)."""
        try:
            return self._proc.num_fds()
        except (psutil.Error, OSError):
            return -1

    def _get_child_count(self) -> int:
        try:
            return len(self._proc.children(recursive=False))
        except (psutil.Error, OSError):
            return 0

    def _get_rss_mb(self) -> float:
        try:
            return self._proc.memory_info().rss / (1024 * 1024)
        except (psutil.Error, OSError):
            return 0.0

    def snapshot_baseline(self):
        """Capture initial resource counts at session start."""
        self._initial_fds = self._get_fd_count()
        self._initial_children = self._get_child_count()

    def check(self, item_name: str = ""):
        """Run periodic health check. Called from pytest_runtest_teardown."""
        self._test_count += 1
        if self._test_count % _CHECK_INTERVAL != 0:
            return

        rss = self._get_rss_mb()
        fds = self._get_fd_count()
        children = self._get_child_count()

        # --- Memory ---
        if rss > _ABORT_RSS_THRESHOLD_MB:
            gc.collect(2)
            rss_after = self._get_rss_mb()
            if rss_after > _ABORT_RSS_THRESHOLD_MB:
                pytest.exit(
                    f"ABORT: RSS {rss_after:.0f}MB > {_ABORT_RSS_THRESHOLD_MB}MB "
                    f"after GC (likely leak). Last test: {item_name}",
                    returncode=99,
                )
        elif rss > _GC_RSS_THRESHOLD_MB:
            gc.collect(2)

        # --- FD leak ---
        if self._initial_fds and self._initial_fds > 0 and fds > 0:
            fd_delta = fds - self._initial_fds
            if fd_delta > _FD_LEAK_THRESHOLD:
                print(
                    f"\n[watchdog] FD drift: {self._initial_fds} -> {fds} "
                    f"(+{fd_delta}) after {self._test_count} tests"
                )

        # --- Child process drift ---
        if self._initial_children is not None:
            child_delta = children - self._initial_children
            if child_delta > 5:
                print(
                    f"\n[watchdog] Child process drift: "
                    f"{self._initial_children} -> {children} "
                    f"(+{child_delta}) after {self._test_count} tests"
                )


_watchdog = ResourceWatchdog()


def pytest_sessionstart(session):
    """Capture baseline resource counts before any test runs."""
    _watchdog.snapshot_baseline()


def pytest_runtest_teardown(item, nextitem):
    """Periodic resource check — every _CHECK_INTERVAL tests."""
    _watchdog.check(item.nodeid)


# ---------------------------------------------------------------------------
# Per-test timeout guard (xdist-safe, no SIGALRM, no interrupt_main)
# ---------------------------------------------------------------------------
# SIGALRM is process-global and breaks under xdist (workers are child procs).
# _thread.interrupt_main() raises KeyboardInterrupt which xdist interprets as
# a worker crash — killing the entire worker and all queued tests on it.
#
# Instead we use a cooperative approach: a timer sets a flag, and the teardown
# phase checks it. This can't stop a truly hung test mid-execution, but it
# catches tests that complete but took too long — which is what we care about
# for Hypothesis shrinking loops and slow I/O.
#
# For truly stuck tests, the watchdog's RSS abort is the backstop.

_DEFAULT_TIMEOUT = 60  # seconds (generous — catches runaways, not slow tests)


@pytest.fixture(autouse=True)
def _test_timeout(request):
    """Flag tests that exceed _DEFAULT_TIMEOUT seconds.

    Override per-test: @pytest.mark.timeout(120)

    NOTE: Measures wall-clock time including fixture setup/teardown. A fast
    test with slow fixture cleanup could get flagged. Acceptable with the
    generous 60s default — only revisit if fixture teardown becomes heavy.
    """
    # Allow per-test override via marker
    marker = request.node.get_closest_marker("timeout")
    timeout = marker.args[0] if marker and marker.args else _DEFAULT_TIMEOUT

    start = time.monotonic()

    yield

    elapsed = time.monotonic() - start
    if elapsed > timeout:
        pytest.fail(
            f"Test exceeded {timeout}s timeout (took {elapsed:.1f}s). "
            f"Mark with @pytest.mark.timeout(N) to increase."
        )
