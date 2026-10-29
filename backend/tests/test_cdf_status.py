"""CDF can see itself: ``ingest status``, its stalls in Need-You, and whether what it
writes is ever used (core/ingestion/status.py).

- A unit or candidate that stopped moving, an overdue session job and a dead owner
  are stalls; a healthy store has none, and the stall reaches the attention overlay.
- The north star counts an entry as used only when it was read or cited within the
  window after its write; recall alone is reported apart; only mature entries give
  the rate; per-turn files also report what the injection selection holds now.
- ``knowledge find`` and ``knowledge get`` record what they showed, so the usage
  measure sees the main way the DDD is read.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "SwarmWS"))
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    conn.execute("INSERT OR REPLACE INTO ingestion_owner(id,epoch,pid,started_at) VALUES(1,1,?,?)",
                 (os.getpid(), NOW.isoformat()))
    conn.commit()
    yield conn
    conn.close()


def _write(conn, unit_id, path, at):
    conn.execute("""INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,before_hash,after_hash,
        anchors_present,anchors_absent,relations,step,owner_epoch,created_at,updated_at)
        VALUES(?,'ingestion_unit',?,?,'','','[]','[]','[]','done',1,?,?)""",
                 (f"ingestion:{unit_id}", unit_id, path, at.isoformat(), at.isoformat()))


def _use(conn, path, unit_id, kind, at):
    conn.execute("INSERT INTO unit_usage(anchor,kind,session_id,at) VALUES(?,?,?,?)",
                 (f"{path}#Runtime Traps#cand:{unit_id}", kind, "s", at.isoformat()))


def test_a_healthy_store_has_no_stalls(store):
    from core.ingestion.status import stalls, status
    assert stalls(store, now=NOW) == []
    assert status(store, now=NOW)["owner"]["alive"] is True


def test_what_stopped_moving_is_a_stall(store):
    from core.ingestion.status import stalls
    old = (NOW - timedelta(hours=2)).isoformat()
    store.execute("""INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,
        content_hash,envelope,project_snapshot,state,created_at)
        VALUES('c1','k1','S2','s','session','{}','v','h','{}','{}','in_progress',?)""", (old,))
    store.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,owner_epoch,updated_at) "
                  "VALUES('c1.unit','c1','{}','writing',1,?)", (old,))
    store.execute("INSERT INTO ingestion_s2_jobs(session_id,reason,state,requested_at,next_attempt_at) "
                  "VALUES('s-1','session_end','queued',?,?)", (old, (NOW - timedelta(hours=3)).isoformat()))
    store.execute("UPDATE ingestion_owner SET pid=?", (2 ** 22 + 12345,))
    store.commit()
    found = " | ".join(stalls(store, now=NOW))
    for part in ("no live CDF owner", "writing for over an hour", "candidate(s) in progress",
                 "session job due"):
        assert part in found, found


def test_the_north_star_counts_use_within_the_window(store):
    from core.ingestion.status import north_star
    tech = "Projects/P/2-understanding/TECH.md"
    _write(store, "a.unit", tech, NOW - timedelta(days=20))      # mature, read on day 3
    _use(store, tech, "a.unit", "read_by_agent", NOW - timedelta(days=17))
    _write(store, "b.unit", tech, NOW - timedelta(days=20))      # mature, read only on day 18
    _use(store, tech, "b.unit", "read_by_agent", NOW - timedelta(days=2))
    _write(store, "c.unit", tech, NOW - timedelta(days=20))      # mature, only recalled
    _use(store, tech, "c.unit", "recall_injected", NOW - timedelta(days=19))
    _write(store, "d.unit", tech, NOW - timedelta(days=2))       # young, cited
    _use(store, tech, "d.unit", "cited_as_evidence", NOW - timedelta(days=1))
    store.commit()
    star = north_star(store, now=NOW)
    assert (star["mature_written"], star["mature_used"], star["used_rate"]) == (3, 1, 0.333)
    assert star["groups"]["ddd"]["mature_recalled"] == 1
    assert (star["young_written"], star["young_used_so_far"]) == (1, 1)


def test_per_turn_entries_report_what_injection_holds(store):
    from core.ingestion.status import north_star
    _write(store, "m.unit", ".context/MEMORY.md", NOW - timedelta(days=1))
    _write(store, "n.unit", ".context/MEMORY.md", NOW - timedelta(days=1))
    store.execute("INSERT INTO injection_selection(file_path,file_hash,selected_anchors,score_snapshot_version,"
                  "computed_at) VALUES('/x/.context/MEMORY.md','h',?,'v',?)",
                  (json.dumps([".context/MEMORY.md#Guidelines#cand:m.unit"]), NOW.isoformat()))
    store.commit()
    assert north_star(store, now=NOW)["groups"]["per_turn"]["injected_now"] == 1


def test_a_stall_reaches_need_you(store, monkeypatch):
    from core import attention_authority
    store.execute("UPDATE ingestion_owner SET pid=?", (2 ** 22 + 12345,))
    store.commit()
    items = attention_authority._collect_ingestion()
    stalled = [i for i in items if i.id == "ingestion:stalled"]
    assert stalled and "no live CDF owner" in stalled[0].summary


def test_knowledge_find_and_get_record_what_they_showed(store, tmp_path, monkeypatch):
    ws = tmp_path / "SwarmWS"
    doc = ws / "Projects/P/2-understanding/TECH.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# TECH\n\n## Runtime Traps\n\n- [pitfall] **Redaction misses aws_secret_access_key** "
                   "A regex for the word secret misses the AWS key name. <!-- cand:r.unit -->\n",
                   encoding="utf-8")
    (ws / "Projects/P/AGENTS.md").write_text("# P\n", encoding="utf-8")
    monkeypatch.setenv("SWARM_APP_SESSION_ID", "sess-1")
    from core import knowledge_cli
    assert knowledge_cli.main(["find", "--project", "P", "--query", "redaction secret"]) == 0
    rows = store.execute("SELECT anchor,kind,session_id FROM unit_usage").fetchall()
    assert [(r[1], r[2]) for r in rows] == [("recall_injected", "sess-1")]
    assert rows[0][0].endswith("#cand:r.unit")
    ident = json.loads(_capture(knowledge_cli, ["find", "--project", "P", "--query", "redaction", "--json"]))
    unit_id = ident["units"][0]["id"]
    assert knowledge_cli.main(["get", "--project", "P", unit_id]) == 0
    kinds = [r[0] for r in store.execute("SELECT kind FROM unit_usage ORDER BY rowid")]
    assert kinds[-1] == "read_by_agent"


def _capture(module, argv):
    out = []
    real = module._out
    module._out = out.append
    try:
        module.main(argv)
    finally:
        module._out = real
    return out[-1]


def test_requeue_gives_the_units_of_one_reason_a_fresh_start(store):
    """After a poison's or a deferral's cause is fixed, its units run again; others stay put."""
    from core.ingestion.api_v2 import requeue_units
    from core.ingestion.worker import _READY
    at = NOW.isoformat()
    for cand in ("c1", "c2"):
        store.execute("""INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,
            content_hash,envelope,project_snapshot,state,created_at)
            VALUES(?,?,'S7','s','session','{}','v','h','{}','{}','done',?)""", (cand, "k" + cand, at))
    store.execute("""INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,reason,attempts,
        error_class,error_fingerprint,owner_epoch,updated_at)
        VALUES('c1.unit','c1','{}','failed_poison','error:OSError',3,'infra','f',1,?),
              ('c2.unit','c2','{}','failed_poison','error:ValueError',3,'logic','g',1,?)""", (at, at))
    store.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES(?,'poison',?,0)",
                  (at, json.dumps({"unit_id": "c1.unit", "reason": "error:OSError"})))
    store.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES(?,'poison',?,0)",
                  (at, json.dumps({"unit_id": "c2.unit", "reason": "error:ValueError"})))
    store.commit()

    assert requeue_units("error:OSError") == 1
    store.rollback()
    units = {r[0]: r[1:] for r in store.execute(
        "SELECT unit_id,state,attempts,error_class,reason FROM ingestion_units")}
    assert units["c1.unit"] == ("queued", 0, None, None)
    assert units["c2.unit"][0] == "failed_poison"
    ready = [r[0] for r in store.execute(f"SELECT id FROM ingestion_inbox WHERE {_READY}", (at,))]
    assert ready == ["c1"]                      # the worker will take it again
    open_events = [json.loads(r[0])["unit_id"] for r in store.execute(
        "SELECT detail FROM ingestion_events WHERE kind='poison' AND resolved=0")]
    assert open_events == ["c2.unit"]
    assert requeue_units("error:OSError") == 0
    store.execute("""INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,reason,defer_reason,
        owner_epoch,updated_at) VALUES('c2.gap','c2','{}','deferred','unknown_domain','unknown_domain',1,?)""", (at,))
    store.commit()
    assert requeue_units("unknown_domain") == 1
    store.rollback()
    assert store.execute("SELECT state FROM ingestion_units WHERE unit_id='c2.gap'").fetchone()[0] == "queued"
