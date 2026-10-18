"""Sub-agent activity stays visible in the chat stream, and step results persist.

What is tested (real StreamingOrchestrator._read_formatted_response loop, SDK
message classes from claude-agent-sdk; the DB is mocked at db.messages.put):
- a sub-agent's tool_use (AssistantMessage.parent_tool_use_id set) is yielded
  stamped with `parent_tool_use_id`, so the UI nests it under its Agent step;
  its text/thinking and its stream deltas never reach the main reply
- a sub-agent's tool result is yielded live with the same parent stamp
- SDK Task* system messages become `subagent_progress` events keyed by the
  parent Agent tool_use id (TaskUpdatedMessage, which has no tool_use_id, is
  mapped through its task_id); a task with no parent emits nothing
- SessionRouter._persist_stream_event: live tool_result events are kept as
  COMPACT blocks (no content) and written as the prefix of the next assistant
  row; the yielded assistant event is unchanged; a terminal event flushes them
- the Agent step's summary prefers its short `description`
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import (
    AssistantMessage, SystemMessage, TaskNotificationMessage, TaskProgressMessage,
    TaskStartedMessage, TaskUpdatedMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage,
)
from claude_agent_sdk.types import StreamEvent

from core.session_router import SessionRouter
from core.session_unit import SessionState, SessionUnit
from core.streaming_orchestrator import StreamingOrchestrator
from core.tool_summarizer import summarize_tool_use


class _Script:
    def __init__(self, msgs):
        self._m = list(msgs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._m:
            return self._m.pop(0)
        raise RuntimeError("script exhausted")


def _task(cls, **kw):
    base = {"subtype": cls.__name__, "data": {}}
    if cls is not TaskUpdatedMessage:
        base.update(uuid="u", session_id="s")
    return cls(**base, **kw)


async def _run(msgs) -> list[dict]:
    from core import permission_manager as _pm

    unit = SessionUnit(session_id="sub-stream", agent_id="default")
    unit.state = SessionState.STREAMING
    unit._streaming_start_time = unit._last_event_time = time.time()
    orch = StreamingOrchestrator(unit)
    unit._client = MagicMock()
    unit._client.receive_response = lambda: _Script(msgs)
    events: list[dict] = []
    with patch.object(_pm.permission_manager, "get_session_queue", return_value=asyncio.Queue()):
        with pytest.raises(Exception):
            async for ev in orch._read_formatted_response():
                events.append(ev)
    return events


AGENT_ID = "tu-agent"


@pytest.mark.asyncio
async def test_subagent_steps_results_and_progress_stream_under_their_agent():
    events = await _run([
        SystemMessage(subtype="init", data={"session_id": "s"}),
        AssistantMessage(content=[ToolUseBlock(id=AGENT_ID, name="Agent", input={
            "description": "Review spec", "prompt": "long prompt"})], model="m"),
        _task(TaskStartedMessage, task_id="t1", description="Review spec", tool_use_id=AGENT_ID),
        StreamEvent(uuid="e1", session_id="s", parent_tool_use_id=AGENT_ID, event={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "SUB_AGENT_TEXT"}}),
        AssistantMessage(content=[TextBlock(text="SUB_AGENT_PROSE"),
                                  ToolUseBlock(id="sub1", name="Read", input={"file_path": "/r/a.py"})],
                         model="m", parent_tool_use_id=AGENT_ID),
        UserMessage(content=[ToolResultBlock(tool_use_id="sub1", content="1\tx\n2\ty\n", is_error=False)],
                    parent_tool_use_id=AGENT_ID),
        _task(TaskProgressMessage, task_id="t1", description="Review spec", tool_use_id=AGENT_ID,
              usage={"total_tokens": 10, "tool_uses": 1, "duration_ms": 4200}, last_tool_name="Read"),
        _task(TaskUpdatedMessage, task_id="t1", patch={"status": "killed"}),
        _task(TaskNotificationMessage, task_id="t1", status="completed", output_file="",
              summary="Spec matches", tool_use_id=AGENT_ID),
        _task(TaskStartedMessage, task_id="bg", description="background", tool_use_id=None),
    ])
    texts = [e for e in events if e["type"] in ("text_delta", "thinking_delta")]
    assert texts == []
    assistant_blocks = [b for e in events if e["type"] == "assistant" for b in e["content"]]
    assert all(b.get("text") != "SUB_AGENT_PROSE" for b in assistant_blocks)
    sub_tu = [b for b in assistant_blocks if b.get("id") == "sub1"]
    assert sub_tu and sub_tu[0]["parent_tool_use_id"] == AGENT_ID
    main_tu = [b for b in assistant_blocks if b.get("id") == AGENT_ID]
    assert main_tu and "parent_tool_use_id" not in main_tu[0]
    assert main_tu[0]["summary"] == "Agent: Review spec"

    results = [b for e in events if e["type"] == "tool_result" for b in e["content"]]
    assert [(b["tool_use_id"], b.get("parent_tool_use_id"), b["outcome"]) for b in results] == [
        ("sub1", AGENT_ID, "lines 1–2")]

    progress = [e for e in events if e["type"] == "subagent_progress"]
    assert [(p["parentToolUseId"], p["status"]) for p in progress] == [
        (AGENT_ID, "running"), (AGENT_ID, "running"), (AGENT_ID, "stopped"), (AGENT_ID, "completed")]
    assert progress[1]["toolUses"] == 1 and progress[1]["durationMs"] == 4200
    assert progress[1]["lastToolName"] == "Read"
    assert progress[3]["summary"] == "Spec matches"


def test_agent_summary_prefers_description():
    assert summarize_tool_use("Agent", {"description": "Fix tests", "prompt": "x" * 500}) == "Agent: Fix tests"
    assert summarize_tool_use("Agent", {"prompt": "do the thing"}) == "Agent: do the thing"


class _Router:
    """Just the router surface _persist_stream_event uses; the real
    _persist_assistant_blocks runs against a mocked db.messages.put."""
    _compact_tool_results = staticmethod(SessionRouter._compact_tool_results)
    _persist_assistant_blocks = staticmethod(SessionRouter._persist_assistant_blocks)
    _RESULT_FLUSH_EVENTS = SessionRouter._RESULT_FLUSH_EVENTS
    _persist_stream_event = SessionRouter._persist_stream_event


@pytest.mark.asyncio
async def test_step_results_persist_compact_with_the_next_assistant_row():
    put = AsyncMock()
    router, pending = _Router(), []
    tr_event = {"type": "tool_result", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "HUGE OUTPUT " * 500,
         "is_error": True, "truncated": True, "outcome": "make: *** no rule"},
        {"type": "tool_result", "tool_use_id": "s1", "content": "x", "is_error": False,
         "truncated": False, "outcome": "lines 1–2", "parent_tool_use_id": AGENT_ID},
    ]}
    asst = {"type": "assistant", "model": "m", "content": [{"type": "text", "text": "Fixed it."}]}
    with patch("database.db.messages.put", put):
        await router._persist_stream_event("sid", tr_event, pending, client_id="c-asst")
        assert put.await_count == 0  # waits for the next assistant row
        assert len(tr_event["content"][0]["content"]) > 1000  # the live event keeps its content
        await router._persist_stream_event("sid", asst, pending, client_id="c-asst")
    assert put.await_count == 1
    row = put.await_args.args[0]
    assert row["metadata"] == {"client_id": "c-asst"}
    assert row["content"] == [
        {"type": "tool_result", "tool_use_id": "a", "is_error": True, "truncated": False,
         "outcome": "make: *** no rule"},
        {"type": "tool_result", "tool_use_id": "s1", "is_error": False, "truncated": False,
         "outcome": "lines 1–2", "parent_tool_use_id": AGENT_ID},
        {"type": "text", "text": "Fixed it."},
    ]
    assert asst["content"] == [{"type": "text", "text": "Fixed it."}]  # stream unchanged
    assert pending == []


@pytest.mark.asyncio
async def test_results_left_at_turn_end_get_their_own_row():
    put = AsyncMock()
    router, pending = _Router(), []
    with patch("database.db.messages.put", put):
        await router._persist_stream_event("sid", {"type": "tool_result", "content": [
            {"type": "tool_result", "tool_use_id": "z", "is_error": False, "outcome": "done"}]}, pending)
        await router._persist_stream_event("sid", {"type": "heartbeat"}, pending)
        assert put.await_count == 0
        await router._persist_stream_event("sid", {"type": "result"}, pending)
    assert put.await_count == 1
    assert put.await_args.args[0]["content"] == [
        {"type": "tool_result", "tool_use_id": "z", "is_error": False, "truncated": False, "outcome": "done"}]
    assert pending == []


# ── The sub-agent's outcome rides its Agent tool_result (persisted) ───────────
# subagent_progress is stream-only, so after the turn (and on reload) the Agent
# step showed only its child steps and a failed / stopped sub-agent looked done.

@pytest.mark.asyncio
async def test_agent_result_carries_the_subagents_final_status():
    events = await _run([
        SystemMessage(subtype="init", data={"session_id": "s"}),
        AssistantMessage(content=[ToolUseBlock(id=AGENT_ID, name="Agent", input={
            "description": "Review spec"})], model="m"),
        _task(TaskStartedMessage, task_id="t1", description="Review spec", tool_use_id=AGENT_ID),
        _task(TaskProgressMessage, task_id="t1", description="Review spec", tool_use_id=AGENT_ID,
              usage={"total_tokens": 10, "tool_uses": 3, "duration_ms": 9100}, last_tool_name="Read"),
        _task(TaskUpdatedMessage, task_id="t1", patch={"status": "killed"}),
        _task(TaskNotificationMessage, task_id="t1", status="stopped", output_file="",
              summary="Stopped after 3 steps", tool_use_id=AGENT_ID),
        UserMessage(content=[ToolResultBlock(tool_use_id=AGENT_ID, content="partial", is_error=False)]),
    ])
    (res,) = [b for e in events if e["type"] == "tool_result" for b in e["content"]]
    assert res["tool_use_id"] == AGENT_ID
    assert res["subagent"] == {"status": "stopped", "toolUses": 3, "durationMs": 9100,
                               "summary": "Stopped after 3 steps"}


@pytest.mark.asyncio
async def test_agent_result_without_progress_has_no_subagent_and_others_never_do():
    events = await _run([
        SystemMessage(subtype="init", data={"session_id": "s"}),
        AssistantMessage(content=[ToolUseBlock(id=AGENT_ID, name="Agent", input={"description": "x"}),
                                  ToolUseBlock(id="r1", name="Read", input={"file_path": "/a"})], model="m"),
        UserMessage(content=[ToolResultBlock(tool_use_id=AGENT_ID, content="ok", is_error=False),
                             ToolResultBlock(tool_use_id="r1", content="1\tx\n", is_error=False)]),
    ])
    results = [b for e in events if e["type"] == "tool_result" for b in e["content"]]
    assert [("subagent" in b) for b in results] == [False, False]


def test_subagent_final_resolves_a_still_running_status_from_the_result():
    fin = StreamingOrchestrator._subagent_final
    assert fin({"status": "running", "toolUses": 2}, False) == {"toolUses": 2, "status": "completed"}
    assert fin({"status": "running"}, True) == {"status": "failed"}
    assert fin({"status": "failed", "summary": "  "}, False) == {"status": "failed"}
    assert fin(None, False) is None
    assert fin({}, False) is None


def test_note_subagent_keeps_the_latest_known_value_of_each_field():
    last: dict = {}
    note = StreamingOrchestrator._note_subagent
    note(last, {"parentToolUseId": "a", "status": "running", "toolUses": 1, "durationMs": 10})
    note(last, {"parentToolUseId": "a", "status": "stopped"})
    note(last, {"status": "running"})  # no parent: ignored
    assert last == {"a": {"status": "stopped", "toolUses": 1, "durationMs": 10}}


def test_compact_result_keeps_only_the_subagent_outcome_fields():
    (c,) = SessionRouter._compact_tool_results([{
        "type": "tool_result", "tool_use_id": AGENT_ID, "content": "BIG" * 1000, "is_error": False,
        "outcome": "done", "subagent": {"status": "failed", "toolUses": 4, "durationMs": 5,
                                        "summary": "s" * 400, "junk": "x"}}])
    assert c["subagent"] == {"status": "failed", "toolUses": 4, "durationMs": 5, "summary": "s" * 300}
    assert "content" not in c
