"""The error_max_turns Result: the turn ends gently and the CLI stays owned.

When the CLI hits max_turns it emits ResultMessage(is_error=True,
subtype="error_max_turns"). The real StreamingOrchestrator._read_formatted_response
must treat it as a pause, not an error: a `turn_limit_reached` event, a `result`
with subtype `turn_limit_reached`, the unit back to IDLE, and the CLI process
kept (it does not exit after this Result). The max_turns values themselves are
pinned in test_session_healing.py (DESKTOP_MAX_TURNS / CHANNEL_MAX_TURNS).
"""
from __future__ import annotations

import pytest

from core.session_unit import SessionState, SessionUnit


# ═══════════════════════════════════════════════════════════════════
# The CLI process stays owned after error_max_turns (design chat-steer E13c/E14)
# ═══════════════════════════════════════════════════════════════════
# The CLI does not exit after error_max_turns: it reports idle and keeps reading
# stdin. The branch used to clear _client / _wrapper on the premise that it had
# exited, which left the live CLI unreachable (no pid → no RSS check, no kill, the
# orphan reaper only takes ppid==1) until the daemon restarted.
# Reverting the fix (putting back `_client = None` / `_wrapper = None` in the
# error_max_turns branch of StreamingOrchestrator._read_formatted_response) turns
# both tests below red.


def _max_turns_result():
    from claude_agent_sdk import ResultMessage

    return ResultMessage(
        subtype="error_max_turns", duration_ms=10, duration_api_ms=10, is_error=True,
        num_turns=1, session_id="sdk-test", usage={"input_tokens": 1, "output_tokens": 1},
    )


async def _drive_to_turn_limit():
    from unittest.mock import AsyncMock, patch

    from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage

    import database
    from tests.test_tool_call_leak_guard import _drive, _make_orchestrator

    orch = _make_orchestrator()
    wrapper = object()
    orch._parent._wrapper = wrapper
    orch._parent._last_turn_clean = False  # set on STREAMING entry in production
    msgs = [
        AssistantMessage(content=[ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok", is_error=False)]),
        _max_turns_result(),
    ]
    with patch("core.usage_accounting.record_sync", lambda rows: None):
        events, raised = await _drive(orch, msgs)
    return orch, wrapper, events, raised


@pytest.mark.asyncio
async def test_error_max_turns_keeps_the_cli_owned():
    orch, wrapper, events, raised = await _drive_to_turn_limit()
    assert raised is None
    assert any(e.get("type") == "turn_limit_reached" for e in events)
    assert [e for e in events if e.get("type") == "result"][-1]["subtype"] == "turn_limit_reached"
    assert orch._parent.state == SessionState.IDLE
    # the live process stays reachable: same client, same wrapper (pid source)
    assert orch._parent._client is not None
    assert orch._parent._wrapper is wrapper
    assert orch._parent._last_turn_clean is False


@pytest.mark.asyncio
async def test_next_send_after_error_max_turns_recycles_through_the_poison_guard():
    """After the branch, the real poison-guard predicate says recycle: send() then
    kills the process through _crash_to_cold_async → _force_kill and respawns with
    --resume (send path covered by test_session_unit_cleanliness TestAC2)."""
    orch, _wrapper, _events, _raised = await _drive_to_turn_limit()
    unit = SessionUnit.__new__(SessionUnit)
    unit.session_id = "sess-test"
    unit._adopted_prewarm_fresh = False
    unit.state = orch._parent.state
    unit._client = orch._parent._client
    unit._last_turn_clean = orch._parent._last_turn_clean
    assert unit._should_poison_guard_recycle() is True
