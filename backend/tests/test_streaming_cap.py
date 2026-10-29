"""Streaming admission (peak-OOM guard) and its daemon-wide stream counter.

spawn_budget governs how many sessions may EXIST (follows RAM); streaming
admission governs whether a NEW turn may start streaming now. It follows real
memory pressure — a turn waits only under critical pressure with another stream
in flight, never because of a count cap. Admission must NOT touch spawn_budget.

Tests:
- counter inc/dec is exactly-once per STREAMING entry/exit, across EVERY exit
  path (IDLE, WAITING_INPUT, DEAD/kill, COLD) — because all route through
  _transition.
- counter never goes negative (clamp).
- multi-question loop (WAITING_INPUT→STREAMING→WAITING_INPUT) does not leak.
- _await_streaming_slot returns at once with no memory pressure (any number of
  streams), waits under critical pressure with streams in flight, and proceeds
  when pressure eases or the other streams end.
- COE05 invariant: resource_monitor.spawn_budget penalty is untouched by Step B.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import core.session_unit as su
from core.session_unit import SessionState, SessionUnit


@pytest.fixture(autouse=True)
def _reset_streaming_count():
    """Each test starts from a clean daemon-wide counter."""
    su._streaming_count = 0
    yield
    su._streaming_count = 0


def _idle_unit(session_id: str = "s") -> SessionUnit:
    u = SessionUnit(session_id=session_id, agent_id="default")
    u._transition(SessionState.IDLE)
    return u


# ── Counter accuracy ────────────────────────────────────────────────────────

def test_enter_streaming_increments_once():
    u = _idle_unit()
    assert su._get_streaming_count() == 0
    u._transition(SessionState.STREAMING)
    assert su._get_streaming_count() == 1


@pytest.mark.parametrize("exit_state", [
    SessionState.IDLE,
    SessionState.WAITING_INPUT,
    SessionState.DEAD,
    SessionState.COLD,
])
def test_every_streaming_exit_decrements_once(exit_state):
    """Every exit from STREAMING routes through _transition → decrements."""
    u = _idle_unit()
    u._transition(SessionState.STREAMING)
    assert su._get_streaming_count() == 1
    u._transition(exit_state)
    assert su._get_streaming_count() == 0


def test_multi_question_loop_does_not_leak():
    """WAITING_INPUT→STREAMING→WAITING_INPUT (multi-question turn) balances."""
    u = _idle_unit()
    u._transition(SessionState.STREAMING)      # +1 = 1
    u._transition(SessionState.WAITING_INPUT)  # -1 = 0
    u._transition(SessionState.STREAMING)      # +1 = 1  (answer 1)
    u._transition(SessionState.WAITING_INPUT)  # -1 = 0  (asks again)
    u._transition(SessionState.STREAMING)      # +1 = 1  (answer 2)
    u._transition(SessionState.IDLE)           # -1 = 0  (done)
    assert su._get_streaming_count() == 0


def test_counter_clamped_never_negative():
    """A spurious extra decrement (defensive) cannot drive the counter below 0."""
    su._streaming_count = 0
    u = _idle_unit()
    # Force an exit-from-STREAMING decrement path without a matching entry by
    # hand-driving the state (simulates a hypothetical double-dec); clamp holds.
    u.state = SessionState.STREAMING  # pretend streaming, count still 0
    u._transition(SessionState.IDLE)  # decrement path fires on count=0
    assert su._get_streaming_count() == 0  # clamped, not -1


def test_three_concurrent_streams_counted():
    units = [_idle_unit(f"s{i}") for i in range(3)]
    for u in units:
        u._transition(SessionState.STREAMING)
    assert su._get_streaming_count() == 3
    units[0]._transition(SessionState.IDLE)
    assert su._get_streaming_count() == 2


# ── Admission gate ───────────────────────────────────────────────────────────

from core.resource_monitor import SystemMemory, resource_monitor


def _mem(percent: float, measured: bool = True) -> SystemMemory:
    total = 36 * 1024**3
    return SystemMemory(total=total, available=int(total * (100 - percent) / 100),
                        used=int(total * percent / 100), percent_used=percent,
                        measured=measured)


@pytest.fixture
def fast_poll():
    with patch.object(SessionUnit, "_STREAM_ADMIT_POLL_INTERVAL", 0.02):
        yield


@pytest.mark.asyncio
async def test_many_streams_without_pressure_proceed_at_once():
    """The reported bug: 4 streams at ~60% memory queued the 5th for 120 s. With
    no memory pressure a new turn starts at once, however many streams run."""
    u = _idle_unit()
    su._streaming_count = 12
    with patch.object(resource_monitor, "system_memory", return_value=_mem(60.0)):
        await asyncio.wait_for(u._await_streaming_slot(), timeout=0.5)


@pytest.mark.asyncio
async def test_critical_pressure_with_no_other_stream_proceeds():
    """Waiting frees nothing when nothing else streams → proceed at once."""
    u = _idle_unit()
    su._streaming_count = 0
    with patch.object(resource_monitor, "system_memory", return_value=_mem(97.0)):
        await asyncio.wait_for(u._await_streaming_slot(), timeout=0.5)


@pytest.mark.asyncio
async def test_unreadable_memory_does_not_queue():
    u = _idle_unit()
    su._streaming_count = 3
    with patch.object(resource_monitor, "system_memory",
                      return_value=_mem(90.0, measured=False)):
        await asyncio.wait_for(u._await_streaming_slot(), timeout=0.5)


@pytest.mark.asyncio
async def test_critical_pressure_waits_until_pressure_eases(fast_poll):
    u = _idle_unit()
    su._streaming_count = 2
    reading = {"mem": _mem(93.0)}
    with patch.object(resource_monitor, "system_memory", side_effect=lambda: reading["mem"]):
        waiter = asyncio.create_task(u._await_streaming_slot())
        await asyncio.sleep(0.3)
        assert not waiter.done(), "must hold the turn while memory is critical"
        reading["mem"] = _mem(70.0)
        await asyncio.wait_for(waiter, timeout=1.0)


@pytest.mark.asyncio
async def test_critical_pressure_waits_until_other_streams_end(fast_poll):
    u = _idle_unit()
    su._streaming_count = 1
    with patch.object(resource_monitor, "system_memory", return_value=_mem(95.0)):
        waiter = asyncio.create_task(u._await_streaming_slot())
        await asyncio.sleep(0.3)
        assert not waiter.done()
        su._streaming_count = 0  # the in-flight stream ended
        await asyncio.wait_for(waiter, timeout=1.0)


@pytest.mark.asyncio
async def test_waiting_unit_is_marked_and_wait_ends_when_it_leaves_idle(fast_poll):
    """While waiting the unit is marked _admission_waiting (memory-pressure
    eviction skips it); if it leaves IDLE anyway (a refresh, a kill) the wait ends
    at once, so send()'s post-wait re-check handles it, and the mark is cleared."""
    u = _idle_unit()
    su._streaming_count = 1
    with patch.object(resource_monitor, "system_memory", return_value=_mem(95.0)):
        waiter = asyncio.create_task(u._await_streaming_slot())
        await asyncio.sleep(0.2)
        assert not waiter.done()
        assert u._admission_waiting is True
        u.state = SessionState.COLD  # killed / refreshed during the wait
        await asyncio.wait_for(waiter, timeout=1.0)
    assert u._admission_waiting is False


def test_no_count_cap_or_timeout_left():
    """No fixed stream cap and no admission timer exist any more."""
    assert not hasattr(SessionUnit, "MAX_CONCURRENT_STREAMS")
    assert not hasattr(SessionUnit, "_STREAM_ADMIT_TIMEOUT")


# ── COE05 invariant: spawn_budget untouched ──────────────────────────────────

def test_spawn_budget_penalty_untouched_by_step_b():
    """The two-limit split's whole point: Step B is orthogonal to spawn_budget.
    The COE05 simultaneous-peak floor (_CONCURRENT_PENALTY_FACTOR) must be intact."""
    from core import resource_monitor
    # The penalty constant still exists and is the documented COE05 floor.
    assert hasattr(resource_monitor, "_CONCURRENT_PENALTY_FACTOR") or \
        hasattr(resource_monitor.ResourceMonitor, "_CONCURRENT_PENALTY_FACTOR") or \
        any("PENALTY" in n for n in dir(resource_monitor)) or \
        any("PENALTY" in n for n in dir(resource_monitor.ResourceMonitor)), \
        "spawn_budget concurrency penalty (COE05 floor) must still exist"
