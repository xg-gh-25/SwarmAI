"""Recovery when the CLI dies with -9 (2026-10-20 13:22 and 17:20).

13:22: a sandboxed pytest booted the app, whose startup sweep could not read the
live CLIs' SWARMAI_OWNER_PID (the sandbox denies /bin/ps) and killed them all.
One of them was mid permission-continuation, which had no retry, so it stayed
stopped. 17:20: the stall watchdog's own -9 was classified OOM and set a global
cooldown for every tab.

Tested here:
- the startup sweep skips a CLI whose owner tag cannot be read, and still kills
  one whose owner is dead (control);
- the test suite runs with the sweep off;
- a stall-watchdog -9 is not OOM: no OOM status, no OOM counter (control: the
  same -9 without the marker is still OOM);
- a permission continuation whose process dies resumes through the send()
  retry; a non-retriable error still re-raises.
"""
from __future__ import annotations

import os
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import session_registry
from core.session_unit import SessionState, SessionUnit
from core.session_utils import FailureType, classify_failure

KILL = "Command failed with exit code -9 (exit code: -9)"


def _pgrep(pids):
    return MagicMock(returncode=0, stdout="\n".join(str(p) for p in pids))


def _sweep(owner_pid):
    killed = []
    with patch("core.session_registry.platform.system", return_value="Darwin"), \
         patch("core.session_registry.subprocess.run", return_value=_pgrep([4242])), \
         patch("core.session_registry._read_owner_pid_sync", return_value=owner_pid), \
         patch("core.session_unit._snapshot_descendant_tree", return_value=[]), \
         patch("core.session_unit._kill_pids", return_value=0), \
         patch("core.session_registry.os.kill",
               side_effect=lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError())
               if sig == 0 else killed.append((pid, sig))):
        n = session_registry.kill_all_claude_processes()
    return n, killed


def test_sweep_skips_a_cli_whose_owner_tag_cannot_be_read():
    n, killed = _sweep(None)
    assert n == 0 and killed == []


def test_sweep_still_kills_a_cli_whose_owner_is_dead():
    n, killed = _sweep(999_999)
    assert n == 1 and killed == [(4242, signal.SIGKILL)]


def test_suite_runs_with_the_startup_sweep_off():
    assert os.environ.get("SWARMAI_NO_CLI_SWEEP") == "1"


def test_stall_kill_classifies_as_a_hang_not_oom():
    ft, meta = classify_failure(KILL, None, stall_kill=True)
    assert ft is FailureType.UNKNOWN and meta.get("stall_kill")


def _unit() -> SessionUnit:
    u = SessionUnit(session_id="kill-1", agent_id="agent-1")
    u._sdk_session_id = "sdk-1"
    u.MAX_RETRY_ATTEMPTS = 3
    u.RETRY_BACKOFF_SECONDS = 0.01
    return u


async def _retry(unit):
    async def ok_stream(_q):
        yield {"type": "result"}

    budget = MagicMock(can_spawn=True, reason="")
    with patch("core.session_unit._oom_cooldown_until", 0), \
         patch("core.resource_monitor.resource_monitor.spawn_budget", return_value=budget), \
         patch.object(unit, "_build_retry_options", return_value=MagicMock()), \
         patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock), \
         patch.object(unit, "_transition"), \
         patch.object(unit, "_spawn", AsyncMock()), \
         patch.object(unit._streaming_orchestrator, "stream_query", side_effect=ok_stream), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        return [e async for e in unit._retry_with_resume("msg", MagicMock(), None, KILL, "")]


def _oom_status(events):
    return [e for e in events if e.get("code") == "OOM_DETECTED"]


@pytest.mark.asyncio
async def test_stall_watchdog_kill_sets_no_oom_state():
    control = _unit()
    control_events = await _retry(control)
    assert _oom_status(control_events), "control: a bare -9 is still treated as OOM"

    unit = _unit()
    unit._stall_kill_pending = True
    events = await _retry(unit)
    assert _oom_status(events) == []
    assert unit._consecutive_oom_kills == 0
    assert unit._stall_kill_pending is False      # consumed once
    assert {"type": "result"} in events


async def _continue(unit, read_exc):
    async def dying_read():
        raise read_exc
        yield  # pragma: no cover

    async def resumed(*_a, **_k):
        yield {"type": "result", "resumed": True}

    unit.state = SessionState.WAITING_INPUT
    unit._pending_question = {"request_id": "req-1", "tool_use_id": "req-1"}
    unit._pending_tool_use_id = "req-1"
    unit._client = MagicMock()
    retry = MagicMock(side_effect=resumed)
    with patch("core.permission_manager.permission_manager") as pm, \
         patch("core.permission_prompt.record_decision", AsyncMock(return_value=True)), \
         patch.object(unit, "_read_formatted_response", side_effect=dying_read), \
         patch.object(unit, "_retry_with_resume", retry), \
         patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock), \
         patch.object(unit, "_transition"):
        pm.is_open = MagicMock(return_value=True)
        pm.update_pending_request = MagicMock()
        pm.set_permission_decision = MagicMock()
        events = [e async for e in unit.continue_with_permission("req-1", True)]
    return events, retry


@pytest.mark.asyncio
async def test_permission_continuation_resumes_when_its_process_dies():
    unit = _unit()
    unit._last_send_options = opts = MagicMock()
    events, retry = await _continue(unit, RuntimeError(KILL))
    assert {"type": "result", "resumed": True} in events
    args = retry.call_args.args
    assert args[1] is opts and args[3] == KILL


@pytest.mark.asyncio
async def test_permission_continuation_non_retriable_error_still_raises():
    unit = _unit()
    unit._last_send_options = MagicMock()
    with pytest.raises(ValueError):
        await _continue(unit, ValueError("bad schema"))


@pytest.mark.asyncio
async def test_running_out_of_restarts_writes_gave_up_with_the_turn():
    """docs/design/chat-turn-liveness.md S2: when every restart attempt fails, the
    turn carries a ``turn_notice(gave_up)`` (persisted like any assistant row, so
    it shows after a reload) before the ALL_RETRIES_EXHAUSTED error."""
    for oom_limit in (99, 3):   # out of attempts; the OOM limit (a -9 reads as OOM)
        await _gives_up(oom_limit)


async def _gives_up(oom_limit):
    unit = _unit()
    unit._OOM_KILL_LIMIT = oom_limit

    async def dies(_q):
        raise RuntimeError(KILL)
        yield  # pragma: no cover

    budget = MagicMock(can_spawn=True, reason="")
    with patch("core.session_unit._oom_cooldown_until", 0), \
         patch("core.resource_monitor.resource_monitor.spawn_budget", return_value=budget), \
         patch.object(unit, "_build_retry_options", return_value=MagicMock()), \
         patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock), \
         patch.object(unit, "_transition"), \
         patch.object(unit, "_spawn", AsyncMock()), \
         patch.object(unit._streaming_orchestrator, "stream_query", side_effect=dies), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        events = [e async for e in unit._retry_with_resume("msg", MagicMock(), None, KILL, "")]
    reasons = [b.get("reason") for e in events if e.get("type") == "assistant"
               for b in e.get("content", []) if b.get("type") == "turn_notice"]
    assert reasons[0] == "restarted" and reasons.count("restarted") == 1   # once per turn
    assert reasons[-1] == "gave_up"
    codes = [e.get("code") for e in events if e.get("type") == "error"]
    assert codes == (["ALL_RETRIES_EXHAUSTED"] if oom_limit == 99 else ["OOM_LIMIT_REACHED"])
    i_gave = next(i for i, e in enumerate(events) if e.get("type") == "assistant"
                  and any(b.get("reason") == "gave_up" for b in e.get("content", [])))
    i_err = next(i for i, e in enumerate(events) if e.get("type") == "error")
    assert i_gave < i_err


@pytest.mark.asyncio
async def test_stop_during_the_backoff_ends_the_retry_without_a_respawn():
    """A user Stop between attempts (the unit is COLD, backing off) ends the retry:
    no respawn, no further stream (docs/design/chat-turn-liveness.md S2)."""
    unit = _unit()
    spawn = AsyncMock()
    stream = MagicMock()

    async def sleep_then_stop(_s):
        assert unit._retrying is True
        await unit.interrupt()          # the user's Stop, while COLD

    budget = MagicMock(can_spawn=True, reason="")
    with patch("core.session_unit._oom_cooldown_until", 0), \
         patch("core.resource_monitor.resource_monitor.spawn_budget", return_value=budget), \
         patch.object(unit, "_build_retry_options", return_value=MagicMock()), \
         patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock), \
         patch.object(unit, "_transition"), \
         patch.object(unit, "_spawn", spawn), \
         patch.object(unit._streaming_orchestrator, "stream_query", stream), \
         patch("asyncio.sleep", side_effect=sleep_then_stop):
        events = [e async for e in unit._retry_with_resume("msg", MagicMock(), None, KILL, "")]
    assert spawn.await_count == 0 and stream.call_count == 0
    assert events[-1] == {"_abort": True}
    assert unit._retrying is False


@pytest.mark.asyncio
async def test_a_continuation_with_its_attempts_used_up_gives_up_with_the_turn():
    unit = _unit()
    unit._last_send_options = MagicMock()
    unit._retry_count = unit.MAX_RETRY_ATTEMPTS
    with patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock):
        events = [e async for e in unit._retry_continuation(RuntimeError(KILL), "")]
    assert [b["reason"] for b in events[0]["content"]] == ["gave_up"]
    assert events[1]["code"] == "ALL_RETRIES_EXHAUSTED"


@pytest.mark.asyncio
async def test_an_abandoned_turn_is_not_a_failure():
    """TurnAbandoned is control flow: read_with_recovery passes it on without
    crashing the healthy CLI to COLD or retrying."""
    from core.cli_stream_reader import TurnAbandoned
    unit = _unit()
    unit._last_send_options = MagicMock()

    async def abandoned():
        raise TurnAbandoned("superseded")
        yield  # pragma: no cover
    crash = AsyncMock()
    with patch.object(unit, "_read_formatted_response", side_effect=abandoned), \
         patch.object(unit, "_crash_to_cold_async", crash):
        with pytest.raises(TurnAbandoned):
            _ = [e async for e in unit.read_with_recovery()]
    assert crash.await_count == 0


@pytest.mark.asyncio
async def test_stop_while_the_retry_spawns_never_sends_the_query():
    unit = _unit()
    stream = MagicMock()

    async def spawn_and_stop(*_a, **_k):
        await unit.interrupt()          # Stop lands while the fresh process starts

    budget = MagicMock(can_spawn=True, reason="")
    crash = AsyncMock()
    with patch("core.session_unit._oom_cooldown_until", 0), \
         patch("core.resource_monitor.resource_monitor.spawn_budget", return_value=budget), \
         patch.object(unit, "_build_retry_options", return_value=MagicMock()), \
         patch.object(unit, "_crash_to_cold_async", crash), \
         patch.object(unit, "_transition"), \
         patch.object(unit, "_spawn", AsyncMock(side_effect=spawn_and_stop)), \
         patch.object(unit._streaming_orchestrator, "stream_query", stream), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        events = [e async for e in unit._retry_with_resume("msg", MagicMock(), None, KILL, "")]
    assert stream.call_count == 0 and events[-1] == {"_abort": True}
    assert crash.await_args_list[-1].kwargs == {"clear_identity": False}


@pytest.mark.asyncio
async def test_stop_between_spawn_attempts_ends_the_spawn_retry():
    """The first spawn of a send retries too (_ensure_spawned): a Stop between its
    attempts ends it without another spawn."""
    unit = _unit()
    unit._user_stopped_current_turn = False
    spawn = AsyncMock(side_effect=RuntimeError(KILL))

    async def sleep_then_stop(_s):
        assert unit._retrying is True
        await unit.interrupt()

    cfg = MagicMock()
    cfg.get.side_effect = lambda k, d=None: False if k == "use_bedrock" else d
    with patch("core.app_config_manager.AppConfigManager.instance", return_value=cfg), \
         patch.object(unit, "_build_retry_options", return_value=MagicMock()), \
         patch.object(unit, "_crash_to_cold_async", new_callable=AsyncMock), \
         patch.object(unit, "_spawn", spawn), \
         patch("asyncio.sleep", side_effect=sleep_then_stop):
        events = [e async for e in unit._ensure_spawned(MagicMock(), None)]
    assert spawn.await_count == 1, "no spawn after the Stop"
    assert events[-1] == {"_abort": True} and unit._retrying is False


@pytest.mark.asyncio
async def test_the_stop_endpoint_reaches_a_retry_between_attempts():
    from core.session_router import SessionRouter
    r = SessionRouter.__new__(SessionRouter)
    unit = _unit()
    unit._retrying = True                     # COLD, backing off
    r.get_unit = lambda sid: unit
    with patch.object(SessionRouter, "_persist_stop_receipt", AsyncMock(return_value=[])):
        out = await r.interrupt_session("kill-1")
    assert unit._user_stopped_current_turn is True
    assert out.get("message") == "Interrupted"
