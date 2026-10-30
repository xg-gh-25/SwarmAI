"""Design 2026-10-30-cdf-agent-decides §5 (step 9): the backlog of the deleted paths is
marked, not run again.

Questions:
- v9 → v10: a unit held for the daily quota or parked on an unknown domain becomes
  rejected with reason ``discarded_backlog``; what held it is kept in ``defer_reason``
  and the candidate keeps its payload, so the documented UPDATE puts it back;
- every other deferred unit (writer busy, a missing section) is left as it was;
- the store is copied before the migration, and the marked units are no longer ready
  for the worker.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc).isoformat()


def _seed(conn):
    conn.execute("""INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,
        content_hash,envelope,project_snapshot,state,created_at)
        VALUES('c1','k1','S7','s','session','{"keep":"me"}','v','h','{}','{}','partial_pending',?)""", (NOW,))
    rows = [("c1.q", "deferred", "quota", None, "2026-10-31T00:00:00+00:00"),
            ("c1.d", "deferred", "unknown_domain", "unknown_domain", None),
            ("c1.b", "deferred", "writer_busy", "writer_busy", "2026-10-30T13:00:00+00:00"),
            ("c1.m", "deferred", "missing_section", "missing_section", None),
            ("c1.w", "written", "new", None, None)]
    for unit_id, state, reason, defer, nxt in rows:
        conn.execute("""INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,reason,defer_reason,
            next_attempt_at,owner_epoch,updated_at) VALUES(?,?,'{}',?,?,?,?,1,?)""",
                     (unit_id, "c1", state, reason, defer, nxt, NOW))


def _units(conn):
    return {r[0]: tuple(r[1:]) for r in conn.execute(
        "SELECT unit_id,state,reason,defer_reason,next_attempt_at FROM ingestion_units")}


def test_the_backlog_of_the_deleted_paths_is_marked_and_can_be_put_back(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.store import connect_rw, ingestion_db_path
    from core.ingestion.worker import _READY, reaggregate_candidates
    conn = connect_rw()
    _seed(conn)
    conn.execute("PRAGMA user_version=9")
    conn.commit()
    conn.close()

    conn = connect_rw()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        units = _units(conn)
        assert units["c1.q"] == ("rejected", "discarded_backlog", "quota", None)
        assert units["c1.d"] == ("rejected", "discarded_backlog", "unknown_domain", None)
        assert units["c1.b"] == ("deferred", "writer_busy", "writer_busy", "2026-10-30T13:00:00+00:00")
        assert units["c1.m"] == ("deferred", "missing_section", "missing_section", None)
        assert units["c1.w"] == ("written", "new", None, None)
        assert conn.execute("SELECT payload FROM ingestion_inbox WHERE id='c1'").fetchone()[0] == '{"keep":"me"}'
        stamp = conn.execute("SELECT updated_at FROM ingestion_units WHERE unit_id='c1.q'").fetchone()[0]
        assert "T" in stamp and stamp != NOW

        # the marked units are never ready again; the writer-busy one still is, in time
        ready_later = [r[0] for r in conn.execute(f"SELECT id FROM ingestion_inbox WHERE {_READY}",
                                                  ("2026-11-02T00:00:00+00:00",))]
        assert ready_later == ["c1"]
        conn.execute("UPDATE ingestion_units SET state='rejected' WHERE unit_id IN ('c1.b','c1.m')")
        assert [r[0] for r in conn.execute(f"SELECT id FROM ingestion_inbox WHERE {_READY}",
                                           ("2026-11-02T00:00:00+00:00",))] == []
        reaggregate_candidates(conn)
        assert conn.execute("SELECT state FROM ingestion_inbox WHERE id='c1'").fetchone()[0] == "done"

        # the recovery the migration documents
        conn.execute("UPDATE ingestion_units SET state='queued', reason=NULL, defer_reason=NULL "
                     "WHERE reason='discarded_backlog'")
        assert {u for u, v in _units(conn).items() if v[0] == "queued"} == {"c1.q", "c1.d"}
    finally:
        conn.close()

    backups = list(Path(ingestion_db_path()).parent.glob("ingestion.db.pre-v11-from-v9-*.bak"))
    assert len(backups) == 1
    check = sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
    try:
        assert check.execute("SELECT state FROM ingestion_units WHERE unit_id='c1.q'").fetchone()[0] == "deferred"
    finally:
        check.close()


def test_a_new_store_is_created_at_the_current_version(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
    finally:
        conn.close()
