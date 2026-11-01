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
import sys
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


# ── Starvation stack dump: the log names the call that blocks the loop ──

def _blocking_call_on_loop_thread(ready: threading.Event, release: threading.Event):
    """Stands in for the unknown sync call that starves the asyncio loop."""
    heartbeat.bump_loop_tick()  # runs "on the loop": records its thread id
    ready.set()
    release.wait(5)


def test_starvation_log_includes_the_blocked_loop_thread_stack(caplog):
    """The warning carries the loop thread's stack, so the offending function is
    named. Before this, 177 starvations (max 16.7s) had no location at all."""
    _reset_starvation_log_state()
    ready, release = threading.Event(), threading.Event()
    t = threading.Thread(target=_blocking_call_on_loop_thread, args=(ready, release),
                         name="fake-loop", daemon=True)
    t.start()
    assert ready.wait(2)
    try:
        heartbeat._last_loop_tick = time.monotonic() - (heartbeat._LOOP_AGE_LOG_THRESHOLD_S + 1.0)
        with caplog.at_level("WARNING", logger="core.heartbeat"):
            heartbeat._write_heartbeat_once()
    finally:
        release.set()
        t.join(2)
    msgs = [r.getMessage() for r in caplog.records if "event-loop starvation" in r.getMessage()]
    assert msgs, "a real stall must log"
    assert "loop thread (fake-loop)" in msgs[0]
    assert "_blocking_call_on_loop_thread" in msgs[0]


def test_stack_dump_lists_busy_threads_and_skips_idle_ones():
    """A GIL-holding worker starves the loop too: a non-idle thread is listed,
    a thread parked in Event.wait is not."""
    stop = threading.Event()

    def spin_cpu():
        while not stop.is_set():
            sum(range(1000))

    busy = threading.Thread(target=spin_cpu, name="busy-worker", daemon=True)
    idle = threading.Thread(target=stop.wait, name="idle-worker", daemon=True)
    busy.start(); idle.start()
    try:
        time.sleep(0.05)
        heartbeat._loop_thread_id = None
        out = heartbeat._format_starvation_stacks()
    finally:
        stop.set()
        busy.join(2); idle.join(2)
    assert "busy thread busy-worker" in out
    assert "idle-worker" not in out
    assert "loop thread: not found" in out


def test_stack_dump_skips_threads_blocked_in_c_level_waits():
    """aiosqlite's worker parks in ``SimpleQueue.get()``, a C call with no
    Python frame of its own: its caller is the innermost frame. The 10-21 log
    listed 788 such idle workers as busy. Same for ``sock.recv`` and a bare
    ``sleep``. A CPU loop that keeps calling ``dict.get`` must stay listed."""
    import queue
    import socket

    stop = threading.Event()
    a, b = socket.socketpair()
    sq: "queue.SimpleQueue[int]" = queue.SimpleQueue()

    def sq_waiter():
        sq.get()

    def recv_waiter():
        try:
            a.recv(10)
        except OSError:
            pass

    def sleeper():
        while not stop.is_set():
            time.sleep(0.05)

    def hot_dict_get():
        d = {1: 2}
        while not stop.is_set():
            d.get(1)

    ts = [threading.Thread(target=f, name=f.__name__, daemon=True)
          for f in (sq_waiter, recv_waiter, sleeper, hot_dict_get)]
    for t in ts:
        t.start()
    try:
        time.sleep(0.1)
        heartbeat._loop_thread_id = None
        out = heartbeat._format_starvation_stacks()
    finally:
        stop.set(); sq.put(0); b.send(b"x")
        for t in ts:
            t.join(2)
        a.close(); b.close()
    assert "sq_waiter" not in out
    assert "recv_waiter" not in out
    assert "sleeper" not in out
    assert "busy thread hot_dict_get" in out


def test_pending_c_wait_needs_a_blocking_receiver_not_just_a_name():
    """Read from bytecode (the frozen build ships no source): a frame parked in
    ``SimpleQueue.get()`` is a C wait; a frame calling ``dict.get`` is not,
    though the method name is the same."""
    import queue
    sq: "queue.SimpleQueue[int]" = queue.SimpleQueue()
    seen = {}

    def waiter():
        seen["frame"] = sys._getframe()
        sq.get()

    t = threading.Thread(target=waiter, daemon=True)
    t.start()
    try:
        time.sleep(0.05)
        assert heartbeat._pending_c_wait(seen["frame"]) is True
    finally:
        sq.put(0); t.join(2)

    captured = {}

    class Probe(dict):
        def get(self, k, default=None):  # Python-level stand-in for dict.get
            captured["caller"] = sys._getframe(1)
            return super().get(k, default)

    def hot(d):
        return d.get(1)

    hot(Probe())
    # The caller frame is suspended on CALL get with a dict receiver.
    assert heartbeat._pending_c_wait(captured["caller"]) is False


# ── Battery status in the starvation log (10-21 10:47 freeze was a 3% battery) ──

_PMSET_LOW = (
    "Now drawing from 'Battery Power'\n"
    " -InternalBattery-0 (id=25034851)\t3%; discharging; 0:04 remaining present: true\n"
)
_PMSET_AC = (
    "Now drawing from 'AC Power'\n"
    " -InternalBattery-0 (id=25034851)\t98%; charging; 0:16 remaining present: true\n"
)


def test_parse_pmset_batt_low_battery_and_ac():
    assert heartbeat._parse_pmset_batt(_PMSET_LOW) == "Battery Power, 3%, discharging"
    assert heartbeat._parse_pmset_batt(_PMSET_AC) == "AC Power, 98%, charging"
    assert heartbeat._parse_pmset_batt("Now drawing from 'AC Power'\n") == "AC Power, no battery"


def test_battery_status_never_raises_and_reports_slow_probe(monkeypatch):
    import subprocess
    monkeypatch.setattr(heartbeat.sys, "platform", "darwin")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pmset", timeout=2)

    def broken(*a, **k):
        raise OSError("no pmset")

    assert "pmset took over" in heartbeat._battery_status(run=slow)
    assert heartbeat._battery_status(run=broken) == "power: unknown (OSError)"


def test_starvation_log_carries_battery_status(caplog, monkeypatch):
    """The warning line itself names the power source and battery level."""
    _reset_starvation_log_state()
    monkeypatch.setattr(heartbeat, "_battery_status",
                        lambda: "power: Battery Power, 3%, discharging")
    heartbeat._last_loop_tick = time.monotonic() - (heartbeat._LOOP_AGE_LOG_THRESHOLD_S + 2.0)
    with caplog.at_level("WARNING", logger="core.heartbeat"):
        heartbeat._write_heartbeat_once()
    msgs = [r.getMessage() for r in caplog.records if "event-loop starvation" in r.getMessage()]
    assert msgs and "power: Battery Power, 3%, discharging" in msgs[0]


def test_no_battery_probe_without_starvation(monkeypatch):
    """A healthy tick must not spawn pmset: the probe only runs when logging."""
    _reset_starvation_log_state()
    calls = []
    monkeypatch.setattr(heartbeat, "_battery_status", lambda: calls.append(1) or "")
    heartbeat._last_loop_tick = time.monotonic()
    heartbeat._write_heartbeat_once()
    assert calls == []


# ── Channel ack / heartbeat text follows the user's language (R3.4) ──────────
# channels.heartbeat is the chat-channel "still working" message, a different
# module from core.heartbeat above; it shares this file because both are the
# "heartbeat" a user or the daemon sees.

import asyncio as _asyncio

from channels import heartbeat as channel_hb


@pytest.mark.parametrize("text, lang", [
    ("帮我看看这个函数有没有问题", "zh"),
    ("帮我 review 一下这个 CI log", "zh"),
    ("在吗", "zh"),
    ("please check the deploy", "en"),
    ("¿Puedes revisar esto?", "en"),
    ("このコードを確認してください", "en"),
    ("이 코드를 검토해 주세요", "en"),
    ("ok", "en"),
    ("", "en"),
])
def test_reply_language_from_the_triggering_message(text, lang):
    assert channel_hb.reply_language(text) == lang


def test_templates_exist_for_both_languages():
    for lang in ("zh", "en"):
        assert set(channel_hb.ACK_TEMPLATES[lang]) == {"quick", "medium", "heavy"}
        assert all(channel_hb.ACK_TEMPLATES[lang][level] for level in ("quick", "medium", "heavy"))
        assert len(channel_hb.HEARTBEAT_PHASES[lang]) == len(channel_hb.HEARTBEAT_PHASES["zh"])
        assert channel_hb.stop_reply(lang)


def _has_han(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def test_english_sets_carry_no_chinese():
    for level, texts in channel_hb.ACK_TEMPLATES["en"].items():
        assert not any(_has_han(t) for t in texts), level
    for _threshold, texts in channel_hb.HEARTBEAT_PHASES["en"]:
        assert not any(_has_han(t) for t in texts)
    assert not _has_han(channel_hb.stop_reply("en"))


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_pick_ack_uses_the_language(lang):
    for level in ("quick", "medium", "heavy"):
        assert channel_hb.pick_ack(level, lang) in channel_hb.ACK_TEMPLATES[lang][level]


def test_pick_ack_unknown_language_falls_back_to_english():
    assert channel_hb.pick_ack("quick", "ja") in channel_hb.ACK_TEMPLATES["en"]["quick"]


def test_stop_reply_per_language():
    assert _has_han(channel_hb.stop_reply("zh"))
    assert channel_hb.stop_reply("ko") == channel_hb.stop_reply("en")


@pytest.mark.parametrize("lang", ["zh", "en"])
def test_heartbeat_updates_use_the_managers_language(lang, monkeypatch):
    updates = []

    async def post(channel, text):
        return "ts1"

    async def update(channel, ts, text):
        updates.append(text)

    async def delete(channel, ts):
        return None

    calls = {"n": 0}

    async def fast_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] > 2:
            raise _asyncio.CancelledError

    monkeypatch.setattr(channel_hb.asyncio, "sleep", fast_sleep)
    hb = channel_hb.HeartbeatManager(post_fn=post, update_fn=update, delete_fn=delete,
                                     channel="C1", language=lang)

    async def scenario():
        await hb.post_ack("x")
        hb._start_time = time.monotonic() - 10_000  # every phase is due
        with pytest.raises(_asyncio.CancelledError):
            await hb.run()

    _asyncio.run(scenario())
    phases = channel_hb.HEARTBEAT_PHASES[lang]
    assert len(updates) == 2
    assert updates[0] in phases[0][1]
    assert updates[1] in phases[1][1]
