"""Tests for the independent-thread liveness heartbeat (run_5b0d6ec3).

The heartbeat decouples "process alive" from the asyncio loop/GIL so a busy
backend can't be misread as dead. These tests assert:
  1. The writer thread ticks and writes a well-formed file (pid/ts/loop_age).
  2. loop_age reflects the loop-tick timestamp (small when bumped, large when not).
  3. The writer keeps ticking even when the MAIN THREAD is CPU-bound (the whole
     point — the thread must not be starved by loop-thread CPU work).
  4. start/stop are idempotent and stop removes the file.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from core import heartbeat


@pytest.fixture(autouse=True)
def _isolate_heartbeat(tmp_path, monkeypatch):
    """Point HEARTBEAT_PATH at a temp file + reset module state between tests."""
    hb_path = tmp_path / "heartbeat"
    monkeypatch.setattr(heartbeat, "HEARTBEAT_PATH", hb_path)
    # Fast cadence so tests are quick but still exercise real timing.
    monkeypatch.setattr(heartbeat, "TICK_INTERVAL_S", 0.05)
    # Reset shared state.
    heartbeat._last_loop_tick = 0.0
    heartbeat._writer_thread = None
    heartbeat._stop_event.clear()
    yield
    heartbeat.stop_heartbeat()


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_writer_writes_wellformed_file():
    heartbeat.bump_loop_tick()  # loop is "alive"
    heartbeat.start_heartbeat()
    time.sleep(0.2)  # let it tick a few times
    assert heartbeat.HEARTBEAT_PATH.exists()
    data = _read(heartbeat.HEARTBEAT_PATH)
    assert data["pid"] > 0
    assert isinstance(data["ts"], (int, float)) and data["ts"] > 0
    assert isinstance(data["loop_age"], (int, float))
    # loop was bumped just before start → loop_age is small.
    assert data["loop_age"] < 2.0


def test_loop_age_grows_without_bump():
    # Never bump → _last_loop_tick stays 0.0 → sentinel 999 loop_age.
    heartbeat.start_heartbeat()
    time.sleep(0.2)
    data = _read(heartbeat.HEARTBEAT_PATH)
    assert data["loop_age"] >= 900.0, "unbumped loop must report a large loop_age (wedged sentinel)"


def test_loop_age_small_after_recent_bump():
    heartbeat.start_heartbeat()
    time.sleep(0.15)
    heartbeat.bump_loop_tick()
    time.sleep(0.1)
    data = _read(heartbeat.HEARTBEAT_PATH)
    assert data["loop_age"] < 1.0, "a recent bump must yield a small loop_age (not wedged)"


def test_writer_ticks_despite_cpu_bound_main_thread():
    """THE decisive test: the writer thread must keep updating the file even
    while the main thread holds the CPU in a tight Python loop. sleep + write
    release the GIL, so the OS schedules the writer thread. If this fails, the
    heartbeat provides no protection under exactly the load it exists for."""
    heartbeat.bump_loop_tick()
    heartbeat.start_heartbeat()
    time.sleep(0.15)
    first = _read(heartbeat.HEARTBEAT_PATH)["ts"]

    # Burn CPU on the main thread for ~0.4s (no sleeps → holds GIL between
    # bytecode boundaries, but Python yields the GIL periodically).
    deadline = time.monotonic() + 0.4
    x = 0
    while time.monotonic() < deadline:
        x += 1  # busy work
    _ = x

    time.sleep(0.15)
    second = _read(heartbeat.HEARTBEAT_PATH)["ts"]
    assert second > first, "heartbeat ts must advance despite a CPU-bound main thread"


def test_start_is_idempotent():
    heartbeat.start_heartbeat()
    t1 = heartbeat._writer_thread
    heartbeat.start_heartbeat()  # second call — no-op
    t2 = heartbeat._writer_thread
    assert t1 is t2, "second start must not spawn a second thread"
    assert sum(1 for t in threading.enumerate() if t.name == "swarm-heartbeat") == 1


def test_stop_removes_file_and_joins():
    heartbeat.start_heartbeat()
    time.sleep(0.15)
    assert heartbeat.HEARTBEAT_PATH.exists()
    heartbeat.stop_heartbeat()
    # writer removes the file on exit + thread is joined.
    assert not heartbeat.HEARTBEAT_PATH.exists()
    assert heartbeat._writer_thread is None


def test_stop_start_cycle_no_duplicate_thread():
    """MED-3 regression: after stop→start, exactly one writer thread exists.
    A stop that nulled the handle on join-timeout would let start spawn a 2nd."""
    heartbeat.start_heartbeat()
    time.sleep(0.12)
    heartbeat.stop_heartbeat()
    assert heartbeat._writer_thread is None
    heartbeat.start_heartbeat()  # restart
    time.sleep(0.12)
    live = [t for t in threading.enumerate() if t.name == "swarm-heartbeat" and t.is_alive()]
    assert len(live) == 1, f"expected exactly 1 writer thread after stop→start, got {len(live)}"


def test_start_unlinks_stale_predecessor_file():
    """HIGH-2 regression: start removes a pre-existing (previous-process) file
    before spawning, so a dead predecessor's heartbeat can't be trusted."""
    # Simulate a stale file left by a crashed previous process.
    heartbeat.HEARTBEAT_PATH.write_text('{"pid": 99999, "ts": 1, "loop_age": 0.1}', encoding="utf-8")
    assert heartbeat.HEARTBEAT_PATH.exists()
    heartbeat.bump_loop_tick()
    heartbeat.start_heartbeat()
    time.sleep(0.12)
    # File now belongs to THIS process (pid matches os.getpid()).
    data = _read(heartbeat.HEARTBEAT_PATH)
    assert data["pid"] == __import__("os").getpid()


@pytest.mark.asyncio
async def test_loop_tick_loop_bumps():
    """The asyncio loop-tick task must bump the shared timestamp."""
    import asyncio

    heartbeat._last_loop_tick = 0.0
    task = asyncio.create_task(heartbeat.loop_tick_loop())
    try:
        await asyncio.sleep(0.15)
        assert heartbeat._last_loop_tick > 0.0, "loop_tick_loop must bump the timestamp"
        before = heartbeat._last_loop_tick
        await asyncio.sleep(0.1)
        assert heartbeat._last_loop_tick > before, "loop_tick_loop must keep bumping"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _reset_starvation_log_state():
    """Reset the loop_age-log rate-limit globals (the autouse fixture resets
    _last_loop_tick but not these). Keeps each starvation-log case independent."""
    heartbeat._last_loop_age_log = 0.0
    heartbeat._loop_age_was_over = False


def test_starvation_log_suppressed_for_startup_sentinel(caplog):
    """run_32f2cfe6 regression: the never-ticked startup sentinel (loop_age=999,
    _last_loop_tick==0) must NOT emit the 'event-loop starvation' WARNING — it is a
    transient every restart, not a real wedge. (The FILE still reports 999 — that is
    the Tauri watchdog's pre-yield semantics, asserted by test_loop_age_grows_without_bump.)"""
    _reset_starvation_log_state()
    heartbeat._last_loop_tick = 0.0  # never ticked → sentinel
    with caplog.at_level("WARNING", logger="core.heartbeat"):
        heartbeat._write_heartbeat_once()
    assert not any("event-loop starvation" in r.getMessage() for r in caplog.records), (
        "startup sentinel (last==0, loop_age=999) must NOT log starvation — "
        "reverting the `last > 0` guard makes this RED (the original false-positive)"
    )


def test_starvation_log_fires_for_real_stall(caplog):
    """A loop that HAS ticked (last>0) and then stalled >= threshold must still log —
    the fix must not silence genuine starvation."""
    _reset_starvation_log_state()
    heartbeat._last_loop_tick = time.monotonic() - (heartbeat._LOOP_AGE_LOG_THRESHOLD_S + 2.0)
    with caplog.at_level("WARNING", logger="core.heartbeat"):
        heartbeat._write_heartbeat_once()
    assert any("event-loop starvation" in r.getMessage() for r in caplog.records), (
        "a real ticked-then-stalled loop must log starvation"
    )


def test_startup_sentinel_does_not_swallow_first_real_stall(caplog):
    """The sentinel must not leave rising-edge state set — else the FIRST real
    post-startup stall would be mis-suppressed. Sequence: sentinel (silent) → real stall (logs)."""
    _reset_starvation_log_state()
    # 1. startup sentinel — silent, must reset _loop_age_was_over
    heartbeat._last_loop_tick = 0.0
    with caplog.at_level("WARNING", logger="core.heartbeat"):
        heartbeat._write_heartbeat_once()
        assert not any("starvation" in r.getMessage() for r in caplog.records)
        caplog.clear()
        # 2. now a real stall — rising edge must fire despite the preceding sentinel
        heartbeat._last_loop_tick = time.monotonic() - (heartbeat._LOOP_AGE_LOG_THRESHOLD_S + 2.0)
        heartbeat._write_heartbeat_once()
    assert any("event-loop starvation" in r.getMessage() for r in caplog.records), (
        "first real stall after a startup sentinel must still fire (rising-edge intact)"
    )
