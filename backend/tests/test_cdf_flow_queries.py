"""CDF Flow query and route tests against a seeded temporary ingestion.db.

The tests use the frozen SCHEMA_DDL, patch only the read-only connection factory,
and assert bounds, redaction, complete audit detail, and no database mutation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from core.ingestion.api import SCHEMA_DDL
from core.ingestion import queries
from routers import ingestion

_REDACTABLE = "AKIA" + "ABCDEFGHIJKLMNOP"
_REDACTABLE_REASON = "pass" + "word='very-secret-value'"


def iso(delta: timedelta = timedelta()) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "ingestion.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_DDL)
    from core.ingestion.store import _V6_DDL
    conn.executescript(_V6_DDL)
    payload = {"text": "keep this", "secret": _REDACTABLE}
    conn.execute("""INSERT INTO ingestion_inbox
        (id,idem_key,source,source_ref,lane,payload,payload_schema,content_hash,envelope,
         project_snapshot,project_candidates,state,versions,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("c1", "idem", "remember", "session-1", "immediate", json.dumps(payload), "remember.v1",
         "hash", json.dumps({"source_event_ids": ["e1"], "tainted": False}), "{}", "[]", "done", "{}", iso()))
    decision = {"card0": {"passed": True}, "card1": {"shape": "UserCross"},
                "rules": [{"rule": "C2", "passed": True}], "card3": {"status": "OK_EMPTY"}}
    conn.execute("""INSERT INTO ingestion_units
        (unit_id,candidate_id,claim_group,decision,destination,action,reason,state,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        ("c1.lesson", "c1", json.dumps({"text": "keep this"}), json.dumps(decision),
         json.dumps({"store": "MEMORY", "relative_path": ".context/MEMORY.md"}),
         "append", _REDACTABLE_REASON, "written", iso()))
    conn.execute("""INSERT INTO ingestion_writes
        (op_id,op_kind,unit_id,target_path,anchors_present,anchors_absent,relations,step,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        ("w1", "ingestion_unit", "c1.lesson", ".context/MEMORY.md", "[]", "[]", "[]", "done", iso(), iso()))
    conn.execute("""INSERT INTO ingestion_daily_stats
        (utc_day,source,lane,store,state,reason,rules_digest,candidates,units,bytes_written)
        VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now(timezone.utc).date().isoformat(), "remember", "immediate", "MEMORY", "written", "-", "v1", 1, 1, 10))
    conn.execute("""INSERT INTO ingestion_llm_usage(utc_day,source,calls,input_tokens,output_tokens)
        VALUES(?,?,?,?,?)""", (datetime.now(timezone.utc).date().isoformat(), "S2", 3, 2400, 310))
    conn.execute("INSERT INTO ingestion_lanes(lane,last_completed_at) VALUES('maintenance',?)", (iso(),))
    conn.commit()
    conn.close()

    def ro() -> sqlite3.Connection:
        value = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        value.execute("PRAGMA query_only=ON")
        return value

    monkeypatch.setattr(queries, "connect_ro", ro)
    monkeypatch.setattr(queries, "ingestion_db_path", lambda: db)
    monkeypatch.setattr(ingestion.queries, "connect_ro", ro)
    monkeypatch.setattr(ingestion.queries, "ingestion_db_path", lambda: db)
    return db


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_overview_stats_and_health_queries(seeded: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / ".context").mkdir(parents=True)
    (workspace / ".context" / "MEMORY.md").write_text("memory")
    (workspace / ".context" / "EVOLUTION.md").write_text("evolution")
    monkeypatch.setattr(queries, "get_swarmws", lambda: workspace)
    overview = queries.flow_overview("24h")
    assert overview["total_units"] == 1
    assert "stages" not in overview   # v1 card counts; the Cognition flow view reads flow_view
    assert overview["written_rate"] == 1
    assert overview["lanes"] == {"immediate": 1}
    assert queries.daily_stats(30)["rows"][0]["units"] == 1
    health = queries.health()
    assert health["inbox_count"] == 1
    assert health["memory_bytes"] == 6
    assert health["llm_by_source"] == [{
        "utc_day": datetime.now(timezone.utc).date().isoformat(),
        "source": "S2", "calls": 3, "input_tokens": 2400, "output_tokens": 310,
    }]
    assert "recurrence" not in health   # replaced by the correction ledger


def test_list_filters_page_cap_and_window_cap(seeded: Path):
    result = queries.list_items({"source": "remember", "store": "MEMORY", "state": "written",
                                 "reason": _REDACTABLE_REASON, "keyword": "keep",
                                 "time": 1000, "page_size": 500}, 1)
    assert result["total"] == 1
    assert result["page_size"] == 50
    assert result["window_days"] == 30
    assert result["items"][0]["id"] == "c1"


def test_detail_is_capped_redacted_complete_and_path_confined(seeded: Path, tmp_path: Path):
    conn = sqlite3.connect(seeded)
    huge = "x" * (20 * 1024)
    conn.execute("UPDATE ingestion_inbox SET payload=? WHERE id='c1'", (json.dumps({"body": huge, "key": _REDACTABLE}),))
    conn.execute("UPDATE ingestion_units SET reason=? WHERE unit_id='c1.lesson'", (huge,))
    conn.execute("UPDATE ingestion_writes SET archive_path=? WHERE op_id='w1'", ("../../outside",))
    conn.commit(); conn.close()
    detail = queries.item_detail("c1", tmp_path)
    assert detail is not None
    assert _REDACTABLE not in json.dumps(detail)
    body_page = detail["candidate"]["payload"]["body"]
    reason_page = detail["units"][0]["reason"]
    assert body_page["total_bytes"] == len(huge.encode())
    assert reason_page["total_bytes"] == len(huge.encode())
    assert detail["has_more"] and detail["next_offset"] == 16 * 1024
    second = queries.item_detail("c1", tmp_path, detail["next_offset"])
    assert second is not None
    assert body_page["text"] + second["candidate"]["payload"]["body"]["text"] == huge
    assert reason_page["text"] + second["units"][0]["reason"]["text"] == huge
    assert detail["units"][0]["decision"]["card0"]["passed"] is True
    assert detail["writes"][0]["open_file"].startswith(str(tmp_path.resolve()))
    assert detail["writes"][0]["open_archive"] is None


def test_every_query_preserves_database_bytes(seeded: Path, tmp_path: Path):
    before = digest(seeded)
    queries.flow_overview("7d")
    queries.list_items({"time": 30}, 1)
    queries.item_detail("c1", tmp_path)
    queries.daily_stats(30)
    queries.health()
    assert digest(seeded) == before


def test_routes_have_response_models_and_run_successfully(seeded: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(ingestion, "get_swarmws", lambda: tmp_path)
    monkeypatch.setattr(ingestion.queries, "read_bootstrap_progress", lambda project: {
        "project": project, "terminal": 1, "total": 2, "pending": 1,
        "s7": {"terminal": 1, "total": 1},
        "s4_backfill": {"terminal": 0, "total": 1},
    })
    app = FastAPI(); app.include_router(ingestion.router)
    client = TestClient(app)
    assert client.get("/api/ingestion/flow?window=24h").status_code == 200
    assert client.get("/api/ingestion/items?page_size=999&time=999").json()["page_size"] == 50
    assert client.get("/api/ingestion/items/c1").json()["candidate"]["id"] == "c1"
    assert client.get("/api/ingestion/stats?range=30").status_code == 200
    assert client.get("/api/ingestion/health").status_code == 200
    assert client.get("/api/ingestion/bootstrap-progress?project=ProjectA").json()["pending"] == 1
    assert client.get("/api/ingestion/items/missing").status_code == 404
    schema = app.openapi()["paths"]
    for path in ("/api/ingestion/flow", "/api/ingestion/items", "/api/ingestion/items/{item_id}", "/api/ingestion/stats", "/api/ingestion/health", "/api/ingestion/bootstrap-progress"):
        assert schema[path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]


def test_access_log_queue_is_drained_only_by_worker(seeded: Path, tmp_path: Path):
    queries.item_detail("c1", tmp_path)
    rw = sqlite3.connect(seeded)
    count = queries.drain_access_log(rw)
    rw.commit()
    assert count >= 1
    assert rw.execute("SELECT item_id,actor FROM ingestion_access_log ORDER BY id DESC").fetchone() == ("c1", "dashboard")
    rw.close()


def test_redaction_route_requires_explicit_user_action(monkeypatch: pytest.MonkeyPatch):
    from routers import ingestion_redact
    calls = []
    monkeypatch.setattr(ingestion_redact, "redact", lambda anchor, reason, actor: calls.append(
        (anchor, reason, actor)) or "revision")
    app = FastAPI(); app.include_router(ingestion_redact.router)
    client = TestClient(app, client=("127.0.0.1", 50000))
    payload = {"anchor": "Knowledge/x.md#S#cand:u", "reason": "Incorrect fact"}
    assert client.post("/api/ingestion/redact", json=payload).status_code == 403
    response = client.post("/api/ingestion/redact", json=payload,
                           headers={"X-Swarm-User-Action": "redact"})
    assert response.status_code == 200
    assert response.json() == {"revision": "revision"}
    assert calls == [(payload["anchor"], payload["reason"], "user")]
