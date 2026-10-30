"""Output rate of the main model (core/stream_rate.py) and its wiring in the stream loop.

What is tested:
- the meter: tokens over writing time (first content_block_start → message_stop),
  summed over the turn's API calls; a call without a stop or without a block is left
  out; no completed call → no log line
- the real StreamingOrchestrator._read_formatted_response loop feeds it from the
  main agent's stream events only (a sub-agent's call is not counted) and logs one
  ``stream_rate`` line at the result
"""
from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock, patch

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage
from claude_agent_sdk.types import StreamEvent

from core.stream_rate import StreamRateMeter


def _call(m: StreamRateMeter, block_at: float, tokens: int, stop_at: float) -> None:
    m.message_start()
    m.block_start(block_at)
    m.block_start(block_at + 0.5)  # a later block does not move the start
    m.output_so_far(tokens // 2)
    m.output_so_far(tokens)  # the running count: the last value is the call's
    m.message_stop(stop_at)


def test_rate_is_tokens_over_writing_time_across_calls():
    m = StreamRateMeter()
    _call(m, 1.0, 50, 3.0)
    _call(m, 10.0, 30, 12.0)
    assert (m.calls, m.output_tokens, m.writing_s) == (2, 80, 4.0)
    assert m.log_line() == "stream_rate calls=2 out_tokens=80 writing_s=4.0 tok_per_s=20.0"


def test_incomplete_calls_are_left_out():
    m = StreamRateMeter()
    m.message_start()
    m.block_start(1.0)
    m.output_so_far(40)  # interrupted: no stop
    m.message_start()  # the next call starts over
    m.output_so_far(7)
    m.message_stop(5.0)  # no content block: no writing time
    assert m.log_line() is None
    _call(m, 6.0, 10, 8.0)
    assert (m.calls, m.output_tokens, m.writing_s) == (1, 10, 2.0)
    m.reset()
    assert m.log_line() is None


# ── wiring in the real stream loop ─────────────────────────────────────────

_CLOCK = {"t": 0.0}


class _Script:
    """Yields (clock, message) pairs, setting the orchestrator's clock first."""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise RuntimeError("script exhausted")
        t, msg = self._items.pop(0)
        _CLOCK["t"] = t
        return msg


def _ev(t, event, parent=None):
    return t, StreamEvent(uuid=f"e{t}", session_id="s", parent_tool_use_id=parent, event=event)


def _api_call(start, block, tokens, stop, parent=None):
    return [
        _ev(start, {"type": "message_start", "message": {}}, parent),
        _ev(block, {"type": "content_block_start", "index": 0,
                    "content_block": {"type": "text", "text": ""}}, parent),
        _ev(block + 0.1, {"type": "content_block_delta", "index": 0,
                          "delta": {"type": "text_delta", "text": "hi"}}, parent),
        _ev(stop - 0.1, {"type": "message_delta", "delta": {},
                         "usage": {"output_tokens": tokens}}, parent),
        _ev(stop, {"type": "message_stop"}, parent),
    ]


@pytest.mark.asyncio
async def test_stream_loop_logs_main_agent_rate_at_result(caplog):
    from core.session_unit import SessionState, SessionUnit
    from core.streaming_orchestrator import StreamingOrchestrator

    items = [(0.0, SystemMessage(subtype="init", data={"session_id": "s"}))]
    items += _api_call(0.0, 1.0, 50, 3.0)
    items += _api_call(3.5, 4.0, 1000, 5.0, parent="tu-agent")  # sub-agent: not counted
    items += _api_call(9.0, 10.0, 30, 12.0)
    items.append((12.5, ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=10, is_error=False,
        num_turns=1, session_id="s", stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 80}, total_cost_usd=0.0)))

    unit = SessionUnit(session_id="rate-sess", agent_id="default")
    unit.state = SessionState.STREAMING
    unit._streaming_start_time = unit._last_event_time = time.time()
    orch = StreamingOrchestrator(unit)
    unit._client = MagicMock()
    unit._client.receive_response = lambda: _Script(items)
    caplog.set_level(logging.INFO, logger="core.streaming_orchestrator")
    with patch("core.streaming_orchestrator.awake_time", lambda: _CLOCK["t"]):
        try:
            async for _ in orch._read_formatted_response():
                pass
        except Exception:  # noqa: BLE001 — the script may run dry after the result
            pass
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("stream_rate ")]
    assert lines == [
        "stream_rate calls=2 out_tokens=80 writing_s=4.0 tok_per_s=20.0 session_id=rate-sess"
    ], lines
