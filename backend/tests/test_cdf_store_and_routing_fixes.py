"""ingestion.db growth, project routing, TIMELINE relations, the source breaker (live
findings 2026-10-30).

Questions:
- a write plan that settles (done / abandoned) releases its target file's bytes; v11
  releases them for the plans already settled (after a checked backup that keeps them),
  gives the pages back, and resolves the breaker events of the removed breaker;
- a project name that is no project goes to the General Brain under that name, even in a
  session that has a project; the review is shown the projects that exist;
- an event's plan anchors it under the section it is written in (its month), so its
  relations are recorded, not skipped as "anchor not found";
- there is no source breaker: deferred units never pause a source.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.test_cdf_writer_core import _decision, _unit, corpus  # noqa: F401  (fixture)


def test_a_settled_plan_releases_its_bytes(corpus):
    from core.ingestion.writer import apply_unit
    conn = sqlite3.connect(corpus["db"])
    conn.row_factory = sqlite3.Row
    row = _unit(conn, "u.1", "Verify current state before editing.", title="Verify")
    decision = _decision("u.1", "MEMORY", ".context/MEMORY.md", "Guidelines")
    (corpus["workspace"] / ".context").mkdir(exist_ok=True)
    (corpus["workspace"] / ".context" / "MEMORY.md").write_text("# Memory\n\n## Guidelines\n", encoding="utf-8")
    assert apply_unit(decision, row, conn) == "done"
    step, data, after = conn.execute(
        "SELECT step, intended_bytes, after_hash FROM ingestion_writes WHERE unit_id='u.1'").fetchone()
    assert (step, data) == ("done", None) and after            # the hash of what was written stays
    conn.close()


def test_v11_releases_settled_bytes_keeps_open_plans_and_resolves_breakers(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    from core.ingestion.store import connect_rw, ingestion_db_path
    conn = connect_rw()
    big = b"x" * 2_000_000
    for op, step in (("done-1", "done"), ("gone-1", "abandoned"), ("open-1", "target_applied")):
        conn.execute("""INSERT INTO ingestion_writes(op_id,op_kind,target_path,intended_bytes,step,created_at,updated_at)
                        VALUES(?, 'ingestion_unit', '.context/MEMORY.md', ?, ?, 't', 't')""", (op, big, step))
    conn.execute("INSERT INTO ingestion_events(at,kind,detail) VALUES('t','breaker','{}')")
    conn.execute("PRAGMA user_version=10")
    conn.commit()
    conn.close()
    before = Path(ingestion_db_path()).stat().st_size

    conn = connect_rw()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        kept = dict(conn.execute("SELECT op_id, intended_bytes IS NOT NULL FROM ingestion_writes"))
        assert kept == {"done-1": 0, "gone-1": 0, "open-1": 1}       # recovery still has what it needs
        assert conn.execute("SELECT resolved FROM ingestion_events WHERE kind='breaker'").fetchone()[0] == 1
        assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0
    finally:
        conn.close()
    assert Path(ingestion_db_path()).stat().st_size < before - 3_000_000
    (backup,) = Path(ingestion_db_path()).parent.glob("ingestion.db.pre-v11-from-v10-*.bak")
    check = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        assert check.execute("SELECT COUNT(*) FROM ingestion_writes WHERE intended_bytes IS NOT NULL").fetchone()[0] == 3
    finally:
        check.close()


def test_a_name_that_is_no_project_goes_to_the_general_brain_in_any_session(tmp_path, monkeypatch):
    from core.ingestion import pipeline_v2
    from tests.test_cdf_refactor import SAID, _cog, _row, _ws
    monkeypatch.setattr(pipeline_v2, "_user_texts", lambda unit, row: [SAID])
    d = pipeline_v2.run_unit_v2(_row(_cog(scope="project:CNTech IVT Autonomous Tenets", topic="decision",
                                          type="decision", why="w", rejected_alternatives="none"),
                                     session_project="Widget"), _ws(tmp_path))
    assert (d.destination.store, d.destination.relative_path) == (
        "COMMON_DDD", "Knowledge/Library/cntech-ivt-autonomous-tenets.md")


def test_the_review_is_shown_the_projects_that_exist():
    from core.ingestion.sources import s7_review
    prompt = s7_review.build_prompt(s7_review.ALLOWED_KINDS, {}, "", "", projects=["SwarmAI", "IVT"])
    assert "Projects that exist: SwarmAI, IVT." in prompt
    assert "a subsystem or part of one of them" in prompt


def test_an_event_plan_anchors_it_under_its_month(corpus):
    from core.ingestion.writer import apply_unit
    timeline = corpus["workspace"] / ".context" / "TIMELINE.md"
    timeline.parent.mkdir(exist_ok=True)
    timeline.write_text("# Timeline\n\n## 2026-10\n\n### 2026-10-29 Earlier\n- what: Before.\n"
                        "<!-- cand:old.1 | event_key:k | source:S7 -->\n", encoding="utf-8")
    conn = sqlite3.connect(corpus["db"])
    conn.row_factory = sqlite3.Row
    # the fixture's schema is v1, whose relations allow only supersedes (and OR IGNORE
    # would drop a derived_from silently): use the current table
    conn.executescript("""DROP TABLE ingestion_relations;
        CREATE TABLE ingestion_relations (subject_anchor TEXT NOT NULL,
          predicate TEXT NOT NULL CHECK (predicate IN ('supersedes','derived_from')),
          object_anchor TEXT NOT NULL, created TEXT NOT NULL, inactive INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (subject_anchor, predicate, object_anchor));""")
    row = _unit(conn, "new.1", "The tab shipped.", title="Shipped")
    decision = _decision("new.1", "TIMELINE", ".context/TIMELINE.md", None)
    decision["version_vector"] = {"relation_hints": [
        {"predicate": "derived_from", "object_anchor": ".context/TIMELINE.md#2026-10#cand:old.1"}]}
    group_when = {"when": "2026-10-30"}
    conn.execute("UPDATE ingestion_units SET claim_group=? WHERE unit_id='new.1'",
                 (__import__("json").dumps({"kind": "event", "title": "Shipped", "what": "The tab shipped.",
                                            **group_when}),))
    conn.commit()
    row = conn.execute("SELECT * FROM ingestion_units WHERE unit_id='new.1'").fetchone()
    assert apply_unit(decision, row, conn) == "done"
    rel = conn.execute("SELECT subject_anchor, object_anchor FROM ingestion_relations").fetchall()
    assert [tuple(r) for r in rel] == [(".context/TIMELINE.md#2026-10#cand:new.1",
                                        ".context/TIMELINE.md#2026-10#cand:old.1")]
    assert conn.execute("SELECT COUNT(*) FROM ingestion_events WHERE kind='relation_skipped'").fetchone()[0] == 0
    conn.close()


def test_there_is_no_source_breaker():
    from core.ingestion.worker import IngestionWorker
    assert not hasattr(IngestionWorker, "_update_breaker")


def test_the_review_is_shown_the_general_subjects_one_name_each():
    from core.ingestion.queries import general_subjects
    from core.ingestion.sources import s7_review
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_units(unit_id TEXT, state TEXT, destination TEXT, updated_at TEXT)")
    rows = [("a", "written", "CNTech IVT Autonomous Tenets", "2026-10-30T01"),
            ("b", "written", "cntech-ivt-autonomous-tenets", "2026-10-30T02"),   # same document
            ("c", "reinforced", "Data Agent", "2026-10-29T00"),
            ("d", "deferred", "Not Written", "2026-10-30T05"),
            ("e", "written", None, "2026-10-30T06")]
    for unit, state, subject, at in rows:
        dest = {"store": "COMMON_DDD", "project": subject}
        conn.execute("INSERT INTO ingestion_units VALUES(?,?,?,?)", (unit, state, __import__("json").dumps(dest), at))
    conn.execute("INSERT INTO ingestion_units VALUES('f','written',?,'2026-10-30T07')",
                 (__import__("json").dumps({"store": "DDD", "project": "SwarmAI"}),))
    assert general_subjects(conn) == ["cntech-ivt-autonomous-tenets", "Data Agent"]
    prompt = s7_review.build_prompt(s7_review.ALLOWED_KINDS, {}, "", "", projects=["SwarmAI"],
                                    general=["Data Agent"])
    assert "Subjects the general knowledge already holds: Data Agent." in prompt


def test_a_subject_whose_document_is_gone_is_no_longer_offered(tmp_path, monkeypatch):
    """A subject whose entries were moved into a project (its document emptied and removed)
    stops drawing new entries under its old name."""
    import json
    from core.ingestion import queries
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_units(unit_id TEXT, state TEXT, destination TEXT, updated_at TEXT)")
    for unit, subject in (("a", "Acme Plan"), ("b", "Old Subsystem")):
        conn.execute("INSERT INTO ingestion_units VALUES(?,?,?,?)",
                     (unit, "written", json.dumps({"store": "COMMON_DDD", "project": subject}), "2026-10-30T01"))
    lib = tmp_path / "Knowledge" / "Library"
    lib.mkdir(parents=True)
    (lib / "acme-plan.md").write_text("---\ningestion_candidate: n/a (multi-unit)\n---\n## Decisions\n"
                                      "- **x** y <!-- cand:cand-a.unit -->\n", encoding="utf-8")
    monkeypatch.setattr(queries, "connect_ro", lambda: conn)
    assert queries.read_general_subjects(tmp_path) == ["Acme Plan"]
