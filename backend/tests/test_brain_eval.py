"""Brain Eval (core.brain_eval; design docs/design/2026-11-01-brain-eval-and-runs.md §2.2).

Questions, one per test:
- is the healthy rate the share judged useful among this Brain's scored DDD / GAP
  entries, the person's verdict over the judge's, other Brains and destinations left out,
  and is a weak entry still in force listed with where it is (a gone one not)?
- below ten scored entries, is the rate None with the count (never a rate from 3)?
- is an exact copy of an entry in another main file counted as a duplicate?
- are the open gaps counted, with the oldest one's age?
- is the used rate the share of runs whose documents cite an entry still in this
  Brain's main files, a Library id or a removed entry not counting, a run without
  documents left out?
- is missed use the S3 lessons CDF found to repeat this Brain's entries (reinforce)?
- is written-then-used read for this Brain's writes only?
- does a store that cannot be read give None, never 0, and the route 404 an unknown Brain?
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _entry(title, unit, kind="decision", extra=""):
    return f"- [{kind}] **{title}** The body of {title}.{extra} <!-- cand:{unit} -->\n"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    ws = tmp_path / "SwarmWS"
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    und = ws / "Projects/P/2-understanding"
    und.mkdir(parents=True)
    (ws / "Projects/P/.project.json").write_text('{"name":"P"}', encoding="utf-8")
    (ws / "Projects/Q/2-understanding").mkdir(parents=True)
    (ws / "Projects/Q/.project.json").write_text('{"name":"Q"}', encoding="utf-8")
    (und / "TECH.md").write_text(
        "# TECH\n\n## Conventions\n\n" + _entry("Kept", "cand-keep.unit") + "\n"
        + _entry("Weak one", "cand-weak.unit") + "\n"
        + _entry("Copied lesson", "cand-c1.unit", "pitfall") + "\n", encoding="utf-8")
    (und / "IMPROVEMENT.md").write_text(
        "# IMPROVEMENT\n\n## What Worked\n\n" + _entry("Copied lesson", "cand-c2.unit", "pitfall") + "\n"
        + "## What to Watch For\n\n"
        + _entry("Old gap", "cand-g1.unit", "gap:bug", " gap kind: bug (2026-10-20)") + "\n"
        + _entry("New gap", "cand-g2.unit", "gap:bug", " gap kind: bug (2026-10-30)") + "\n"
        + "- [x] **Closed gap** done. gap kind: bug (2026-10-01) <!-- cand:cand-g3.unit -->\n",
        encoding="utf-8")
    return ws


def _data() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir())


def _verdicts(rows):
    from core.ingestion import health
    conn = health.connect_health()
    for i, (unit, dest, project, verdict, user) in enumerate(rows):
        conn.execute(
            "INSERT INTO verdicts(unit_id,dest,project,title,written_at,judged_at,verdict,klass,"
            "reason_en,reason_zh,judge_model,judge_version,user_verdict) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (unit, dest, project, f"title {unit}", (NOW - timedelta(days=2, minutes=i)).isoformat(),
             NOW.isoformat(), verdict, None, "why", "为什么", "m", "v", user))
    conn.commit()
    conn.close()


def _ingestion(writes=(), uses=(), s3=()):
    conn = sqlite3.connect(_data() / "ingestion.db")
    conn.executescript(
        "CREATE TABLE ingestion_writes (unit_id TEXT, target_path TEXT, created_at TEXT, op_kind TEXT, step TEXT);"
        "CREATE TABLE unit_usage (anchor TEXT, kind TEXT, at TEXT);"
        "CREATE TABLE injection_selection (id INTEGER PRIMARY KEY, file_path TEXT, selected_anchors TEXT);"
        "CREATE TABLE ingestion_inbox (id TEXT, source TEXT, source_ref TEXT, payload TEXT);"
        "CREATE TABLE ingestion_units (unit_id TEXT, candidate_id TEXT, action TEXT, project TEXT, updated_at TEXT);")
    for unit, path, at in writes:
        conn.execute("INSERT INTO ingestion_writes VALUES (?,?,?,'ingestion_unit','done')", (unit, path, at.isoformat()))
    for anchor, kind, at in uses:
        conn.execute("INSERT INTO unit_usage VALUES (?,?,?)", (anchor, kind, at.isoformat()))
    for i, (run, action, project, title) in enumerate(s3):
        conn.execute("INSERT INTO ingestion_inbox VALUES (?,?,?,?)", (f"c{i}", "S3", run, json.dumps({"title": title})))
        conn.execute("INSERT INTO ingestion_units VALUES (?,?,?,?,?)", (f"u{i}", f"c{i}", action, project, NOW.isoformat()))
    conn.commit()
    conn.close()


def _run(ws, project, run_id, refs=None):
    d = ws / "Projects" / project / ".artifacts/runs" / run_id
    (d / "artifacts").mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"created_at": NOW.isoformat(), "requirement": run_id}), encoding="utf-8")
    if refs is not None:
        (d / "artifacts/requirements.json").write_text(json.dumps({"knowledge_refs": refs}), encoding="utf-8")


def _id(ws, title):
    from core.addressing import ddd_units
    return next(u.id for u in ddd_units("P").units if title in u.text)


def test_health_uses_the_persons_verdict_and_lists_weak_entries_still_in_force(ws):
    from core import brain_eval
    rows = [(f"cand-u{i}.unit", "DDD", "P", "useful", None) for i in range(8)]
    rows += [("cand-weak.unit", "DDD", "P", "useful", "weak"),         # the person overrides
             ("cand-gone.unit", "GAP", "P", "withdraw", None),         # no longer in the Brain
             ("cand-q.unit", "DDD", "Q", "weak", None),                # another Brain
             ("cand-m.unit", "MEMORY", "P", "weak", None)]             # not a Brain destination
    _verdicts(rows)
    h = brain_eval.compute("P", workspace=ws, now=NOW)["health"]
    assert h["scored"] == 10
    assert h["counts"] == {"useful": 8, "weak": 1, "withdraw": 1}
    assert h["rate"] == 0.8
    assert [(f["unit_id"], f["verdict"], f["by_person"], f["rel"]) for f in h["flagged"]] == [
        ("cand-weak.unit", "weak", True, "Projects/P/2-understanding/TECH.md")]
    assert h["flagged"][0]["line"] == 7


def test_too_few_scored_entries_give_no_rate(ws):
    from core import brain_eval
    _verdicts([(f"cand-u{i}.unit", "DDD", "P", "useful", None) for i in range(3)])
    h = brain_eval.compute("P", workspace=ws, now=NOW)["health"]
    assert (h["rate"], h["scored"]) == (None, 3)


def test_a_copy_across_main_files_is_a_duplicate(ws):
    from core import brain_eval
    d = brain_eval.compute("P", workspace=ws, now=NOW)["health"]["duplicates"]
    assert (d["groups"], d["extra"]) == (1, 1)
    assert {p["rel"].split("/")[-1] for p in d["shown"][0]["places"]} == {"TECH.md", "IMPROVEMENT.md"}


def test_open_gaps_are_counted_with_the_oldest_age(ws):
    from core import brain_eval
    g = brain_eval.compute("P", workspace=ws, now=NOW)["health"]["gaps"]
    assert g == {"open": 2, "oldest_days": 12, "oldest_title": "Old gap"}


def test_used_counts_runs_citing_an_entry_still_in_the_brain(ws):
    from core import brain_eval
    kept = _id(ws, "Kept")
    for i in range(4):
        _run(ws, "P", f"run_cite{i}", [kept, "libxxxxx"])
    for i in range(5):
        _run(ws, "P", f"run_lib{i}", ["libxxxxx", "deadbeef"])       # a Library id, a removed entry
    _run(ws, "P", "run_none", [])
    _run(ws, "P", "run_direct")                                        # no documents: cannot cite
    _run(ws, "Q", "run_q", [kept])                                     # another Brain's run
    u = brain_eval.compute("P", workspace=ws, now=NOW)["used"]
    assert (u["runs"], u["citing"], u["rate"]) == (10, 4, 0.4)
    cite = next(r for r in u["shown"] if r["run_id"] == "run_cite0")
    assert [c["id"] for c in cite["cited"]] == [kept] and cite["other_refs"] == 1


def test_missed_use_is_the_reinforced_s3_lessons_of_this_brain(ws):
    from core import brain_eval
    _ingestion(s3=[("run_a", "reinforce", "P", "Same as Kept"), ("run_b", "add", "P", "New"),
                   ("run_c", "reinforce", "Q", "Q's")])
    u = brain_eval.compute("P", workspace=ws, now=NOW)["used"]
    assert u["missed_total"] == 1
    assert (u["missed"][0]["run_id"], u["missed"][0]["title"]) == ("run_a", "Same as Kept")


def test_written_then_used_reads_this_brains_writes_only(ws):
    from core import brain_eval
    old = NOW - timedelta(days=20)
    writes = [(f"cand-p{i}.unit", "Projects/P/2-understanding/TECH.md", old) for i in range(10)]
    writes += [(f"cand-q{i}.unit", "Projects/Q/2-understanding/TECH.md", old) for i in range(10)]
    uses = [(f"Projects/P/2-understanding/TECH.md#Conventions#cand:cand-p{i}.unit", "read_by_agent", old + timedelta(days=1))
            for i in range(3)]
    _ingestion(writes=writes, uses=uses)
    w = brain_eval.compute("P", workspace=ws, now=NOW)["used"]["written_used"]
    assert (w["mature_written"], w["mature_used"], w["rate"]) == (10, 3, 0.3)


def test_unreadable_stores_are_unknown_and_an_unknown_brain_is_404(ws):
    from fastapi import HTTPException
    from core import brain_eval
    from routers import ddd_brain
    r = brain_eval.compute("P", workspace=ws, now=NOW)        # no cdf_health.db, no ingestion.db
    assert (r["health"]["rate"], r["health"]["scored"]) == (None, None)
    assert r["used"]["missed_total"] is None and r["used"]["written_used"] is None
    import asyncio
    with pytest.raises(HTTPException) as e:
        asyncio.run(ddd_brain.get_brain_eval("NoSuchBrain"))
    assert e.value.status_code == 404
