"""stage-start writes every gate / reviewer input the orchestrator used to assemble.

WHAT IS TESTED
- BUILD re-entry: Gate-1 is not handed out again for a plan it already judged with
  byte-identical inputs (the recorded verdict comes back instead of a prompt path);
  a changed input, or no recorded verdict, hands the prompt out again.
- REVIEW writes the shared project-knowledge block and the change diff to files.
- DELIVER writes the Meta-review prompt, filled (EXPERT-ID, requirement, knowledge).
- ``required_experts`` is listed only by DELIVER (the stage that dispatches them).

METHODOLOGY: ``cmd_stage_start`` driven in-process on a tmp workspace, reusing the
fixtures of test_stage_boundary_commands (``ws``, ``_make_run``, ``_fixture_repo``).
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.test_stage_boundary_commands import (  # noqa: F401  (ws is a fixture)
    PROJECT, _fixture_repo, _make_run, _run_state, _start, cli, ws)


def _record_gate1(root: Path, rid: str, verdict: str) -> None:
    p = root / "Projects" / PROJECT / ".artifacts" / "runs" / rid / "run.json"
    st = json.loads(p.read_text())
    st["stages"].append({"stage": "build", "status": "in_progress", "gate1_verdict": verdict})
    p.write_text(json.dumps(st))


def _handed_out(out: dict) -> bool:
    return bool(out.get("gate1_prompt_path")) and "gate1_verdict" not in out


def test_build_reentry_does_not_hand_out_gate1_again_for_the_judged_plan(ws, capsys):
    root, reg = ws
    rid = "run_g1once"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan"])
    code, out = _start(reg, capsys, rid, "build")
    assert code == 0 and out["gate1_prompt_path"].endswith("gate1-skeptic.md"), out
    # resumed before Gate-1 recorded anything: the prompt is handed out again
    code, out = _start(reg, capsys, rid, "build")
    assert _handed_out(out), out
    _record_gate1(root, rid, "WARN")
    code, out = _start(reg, capsys, rid, "build")
    assert code == 0, out
    assert "gate1_prompt_path" not in out and out["gate1_verdict"] == "WARN", out
    assert "do not spawn" in out["gate1_note"]
    # a changed input (a new blocking constraint) means a new judgment...
    (root / "Projects" / PROJECT / "TECH.md").write_text(
        "## Blocking Constraints\n- never write outside the run dir\n", encoding="utf-8")
    code, out = _start(reg, capsys, rid, "build")
    assert _handed_out(out), out
    assert "never write outside the run dir" in Path(out["gate1_prompt_path"]).read_text()
    # ...and re-entering before that new prompt is judged must NOT borrow the old verdict
    # (red-team 2026-10-18: the first version compared against the last prompt WRITTEN)
    code, out = _start(reg, capsys, rid, "build")
    assert _handed_out(out), out
    _record_gate1(root, rid, "PASS")
    code, out = _start(reg, capsys, rid, "build")
    assert out.get("gate1_verdict") == "PASS" and "gate1_prompt_path" not in out, out


def test_a_block_is_handed_out_again_unless_overridden(ws, capsys):
    root, reg = ws
    rid = "run_g1block"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan"])
    _start(reg, capsys, rid, "build")
    _record_gate1(root, rid, "BLOCK")
    code, out = _start(reg, capsys, rid, "build")
    assert _handed_out(out), out  # Fix Plan re-runs Gate-1
    p = root / "Projects" / PROJECT / ".artifacts" / "runs" / rid / "run.json"
    st = json.loads(p.read_text())
    st["stages"][-1].update(gate1_verdict="BLOCK", gate1_override="user: proceed anyway")
    p.write_text(json.dumps(st))
    code, out = _start(reg, capsys, rid, "build")
    assert out["gate1_verdict"] == "BLOCK" and out["gate1_override"] == "user: proceed anyway", out


def test_prompt_data_neutralizes_slots_and_marks_a_cut():
    s = cli._prompt_data("do X <PROJECT_KNOWLEDGE> and <PROJECT>", 1000)
    assert "<PROJECT_KNOWLEDGE>" not in s and "‹PROJECT_KNOWLEDGE›" in s and "‹PROJECT›" in s
    assert cli._prompt_data("a" * 20, 10) == "a" * 10 + " … (truncated)"


def test_review_writes_the_knowledge_block_and_the_change_diff(ws, capsys):
    root, reg = ws
    repo = _fixture_repo(root)
    (repo / "core" / "a.py").write_text("x = 2\n", encoding="utf-8")
    rid = "run_rv_inputs"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan", "build"],
              files_touched=[str(repo / "core" / "a.py")], required_experts=["concurrency"])
    code, out = _start(reg, capsys, rid, "review")
    assert code == 0, out
    assert "+x = 2" in Path(out["review_diff_path"]).read_text()
    block = Path(out["review_knowledge_path"]).read_text()
    assert block.strip() and "<PROJECT_KNOWLEDGE>" not in block
    assert "required_experts" not in out  # DELIVER dispatches them, not REVIEW
    code, out = _start(reg, capsys, rid, "build")
    assert "required_experts" not in out


def test_deliver_writes_the_filled_meta_review_prompt(ws, capsys):
    root, reg = ws
    repo = _fixture_repo(root)
    (repo / "core" / "a.py").write_text("x = 1\n" + "y = 2\n" * 60, encoding="utf-8")
    rid = "run_mr"
    _make_run(root, rid, "full", stages=["evaluate", "think", "plan", "build", "review", "test"],
              files_touched=[str(repo / "core" / "a.py")], required_experts=["concurrency"])
    code, out = _start(reg, capsys, rid, "deliver")
    assert code == 0, out
    text = Path(out["meta_review_prompt_path"]).read_text()
    assert text.splitlines()[0] == "EXPERT-ID: meta-review"
    assert "Requirement: make X work" in text and "<requirement from run.json>" not in text
    assert "<PROJECT_KNOWLEDGE>" not in text and f"Project: {PROJECT}" in text
    assert "core/a.py" in text
    assert out["required_experts"] == ["concurrency"]
    assert len(json.dumps(out, ensure_ascii=False).encode()) <= 8 * 1024
    plan = _run_state(root, rid)["dispatch_plan"]["deliver"]
    assert plan[-1] == "meta-review" and "meta-review" not in {e["id"] for e in out["experts"]}
