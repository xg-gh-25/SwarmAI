"""Knowledge a run finds on demand is delivered, citable and counted like recalled
knowledge (knowledge addressing §2.3, §2.5, §2.7; §6.3 tests 6, 7, 8, 13).

Through the real CLI rig:
- knowledge.md carries the DDD map when the project has a DDD, none without one;
- every review handout carries a map (full for correctness, brief for the others) and
  the project name, so a finding may cite a unit the expert found itself;
- a finding citing an on-demand unit keeps the ref; a made-up id is dropped and the
  finding still counts;
- REQUIREMENTS / DESIGN_SPEC may cite a unit of the DDD (archives included) besides
  the recalled ones; a made-up id is refused; REPORT counts both kinds and the searches.
"""
import json

import pytest

from core import addressing
from core.run_protocol import run_knowledge as rk
from core.run_protocol.documents import DocumentError
from core.run_protocol.schemas import validate_design_spec
from tests.pipeline_v1_rig import PROJECT, Rig
from tests.test_pipeline_v1_project_binding import _bind

RECALLED = "- **Operand order in mod.add**: callers rely on add(a, b) returning a + b; swap tests."
OTHER = "- [pitfall] **Quokka feeding**: never feed quokkas after midnight, whatever the request says."
ARCHIVED = "- [decision] **Retired echidna rule**: echidnas were once fed at noon."


def _with_ddd(r: Rig) -> None:
    _bind(r, PROJECT, r.repo)
    und = r.ws / "Projects" / PROJECT / "2-understanding"
    (und / "TECH.md").write_text(f"# TECH\n\n## Traps\n\n{RECALLED}\n\n## Other\n\n{OTHER}\n",
                                 encoding="utf-8")
    (und / "TECH-archive.md").write_text(f"# archive\n\n## Old\n\n{ARCHIVED}\n", encoding="utf-8")
    (und / "PRODUCT.md").write_text("# PRODUCT\n\n## Vision\n\nA platypus for everyone.\n",
                                    encoding="utf-8")       # pinned: always recalled


def _handouts(r: Rig) -> list[dict]:
    return [e for e in r.events() if e["kind"] == "handout" and e.get("stage") == "review"]


def _prompt(h) -> str:
    return open(h["path"], encoding="utf-8").read()


def test_knowledge_md_carries_the_map_only_with_a_ddd(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="full")
    _with_ddd(r)
    r.create()
    text = (r.engine().run_dir / "artifacts" / "knowledge.md").read_text(encoding="utf-8")
    assert "What the project's DDD holds" in text and "TECH-archive.md" in text
    assert f"knowledge find --project {PROJECT}" in text
    plain = Rig(tmp_path / "b", monkeypatch, profile="full")
    plain.create()
    assert "What the project's DDD holds" not in (
        plain.engine().run_dir / "artifacts" / "knowledge.md").read_text(encoding="utf-8")


def test_handouts_carry_a_map_and_the_project(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="full")
    _with_ddd(r)
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    assert started["handouts"], started
    for h in started["handouts"]:
        prompt = _prompt(h)
        assert "PROJECT DDD MAP" in prompt and f"knowledge find --project {PROJECT}" in prompt
    for e in _handouts(r):
        assert e["payload"].get("knowledge_project") == PROJECT
    correctness = [e for e in _handouts(r) if e["payload"]["expert_id"] == "correctness"]
    others = [e for e in _handouts(r) if e["payload"]["expert_id"] != "correctness"]
    store = r.engine().store
    text = lambda e: store.read_blob(e["payload"]["prompt"]["blob"]).decode() \
        if isinstance(e["payload"]["prompt"], dict) else e["payload"]["prompt"]
    assert all(" ## Traps" in text(e) for e in correctness)
    assert all(" ## Traps" not in text(e) for e in others)


def test_no_ddd_run_gets_no_map_and_no_project(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="full")
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    assert all("PROJECT DDD MAP" not in _prompt(h) for h in started["handouts"])
    assert all("knowledge_project" not in e["payload"] for e in _handouts(r))


def _finding(ref, claim):
    return json.dumps({"severity": "LOW", "kind": "docs", "file": "backend/core/mod.py",
                       "line": 1, "claim": claim, "fix": "add one", "knowledge_ref": ref})


def test_a_finding_may_cite_a_unit_found_on_demand(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="full")
    _with_ddd(r)
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    other = addressing.find(PROJECT, "quokkas midnight").units[0]
    assert other.id != rk.item_id(RECALLED)
    for i, h in enumerate(started["handouts"]):
        answer = "NO FINDINGS"
        if i == 0:
            answer = ("New finding: " + _finding(other.id, "add lacks a docstring") + "\n"
                      "New finding: " + _finding("deadbeef", "add lacks a type hint"))
        r.spawn_reviewer(h["prompt"], answer, f"review-{i}")
    occ = {o["claim"]: o for o in (e["payload"] for e in r.events() if e["kind"] == "finding_occ")}
    assert occ["add lacks a docstring"]["knowledge_ref"] == other.id
    assert "knowledge_ref" not in occ["add lacks a type hint"]


def test_documents_may_cite_ddd_units_and_report_counts_both(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="full")
    _with_ddd(r)
    r.create()
    eng = r.engine()
    recalled = sorted(eng._recalled_ids())[0]          # the pinned Vision section
    other = addressing.find(PROJECT, "quokkas midnight").units[0].id
    archived = addressing.find(PROJECT, "echidna").units[0]
    assert archived.store == "archive"
    eng._check_knowledge_refs({"knowledge_refs": [recalled, other, archived.id]}, "RQ-5")
    with pytest.raises(DocumentError) as exc:
        eng._check_knowledge_refs({"knowledge_refs": [other, "deadbeef"]}, "RQ-5")
    assert "deadbeef" in str(exc.value) and "knowledge find" in str(exc.value)
    art = eng.run_dir / "artifacts"
    (art / "requirements.json").write_text(json.dumps({"knowledge_refs": [recalled]}))
    (art / "design_spec.json").write_text(json.dumps({
        "knowledge_refs": [other],
        "knowledge_checked": [{"query": "quokka feeding", "result_ids": [other]},
                              {"query": "noon rule", "result_ids": []}]}))
    summary = eng._knowledge_summary(r.events())
    assert recalled in summary["cited"] and summary["cited_on_demand"] == [other]
    assert summary["searches"] == 2


def test_a_run_without_ddd_refuses_unit_ids(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="full")
    r.create()
    with pytest.raises(DocumentError):
        r.engine()._check_knowledge_refs({"knowledge_refs": ["0a1b2c3d"]}, "RQ-5")


@pytest.mark.parametrize("variant", ["full", "lite"])
def test_design_spec_knowledge_checked_shape(variant):
    def issues(value):
        doc = {"knowledge_checked": value}
        return [i for i in validate_design_spec(doc, variant=variant, requirement_ids=set())
                if "knowledge_checked" in i.path]
    assert issues([]) == [] and issues([{"query": "x", "result_ids": ["0a1b2c3d"]}]) == []
    assert issues("x") and issues([{"query": ""}]) and issues([{"query": "x", "result_ids": ["nope"]}])
    assert issues([{"query": "x", "extra": 1}])


def test_stage_complete_accepts_a_unit_found_on_demand(tmp_path, monkeypatch):
    """Through the real EVALUATE completion: a REQUIREMENTS citing a unit found on demand
    is accepted, a made-up id refused (the stage write path, not a direct call)."""
    from tests.pipeline_v1_rig import documents
    r = Rig(tmp_path, monkeypatch, profile="full")
    _with_ddd(r)
    r.create()
    other = addressing.find(PROJECT, "quokkas midnight").units[0]
    req = {**documents("full")["evaluate"], "knowledge_refs": [other.id]}
    assert r.v1("stage-complete", "--artifact", r.artifact("requirements", req)).get("ok") is True
    bad = {**documents("full")["evaluate"], "knowledge_refs": ["deadbeef"]}
    r2 = Rig(tmp_path / "b", monkeypatch, profile="full")
    _with_ddd(r2)
    r2.create()
    assert r2.v1("stage-complete", "--artifact", r2.artifact("requirements", bad)).get("ok") is False


def test_report_counts_a_cited_block_entry_as_handed_out_not_on_demand(tmp_path, monkeypatch):
    """An entry handed to the correctness expert in its block (handout knowledge_items)
    and cited by a finding is "cited", even when no knowledge file lists it; a ref that
    passed the hook's check but names no handed-out entry is "found on demand", without
    re-checking today's DDD (an entry rewritten since has a new id)."""
    r = Rig(tmp_path, monkeypatch, profile="full")
    _with_ddd(r)
    r.create()
    eng = r.engine()
    assert "b10cb10c" not in eng._recalled_ids()
    rows = r.events() + [
        {"kind": "handout", "payload": {"expert_id": "correctness", "knowledge_items": ["b10cb10c"]}},
        {"kind": "finding_occ", "payload": {"knowledge_ref": "b10cb10c"}},
        {"kind": "finding_occ", "payload": {"knowledge_ref": "0dd0dd00"}}]
    summary = eng._knowledge_summary(rows)
    assert "b10cb10c" in summary["cited"] and summary["cited_on_demand"] == ["0dd0dd00"]


def test_an_unreadable_ddd_is_named_in_the_refusal(tmp_path, monkeypatch):
    """A DDD that could not be read is not reported as "not units of the DDD": the agent
    is told to retry, and the next check reads again (not cached)."""
    r = Rig(tmp_path, monkeypatch, profile="full")
    _with_ddd(r)
    r.create()
    eng = r.engine()
    other = addressing.find(PROJECT, "quokkas midnight").units[0].id

    real, broken = addressing.unit_ids, [True]

    def flaky(project):
        if broken[0]:
            raise OSError("disk gone")
        return real(project)
    monkeypatch.setattr(addressing, "unit_ids", flaky)
    with pytest.raises(DocumentError) as exc:
        eng._check_knowledge_refs({"knowledge_refs": [other]}, "RQ-5")
    assert "could not be read" in str(exc.value)
    broken[0] = False
    eng._check_knowledge_refs({"knowledge_refs": [other]}, "RQ-5")
