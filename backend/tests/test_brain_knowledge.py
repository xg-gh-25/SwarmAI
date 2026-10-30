"""What a Brain learned (design docs/design/2026-10-30-brain-change-feed.md §7).

Questions, one per test:
- does a CDF entry of a Brain that is not in git show, with its layer and type, counted
  this week, newest first, and a hand-written entry or an old tombstone not show?
- when the write record is gone, does it fall back to the collected time, and with neither
  only show under "all", last?
- is an entry another one adds to named on the newer one, and a superseded entry hidden?
- does the General Brain list the CDF entries of Knowledge/Library?
- is a file that is not UTF-8 skipped instead of failing the view?
- does a withdrawal delete the entry (no tombstone, metadata line with it), keep what CDF
  knew about it, show in the Brain's withdrawn list, and reach the evaluation export?
- is a whole-document unit refused, and does a stale withdrawal answer 409, never 500?
- does the next EVOLUTION number skip one that was withdrawn?
- is a file that did not change parsed only once?
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
DOC = "Projects/P/2-understanding/TECH.md"


def _entry(kind, title, unit):
    return (f"- [{kind}] **{title}** The body of {title}. <!-- cand:{unit} -->\n"
            "  <!-- ref:0 | last:2026-10-30 | decay:active | source:S7 -->\n")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    ws = tmp_path / "SwarmWS"
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    und = ws / "Projects/P/2-understanding"
    und.mkdir(parents=True)
    (ws / "Projects/P/.project.json").write_text('{"name":"P"}', encoding="utf-8")
    (und / "TECH.md").write_text(
        "# TECH\n\n## Architecture\n\n"
        + _entry("decision", "New decision", "cand-n.unit") + "\n"
        + _entry("pitfall", "Old pitfall", "cand-o.unit") + "\n"
        + _entry("pitfall", "Adds to it", "cand-a.unit") + "\n"
        + _entry("model", "Replaced model", "cand-r.unit") + "\n"
        + "- [guideline] **Hand written** no marker here.\n\n"
        + "- [redacted] **Withdrawn** [...] <!-- cand:cand-t.unit | redacted:abc -->\n\n"
        + "## Runtime Traps\n\n" + "- [gap:bug] **Open thing** still open. <!-- cand:cand-g.unit -->\n",
        encoding="utf-8")
    lib = ws / "Knowledge/Library"
    lib.mkdir(parents=True)
    (lib / "general-knowledge.md").write_text("# General\n\n## Pitfalls\n\n" + _entry("pitfall", "World fact", "cand-w.unit")
                                              + "\n" + _entry("link", "A link", "cand-l.unit"), encoding="utf-8")
    (lib / "reference.md").write_text("# A reference without CDF entries\n", encoding="utf-8")
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    conn.execute("INSERT OR REPLACE INTO ingestion_owner(id,epoch,pid,started_at) VALUES(1,1,1,'t')")
    times = {"cand-n.unit": NOW - timedelta(days=1), "cand-o.unit": NOW - timedelta(days=20),
             "cand-a.unit": NOW - timedelta(days=2), "cand-r.unit": NOW - timedelta(days=3),
             "cand-g.unit": NOW - timedelta(days=1), "cand-w.unit": NOW - timedelta(days=1),
             "cand-l.unit": NOW - timedelta(days=1)}          # a [link]: no layer, not shown, not counted
    for unit, at in times.items():
        cid = unit.removesuffix(".unit")
        conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
                     "content_hash,envelope,project_snapshot,created_at,source_v2) VALUES(?,?,'S7','sess-1','session',"
                     "'{}','v2','h','{}','{}',?,'S7')", (cid, cid, at.isoformat()))
        conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,owner_epoch,updated_at)"
                     " VALUES(?,?,?,'written',1,?)", (unit, cid, json.dumps({"title": unit}), at.isoformat()))
        if unit != "cand-o.unit":          # its write record was purged (30-day cleanup)
            conn.execute("INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,step,created_at,updated_at)"
                         " VALUES(?, 'ingestion_unit', ?, ?, 'done', ?, ?)",
                         (f"ingestion:{unit}", unit, DOC, at.isoformat(), at.isoformat()))
    conn.execute("CREATE TABLE IF NOT EXISTS ingestion_eval_log(unit_id TEXT, at TEXT, source TEXT, state TEXT, "
                 "reason TEXT, record TEXT)")
    conn.execute("INSERT INTO ingestion_eval_log(unit_id,at,source,state,reason,record) VALUES"
                 "('cand-o.unit', ?, 'S7', 'written', 'new', '{}')", ((NOW - timedelta(days=20)).isoformat(),))
    later = (NOW - timedelta(hours=6)).isoformat()      # a second finished write of the same unit
    conn.execute("INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,step,created_at,updated_at)"
                 " VALUES('ingestion:cand-n.unit:2', 'ingestion_unit', 'cand-n.unit', ?, 'done', ?, ?)", (DOC, later, later))
    a = f"{DOC}#Architecture#cand:"
    conn.execute("INSERT INTO ingestion_relations VALUES(?,?,?,'t',0)", (a + "cand-a.unit", "derived_from", a + "cand-o.unit"))
    conn.execute("INSERT INTO ingestion_relations VALUES(?,?,?,'t',0)", (a + "cand-n.unit", "supersedes", a + "cand-r.unit"))
    conn.commit()
    conn.close()
    return ws


def _titles(out):
    return [i["title"] for layer in out["layers"] for i in layer["items"]]


def test_a_brain_not_in_git_shows_what_it_learned_by_layer(ws):
    from core.brain_knowledge import knowledge
    out = knowledge(ws, "P", "week", now=NOW)
    layers = {l["layer"]: [i["title"] for i in l["items"]] for l in out["layers"]}
    assert layers == {"meta": [], "cognitive": ["New decision"], "operational": ["Adds to it"]}
    assert [g["title"] for g in out["gaps"]] == ["Open thing"]
    assert out["counts"] == {"week": 3, "all": 4}       # decision, adds, gap; the 20-day pitfall
    item = out["layers"][1]["items"][0]
    assert (item["type"], item["layer"], item["section"], item["doc"]) == ("decision", "cognitive", "Architecture", "TECH.md")
    assert (item["source"], item["source_ref"]) == ("S7", "sess-1")
    assert item["written_at"] == (NOW - timedelta(hours=6)).isoformat()     # the latest write
    assert "<!--" not in item["text"]
    assert "Hand written" not in _titles(knowledge(ws, "P", "all", now=NOW))


def test_without_its_write_record_an_entry_keeps_its_collected_time(ws):
    from core.brain_knowledge import knowledge
    month = knowledge(ws, "P", "month", now=NOW)
    old = next(i for l in month["layers"] for i in l["items"] if i["title"] == "Old pitfall")
    assert old["collected"] is True and old["written_at"]
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    conn.execute("DELETE FROM ingestion_eval_log")
    conn.commit(); conn.close()
    assert "Old pitfall" not in _titles(knowledge(ws, "P", "month", now=NOW))
    assert _titles(knowledge(ws, "P", "all", now=NOW))[-1] == "Old pitfall"       # unknown time: last


def test_an_addition_names_what_it_adds_to_and_a_superseded_entry_is_hidden(ws):
    from core.brain_knowledge import knowledge
    out = knowledge(ws, "P", "all", now=NOW)
    adds = next(i for l in out["layers"] for i in l["items"] if i["title"] == "Adds to it")
    assert adds["adds_to"] == "Old pitfall"
    assert "Replaced model" not in _titles(out)


def test_the_general_brain_lists_the_cdf_entries_of_the_library(ws):
    from core.brain_knowledge import brain_files, knowledge
    assert [p.name for p in brain_files(ws, "General")] == ["general-knowledge.md", "reference.md"]
    assert _titles(knowledge(ws, "General", "week", now=NOW)) == ["World fact"]      # the [link] is not shown


def test_a_withdrawal_from_the_general_brain_stays_listed_after_its_files_last_entry(ws):
    from core.brain_knowledge import knowledge
    from core.ingestion import governed_edit
    path = "Knowledge/Library/general-knowledge.md"
    governed_edit.redact(f"{path}#Pitfalls#cand:cand-w.unit", "wrong", actor="user")
    governed_edit.redact(f"{path}#Pitfalls#cand:cand-l.unit", "wrong too", actor="user")
    assert "cand:" not in (ws / path).read_text(encoding="utf-8")
    out = knowledge(ws, "General", "all", now=NOW)
    assert out["withdrawn"]["count"] == 2 and _titles(out) == []


def test_an_unknown_range_is_the_week_and_the_route_refuses_it(ws, monkeypatch):
    from core.brain_knowledge import knowledge
    out = knowledge(ws, "P", "year", now=NOW)
    assert out["since"] == "week" and _titles(out) == _titles(knowledge(ws, "P", "week", now=NOW))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import ddd_brain
    monkeypatch.setattr(ddd_brain, "_workspace_root", lambda: ws)
    app = FastAPI()
    app.include_router(ddd_brain.router)
    client = TestClient(app)
    base = next(r.path for r in ddd_brain.router.routes if r.path.endswith("/knowledge")).replace("{name}", "P")
    assert client.get(base, params={"since": "year"}).status_code == 422
    assert client.get(base, params={"since": "month"}).json()["since"] == "month"


def test_a_whole_document_refusal_closes_no_gap_and_is_not_retried(monkeypatch, tmp_path):
    from core.ingestion import gaps, governed_edit
    doc = tmp_path / "IMPROVEMENT.md"
    text = "# I\n\n## Open Gaps\n\n- [gap:bug] **X** open. <!-- cand:cand-x.unit -->\n"
    doc.write_text(text, encoding="utf-8")
    unit = type("U", (), {"anchor": "a#b#cand:cand-x.unit", "identity": "cand:cand-x.unit", "text": text[text.index("- [gap"):-1],
                          "byte_start": text.index("- [gap"), "byte_end": len(text) - 1})()
    monkeypatch.setattr(gaps, "_improvement", lambda project: doc)
    monkeypatch.setattr(gaps, "_gap_units", lambda path: [unit])
    monkeypatch.setattr(gaps, "_is_open", lambda u: True)

    def whole(*a, **k):
        raise governed_edit.WholeDocument("whole")
    monkeypatch.setattr(governed_edit, "withdraw", whole)
    assert gaps.close_gap("P", "cand-x", closed_by="run", note="done") == "unparseable"


def test_a_file_that_is_not_utf8_is_skipped(ws):
    from core.brain_knowledge import knowledge
    (ws / "Projects/P/2-understanding/PROJECT.md").write_bytes(b"# P\n\n- [decision] **x** \x93bad <!-- cand:c-x -->\n")
    assert "New decision" in _titles(knowledge(ws, "P", "all", now=NOW))


def test_a_withdrawal_deletes_the_entry_keeps_its_record_and_reaches_the_export(ws):
    from core.brain_knowledge import knowledge
    from core.ingestion import eval_log, governed_edit
    from core.ingestion.store import connect_ro
    governed_edit.redact(f"{DOC}#Architecture#cand:cand-n.unit", "wrong", actor="user")
    text = (ws / DOC).read_text(encoding="utf-8")
    assert "New decision" not in text and text.count("redacted:") == 1     # only the old tombstone
    assert "\n\n\n" not in text                                    # the metadata line left with it
    from core.ingestion.store import connect_rw
    conn = connect_rw()                    # an agent cleanup and an undone withdrawal: not the person's
    row = conn.execute("SELECT * FROM ingestion_writes WHERE op_kind='user_redact'").fetchone()
    cols = [d[1] for d in conn.execute("PRAGMA table_info(ingestion_writes)")]
    for i, change in enumerate(({"actor": "agent", "directed_by": "user"}, {"status": "restored"})):
        rec = dict(zip(cols, row))
        rec["op_id"] = f"user-redact:other{i}"
        rec["relations"] = json.dumps([json.loads(rec["relations"])[0] | change])
        conn.execute(f"INSERT INTO ingestion_writes({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                     [rec[c] for c in cols])
    conn.commit(); conn.close()
    out = knowledge(ws, "P", "all", now=NOW)
    assert "New decision" not in _titles(out)
    assert out["withdrawn"]["count"] == 1 and out["withdrawn"]["items"][0]["title"] == "New decision"
    conn = connect_ro()
    try:
        rel = json.loads(conn.execute("SELECT relations FROM ingestion_writes WHERE op_kind='user_redact' "
                                      "ORDER BY rowid").fetchone()[0])[0]
        exported = [r for r in eval_log.export(conn, "2000-01-01") if r["origin"] == "withdrawn"]
    finally:
        conn.close()
    assert rel["unit_id"] == "cand-n.unit" and rel["source_ref"] == "sess-1" and rel["written_at"]
    assert [(r["stage"], r["label"], r["reason"], r["unit_id"], r["source"]) for r in exported] == \
        [("withdrawn", "user_withdrawn", "wrong", "cand-n.unit", "S7")]          # its own stage, never a judge outcome


def test_a_whole_document_unit_is_not_withdrawn(ws):
    from core.ingestion import governed_edit
    from core.ingestion.units import parse_units
    doc = ws / "Projects/P/2-understanding/PRODUCT.md"
    doc.write_text("# Product\n\nOne paragraph, no entry.\n", encoding="utf-8")
    (unit,) = parse_units(doc, root=ws)
    with pytest.raises(ValueError):
        governed_edit.redact(unit.anchor, "x", actor="user")
    assert doc.read_text(encoding="utf-8") == "# Product\n\nOne paragraph, no entry.\n"


def test_a_stale_withdrawal_answers_409_not_500(ws, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from core.ingestion.api import StaleRevision
    from routers import ingestion_redact as route
    calls = []

    def stale(anchor, reason, *, actor):
        calls.append(1)
        raise StaleRevision("changed")
    monkeypatch.setattr(route, "redact", stale)
    request = type("R", (), {"client": type("C", (), {"host": "127.0.0.1"})()})()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(route.redact_ingestion_unit(route.RedactRequest(anchor="a#b#c", reason="r"),
                                                request, user_action="redact"))
    assert exc.value.status_code == 409 and len(calls) == 2


def test_a_withdrawn_evolution_number_is_not_given_again():
    from core.ingestion.writer import _next_evolution_id
    content = "# E\n\n## Corrections Captured\n\n### C070 | 2026-10-29\n- x\n"
    assert _next_evolution_id(content, "Corrections Captured", "C") == 71
    assert _next_evolution_id(content, "Corrections Captured", "C",
                              ["### C072 | 2026-10-30\n- withdrawn"]) == 73


def test_a_file_that_did_not_change_is_parsed_once(ws, monkeypatch):
    from core import brain_knowledge
    import core.ingestion.units as units
    real, calls = units.parse_units, []

    def counting(path, root=None):
        calls.append(str(path))
        return real(path, root=root)
    monkeypatch.setattr(units, "parse_units", counting)
    brain_knowledge._cache.clear()
    brain_knowledge.knowledge(ws, "P", "week", now=NOW)
    first = len(calls)
    brain_knowledge.knowledge(ws, "P", "week", now=NOW)
    assert first > 0 and len(calls) == first


def test_the_home_count_is_what_each_brain_learned_this_week(ws):
    from core.brain_knowledge import week_counts
    assert week_counts(ws, ["P", "General"], now=NOW) == {"P": 3, "General": 1}


def test_the_knowledge_view_refuses_a_name_that_is_not_a_brain(ws, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from routers import ddd_brain
    monkeypatch.setattr(ddd_brain, "_workspace_root", lambda: ws)
    monkeypatch.setattr(ddd_brain, "_projects_root", lambda: ws / "Projects")
    assert _titles(asyncio.run(ddd_brain.get_brain_knowledge("General", "week"))) == ["World fact"]
    for name in ("Nope", "..", "../Knowledge"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ddd_brain.get_brain_knowledge(name, "week"))
        assert exc.value.status_code == 404


def test_the_parse_cache_is_per_workspace_root(ws):
    """Anchors are relative to the root a file is parsed under: the same file under
    another root must not get the first root's anchors from the cache."""
    from core import brain_knowledge
    brain_knowledge._cache.clear()
    path = ws / DOC
    first = brain_knowledge._parse(path, ws)[0]["anchor"]
    other = brain_knowledge._parse(path, ws / "Projects")[0]["anchor"]
    assert first.startswith("Projects/P/") and other.startswith("P/")
