"""scripts/cdf_move_misrouted: entries a project DDD holds for a subject that is no
project move to that subject's General Brain document, whole and once.

Questions: is every matched entry in the destination (marker and metadata with it) and
gone from the DDD file, with the other entries untouched; are the records updated (new
destination, a moved event, the relation under the new anchors); does a second run
change nothing; does a dry run write nothing?
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

TENETS = ("- [decision] **Checkpoints collapse into one matrix** One area-based matrix. "
          "<!-- cand:cand-t1.unit -->\n  <!-- ref:0 | last:2026-10-30 | decay:active | source:S7 -->\n")
TENETS2 = ("- [decision] **Run traces feed eval** Required input. <!-- cand:cand-t2.unit -->\n"
           "  <!-- ref:0 | last:2026-10-30 | decay:active | source:S7 -->\n")
OTHER = ("- [decision] **A project that exists keeps its entry** Here by its own scope. "
         "<!-- cand:cand-p1.unit -->\n  <!-- ref:0 | last:2026-10-30 | decay:active | source:S7 -->\n")
OWN = ("- [decision] **Swarm Out list is one line per run** Rows grouped by action. "
       "<!-- cand:cand-s1.unit -->\n  <!-- ref:0 | last:2026-10-30 | decay:active | source:S7 -->\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    data, ws = tmp_path / "data", tmp_path / "SwarmWS"
    data.mkdir()
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    doc = ws / "Projects/SwarmAI/2-understanding/PROJECT.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(f"# Project\n\n## Recent Decisions\n\n{TENETS}\n{OWN}\n{OTHER}\n{TENETS2}\n## Open Items\n",
                   encoding="utf-8")
    (ws / "Knowledge/Library").mkdir(parents=True)
    # a project that exists: an entry scoped to it is not moved, whatever file holds it
    (ws / "Projects/CNTech-IVT/2-understanding").mkdir(parents=True)
    (ws / "Projects/CNTech-IVT/2-understanding/PROJECT.md").write_text("# Project\n", encoding="utf-8")
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    conn.execute("INSERT OR REPLACE INTO ingestion_owner(id,epoch,pid,started_at) VALUES(1,1,1,'t')")
    for unit, scope in (("cand-t1.unit", "project:CNTech-IVT-Autonomous"),
                        ("cand-t2.unit", "project:cntech-ivt-autonomous-tenets"),
                        ("cand-s1.unit", "project:SwarmAI"),
                        ("cand-p1.unit", "project:CNTech IVT")):
        conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
                     "content_hash,envelope,project_snapshot,created_at) VALUES(?,?,'S7','r','session','{}','v2',"
                     "'h','{}','{}','t')", (unit.removesuffix(".unit"), unit))
        conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,owner_epoch,"
                     "updated_at,destination) VALUES(?,?,?,'written',1,'t',?)",
                     (unit, unit.removesuffix(".unit"), json.dumps({"scope": scope}),
                      json.dumps({"store": "DDD", "relative_path": "Projects/SwarmAI/2-understanding/PROJECT.md"})))
        conn.execute("INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,step,created_at,updated_at)"
                     " VALUES(?, 'ingestion_unit', ?, 'Projects/SwarmAI/2-understanding/PROJECT.md', 'done', 't', 't')",
                     (f"ingestion:{unit}", unit))
    old = "Projects/SwarmAI/2-understanding/PROJECT.md#Recent Decisions#cand:"
    conn.execute("INSERT INTO ingestion_relations VALUES(?,?,?,'t',0)",
                 (old + "cand-t2.unit", "derived_from", old + "cand-t1.unit"))
    conn.commit()
    conn.close()
    return ws, doc


def _run(ws, apply):
    from scripts.cdf_move_misrouted import run
    return run(ws, match="cntech-ivt", subject="CNTech IVT Autonomous Tenets", apply=apply)


def test_a_dry_run_writes_nothing(env):
    ws, doc = env
    before = doc.read_bytes()
    report = _run(ws, apply=False)
    assert (report["units"], report["in_files"]) == (2, 2)
    assert doc.read_bytes() == before and not (ws / report["destination"]).exists()


def test_the_entries_move_whole_and_the_records_follow(env):
    ws, doc = env
    report = _run(ws, apply=True)
    assert report["moved"] == 2 and report["relations_recorded"] == 1
    dest = ws / "Knowledge/Library/cntech-ivt-autonomous-tenets.md"
    text = dest.read_text(encoding="utf-8")
    assert TENETS.strip() in text and TENETS2.strip() in text and "## Recent Decisions" in text
    left = doc.read_text(encoding="utf-8")
    assert "cand-t1" not in left and "cand-t2" not in left and OWN.strip() in left and OTHER.strip() in left and "## Open Items" in left
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    try:
        dests = dict(conn.execute("SELECT unit_id, json_extract(destination,'$.relative_path') FROM ingestion_units"))
        assert dests["cand-t1.unit"] == dests["cand-t2.unit"] == "Knowledge/Library/cntech-ivt-autonomous-tenets.md"
        assert dests["cand-s1.unit"].startswith("Projects/SwarmAI/")
        assert conn.execute("SELECT COUNT(*) FROM ingestion_events WHERE kind='moved'").fetchone()[0] == 2
        new = "Knowledge/Library/cntech-ivt-autonomous-tenets.md#Recent Decisions#cand:"
        assert conn.execute("SELECT COUNT(*) FROM ingestion_relations WHERE subject_anchor=? AND object_anchor=? "
                            "AND inactive=0", (new + "cand-t2.unit", new + "cand-t1.unit")).fetchone()[0] == 1
        from core.ingestion.queries import general_subjects
        assert general_subjects(conn) == ["CNTech IVT Autonomous Tenets"]
    finally:
        conn.close()
    again = _run(ws, apply=True)
    assert again["in_files"] == 0 and dest.read_text(encoding="utf-8") == text


def test_an_interrupted_move_finishes_on_the_next_run_with_one_copy(env, monkeypatch):
    ws, doc = env
    from core.ingestion import governed_edit
    real = governed_edit.apply_user_edit
    calls = []

    def fail_second(path, data, **kw):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("interrupted before the DDD file was changed")
        return real(path, data, **kw)
    monkeypatch.setattr(governed_edit, "apply_user_edit", fail_second)
    with pytest.raises(OSError):
        _run(ws, apply=True)
    monkeypatch.setattr(governed_edit, "apply_user_edit", real)
    assert "cand-t1" in doc.read_text(encoding="utf-8")        # a copy, never a loss
    report = _run(ws, apply=True)
    assert report["already_in_destination"] == 2 and report["moved"] == 2
    text = (ws / "Knowledge/Library/cntech-ivt-autonomous-tenets.md").read_text(encoding="utf-8")
    assert text.count("cand:cand-t1.unit") == 1 and text.count("cand:cand-t2.unit") == 1
    assert "cand-t1" not in doc.read_text(encoding="utf-8")


def test_it_never_migrates_the_store(env):
    ws, _doc = env
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    conn.execute("PRAGMA user_version=10")       # a daemon one release behind this code
    conn.commit()
    conn.close()
    with pytest.raises(SystemExit, match="build and restart the daemon first"):
        _run(ws, apply=False)
    import sqlite3
    from core.ingestion.store import ingestion_db_path
    check = sqlite3.connect(ingestion_db_path())
    try:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 10
    finally:
        check.close()
