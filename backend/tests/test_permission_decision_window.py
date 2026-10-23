"""A decided permission prompt never reads "expired" — not even before its record lands.

``SessionUnit._deliver_permission_decision`` hands the decision to the hook, takes
the prompt off the board and switches state at once, and writes the decision onto
the stored block in a background task (aa491b97). A history read between the two
saw a prompt that is neither live nor decided, and ``permission_prompt.annotate``
marked it ``expired``; the ETag (row count + live set) stayed the same after the
record landed, so a client holding that body kept getting 304 with "expired".

Real pieces: the conftest SQLite DB, the real permission manager, the real unit
decision path, the real history endpoint. The background record is held by an
event so the window is deterministic.

Properties:
- in the window, the reload shows the user's decision (from the durable pending
  request the decision path updates synchronously), not "expired";
- after the record lands, the body shows the same (its rows' revs, and so the
  ETag, moved: the record is a write);
- a prompt nobody decided and nobody waits on still reads "expired".
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core import permission_prompt as pp
from core.permission_manager import permission_manager as pm
from core.session_unit import SessionState, SessionUnit

EVENT = {"type": "cmd_permission_request", "requestId": "", "sessionId": "", "toolName": "Bash",
         "toolInput": {"command": "make clean"}, "reason": "risky", "options": ["approve", "deny"]}


async def _store_prompt(sid: str, rid: str) -> None:
    from database import db
    await db.messages.put({"id": str(uuid4()), "session_id": sid, "role": "user",
                           "content": [{"type": "text", "text": "go"}], "created_at": datetime.now().isoformat()})
    await db.messages.put({"id": str(uuid4()), "session_id": sid, "role": "assistant",
                           "content": [pp.permission_block({**EVENT, "requestId": rid, "sessionId": sid})],
                           "created_at": datetime.now().isoformat()})


async def _history(sid: str, unit, if_none_match: str | None = None):
    import database
    import routers.chat as chat_router
    req = MagicMock()
    req.headers = {"if-none-match": if_none_match} if if_none_match else {}
    router = MagicMock()
    router.get_unit = lambda _sid: unit
    with patch.object(chat_router, "db", database.db), \
            patch.object(chat_router, "_get_router", lambda: router), \
            patch.object(chat_router.session_manager, "get_session", AsyncMock(return_value=object())):
        return await chat_router.get_session_messages(sid, req, limit=None, before_id=None, turns=None)


def _decision(resp) -> str | None:
    body = json.loads(resp.body)
    return next(b.get("decision") for m in body for b in m["content"] if b.get("type") == pp.BLOCK_TYPE)


def _unit(sid: str) -> SessionUnit:
    u = SessionUnit(session_id=sid, agent_id="a")
    u._client = object()
    u._transition(SessionState.IDLE)
    u._transition(SessionState.STREAMING)
    return u


@pytest.mark.asyncio
async def test_a_decision_reads_as_decided_before_and_after_its_record_lands():
    sid, rid = "s-window", f"perm_{uuid4().hex[:8]}"
    await _store_prompt(sid, rid)
    unit = _unit(sid)
    pm.store_pending_request({"id": rid, "session_id": sid, "tool_name": "Bash", "status": "pending",
                              "created_at": datetime.now().isoformat()})
    unit._pending_question = {"request_id": rid, "tool_use_id": rid}
    unit._transition(SessionState.WAITING_INPUT)
    waiter = asyncio.ensure_future(pm.request_decision(sid, {"requestId": rid}))
    await asyncio.sleep(0)
    assert _decision(await _history(sid, unit)) is None           # live: offered

    landed = asyncio.Event()
    real_record = pp.record_decision

    async def held_record(*a):
        await landed.wait()
        return await real_record(*a)

    with patch("core.permission_prompt.record_decision", held_record):
        assert unit._deliver_permission_decision(rid, False) is True
        unit._transition(SessionState.STREAMING)
        unit._pending_question = None
        window = await _history(sid, unit)
        assert _decision(window) == "deny"                         # not "expired"
        landed.set()
        for _ in range(50):
            await asyncio.sleep(0.01)
            from database import db
            rows = await db.messages.list_by_session(sid)
            if any(b.get("decision") for r in rows for b in r["content"] if isinstance(b, dict)):
                break
    after = await _history(sid, unit)
    assert _decision(after) == "deny"
    # the reload in the window shows what the reload after shows; the record moved
    # the row's rev, so the ETag changes too (no stale 304 either way)
    strip = lambda resp: [{k: v for k, v in m.items() if k != "row_revs"} for m in json.loads(resp.body)]
    assert strip(after) == strip(window)
    assert after.headers["ETag"] != window.headers["ETag"]
    assert await asyncio.wait_for(waiter, 2) == "deny"


@pytest.mark.asyncio
async def test_an_undecided_prompt_nobody_waits_on_still_reads_expired():
    sid, rid = "s-dead", f"perm_{uuid4().hex[:8]}"
    await _store_prompt(sid, rid)
    assert _decision(await _history(sid, None)) == "expired"
