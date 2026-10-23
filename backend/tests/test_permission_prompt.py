"""A command-permission prompt is durable (core/permission_prompt).

Regression for the 2026-10-22 incident (session e4f3397f): a CLI-initiated turn hit
the dangerous-command gate; the prompt existed only as a live event, was never
stored, and the session sat in WAITING_INPUT with nothing on screen.

What is tested
- ``permission_block``: the stored shape is the frontend's own block shape (so the
  two dedupe by requestId); an event without a requestId stores nothing.
- ``_persist_stream_event``: a ``cmd_permission_request`` event is written as a block
  of the turn's rows — on every turn path (user send, injected CLI turn, answer and
  permission continuations all go through it), and on the injected-turn path end to end.
- ``update_permission_decision`` (real SQLite): the decision lands on the right block
  of the right session; an already-decided block is not overwritten.
- the permission continuation writes the decision BEFORE the turn continues.
- ``live_request_ids`` / ``annotate``: a stored prompt with no decision that no hook
  waits on any more reads "expired"; a live one stays undecided; a decided one keeps
  its decision. The history ETag changes when the live set changes.
- ``cmd_permission_request`` is a display block, kept out of resume history.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import permission_prompt as pp

EVENT = {"type": "cmd_permission_request", "requestId": "perm_1", "sessionId": "s1",
         "toolName": "Bash", "toolInput": {"command": "rm -rf /tmp/x"}, "reason": "Matches dangerous command pattern",
         "options": ["approve", "deny"]}


@pytest.fixture(autouse=True)
def _no_decision_in_flight():
    """``note_decision`` is process state; a test that patches ``record_decision``
    (which drops the entry) would leave its decision for the next test to read."""
    pp._IN_FLIGHT.clear()
    yield
    pp._IN_FLIGHT.clear()


def test_block_is_the_frontend_shape():
    assert pp.permission_block(EVENT) == {
        "type": "cmd_permission_request", "requestId": "perm_1", "toolName": "Bash",
        "toolInput": {"command": "rm -rf /tmp/x"}, "reason": "Matches dangerous command pattern",
        "options": ["approve", "deny"]}
    assert pp.permission_block({"type": "cmd_permission_request"}) is None
    assert pp.permission_block({**EVENT, "toolInput": "not a dict"})["toolInput"] == {}


def _router():
    from core.session_router import SessionRouter
    return SessionRouter.__new__(SessionRouter)


@pytest.mark.asyncio
async def test_persist_stream_event_stores_the_prompt_on_its_own_row():
    router = _router()
    written = []

    async def _persist(session_id, blocks, model, **kw):
        written.append((session_id, blocks, kw.get("client_id")))
        return blocks

    pending = [{"type": "tool_result", "tool_use_id": "t0", "is_error": False, "truncated": False}]
    with patch.object(type(router), "_persist_assistant_blocks", AsyncMock(side_effect=_persist)):
        before = await router._persist_stream_event("s1", dict(EVENT), pending, client_id="c-asst")
    assert before == []
    assert len(written) == 1 and written[0][0] == "s1" and written[0][2] == "c-asst"
    types = [b["type"] for b in written[0][1]]
    assert types == ["tool_result", "cmd_permission_request"], "the result first, then the prompt"
    assert pending == []


@pytest.mark.asyncio
async def test_an_injected_cli_turn_that_stops_on_a_permission_stores_it():
    """The incident path end to end: CLI-initiated turn → permission → WAITING_INPUT."""
    from core.session_router import SessionRouter
    from core.session_unit import SessionState
    from core.background_agents import BackgroundAgents

    router = SessionRouter.__new__(SessionRouter)
    unit = MagicMock()
    unit.session_id = "s1"
    unit.background_agents = BackgroundAgents()
    unit._turn_changes = None
    unit._turn_files = {}
    unit.state = SessionState.STREAMING

    async def _frames():
        unit.state = SessionState.WAITING_INPUT
        yield dict(EVENT)

    unit._read_formatted_response = _frames
    rows = []

    async def _persist(session_id, blocks, model, **kw):
        rows.append(list(blocks))
        return blocks

    async def _published(_self, gen, **kw):
        async for ev in gen:
            yield ev

    with patch.object(SessionRouter, "_persist_assistant_blocks", AsyncMock(side_effect=_persist)), \
         patch.object(SessionRouter, "_published_turn", _published):
        await router.run_injected_turn(unit, object())
    stored = [b for r in rows for b in r]
    assert any(b.get("type") == "cmd_permission_request" and b.get("requestId") == "perm_1" for b in stored)


@pytest.mark.asyncio
async def test_update_permission_decision_real_sqlite(tmp_path):
    import aiosqlite
    from database.sqlite import SQLiteMessagesTable, dumps_json
    db_path = tmp_path / "t.db"
    blk = pp.permission_block(EVENT)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, "
                           "content TEXT, created_at TEXT, updated_at TEXT)")
        for mid, sid in (("m1", "s1"), ("m2", "s2")):
            await conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                               (mid, sid, "assistant", dumps_json([{"type": "text", "text": "go"}, blk]),
                                "2026-10-22T18:34", "x"))
        await conn.commit()
    t = SQLiteMessagesTable("messages", db_path)
    assert await t.update_permission_decision("s1", "perm_1", "deny") is True
    assert await t.update_permission_decision("s1", "perm_1", "approve") is False, "decided once"
    assert await t.update_permission_decision("s1", "nope", "approve") is False
    async with aiosqlite.connect(db_path) as conn:
        rows = dict(await (await conn.execute("SELECT id, content FROM messages")).fetchall())
    assert json.loads(rows["m1"])[1]["decision"] == "deny"
    assert "decision" not in json.loads(rows["m2"])[1], "another session untouched"


@pytest.mark.asyncio
async def test_record_decision_rejects_anything_but_approve_or_deny():
    with patch("database.db.messages.update_permission_decision", AsyncMock(return_value=True)) as upd:
        assert await pp.record_decision("s1", "perm_1", "approve") is True
        assert await pp.record_decision("s1", "perm_1", "maybe") is False
        assert await pp.record_decision("s1", "", "deny") is False
    upd.assert_awaited_once_with("s1", "perm_1", "approve")


@pytest.mark.asyncio
async def test_the_decision_is_recorded_only_once_the_hook_has_it():
    """The unit records the decision on the stored prompt after handing it to the
    blocked hook -- and records nothing when no hook waits (the 2026-10-22
    incident: the tab showed "Approved" for a decision that went nowhere)."""
    import asyncio
    from core.permission_manager import permission_manager as pm
    from core.session_unit import SessionState, SessionUnit
    unit = SessionUnit(session_id="s1", agent_id="a")
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.STREAMING)
    recorded = []

    async def _rec(sid, rid, decision):
        recorded.append((rid, decision, pm.is_open(rid)))
        return True

    with patch("core.permission_prompt.record_decision", _rec):
        with pytest.raises(RuntimeError):
            [e async for e in unit.continue_with_permission("perm_nobody", False)]
        assert recorded == []
        waiter = asyncio.ensure_future(pm.request_decision("s1", {"requestId": "perm_1"}))
        await asyncio.sleep(0)
        [e async for e in unit.continue_with_permission("perm_1", False)]
    assert await asyncio.wait_for(waiter, 2) == "deny"
    await asyncio.sleep(0)  # the record runs in the background
    assert recorded == [("perm_1", "deny", False)]  # recorded after delivery


def test_annotate_marks_only_dead_undecided_prompts_expired():
    msgs = [{"role": "assistant", "content": [
        {**pp.permission_block(EVENT)},                                     # live
        {**pp.permission_block({**EVENT, "requestId": "perm_old"})},        # dead, undecided
        {**pp.permission_block({**EVENT, "requestId": "perm_ok"}), "decision": "approve"},
        {"type": "text", "text": "x"}]}, {"role": "user", "content": "raw"}]
    pp.annotate(msgs, {"perm_1"})
    c = msgs[0]["content"]
    assert "decision" not in c[0]
    assert c[1]["decision"] == "expired"
    assert c[2]["decision"] == "approve"


def test_live_request_ids_reads_the_unit_and_the_prompt_board():
    from core.session_unit import SessionState
    unit = MagicMock()
    unit.state = SessionState.WAITING_INPUT
    unit._pending_question = {"request_id": "perm_1"}
    pm = MagicMock()
    pm.open_prompts.return_value = [{"requestId": "perm_2"},
                                    {"kind": "ask_user_question", "tool_use_id": "tu"}]
    with patch("core.permission_manager.permission_manager", pm):
        assert pp.live_request_ids(unit, "s1") == {"perm_1", "perm_2"}
    unit.state = SessionState.IDLE
    with patch("core.permission_manager.permission_manager", pm):
        assert pp.live_request_ids(unit, "s1") == {"perm_2"}
    assert pp.etag_part(set()) == "-" and pp.etag_part({"b", "a"}) == "a,b"


def test_prompt_is_kept_out_of_resume_history():
    from core.context_injector import _TOOL_ONLY_TYPES
    assert "cmd_permission_request" in _TOOL_ONLY_TYPES


def test_history_endpoint_annotates_and_keys_the_etag_on_live_prompts():
    """The real endpoint: a dead prompt reads expired, a live one stays pending, and
    the ETag differs between the two states (a 304 never serves the stale one)."""
    import asyncio
    import routers.chat as chat_router
    rows = [{"id": "u1", "session_id": "s", "role": "user", "content": [{"type": "text", "text": "go"}],
             "created_at": "2026-10-22T18:00:00"},
            {"id": "a1", "session_id": "s", "role": "assistant", "content": [pp.permission_block(EVENT)],
             "created_at": "2026-10-22T18:00:01"}]
    fake_db = MagicMock()
    fake_db.messages.count_by_session = AsyncMock(return_value=len(rows))
    fake_db.messages.list_by_session = AsyncMock(side_effect=lambda sid: json.loads(json.dumps(rows)))
    fake_db.transcript_rev = AsyncMock(return_value=5)
    fake_db.list_compactions = AsyncMock(return_value=[])
    req = MagicMock()
    req.headers = {}

    def get(live):
        async def go():
            with patch.object(chat_router, "db", fake_db), \
                    patch.object(chat_router.session_manager, "get_session", AsyncMock(return_value=object())), \
                    patch("core.permission_prompt.live_request_ids", lambda unit, sid: set(live)):
                return await chat_router.get_session_messages("s", req, limit=None, before_id=None, turns=None)
        return asyncio.run(go())

    live = get({"perm_1"})
    dead = get(set())
    blk = lambda resp: json.loads(resp.body)[-1]["content"][0]
    assert "decision" not in blk(live)
    assert blk(dead)["decision"] == "expired"
    assert live.headers["ETag"] != dead.headers["ETag"]
