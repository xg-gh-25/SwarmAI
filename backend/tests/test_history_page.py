"""core.history_page.page_by_turns — history pages in user turns, not rows.

What is tested: a page holds exactly the last N user turns, each whole (starts at a
user row); has_more is True while older rows remain and False at the start of the
session; a cursor pages further back without overlap or gaps; a turn far longer
than one DB batch still arrives whole; the batch boundary landing exactly on the
page start is handled.
"""
from __future__ import annotations

import asyncio

from core.history_page import page_by_turns


def _session(turn_sizes: list[int]) -> list[dict]:
    """One user row + n assistant rows per turn, chronological ids r0001.."""
    rows: list[dict] = []
    for t, n in enumerate(turn_sizes):
        rows.append({"id": f"r{len(rows):04d}", "role": "user", "turn": t})
        for _ in range(n):
            rows.append({"id": f"r{len(rows):04d}", "role": "assistant", "turn": t})
    return rows


def _fetch(rows: list[dict]):
    calls = []

    async def fetch(limit: int, before_id):
        calls.append((limit, before_id))
        end = len(rows) if before_id is None else next(i for i, r in enumerate(rows) if r["id"] == before_id)
        return rows[max(0, end - limit):end]

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def run(coro):
    return asyncio.run(coro)


def test_last_n_turns_whole_and_has_more():
    rows = _session([3, 300, 2, 5, 1])
    page, more = run(page_by_turns(_fetch(rows), 2, batch=50))
    assert page[0]["role"] == "user"
    assert {r["turn"] for r in page} == {3, 4}
    assert len(page) == (1 + 5) + (1 + 1)
    assert more is True


def test_a_turn_longer_than_a_batch_arrives_whole():
    rows = _session([2, 1000])
    page, more = run(page_by_turns(_fetch(rows), 1, batch=100))
    assert page[0]["role"] == "user" and page[0]["turn"] == 1
    assert len(page) == 1001
    assert more is True


def test_start_of_session_has_no_more():
    rows = _session([2, 3])
    page, more = run(page_by_turns(_fetch(rows), 5, batch=50))
    assert page == rows
    assert more is False


def test_cursor_pages_back_without_overlap_or_gap():
    rows = _session([4, 250, 1, 7, 3, 2])
    fetch = _fetch(rows)
    seen: list[dict] = []
    cursor = None
    pages = 0
    while True:
        page, more = run(page_by_turns(fetch, 2, cursor, batch=60))
        seen = page + seen
        pages += 1
        if not more:
            break
        cursor = page[0]["id"]
    assert seen == rows
    assert pages == 3


def test_batch_boundary_on_the_page_start():
    # 2 turns of 1 assistant row each = 4 rows = exactly one batch: older rows exist
    rows = _session([1, 1, 1])
    page, more = run(page_by_turns(_fetch(rows), 2, batch=4))
    assert [r["turn"] for r in page] == [1, 1, 2, 2]
    assert more is True
    # and when nothing older exists
    rows2 = _session([1, 1])
    page2, more2 = run(page_by_turns(_fetch(rows2), 2, batch=4))
    assert page2 == rows2
    assert more2 is False


def test_empty_session():
    page, more = run(page_by_turns(_fetch([]), 3))
    assert page == [] and more is False


def _walk(rows, turns, **kw):
    """Page back from the newest row to the start; return (pages, all rows seen)."""
    fetch = _fetch(rows)
    pages, seen, cursor = [], [], None
    for _ in range(200):
        page, more = run(page_by_turns(fetch, turns, cursor, **kw))
        pages.append(page)
        seen = page + seen
        if not more:
            break
        cursor = page[0]["id"]
    return pages, seen


def test_row_cap_stops_adding_whole_turns():
    rows = _session([600] * 6)  # 601 rows per turn
    page, more = run(page_by_turns(_fetch(rows), 20, max_rows=1500))
    assert page[0]["role"] == "user"
    assert {r["turn"] for r in page} == {4, 5}  # a third whole turn would pass 1500
    assert len(page) == 1202
    assert more is True


def test_a_newest_turn_longer_than_the_cap_is_cut_and_paged_back_whole():
    rows = _session([3, 10_000])
    pages, seen = _walk(rows, 20, max_rows=1500)
    assert len(pages[0]) == 1500 and pages[0][0]["role"] == "assistant"
    assert all(len(p) <= 1500 for p in pages)
    assert seen == rows  # nothing lost, nothing twice


def test_mixed_sizes_page_back_without_overlap_or_gap_under_the_cap():
    rows = _session([2, 5000, 1, 40, 700, 700, 3, 1600, 9])
    pages, seen = _walk(rows, 20, max_rows=1500, batch=300)
    assert all(len(p) <= 1500 for p in pages)
    assert seen == rows


# ── the history endpoint with ?turns= ─────────────────────────────────────────

def test_endpoint_turns_page_sets_has_more_and_a_shape_specific_etag():
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    import routers.chat as chat_router

    rows = [dict(r, session_id="s", content=[{"type": "text", "text": r["id"]}], created_at=f"2026-10-22T10:{i:02d}:00")
            for i, r in enumerate(_session([2, 250, 3]))]
    fetch = _fetch(rows)
    fake_db = MagicMock()
    fake_db.messages.count_by_session = AsyncMock(return_value=len(rows))
    async def paginated(sid, limit, before_id):
        return await fetch(limit, before_id)
    fake_db.messages.list_by_session_paginated = AsyncMock(side_effect=paginated)
    fake_db.compaction_signature = AsyncMock(return_value="0.0")
    fake_db.list_compactions = AsyncMock(return_value=[])
    req = MagicMock()
    req.headers = {}

    async def go(**kw):
        with patch.object(chat_router, "db", fake_db), \
                patch.object(chat_router.session_manager, "get_session", AsyncMock(return_value=object())):
            return await chat_router.get_session_messages("s", req, limit=None, before_id=kw.get("before_id"), turns=kw["turns"])

    resp = run(go(turns=1))
    body = json.loads(resp.body)
    # the last turn, merged: its user row + one assistant bubble
    assert [m["role"] for m in body] == ["user", "assistant"]
    assert resp.headers["X-Has-More"] == "1"
    assert resp.headers["ETag"].endswith(':t1"')

    resp = run(go(turns=3))
    assert resp.headers["X-Has-More"] == "0"
    assert [m["role"] for m in json.loads(resp.body)] == ["user", "assistant"] * 3
