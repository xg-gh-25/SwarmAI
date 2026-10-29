"""REPORT knowledge line (design docs/design/2026-10-26-run-knowledge.md §3.5).

What is tested:
- REPORT carries one line ``Knowledge: <purpose> <status> · N ... · cited M`` and no recalled
  text; M counts the distinct ids cited by REQUIREMENTS / DESIGN_SPEC knowledge_refs and
  by findings; a run created before step 3 has no line.
- A whole run: create recalls the DDD entry, REQUIREMENTS cites it, the run lands, and
  REPORT counts the citation.

(REFLECT's knowledge candidates and their annotation of DDD entries were removed by
CDF stage B: REFLECT now only enqueues its lessons, tested in test_cdf_v2_e2e.)
"""
import pytest

from core.run_protocol import audit, run_knowledge as rk


def test_head_is_the_first_line_without_the_bullet():
    assert rk.entry_head("- [pitfall] **A** long rule.\n  - refines: more\n<!-- meta -->") \
        == "[pitfall] **A** long rule."


def _build(**knowledge):
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    events = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}},
              {"kind": "land", "at": "2026-10-22T00:00:30+00:00", "payload": {}}]
    return audit.build(run_id="run_x", projection=projection, events=events, findings=[],
                       contract=contract, open_checks={}, audit_through="COMPLETED",
                       commits={"repo": "a" * 40}, knowledge=knowledge or None)


def test_report_has_one_knowledge_line_and_no_recalled_text():
    a = _build(status="recalled", entries=7, cited=["0a1b2c3d", "1b2c3d4e"])
    text = audit.render(a, fmt="report")
    assert "Knowledge: requirement recalled · 7 · cited 2" in text
    b = _build(status="recalled", entries=9, cited=[],
               parts=[{"purpose": "requirement", "status": "recalled", "entries": 4},
                      {"purpose": "design", "status": "none", "entries": 0}])
    assert "Knowledge: requirement recalled · 4 · design none · 0 · cited 0" in \
        audit.render(b, fmt="report")
    assert a["details"]["knowledge"]["cited"] == ["0a1b2c3d", "1b2c3d4e"]


def test_report_counts_units_found_on_demand_and_searches():
    a = _build(status="recalled", entries=3, cited=["0a1b2c3d"], cited_on_demand=["2c3d4e5f", "3d4e5f6a"],
               searches=4)
    text = audit.render(a, fmt="report")
    assert "· cited 1 (+2 found on demand) · searched the DDD 4×" in text
    assert a["details"]["knowledge"]["cited_on_demand"] == ["2c3d4e5f", "3d4e5f6a"]
    plain = audit.render(_build(status="recalled", entries=3, cited=[]), fmt="report")
    assert "found on demand" not in plain and "searched the DDD" not in plain


def test_no_knowledge_line_for_an_older_run():
    assert "Knowledge:" not in audit.render(_build(), fmt="report")


def test_end_to_end_a_cited_entry_reaches_report(tmp_path, monkeypatch):
    """A full run: create recalls the DDD entry, REQUIREMENTS cites it, the run lands;
    REPORT counts the citation and carries no recalled text."""
    from tests.pipeline_v1_rig import PROJECT, Rig, documents
    from tests.test_pipeline_v1_run_knowledge import _ddd
    r = Rig(tmp_path, monkeypatch, profile="full")
    _ddd(r)
    out = r.v1("create", "--project", PROJECT, "--profile", "full",
               "--requirement", "make the quokka feeder skip midnight", "--repo", f"repo={r.repo}")
    assert out.get("ok") is True and out["knowledge"]["entries"] >= 1, out
    from core.run_protocol.contract import read_contract
    record = read_contract(r.data, r.run_id)["knowledge"]
    cited = record["ddd"][0]
    req = {**documents("full")["evaluate"], "knowledge_refs": [cited["id"]]}
    assert r.v1("stage-complete", "--artifact", r.artifact("requirements", req)).get("ok") is True
    r.drive()
    state = r.state()
    assert state["status"] == "completed" and state["landing"] == "landed", state
    design = [e for e in r.events() if e["kind"] == "knowledge_recalled"][0]["payload"]
    report = (r.repo / ".aidlc" / "deliverables" / r.run_id / "REPORT.md").read_text(encoding="utf-8")
    assert (f"Knowledge: requirement recalled · {len(record['items'])} · "
            f"design recalled · {len(design['items'])} · cited 1") in report, report
    assert "feeder only skips midnight" not in report         # no recalled text in REPORT
    assert cited["source"].startswith("PROJECT.md#")
    r.close()
