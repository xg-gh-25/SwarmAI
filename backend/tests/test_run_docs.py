"""Tests for the pipeline run documents (``core/run_docs.py``) and where they surface.

- Renderers: REQUIREMENTS / DESIGN_SPEC / REVIEW from realistic artifacts (requirements,
  root cause, solution class, mermaid design, gates, experts, fix-by-class); a mermaid
  body cannot close its fence; artifacts of the wrong shape never raise.
- ``doc_status`` / ``required_docs``: per profile (research has no design spec), a
  skipped stage is not required.
- Writers: ``write_stage_doc`` (by stage), ``completion_check`` (an error only when the
  artifact loads but the write fails; an unreadable artifact only warns).
- Wiring: ``artifact_cli`` stage-complete writes the stage's document and reports it;
  deliverable B carries the documents; the chat summary lists them;
  ``pipeline_stage_events`` attaches the doc to its row and builds the summary block;
  the orchestrator emits a knowledge ``file_changed`` per document and the summary when
  run.json turns completed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core import pipeline_stage_events as pse
from core import run_docs as rd

EVAL = {
    "recommendation": "GO", "scope": "standard", "scores": {"strategic": 8},
    "acceptance_criteria": ["AC1: the CLI keeps the flag on retry"],
    "requirements": [{"id": "FR-1", "statement": "keep the flag on retry", "source": {"kind": "user-stated"},
                      "status": "confirmed", "acceptance": [{"given": "a retry", "when": "it runs", "then": "flag kept"}]}],
    "clarifications": [{"qid": "Q1", "question": "which CLI?", "answer": "artifact_cli", "answered_by": "user"}],
    "understanding": {"claim": "the retry path drops the flag", "evidence": "cli.py:10",
                      "skeptic_verdict": "SUPPORTED",
                      "root_cause": {"why_chain": [{"id": "W1", "because": "retry rebuilds args", "evidence": "cli.py:10"},
                                                   {"id": "W2", "because": "args are rebuilt per caller", "evidence": "grep"},
                                                   {"id": "W3", "because": "no single owner of args", "evidence": "git log"}],
                                     "root_layer": "module-design", "missed_invariant": "one owner builds args",
                                     "recurrence": {"searched": ["git log -S", "grep"], "prior_instances": ["abc: same"],
                                                    "siblings": [{"id": "C1", "where": "x.py:4", "shape": "same"}]}}},
}
THINK = {"alternatives": [{"constraint": "SPEED", "what": "patch the line", "effort": "S"},
                          {"constraint": "QUALITY", "what": "one owner", "effort": "M", "targets_root": True}],
         "recommendation": "QUALITY",
         "solution_class": {"verdict": "systemic", "addresses": "W3", "why_root": "one owner for every caller",
                            "covers": [{"id": "C1", "how": "x.py uses the owner"}], "structural_change": "refactor"}}
PLAN = {"approach": "move arg building into one function",
        "design": {"root_invariant": "I1", "user_workflow": ["1. before: flag lost", "2. after: kept"],
                   "architecture": "flowchart LR\n  A --> B\n  B --> C```\nINJECT",
                   "flow": "flowchart TD\n  S --> C{ok}\n  C -->|fail| E[refuse]",
                   "states": [{"id": "S1", "name": "args", "created_by": "cli.py::build", "removed_by": "never",
                               "crosses": [{"with": "retry", "outcome": "rebuilt the same"}]}],
                   "invariants": [{"id": "I1", "statement": "one owner builds args", "enforced_at": "cli.py::build"}],
                   "failure_modes": [{"id": "F1", "scenario": "killed mid-retry", "expected": "next call rebuilds"}]},
        "design_doc": {"change_spec": [{"file": "cli.py", "target": "build() owns args"}]},
        "exit_criteria": [{"id": "X1", "kind": "test", "ref": "t.py::test_a", "expect": "pass"}]}
DELIVER = {"adversarial_review": {
    "profile_tier": "full", "red_team_dispatched": True, "evidence": "5 experts in one batch",
    "findings": [{"specialist": "correctness", "severity": "HIGH", "confidence": 9, "resolved": True,
                  "finding": "retry drops flag", "invariant": "I1", "root_cause": "two builders",
                  "class_fix": "one builder for all callers", "break_test": "t.py::test_class"},
                 {"specialist": "security", "severity": "MED", "confidence": 8, "resolved": True,
                  "finding": "Not a bug: the token is scoped"},
                 {"specialist": "security", "severity": "LOW", "confidence": 4, "resolved": False,
                  "finding": "nit"}]},
    "meta_review": {"verdict": "PASS", "risks": [{"severity": "MED", "description": "dormant path", "resolved": True}]}}


def _run(tmp_path: Path, stages: dict, profile: str = "full") -> tuple[Path, dict]:
    run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_d1"
    run_dir.mkdir(parents=True)
    recs = []
    for i, (stage, art) in enumerate(stages.items()):
        rec = {"stage": stage, "status": "completed"}
        if art is not None:
            aid = f"art_{i:04d}"
            (run_dir / f"x-20261019-{aid}.json").write_text(json.dumps({"data": art}))
            rec["artifact_id"] = aid
        recs.append(rec)
    state = {"id": "run_d1", "profile": profile, "project": "P", "requirement": "keep the flag",
             "stages": recs, "required_experts": ["concurrency"]}
    (run_dir / "run.json").write_text(json.dumps(state))
    return run_dir, state


# ── renderers ────────────────────────────────────────────────────────────────

def test_requirements_doc_carries_requirements_clarifications_and_root_cause():
    md = rd.render_requirements({"id": "run_d1", "profile": "full"}, EVAL)
    for needle in ("# Requirements", "FR-1", "keep the flag on retry", "Given a retry", "Q1",
                   "artifact_cli", "Gate-0 skeptic:** SUPPORTED", "## Root cause", "W3",
                   "module-design", "one owner builds args", "Prior instances:** 1", "C1 x.py:4"):
        assert needle in md, needle


def test_design_spec_renders_solution_class_diagrams_and_invariants():
    md = rd.render_design_spec({"id": "run_d1"}, THINK, PLAN)
    assert "Solution class: SYSTEMIC" in md and "addresses W3" in md and "🎯 QUALITY" in md
    assert md.count("```mermaid") == 2
    assert "I1 (root)" in md and "killed mid-retry" in md and "cli.py" in md and "X1" in md
    # a diagram cannot close its fence and inject markdown after it
    arch = md.split("### Architecture", 1)[1].split("### Flow", 1)[0]
    assert arch.count("```") == 2 and "'''" in arch


def test_review_doc_lists_gates_experts_and_class_fixes():
    recs = {"build": {"gate1_verdict": "PASS"}, "deliver": {"push_ready": True}}
    md = rd.render_review({"id": "run_d1", "required_experts": ["concurrency"]},
                          EVAL, recs, {"approved": True, "findings_count": 0}, DELIVER, ["red-team"])
    for needle in ("Gate 0 — understanding skeptic | SUPPORTED", "Gate 1 — plan skeptic | PASS",
                   "full · 3 finding(s), 1 open", "Red Team: yes", "Meta-review | PASS", "Push-ready | yes",
                   "| correctness | 1 | 1 | 1 | 0 | 0 |", "| security | 2 | 1 | 0 | 1 | 1 |",
                   "| concurrency | 0 |", "| red-team | 0 |", "refuted (not a bug)",
                   "### Fixed by class", "class fix: one builder for all callers", "break test: t.py::test_class"):
        assert needle in md, needle


@pytest.mark.parametrize("bad", [None, 3, "x", [], {"understanding": 5, "requirements": "no"},
                                 {"design": {"states": "x", "invariants": 7, "flow": 3}},
                                 {"adversarial_review": {"findings": [1, None, "x"]}, "meta_review": []}])
def test_renderers_never_raise_on_odd_shapes(bad):
    rd.render_requirements({"id": "r"}, bad)
    rd.render_design_spec({"id": "r"}, bad, bad)
    rd.render_review({"id": "r"}, bad, bad, bad, bad, bad)


# ── which documents a run owes ───────────────────────────────────────────────

def test_required_docs_follow_the_profile():
    assert rd.required_docs("full") == ["REQUIREMENTS.md", "DESIGN_SPEC.md", "REVIEW.md", "REPORT.md"]
    assert rd.required_docs("research") == ["REQUIREMENTS.md", "REPORT.md"]
    assert "DESIGN_SPEC.md" not in rd.required_docs("trivial")


def test_doc_status_explains_what_is_missing_or_skipped(tmp_path):
    run_dir, state = _run(tmp_path, {"evaluate": EVAL, "plan": PLAN})
    state["stages"].append({"stage": "deliver", "status": "skipped"})
    rd.write_all(run_dir, state)
    st = {d["name"]: d for d in rd.doc_status(run_dir, "full", state)}
    assert st["REQUIREMENTS.md"]["exists"] and st["DESIGN_SPEC.md"]["exists"]
    assert not st["REVIEW.md"]["required"] and "skipped" in st["REVIEW.md"]["reason"]
    assert st["REPORT.md"]["required"] and st["REPORT.md"]["reason"] == "missing"
    research = {d["name"]: d for d in rd.doc_status(run_dir, "research")}
    assert not research["DESIGN_SPEC.md"]["required"] and "no plan stage" in research["DESIGN_SPEC.md"]["reason"]


# ── writers ──────────────────────────────────────────────────────────────────

def test_write_stage_doc_by_stage(tmp_path):
    run_dir, state = _run(tmp_path, {"evaluate": EVAL, "think": THINK, "plan": PLAN, "deliver": DELIVER})
    assert rd.write_stage_doc(run_dir, state, "build") is None          # no document for build
    p = rd.write_stage_doc(run_dir, state, "plan")
    assert p and Path(p).name == "DESIGN_SPEC.md" and "one owner" in Path(p).read_text()
    assert rd.write_stage_doc(run_dir, state, "deliver", ["red-team"]).endswith("REVIEW.md")
    assert not list(run_dir.glob(".*.md.*")), "no temp file left behind"


def test_completion_check_errors_only_on_a_failed_write(tmp_path, monkeypatch):
    run_dir, state = _run(tmp_path, {"evaluate": EVAL, "think": THINK, "plan": PLAN, "deliver": DELIVER})
    errs, warns, status = rd.completion_check(run_dir, state)
    assert errs == [] and all(d["exists"] for d in status if d["name"] != "REPORT.md" and d["required"])

    def boom(path, text):
        raise OSError("disk full")
    for f in run_dir.glob("*.md"):
        f.unlink()
    monkeypatch.setattr(rd, "_atomic_write", boom)
    errs, _, _ = rd.completion_check(run_dir, state)
    assert len(errs) == 3 and all("could not be written" in e for e in errs)


def test_completion_check_warns_on_an_unreadable_artifact(tmp_path):
    run_dir, state = _run(tmp_path, {"evaluate": EVAL, "plan": None, "deliver": DELIVER})
    errs, warns, _ = rd.completion_check(run_dir, state)
    assert errs == [] and any("DESIGN_SPEC.md" in w and "could not be read" in w for w in warns)


# ── stage events: doc on the row, summary block ──────────────────────────────

def test_attach_stage_docs_and_summary_block(tmp_path, monkeypatch):
    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    run_dir, state = _run(tmp_path, {"evaluate": EVAL, "plan": PLAN, "deliver": DELIVER})
    rd.write_all(run_dir, state)
    (run_dir / "REPORT.md").write_text("# report")
    blocks = [{"type": "pipeline_stage", "stage": "evaluate", "status": "completed"},
              {"type": "pipeline_stage", "stage": "plan", "status": "in_progress"},
              {"type": "pipeline_stage", "stage": "build", "status": "completed"}]
    st = {"run_dir": str(run_dir), "profile": "full", "status": "completed",
          "stages": {r["stage"]: dict(r, push_ready=False, push_ready_reason="X1 unmet")
                     if r["stage"] == "deliver" else r for r in state["stages"]}}
    refs = pse.attach_stage_docs(blocks, st)
    assert [r["name"] for r in refs] == ["REQUIREMENTS.md"]
    assert blocks[0]["doc"]["path"] == "Projects/P/.artifacts/runs/run_d1/REQUIREMENTS.md"
    assert "doc" not in blocks[1] and "doc" not in blocks[2]
    sb = pse.summary_block("run_d1", st, "now")
    assert sb["type"] == "pipeline_summary" and sb["pushReady"] is False and sb["pushReason"] == "X1 unmet"
    docs = {d["name"]: d for d in sb["docs"]}
    assert set(docs) == {"REQUIREMENTS.md", "DESIGN_SPEC.md", "REVIEW.md", "FIXING_SPEC.md", "REPORT.md"}
    assert all(d["absolutePath"] for n, d in docs.items() if n != "FIXING_SPEC.md")
    assert docs["FIXING_SPEC.md"]["absolutePath"] is None and not docs["FIXING_SPEC.md"]["required"]
    # DELIVER fixed one HIGH straight from the review, with no fixing spec
    assert docs["FIXING_SPEC.md"]["reason"] == \
        "not written: 1 Gate-2 finding(s) were fixed without a fixing spec"
    assert docs["REVIEW.md"]["stat"] == {"experts": 2, "findings": 3, "settled": 2}
    assert pse.summary_block("run_d1", dict(st, status="running"), "now") is None


class TestOrchestratorEmitsDocs:
    def _drive(self, monkeypatch, before, after):
        from tests.test_pipeline_stage_events import CLI, _ScriptedReader, _bash_use, _drive_stream, _result
        from tests.test_tool_call_leak_guard import _make_orchestrator, _make_result_message
        from core.permission_manager import permission_manager
        permission_manager.remove_session_queue("sess-test")   # a fresh event loop per drive
        monkeypatch.setattr(pse, "read_stage_state", _ScriptedReader(before, after))
        events, raised = _drive_stream(_make_orchestrator(), [
            _bash_use("tu1", CLI.format(rid="run_d1")), _result("tu1"), _make_result_message()])
        assert raised is None
        return events

    def test_completed_stage_surfaces_its_document(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        run_dir, state = _run(tmp_path, {"evaluate": EVAL})
        rd.write_all(run_dir, state)
        before = {"profile": "full", "stages": {}, "timing": {}, "run_dir": str(run_dir)}
        after = {"profile": "full", "stages": {"evaluate": {"stage": "evaluate", "status": "completed"}},
                 "timing": {}, "run_dir": str(run_dir)}
        events = self._drive(monkeypatch, before, after)
        fc = [e for e in events if e.get("type") == "file_changed" and e.get("kind") == "knowledge"]
        assert [Path(e["absolutePath"]).name for e in fc] == ["REQUIREMENTS.md"]
        rows = [b for e in events if e.get("type") == "assistant" for b in e.get("content") or []
                if isinstance(b, dict) and b.get("type") == "pipeline_stage"]
        assert rows and rows[0]["doc"]["name"] == "REQUIREMENTS.md"

    def test_summary_block_once_when_the_run_completes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        run_dir, state = _run(tmp_path, {"evaluate": EVAL, "deliver": DELIVER})
        stages = {r["stage"]: r for r in state["stages"]}
        base = {"profile": "full", "stages": stages, "timing": {}, "run_dir": str(run_dir)}
        events = self._drive(monkeypatch, dict(base, status="running"), dict(base, status="completed"))
        sums = [b for e in events if e.get("type") == "assistant" for b in e.get("content") or []
                if isinstance(b, dict) and b.get("type") == "pipeline_summary"]
        assert len(sums) == 1 and sums[0]["runId"] == "run_d1"
        again = self._drive(monkeypatch, dict(base, status="completed"), dict(base, status="completed"))
        assert not any(isinstance(b, dict) and b.get("type") == "pipeline_summary"
                       for e in again if e.get("type") == "assistant" for b in e.get("content") or [])


# ── chat summary + artifact_cli wiring ───────────────────────────────────────

def test_delivery_summary_lists_the_products():
    from core.run_delivery_summary import RULE_LINE, render_delivery_summary_md
    docs = [{"name": "REQUIREMENTS.md", "purpose": "需求", "path": "/r/REQUIREMENTS.md", "exists": True},
            {"name": "DESIGN_SPEC.md", "purpose": "方案", "path": None, "exists": False,
             "reason": "skipped: profile 'research' has no plan stage"},
            {"name": "REVIEW.md", "purpose": "审查", "path": "/r/REVIEW.md", "exists": True},
            {"name": "REPORT.md", "path": "/r/REPORT.md", "exists": True}]
    md = render_delivery_summary_md({"id": "run_d1", "status": "completed"}, {}, DELIVER, {}, None, [],
                                    "/r/REPORT.md", docs=docs)
    assert "Pipeline 执行产物" in md and "[REQUIREMENTS.md](/r/REQUIREMENTS.md)" in md
    assert "DESIGN\\_SPEC.md — skipped" in md and "2 位专家 · 3 条发现 · 2 已处置" in md
    assert md.count(RULE_LINE) == 2 and md.index("REVIEW.md") < md.index("REPORT.md")


def test_stage_complete_helper_writes_and_reports_the_doc(tmp_path):
    import artifact_cli
    run_dir, state = _run(tmp_path, {"evaluate": EVAL})
    warnings: list = []
    p = artifact_cli._write_stage_doc("run_d1", run_dir, state, "evaluate", warnings)
    assert p and Path(p).name == "REQUIREMENTS.md" and warnings == []
    assert artifact_cli._write_stage_doc("run_d1", run_dir, state, "build", warnings) is None
    missing = dict(state, stages=[{"stage": "plan", "status": "completed"}])
    assert artifact_cli._write_stage_doc("run_d1", run_dir, missing, "plan", warnings) is None
    assert warnings and "DESIGN_SPEC.md was not written" in warnings[0]


def test_deliverable_b_carries_the_run_documents(tmp_path, capsys):
    from tests.test_deliverable_checkin import _run_commit, _src_repo, _write_run_with_report
    ws = tmp_path / "ws"
    (ws / "Projects" / "TestProject" / ".artifacts" / "runs").mkdir(parents=True)
    repo = _src_repo(tmp_path, "docrepo")
    run_dir = _write_run_with_report(ws, "run_docb", [str(repo / "f.py")])
    (run_dir / "REQUIREMENTS.md").write_text(f"# Requirements\nevidence: {Path.home()}/x/cli.py:10\n")
    (run_dir / "REVIEW.md").write_text("# Review\n")
    (run_dir / "evaluation-20261019-art_x.json").write_text("{}")   # a snapshot never travels
    _run_commit(ws, "run_docb")
    capsys.readouterr()
    got = sorted(p.name for p in (repo / ".aidlc" / "deliverables" / "run_docb").iterdir())
    assert got == ["REPORT.md", "REQUIREMENTS.md", "REVIEW.md"]
    copied = (repo / ".aidlc" / "deliverables" / "run_docb" / "REQUIREMENTS.md").read_text()
    assert str(Path.home()) not in copied and "~/x/cli.py:10" in copied


def test_model_text_cannot_open_a_fence_and_refuted_open_findings_count_once():
    md = rd.render_design_spec({"id": "r"}, {}, {"approach": "```python\nrm -rf /", "exit_criteria": [
        {"id": "X1", "kind": "test", "ref": "t", "expect": "pass"}]})
    assert "```" not in md.split("## Plan", 1)[1] and "X1" in md
    deliver = {"adversarial_review": {"findings": [
        {"specialist": "security", "severity": "HIGH", "resolved": False, "finding": "Not a bug: scoped"}]}}
    md = rd.render_review({"id": "r"}, {}, {}, {}, deliver)
    assert "| security | 1 | 1 | 0 | 1 | 0 |" in md


def test_doc_status_tells_not_generated_from_not_completed(tmp_path):
    run_dir, state = _run(tmp_path, {"evaluate": EVAL})          # completed, no document written
    state["stages"].append({"stage": "plan", "status": "in_progress"})
    st = {d["name"]: d for d in rd.doc_status(run_dir, "full", state)}
    assert st["REQUIREMENTS.md"]["reason"].startswith("not generated: evaluate completed")
    assert st["DESIGN_SPEC.md"]["reason"] == "missing: plan has not completed"
    state["stages"][0]["status"] = "done"                       # the third finished spelling
    st = {d["name"]: d for d in rd.doc_status(run_dir, "full", state)}
    assert st["REQUIREMENTS.md"]["reason"].startswith("not generated: evaluate completed")
    state["stages"][0].pop("artifact_id")                       # finished, artifact unreadable
    st = {d["name"]: d for d in rd.doc_status(run_dir, "full", state)}
    assert st["REQUIREMENTS.md"]["reason"] == "not generated: the evaluate artifact could not be read"


def test_completion_check_counts_done_as_finished(tmp_path, monkeypatch):
    """I2: completion_check and doc_status share one finished-status set — a 'done' stage
    whose document cannot be written is a write failure (error), not an unfinished stage."""
    run_dir, state = _run(tmp_path, {"evaluate": EVAL})
    state["stages"][0]["status"] = "done"

    def boom(path, text):
        raise OSError("disk full")
    monkeypatch.setattr(rd, "_atomic_write", boom)
    errs, warns, _ = rd.completion_check(run_dir, state)
    assert any("REQUIREMENTS.md could not be written" in e for e in errs)
    assert not any("REQUIREMENTS.md: the evaluate stage is not completed" in w for w in warns)


def test_every_reader_shares_one_finished_status_set():
    """I2 across the system: the stage rows / summary count and the documents agree."""
    from core.pipeline_profiles import FINISHED_STATUSES
    assert rd._DONE is FINISHED_STATUSES
    assert {s for s, v in pse._TERMINAL.items() if v == "completed"} == set(FINISHED_STATUSES)
    st = {"status": "completed", "profile": "full", "run_dir": None,
          "stages": {"evaluate": {"stage": "evaluate", "status": "done"}}}
    assert pse.summary_block("run_d1", st, "now")["stagesDone"] == 1


# ── what each stage shows in the chat (XG 2026-10-20) ───────────────────────────

def test_every_stage_gets_a_summary_from_its_artifact():
    """A completed stage's row says what it produced even when the model wrote no
    summary: the line is built from the artifact by code."""
    assert rd.stage_summary("evaluate", EVAL) == "GO · scope standard · 1 requirement · 1 acceptance criterion"
    assert rd.stage_summary("think", THINK) == "QUALITY · 2 alternatives weighed"
    assert rd.stage_summary("plan", PLAN) == "move arg building into one function · 1 exit criterion"
    build = {"files_changed": ["a.py", "b.py"], "tdd": {"green_pass": True, "tests_generated": 3},
             "break_tests": [{"test": "t.py::x"}]}
    assert rd.stage_summary("build", build) == "2 files changed · 3 tests written · tests green · 1 break test"
    assert rd.stage_summary("review", {"approved": False, "findings_count": 4, "open_items": ["x"]}) \
        == "not approved · 4 findings · 1 open item"
    assert rd.stage_summary("test", {"tests_total": 120, "failed": 0, "regressions": 0}) \
        == "120 tests · 0 failed · 0 regressions"
    deliver = {"title": "Keep the flag", "adversarial_review": {"findings_total": 3, "findings_fixed": 2}}
    assert rd.stage_summary("deliver", deliver, {"push_ready": False}) \
        == "Keep the flag · 2/3 findings fixed"  # push-readiness: the summary card
    assert rd.stage_summary("reflect", {}, {"lessons": ["a", "b"]}) == "2 lessons"
    assert rd.stage_summary("build", {}) is None and rd.stage_summary("nope", EVAL) is None


def test_a_document_excerpt_is_its_first_section_not_the_generated_header(tmp_path):
    run_dir, state = _run(tmp_path, {"evaluate": EVAL})
    rd.write_all(run_dir, state)
    ex = rd.doc_excerpt(run_dir / "REQUIREMENTS.md")
    assert ex.startswith("## ") and "**Run**" not in ex and "Generated from" not in ex
    assert "\n## " not in ex                                      # one section only
    (run_dir / "big.md").write_text("# T\n\n## S\n" + "word " * 500)
    cut = rd.doc_excerpt(run_dir / "big.md", max_chars=100)
    assert len(cut) == 100 and cut.endswith("…")
    assert rd.doc_excerpt(run_dir / "missing.md") is None


def test_the_deliver_row_carries_review_fixing_spec_and_report_with_excerpts(tmp_path, monkeypatch):
    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    run_dir, state = _run(tmp_path, {"deliver": DELIVER})
    for name in ("REVIEW.md", "FIXING_SPEC.md", "REPORT.md"):
        (run_dir / name).write_text(f"# {name}\n\n## First\n{name} body\n\n## Second\nmore\n")
    blocks = [{"type": "pipeline_stage", "stage": "deliver", "status": "completed"}]
    refs = pse.attach_stage_docs(blocks, {"run_dir": str(run_dir)})
    assert [r["name"] for r in refs] == ["REVIEW.md", "FIXING_SPEC.md", "REPORT.md"]
    assert [d["name"] for d in blocks[0]["docs"]] == ["REVIEW.md", "FIXING_SPEC.md", "REPORT.md"]
    assert blocks[0]["doc"]["name"] == "REVIEW.md"
    assert blocks[0]["docs"][1]["excerpt"] == "## First\nFIXING_SPEC.md body"


_VIEW = {"ok": True, "convergence": "deterministic", "spawns": 5,
         "next": {"action": "done"},
         "findings": [{"id": "security-1", "round": 1, "expert": "security", "state": "proven",
                       "test": "backend/tests/t.py::test_a", "claim": "flag lost"},
                      {"id": "correctness-R2-nooutput", "round": 2, "expert": "correctness",
                       "state": "open", "test": None, "claim": "no usable output"}],
         "notes": [{"severity": "LOW", "expert": "correctness", "claim": "stale comment"}],
         "push_ready_reasons": ["gate2: correctness-R2-nooutput open"]}


def test_deterministic_gate2_writes_fixing_spec_on_a_fix_and_review_when_done(tmp_path):
    run_dir, state = _run(tmp_path, {"deliver": None})
    (run_dir / "evidence").mkdir()
    (run_dir / "evidence" / rd.GATE2_VIEW).write_text(json.dumps(_VIEW))
    st = dict(state, gate2_v2={"events": [{"kind": "fixed", "pass": 1}]})
    # a fix declared, Gate-2 not done: FIXING_SPEC only
    written = rd.write_gate2_docs(run_dir, st, dict(_VIEW, next={"action": "spawn"}))
    assert [Path(p).name for p in written] == ["FIXING_SPEC.md"]
    fix = (run_dir / "FIXING_SPEC.md").read_text()
    assert "## Fix pass 1" in fix and "security-1" in fix and "backend/tests/t.py::test_a" in fix
    assert "## Not in a fix pass yet" in fix and "correctness-R2-nooutput" in fix
    assert not (run_dir / "REVIEW.md").exists()
    # done: REVIEW with the ledger
    written = rd.write_gate2_docs(run_dir, st, _VIEW)
    assert [Path(p).name for p in written] == ["FIXING_SPEC.md", "REVIEW.md"]
    review = (run_dir / "REVIEW.md").read_text()
    assert "## Gate 2 — deterministic ledger" in review and "1 open, 1 proven" in review
    assert "stale comment" in review and "Lowers push_ready" in review
    # no fix declared: no FIXING_SPEC
    other = tmp_path / "o"
    other.mkdir()
    assert rd.write_gate2_docs(other, state, dict(_VIEW, next={"action": "spawn"})) == []


def test_fixing_spec_is_listed_for_every_run_and_required_once_a_fix_was_declared(tmp_path):
    run_dir, state = _run(tmp_path, {"evaluate": EVAL})
    names = [d["name"] for d in rd.doc_status(run_dir, "full", state)]
    assert names == ["REQUIREMENTS.md", "DESIGN_SPEC.md", "REVIEW.md", "FIXING_SPEC.md", "REPORT.md"]
    fx = next(d for d in rd.doc_status(run_dir, "full", state) if d["name"] == "FIXING_SPEC.md")
    assert fx["required"] is False and fx["reason"] == "skipped: Gate-2 had nothing to fix"
    st = dict(state, gate2_v2={"events": [{"kind": "fixed", "pass": 1}]})
    fx = next(d for d in rd.doc_status(run_dir, "full", st) if d["name"] == "FIXING_SPEC.md")
    assert fx["required"] is True and fx["reason"] == "missing"


def test_fixing_spec_reason_counts_fixes_made_without_a_spec(tmp_path):
    """A direct run fixes Gate-2 findings in one pass with no fixing spec: the reason
    must not say Gate-2 had nothing to fix."""
    deliver = {"adversarial_review": {"findings_total": 13, "findings_fixed": 13, "findings": []}}
    run_dir, state = _run(tmp_path, {"deliver": deliver}, profile="direct")
    fx = next(d for d in rd.doc_status(run_dir, "direct", state) if d["name"] == "FIXING_SPEC.md")
    assert fx["required"] is False
    assert fx["reason"] == "not written: 13 Gate-2 finding(s) were fixed without a fixing spec"
    clean = tmp_path / "c"
    clean.mkdir()
    run_dir, state = _run(clean, {"deliver": {"adversarial_review": {"findings_fixed": 0, "findings": [
        {"resolved": True, "finding": "Not a bug: scoped"}]}}}, profile="direct")
    fx = next(d for d in rd.doc_status(run_dir, "direct", state) if d["name"] == "FIXING_SPEC.md")
    assert fx["reason"] == "skipped: Gate-2 had nothing to fix"
    refuted = tmp_path / "r"
    refuted.mkdir()
    run_dir, state = _run(refuted, {"deliver": {"adversarial_review": {"findings_fixed": 0, "findings": [
        {"resolved": True, "disposition": "refuted", "finding": "guard bypass"}]}}}, profile="direct")
    fx = next(d for d in rd.doc_status(run_dir, "direct", state) if d["name"] == "FIXING_SPEC.md")
    assert fx["reason"] == "skipped: Gate-2 had nothing to fix"


def test_deliver_stage_review_keeps_the_deterministic_ledger(tmp_path):
    run_dir, state = _run(tmp_path, {"deliver": DELIVER})
    (run_dir / "evidence").mkdir()
    (run_dir / "evidence" / rd.GATE2_VIEW).write_text(json.dumps(_VIEW))
    rd.write_stage_doc(run_dir, state, "deliver")
    review = (run_dir / "REVIEW.md").read_text()
    # one findings table: the ledger replaces the legacy one, the findings are not listed twice
    assert "## Gate 2 — deterministic ledger" in review and "## Gate 2 findings" not in review


class TestRunEndSummary:
    def test_a_direct_run_that_is_abandoned_gets_the_summary_card(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        run_dir, state = _run(tmp_path, {"deliver": DELIVER}, profile="direct")
        (run_dir / "REPORT.md").write_text("# r\n\n## TL;DR\nwhat happened\n")
        stages = {r["stage"]: r for r in state["stages"]}
        base = {"profile": "direct", "stages": stages, "timing": {}, "run_dir": str(run_dir)}
        events = TestOrchestratorEmitsDocs()._drive(monkeypatch, dict(base, status="running"),
                                                    dict(base, status="abandoned"))
        sums = [b for e in events if e.get("type") == "assistant" for b in e.get("content") or []
                if isinstance(b, dict) and b.get("type") == "pipeline_summary"]
        assert len(sums) == 1 and sums[0]["status"] == "abandoned" and sums[0]["profile"] == "direct"
        report = next(d for d in sums[0]["docs"] if d["name"] == "REPORT.md")
        assert report["absolutePath"]

    def test_a_second_ending_gives_no_second_card(self, monkeypatch, tmp_path):
        """failed -> abandoned is the same run ending once: one summary card."""
        monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
        run_dir, state = _run(tmp_path, {"deliver": DELIVER}, profile="direct")
        stages = {r["stage"]: r for r in state["stages"]}
        base = {"profile": "direct", "stages": stages, "timing": {}, "run_dir": str(run_dir)}
        events = TestOrchestratorEmitsDocs()._drive(monkeypatch, dict(base, status="failed"),
                                                    dict(base, status="abandoned"))
        sums = [b for e in events if e.get("type") == "assistant" for b in e.get("content") or []
                if isinstance(b, dict) and b.get("type") == "pipeline_summary"]
        assert sums == []


# ── one run summary, verdict first (XG 2026-10-20) ──────────────────────────────

def test_the_summary_card_carries_what_was_asked_what_changed_and_where_it_landed(monkeypatch, tmp_path):
    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    deliver = dict(DELIVER, outcome={"problem_before": "原来卡片看不懂", "result_now": "现在一张卡结论先行",
                                     "approach": ["x"]})
    run_dir, state = _run(tmp_path, {"deliver": deliver}, profile="direct")
    stages = {r["stage"]: r for r in state["stages"]}
    sb = pse.summary_block("run_d1", {"status": "completed", "profile": "direct", "stages": stages,
                                      "run_dir": str(run_dir), "requirement": "keep the flag",
                                      "landed_shas": ["5d941e20aa11bb22"]}, "now")
    assert sb["intent"] == "keep the flag"
    assert sb["solved"] == {"before": "原来卡片看不懂", "now": "现在一张卡结论先行"}
    assert sb["landedSha"] == "5d941e20aa11"
    # half an outcome is not a before/after; no landing, no sha
    run_dir2 = tmp_path / "two"
    run_dir2.mkdir()
    sb = pse.summary_block("run_d1", {"status": "abandoned", "profile": "direct", "stages": {},
                                      "run_dir": str(run_dir2)}, "now")
    assert sb["solved"] is None and sb["landedSha"] is None and sb["intent"] is None


def test_read_stage_state_lists_the_landed_commits(monkeypatch, tmp_path):
    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    run_dir, state = _run(tmp_path, {"deliver": DELIVER})
    state["commits"] = [{"sha": "aaa111", "landed": False}, {"sha": "bbb222", "landed": True}]
    (run_dir / "run.json").write_text(json.dumps(state))
    assert pse.read_stage_state("run_d1")["landed_shas"] == ["bbb222"]


def test_a_not_ready_summary_carries_its_plain_reasons(monkeypatch, tmp_path):
    """A refused landing sends no attention card: the summary says why itself."""
    monkeypatch.setattr(pse, "get_swarmws", lambda: str(tmp_path))
    run_dir, state = _run(tmp_path, {"deliver": DELIVER}, profile="direct")
    stages = {r["stage"]: r for r in state["stages"]}
    stages["deliver"] = dict(stages["deliver"], push_ready=False,
                             push_ready_reason="test evidence predates later edits (['a.py']); x: y")
    base = {"profile": "direct", "stages": stages, "run_dir": str(run_dir)}
    sb = pse.summary_block("run_d1", dict(base, status="completed"), "now")
    assert sb["pushReasons"][0] == {"code": "tests_not_final", "text": "The tests did not run on the final code"}
    stages["deliver"]["push_ready"] = True
    assert pse.summary_block("run_d1", dict(base, status="completed"), "now")["pushReasons"] == []


# ── the plan sections the committed document did not show before ──────────────
PLAN_SECTIONS = {
    "security_boundaries": [
        {"boundary": "agent pattern → git grep", "trust_assumption": "no shell",
         "enforcement": "backend/scripts/consumers_gate.py:120"},
        {"boundary": "file-writing tool", "trust_assumption": "confined", "escalate": True,
         "reason": "no confinement yet"}],
    "boundaries": {"always": ["fence model text"], "ask_first": ["CI changes"],
                   "never": ["fail open silently"]},
    "success_criteria": ["lint exits 0 on the tree"],
    "design_doc": {"test_strategy": [{"ac": "AC7", "how": "temp git repo", "mock": "none",
                                      "inputs": "py files"}]},
    "implementation_constraints": {"consumers": [
        {"id": "K1", "contract": "read_index may be unusable", "pattern": "read_index\\(",
         "paths": ["backend"], "readers": [
             {"at": "backend/a.py::f", "status": "migrated"},
             {"at": "backend/b.py::g", "status": "unaffected", "reason": "logs only"}]}]},
}


def test_design_spec_renders_boundaries_tests_success_and_consumers():
    md = rd.render_design_spec({"id": "run_d2"}, {}, {**PLAN, **PLAN_SECTIONS})
    for needle in ("## Security Boundaries", "agent pattern → git grep", "consumers_gate.py:120",
                   "escalate: no confinement yet", "## Boundaries", "**Never:** fail open silently",
                   "## Test Strategy", "| AC7 | temp git repo |", "## Success Criteria",
                   "lint exits 0 on the tree", "## Contract readers", "| K1 |", "read_index\\(",
                   "backend/b.py::g", "unaffected — logs only"):
        assert needle in md, needle


def test_design_spec_renders_no_contract_change_reason():
    md = rd.render_design_spec({"id": "r"}, {}, {"implementation_constraints": {
        "no_contract_change_reason": "adds a module nothing reads yet"}})
    assert "**No contract change:** adds a module nothing reads yet" in md


def test_design_spec_omits_absent_sections():
    md = rd.render_design_spec({"id": "r"}, {}, PLAN)
    for title in ("## Security Boundaries", "## Boundaries", "## Test Strategy",
                  "## Success Criteria", "## Contract readers"):
        assert title not in md


@pytest.mark.parametrize("bad", [
    {"security_boundaries": 5, "boundaries": "x", "success_criteria": {"a": 1},
     "implementation_constraints": {"consumers": "x"}, "design_doc": {"test_strategy": 7}},
    {"security_boundaries": [None, 3, {"boundary": ["x"]}], "boundaries": {"never": "x"},
     "implementation_constraints": {"consumers": [None, {"readers": "x"}, {"readers": [1]}]}},
])
def test_new_sections_never_raise_on_odd_shapes(bad):
    rd.render_design_spec({"id": "r"}, {}, bad)


def test_review_doc_shows_the_gate1_points():
    recs = {"build": {"gate1_verdict": "WARN",
                      "gate1_points": ["2. Failure Pattern: WARN. normalize paths first",
                                       "3. Simplicity: WARN. reuse check_migration_class"]}}
    md = rd.render_review({"id": "r"}, {}, recs, {}, {})
    assert "Gate 1 — plan skeptic | WARN" in md
    assert "normalize paths first" in md and "reuse check_migration_class" in md
