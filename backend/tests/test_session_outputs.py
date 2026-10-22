"""core/session_outputs — the session's Canvas attribution record, and its wiring.

Methodology: SWARM_DATA_DIR is pinned to tmp by conftest, so the store lives in tmp;
the router hook is driven with a fake router; the build() join with a monkeypatched DB
read. No real data is read.

Invariants:
- one row per file: writes counted, first / last time, kind refreshed, baseRef kept;
  a delete marks the row, a later write clears it; non-rail kinds are not rows;
- rows are capped (oldest lastAt dropped); a bad session id never touches disk;
- backfill imports turn_deliverables once and never overrides a live row;
- forget removes the record;
- the router SUBMITS the write to the single-worker canvas_session pool before the
  event moves on, so a read submitted afterwards sees it (the ordering contract);
- build() returns the attribution rows joined with the catalog, and a session with no
  record is backfilled from its turn_deliverables blocks.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core import session_outputs as so


def _ev(path, kind="knowledge", op="written", **kw):
    return {"type": "file_changed", "path": path, "absolutePath": f"/ws/{path}", "kind": kind, "operation": op, **kw}


def _rows(sid):
    return {r["path"]: r for r in so.read(sid)["rows"]}


def test_rows_fold_count_and_delete_marks():
    so.record("s1", [_ev("a.md")], at="t1")
    so.record("s1", [_ev("a.md", kind="content"), _ev("x.py", kind="source"), _ev("r.py", kind="source-final", baseRef="ab12345^")], at="t2")
    so.record("s1", [_ev("a.md", op="deleted", kind=None)], at="t3")
    rows = _rows("s1")
    assert set(rows) == {"a.md", "r.py"}
    a = rows["a.md"]
    assert (a["writes"], a["firstAt"], a["lastAt"], a["kind"], a["deleted"]) == (2, "t1", "t3", "content", True)
    assert rows["r.py"]["baseRef"] == "ab12345^"
    so.record("s1", [_ev("a.md")], at="t4")
    assert _rows("s1")["a.md"]["deleted"] is False
    assert so.read("s2")["rows"] == []


def test_cap_and_bad_ids(monkeypatch):
    monkeypatch.setattr(so, "MAX_ROWS", 3)
    for i in range(5):
        so.record("s1", [_ev(f"f{i}.md")], at=f"t{i}")
    assert sorted(_rows("s1")) == ["f2.md", "f3.md", "f4.md"]
    for bad in ("../x", "", ".hidden", "a/b"):
        so.record(bad, [_ev("a.md")])
        assert so.read(bad)["rows"] == []
    assert sorted(p.name for p in so.store_dir().iterdir() if not p.name.startswith(".")) == ["s1.json"]


def test_backfill_once_live_rows_win_and_forget():
    so.record("s1", [_ev("a.md", kind="content")], at="live")
    so.backfill("s1", [("t1", [{"path": "a.md", "absolutePath": "/ws/a.md", "kind": "knowledge"},
                               {"path": "b.md", "absolutePath": "/ws/b.md", "kind": "knowledge"}])])
    rows = _rows("s1")
    assert rows["a.md"]["kind"] == "content" and rows["b.md"]["firstAt"] == "t1"
    assert so.read("s1")["backfilled"] is True
    so.backfill("s1", [("t9", [{"path": "c.md", "absolutePath": "/ws/c.md", "kind": "knowledge"}])])
    assert "c.md" not in _rows("s1")
    so.forget("s1")
    assert so.read("s1") == {"rows": [], "backfilled": False}


@pytest.mark.asyncio
async def test_router_submits_the_write_before_the_event_moves_on():
    from core import executors
    from core.session_router import SessionRouter
    loop = asyncio.get_running_loop()
    pool = executors.get_pool("canvas_session")
    SessionRouter._record_canvas_output("s1", _ev("a.md"))
    SessionRouter._record_canvas_output("s1", _ev("x.py", kind="source"))   # not a rail kind
    # a read submitted now (the client's refresh) runs after the write
    rows = await loop.run_in_executor(pool, lambda: so.read("s1")["rows"])
    assert [r["path"] for r in rows] == ["a.md"]


@pytest.mark.asyncio
async def test_build_joins_the_catalog_and_backfills_an_old_session(tmp_path, monkeypatch):
    from core import canvas_session as cs
    from core.product_registry import ProductRegistry
    ws = tmp_path / "ws"; (ws / "Knowledge" / "Notes").mkdir(parents=True)
    f = ws / "Knowledge" / "Notes" / "n.md"; f.write_text("x")
    ProductRegistry(ws).register_batch([str(f)])
    monkeypatch.setattr("core.project_registry.get_swarmws", lambda: ws)
    blocks = {"old": [("2026-10-01T10:00", [{"type": "turn_deliverables", "files": [
        {"path": "Knowledge/Notes/n.md", "absolutePath": str(f), "kind": "knowledge"}]}])]}

    async def fake_blocks(sid, needles):
        return blocks.get(sid, []) if cs.BACKFILL_NEEDLES == needles else []
    monkeypatch.setattr("database.db.messages.list_session_blocks", fake_blocks)
    out = await cs.build("old")
    [a] = out["artifacts"]
    assert (a["role"], a["product"], a["firstAt"], a["exists"]) == ("Knowledge", True, "2026-10-01T10:00", True)
    assert out["changes"] == []
    assert json.loads((so.store_dir() / "old.json").read_text())["backfilled"] is True


@pytest.mark.asyncio
async def test_the_live_refresh_skips_changes(monkeypatch):
    from core import canvas_session as cs
    so.record("s1", [_ev("a.md")], at="t1")
    so.backfill("s1", [])

    async def no_blocks(sid, needles):
        raise AssertionError("the live refresh reads no turn records")
    monkeypatch.setattr("database.db.messages.list_session_blocks", no_blocks)
    monkeypatch.setattr(cs, "session_runs", lambda *a: (_ for _ in ()).throw(AssertionError("no run index")))
    out = await cs.build("s1", changes=False)
    assert [a["path"] for a in out["artifacts"]] == ["a.md"] and out["changes"] is None


def test_external_paths_are_only_the_external_rows(tmp_path):
    f = tmp_path / "x.md"; f.write_text("x")
    so.record("s1", [{"path": "x.md", "absolutePath": str(f), "kind": "external-nodiff", "operation": "written"},
                     _ev("Knowledge/n.md")])
    assert so.external_paths("s1") == {str(f.resolve())}
    assert so.external_paths("s2") == set()
