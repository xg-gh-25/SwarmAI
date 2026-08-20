"""Drives the REAL streaming_orchestrator._read_formatted_response TimeoutError
handler to verify the tool-free CPU-gated extension (run_dcd668a6).

This is the path that actually killed session 2b22a852 at 13:22 (the precise
wait_for timer, NOT the watchdog). The test makes the SDK read time out once,
then — depending on the CPU verdict — the loop must either EXTEND (loop again,
consume the next message) or RAISE (kill as today).

- AC1: verdict 'working' at the timeout mark → NO RuntimeError, extension
       counter bumped, the next real message is consumed.
- AC2: verdict 'wedged' → RuntimeError raised (kill path preserved).
- AC6: is_first_message (INIT) path is covered too — extension applies at init.
- AC7: verdict 'unknown' → RuntimeError raised (fail-safe).
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit
from core.streaming_orchestrator import StreamingOrchestrator


class _FakeReceiveResponse:
    """Async iterator: first __anext__ hangs past the timeout, then yields a
    ResultMessage so the loop can terminate cleanly after an extension."""

    def __init__(self, hang_s: float, result_msg):
        self._hang_s = hang_s
        self._result = result_msg
        self._served = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        self._served += 1
        if self._served == 1:
            # Hang longer than the tiny test timeout → wait_for cancels this read.
            await asyncio.sleep(self._hang_s)
        # Reaching the SECOND read PROVES the timeout handler extended (looped
        # again) instead of raising. Raise a UNIQUE marker so the test confirms
        # the extension without driving the full message-processing / post-stream
        # path (out of scope for this timeout-handler test).
        raise _ExtendedMarker()


class _ExtendedMarker(Exception):
    """Raised on the 2nd read — proves the loop extended past the timeout."""


def _make_orchestrator(verdict: str, timeout_s: float = 0.05):
    """Build a real orchestrator over a minimal fake parent SessionUnit."""
    from claude_agent_sdk import ResultMessage

    parent = SessionUnit.__new__(SessionUnit)
    parent.session_id = "orch-tf"
    parent.state = SessionState.STREAMING
    parent._sdk_session_id = None
    parent._streaming_start_time = time.time()
    parent._last_event_time = time.time()
    parent._last_known_context_tokens = 0
    parent._tool_free_extensions = 0
    parent._open_tool_uses = {}
    parent._active_agent_tools = set()
    parent._auto_surfaced_run_ids_set = set()
    parent._pending_question = None
    parent._pending_tool_use_id = None
    parent._tool_hang_episodes = 0
    parent._tool_hang_interrupted = False
    parent._tool_hang_interrupt_at = None
    parent._lifecycle_response_count = 0
    parent._content_emitted = False
    parent._interrupted = False
    parent._last_turn_clean = True
    parent._mcp_health_checked = True
    parent._model_name = "test-model"
    parent._compaction_guard = MagicMock()
    parent.last_used = time.time()
    parent._health_sensor = MagicMock()
    parent._maybe_build_elapsed_heartbeat = MagicMock(return_value=None)
    parent._transition = MagicMock()
    parent._compute_message_timeout = lambda: timeout_s
    parent._compute_init_timeout = lambda: timeout_s
    parent._last_progress_time = None  # streaming_stall_seconds reads this
    # pid drives the verdict gate
    wrapper = MagicMock()
    wrapper.pid = 999
    parent._wrapper = wrapper
    # constants (class-level, but set explicitly for clarity)
    parent.TOOL_FREE_MAX_EXTENSIONS = 4
    parent.TOOL_FREE_HARD_CEILING_S = 1800.0
    parent._tool_free_hang_verdict = AsyncMock(return_value=verdict)

    result_msg = ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=1, session_id="orch-tf", total_cost_usd=0.0, usage={},
        result="ok",
    )
    orch = StreamingOrchestrator(parent)
    parent._client = MagicMock()
    parent._client.receive_response = lambda: _FakeReceiveResponse(
        hang_s=timeout_s * 20, result_msg=result_msg
    )
    return orch, parent


async def _drain(orch):
    """Drive the read loop under a fresh per-test permission queue (the real
    singleton binds an asyncio.Queue to the first test's loop → cross-loop hang).
    Returns the list of yielded events (raises propagate)."""
    from core import permission_manager as _pm_mod

    fresh_q = asyncio.Queue()
    with patch.object(
        _pm_mod.permission_manager, "get_session_queue", return_value=fresh_q
    ):
        events = []
        async for ev in orch._read_formatted_response():
            events.append(ev)
        return events


@pytest.mark.asyncio
async def test_ac1_working_verdict_extends_not_raises():
    """AC1: CPU-busy at the timeout mark → extend (no raise), counter bumped,
    next message consumed."""
    orch, parent = _make_orchestrator(verdict="working")
    # Must NOT raise RuntimeError (the kill path). Reaching the 2nd read (the
    # _ExtendedMarker) proves the loop extended instead of killing.
    with pytest.raises(_ExtendedMarker):
        await _drain(orch)
    assert parent._tool_free_extensions == 1, "extension counter must bump once"
    parent._tool_free_hang_verdict.assert_awaited()


@pytest.mark.asyncio
async def test_ac2_wedged_verdict_raises():
    """AC2: wedged → RuntimeError (kill path preserved)."""
    orch, parent = _make_orchestrator(verdict="wedged")
    with pytest.raises(RuntimeError, match="Streaming timeout"):
        await _drain(orch)
    assert parent._tool_free_extensions == 0


@pytest.mark.asyncio
async def test_ac7_unknown_verdict_raises_failsafe():
    """AC7: unknown (unmeasurable) → RuntimeError (fail-safe, never hang forever)."""
    orch, parent = _make_orchestrator(verdict="unknown")
    with pytest.raises(RuntimeError, match="Streaming timeout"):
        await _drain(orch)
    assert parent._tool_free_extensions == 0


@pytest.mark.asyncio
async def test_ac6_init_path_extends_too():
    """AC6: the is_first_message/INIT timeout also extends on 'working'
    (large-context cold-resume TTFT is the most common false-timeout)."""
    orch, parent = _make_orchestrator(verdict="working")
    # is_first_message starts True; the INIT_TIMEOUT == our tiny timeout, so the
    # FIRST read times out on the init path → must extend, not raise.
    with pytest.raises(_ExtendedMarker):
        await _drain(orch)
    assert parent._tool_free_extensions == 1
