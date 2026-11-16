"""Graceful close of an idle CLI before the kill (session_unit._force_kill(graceful=True)).

A teardown of an IDLE unit with a live stream (TTL, orphan reap, release, eviction,
refresh, shutdown) closes the CLI's stdin and lets it exit by itself, so it writes its
exit records (cost-state, last-prompt) first; a CLI still running after the grace, or a
close that raises, ends in the SIGKILL sweep. A STREAMING / WAITING_INPUT / DEAD unit, a
dead stream and the send path's poison-guard recycle are SIGKILLed at once.

Only the leaves are faked (the SDK transport, the process table, os signals); the
branch under test is the real _force_kill / kill / _crash_to_cold_async / send.
"""
from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.session_unit as su
from core.session_unit import SessionState, SessionUnit

CLI_PID = 4242


class _Reader:
    def __init__(self, dead: bool = False):
        self.dead = dead

    def close(self):
        pass


def _unit(state: SessionState = SessionState.IDLE, *, reader_dead: bool = False,
          end_input=None, wait=None, sid: str = "graceful-test") -> tuple[SessionUnit, MagicMock]:
    unit = SessionUnit(session_id=sid, agent_id="default")
    transport = MagicMock()
    transport.end_input = end_input or AsyncMock()
    transport._process = MagicMock()
    transport._process.wait = wait or AsyncMock(return_value=0)
    wrapper = MagicMock()
    wrapper.pid = CLI_PID
    wrapper.client._transport = transport
    wrapper.__aexit__ = AsyncMock()
    unit._wrapper = wrapper
    unit._client = wrapper.client
    unit._stream_reader = _Reader(dead=reader_dead)
    unit._transition(SessionState.IDLE)
    if state == SessionState.STREAMING:
        unit._transition(SessionState.STREAMING)
    elif state == SessionState.WAITING_INPUT:
        unit._transition(SessionState.STREAMING)
        unit._transition(SessionState.WAITING_INPUT)
    return unit, transport


class _Signals:
    """Records every SIGKILL sent (to a group or a pid) and the order of events."""

    def __init__(self):
        self.events: list[str] = []

    def killpg(self, pgid, sig):
        self.events.append(f"killpg:{pgid}:{sig}")

    def kill(self, pid, sig):
        if sig == 0:
            raise ProcessLookupError()      # existence check: gone
        self.events.append(f"kill:{pid}:{sig}")

    @property
    def sigkills(self):
        return [e for e in self.events if e.endswith(f":{int(signal.SIGKILL)}")
                or e.endswith(f":{signal.SIGKILL}")]


def _patches(sig: _Signals, children=None):
    return [
        patch.object(su.os, "getpgid", side_effect=lambda p: 5000 if p == CLI_PID else 9999),
        patch.object(su.os, "getpid", return_value=1),
        patch.object(su.os, "killpg", side_effect=sig.killpg),
        patch.object(su.os, "kill", side_effect=sig.kill),
        patch.object(su, "_snapshot_descendant_procs", return_value=children or []),
        patch.object(SessionUnit, "_await_process_exit", new=AsyncMock()),
    ]


async def _run_kill(unit, sig, children=None, crash=False, **kw):
    ps = _patches(sig, children)
    for p in ps:
        p.start()
    try:
        if crash:
            await unit._crash_to_cold_async(**kw)
        else:
            await unit.kill()
    finally:
        for p in reversed(ps):
            p.stop()


@pytest.mark.asyncio
async def test_idle_kill_closes_stdin_before_sigkill():
    """IDLE + live stream: stdin closed and the exit awaited; the CLI, having exited by
    itself, gets no SIGKILL; a child that outlived it is killed."""
    sig = _Signals()
    order = sig.events
    end_input = AsyncMock(side_effect=lambda: order.append("end_input"))
    wait = AsyncMock(side_effect=lambda: order.append("exited") or 0)
    unit, transport = _unit(end_input=end_input, wait=wait)
    child = MagicMock()
    child.pid = 777
    child.is_running.return_value = True

    await _run_kill(unit, sig, children=[child])

    # stdin closed, the CLI exited by itself; only then its own process group is
    # swept (a detached process of the session dies too); the CLI pid is never killed
    assert order == ["end_input", "exited", f"killpg:5000:{signal.SIGKILL}"], order
    assert not any(e.startswith(f"kill:{CLI_PID}:") for e in order)
    child.kill.assert_called_once()
    assert unit.state == SessionState.COLD


@pytest.mark.asyncio
async def test_shared_group_graceful_exit_sweeps_only_descendants():
    """A CLI in the backend's own process group: never killpg (it would kill the
    backend); its surviving descendants are still killed."""
    sig = _Signals()
    unit, _ = _unit()
    child = MagicMock(pid=778)
    child.is_running.return_value = True
    ps = _patches(sig, [child])
    ps[0] = patch.object(su.os, "getpgid", return_value=9999)      # shared pgid
    for p in ps:
        p.start()
    try:
        await unit.kill()
    finally:
        for p in reversed(ps):
            p.stop()
    assert sig.sigkills == [], sig.events
    child.kill.assert_called_once()


@pytest.mark.asyncio
async def test_user_stop_recycle_is_immediate():
    """A user Stop recycles a CLI in a corrupt turn-state, and the next send waits for
    it: no graceful-close grace."""
    sig = _Signals()
    unit, transport = _unit(SessionState.STREAMING)
    unit._client.interrupt = AsyncMock()
    ps = _patches(sig)
    for p in ps:
        p.start()
    try:
        assert await unit.interrupt(timeout=5.0, autonomous=False) is True
    finally:
        for p in reversed(ps):
            p.stop()
    transport.end_input.assert_not_awaited()
    assert sig.sigkills
    assert unit.state == SessionState.COLD


@pytest.mark.asyncio
async def test_idle_kill_sweeps_children_that_outlive_cli():
    """Only the children still running are killed (psutil's identity check)."""
    sig = _Signals()
    unit, _ = _unit()
    gone, alive = MagicMock(pid=1), MagicMock(pid=2)
    gone.is_running.return_value = False
    alive.is_running.return_value = True

    await _run_kill(unit, sig, children=[gone, alive])

    gone.kill.assert_not_called()
    alive.kill.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [SessionState.STREAMING, SessionState.WAITING_INPUT])
async def test_streaming_kill_is_immediate(state):
    sig = _Signals()
    unit, transport = _unit(state)

    await _run_kill(unit, sig)

    transport.end_input.assert_not_awaited()
    assert sig.sigkills, "a mid-turn unit is SIGKILLed at once"


@pytest.mark.asyncio
async def test_dead_reader_kill_is_immediate():
    sig = _Signals()
    unit, transport = _unit(reader_dead=True)

    await _run_kill(unit, sig)

    transport.end_input.assert_not_awaited()
    assert sig.sigkills


@pytest.mark.asyncio
async def test_grace_timeout_falls_back_to_sigkill():
    """Forced execution of the recovery path: the CLI ignores the stdin close for the
    whole grace -> the SIGKILL sweep runs and the teardown completes."""
    sig = _Signals()
    never = asyncio.Event()

    async def hang():
        await never.wait()

    unit, transport = _unit(wait=AsyncMock(side_effect=hang))
    with patch.object(su, "GRACEFUL_EXIT_GRACE_S", 0.05):
        await _run_kill(unit, sig)

    transport.end_input.assert_awaited_once()
    assert sig.sigkills == [f"killpg:5000:{signal.SIGKILL}"], sig.events
    assert unit.state == SessionState.COLD


@pytest.mark.asyncio
async def test_end_input_error_falls_back_to_sigkill():
    sig = _Signals()
    unit, transport = _unit(end_input=AsyncMock(side_effect=OSError("broken pipe")))

    await _run_kill(unit, sig)

    transport._process.wait.assert_not_awaited()
    assert sig.sigkills
    assert unit.state == SessionState.COLD


@pytest.mark.asyncio
async def test_crash_to_cold_graceful_false_is_immediate():
    sig = _Signals()
    unit, transport = _unit()

    await _run_kill(unit, sig, crash=True, clear_identity=False, graceful=False)

    transport.end_input.assert_not_awaited()
    assert sig.sigkills


@pytest.mark.asyncio
async def test_refresh_context_closes_gracefully():
    """refresh_context (IDLE, user-initiated) takes the graceful close."""
    sig = _Signals()
    unit, transport = _unit()
    ps = _patches(sig)
    for p in ps:
        p.start()
    try:
        await unit.refresh_context()
    finally:
        for p in reversed(ps):
            p.stop()
    transport.end_input.assert_awaited_once()
    assert not any(e.startswith(f"kill:{CLI_PID}:") for e in sig.events)  # exited by itself


@pytest.mark.asyncio
async def test_poison_guard_recycle_is_immediate():
    """The send path's poison-guard recycle passes graceful=False: no grace there."""
    unit, _ = _unit()
    unit._last_turn_clean = False
    seen: list = []

    async def fake_force_kill(graceful=False):
        seen.append(graceful)

    async def abort_spawn(options, config):
        yield {"_abort": True}

    with patch.object(unit, "_force_kill", new=fake_force_kill), \
         patch.object(unit, "_arm_recovery_checkpoint", new=AsyncMock()), \
         patch.object(unit, "_ensure_spawned", new=abort_spawn):
        async for _ in unit.send("hi", MagicMock(), app_session_id="graceful-test"):
            pass

    assert seen == [False]


@pytest.mark.asyncio
async def test_stop_background_recycle_is_immediate():
    """Stopping background agents recycles at once (the person asked for it now)."""
    sig = _Signals()
    unit, transport = _unit()
    ps = _patches(sig) + [patch.object(unit.background_agents, "running", return_value=True)]
    for p in ps:
        p.start()
    try:
        assert await unit.stop_background() is True
    finally:
        for p in reversed(ps):
            p.stop()
    transport.end_input.assert_not_awaited()
    assert sig.sigkills


# ── shutdown: idle CLIs close concurrently ────────────────────────────────────

def _router_with(units):
    from core.session_router import SessionRouter
    router = SessionRouter(prompt_builder=MagicMock())
    for u in units:
        router._units[u.session_id] = u
    order: list[str] = []
    lm = MagicMock()

    async def enqueue(unit):
        order.append(f"hooks:{unit.session_id}")
        unit._hooks_enqueued = True

    lm.enqueue_hooks_for_unit = AsyncMock(side_effect=enqueue)
    router._lifecycle_manager = lm
    return router, order


async def _shutdown(router, sig):
    ps = _patches(sig) + [patch("core.session_state_persistence.persist_session_state",
                                return_value=0)]
    for p in ps:
        p.start()
    try:
        await router.disconnect_all()
    finally:
        for p in reversed(ps):
            p.stop()


@pytest.mark.asyncio
async def test_shutdown_closes_idle_clis_concurrently():
    """Four idle CLIs that each ignore the stdin close for the whole grace: shutdown
    takes about one grace, not four; each unit's hooks are enqueued before its stdin
    is closed, each keeps the full grace and is SIGKILLed after it."""
    grace = 0.3
    sig = _Signals()
    never = asyncio.Event()
    units, order = [], None

    def _hang_for(sid):
        async def hang():
            await never.wait()
        return hang

    for i in range(4):
        sid = f"shutdown-{i}"
        unit, _ = _unit(sid=sid, wait=AsyncMock(side_effect=_hang_for(sid)))
        units.append(unit)
    router, order = _router_with(units)
    for unit in units:
        transport = unit._wrapper.client._transport
        transport.end_input = AsyncMock(
            side_effect=lambda s=unit.session_id: order.append(f"stdin:{s}"))

    t0 = asyncio.get_running_loop().time()
    with patch.object(su, "GRACEFUL_EXIT_GRACE_S", grace):
        await _shutdown(router, sig)
    elapsed = asyncio.get_running_loop().time() - t0

    assert elapsed >= grace                      # each CLI still got its grace
    assert elapsed < 2 * grace, f"closed one after another: {elapsed:.2f}s"
    assert len(sig.sigkills) == 4, sig.events    # and the kill after it
    for unit in units:
        assert order.index(f"hooks:{unit.session_id}") < order.index(f"stdin:{unit.session_id}")
        assert unit.state == SessionState.COLD


@pytest.mark.asyncio
async def test_shutdown_one_failing_unit_does_not_stop_others():
    sig = _Signals()
    units = [_unit(sid=f"fail-{i}")[0] for i in range(3)]
    router, _ = _router_with(units)
    units[0].kill = AsyncMock(side_effect=RuntimeError("kill failed"))
    real_enqueue = router._lifecycle_manager.enqueue_hooks_for_unit.side_effect

    async def enqueue(unit):
        if unit is units[1]:
            raise RuntimeError("hook context failed")
        await real_enqueue(unit)

    router._lifecycle_manager.enqueue_hooks_for_unit = AsyncMock(side_effect=enqueue)
    await _shutdown(router, sig)
    assert units[1].state == SessionState.COLD    # its hooks failed: its CLI still closed
    assert units[2].state == SessionState.COLD
    units[0].kill.assert_awaited_once()
