"""Early spawn: a cold turn's CLI starts while its recall runs (SessionUnit.start_early_spawn).

The CLI itself (_spawn_client's subprocess) is the only fake; the staging, the adoption
in _ensure_spawned, the fallbacks, the adoption window and the router's call order are
the real code. Marked ``early_spawn`` (conftest stubs start_early_spawn elsewhere).
"""
from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import ClaudeAgentOptions

import core.session_unit as su
from core.session_unit import SessionState, SessionUnit, _SpawnedClient

pytestmark = pytest.mark.early_spawn


def _spawned(total_ms: float = 900.0) -> _SpawnedClient:
    wrapper = MagicMock(pid=31337)
    wrapper.__aexit__ = AsyncMock()
    return _SpawnedClient(wrapper=wrapper, client=MagicMock(name="early-client"),
                          lock_wait_ms=0.0, configure_ms=0.1, aenter_ms=total_ms,
                          total_ms=total_ms)


def _unit(sid: str = "early-test") -> SessionUnit:
    return SessionUnit(session_id=sid, agent_id="default")


async def _drain(gen):
    return [ev async for ev in gen]


@pytest.mark.asyncio
async def test_send_adopts_early_client_without_second_spawn():
    unit = _unit()
    spawned = _spawned()
    spawn_client = AsyncMock(return_value=spawned)
    with patch.object(SessionUnit, "_spawn_client", new=spawn_client), \
         patch.object(SessionUnit, "_start_stream_reader"):
        assert unit.start_early_spawn(ClaudeAgentOptions(), None) is True
        events = await _drain(unit._ensure_spawned(ClaudeAgentOptions(), None))
    assert events == []
    assert spawn_client.await_count == 1
    assert unit.state == SessionState.IDLE
    assert unit._client is spawned.client and unit._wrapper is spawned.wrapper
    assert unit._early_spawn is None
    spawned.wrapper.__aexit__.assert_not_awaited()


@pytest.mark.asyncio
async def test_unit_stays_cold_until_adoption():
    """The spawned CLI is held aside: the unit is COLD with no client until the send
    adopts it, so IDLE-edge listeners and the cold pending claim see no change."""
    unit = _unit()
    states: list = []
    unit._on_state_change = lambda sid, old, new: states.append(new)
    spawned = _spawned()
    with patch.object(SessionUnit, "_spawn_client", new=AsyncMock(return_value=spawned)), \
         patch.object(SessionUnit, "_start_stream_reader"):
        unit.start_early_spawn(ClaudeAgentOptions(), None)
        await unit._early_spawn.task                  # the CLI is up
        assert unit.state == SessionState.COLD and unit._client is None
        assert states == []
        await _drain(unit._ensure_spawned(ClaudeAgentOptions(), None))
    assert unit.state == SessionState.IDLE


@pytest.mark.asyncio
async def test_ttft_spawn_segment_is_the_wait():
    """The TTFT spawn segment of an adopted early spawn is what send() waited for, not
    the whole spawn (most of it ran during recall)."""
    unit = _unit()
    spawned = _spawned(total_ms=2500.0)
    with patch.object(SessionUnit, "_spawn_client", new=AsyncMock(return_value=spawned)), \
         patch.object(SessionUnit, "_start_stream_reader"):
        unit.start_early_spawn(ClaudeAgentOptions(), None)
        await unit._early_spawn.task
        await _drain(unit._ensure_spawned(ClaudeAgentOptions(), None))
    assert unit._last_turn_spawn_ms is not None and unit._last_turn_spawn_ms < 2500.0


@pytest.mark.asyncio
async def test_early_spawn_failure_falls_back_to_normal_spawn():
    """Forced execution: the early spawn raises a non-retriable error → the send's own
    fallback chain handles that error exactly as one of its own spawn (no second
    spawn, the same SPAWN_FAILED frame and abort)."""
    unit = _unit()
    spawn_client = AsyncMock(side_effect=RuntimeError("bad option --frobnicate"))
    normal = AsyncMock()
    with patch.object(SessionUnit, "_spawn_client", new=spawn_client), \
         patch.object(SessionUnit, "_spawn", new=normal):
        unit.start_early_spawn(ClaudeAgentOptions(), None)
        events = await _drain(unit._ensure_spawned(ClaudeAgentOptions(), None))
    normal.assert_not_awaited()
    assert events[-1] == {"_abort": True}
    assert any(ev.get("code") == "SPAWN_FAILED" for ev in events)


@pytest.mark.asyncio
async def test_early_resume_not_found_goes_down_the_resume_fallback():
    """Forced execution: the early --resume spawn finds no transcript → the real
    session-not-found fallback runs (identity dropped, message kept)."""
    unit = _unit()
    unit._sdk_session_id = "sdk-gone"
    spawn_client = AsyncMock(side_effect=RuntimeError(
        "Claude Code returned an error result: No conversation found with session ID: "
        "sdk-gone (exit code: 1)"))
    with patch.object(SessionUnit, "_spawn_client", new=spawn_client):
        unit.start_early_spawn(ClaudeAgentOptions(), None)
        assert unit._early_spawn.options.resume == "sdk-gone"
        events = await _drain(unit._ensure_spawned(ClaudeAgentOptions(), None))
    assert unit._sdk_session_id is None
    assert any(ev.get("history_dropped") for ev in events)


@pytest.mark.asyncio
async def test_resume_id_change_discards_early_client():
    """Forced execution: the unit's resume id changed between the early start and the
    send → the early CLI is closed and a normal spawn runs."""
    import time as _time
    unit = _unit()
    unit._sdk_session_id = "sdk-a"
    spawned = _spawned()

    async def slow_close(*a):
        await asyncio.sleep(0.5)

    spawned.wrapper.__aexit__ = AsyncMock(side_effect=slow_close)
    normal = AsyncMock()
    with patch.object(SessionUnit, "_spawn_client", new=AsyncMock(return_value=spawned)), \
         patch.object(SessionUnit, "_spawn", new=normal):
        unit.start_early_spawn(ClaudeAgentOptions(), None)
        await unit._early_spawn.task
        unit._sdk_session_id = None          # e.g. a give-up dropped the identity
        t0 = _time.perf_counter()
        await _drain(unit._ensure_spawned(ClaudeAgentOptions(), None))
        normal.assert_awaited_once()
        # the send spawns at once: the discarded CLI is closed in the background
        assert _time.perf_counter() - t0 < 0.4
        for _ in range(80):
            await asyncio.sleep(0.01)
        for _ in range(20):
            await asyncio.sleep(0.01)
    spawned.wrapper.__aexit__.assert_awaited()


@pytest.mark.asyncio
async def test_staged_early_spawn_counts_in_the_spawn_budget():
    """Another unit's early CLI (its unit still COLD) counts as running for the
    spawn budget, like an alive unit."""
    from core import session_registry
    from core.session_router import SessionRouter
    router = SessionRouter(prompt_builder=MagicMock(), config=MagicMock())
    a = router.get_or_create_unit("unit-a", "default")
    b = router.get_or_create_unit("unit-b", "default")
    seen: list[int] = []

    def budget(alive_count=0):
        seen.append(alive_count)
        return MagicMock(can_spawn=False, reason="test", available_mb=0,
                         estimated_cost_mb=0, headroom_mb=0)

    from core.resource_monitor import resource_monitor
    with patch.object(SessionUnit, "_early_spawn_job", new=AsyncMock(return_value=_spawned())), \
         patch.object(session_registry, "session_router", router), \
         patch.object(resource_monitor, "spawn_budget", side_effect=budget):
        a.start_early_spawn(ClaudeAgentOptions(), None)
        assert router.staged_spawn_count() == 1
        assert router.staged_spawn_count(exclude=a) == 0
        with pytest.raises(Exception):
            await b._spawn_client(ClaudeAgentOptions(), None)
        a.release_early_spawn()
    assert seen == [1]


@pytest.mark.asyncio
async def test_unadopted_early_client_closed_after_window():
    """Forced execution of the backstop: no send adopts it → closed when the window
    ends, and the unit no longer holds it."""
    unit = _unit()
    spawned = _spawned()
    with patch.object(SessionUnit, "_spawn_client", new=AsyncMock(return_value=spawned)), \
         patch.object(su, "EARLY_SPAWN_ADOPT_WINDOW_S", 0.05):
        unit.start_early_spawn(ClaudeAgentOptions(), None)
        await asyncio.wait_for(unit._early_spawn.watch, timeout=5)
    spawned.wrapper.__aexit__.assert_awaited_once()
    assert unit._early_spawn is None


@pytest.mark.asyncio
async def test_release_closes_an_unadopted_early_client():
    """The router's turn-end release closes it at once (no wait for the window)."""
    unit = _unit()
    spawned = _spawned()
    with patch.object(SessionUnit, "_spawn_client", new=AsyncMock(return_value=spawned)):
        unit.start_early_spawn(ClaudeAgentOptions(), None)
        unit.release_early_spawn()
        assert unit._early_spawn is None
        for _ in range(20):
            await asyncio.sleep(0.01)
    spawned.wrapper.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_preflight_refusal_starts_no_cli():
    unit = _unit()
    spawn_client = AsyncMock(return_value=_spawned())
    refusal = [{"type": "error", "code": "CREDENTIALS_EXPIRED"}, {"_abort": True}]
    with patch.object(SessionUnit, "_spawn_client", new=spawn_client), \
         patch.object(SessionUnit, "_credential_preflight", new=AsyncMock(return_value=refusal)):
        unit.start_early_spawn(ClaudeAgentOptions(), None)
        await unit._early_spawn.task
    spawn_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_early_spawn_when_transcript_poisoned_or_shed_due():
    unit = _unit()
    with patch.object(SessionUnit, "_spawn_client", new=AsyncMock(return_value=_spawned())):
        unit._transcript_poisoned = True
        assert unit.start_early_spawn(ClaudeAgentOptions(), None) is False
        unit._transcript_poisoned = False
        unit._media_shed_due = True
        assert unit.start_early_spawn(ClaudeAgentOptions(), None) is False
        unit._media_shed_due = False
        unit._client = object()
        unit._transition(SessionState.IDLE)
        assert unit.start_early_spawn(ClaudeAgentOptions(), None) is False
    assert unit._early_spawn is None


@pytest.mark.asyncio
async def test_cold_turn_starts_spawn_before_recall():
    """Through the REAL run_conversation: on a cold turn the CLI spawn has started
    when recall runs, and the query reaches the unit only after recall finished."""
    from core.session_router import SessionRouter

    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=ClaudeAgentOptions(system_prompt="STABLE"))
    router = SessionRouter(prompt_builder=pb, config=MagicMock())
    sid = f"early-{uuid.uuid4()}"
    unit = router.get_or_create_unit(sid, "default")
    order: list[str] = []
    spawned = _spawned()

    async def spawn_client(self, options, config):
        order.append("spawn")
        return spawned

    async def recall(**kw):
        await asyncio.sleep(0)               # the spawn task gets the loop first
        order.append("recall")
        return None

    async def send(query_content, options, *a, **k):
        order.append("send")
        yield {"type": "result", "sessionId": sid, "durationMs": 1, "totalCostUsd": 0}

    with patch.object(SessionUnit, "_spawn_client", new=spawn_client), \
         patch("core.session_router._maybe_inject_recall", new=recall), \
         patch.object(SessionUnit, "send", side_effect=send):
        async for _ in router.run_conversation(agent_id="default", user_message="hello there",
                                               session_id=sid, client_id=f"c-{uuid.uuid4()}"):
            pass
        for _ in range(20):
            await asyncio.sleep(0.01)
    assert order[:3] == ["spawn", "recall", "send"], order
    # send() was scripted here, so nothing adopted it: the turn-end release closed it
    spawned.wrapper.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_released_then_window_closes_only_once():
    """A spawn the turn-end release closed is not closed a second time by the window."""
    unit = _unit()
    spawned = _spawned()
    with patch.object(SessionUnit, "_spawn_client", new=AsyncMock(return_value=spawned)), \
         patch.object(su, "EARLY_SPAWN_ADOPT_WINDOW_S", 0.05):
        unit.start_early_spawn(ClaudeAgentOptions(), None)
        watch = unit._early_spawn.watch
        unit.release_early_spawn()
        await asyncio.wait_for(watch, timeout=5)
        for _ in range(10):
            await asyncio.sleep(0.01)
    spawned.wrapper.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_of_a_failed_or_cancelled_early_spawn_closes_nothing():
    """Releasing an early spawn that raised, or whose task was cancelled, ends quietly
    with nothing to close."""
    from core.session_unit import _EARLY_CLOSE_TASKS
    for outcome in ("raised", "cancelled"):
        unit = _unit(f"early-{outcome}")
        gate = asyncio.Event()

        async def job(self, options, config):
            await gate.wait()
            raise RuntimeError("spawn failed")

        with patch.object(SessionUnit, "_early_spawn_job", new=job), \
             patch.object(su, "_close_spawned", new=AsyncMock()) as close:
            unit.start_early_spawn(ClaudeAgentOptions(), None)
            early = unit._early_spawn
            if outcome == "cancelled":
                early.task.cancel()
            else:
                gate.set()
            unit.release_early_spawn()
            tasks = list(_EARLY_CLOSE_TASKS)
            done, _ = await asyncio.wait(tasks, timeout=5)
            assert all(not t.cancelled() and t.exception() is None for t in done)
            close.assert_not_awaited()
            early.watch.cancel()


@pytest.mark.asyncio
async def test_cancelled_close_task_reraises_and_leaves_the_spawn_running():
    """Cancelling the close task (loop shutdown) is not swallowed; the spawn itself
    keeps running (the window still backstops it)."""
    from core.session_unit import _EARLY_CLOSE_TASKS
    unit = _unit()
    gate = asyncio.Event()

    async def job(self, options, config):
        await gate.wait()
        return _spawned()

    with patch.object(SessionUnit, "_early_spawn_job", new=job):
        unit.start_early_spawn(ClaudeAgentOptions(), None)
        early = unit._early_spawn
        unit.release_early_spawn()
        (close_task,) = [t for t in _EARLY_CLOSE_TASKS]
        await asyncio.sleep(0)
        close_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_task
        assert not early.task.done()
        gate.set()
        await early.task
        early.watch.cancel()
