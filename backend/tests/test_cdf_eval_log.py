"""CDF eval record (core/ingestion/eval_log.py, design §14.2).

What is tested:
- The 30-day cleanup moves every unit of an expired candidate, written ones included,
  into ingestion_eval_log with its payload, card answers and reason; the live rows go.
  A unit is never in both places and never in neither.
- A cleanup that fails after archiving rolls the archive back with the delete.
- prune drops archive rows and extraction_attempts past 180 days, nothing newer.
- export merges live judge rows, the archive and the extractor's rejected units, tagged
  with stage/origin, filtered by [since, until).
- ``ingest eval-export`` writes that JSONL read-only and reports counts; with no store it
  says so instead of failing.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

NOW = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    from core.ingestion.api import connect_rw
    value = connect_rw()
    yield value
    value.close()


def _candidate(conn: sqlite3.Connection, cid: str, at: datetime, units: list[tuple[str, str, str]]) -> None:
    conn.execute(
        """INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,
               content_hash,envelope,project_snapshot,created_at,source_v2,unit_kind,extraction_pass)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, cid, "S2", "session:abc", "session", json.dumps({"what": f"text of {cid}"}),
         "v2", "h", "{}", "{}", at.isoformat(), "S2", "cognition", "main"))
    for slot, state, reason in units:
        conn.execute(
            """INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,decision,state,reason,updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (f"{cid}.{slot}", cid, json.dumps({"text": slot}),
             json.dumps({"card1": {"should_write": state == "written"}}), state, reason, at.isoformat()))


def _attempt(conn: sqlite3.Connection, aid: str, at: datetime) -> None:
    conn.execute(
        """INSERT INTO extraction_attempts(attempt_id,source,evidence_range,extractor_version,
               started_at,units_invalid,invalid_units,status) VALUES(?,?,?,?,?,?,?,?)""",
        (aid, "S4", json.dumps({"commits": ["c1"]}), "v1", at.isoformat(), 1,
         json.dumps([{"raw": {"what": "half"}, "reason": "too_large_incomplete"}]), "ok"))


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_cleanup_moves_every_outcome_into_the_archive(conn) -> None:
    from core.ingestion.worker import cleanup_expired
    old = NOW - timedelta(days=40)
    _candidate(conn, "old", old, [("a", "written", "new"), ("b", "rejected", "not_durable"),
                                  ("c", "deferred", "busy")])
    _candidate(conn, "fresh", NOW - timedelta(days=2), [("a", "rejected", "no_judgment_value")])
    assert cleanup_expired(conn, now=NOW, vacuum=False) == 1
    archived = {row[0]: row[1:] for row in conn.execute(
        "SELECT unit_id,state,reason,record FROM ingestion_eval_log")}
    assert set(archived) == {"old.a", "old.b", "old.c"}
    assert archived["old.a"][:2] == ("written", "new")
    assert archived["old.c"][:2] == ("expired_unprocessed", "expired_unprocessed")
    record = json.loads(archived["old.b"][2])
    assert record["payload"] == {"what": "text of old"}
    assert record["decision"] == {"card1": {"should_write": False}}
    live = {row[0] for row in conn.execute("SELECT unit_id FROM ingestion_units")}
    assert live == {"fresh.a"}


def test_failed_cleanup_rolls_back_the_archive(conn, monkeypatch) -> None:
    from core.ingestion import worker
    _candidate(conn, "old", NOW - timedelta(days=40), [("a", "rejected", "x")])
    real = conn

    class Failing:
        def __getattr__(self, name):
            return getattr(real, name)

        def execute(self, sql, *args):
            if sql.startswith("DELETE FROM ingestion_inbox"):
                raise sqlite3.OperationalError("disk full")
            return real.execute(sql, *args)

    with pytest.raises(sqlite3.OperationalError):
        worker.cleanup_expired(Failing(), now=NOW, vacuum=False)
    assert _count(conn, "ingestion_eval_log") == 0
    assert _count(conn, "ingestion_units") == 1


def test_prune_keeps_180_days(conn) -> None:
    from core.ingestion.eval_log import prune
    from core.ingestion.worker import cleanup_expired
    _candidate(conn, "ancient", NOW - timedelta(days=200), [("a", "rejected", "x")])
    _candidate(conn, "old", NOW - timedelta(days=40), [("a", "rejected", "x")])
    _attempt(conn, "att-ancient", NOW - timedelta(days=200))
    _attempt(conn, "att-recent", NOW - timedelta(days=10))
    cleanup_expired(conn, now=NOW, vacuum=False)
    assert {row[0] for row in conn.execute("SELECT unit_id FROM ingestion_eval_log")} == {"old.a"}
    assert {row[0] for row in conn.execute("SELECT attempt_id FROM extraction_attempts")} == {"att-recent"}
    assert prune(conn, now=NOW) == 0


def test_export_merges_live_archive_and_extractor_rejects(conn) -> None:
    from core.ingestion.eval_log import export
    from core.ingestion.worker import cleanup_expired
    _candidate(conn, "old", NOW - timedelta(days=40), [("a", "written", "new")])
    _candidate(conn, "fresh", NOW - timedelta(days=2), [("a", "rejected", "not_durable")])
    _attempt(conn, "att", NOW - timedelta(days=3))
    cleanup_expired(conn, now=NOW, vacuum=False)
    since = (NOW - timedelta(days=60)).date().isoformat()
    rows = list(export(conn, since))
    tags = {(r["unit_id"], r["stage"], r["origin"], r["state"], r["reason"]) for r in rows}
    assert tags == {
        ("fresh.a", "judge", "live", "rejected", "not_durable"),
        ("old.a", "judge", "archive", "written", "new"),
        ("att#0", "extraction", "live", "rejected_at_source", "too_large_incomplete"),
    }
    recent = list(export(conn, (NOW - timedelta(days=5)).date().isoformat()))
    assert {r["unit_id"] for r in recent} == {"fresh.a", "att#0"}


def test_cli_exports_jsonl(conn, tmp_path, capsys) -> None:
    import desktop_main
    _candidate(conn, "fresh", NOW - timedelta(days=2), [("a", "rejected", "not_durable")])
    out = tmp_path / "eval.jsonl"
    assert desktop_main.run_ingest(["eval-export", "--since", "2026-01-01", "--out", str(out)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["records"] == 1 and report["by_stage_state"] == {"judge:rejected": 1}
    line = json.loads(out.read_text().strip())
    assert line["record"]["payload"] == {"what": "text of fresh"}


def test_cli_without_a_store(tmp_path, monkeypatch, capsys) -> None:
    import desktop_main
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "empty"))
    assert desktop_main.run_ingest(["eval-export", "--since", "2026-01-01",
                                    "--out", str(tmp_path / "x.jsonl")]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "empty"
    assert not (tmp_path / "empty" / "ingestion.db").exists()
