"""core/usage_hive_sync + the Hive's /api/hive/usage rows (design 2026-10-29-usage-accounting §4).

Questions: does a Hive serve its own rows only (never rows it pulled), with no session
content; does the host take each Hive row once, under the Hive's name, tied to the
dispatch when the session is one it sent; is a row with a field off its type dropped,
not stored; is a Hive that does not serve usage yet skipped without moving its read
position; is a pass that could not write read again next time.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3

import httpx
import pytest

from core import swarm_out, usage_accounting as ua, usage_hive_sync as hs
from database.sqlite import _USAGE_LEDGER_DDL


def _db(path):
    with sqlite3.connect(path) as c:
        for stmt in _USAGE_LEDGER_DDL:
            c.execute(stmt)


def _hive_row(i, **over):
    return {"id": i, "at_utc": "2026-10-29T09:00:00+00:00", "surface": "chat", "feature": None,
            "session_id": "sess-1", "run_id": None, "stage": None, "job_id": None, "model": "opus",
            "input": 1, "output": 10, "cache_read": 100, "cache_write": 5, "cost_usd": 0.5,
            "cost_source": "cli", "kind": "spend", "imported": 0, **over}


def test_a_hive_serves_its_own_rows_after_an_id(tmp_path, monkeypatch):
    import database
    db = tmp_path / "h.db"
    _db(db)
    monkeypatch.setattr(database.db, "db_path", db)
    rows = [ua._row(surface="chat", model="m", tokens={"output": n}, cost=0.1, cost_source="cli",
                    dedup_key=f"k{n}", session_id="s") for n in (1, 2, 3)]
    rows.append(ua._row(surface="chat", model="m", tokens={"output": 9}, cost=0.1, cost_source="cli",
                        dedup_key="pulled", machine="other-hive"))
    assert ua.insert_now(rows)
    page = ua.rows_after(1, limit=1)
    assert [r["output"] for r in page["rows"]] == [2] and page["more"] is True and page["last_id"] == 2
    page = ua.rows_after(2, limit=10)
    assert [r["output"] for r in page["rows"]] == [3] and page["more"] is False      # never the pulled row
    assert set(page["rows"][0]) == set(ua.SERVED_COLUMNS)


@pytest.mark.parametrize("bad", [{"id": "1"}, {"at_utc": "yesterday"}, {"input": -1}, {"output": 1.5},
                                 {"cost_usd": float("nan")}, {"kind": "other"}, {"cost_usd": -1}])
def test_a_row_off_its_type_is_dropped(bad):
    assert hs.clean(_hive_row(1, **bad), "demo", "h1", lambda s: None) is None


def test_a_long_or_odd_text_field_is_emptied_not_stored():
    row = hs.clean(_hive_row(1, model="x" * 500, feature="cdf.s2; DROP TABLE"), "demo", "h1", lambda s: None)
    assert row["model"] is None and row.get("feature") is None and row["machine"] == "demo"


class _Hive:
    def __init__(self, rows, status=200):
        self.rows, self.status, self.calls = rows, status, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        after = int(request.url.params.get("after_id", 0))
        self.calls.append(after)
        if self.status != 200:
            return httpx.Response(self.status)
        page = [r for r in self.rows if r["id"] > after][:2]
        more = len([r for r in self.rows if r["id"] > after]) > 2
        return httpx.Response(200, json={"rows": page, "last_id": page[-1]["id"] if page else after, "more": more})


def _wire(monkeypatch, tmp_path, hive):
    import database
    db = tmp_path / "u.db"
    _db(db)
    monkeypatch.setattr(database.db, "db_path", db)
    monkeypatch.setattr(hs, "_state_path", lambda: tmp_path / "usage_sync.json")

    async def statuses():
        return {"h1": "running", "h2": "stopped"}

    async def instance(ref):
        return {"id": ref, "name": "demo"}

    monkeypatch.setattr(swarm_out, "_hive_statuses", statuses)
    monkeypatch.setattr(swarm_out, "_instance", instance)
    monkeypatch.setattr(swarm_out, "_client", lambda inst, timeout=20: httpx.AsyncClient(
        transport=httpx.MockTransport(hive.handler), base_url="https://demo.cloudfront.net"))
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [{"hive_id": "h1", "session_id": "sess-1", "dispatch_id": "so_aaaaaaaaaaaa"}])
    return db


def test_each_hive_row_is_taken_once_under_the_hive_tied_to_its_dispatch(tmp_path, monkeypatch):
    hive = _Hive([_hive_row(1), _hive_row(2, session_id="other"), _hive_row(3, kind="attribution", run_id="run_1"),
                  _hive_row(4, input=-5)])
    db = _wire(monkeypatch, tmp_path, hive)
    assert asyncio.run(hs.sync_once()) == {"h1": 3}
    assert asyncio.run(hs.sync_once()) == {"h1": 0}              # nothing new: nothing twice
    with sqlite3.connect(db) as c:
        got = c.execute("SELECT machine, session_id, dispatch_id, kind, dedup_key FROM usage_ledger ORDER BY id").fetchall()
    assert got == [("demo", "sess-1", "so_aaaaaaaaaaaa", "spend", "hive:h1:1"),
                   ("demo", "other", None, "spend", "hive:h1:2"),
                   ("demo", "sess-1", "so_aaaaaaaaaaaa", "attribution", "hive:h1:3")]
    assert json.loads((tmp_path / "usage_sync.json").read_text()) == {"h1": 4}


def test_a_hive_without_the_endpoint_is_skipped_and_its_position_kept(tmp_path, monkeypatch):
    hive = _Hive([], status=404)
    _wire(monkeypatch, tmp_path, hive)
    (tmp_path / "usage_sync.json").write_text(json.dumps({"h1": 7}))
    assert asyncio.run(hs.sync_once()) == {}
    assert json.loads((tmp_path / "usage_sync.json").read_text()) == {"h1": 7}


def test_a_pass_that_could_not_write_is_read_again(tmp_path, monkeypatch):
    hive = _Hive([_hive_row(1)])
    _wire(monkeypatch, tmp_path, hive)
    monkeypatch.setattr(ua, "insert_now", lambda rows: False)
    assert asyncio.run(hs.sync_once()) == {}
    assert json.loads((tmp_path / "usage_sync.json").read_text()) == {}
