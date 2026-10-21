"""Live tool results reach the UI, each with a one-line outcome.

What is tested:
- summarize_tool_result: per-tool one-liners from the FULL result, using the real
  CLI result shapes (Read is "N\\t..." numbered lines; Grep/Glob "Found N files"
  or match lines; Bash plain output; errors).
- The real _read_formatted_response loop: a CLI tool result arrives in a
  UserMessage and is now yielded as a `tool_result` SSE event (it used to be
  dropped, so every step span until the turn ended). A result whose tool_use was
  never seen is not. Sub-agent nesting and the compact persistence of these
  events are covered in test_subagent_stream.py.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from claude_agent_sdk import AssistantMessage, SystemMessage, ToolResultBlock, ToolUseBlock, UserMessage

from core.session_unit import SessionState, SessionUnit
from core.streaming_orchestrator import StreamingOrchestrator
from core.tool_summarizer import summarize_tool_result, tool_result_text


class TestSummarizeToolResult:
    def test_read_reports_line_range(self):
        text = "".join(f"{n}\tline {n}\n" for n in range(120, 241))
        assert summarize_tool_result("Read", text, False) == "lines 120–240"

    def test_grep_counts_and_passthrough(self):
        assert summarize_tool_result("Grep", "a.py:1:x\nb.py:9:y\n", False) == "2 matches"
        assert summarize_tool_result("Grep", "Found 7 files\na.py", False) == "Found 7 files"
        assert summarize_tool_result("Glob", "No files found", False) == "no matches"

    def test_bash_last_output_line(self):
        assert summarize_tool_result("Bash", "collecting\n\n12 passed in 3.1s\n", False) == "12 passed in 3.1s"
        assert summarize_tool_result("Bash", "", False) == "no output"

    def test_error_first_line(self):
        assert summarize_tool_result("Bash", "PreToolUse:Bash hook error: denied\nmore", True) == \
            "PreToolUse:Bash hook error: denied"

    def test_edit_saved_and_list_content(self):
        assert summarize_tool_result("Edit", "The file x has been updated", False) == "saved"
        assert tool_result_text([{"type": "text", "text": "a"}, {"type": "image"}]) == "a"


class _Stop(Exception):
    pass


class _Script:
    def __init__(self, msgs):
        self._m = list(msgs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._m:
            return self._m.pop(0)
        raise _Stop()


@pytest.mark.asyncio
async def test_read_loop_yields_live_tool_result_with_outcome():
    from core import permission_manager as _pm

    unit = SessionUnit(session_id="live-tr", agent_id="default")
    unit.state = SessionState.STREAMING
    unit._streaming_start_time = unit._last_event_time = time.time()
    orch = StreamingOrchestrator(unit)
    unit._client = MagicMock()
    read_out = "".join(f"{n}\tx\n" for n in range(1, 41))
    unit._client.receive_response = lambda: _Script([
        SystemMessage(subtype="init", data={"session_id": "s"}),
        AssistantMessage(content=[ToolUseBlock(id="tu1", name="Read", input={"file_path": "/a.py"})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id="tu1", content=read_out, is_error=False)]),
        UserMessage(content=[ToolResultBlock(tool_use_id="sub1", content="x", is_error=False)],
                    parent_tool_use_id="tu-agent"),
    ])
    events = []
    with patch.object(_pm.permission_manager, "get_session_queue", return_value=asyncio.Queue()):
        with pytest.raises(Exception):
            async for ev in orch._read_formatted_response():
                events.append(ev)
    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 1
    blk = results[0]["content"][0]
    assert (blk["tool_use_id"], blk["is_error"], blk["outcome"]) == ("tu1", False, "lines 1–40")


async def _drain(orch, unit, msgs):
    from core import permission_manager as _pm
    unit._client.receive_response = lambda: _Script(msgs)
    events = []
    with patch.object(_pm.permission_manager, "get_session_queue", return_value=asyncio.Queue()):
        with pytest.raises(Exception):
            async for ev in orch._read_formatted_response():
                events.append(ev)
    return [e for e in events if e["type"] == "tool_result"]


@pytest.mark.asyncio
async def test_approved_call_result_survives_the_permission_pause():
    """The tool_use arrives in the send stream; after the user approves, the
    result arrives in a SECOND _read_formatted_response call (the continue
    stream). It must still be yielded, or the turn renders as Stopped."""
    unit = SessionUnit(session_id="carry-tr", agent_id="default")
    unit.state = SessionState.STREAMING
    unit._streaming_start_time = unit._last_event_time = time.time()
    orch = StreamingOrchestrator(unit)
    unit._client = MagicMock()
    first = await _drain(orch, unit, [
        AssistantMessage(content=[ToolUseBlock(id="tu-perm", name="Bash", input={"command": "echo approved"})],
                         model="m"),
    ])
    assert first == []
    unit.state = SessionState.STREAMING
    unit._last_event_time = time.time()
    second = await _drain(orch, unit, [
        UserMessage(content=[ToolResultBlock(tool_use_id="tu-perm", content="done", is_error=False)]),
    ])
    assert [b["tool_use_id"] for r in second for b in r["content"]] == ["tu-perm"]


@pytest.mark.asyncio
async def test_a_new_turn_forgets_the_previous_turns_tool_ids():
    unit = SessionUnit(session_id="carry-reset", agent_id="default")
    orch = StreamingOrchestrator(unit)
    orch._turn_carry["tool_names"]["old"] = "Bash"
    orch.reset_turn_carry()
    assert orch._turn_carry["tool_names"] == {}
