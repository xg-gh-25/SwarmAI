"""scripts/cdf_relink_skipped.py: the relations skipped before the event-anchor fix.

Questions:
- a derived_from whose two entries are on disk is recorded at the sections they are in
  now, through the writer's own relation step; the old event is resolved;
- a supersedes is not replayed, an entry that is gone links nothing, a cycle stays
  refused — and all are resolved, with one relations_repaired event saying what happened;
- the dry run changes nothing; --apply keeps a checked backup first.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

TIMELINE = """# Timeline

## 2026-10

### 2026-10-30 New event
- what: something happened
<!-- cand:cand-new.unit | event_key: | source:S2 | event_kind:event -->

## Not injected

### 2026-10-29 Older event
- what: something before
<!-- cand:cand-old.unit | event_key: | source:S2 | event_kind:event -->
"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    workspace = tmp_path / "ws"
    (workspace / ".context").mkdir(parents=True)
    (workspace / ".context" / "TIMELINE.md").write_text(TIMELINE, encoding="utf-8")
    monkeypatch.setattr("core.ingestion.writer.get_swarmws", lambda: workspace)
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    skipped = [
        # the bug: the new entry anchored with an empty section
        ({"subject_anchor": ".context/TIMELINE.md##cand:cand-new.unit", "predicate": "derived_from",
          "object_anchor": ".context/TIMELINE.md#Not injected#cand:cand-old.unit"}, "anchor not found"),
        ({"subject_anchor": ".context/TIMELINE.md##cand:cand-new.unit", "predicate": "supersedes",
          "object_anchor": ".context/TIMELINE.md#Not injected#cand:cand-old.unit"}, "anchor not found"),
        ({"subject_anchor": ".context/TIMELINE.md##cand:cand-new.unit", "predicate": "derived_from",
          "object_anchor": ".context/TIMELINE.md#2026-09#cand:cand-gone.unit"}, "anchor not found"),
        ({"subject_anchor": ".context/TIMELINE.md#2026-10#cand:cand-new.unit", "predicate": "supersedes",
          "object_anchor": ".context/TIMELINE.md#Not injected#cand:cand-old.unit"}, "cycle"),
    ]
    for relation, reason in skipped:
        conn.execute("INSERT INTO ingestion_events(at,kind,detail) VALUES('t','relation_skipped',?)",
                     (json.dumps({"op_id": "op", "relation": relation, "reason": reason}),))
    conn.close()
    return workspace


def _state(workspace: Path):
    from core.ingestion.store import connect_ro
    conn = connect_ro()
    try:
        relations = conn.execute("SELECT subject_anchor,predicate,object_anchor FROM ingestion_relations").fetchall()
        open_events = conn.execute(
            "SELECT COUNT(*) FROM ingestion_events WHERE kind='relation_skipped' AND resolved=0").fetchone()[0]
        repaired = conn.execute("SELECT detail FROM ingestion_events WHERE kind='relations_repaired'").fetchall()
        return [tuple(r) for r in relations], open_events, repaired
    finally:
        conn.close()


def test_dry_run_changes_nothing(store):
    from scripts.cdf_relink_skipped import run
    result = run(store, apply=False)
    assert result["counts"] == {"relink": 1, "supersedes_not_replayed": 1, "entry_gone": 1, "cycle_refused": 1}
    assert _state(store) == ([], 4, [])


def test_apply_relinks_what_is_sound_and_closes_every_event(store):
    from core.ingestion.store import ingestion_db_path
    from scripts.cdf_relink_skipped import run
    result = run(store, apply=True)
    relations, open_events, repaired = _state(store)
    assert relations == [(".context/TIMELINE.md#2026-10#cand:cand-new.unit", "derived_from",
                          ".context/TIMELINE.md#Not injected#cand:cand-old.unit")]
    assert open_events == 0
    (detail,) = repaired
    assert json.loads(detail[0])["recorded"] == 1
    assert result["counts"]["recorded"] == 1
    backup = Path(result["backup"])
    assert backup.is_file() and backup.parent == Path(ingestion_db_path()).parent
    check = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:   # the backup holds the store as it was
        assert check.execute("SELECT COUNT(*) FROM ingestion_events WHERE resolved=0").fetchone()[0] == 4
    finally:
        check.close()


def test_the_backup_check_holds_while_the_store_is_written(tmp_path):
    """The daemon writes while a one-time script backs up: the copy is checked against the
    snapshot it was taken from, not against the live table (that compare never held)."""
    import threading
    from scripts.cdf_move_misrouted import _backup
    db = tmp_path / "live.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(x)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(20000)])
    conn.commit()
    stop = threading.Event()

    def writer():
        w = sqlite3.connect(db)
        while not stop.is_set():
            w.execute("INSERT INTO t VALUES (1)")
            w.commit()
        w.close()
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for _ in range(5):
            assert Path(_backup(db)).is_file()
    finally:
        stop.set()
        thread.join()
        conn.close()
