"""The prompt board and the read loop's persistent SDK read.

Replaces the per-session permission queue (items were TAKEN by a losing
``queue.get()`` and lost; a 15-30 s rescue papered over it) and the per-iteration
SDK read that was cancelled as a race loser (a message it had already taken was
lost). Properties:

- a prompt is on the board exactly while its waiter is live and undecided;
- reading the board takes nothing; a board-change future carries no data, so
  cancelling it is free; a publish between "register" and "read" is not missed;
- request_decision publishes and waits as one step and always cleans up;
- the read loop keeps ONE read per stream: asking again returns the same read,
  a finished read is consumed once, a read of an abandoned stream is cancelled.
"""

import asyncio

import pytest

from core.ask_question_manager import ask_question_manager as aqm
from core.permission_manager import permission_manager as pm
from core.streaming_orchestrator import StreamingOrchestrator

SID = "board-sess"


def _item(rid: str) -> dict:
    return {"sessionId": SID, "requestId": rid, "toolName": "Bash",
            "toolInput": {"command": "x"}, "reason": "r", "options": ["approve", "deny"]}


def test_publish_makes_a_live_open_prompt_and_reading_takes_nothing():
    pm.publish_prompt(SID, _item("p1"))
    assert pm.has_live_waiter("p1")
    assert [i["requestId"] for i in pm.open_prompts(SID)] == ["p1"]
    assert [i["requestId"] for i in pm.open_prompts(SID)] == ["p1"]  # still there


def test_decision_takes_the_prompt_off_the_board():
    pm.publish_prompt(SID, _item("p2"))
    pm.set_permission_decision("p2", "deny")
    assert pm.open_prompts(SID) == []


@pytest.mark.asyncio
async def test_change_future_fires_on_publish_and_cancelling_it_loses_nothing():
    fut = pm.wait_prompt_change(SID)
    pm.publish_prompt(SID, _item("p3"))
    assert fut.done()
    other = pm.wait_prompt_change(SID)
    other.cancel()                       # a reader that gave up
    assert [i["requestId"] for i in pm.open_prompts(SID)] == ["p3"]


@pytest.mark.asyncio
async def test_request_decision_returns_the_decision_and_cleans_up():
    task = asyncio.ensure_future(pm.request_decision(SID, _item("p4")))
    await asyncio.sleep(0)
    assert [i["requestId"] for i in pm.open_prompts(SID)] == ["p4"]
    pm.set_permission_decision("p4", "approve")
    assert await asyncio.wait_for(task, 2) == "approve"
    assert pm.open_prompts(SID) == [] and not pm.has_live_waiter("p4")


@pytest.mark.asyncio
async def test_cancelled_gate_leaves_no_ghost_prompt():
    task = asyncio.ensure_future(pm.request_decision(SID, _item("p5")))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pm.open_prompts(SID) == [] and not pm.has_live_waiter("p5")


def test_question_is_open_while_its_waiter_is_unanswered():
    aqm.register_waiter("tu_q")
    try:
        pm.publish_prompt(SID, {"kind": "ask_user_question", "sessionId": SID,
                                "tool_use_id": "tu_q", "questions": []})
        assert [i["tool_use_id"] for i in pm.open_prompts(SID)] == ["tu_q"]
        aqm.set_answer("tu_q", {"a": "b"})
        assert pm.open_prompts(SID) == []
    finally:
        aqm.discard_waiter("tu_q")


class _Gate:
    """An SDK stream whose next message arrives when ``release`` is set."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.release.wait()
        return "message"


def _orch() -> StreamingOrchestrator:
    o = StreamingOrchestrator.__new__(StreamingOrchestrator)
    o._sdk_read = None
    return o


@pytest.mark.asyncio
async def test_one_read_per_stream_survives_until_taken():
    orch, it = _orch(), _Gate()
    first = orch._sdk_read_for(it)
    assert orch._sdk_read_for(it) is first          # a second stream reuses it
    it.release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert first.done()
    assert orch._sdk_read_for(it) is first          # not lost, not replaced
    assert orch._take_sdk_read(first) == "message"
    assert orch._sdk_read_for(it) is not first      # taken once, then a new read


@pytest.mark.asyncio
async def test_read_of_an_abandoned_stream_is_cancelled():
    orch = _orch()
    old = orch._sdk_read_for(_Gate())
    orch._sdk_read_for(_Gate())
    await asyncio.sleep(0)
    assert old.cancelled()
    orch._discard_sdk_read()
    assert orch._sdk_read is None
