"""The monthly brain mistake audit (design docs/design/2026-10-30-brain-driven.md §2.8): the
model judges what a mistake needed and which entry states it; whether that entry was in
the brain beforehand and whether it was delivered come from the records; the job writes
only its report."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from tests.test_cdf_v2_e2e import cdf_env  # noqa: F401  (fixture)

NOW = datetime(2026, 12, 1, 2, tzinfo=timezone.utc)          # audits 2026-11
TECH = """# Tech

## Runtime Traps

- [pitfall] **Relay filter checks every session id field** A proxy event filter must check
  newSessionId too, not only sessionId. (2026-11-02)
- [pitfall] **Body cap counts decoded bytes** A size cap must bound the decompressed body. (2026-11-03)
- [pitfall] **Late lesson about lock order** Take the spawn lock first. (2026-11-25)
- [pitfall] **Old retry rule** Retry a failed send three times. (2026-11-01)
"""
FINDINGS = {
    "CF-1": "The filter checked only sessionId; newSessionId rebinds the tab.",
    "CF-2": "The cap counted compressed bytes; a gzip body expands past it.",
    "CF-3": "The lock order was reversed and deadlocked.",
    "CF-4": "Sending retried forever instead of failing loudly.",
    "CF-5": "Wording nit in a comment.",
    "CF-6": "A mistake no entry covers at all.",
}
# the search words the model gives (a real model names the solution, not the symptom)
QUERY = {"CF-1": "relay filter session id newSessionId", "CF-2": "body cap decoded bytes",
         "CF-3": "lock order spawn lock", "CF-4": "retry failed send"}
# needed knowledge -> (index into the candidates whose head contains this word, stale)
MATCH = {"CF-1": ("Relay filter", False), "CF-2": ("Body cap", False),
         "CF-3": ("Late lesson", False), "CF-4": ("Old retry", True), "CF-6": (None, False)}


def _hashes(ws: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(ws.rglob("*.md")) if "JobResults" not in str(p)}


def _a_record_before_the_month():
    """The CDF records began before the audited month (else the month is skipped)."""
    from core.ingestion.api import connect_rw
    conn = connect_rw()
    conn.execute("INSERT OR IGNORE INTO ingestion_writes(op_id,op_kind,target_path,step,created_at,updated_at) "
                 "VALUES ('w0','user_edit','x','done','2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00')")
    conn.commit()
    conn.close()


def _setup(cdf_env, *, knowledge_ids=()):
    ws = cdf_env["workspace"]
    _a_record_before_the_month()
    (cdf_env["ddd"] / "TECH.md").write_text(TECH, encoding="utf-8")
    run = ws / "Projects/P/.artifacts/runs/run_x"
    (run / "artifacts").mkdir(parents=True, exist_ok=True)
    (run / "run.json").write_text(json.dumps({"created_at": "2026-11-20T08:00:00+00:00",
                                              "project": "P"}))
    (run / "run_audit.json").write_text(json.dumps({"details": {"findings": [
        {"canonical_id": cid, "claim": claim, "severity": "MEDIUM"} for cid, claim in FINDINGS.items()]}}))
    (run / "artifacts/knowledge.md").write_text(
        "# Knowledge\n\n" + "\n".join(f"- `{i}` · TECH.md#Runtime Traps" for i in knowledge_ids))
    return ws


def _ask(prompt: str, schema: dict) -> dict:
    if "items" in schema["properties"]:
        return {"items": [{"id": cid, "real": cid != "CF-5", "class": "A",
                           "knowledge": f"needed for {cid}", "query": QUERY.get(cid, claim)}
                          for cid, claim in FINDINGS.items() if f"id={cid} " in prompt]}
    cid = prompt.split("Needed knowledge: needed for ", 1)[1].split("\n", 1)[0]
    head, stale = MATCH[cid]
    entries = [line for line in prompt.splitlines() if line.startswith("[")]
    idx = next((i for i, e in enumerate(entries) if head and head in e), -1)
    return {"reasoning": "stub", "match": idx, "stale": stale}


def _ids(ws: Path) -> dict[str, str]:
    from core import addressing
    return {u.title: u.id for u in addressing.ddd_units("P").units}


def test_each_outcome_comes_from_the_records(cdf_env):
    from core.ingestion.api import connect_rw
    from jobs.handlers.brain_mistake_audit import run_brain_mistake_audit
    connect_rw().close()
    ws = _setup(cdf_env)
    ids = _ids(ws)
    delivered = next(i for t, i in ids.items() if "Relay filter" in t)
    _setup(cdf_env, knowledge_ids=[delivered])
    before = _hashes(ws)
    res = run_brain_mistake_audit({"workspace": str(ws)}, ask=_ask, now=NOW)
    assert res["status"] == "success"
    data = json.loads(Path(res["output_path"]).with_suffix(".json").read_text(encoding="utf-8"))
    got = {r["id"]: r["outcome"] for r in data["rows"]}
    assert got == {"CF-1": "送到仍犯",          # written 11-02, in the run's handout
                   "CF-2": "有但没送到",        # written 11-03, not handed out
                   "CF-3": "事先没有",          # written 11-25, after the mistake (11-20)
                   "CF-4": "DDD 内容过时",      # the model says the entry is contradicted
                   "CF-6": "事先没有"}          # no entry states it
    assert "CF-5" not in got                    # not a real mistake
    assert data["month"] == "2026-11" and data["table"]["A"]["事先没有"] == 2
    assert _hashes(ws) == before                # read-only: no main file changed


def test_the_report_compares_with_the_baseline_and_counts_the_month(cdf_env):
    from core.ingestion.api import connect_rw
    from jobs.handlers.brain_mistake_audit import OUTCOMES, run_brain_mistake_audit
    connect_rw().close()
    ws = _setup(cdf_env)
    out = ws / "Knowledge/JobResults"
    out.mkdir(parents=True, exist_ok=True)
    base = {c: {o: 0 for o in OUTCOMES} for c in "ABC"}
    base["A"]["事先没有"] = 10
    (out / f"2026-10-30-brain-mistake-audit.json").write_text(json.dumps(
        {"baseline": "2026-10-30 基线", "table": base}, ensure_ascii=False), encoding="utf-8")
    res = run_brain_mistake_audit({"workspace": str(ws)}, ask=_ask, now=NOW)
    md = Path(res["output_path"]).read_text(encoding="utf-8")
    assert " (last month 10)" in md and "Compared with: 2026-10-30 基线" in md
    assert "## 2. Changes to the brain this month" in md and "## 3. Open conflicts" in md


def test_a_failed_model_call_fails_the_audit_and_keeps_the_last_report(cdf_env):
    """A model call that fails (no answer) is not "the model could not judge": an audit
    run while Midway had expired wrote "0 real mistakes, 113 unjudged" for 2026-10. The run
    fails, writes nothing, and the month's report already on disk stays as it was."""
    from core.ingestion.api import connect_rw
    from jobs.handlers.brain_mistake_audit import run_brain_mistake_audit
    connect_rw().close()
    ws = _setup(cdf_env)
    out = ws / "Knowledge/JobResults"
    out.mkdir(parents=True, exist_ok=True)
    kept = out / "2026-11-brain-mistake-audit.json"
    kept.write_text('{"month": "2026-11", "table": {}, "rows": []}', encoding="utf-8")

    def broken(prompt, schema):
        raise RuntimeError("model down")
    res = run_brain_mistake_audit({"workspace": str(ws)}, ask=broken, now=NOW)
    assert res["status"] == "failed" and res["output_path"] is None
    assert "model down" in res["error"]
    assert kept.read_text(encoding="utf-8") == '{"month": "2026-11", "table": {}, "rows": []}'
    assert not (out / "2026-11-brain-mistake-audit.md").exists()


def test_an_expired_sign_in_is_an_auth_failure_the_scheduler_retries(cdf_env):
    from core.aws_account import NeedsLogin
    from core.ingestion.api import connect_rw
    from jobs.handlers.brain_mistake_audit import run_brain_mistake_audit
    connect_rw().close()
    ws = _setup(cdf_env)

    def expired(prompt, schema):
        raise NeedsLogin("Midway has expired: run `mwinit -f`")
    res = run_brain_mistake_audit({"workspace": str(ws)}, ask=expired, now=NOW)
    assert res["status"] == "auth_failed" and res["output_path"] is None


def test_a_model_answer_without_an_item_is_still_unjudged(cdf_env):
    """The model answered but left a mistake out: that one is unjudged, the report is written."""
    from core.ingestion.api import connect_rw
    from jobs.handlers.brain_mistake_audit import run_brain_mistake_audit
    connect_rw().close()
    ws = _setup(cdf_env)

    def silent(prompt, schema):
        return {"items": []} if "items" in schema["properties"] else {"match": -1, "stale": False}
    res = run_brain_mistake_audit({"workspace": str(ws)}, ask=silent, now=NOW)
    data = json.loads(Path(res["output_path"]).with_suffix(".json").read_text(encoding="utf-8"))
    assert res["status"] == "success" and data["unjudged"] == len(FINDINGS)


def test_the_executor_passes_an_auth_failure_through(monkeypatch):
    from jobs import executor
    from jobs.handlers import brain_mistake_audit
    monkeypatch.setattr(brain_mistake_audit, "run_brain_mistake_audit",
                        lambda config=None, **_: {"status": "auth_failed", "summary": "s", "error": "e",
                                                  "output_path": None})
    assert executor._audit_status({"status": "auth_failed"}) == "auth_failed"
    assert executor._audit_status({"status": "success"}) == "success"
    assert executor._audit_status({"status": "weird"}) == "failed"


def test_the_job_is_registered_monthly():
    from jobs.system_jobs import SYSTEM_JOBS
    job = next(j for j in SYSTEM_JOBS if j.id == "brain-mistake-audit")
    assert job.type == "brain_mistake_audit" and job.schedule.split()[2:] == ["1", "*", "*"]


def test_an_entry_written_the_day_of_the_mistake_was_not_there_before():
    from jobs.handlers.brain_mistake_audit import _outcome
    assert _outcome("2026-11-20", "2026-11-20", True, False) == "事先没有"
    assert _outcome("2026-11-19", "2026-11-20", True, False) == "送到仍犯"


def test_a_month_before_the_records_is_skipped_and_reports_are_named_by_month(cdf_env):
    from core.ingestion.api import connect_rw
    from jobs.handlers.brain_mistake_audit import run_brain_mistake_audit
    connect_rw().close()
    ws = cdf_env["workspace"]
    (cdf_env["ddd"] / "TECH.md").write_text(TECH, encoding="utf-8")
    early = run_brain_mistake_audit({"workspace": str(ws)}, ask=_ask, now=NOW)
    assert early["status"] == "skipped" and early["output_path"] is None
    _setup(cdf_env)
    res = run_brain_mistake_audit({"workspace": str(ws)}, ask=_ask, now=NOW)
    assert Path(res["output_path"]).name == "2026-11-brain-mistake-audit.md"


def test_the_model_reads_each_mistake_and_entry_whole():
    """Cut at 800 / 900 characters, a mistake or an entry could read as lacking what it
    says further on, which the chain then counts as missing knowledge."""
    from jobs.handlers import brain_mistake_audit as audit
    tail = "THE-DECISIVE-TAIL"
    long_mistake = "x " * 600 + tail
    seen = []

    def ask(prompt, schema):
        seen.append(prompt)
        return {"verdicts": [], "match": -1, "stale": False, "reasoning": "r"}
    audit._classify([{"id": "m1", "mode": "chat", "project": "P", "mistake": long_mistake}], ask)
    audit._match({"knowledge": "k", "mistake": "m"}, [{"text": "y " * 600 + tail}], ask)
    assert all(tail in p for p in seen) and len(seen) == 2


def test_a_correction_is_collected_whole():
    import json as _json
    import sqlite3 as _sq
    from jobs.handlers import brain_mistake_audit as audit
    conn = _sq.connect(":memory:")
    conn.execute("CREATE TABLE ingestion_inbox (id TEXT, payload TEXT, project_snapshot TEXT, source_ref TEXT)")
    conn.execute("CREATE TABLE ingestion_units (unit_id TEXT, candidate_id TEXT, state TEXT, updated_at TEXT)")
    what = "w " * 600 + "THE-DECISIVE-TAIL"
    conn.execute("INSERT INTO ingestion_inbox VALUES ('c1', ?, '{}', 's')",
                 (_json.dumps({"type": "correction", "title": "t", "what": what}),))
    conn.execute("INSERT INTO ingestion_units VALUES ('u1', 'c1', 'written', '2026-10-15T00:00:00')")
    (item,) = audit._corrections(conn, "2026-10-01", "2026-11-01")
    assert item["mistake"].endswith("THE-DECISIVE-TAIL")


def test_open_conflicts_are_only_those_whose_two_entries_are_both_in_force(cdf_env):
    """The retired verify judge wrote a unit beside an entry it said it contradicts; 56
    such pairs were listed as open in 2026-10 though most had been settled since. A pair
    is open only while both entries are still in their files."""
    from core.ingestion.api import connect_rw
    from jobs.handlers.brain_mistake_audit import run_brain_mistake_audit
    ws = _setup(cdf_env)
    tech = cdf_env["ddd"] / "TECH.md"
    tech.write_text(TECH + "\n- [decision] **Open pair new side** Still here. <!-- cand:cand-open.unit -->\n"
                    "- [decision] **Old side of the open pair** Still here. <!-- cand:cand-old.unit -->\n"
                    "- [decision] **Settled pair new side** Here. <!-- cand:cand-settled.unit -->\n",
                    encoding="utf-8")
    old = "Projects/P/2-understanding/TECH.md#Runtime Traps#cand:cand-old.unit"
    gone = "Projects/P/2-understanding/TECH.md#Runtime Traps#cand:cand-gone.unit"
    conn = connect_rw()
    for uid, title, target in (("cand-open", "Open pair new side", old),
                               ("cand-settled", "Settled pair new side", gone)):
        decision = {"card3": {"hits": [{"anchor": target}]},
                    "version_vector": {"verify": {"hits": [{"anchor": "h1", "relation": "contradicts"}]}}}
        conn.execute("INSERT INTO ingestion_inbox(id,idem_key,source,source_ref,lane,payload,payload_schema,"
                     "content_hash,envelope,project_snapshot,created_at) VALUES (?,?,'S7','s','session',?,'v','h',"
                     "'{}','{}','2026-11-10T00:00:00+00:00')", (uid, uid, json.dumps({"title": title})))
        conn.execute("INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,state,action,reason,decision,"
                     "updated_at) VALUES (?,?,'{}','written','add','contradicts',?,'2026-11-10T00:00:00+00:00')",
                     (uid + ".unit", uid, json.dumps(decision)))
        conn.execute("INSERT INTO ingestion_writes(op_id,op_kind,unit_id,target_path,step,created_at,updated_at) "
                     "VALUES (?,'ingestion_unit',?,'Projects/P/2-understanding/TECH.md','done',"
                     "'2026-11-10T00:00:00+00:00','2026-11-10T00:00:00+00:00')", ("w-" + uid, uid + ".unit"))
    conn.commit()
    conn.close()
    res = run_brain_mistake_audit({"workspace": str(ws)}, ask=_ask, now=NOW)
    md = Path(res["output_path"]).read_text(encoding="utf-8")
    section = md.split("## 3. Open conflicts", 1)[1]
    assert "Open pair new side" in section and "Settled pair new side" not in section
