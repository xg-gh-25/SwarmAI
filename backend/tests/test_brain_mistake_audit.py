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


def _setup(cdf_env, *, knowledge_ids=()):
    ws = cdf_env["workspace"]
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
    assert "（上月 10）" in md and "对比：2026-10-30 基线" in md
    assert "## 2. 本月 Brain 的变化" in md and "## 3. 未决冲突" in md


def test_a_failed_model_call_is_unjudged_not_a_guess(cdf_env):
    from core.ingestion.api import connect_rw
    from jobs.handlers.brain_mistake_audit import run_brain_mistake_audit
    connect_rw().close()
    ws = _setup(cdf_env)

    def broken(prompt, schema):
        raise RuntimeError("model down")
    res = run_brain_mistake_audit({"workspace": str(ws)}, ask=broken, now=NOW)
    data = json.loads(Path(res["output_path"]).with_suffix(".json").read_text(encoding="utf-8"))
    assert data["rows"] == [] and data["unjudged"] == len(FINDINGS)


def test_the_job_is_registered_monthly():
    from jobs.system_jobs import SYSTEM_JOBS
    job = next(j for j in SYSTEM_JOBS if j.id == "brain-mistake-audit")
    assert job.type == "brain_mistake_audit" and job.schedule.split()[2:] == ["1", "*", "*"]


def test_an_entry_written_the_day_of_the_mistake_was_not_there_before():
    from jobs.handlers.brain_mistake_audit import _outcome
    assert _outcome("2026-11-20", "2026-11-20", True, False) == "事先没有"
    assert _outcome("2026-11-19", "2026-11-20", True, False) == "送到仍犯"
