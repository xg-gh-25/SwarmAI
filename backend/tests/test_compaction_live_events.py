"""Live compaction visibility: the CLI's compaction SystemMessages become SSE events.

What is tested:
    The CLI streams ``status`` (status=compacting), then ``status`` (status=None,
    compact_result=...), then ``compact_boundary`` (compact_metadata with sizes)
    — shapes probed on the real bundled CLI 2026-10-17. The orchestrator used to
    drop all three, so a 1-2 minute compaction looked like a hang and the notice
    arrived only after ``result``. Now:
      * status=compacting       → ``context_compacting`` phase=started (live)
      * compact_result != success → ``context_compacting`` phase=failed
      * compact_boundary        → ``context_compacted`` with pre/post sizes,
        clears the PreCompact flag (no duplicate turn-end notice) and activates
        the CompactionGuard on the real signal.

Methodology:
    - Unit: ``StreamingOrchestrator._compaction_events`` on a real SessionUnit.
    - Wiring: drive the REAL ``_read_formatted_response`` loop with fake SDK
      messages and assert the events come out mid-stream, before any result.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from unittest.mock import MagicMock, patch

import pytest
from claude_agent_sdk import SystemMessage

from core.compaction_guard import GuardPhase
from core.prompt_builder import PromptBuilder
from core.session_unit import SessionState, SessionUnit
from core.streaming_orchestrator import StreamingOrchestrator

BOUNDARY = {
    "type": "system", "subtype": "compact_boundary",
    "compact_metadata": {"trigger": "auto", "pre_tokens": 752_000,
                         "post_tokens": 41_000, "duration_ms": 95_400},
}


def _unit() -> SessionUnit:
    unit = SessionUnit(session_id="live-compact", agent_id="default")
    unit.state = SessionState.STREAMING
    unit._hook_session_context = {"_compact_notice_pending": True, "_compacted": True}
    return unit


class TestCompactionEvents:
    def test_status_compacting_emits_started(self):
        orch = StreamingOrchestrator(_unit())
        evs = orch._compaction_events("status", {"status": "compacting"})
        assert evs == [PromptBuilder.build_compacting_notice()]
        assert evs[0]["type"] == "context_compacting" and evs[0]["phase"] == "started"

    def test_status_success_end_emits_nothing(self):
        orch = StreamingOrchestrator(_unit())
        assert orch._compaction_events(
            "status", {"status": None, "compact_result": "success"}) == []

    def test_status_failure_emits_failed(self):
        orch = StreamingOrchestrator(_unit())
        evs = orch._compaction_events("status", {"status": None, "compact_result": "error"})
        assert evs[0]["phase"] == "failed"

    def test_unrelated_status_emits_nothing(self):
        orch = StreamingOrchestrator(_unit())
        assert orch._compaction_events("status", {"status": "requesting"}) == []

    def test_boundary_emits_sized_notice_and_clears_fallback_flag(self):
        unit = _unit()
        orch = StreamingOrchestrator(unit)
        evs = orch._compaction_events("compact_boundary", BOUNDARY)
        evt = evs[0]
        assert evt["type"] == "context_compacted" and evt["trigger"] == "auto"
        assert evt["message"].startswith(PromptBuilder.COMPACT_NOTICE_TEXT)
        assert "752K → 41K tokens in 95s" in evt["message"]
        assert (evt["preTokens"], evt["postTokens"]) == (752_000, 41_000)
        # turn-end fallback must not repeat it; post_compact_injection's flag untouched
        assert unit._hook_session_context["_compact_notice_pending"] is False
        assert unit._hook_session_context["_compacted"] is True
        assert unit._compaction_guard.phase == GuardPhase.ACTIVE

    def test_turn_end_fallback_silent_after_live_notice(self):
        unit = _unit()
        unit.state = SessionState.IDLE
        StreamingOrchestrator(unit)._compaction_events("compact_boundary", BOUNDARY)
        evs = unit._emit_post_stream_metadata(usage=None, num_turns=1)
        assert not [e for e in evs if e.get("type") == "context_compacted"]

    def test_boundary_without_sizes_keeps_fixed_text(self):
        evt = PromptBuilder.build_compact_boundary_notice({"trigger": "manual"})
        assert evt["message"] == PromptBuilder.COMPACT_NOTICE_TEXT
        assert evt["trigger"] == "manual"


class _StopMarker(Exception):
    """Raised after the scripted messages so the loop ends without a result."""


class _Script:
    def __init__(self, msgs):
        self._msgs = list(msgs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._msgs:
            return self._msgs.pop(0)
        raise _StopMarker()


def _sys(subtype, **data):
    return SystemMessage(subtype=subtype, data={"type": "system", "subtype": subtype, **data})


@pytest.mark.asyncio
async def test_read_loop_yields_compaction_events_mid_stream():
    """The real read loop surfaces started → compacted live, in CLI order."""
    from core import permission_manager as _pm_mod

    unit = _unit()
    unit._streaming_start_time = time.time()
    unit._last_event_time = time.time()
    orch = StreamingOrchestrator(unit)
    unit._client = MagicMock()
    unit._client.receive_response = lambda: _Script([
        _sys("init", session_id="sdk-1"),
        _sys("status", status="compacting"),
        _sys("status", status=None, compact_result="success"),
        _sys("init", session_id="sdk-1"),
        _sys("compact_boundary", compact_metadata=BOUNDARY["compact_metadata"]),
    ])
    events = []
    with contextlib.nullcontext():
        # The script ends with _StopMarker (possibly re-wrapped by the loop's
        # error handling) — only the events yielded before it matter here.
        with pytest.raises(Exception):
            async for ev in orch._read_formatted_response():
                events.append(ev)
    kinds = [e["type"] for e in events if e["type"].startswith("context_compact")]
    assert kinds == ["context_compacting", "context_compacted"]


# ── Real context size: last request's usage, not the per-turn average ──

def _warning(events):
    return next(e for e in events if e.get("type") == "context_warning")


class TestLastCallUsage:
    def test_last_request_usage_drives_the_warning(self):
        """20 requests summing 5M tokens average 250K (25%), but the LAST request
        carried 900K — the real context is 90% → critical."""
        unit = _unit()
        unit.state = SessionState.IDLE
        unit._model_name = "claude-opus-5-5"
        unit._last_call_usage = {"input_tokens": 10, "cache_read_input_tokens": 899_990}
        evs = unit._emit_post_stream_metadata(
            {"input_tokens": 0, "cache_read_input_tokens": 5_000_000}, num_turns=20)
        w = _warning(evs)
        assert (w["pct"], w["level"]) == (90, "critical")
        assert unit._last_call_usage is None  # consumed once

    def test_falls_back_to_average_without_last_call(self):
        unit = _unit()
        unit.state = SessionState.IDLE
        unit._model_name = "claude-opus-5-5"
        evs = unit._emit_post_stream_metadata(
            {"input_tokens": 0, "cache_read_input_tokens": 5_000_000}, num_turns=20)
        assert _warning(evs)["pct"] == 25


@pytest.mark.asyncio
async def test_read_loop_records_main_agent_usage_only():
    """The orchestrator keeps the main agent's per-request usage; a sub-agent's
    AssistantMessage (its own, smaller context) must not overwrite it."""
    from claude_agent_sdk import AssistantMessage, TextBlock
    from core import permission_manager as _pm_mod

    unit = _unit()
    unit._streaming_start_time = time.time()
    unit._last_event_time = time.time()
    orch = StreamingOrchestrator(unit)
    unit._client = MagicMock()
    main = AssistantMessage(content=[TextBlock(text="working")], model="m",
                            usage={"input_tokens": 5, "cache_read_input_tokens": 700_000})
    sub = AssistantMessage(content=[TextBlock(text="sub")], model="m",
                           parent_tool_use_id="tu-agent",
                           usage={"input_tokens": 5, "cache_read_input_tokens": 20_000})
    unit._client.receive_response = lambda: _Script(
        [_sys("init", session_id="sdk-1"), main, sub])
    with contextlib.nullcontext():
        with pytest.raises(Exception):
            async for _ev in orch._read_formatted_response():
                pass
    assert unit._last_call_usage == main.usage


@pytest.mark.asyncio
async def test_pre_compact_hook_reads_trigger_from_dict_input():
    """SDK hook input is a dict; a manual /compact must be labeled manual."""
    from core.hook_builder import build_hooks

    pm = MagicMock()
    pm.is_command_approved = MagicMock(return_value=False)
    ctx: dict = {"sdk_session_id": "s"}
    hooks, _, _ = await build_hooks(
        agent_config={"enable_tool_logging": False, "global_user_mode": True},
        enable_skills=False, enable_mcp=False, resume_session_id=None,
        session_context=ctx, permission_manager=pm,
    )
    for matcher in hooks["PreCompact"]:
        for fn in matcher.hooks:
            await fn({"hook_event_name": "PreCompact", "trigger": "manual",
                      "custom_instructions": None}, None, None)
    assert ctx["_compact_trigger"] == "manual"
    assert ctx["_compact_notice_pending"] is True
