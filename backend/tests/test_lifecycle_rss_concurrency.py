"""RSS-sampling concurrency + job RAM-gate tests (run_409392d4).

Root cause fixed: the maintenance loop sampled per-session process-tree RSS
(process_tree_rss ~= 107ms each, psutil tree walk) in a SERIAL await for-loop,
so the loop coroutine was suspended for SUM(N x 107ms), delaying all SSE reader
tasks on the same event loop -> simultaneous 90s SSE stalls across all tabs.

Fix: asyncio.gather the per-unit RSS reads (loop suspends ~1x call, not Nx) on a
dedicated _rss_executor (so the gather burst can't starve force_kill on the shared
pool). Plus a job RAM admission gate so scheduled agent_task CLIs defer under
memory pressure instead of racing chat spawns.

Methodology: mock process_tree_rss with a fixed sleep; assert N-unit sampling
wall-time is ~1x the sleep (concurrent), NOT Nx (serial). This test FAILS on the
old serial implementation (that is the RED proof) and passes on the gather fix.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.lifecycle_manager import LifecycleManager
from core.session_unit import SessionState


_RSS_CALL_SECONDS = 0.1  # simulate the measured ~107ms psutil tree walk


def _make_unit(sid: str, pid: int, state=SessionState.IDLE, peak: int = 0):
    """Minimal duck-typed SessionUnit for maintenance-loop sampling."""
    u = SimpleNamespace()
    u.session_id = sid
    u.pid = pid
    u.state = state
    u.is_alive = True
    u._peak_tree_rss_bytes = peak
    u._last_proactive_restart = 0.0
    u._hooks_enqueued = True  # skip hook enqueue path in tests
    return u


def _slow_tree_rss(pid: int) -> int:
    """Stand-in for resource_monitor.process_tree_rss — blocks _RSS_CALL_SECONDS."""
    time.sleep(_RSS_CALL_SECONDS)
    return 500 * 1024 * 1024  # 500MB, below all thresholds (no kill/restart)


@pytest.mark.asyncio
async def test_sample_process_memory_is_concurrent_not_serial():
    """N units must be sampled CONCURRENTLY: wall-time ~= 1x call, not Nx.

    RED on the old serial `for unit: await run_in_executor(...)` loop
    (3 units x 100ms = ~300ms). GREEN on the asyncio.gather fix (~100ms).
    """
    n = 4
    units = [_make_unit(f"sess{i:02d}", 1000 + i) for i in range(n)]
    router = MagicMock()
    router.list_units.return_value = units
    lm = LifecycleManager(router=router)

    with patch("core.resource_monitor.resource_monitor.process_tree_rss",
               side_effect=_slow_tree_rss), \
         patch("core.resource_monitor.resource_monitor.process_rss",
               return_value=400 * 1024 * 1024):
        t0 = time.perf_counter()
        await lm._sample_process_memory()
        elapsed = time.perf_counter() - t0

    serial_floor = n * _RSS_CALL_SECONDS  # 0.4s
    # Concurrent must be well under the serial sum. Generous ceiling (2x single
    # call) absorbs scheduling jitter while still failing the ~0.4s serial path.
    assert elapsed < serial_floor * 0.75, (
        f"RSS sampling took {elapsed:.3f}s for {n} units "
        f"(serial floor {serial_floor:.3f}s) — not concurrent"
    )


@pytest.mark.asyncio
async def test_sample_process_memory_still_records_all_units():
    """Concurrency must not drop units: every alive unit's peak is updated."""
    units = [_make_unit(f"s{i}", 2000 + i, peak=0) for i in range(3)]
    router = MagicMock()
    router.list_units.return_value = units
    lm = LifecycleManager(router=router)

    with patch("core.resource_monitor.resource_monitor.process_tree_rss",
               side_effect=_slow_tree_rss), \
         patch("core.resource_monitor.resource_monitor.process_rss",
               return_value=400 * 1024 * 1024):
        await lm._sample_process_memory()

    # Every unit got its peak watermark set from the gathered RSS read.
    for u in units:
        assert u._peak_tree_rss_bytes == 500 * 1024 * 1024, (
            f"{u.session_id} peak not updated — result mis-mapped or dropped"
        )


@pytest.mark.asyncio
async def test_sample_tolerates_one_unit_raising():
    """return_exceptions=True: one unit's RSS read raising must not abort the rest."""
    units = [_make_unit(f"e{i}", 3000 + i, peak=0) for i in range(3)]
    router = MagicMock()
    router.list_units.return_value = units
    lm = LifecycleManager(router=router)

    def _rss_one_raises(pid: int) -> int:
        if pid == 3001:  # middle unit blows up
            raise RuntimeError("psutil exploded")
        time.sleep(0.01)
        return 500 * 1024 * 1024

    with patch("core.resource_monitor.resource_monitor.process_tree_rss",
               side_effect=_rss_one_raises), \
         patch("core.resource_monitor.resource_monitor.process_rss",
               return_value=400 * 1024 * 1024):
        # Must not raise — the two healthy units still get sampled.
        await lm._sample_process_memory()

    assert units[0]._peak_tree_rss_bytes == 500 * 1024 * 1024
    assert units[2]._peak_tree_rss_bytes == 500 * 1024 * 1024
    # The raising unit is simply skipped (peak stays 0).
    assert units[1]._peak_tree_rss_bytes == 0
