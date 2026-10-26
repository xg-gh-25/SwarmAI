"""Swarm Out bridge, Hive side (core/swarm_out_bridge.py).

The bridge must MIRROR only prompts that are open on the board, and deliver a valid
answer the way the web page does — through the session router's continuation, which
reads the rest of the turn (a unit waiting on a person has stopped reading). It must
never deliver twice, deliver an answer filed under another request, or a decision
outside approve/deny; after a restart it closes the asks nobody waits on any more.
"""
import asyncio
import json

import pytest

from core.permission_manager import PermissionManager
from core.swarm_out_bridge import Bridge, identity_from


class FakeStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, key, body):
        self.objects[key] = body

    def list_keys(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def get(self, key):
        return self.objects.get(key)


class Unit:
    def __init__(self, session_id, pending):
        self.session_id = session_id
        self._pending_tool_use_id = pending


class FakeRouter:
    """Records continuations and how far each generator was read."""
    def __init__(self):
        self.units = []
        self.calls = []
        self.read_to_end = []

    def list_units(self):
        return self.units

    async def _gen(self, tag):
        for i in range(3):
            yield {"type": "assistant", "i": i}
        self.read_to_end.append(tag)

    def continue_with_answer(self, session_id, answer, tool_use_id=None, answered_by="person"):
        self.calls.append(("answer", session_id, json.loads(answer), tool_use_id, answered_by))
        return self._gen(tool_use_id)

    def continue_with_cmd_permission(self, session_id, request_id, allowed):
        self.calls.append(("permission", session_id, request_id, allowed))
        return self._gen(request_id)


QUESTIONS = [{"question": "Which database?", "header": "DB", "multiSelect": False,
              "options": [{"label": "sqlite", "description": ""}]}]


@pytest.fixture
def env(tmp_path):
    pm = PermissionManager(persist_path=tmp_path / "pending.json")
    router = FakeRouter()
    store = FakeStore()
    return pm, router, store, Bridge("worker-1", store, pm=pm, router=router)


def _question(pm, router, board_sid="temp-1", unit_sid="sess-1", tid="toolu_1"):
    """A question filed on the board under a (first-turn, temporary) id while the
    router holds the waiting unit under its real id."""
    pm._prompts.setdefault(board_sid, {})[tid] = {
        "kind": "ask_user_question", "sessionId": board_sid, "tool_use_id": tid, "questions": QUESTIONS}
    router.units.append(Unit(unit_sid, tid))


def _approval(pm, router, sid="sess-2", rid="req-1"):
    pm.publish_prompt(sid, {"sessionId": sid, "requestId": rid, "toolName": "Bash",
                            "toolInput": {"command": "rm -rf /tmp/x"}, "reason": "recursive delete",
                            "options": ["approve", "deny"]})
    router.units.append(Unit(sid, rid))


def _answer(store, sid, key, body):
    store.put(f"bridge/worker-1/answer/{sid}/{key}.json", json.dumps(body).encode())


@pytest.fixture(autouse=True)
def live_questions(monkeypatch):
    # The board keeps a question while ask_question_manager has its waiter; these
    # tests put questions on the board directly, so every question counts as live.
    from core import ask_question_manager as aqm_mod
    monkeypatch.setattr(aqm_mod.ask_question_manager, "is_open", lambda key: True)


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


def test_question_is_delivered_through_the_router_and_the_turn_is_read(env):
    pm, router, store, bridge = env

    async def go():
        _question(pm, router)
        await bridge.tick()
        rec = json.loads(store.objects["bridge/worker-1/ask/sess-1/toolu_1.json"])
        assert rec["kind"] == "question" and rec["session_id"] == "sess-1"   # the unit's id
        _answer(store, "sess-1", "toolu_1", {"request_id": "toolu_1", "answers": {"Which database?": "sqlite"}})
        await bridge.tick()
        await _settle()

    asyncio.run(go())
    assert router.calls == [("answer", "sess-1", {"Which database?": "sqlite"}, "toolu_1", "person")]
    assert router.read_to_end == ["toolu_1"]


def test_approve_is_delivered_as_allowed(env):
    pm, router, store, bridge = env

    async def go():
        _approval(pm, router)
        await bridge.tick()
        rec = json.loads(store.objects["bridge/worker-1/ask/sess-2/req-1.json"])
        assert rec["kind"] == "approval" and rec["command"] == "rm -rf /tmp/x"
        _answer(store, "sess-2", "req-1", {"request_id": "req-1", "decision": "approve"})
        await bridge.tick()
        await _settle()

    asyncio.run(go())
    assert router.calls == [("permission", "sess-2", "req-1", True)]
    assert router.read_to_end == ["req-1"]


def test_decision_outside_approve_deny_is_not_delivered(env):
    pm, router, store, bridge = env

    async def go():
        _approval(pm, router)
        await bridge.tick()
        _answer(store, "sess-2", "req-1", {"request_id": "req-1", "decision": "allow_all"})
        await bridge.tick()

    asyncio.run(go())
    assert router.calls == []


def test_answer_filed_for_another_request_is_not_delivered(env):
    pm, router, store, bridge = env

    async def go():
        _question(pm, router, tid="toolu_9")
        await bridge.tick()
        _answer(store, "sess-1", "toolu_9", {"request_id": "toolu_OTHER", "answers": {"Which database?": "x"}})
        await bridge.tick()

    asyncio.run(go())
    assert router.calls == []


def test_an_answer_is_delivered_once(env):
    pm, router, store, bridge = env

    async def go():
        _question(pm, router, tid="toolu_7")
        await bridge.tick()
        _answer(store, "sess-1", "toolu_7", {"request_id": "toolu_7", "answers": {"Which database?": "sqlite"}})
        await bridge.tick()
        await bridge.tick()
        await _settle()

    asyncio.run(go())
    assert len(router.calls) == 1


def test_a_prompt_with_no_waiting_unit_is_not_mirrored(env):
    pm, router, store, bridge = env
    pm._prompts.setdefault("sess-x", {})["toolu_x"] = {"kind": "ask_user_question", "tool_use_id": "toolu_x",
                                                       "questions": QUESTIONS}
    asyncio.run(bridge.tick())
    assert not [k for k in store.objects if "/ask/" in k]


def test_prompt_that_left_the_board_gets_a_closed_marker(env):
    pm, router, store, bridge = env

    async def go():
        _approval(pm, router, rid="req-5")
        await bridge.tick()
        pm._prompts.clear()                        # answered on the Hive's own page
        await bridge.tick()

    asyncio.run(go())
    assert "bridge/worker-1/closed/sess-2/req-5.json" in store.objects


def test_reconcile_closes_asks_left_from_before_a_restart(env):
    pm, router, store, bridge = env
    store.put("bridge/worker-1/ask/sess-old/req-old.json", b"{}")
    _approval(pm, router, rid="req-live")
    store.put("bridge/worker-1/ask/sess-2/req-live.json", b"{}")
    asyncio.run(bridge.reconcile())
    assert "bridge/worker-1/closed/sess-old/req-old.json" in store.objects
    assert "bridge/worker-1/closed/sess-2/req-live.json" not in store.objects


@pytest.mark.parametrize("role,bucket,region,ok", [
    ("SwarmAI-Hive-worker-1", "swarmai-hive-2361-us-east-1", "us-east-1", True),
    ("OtherRole", "swarmai-hive-2361-us-east-1", "us-east-1", False),
    ("SwarmAI-Hive-../x", "swarmai-hive-2361-us-east-1", "us-east-1", False),
    ("SwarmAI-Hive-worker-1", "Bad Bucket", "us-east-1", False),
    ("SwarmAI-Hive-worker-1", "swarmai-hive-2361-us-east-1", "evil.example.com", False),
])
def test_identity_shape(role, bucket, region, ok):
    assert (identity_from(role, bucket, region) is not None) == ok


def test_a_failed_continuation_is_tried_again(env):
    pm, router, store, bridge = env
    attempts = []

    async def failing(*a, **k):
        attempts.append(1)
        yield {"type": "error", "code": "SESSION_NOT_FOUND"}
    router.continue_with_cmd_permission = lambda *a, **k: failing()

    async def go():
        _approval(pm, router, rid="req-9")
        await bridge.tick()
        _answer(store, "sess-2", "req-9", {"request_id": "req-9", "decision": "deny"})
        await bridge.tick()
        await _settle()
        await bridge.tick()
        await _settle()

    asyncio.run(go())
    assert len(attempts) == 2
