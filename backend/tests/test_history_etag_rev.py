"""The history ETag moves with the transcript rev, so a 304 never serves a stale body.

The ETag used to be the row count + compaction signature + live prompts. An
in-place update (a background agent's result, a decision, an answer) keeps the
count, so a client holding the earlier body (the frontend keeps it in memory per
session) got 304 and showed the agent still running after reopening the tab.

Real pieces: the conftest SQLite DB (schema v15 rev triggers), the real
``update_agent_result``, the real history endpoint.

Properties:
- an in-place update changes the ETag; the earlier ETag gets 200 with the new body;
- with no change, the same ETag gets 304;
- a rev that cannot be read gives no ETag (never cache a body we cannot version).
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


async def _history(sid: str, if_none_match: str | None = None, db=None):
    import database
    import routers.chat as chat_router
    req = MagicMock()
    req.headers = {"if-none-match": if_none_match} if if_none_match else {}
    router = MagicMock()
    router.get_unit = lambda _sid: None
    with patch.object(chat_router, "db", db or database.db), \
            patch.object(chat_router, "_get_router", lambda: router), \
            patch.object(chat_router.session_manager, "get_session", AsyncMock(return_value=object())):
        return await chat_router.get_session_messages(sid, req, limit=None, before_id=None, turns=None)


async def _store_agent_turn(sid: str, tool_use_id: str) -> None:
    from database import db
    now = datetime.now().isoformat()
    await db.messages.put({"id": str(uuid4()), "session_id": sid, "role": "user",
                           "content": [{"type": "text", "text": "go"}], "created_at": now})
    await db.messages.put({"id": str(uuid4()), "session_id": sid, "role": "assistant", "created_at": now,
                           "content": [{"type": "tool_use", "id": tool_use_id, "name": "Agent", "input": {}},
                                       {"type": "tool_result", "tool_use_id": tool_use_id,
                                        "subagent": {"status": "running"}}]})


def _agent_status(resp) -> str | None:
    body = json.loads(resp.body)
    return next(b["subagent"]["status"] for m in body for b in m["content"]
                if b.get("type") == "tool_result" and b.get("subagent"))


@pytest.mark.asyncio
async def test_an_in_place_update_changes_the_etag_and_the_old_one_gets_the_new_body():
    from database import db
    sid, tid = f"s-etag-{uuid4().hex[:6]}", "toolu_bg1"
    await _store_agent_turn(sid, tid)
    first = await _history(sid)
    assert _agent_status(first) == "running"
    etag = first.headers["ETag"]
    assert (await _history(sid, etag)).status_code == 304          # nothing changed

    assert await db.messages.update_agent_result(sid, tid, {"status": "completed"})
    again = await _history(sid, etag)
    assert again.status_code == 200                                # not a stale 304
    assert _agent_status(again) == "completed"
    assert again.headers["ETag"] != etag
    assert (await _history(sid, again.headers["ETag"])).status_code == 304


@pytest.mark.asyncio
async def test_no_etag_when_the_rev_cannot_be_read():
    import database
    sid = f"s-etag-{uuid4().hex[:6]}"
    await _store_agent_turn(sid, "toolu_bg2")
    broken = MagicMock(wraps=database.db)
    broken.messages = database.db.messages
    broken.list_compactions = database.db.list_compactions
    broken.transcript_rev = AsyncMock(side_effect=RuntimeError("locked"))
    resp = await _history(sid, db=broken)
    assert resp.status_code == 200
    assert "ETag" not in resp.headers and "X-Transcript-Rev" not in resp.headers


@pytest.mark.asyncio
async def test_each_message_lists_the_rows_it_holds_and_their_revs():
    """``row_revs``: a merged bubble names every row in it at the rev of the content
    returned, so a late feed change for one of them is recognised as already shown."""
    from database import db
    sid = f"s-rr-{uuid4().hex[:6]}"
    t = "2026-10-23T10:00:0"
    rows = [("u1", "user", [{"type": "text", "text": "go"}]),
            ("a1", "assistant", [{"type": "text", "text": "one"}]),
            ("a2", "assistant", [{"type": "turn_stop", "items": []}])]
    for i, (rid, role, content) in enumerate(rows):
        await db.messages.put({"id": f"{sid}-{rid}", "session_id": sid, "role": role,
                               "content": content, "created_at": f"{t}{i}"})
    revs = await db.row_revs([f"{sid}-{r[0]}" for r in rows])
    body = json.loads((await _history(sid)).body)
    assert [m["id"] for m in body] == [f"{sid}-u1", f"{sid}-a1"]          # a1 + a2 merged
    assert body[0]["row_revs"] == {f"{sid}-u1": revs[f"{sid}-u1"]}
    assert body[1]["row_revs"] == {f"{sid}-a1": revs[f"{sid}-a1"], f"{sid}-a2": revs[f"{sid}-a2"]}
    # the rev is the version of the content returned: an update moves both
    await db.messages.update_agent_result(sid, "none", {})               # no match: nothing moves
    assert json.loads((await _history(sid)).body)[1]["row_revs"] == body[1]["row_revs"]


@pytest.mark.asyncio
async def test_rows_carry_no_rev_key_unless_asked():
    """Other readers of the messages table never see the ``_rev`` column."""
    from database import db
    sid = f"s-rr-{uuid4().hex[:6]}"
    await _store_agent_turn(sid, "toolu_bg3")
    assert all("_rev" not in r for r in await db.messages.list_by_session(sid))
    assert all("_rev" in r for r in await db.messages.list_by_session(sid, with_rev=True))
    assert all("_rev" in r for r in await db.messages.list_by_session_paginated(sid, limit=5, with_rev=True))
