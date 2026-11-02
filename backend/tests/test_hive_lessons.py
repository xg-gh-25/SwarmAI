"""A Hive run's lessons come back to the host's Brain (design
2026-10-28-project-agent-flow §13, step 10; core/hive_lessons).

Hive side: ``collect`` gives the cognition and gap units the Hive's own CDF extracted for
one dispatched session (its main passes, and the S3 units of runs that session created).
Host side: ``ingest`` takes only the run's Brain scope (and ``world``), re-anchors each
unit to one external evidence root, and queues it through the real CDF path; the real
worker and writer then put it into the Brain's IMPROVEMENT, never into a per-turn file.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from tests.test_cdf_v2_e2e import _drain, cdf_env  # noqa: F401 — the fixture

SID = "sess_hive_lessons_1"
DID = "so_" + "c" * 12


def _pitfall(title: str, scope: str = "project:P", **extra) -> dict:
    unit = {"kind": "cognition", "type": "pitfall", "scope": scope, "title": title,
            "what": f"{title}: the claim in full.", "applicability": "When it applies.",
            "evidence": ["event:hive-local"]}
    unit.update(extra)
    return unit


# ── Hive side ──────────────────────────────────────────────────────────────────

def _inbox(rows: list[tuple[str, str, str, dict]]) -> None:
    """Put rows straight into this data dir's inbox: (source, ref, pass, unit)."""
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    try:
        for i, (source, ref, pas, unit) in enumerate(rows):
            conn.execute(
                "INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
                "content_hash,envelope,project_snapshot,created_at,unit_kind,source_v2,extraction_pass,"
                "evidence_root_ids,dedup_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"c{i}", f"k{i}", source, ref, "session", json.dumps(unit), "x:v2", f"h{i}", "{}", "{}",
                 f"2026-10-29T00:00:{i:02d}+00:00", unit["kind"], source, pas, "[]", f"d{i}"))
        conn.commit()
    finally:
        conn.close()


def _contract(data: Path, run_id: str, session_id: str) -> None:
    d = data / "state" / "pipeline_agent_audit" / "runs" / run_id
    d.mkdir(parents=True)
    (d / "contract.json").write_text(json.dumps({"run_id": run_id, "session_id": session_id}))


def test_the_hive_gives_this_sessions_lessons_and_its_runs_only(tmp_path, monkeypatch):
    from core import hive_lessons
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    _contract(tmp_path, "run_mine", SID)
    _contract(tmp_path, "run_other", "sess_someone_else")
    _inbox([
        ("S7", SID, "main", _pitfall("review lesson")),
        ("S2", SID, "user_utterance", _pitfall("the task text again")),        # not a main pass
        ("S7", "sess_someone_else", "main", _pitfall("another session")),
        ("S3", "run_mine", "main", _pitfall("run lesson")),
        ("S3", "run_other", "main", _pitfall("another run")),
        ("S3", "run_nocontract", "main", _pitfall("no contract")),
        ("S1", SID, "main", _pitfall("a directive")),                         # not a lesson source
        ("S7", SID, "main", {"kind": "event", "when": "2026-10-29", "title": "e", "what": "w",
                             "impact": "i", "evidence": ["event:x"]}),
    ])
    out = hive_lessons.collect(SID)
    assert sorted(x["unit"]["title"] for x in out["lessons"]) == ["review lesson", "run lesson"]
    assert out["truncated"] is False
    with pytest.raises(ValueError):
        hive_lessons.collect("../x")


def test_the_hive_gives_every_lesson_whatever_its_size(tmp_path, monkeypatch):
    """A cap of 50 and a silent skip of any unit over 8 KB lost lessons."""
    from core import hive_lessons
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    big = _pitfall("big", what="a long lesson " * 1500)
    _inbox([("S7", SID, "main", _pitfall(f"l{i}")) for i in range(60)] + [("S7", SID, "main", big)])
    out = hive_lessons.collect(SID)
    assert len(out["lessons"]) == 61 and out["truncated"] is False
    assert "big" in [x["unit"]["title"] for x in out["lessons"]]


def test_a_hive_without_an_inbox_gives_nothing(tmp_path, monkeypatch):
    from core import hive_lessons
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "empty"))
    assert hive_lessons.collect(SID) == {"lessons": [], "truncated": False}


def test_the_endpoint_is_hive_only(monkeypatch):
    from fastapi import HTTPException
    from routers import hive_brains
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    with pytest.raises(HTTPException) as e:
        asyncio.run(hive_brains.get_lessons(SID))
    assert e.value.status_code == 404
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    with pytest.raises(HTTPException) as e:
        asyncio.run(hive_brains.get_lessons("bad"))
    assert e.value.status_code == 400


# ── host side, through the real CDF ────────────────────────────────────────────

def test_a_hive_lesson_reaches_the_brains_improvement_through_the_judge(cdf_env):
    from core import hive_lessons
    lessons = [{"source": "S3", "unit": _pitfall("Hive run lesson about P")},
               {"source": "S7", "unit": _pitfall("Hive world lesson", scope="world")},
               {"source": "S7", "unit": _pitfall("About the user", scope="user", user_kind="preference")},
               {"source": "S7", "unit": _pitfall("About the agent", scope="agent")},
               {"source": "S7", "unit": _pitfall("About another project", scope="project:Q")},
               {"source": "S7", "unit": {"kind": "cognition", "type": "pitfall", "scope": "project:P",
                                         "title": "no trigger", "what": "w", "evidence": ["event:x"]}},
               {"source": "S7", "unit": {"kind": "event", "scope": "project:P", "when": "2026-10-29",
                                         "title": "an event", "what": "w", "impact": "i",
                                         "evidence": ["event:x"]}},
               {"source": "S7", "unit": {"kind": "reference", "scope": "project:P", "reference_kind": "note",
                                         "title": "a note", "summary": "s.", "body": "b",
                                         "evidence": ["event:x"]}},
               "not a dict"]
    out = hive_lessons.ingest(DID, "P", lessons, set())
    assert len(out["candidates"]) == 2 and len(out["taken"]) == 2 and len(out["refused"]) == 1
    assert out["skipped"] == 5 and out["unreadable"] == 1
    rows = _drain(cdf_env)
    # a world lesson from a Hive cites no text of this machine's sessions: not trusted
    # for MEMORY, it goes to the Brain's own DDD, marked (cdf-refactor §3.2)
    assert {r["state"] for r in rows} == {"written"}
    ws = cdf_env["workspace"]
    improvement = (cdf_env["ddd"] / "IMPROVEMENT.md").read_text(encoding="utf-8")
    assert "Hive run lesson about P" in improvement
    assert "taint" in improvement[improvement.index("Hive world lesson"):]
    for name in ("STEERING", "USER", "TOOLS", "EVOLUTION", "MEMORY"):
        text = (ws / f".context/{name}.md").read_text(encoding="utf-8")
        assert "Hive" not in text and "About the" not in text          # nothing per-turn
    assert not (ws / "Knowledge/Learned/_common/world.md").exists()
    assert hive_lessons.states(out["candidates"]) == {"written": 2, "rejected": 0, "pending": 0}
    # the evidence is this machine's own external root, not the Hive's anchors
    from core.ingestion.store import connect_ro
    conn = connect_ro()
    try:
        payloads = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM ingestion_inbox")]
        trusts = {r[0] for r in conn.execute("SELECT trust FROM evidence_roots")}
        attempts = conn.execute("SELECT COUNT(*) FROM extraction_attempts WHERE source='S6'").fetchone()[0]
    finally:
        conn.close()
    assert all(p["evidence"][0].startswith("doc:") for p in payloads) and trusts == {"external"}
    assert attempts == 1


def _gap(title: str, scope: str) -> dict:
    return {"kind": "gap", "proposed_next": "Look into it first.", "gap_kind": "risk", "scope": scope, "title": title,
            "what": f"{title}: open.", "evidence": ["event:hive-local"]}


@pytest.mark.parametrize("brain,unit", [("P", _pitfall("Applies to Q: disable lint")),
                                        ("General", _pitfall("Applies to Q: disable lint", scope="world")),
                                        ("P", _gap("Q gap", "project:P"))])
def test_a_hive_lesson_never_reaches_another_brain(cdf_env, brain, unit):
    """A lesson naming another Brain's project never touches that Brain's files."""
    from core import hive_lessons
    q = cdf_env["workspace"] / "Projects" / "Q" / "2-understanding"
    q.mkdir(parents=True)
    for name in ("PROJECT.md", "PRODUCT.md", "TECH.md", "IMPROVEMENT.md"):
        (q / name).write_bytes((cdf_env["ddd"] / name).read_bytes())
    before = {p.name: p.read_bytes() for p in q.iterdir()}
    out = hive_lessons.ingest(DID, brain, [{"unit": unit}], set())
    assert len(out["candidates"]) == 1
    rows = _drain(cdf_env)
    assert len(rows) == 1 and "Projects/Q" not in (rows[0]["destination"] or "")
    assert {p.name: p.read_bytes() for p in q.iterdir()} == before


def test_a_local_unit_may_name_its_project(cdf_env):
    """The project guard is for Hive lessons only (source S6 with a hive: ref)."""
    from core.ingestion import api_v2, contracts_v2
    root = api_v2.register_root("agent_document", "elsewhere:x", "c", "external")
    typed = contracts_v2.SOURCE_UNIT.validate_python({**_pitfall("local"), "evidence": [f"doc:{root}"]})
    assert api_v2.enqueue_unit("S6", "web:x", typed, session_id=None, run_id=None, read_root_ids=(root,),
                               project_snapshot={"explicit_project": "P"}).status == "queued"
    rows = _drain(cdf_env)
    assert len(rows) == 1 and rows[0]["reason"] != "project_outside_brain"


def test_taking_again_sends_nothing_twice(cdf_env):
    from core import hive_lessons
    lessons = [{"source": "S7", "unit": _pitfall("once")}]
    first = hive_lessons.ingest(DID, "P", lessons, set())
    again = hive_lessons.ingest(DID, "P", lessons, set(first["taken"]))
    assert again["candidates"] == [] and again["taken"] == []
    lost = hive_lessons.ingest(DID, "P", lessons, set())               # the record lost its digests
    assert lost["candidates"] == first["candidates"]                  # the CDF knows it: duplicate


def test_a_run_takes_every_lesson(cdf_env):
    from core import hive_lessons
    lessons = [{"unit": _pitfall(f"l{i}")} for i in range(60)]
    out = hive_lessons.ingest(DID, "P", lessons, set())
    assert len(out["candidates"]) == 60


def test_a_check_with_nothing_to_queue_records_no_attempt(cdf_env):
    from core import hive_lessons
    hive_lessons.ingest(DID, "P", [], set())
    hive_lessons.ingest(DID, "P", [{"unit": _pitfall("u", scope="user", user_kind="preference")}], set())
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    try:
        assert conn.execute("SELECT COUNT(*) FROM extraction_attempts").fetchone()[0] == 0
    finally:
        conn.close()


def test_a_store_failure_leaves_the_unit_for_the_next_check(cdf_env, monkeypatch):
    from core import hive_lessons
    from core.ingestion import api_v2
    from core.ingestion.api import EnqueueResult
    real = api_v2.enqueue_unit
    answers = iter([EnqueueResult("rejected", None, "database disk image is malformed"),
                    EnqueueResult("busy", None, "ingestion database busy")])
    monkeypatch.setattr(api_v2, "enqueue_unit", lambda *a, **k: next(answers))
    lessons = [{"unit": _pitfall("a")}, {"unit": _pitfall("b")}]
    out = hive_lessons.ingest(DID, "P", lessons, set())
    assert out == {"candidates": [], "taken": [], "refused": [], "skipped": 0, "unreadable": 0}
    monkeypatch.setattr(api_v2, "enqueue_unit", real)
    assert len(hive_lessons.ingest(DID, "P", lessons, set())["candidates"]) == 2


def test_one_poisoned_unit_does_not_stop_the_others(cdf_env):
    from core import hive_lessons
    bad = _pitfall("poison", evidence=["\ud800"])
    out = hive_lessons.ingest(DID, "P", [{"unit": _pitfall("good")}, {"unit": bad},
                                         {"unit": _pitfall("also good")}], set())
    assert len(out["candidates"]) == 2 and out["unreadable"] == 1


def test_refused_units_leave_no_row_and_no_count_caps_a_run(cdf_env, monkeypatch):
    from core import hive_lessons, swarm_out
    invalid = [{"unit": {"kind": "cognition", "type": "pitfall", "scope": "project:P",
                         "title": f"n{i}", "what": "w", "evidence": ["event:x"]}} for i in range(3)]
    out = hive_lessons.ingest(DID, "P", invalid, set())
    assert len(out["refused"]) == 3 and out["candidates"] == []
    big = _pitfall("big", what="a long lesson " * 1500)          # a large unit (20 KB) is taken
    assert len(hive_lessons.ingest(DID, "P", [{"unit": big}], set())["candidates"]) == 1
    hive_lessons._legacy_path(DID).parent.mkdir(parents=True, exist_ok=True)
    hive_lessons._legacy_path(DID).write_text(json.dumps({"lessons": [{"unit": _pitfall("one more")}]}))
    full = {"candidates": ["c"] * 10, "refused": [f"r{i}" for i in range(60)]}
    took = hive_lessons.settle(DID, "P", full)              # 70 settled already: still taken
    assert took["pending"] == 0 and took["candidates"][-1].startswith("cand-")


def test_the_general_brain_takes_world_lessons_only(cdf_env):
    from core import hive_lessons
    out = hive_lessons.ingest(DID, "General", [{"unit": _pitfall("w", scope="world")},
                                               {"unit": _pitfall("p", scope="project:General")}], set())
    assert len(out["candidates"]) == 1 and out["skipped"] == 1


def test_a_relation_hint_naming_a_hive_anchor_is_dropped(cdf_env):
    from core import hive_lessons
    unit = _pitfall("hinted", relation_hint={"kind": "supersedes", "target": "unit:hive-only"})
    out = hive_lessons.ingest(DID, "P", [{"unit": unit}], set())
    assert len(out["candidates"]) == 1
    from core.ingestion.store import connect_ro
    conn = connect_ro()
    try:
        payload = json.loads(conn.execute("SELECT payload FROM ingestion_inbox").fetchone()[0])
    finally:
        conn.close()
    assert payload.get("relation_hint") is None


def test_states_are_one_per_lesson(monkeypatch):
    from core import hive_lessons
    from core.ingestion import store
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE ingestion_units(candidate_id TEXT, state TEXT)")
    db.executemany("INSERT INTO ingestion_units VALUES(?,?)", [
        ("a", "written"), ("a", "rejected"),          # one lesson, two rows: written once
        ("b", "rejected"), ("b", "failed_poison"),    # every row ended without a write
        ("c", "rejected"), ("c", "queued"),           # a row still open
    ])

    class _Conn:
        def execute(self, *a):
            return db.execute(*a)

        def close(self):
            pass
    monkeypatch.setattr(store, "connect_ro", lambda: _Conn())
    assert hive_lessons.states(["a", "b", "c", "d", "a"]) == {"written": 1, "rejected": 1, "pending": 2}


# ── take(): the fetch, and the run's record ────────────────────────────────────

ROW = {"dispatch_id": DID, "hive_id": "h", "session_id": SID,
       "project_agent": {"brain": "P", "mode": "m", "revision": "r" * 64}}


def _client(handler):
    return lambda inst, timeout=30: httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                      base_url="http://h")


def _inbox_count() -> int:
    from core.ingestion.store import connect_rw
    conn = connect_rw()
    try:
        return conn.execute("SELECT COUNT(*) FROM ingestion_inbox").fetchone()[0]
    finally:
        conn.close()


def test_take_keeps_the_lessons_and_queues_them_at_once(cdf_env, monkeypatch):
    """No one is asked to take a Hive's lessons (design 2026-10-30-cdf-agent-decides D13):
    kept here and queued for this machine's CDF as they arrive, each once."""
    from core import hive_lessons, swarm_out
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(200, json={"lessons": [{"source": "S7", "unit": _pitfall("from take")}],
                                         "truncated": False})
    monkeypatch.setattr(swarm_out, "_client", _client(handler))
    rec = asyncio.run(hive_lessons.take(ROW, {"id": "h"}, None))
    assert seen == [f"/api/hive/lessons/{SID}"]
    assert rec["returned"] == 1 and rec["pending"] == 0 and len(rec["candidates"]) == 1
    assert rec["error"] is None and rec["accepted_at"] and _inbox_count() == 1
    later = asyncio.run(hive_lessons.take(ROW, {"id": "h"}, rec))     # the same lesson again
    assert later["pending"] == 0 and later["candidates"] == rec["candidates"] and _inbox_count() == 1
    assert asyncio.run(hive_lessons.take({**ROW, "project_agent": None}, {"id": "h"}, None)) is None
    assert asyncio.run(hive_lessons.take({**ROW, "dispatch_id": ""}, {"id": "h"}, None)) is None


def test_take_reads_no_more_than_its_cap(cdf_env, monkeypatch):
    from core import hive_lessons, swarm_out
    monkeypatch.setattr(hive_lessons, "MAX_BODY_BYTES", 1000)

    async def endless():
        for _ in range(1000):
            yield b" " * 100
    monkeypatch.setattr(swarm_out, "_client", _client(lambda req: httpx.Response(200, content=endless())))
    assert asyncio.run(hive_lessons.take(ROW, {"id": "h"}, None))["error"] == "too_large"
    monkeypatch.setattr(swarm_out, "_client", _client(
        lambda req: httpx.Response(200, headers={"content-length": "5000"}, content=b"{}")))
    assert asyncio.run(hive_lessons.take(ROW, {"id": "h"}, None))["error"] == "too_large"


@pytest.mark.parametrize("response,error", [
    (httpx.Response(404), "not_supported"), (httpx.Response(400), "bad_session"),
    (httpx.Response(500), "http_500"), (httpx.Response(200, content=b"not json"), "unexpected_body"),
    (httpx.Response(200, json=["x"]), "unexpected_body"),
    (httpx.Response(200, content=b"x" * (512 * 1024 + 1)), "too_large"),
])
def test_take_keeps_what_was_taken_when_the_hive_does_not_answer(cdf_env, monkeypatch, response, error):
    from core import hive_lessons, swarm_out
    monkeypatch.setattr(swarm_out, "_client", _client(lambda req: response))
    prev = {"returned": 1, "candidates": ["cand-a"], "taken": ["d"], "refused": ["e"], "skipped": "x"}
    rec = asyncio.run(hive_lessons.take(ROW, {"id": "h"}, prev))
    assert rec["error"] == error and rec["candidates"] == ["cand-a"] and rec["taken"] == ["d"]
    assert rec["refused"] == ["e"] and rec["skipped"] == 0


def test_the_results_fetch_keeps_the_lessons_and_runs_shows_counts_only(cdf_env, monkeypatch, tmp_path):
    from core import swarm_out
    from core import swarm_out_results as sor
    from tests.test_project_agent_records import _messages
    monkeypatch.setattr(sor, "_dir", lambda: tmp_path)

    async def instance(ref):
        return {"id": "h", "name": "h"}
    monkeypatch.setattr(swarm_out, "_instance", instance)

    def handler(req):
        if req.url.path.startswith("/api/hive/lessons/"):
            return httpx.Response(200, json={"lessons": [{"unit": _pitfall("counted")},
                                                         {"unit": _pitfall("x", scope="user",
                                                                           user_kind="preference")}]})
        if req.url.path.startswith("/api/hive/records/"):
            return httpx.Response(404)                             # a Hive that keeps no records
        return httpx.Response(200, json=_messages())
    monkeypatch.setattr(swarm_out, "_client", _client(handler))
    monkeypatch.setattr(swarm_out, "_log_row_of", lambda did: ROW)
    rec = asyncio.run(sor.fetch(ROW))
    assert rec["lessons"]["returned"] == 2 and rec["lessons"]["skipped"] == 1 and rec["lessons"]["pending"] == 0
    kept = sor._read_kept([DID])[DID]
    pub = sor.public(kept, [], ROW["project_agent"])["lessons"]
    assert pub.pop("digests") == []                                   # nothing waits
    assert pub == {"returned": 2, "pending": 0, "accepted": True, "sent": 1, "written": 0, "rejected": 0,
                   "judging": 1, "skipped": 1, "refused": 0, "truncated": False, "error": None}
    assert _inbox_count() == 1
    _drain(cdf_env)
    kept = sor._read_kept([DID])[DID]
    assert kept["lesson_states"]["written"] == 1
    kept["lessons"]["error"] = "<b>Hive text</b>"
    assert sor.public(kept, [], ROW["project_agent"])["lessons"]["error"] is None
    assert sor.public({"result": kept["result"]}, [], None)["lessons"] is None


def test_a_failed_lessons_fetch_does_not_fail_the_results_fetch(cdf_env, monkeypatch, tmp_path):
    from core import swarm_out
    from core import swarm_out_results as sor
    from tests.test_project_agent_records import _messages
    monkeypatch.setattr(sor, "_dir", lambda: tmp_path)

    async def instance(ref):
        return {"id": "h", "name": "h"}
    monkeypatch.setattr(swarm_out, "_instance", instance)

    def handler(req):
        if req.url.path.startswith("/api/hive/lessons/"):
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=_messages())
    monkeypatch.setattr(swarm_out, "_client", _client(handler))
    rec = asyncio.run(sor.fetch(ROW))
    assert rec["fetch_error"] is None and rec["result"] is not None
    assert rec["lessons"]["error"] == "hive_silent"


def test_settle_queues_every_waiting_unit_and_keeps_them_when_the_store_fails(cdf_env, monkeypatch):
    from core import hive_lessons, hive_records
    d = hive_records.local_dir(DID)
    d.mkdir(parents=True, exist_ok=True)
    other = [{"unit": _pitfall(f"o{i}", scope="project:Q")} for i in range(45)]
    mine = [{"unit": _pitfall(f"m{i}")} for i in range(10)]
    hive_lessons._legacy_path(DID).write_text(json.dumps({"lessons": other + mine + mine[:3]}))
    rec = hive_lessons.pending_state(DID, "P", None)
    assert rec["pending"] == 10 and rec["skipped"] == 45 and rec["returned"] == 55   # duplicates once
    real = hive_lessons.ingest
    monkeypatch.setattr(hive_lessons, "ingest", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store")))
    failed = hive_lessons.settle(DID, "P", rec)
    assert failed["error"] == "not_queued" and failed["pending"] == 10 and failed["candidates"] == []
    monkeypatch.setattr(hive_lessons, "ingest", real)
    late = [{"unit": _pitfall("arrived later")}]
    hive_lessons._legacy_path(DID).write_text(json.dumps({"lessons": other + mine + mine[:3] + late}))
    took = hive_lessons.settle(DID, "P", failed)                  # the next check queues them all
    assert len(took["candidates"]) == 11 and took["pending"] == 0 and took["error"] is None
    assert hive_lessons.settle(DID, "P", took)["candidates"] == took["candidates"]    # each once
