"""Slack Socket-Mode connectivity-stall recovery (run_eb503e1e).

Root cause fixed: the WS thread can stay ALIVE while slack_bolt's internal reconnect
loop fails forever (intermittent getaddrinfo). is_alive() never flips → the existing
thread-death recovery never fires → messages silently dropped. This adds an
is_connected()-based stall detector that, after SUSTAINED disconnection, switches to
the existing polling fallback.

Drives the REAL code under change — _is_socket_connected + _ws_health_monitor's stall
branch — mocking only the slack_bolt boundary (the handler/client) and the recovery
sink (_switch_to_polling). No mock of the method under test (GUI32/PIT13).

Gate-1-hardened invariants under test:
  * None-safe: no handler / no client / no is_connected → fail-SAFE to "connected"
    (a probe gap must NEVER force a false stall — Gate-1 F1).
  * Long window: is_connected()==False is normal DURING any reconnect (Gate-1 F2), so
    only SUSTAINED misses (>= _STALL_MISS_THRESHOLD) trigger; a healthy idle/connected
    channel and a sub-threshold blip never switch.
"""
import asyncio
from types import SimpleNamespace


from channels.adapters import slack as slackmod
from channels.adapters.slack import SlackChannelAdapter, _STALL_MISS_THRESHOLD


def _adapter():
    """A bare adapter instance without running __init__ side effects; set only the
    fields the monitor/helper touch."""
    a = SlackChannelAdapter.__new__(SlackChannelAdapter)
    a._handler = None
    a._stall_misses = 0
    a._ws_fail_count = 0
    a._ever_connected = True   # default: already connected once (stall-detection armed)
    a._stopped = False
    a._connection_mode = "socket"
    a.channel_id = "TESTCH"
    return a


def _handler_with(connected):
    """A fake slack_bolt handler exposing .client.is_connected()."""
    return SimpleNamespace(client=SimpleNamespace(is_connected=lambda: connected))


# ── _is_socket_connected: None-safe + fail-SAFE (Gate-1 F1) ──────────────────

class TestIsSocketConnected:
    def test_no_handler_is_failsafe_true(self):
        a = _adapter(); a._handler = None
        assert a._is_socket_connected() is True  # probe gap → NOT a stall

    def test_no_client_attr_is_failsafe_true(self):
        a = _adapter(); a._handler = SimpleNamespace()  # no .client
        assert a._is_socket_connected() is True

    def test_client_none_is_failsafe_true(self):
        a = _adapter(); a._handler = SimpleNamespace(client=None)
        assert a._is_socket_connected() is True

    def test_is_connected_raises_is_failsafe_true(self):
        def boom(): raise RuntimeError("sdk internals moved")
        a = _adapter(); a._handler = SimpleNamespace(client=SimpleNamespace(is_connected=boom))
        assert a._is_socket_connected() is True

    def test_reports_real_connected_state(self):
        a = _adapter()
        a._handler = _handler_with(True);  assert a._is_socket_connected() is True
        a._handler = _handler_with(False); assert a._is_socket_connected() is False


# ── stall detection in the monitor loop ──────────────────────────────────────

def _run_monitor_until_switch_or_ticks(a, max_ticks):
    """Run _ws_health_monitor but replace asyncio.sleep so the loop advances
    instantly and stops after max_ticks (or when the mode leaves 'socket').
    Returns the number of ticks executed."""
    ticks = {"n": 0}
    real_sleep = asyncio.sleep

    async def fake_sleep(secs):
        # first sleep(15) is the startup grace; every sleep(10) is one tick
        if secs == 10:
            ticks["n"] += 1
            if ticks["n"] >= max_ticks:
                a._stopped = True  # force loop exit after budget
        return await real_sleep(0)

    async def go():
        orig = asyncio.sleep
        asyncio.sleep = fake_sleep  # type: ignore
        try:
            await a._ws_health_monitor()
        finally:
            asyncio.sleep = orig  # type: ignore
    asyncio.run(go())
    return ticks["n"]


class TestStallRecovery:
    def test_sustained_disconnect_triggers_polling(self, monkeypatch):
        """Alive thread + is_connected()==False for _STALL_MISS_THRESHOLD checks
        → _switch_to_polling fires (AC1)."""
        a = _adapter()
        a._ws_thread = SimpleNamespace(is_alive=lambda: True)
        a._handler = _handler_with(False)   # never connected
        switched = {"n": 0}
        async def _sw():
            switched["n"] += 1
            a._connection_mode = "polling"   # real _switch_to_polling sets this
        a._switch_to_polling = _sw
        _run_monitor_until_switch_or_ticks(a, max_ticks=_STALL_MISS_THRESHOLD + 5)
        assert switched["n"] == 1, "sustained stall must switch to polling exactly once"

    def test_healthy_connected_never_switches(self, monkeypatch):
        """Alive + is_connected()==True (healthy, maybe idle) → never switches (AC2)."""
        a = _adapter()
        a._ws_thread = SimpleNamespace(is_alive=lambda: True)
        a._handler = _handler_with(True)
        switched = {"n": 0}
        async def _sw(): switched["n"] += 1
        a._switch_to_polling = _sw
        _run_monitor_until_switch_or_ticks(a, max_ticks=_STALL_MISS_THRESHOLD * 2)
        assert switched["n"] == 0
        assert a._stall_misses == 0

    def test_subthreshold_blip_does_not_switch(self, monkeypatch):
        """A few disconnected checks that RECOVER before the threshold → no switch,
        counter resets (AC3 — the healthy-reconnect false-positive guard)."""
        a = _adapter()
        a._ws_thread = SimpleNamespace(is_alive=lambda: True)
        # disconnected for (threshold-2) ticks, then reconnects
        seq = [False] * (_STALL_MISS_THRESHOLD - 2) + [True] * 10
        it = iter(seq)
        a._handler = SimpleNamespace(client=SimpleNamespace(
            is_connected=lambda: next(it, True)))
        switched = {"n": 0}
        async def _sw(): switched["n"] += 1
        a._switch_to_polling = _sw
        _run_monitor_until_switch_or_ticks(a, max_ticks=_STALL_MISS_THRESHOLD + 8)
        assert switched["n"] == 0, "a blip that recovers before threshold must NOT switch"
        assert a._stall_misses == 0, "counter resets on reconnect"

    def test_cold_start_never_armed_before_first_connection(self, monkeypatch):
        """Gate-2 HIGH-1: a channel that has NEVER connected (slow cold-start
        handshake reads as not-connected) must NOT be treated as a stall, no matter
        how many checks — stall-detection arms only after the first real connection."""
        a = _adapter()
        a._ever_connected = False   # never connected yet
        a._ws_thread = SimpleNamespace(is_alive=lambda: True)
        a._handler = _handler_with(False)  # not connected (still handshaking)
        switched = {"n": 0}
        async def _sw(): switched["n"] += 1
        a._switch_to_polling = _sw
        _run_monitor_until_switch_or_ticks(a, max_ticks=_STALL_MISS_THRESHOLD * 3)
        assert switched["n"] == 0, "cold-start must never false-switch to polling"
        assert a._stall_misses == 0

    def test_first_connection_arms_then_stall_detected(self, monkeypatch):
        """Gate-2 recovery-cycle: once connected (arms), a later sustained stall IS
        detected. Proves _ever_connected doesn't permanently disable detection."""
        a = _adapter()
        a._ever_connected = False
        a._ws_thread = SimpleNamespace(is_alive=lambda: True)
        # connect once, then disconnect forever
        seq = [True] + [False] * (_STALL_MISS_THRESHOLD + 5)
        it = iter(seq)
        a._handler = SimpleNamespace(client=SimpleNamespace(
            is_connected=lambda: next(it, False)))
        switched = {"n": 0}
        async def _sw():
            switched["n"] += 1; a._connection_mode = "polling"
        a._switch_to_polling = _sw
        _run_monitor_until_switch_or_ticks(a, max_ticks=_STALL_MISS_THRESHOLD + 8)
        assert switched["n"] == 1, "after arming on first connect, a sustained stall must switch"

    def test_thread_death_path_still_works(self, monkeypatch):
        """Regression (AC4): the original is_alive()==False path still counts toward
        _WS_FAIL_THRESHOLD and switches — unchanged by the new branch."""
        monkeypatch.setattr(slackmod, "_WS_FAIL_THRESHOLD", 2)
        a = _adapter()
        a._ws_thread = SimpleNamespace(is_alive=lambda: False)   # dead
        a._handler = None
        a._start_socket_mode_thread = lambda: None
        switched = {"n": 0}
        async def _sw():
            switched["n"] += 1; a._connection_mode = "polling"
        a._switch_to_polling = _sw
        _run_monitor_until_switch_or_ticks(a, max_ticks=6)
        assert switched["n"] == 1, "thread-death path still reaches polling"
