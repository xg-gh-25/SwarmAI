"""REPORT knowledge line and REFLECT candidates from knowledge.md (design
docs/design/2026-10-26-run-knowledge.md §3.5).

What is tested:
- A candidate from ``candidates_for`` can be annotated: it names the entry by its text
  without the bullet (red before: the candidate kept "- " and annotate_entry, which
  strips it, never found the entry -- run_bb7b1850 lost two refines that way).
- The candidates of a lesson include the DDD entries recalled into knowledge.md at
  create (from the contract, which the agent cannot edit), merged with the keyword
  recall and de-duplicated.
- REPORT carries one line ``Knowledge: <purpose> <status> · N ... · cited M`` and no recalled
  text; M counts the distinct ids cited by REQUIREMENTS / DESIGN_SPEC knowledge_refs and
  by findings; a run created before step 3 has no line.
"""
from core.run_protocol import audit, reflect_job, run_knowledge as rk

ENTRY = "- **Quokka rule**: never feed quokkas after midnight."


def _ws(tmp_path):
    doc = tmp_path / "Projects" / "P" / "2-understanding" / "TECH.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(f"# TECH\n\n## Traps\n\n{ENTRY}\n", encoding="utf-8")
    return doc


def test_a_recalled_candidate_can_be_annotated(tmp_path, monkeypatch):
    doc = _ws(tmp_path)
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    cands = reflect_job.candidates_for([{"id": "L1", "statement": "never feed quokkas after midnight"}], "P")
    assert cands["L1"], cands
    assert reflect_job.annotate_entry(tmp_path, "P", cands["L1"][0], "reinforced by run_x L1") == "applied"
    assert "  - reinforced by run_x L1" in doc.read_text(encoding="utf-8")


def test_knowledge_entries_join_the_candidates(tmp_path, monkeypatch):
    _ws(tmp_path)
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    known = [{"id": rk.item_id(ENTRY), "source": "TECH.md#Traps", "head": "**Quokka rule**: never feed quokkas after midnight."}]
    cands = reflect_job.candidates_for([{"id": "L1", "statement": "unrelated words entirely"}], "P",
                                       knowledge=known)
    assert cands["L1"] == ["TECH.md#Traps: **Quokka rule**: never feed quokkas after midnight."]
    both = reflect_job.candidates_for([{"id": "L1", "statement": "never feed quokkas after midnight"}], "P",
                                      knowledge=known)
    assert len(both["L1"]) == 1, both            # the same entry from both sources, once


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


def test_end_to_end_cited_entry_reaches_report_and_reflect(tmp_path, monkeypatch):
    """A full run: create recalls the DDD entry, REQUIREMENTS cites it, REFLECT
    reinforces it through the knowledge candidate, the run lands; REPORT counts the
    citation and the DDD entry got its note."""
    import json
    from tests.pipeline_v1_rig import PROJECT, Rig, SWARM, documents
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
    r.drive(until="deliver")
    lesson = {"id": "L1", "type": "pitfall", "scope": f"domain:{PROJECT}",
              # no words shared with the entry: only knowledge.md can offer it as a candidate
              "statement": "xylophone tuning stays out of scope", "tell": "seen in this run",
              "evidence": [r.events()[0]["event_id"]],
              "relation": {"kind": "reinforces", "entry": f"{cited['source']}: {cited['head']}"}}
    design = [e for e in r.events() if e["kind"] == "knowledge_recalled"][0]["payload"]
    tech_entry = next(row for row in design["ddd"] if row["source"].startswith("TECH.md#"))
    lesson2 = {**lesson, "id": "L2", "statement": "marimba tuning stays out of scope",
               # only knowledge_design.md (the event, not the contract) offers this one
               "relation": {"kind": "reinforces",
                            "entry": f"{tech_entry['source']}: {tech_entry['head']}"}}
    prep = r.v1("reflect-prepare", "--artifact",
                r.artifact("reflect", {"lessons": [lesson, lesson2]}))
    assert prep.get("ok") is True, prep
    r.v1("land", "--message", "fix: swap the operands" + SWARM)
    state = r.state()
    assert state["status"] == "completed" and state["landing"] == "landed", state
    report = (r.repo / ".aidlc" / "deliverables" / r.run_id / "REPORT.md").read_text(encoding="utf-8")
    assert (f"Knowledge: requirement recalled · {len(record['items'])} · "
            f"design recalled · {len(design['items'])} · cited 1") in report, report
    assert "feeder only skips midnight" not in report         # no recalled text in REPORT
    assert cited["source"].startswith("PROJECT.md#")
    doc = (r.ws / "Projects" / PROJECT / "2-understanding" / "PROJECT.md").read_text(encoding="utf-8")
    assert f"  - reinforced by {r.run_id} L1" in doc, doc
    tech = (r.ws / "Projects" / PROJECT / "2-understanding" / "TECH.md").read_text(encoding="utf-8")
    assert f"  - reinforced by {r.run_id} L2" in tech, tech
