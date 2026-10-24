"""Conformance K4: documents and the audit are generated from the signed events only.

Methodology: the real v1 rig (CLI-driven runs on temporary repos) and unit calls on the
review orchestrator / audit builder. Each test failed on the pre-K4 code.

- F21: FIXING_SPEC reads the fixer's hunk and cause from ``address`` events; no
  ``review-handoff-*.json`` side file is written or read.
- F20: REVIEW.md carries prompt sha, R2 status, reclassification and its confirmation,
  each finding's evidence event and the decision rules.
- F22: ``run_audit.json`` details carry the critical path, expert rows, waits and the
  R→T→X→result trace matrix.
- F23: a Markdown render failure records ``render_failed`` and the stage still passes.
- L5: the final records ``generated_at``. L6: REPORT's title is the run's title.
- §5: ``manual_rescues`` is null (not measured) and never decides Autonomy.
"""
from __future__ import annotations

import json

import pytest

from core.run_protocol import audit as audit_protocol
from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    rigs: list[Rig] = []

    def make(profile: str = "direct", **kwargs) -> Rig:
        r = Rig(tmp_path / f"case{len(rigs)}", monkeypatch, profile=profile, **kwargs)
        rigs.append(r)
        return r
    yield make
    for r in rigs:
        if r.run_id:
            assert r.v1("status").get("ok") is True, "the event log no longer folds"
        r.close()


def _contract(**extra):
    return {"run_id": "run_k4", "expected_repos": ["repo"], "mode": "shared", **extra}


def _audit(events, **kw):
    return audit_protocol.build(
        run_id="run_k4", projection={"run_id": "run_k4", "status": "active"}, events=events,
        final=kw.pop("final", {"repos": {}}), findings=[], contract=_contract(),
        push={"push_ready": False}, audit_through="DELIVER", **kw)


def test_f22_details_carry_critical_path_experts_waits_and_trace_matrix():
    events = [
        {"kind": "run_create", "event_id": "e0", "at": "2026-10-23T00:00:00+00:00"},
        {"kind": "stage_attempt", "event_id": "e1", "stage": "build", "at": "2026-10-23T00:01:00+00:00"},
        {"kind": "stage_complete", "event_id": "e3", "stage": "build", "at": "2026-10-23T00:03:00+00:00"},
        {"kind": "handout", "event_id": "h1", "stage": "review", "round": 1,
         "at": "2026-10-23T00:04:00+00:00", "payload": {"expert_id": "correctness"}},
        {"kind": "answer", "event_id": "a1", "stage": "review", "round": 1,
         "at": "2026-10-23T00:06:00+00:00",
         "payload": {"handout_event_id": "h1", "state": "parsed"}},
        {"kind": "decision", "event_id": "d1", "at": "2026-10-23T00:07:00+00:00",
         "payload": {"request_id": "q1"}},
        {"kind": "human_answer", "event_id": "d2", "at": "2026-10-23T00:09:00+00:00",
         "payload": {"request_id": "q1"}},
        {"kind": "stage_complete", "event_id": "e4", "stage": "review", "at": "2026-10-23T00:10:00+00:00"},
    ]
    final = {"repos": {"repo": {"trace": [
        {"exit": "X1", "kind": "test", "covers": ["R1"], "result": "pass", "seq": 1}]}}}
    design = {"tasks": [{"id": "T1", "requirements": ["R1"]}],
              "exit_criteria": [{"id": "X1", "kind": "test", "ref": "tests/test_a.py::test_a",
                                 "covers": ["R1"]}],
              "properties": [{"id": "P1", "validates": ["R1"], "test": "tests/test_a.py::test_a"}]}
    details = _audit(events, final=final, design=design)["details"]
    build, review = details["critical_path"]
    assert (build["stage"], build["seconds"], build["attempts"]) == ("build", 180.0, 1)
    # REVIEW: 7 min wall, of which 2 min waited on a person; the expert took 2 min
    assert (review["stage"], review["seconds"], review["active_seconds"],
            review["dispatch_seconds"], review["dispatches"]) == ("review", 420.0, 300.0, 120.0, 1)
    assert details["experts"] == [{"expert": "correctness", "stage": "review", "round": 1,
                                   "handout_event_id": "h1", "answer_event_id": "a1",
                                   "state": "parsed", "seconds": 120.0}]
    assert details["waits"][0]["seconds"] == 120.0 and details["waits"][0]["kind"] == "decision"
    r1 = details["trace_matrix"]["rows"]["R1"]
    assert r1["tasks"] == ["T1"]
    assert r1["exits"] == [{"exit": "X1", "kind": "test", "ref": "tests/test_a.py::test_a",
                            "results": {"repo": "pass"}}]
    assert details["trace_matrix"]["rows"]["P1"]["validates"] == ["R1"]
    assert _audit(events)["details"]["trace_matrix"] == {
        "rows": {}, "unavailable": "no DESIGN_SPEC for this run"}


def test_k4r1_two_parallel_experts_count_once_in_dispatch_time():
    def h(i, at):
        return {"kind": "handout", "event_id": f"h{i}", "stage": "review",
                "at": f"2026-10-23T00:0{at}:00+00:00", "payload": {"expert_id": f"x{i}"}}

    def a(i, at):
        return {"kind": "answer", "event_id": f"a{i}", "stage": "review",
                "at": f"2026-10-23T00:0{at}:00+00:00", "payload": {"handout_event_id": f"h{i}"}}
    events = [{"kind": "run_create", "event_id": "e0", "at": "2026-10-23T00:00:00+00:00"},
              h(1, 1), h(2, 1), a(1, 3), a(2, 4),
              {"kind": "stage_complete", "event_id": "e9", "stage": "review",
               "at": "2026-10-23T00:05:00+00:00"}]
    (review,) = _audit(events)["details"]["critical_path"]
    assert review["dispatch_seconds"] == 180.0       # 00:01-00:04, not 120 + 180


def test_k4r1_untrusted_words_cannot_inject_markdown_or_html_into_report():
    value = _audit([], title="Safe <img src=x onerror=alert(1)> | [link](http://x) *b*")
    head = audit_protocol.render(value, fmt="report").splitlines()[0]
    assert "<img" not in head and "&lt;img" in head
    assert "\\[link\\]" in head and "\\|" in head and "\\*b\\*" in head


def test_s5_manual_rescues_are_not_measured_and_never_decide_autonomy():
    events = [{"kind": "integrity", "event_id": "i", "at": "2026-10-23T00:00:00+00:00",
               "payload": {"conflict_type": "manual_rescue"}}]
    autonomy = _audit(events)["summary"]["autonomy"]
    assert autonomy["manual_rescues"] is None and autonomy["verdict"] == "PASS"


def test_l6_report_title_is_the_run_title():
    value = _audit([], title="Add the shared lock helper")
    text = audit_protocol.render(value, fmt="report")
    assert text.splitlines()[0] == "# Add the shared lock helper　　Audit through DELIVER"
    assert "Pipeline Delivery" not in text


def test_f21_fixing_spec_reads_the_pass_from_address_events_only(rig):
    from tests.test_pipeline_v1_conformance_k1 import _at_review_with_finding
    r = rig()
    finding = _at_review_with_finding(r)
    out = r.v1("review-address", "--artifact", r.artifact("address", {
        "pass": 1, "items": [{"finding_id": finding, "hunk": "@@ -1 +1 @@\n-x\n+y\n",
                               "cause": "the guard was missing"}]}))
    assert out.get("ok") is True, out
    assert not list((r.engine().run_dir / "artifacts").glob("review-handoff-*.json"))
    address = [e for e in r.events() if e["kind"] == "address"][-1]
    assert address["payload"]["cause"] == "the guard was missing"
    assert set(address["payload"]["hunk"]) == {"blob"}        # the hunk is an audit blob
    text = (r.engine().run_dir / "FIXING_SPEC.md").read_text()
    assert "the guard was missing" in text and "+y" in text


def test_f20_review_md_carries_prompt_sha_r2_evidence_and_decision_rules(rig):
    from tests.test_pipeline_v1_conformance_k1 import _at_review_with_finding
    r = rig()
    finding = _at_review_with_finding(r)
    assert r.v1("review-address", "--artifact", r.artifact("address", {
        "pass": 1, "items": [{"finding_id": finding, "hunk": "@@ -1 +1 @@\n-x\n+y\n"}]})
    ).get("ok") is True
    text = (r.engine().run_dir / "REVIEW.md").read_text()
    handout = [e for e in r.events() if e["kind"] == "handout"][-1]
    assert handout["payload"]["prompt_sha"][:12] in text
    for column in ("R2", "Reclassified", "Evidence"):
        assert f"| {column} |" in text
    assert "## Decision rules" in text and "RV-1a" in text and "blocks push" in text


def test_f23_a_failed_markdown_render_is_recorded_and_the_write_still_succeeds(rig, monkeypatch):
    from core.run_protocol import documents
    from tests.pipeline_v1_rig import documents as rig_documents
    r = rig("full")
    r.create()
    engine = r.engine()
    monkeypatch.setattr(documents, "render_requirements",
                        lambda value: (_ for _ in ()).throw(RuntimeError("template broke")))
    store = documents.DocumentStore(engine.run_dir, on_render_failed=engine._render_failed)
    written = store.write_requirements(rig_documents("full")["evaluate"], variant="full",
                                       profile="full")
    assert json.loads((engine.run_dir / "artifacts" / "requirements.json").read_text())
    assert written                                    # the source write succeeded
    failed = [e for e in r.events() if e["kind"] == "render_failed"]
    assert failed and failed[0]["payload"]["document"] == "REQUIREMENTS"
    assert "template broke" in failed[0]["payload"]["error"]
    with pytest.raises(RuntimeError):                 # no recorder: the failure surfaces
        documents.DocumentStore(engine.run_dir).write_requirements(
            rig_documents("full")["evaluate"], variant="full", profile="full")


def test_k4r1_a_lost_hunk_blob_is_recorded_once_and_marked_in_the_spec(rig):
    from tests.test_pipeline_v1_conformance_k1 import _at_review_with_finding
    r = rig()
    finding = _at_review_with_finding(r)
    assert r.v1("review-address", "--artifact", r.artifact("address", {
        "pass": 1, "items": [{"finding_id": finding, "hunk": "@@ -1 +1 @@\n-x\n+y\n"}]})
    ).get("ok") is True
    engine = r.engine()
    address = [e for e in r.events() if e["kind"] == "address"][-1]
    (engine.store.paths.blobs / address["payload"]["hunk"]["blob"]).unlink()
    engine._write_review_documents()
    engine._write_review_documents()                 # same snapshot: still one record
    failed = [e for e in r.events() if e["kind"] == "render_failed"]
    assert len(failed) == 1 and failed[0]["payload"]["document"] == "FIXING_SPEC"
    assert "missing from the audit area" in (engine.run_dir / "FIXING_SPEC.md").read_text()


def test_k4r2_review_shows_verdicts_and_fixing_spec_carries_solution_and_plan(rig):
    from tests.test_pipeline_v1_conformance_k1 import _at_review_with_finding
    r = rig()
    finding = _at_review_with_finding(r)
    out = r.v1("review-address", "--artifact", r.artifact("address", {
        "pass": 1, "items": [{"finding_id": finding, "hunk": "@@ -1 +1 @@\n-x\n+y\n",
                               "cause": "operands swapped", "solution": "restore a + b",
                               "evidence_plan": {"type": "test", "ref": "tests/test_x.py::t"}}]}))
    assert out.get("ok") is True, out
    engine = r.engine()
    text = (engine.run_dir / "FIXING_SPEC.md").read_text()
    assert "Solution" in text and "restore a + b" in text
    assert "Evidence plan" in text and "test" in text
    assert "| Verdict |" in (engine.run_dir / "REVIEW.md").read_text()
    bad = r.v1("review-address", "--artifact", r.artifact("bad", {
        "pass": 2, "items": [{"finding_id": finding, "hunk": "h",
                               "evidence_plan": {"type": "vibes"}}]}))
    assert bad.get("ok") is False


def test_k4r2_an_altered_blob_is_never_rendered(rig):
    from tests.test_pipeline_v1_conformance_k1 import _at_review_with_finding
    r = rig()
    finding = _at_review_with_finding(r)
    assert r.v1("review-address", "--artifact", r.artifact("address", {
        "pass": 1, "items": [{"finding_id": finding, "hunk": "@@ -1 +1 @@\n-x\n+GOOD\n"}]})
    ).get("ok") is True
    engine = r.engine()
    address = [e for e in r.events() if e["kind"] == "address"][-1]
    (engine.store.paths.blobs / address["payload"]["hunk"]["blob"]).write_text("+EVIL\n")
    engine._write_review_documents()
    text = (engine.run_dir / "FIXING_SPEC.md").read_text()
    assert "EVIL" not in text and "missing" in text
    assert any(e["kind"] == "render_failed" for e in r.events())


def test_k4r2_a_fixing_spec_render_failure_is_recorded_as_fixing_spec(rig, monkeypatch):
    from tests.test_pipeline_v1_conformance_k1 import _at_review_with_finding
    from core.run_protocol import review
    r = rig()
    _at_review_with_finding(r)
    engine = r.engine()
    monkeypatch.setattr(review.ReviewOrchestrator, "render_fixing_passes",
                        lambda self: (_ for _ in ()).throw(RuntimeError("fixing renderer broke")))
    engine._write_review_documents()
    docs = [e["payload"]["document"] for e in r.events() if e["kind"] == "render_failed"]
    assert docs == ["FIXING_SPEC"]
