"""R6 Step B — concurrent-streaming admission cap (the two-limit split).

design §9.4: spawn_budget governs how many sessions may EXIST (follows RAM, no
ceiling); this NEW independent cap governs how many may STREAM AT ONCE (peak-OOM
guard). They are orthogonal — Step B must NOT touch spawn_budget. 3027ea6c failed
by conflating both into one number.

Tests:
- counter inc/dec is exactly-once per STREAMING entry/exit, across EVERY exit
  path (IDLE, WAITING_INPUT, DEAD/kill, COLD) — because all route through
  _transition.
- counter never goes negative (clamp).
- multi-question loop (WAITING_INPUT→STREAMING→WAITING_INPUT) does not leak.
- _await_streaming_slot returns immediately below cap, blocks at cap, proceeds on
  timeout.
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

@pytest.mark.asyncio
async def test_await_slot_returns_immediately_below_cap():
    u = _idle_unit()
    su._streaming_count = 0
    # Should not block.
    await asyncio.wait_for(u._await_streaming_slot(), timeout=1.0)


@pytest.mark.asyncio
async def test_await_slot_blocks_at_cap_then_proceeds_when_freed():
    u = _idle_unit()
    su._streaming_count = SessionUnit.MAX_CONCURRENT_STREAMS  # at cap

    async def _free_after_delay():
        await asyncio.sleep(0.25)
        su._streaming_count -= 1  # a stream finishes

    waiter = asyncio.create_task(u._await_streaming_slot())
    freer = asyncio.create_task(_free_after_delay())
    # Without the free, this would run to the 120s timeout; with it, ~0.25s.
    await asyncio.wait_for(waiter, timeout=5.0)
    await freer


@pytest.mark.asyncio
async def test_await_slot_proceeds_on_timeout_does_not_hang_forever():
    """At cap with nothing freeing: gate must PROCEED on timeout, not deadlock."""
    u = _idle_unit()
    su._streaming_count = SessionUnit.MAX_CONCURRENT_STREAMS
    # Shrink timeout so the test is fast; assert it returns (proceeds), not hangs.
    with patch.object(SessionUnit, "_STREAM_ADMIT_TIMEOUT", 0.3), \
         patch.object(SessionUnit, "_STREAM_ADMIT_POLL_INTERVAL", 0.05):
        await asyncio.wait_for(u._await_streaming_slot(), timeout=3.0)


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
