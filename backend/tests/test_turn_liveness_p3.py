"""chat-turn-liveness P3 (docs/design/chat-turn-liveness.md S2): the failure policy
outside the turn reader -- a TaskManager task answers a question as a continuation
of the paused turn, and a failed drain is retried later, a bounded number of times.

Methodology: the real TaskManager / SessionRouter with only the far leaf replaced
(session_router conversation generators, the pending-store calls); timers made
short so the delayed retry is observed directly.

- a task's question is answered with continue_with_answer (its tool_use_id, the
  answer keyed by question text), never a new message; the task completes
- a further question in the continuation is answered the same way
- a failed drain delivery schedules a delayed kick; it stops after the limit;
  a delivery forgets the count; a gone session forgets it
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import session_pending
from core.session_router import SessionRouter
from core.task_manager import TaskManager

Q1 = {"type": "ask_user_question", "toolUseId": "tu-1",
      "questions": [{"question": "Which file?", "options": []}]}
Q2 = {"type": "ask_user_question", "toolUseId": "tu-2",
      "questions": [{"question": "Sure?", "options": []}]}


class _Router:
    def __init__(self, continuations):
        self.continued: list[dict] = []
        self.sent: list[str] = []
        self._continuations = list(continuations)

    async def run_conversation(self, **kw):
        self.sent.append(kw.get("user_message"))
        yield {"type": "session_start", "sessionId": "s-task"}
        yield dict(Q1)

    async def continue_with_answer(self, session_id, answer, tool_use_id=None, **_kw):
        self.continued.append({"session_id": session_id, "answer": json.loads(answer),
                               "tool_use_id": tool_use_id})
        for ev in self._continuations.pop(0):
            yield ev


async def _run(router, replies):
    tm = TaskManager()
    task_id = "task-p3"
    tm._message_queues[task_id] = asyncio.Queue()
    for r in replies:
        await tm._message_queues[task_id].put({"message": r})
    updates: list[dict] = []
    fake_db = MagicMock()
    fake_db.tasks.update = AsyncMock(side_effect=lambda _id, u: updates.append(u))
    fake_db.tasks.get = AsyncMock(return_value={"agent_id": "default"})
    with patch("core.task_manager.db", fake_db), \
            patch("core.task_manager.session_registry") as reg:
        reg.session_router = router
        await asyncio.wait_for(tm._run_task(task_id, "default", "go", None, False, False), 5)
    return updates


@pytest.mark.asyncio
async def test_a_task_answers_its_question_as_a_continuation():
    router = _Router([[{"type": "result"}]])
    updates = await _run(router, ["main.py"])
    assert router.sent == ["go"], "the answer must not be sent as a new message"
    assert router.continued == [{"session_id": "s-task", "answer": {"Which file?": "main.py"},
                                 "tool_use_id": "tu-1"}]
    assert updates[-1]["status"] == "completed"


@pytest.mark.asyncio
async def test_a_further_question_is_answered_the_same_way():
    router = _Router([[dict(Q2)], [{"type": "result"}]])
    updates = await _run(router, ["main.py", "yes"])
    assert [c["tool_use_id"] for c in router.continued] == ["tu-1", "tu-2"]
    assert router.continued[1]["answer"] == {"Sure?": "yes"}
    assert updates[-1]["status"] == "completed"


def _router() -> SessionRouter:
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


@pytest.mark.asyncio
async def test_a_failed_drain_is_retried_later_then_given_up(monkeypatch):
    r = _router()
    r._units["s-dr"] = MagicMock()
    kicks: list[str] = []
    monkeypatch.setattr(r, "kick", lambda sid: kicks.append(sid))
    monkeypatch.setattr(SessionRouter, "_DRAIN_RETRY_DELAYS", (0.01, 0.01, 0.01))
    monkeypatch.setattr(session_pending, "rollback_claim_batch", AsyncMock())
    monkeypatch.setattr(session_pending, "mark_sent_batch", AsyncMock())
    for _ in range(4):
        await r._finalize_pending_delivery("s-dr", [1], delivered_ok=False, saw_error="E")
        await asyncio.sleep(0.05)
    assert kicks == ["s-dr"] * 3, "three delayed retries, then the messages stay queued"
    assert "s-dr" not in r._drain_retries
    # a delivery forgets the count: the next failure starts over
    await r._finalize_pending_delivery("s-dr", [1], delivered_ok=False, saw_error="E")
    await r._finalize_pending_delivery("s-dr", [2], delivered_ok=True, saw_error=None)
    assert "s-dr" not in r._drain_retries


@pytest.mark.asyncio
async def test_a_gone_session_is_forgotten(monkeypatch):
    r = _router()
    kicks: list[str] = []
    monkeypatch.setattr(r, "kick", lambda sid: kicks.append(sid))
    monkeypatch.setattr(SessionRouter, "_DRAIN_RETRY_DELAYS", (0.01, 0.01, 0.01))
    r._retry_drain_later("s-gone")          # the session is not (any more) a unit
    await asyncio.sleep(0.05)
    assert kicks == [] and "s-gone" not in r._drain_retries


def test_every_drain_rollback_schedules_the_retry():
    """Both places a drain's claim is rolled back (the delivery raised; it ended
    undelivered) schedule the delayed retry."""
    import inspect
    src = inspect.getsource(SessionRouter.drain_pending) + inspect.getsource(
        SessionRouter._finalize_pending_delivery)
    rollbacks = src.count("await session_pending.rollback_claim_batch(")
    assert rollbacks >= 2
    import re
    tails = re.findall(r"await session_pending\.rollback_claim_batch\([^)]*\)\n(?:.*\n){0,6}?\s*self\._retry_drain_later\(session_id\)", src)
    assert len(tails) == rollbacks


@pytest.mark.asyncio
async def test_a_delivery_or_shutdown_cancels_the_pending_retry(monkeypatch):
    r = _router()
    r._units["s-c"] = MagicMock()
    kicks: list[str] = []
    monkeypatch.setattr(r, "kick", lambda sid: kicks.append(sid))
    monkeypatch.setattr(SessionRouter, "_DRAIN_RETRY_DELAYS", (0.03, 0.03, 0.03))
    monkeypatch.setattr(session_pending, "mark_sent_batch", AsyncMock())
    r._retry_drain_later("s-c")
    await r._finalize_pending_delivery("s-c", [1], delivered_ok=True, saw_error=None)
    r._retry_drain_later("s-c")
    for sid in list(r._drain_retry_timers):
        r._forget_drain_retry(sid)          # what disconnect_all does first
    await asyncio.sleep(0.08)
    assert kicks == [] and not r._drain_retry_timers and not r._drain_retries


def test_disconnect_all_cancels_the_retry_timers_before_the_worker():
    import inspect
    src = inspect.getsource(SessionRouter.disconnect_all)
    assert src.index("_forget_drain_retry(") < src.index("self._drain_worker_task.cancel()")


def test_a_structured_reply_answers_each_question_its_own_way():
    q = {"questions": [{"question": "A?"}, {"question": "B?"}]}
    assert json.loads(TaskManager._answer_for(q, json.dumps({"A?": "x", "B?": "y"}))) == {"A?": "x", "B?": "y"}
    assert json.loads(TaskManager._answer_for(q, "both fine")) == {"A?": "both fine", "B?": "both fine"}
    # a JSON reply naming other questions is plain text
    assert json.loads(TaskManager._answer_for(q, json.dumps({"C?": "z"}))) == {
        "A?": '{"C?": "z"}', "B?": '{"C?": "z"}'}


def test_a_reply_given_as_content_answers_with_its_text():
    assert TaskManager._reply_text({"message": None, "content": [
        {"type": "text", "text": "main.py"}, {"type": "image", "source": {}}]}) == "main.py"
    assert TaskManager._reply_text({"message": "x", "content": [{"type": "text", "text": "y"}]}) == "x"


@pytest.mark.asyncio
async def test_a_terminally_dropped_drain_forgets_its_retry(monkeypatch):
    r = _router()
    r._units["s-t"] = MagicMock()
    monkeypatch.setattr(r, "kick", lambda sid: None)
    monkeypatch.setattr(session_pending, "mark_sent_batch", AsyncMock())
    r._retry_drain_later("s-t")
    await r._finalize_pending_delivery("s-t", [1], delivered_ok=False, saw_error="E400", terminal=True)
    assert "s-t" not in r._drain_retries and "s-t" not in r._drain_retry_timers
