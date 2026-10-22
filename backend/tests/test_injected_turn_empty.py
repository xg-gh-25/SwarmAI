"""A CLI-initiated turn that ends with nothing ends quietly; a user turn still retries.

Incident 2026-10-22 (session e4f3397f): the user stopped a background agent; the
CLI opened its own turn for that agent's notification and, with the user's next
message already queued, ended it at once with a zero-usage ``success`` and no
model call (the transcript shows one API request for both). The sink ran that
buffered turn 40s later; the blank-turn guard read it as a Bedrock blank
response and raised "for retry" — but the injected path has no retry loop, so
the CLI was crash-killed to COLD.

Driven through the real ``StreamingOrchestrator._read_formatted_response`` with
the leak-guard test harness (real SDK message types).

Properties:
- injected turn + blank result: no raise, no kill, the turn ends with a result;
- the same blank result on a turn we sent still raises the retriable error.
"""
from __future__ import annotations

import asyncio
import types

from claude_agent_sdk import ResultMessage

from tests.test_tool_call_leak_guard import _drive, _make_orchestrator


def _blank_result() -> ResultMessage:
    return ResultMessage(
        subtype="success", duration_ms=0, duration_api_ms=0, is_error=False,
        num_turns=0, session_id="sdk-test", stop_reason=None,
        usage={"input_tokens": 0, "output_tokens": 0}, total_cost_usd=0.0,
    )


def test_a_blank_cli_initiated_turn_ends_quietly():
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    orch._parent._turn_stream = types.SimpleNamespace(kind="injected", opened_at=None)
    events, raised = asyncio.run(_drive(orch, [_blank_result()]))
    assert raised is None
    assert orch._parent._kill_calls["n"] == 0
    assert any(e.get("type") == "result" for e in events)


def test_a_blank_result_on_a_turn_we_sent_still_raises_for_retry():
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    orch._parent._turn_stream = types.SimpleNamespace(kind="user", opened_at=None)
    _events, raised = asyncio.run(_drive(orch, [_blank_result()]))
    assert raised is not None and "API returned empty response" in str(raised)
    # the CLI's own turn time (0ms), not "since the stream start" (huge here)
    assert "duration=0.0s" in str(raised)
