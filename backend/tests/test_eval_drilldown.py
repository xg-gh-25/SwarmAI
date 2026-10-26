"""An offline case's run record carries what the drill-down needs (design
2026-10-26-eval-as-product §8 item 1): the final answer, the trajectory, each
expected step found or not, and each tool step with its input and output."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import eval_runner as er  # noqa: E402
from scripts import scenario_runner as sr  # noqa: E402


def _event(role, content):
    return json.dumps({"type": role, "message": {"role": role, "content": content}})


def _stream():
    return "\n".join([
        _event("assistant", [{"type": "tool_use", "id": "t1", "name": "Grep", "input": {"pattern": "port"}}]),
        _event("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "config.toml: port = 18321"}]),
        _event("assistant", [{"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "missing.md"}}]),
        _event("user", [{"type": "tool_result", "tool_use_id": "t2", "is_error": True,
                         "content": [{"type": "text", "text": "File does not exist"}]}]),
        _event("assistant", [{"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "ls"}}]),
        json.dumps({"type": "result", "result": "端口是 18321。"}),
    ])


def test_parse_steps_pairs_each_call_with_its_result():
    steps = sr.parse_steps(_stream())
    assert [s["tool"] for s in steps] == ["Grep", "Read", "Bash"]
    assert steps[0]["output"] == "config.toml: port = 18321" and steps[0]["is_error"] is False
    assert steps[1]["output"] == "File does not exist" and steps[1]["is_error"] is True
    assert steps[2]["output"] is None  # the run ended before a result came back
    assert '"pattern": "port"' in steps[0]["input"]


def test_parse_steps_is_bounded():
    big = "x" * 5000
    lines = []
    for i in range(sr.STEP_MAX + 5):
        lines.append(_event("assistant", [{"type": "tool_use", "id": f"t{i}", "name": "Read", "input": {"p": big}}]))
        lines.append(_event("user", [{"type": "tool_result", "tool_use_id": f"t{i}", "content": big}]))
    steps = sr.parse_steps("\n".join(lines))
    assert len(steps) == sr.STEP_MAX
    assert all(len(s["input"]) <= sr.STEP_TEXT_MAX and len(s["output"]) <= sr.STEP_TEXT_MAX for s in steps)


def _case():
    return {"id": "OFF_X", "evaluators": ["trajectory_capture"], "title": "x", "tier": "behavior",
            "eval_method": "behavior", "scenario": {"prompt": "what port?"},
            "expected_trajectory": ["Read|Grep port", "Glob anything"], "trajectory_match": "any_order"}


def test_capture_records_drilldown_and_run_record_keeps_it(monkeypatch, tmp_path):
    stdout = _stream()

    def _raw(prompt, allowed_tools=None, timeout=240, system="bare", workspace=None):
        return sr.parse_trajectory(stdout), sr.parse_final_text(stdout), stdout
    monkeypatch.setattr("scripts.scenario_runner.run_scenario_raw", _raw)

    result = er.eval_trajectory_capture(_case())
    assert result["status"] == "failed"  # Glob never ran
    assert result["expected_check"] == [{"step": "Read|Grep port", "found": True},
                                        {"step": "Glob anything", "found": False}]
    assert result["final_text"] == "端口是 18321。"
    assert [s["tool"] for s in result["steps"]] == ["Grep", "Read", "Bash"]
    assert result["observed_trajectory"][0].startswith("Grep ")

    monkeypatch.setattr(er, "evaluate_case", lambda case, root, **kw: dict(result))
    monkeypatch.setattr(er, "compute_code_digest", lambda root: "d")
    run = er.run_eval({"cases": [{**_case(), "_origin": "product"}]}, "manual", None, tmp_path,
                      include_behavior=True)
    rec = run["cases"][0]
    for key in ("observed_trajectory", "expected_check", "steps", "final_text"):
        assert rec[key] == result[key], key

    # A user's own case runs in their real workspace and its record is a git-tracked
    # file there: the answer and the tool output (what the agent read) stay out.
    run = er.run_eval({"cases": [{**_case(), "_origin": "private"}]}, "manual", None, tmp_path,
                      include_behavior=True)
    rec = run["cases"][0]
    assert "final_text" not in rec and "steps" not in rec
    assert rec["observed_trajectory"] == result["observed_trajectory"]
    assert rec["expected_check"] == result["expected_check"]


def test_final_text_is_bounded(monkeypatch):
    long_answer = "答" * 9000
    stdout = _stream().rsplit("\n", 1)[0] + "\n" + json.dumps({"type": "result", "result": long_answer})

    def _raw(prompt, allowed_tools=None, timeout=240, system="bare", workspace=None):
        return sr.parse_trajectory(stdout), long_answer, stdout
    monkeypatch.setattr("scripts.scenario_runner.run_scenario_raw", _raw)
    assert len(er.eval_trajectory_capture(_case())["final_text"]) == er._FINAL_TEXT_MAX
