"""History paging on the real database: the pages a tab loads add up to the history.

What is tested (design chat-transcript-single-source §4 item 3), through the real
``GET /sessions/{id}/messages`` handler on the real SQLite:

- a long session (multi-row assistant turns, context compactions between turns and
  inside one) paged back by whole user turns: the pages, concatenated oldest
  first, equal one full fetch — same bubbles, same order, no overlap, no gap;
- a write between page loads to a row on a page not yet loaded (a permission
  decision) is on that page when it is loaded: an older page reads the record as
  it is now, and the transcript feed never touches a row the tab does not hold
  (hooks/transcriptFeed), so paging needs no pinned rev;
- a row added after the first page (a new turn) never shows up on an older page.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.compact_summary import build_record

SID = "s-paging-union"


async def _row(db, mid, role, blocks, ts, cid=None):
    await db.messages.put({"id": mid, "session_id": SID, "role": role, "content": blocks,
                           "created_at": ts, "metadata": {"client_id": cid} if cid else {}})


def _text(t):
    return [{"type": "text", "text": t}]


async def _build(db):
    n = 0
    for turn in range(12):
        n += 1
        await _row(db, f"u{turn:02d}", "user", _text(f"question {turn}"), f"2026-10-22T10:{n:02d}:00", f"c{turn}")
        for part in range(turn % 3 + 1):
            n += 1
            blocks = _text(f"answer {turn}.{part}")
            if turn == 2 and part == 0:
                blocks = [{"type": "cmd_permission_request", "requestId": "perm-2", "toolName": "Bash",
                           "reason": "r", "decision": None}]
            await _row(db, f"a{turn:02d}{part}", "assistant", blocks, f"2026-10-22T10:{n:02d}:00", f"c{turn}-asst")
        if turn in (3, 7):              # between turns, and below inside turn 9
            await db.insert_compaction(SID, {**build_record({"trigger": "auto"}, f"sum {turn}"),
                                             "created_at": f"2026-10-22T10:{n:02d}:30"})
    await db.insert_compaction(SID, {**build_record({"trigger": "manual"}, "mid-turn"),
                                     "created_at": "2026-10-22T10:22:30"})


async def _get(turns=None, before_id=None):
    import routers.chat as chat_router
    req = MagicMock()
    req.headers = {}
    with patch.object(chat_router.session_manager, "get_session", AsyncMock(return_value=object())):
        resp = await chat_router.get_session_messages(SID, req, limit=None, before_id=before_id, turns=turns)
    return json.loads(resp.body), resp.headers.get("X-Has-More")


async def _paged(turns):
    pages, cursor = [], None
    while True:
        body, more = await _get(turns=turns, before_id=cursor)
        pages.insert(0, body)
        firsts = [m for m in body if not str(m["id"]).startswith("compact-")]
        if more != "1" or not firsts:
            break
        cursor = firsts[0]["id"]
    return [m for p in pages for m in p]


def _shape(msgs):
    return [(m["id"], m["role"], json.dumps(m.get("content"), sort_keys=True)) for m in msgs]


@pytest.mark.asyncio
@pytest.mark.parametrize("turns", [1, 2, 5])
async def test_pages_concatenated_equal_one_full_fetch(turns):
    from database import db
    await _build(db)
    full, _ = await _get()
    assert _shape(await _paged(turns)) == _shape(full)
    assert len({m["id"] for m in full}) == len(full)


@pytest.mark.asyncio
async def test_a_write_to_an_unloaded_page_shows_when_that_page_loads():
    from database import db
    await _build(db)
    first, more = await _get(turns=3)
    assert more == "1"
    await db.messages.update_permission_decision(SID, "perm-2", "approve")
    await _row(db, "u99", "user", _text("newer"), "2026-10-22T11:59:00", "c99")
    older = []
    cursor = [m for m in first if not m["id"].startswith("compact-")][0]["id"]
    while cursor:
        body, more = await _get(turns=3, before_id=cursor)
        older = body + older
        rows = [m for m in body if not m["id"].startswith("compact-")]
        cursor = rows[0]["id"] if more == "1" and rows else None
    perm = [b for m in older for b in m["content"] if b.get("type") == "cmd_permission_request"]
    assert perm and perm[0]["decision"] == "approve"
    assert "u99" not in {m["id"] for m in older}
    full, _ = await _get()
    assert _shape(older + first + [m for m in full if m["id"] == "u99"]) == _shape(full)
