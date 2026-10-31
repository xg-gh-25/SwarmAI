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
from core.swarm_out_bridge import MAX_COMMAND_CHARS, Bridge, approval_digest, identity_from


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


# what the Host's card shows for _approval's request (core.swarm_out._clean_ask)
SHOWN = approval_digest("Bash", "rm -rf /tmp/x", "recursive delete")

QUESTIONS = [{"question": "Which database?", "header": "DB", "multiSelect": False,
              "options": [{"label": "sqlite", "description": ""}]}]


@pytest.fixture
def env(tmp_path):
    pm = PermissionManager(persist_path=tmp_path / "pending.json")
    router = FakeRouter()
    store = FakeStore()
    # no pipeline runs here (the real provider would read this machine's workspace)
    return pm, router, store, Bridge("worker-1", store, pm=pm, router=router, decisions=lambda: [])


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
        _answer(store, "sess-2", "req-1", {"request_id": "req-1", "decision": "approve",
                                               "shown_sha256": SHOWN})
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
        _answer(store, "sess-2", "req-9", {"request_id": "req-9", "decision": "deny",
                                               "shown_sha256": SHOWN})
        await bridge.tick()
        await _settle()
        await bridge.tick()
        await _settle()

    asyncio.run(go())
    assert len(attempts) == 2



# ── fixing spec B: an approval is delivered only for the request it was shown ───

def test_an_approval_without_the_shown_content_is_not_delivered(env):
    pm, router, store, bridge = env

    async def go():
        _approval(pm, router)
        await bridge.tick()
        _answer(store, "sess-2", "req-1", {"request_id": "req-1", "decision": "approve"})
        await bridge.tick()

    asyncio.run(go())
    assert router.calls == []
    closed = json.loads(store.objects["bridge/worker-1/closed/sess-2/req-1.json"])
    assert closed["why"] == "answer_refused"


def test_an_approval_of_a_rewritten_ask_does_not_reach_the_real_request(env):
    """A process on the Hive rewrites the mirrored ask to a harmless command; the person
    approves what the card showed; the request still waiting is the original."""
    pm, router, store, bridge = env
    reads: list[str] = []

    async def go():
        _approval(pm, router)
        await bridge.tick()
        harmless = approval_digest("Bash", "ls")   # what the rewritten card showed
        _answer(store, "sess-2", "req-1", {"request_id": "req-1", "decision": "approve",
                                           "shown_sha256": harmless})
        real_get = store.get
        store.get = lambda key: (reads.append(key), real_get(key))[1]
        await bridge.tick()
        await bridge.tick()   # refused once; not re-read and re-closed every pass

    asyncio.run(go())
    assert router.calls == []
    assert reads == ["bridge/worker-1/answer/sess-2/req-1.json"]


def test_an_over_long_command_is_mirrored_as_truncated_and_cannot_be_approved(env):
    pm, router, store, bridge = env
    long_cmd = "echo " + "x" * (MAX_COMMAND_CHARS + 50)

    async def go():
        pm.publish_prompt("sess-3", {"sessionId": "sess-3", "requestId": "req-3", "toolName": "Bash",
                                     "toolInput": {"command": long_cmd}, "options": ["approve", "deny"]})
        router.units.append(Unit("sess-3", "req-3"))
        await bridge.tick()
        rec = json.loads(store.objects["bridge/worker-1/ask/sess-3/req-3.json"])
        assert rec["truncated"] is True and len(rec["command"]) == MAX_COMMAND_CHARS
        _answer(store, "sess-3", "req-3", {"request_id": "req-3", "decision": "approve",
                                           "shown_sha256": approval_digest("Bash", rec["command"])})
        await bridge.tick()

    asyncio.run(go())
    assert router.calls == []


def test_a_deny_of_an_over_long_command_is_delivered(env):
    pm, router, store, bridge = env
    long_cmd = "echo " + "x" * (MAX_COMMAND_CHARS + 50)

    async def go():
        pm.publish_prompt("sess-4", {"sessionId": "sess-4", "requestId": "req-4", "toolName": "Bash",
                                     "toolInput": {"command": long_cmd}, "options": ["approve", "deny"]})
        router.units.append(Unit("sess-4", "req-4"))
        await bridge.tick()
        rec = json.loads(store.objects["bridge/worker-1/ask/sess-4/req-4.json"])
        _answer(store, "sess-4", "req-4", {"request_id": "req-4", "decision": "deny",
                                           "shown_sha256": approval_digest("Bash", rec["command"])})
        await bridge.tick()
        await _settle()

    asyncio.run(go())
    assert router.calls == [("permission", "sess-4", "req-4", False)]


def test_a_deny_without_the_shown_content_is_delivered(env):
    """An older Host sends no shown_sha256; denying is always safe."""
    pm, router, store, bridge = env

    async def go():
        _approval(pm, router)
        await bridge.tick()
        _answer(store, "sess-2", "req-1", {"request_id": "req-1", "decision": "deny"})
        await bridge.tick()
        await _settle()

    asyncio.run(go())
    assert router.calls == [("permission", "sess-2", "req-1", False)]


def test_an_answer_to_rewritten_questions_is_not_delivered(env):
    pm, router, store, bridge = env

    async def go():
        _question(pm, router)
        await bridge.tick()
        _answer(store, "sess-1", "toolu_1", {"request_id": "toolu_1", "answers": {"Which colour?": "red"}})
        await bridge.tick()

    asyncio.run(go())
    assert router.calls == []



def test_a_refused_ask_is_closed_plainly_once_it_leaves_the_board(env):
    pm, router, store, bridge = env

    async def go():
        _approval(pm, router)
        await bridge.tick()
        _answer(store, "sess-2", "req-1", {"request_id": "req-1", "decision": "approve"})
        await bridge.tick()                         # refused: no shown_sha256
        assert json.loads(store.objects["bridge/worker-1/closed/sess-2/req-1.json"])["why"] == "answer_refused"
        pm.resolve_prompt("sess-2", "req-1") if hasattr(pm, "resolve_prompt") else pm._prompts.clear()
        router.units.clear()
        await bridge.tick()

    asyncio.run(go())
    assert "why" not in json.loads(store.objects["bridge/worker-1/closed/sess-2/req-1.json"])


def test_a_prompt_that_cannot_be_mirrored_does_not_stop_the_others(env, monkeypatch):
    pm, router, store, bridge = env
    real_put = store.put

    def put(key, body):
        if "/ask/sess-2/" in key:
            raise OSError("boom")
        real_put(key, body)
    store.put = put

    async def go():
        _approval(pm, router)
        _question(pm, router)
        await bridge.tick()

    asyncio.run(go())
    assert "bridge/worker-1/ask/sess-1/toolu_1.json" in store.objects


def test_a_question_without_text_is_dropped_on_both_sides(env):
    from core import swarm_out
    from core.swarm_out_bridge import _ask_record
    item = {"kind": "ask_user_question", "questions": [{"question": "  ", "options": []}, QUESTIONS[0]]}
    rec = _ask_record("worker-1", "s", "k", item)
    on_host = swarm_out._clean_ask(json.loads(json.dumps(rec)), "worker-1")
    assert [q["question"] for q in rec["questions"]] == [q["question"] for q in on_host["questions"]]


def test_a_command_with_a_lone_surrogate_is_mirrored(env):
    pm, router, store, bridge = env

    async def go():
        pm.publish_prompt("sess-5", {"sessionId": "sess-5", "requestId": "req-5", "toolName": "Bash",
                                     "toolInput": {"command": "echo \ud800"}, "options": ["approve", "deny"]})
        router.units.append(Unit("sess-5", "req-5"))
        await bridge.tick()

    asyncio.run(go())
    rec = json.loads(store.objects["bridge/worker-1/ask/sess-5/req-5.json"])
    assert rec["command"] == "echo \ud800"
