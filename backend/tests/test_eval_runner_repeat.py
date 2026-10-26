"""eval_runner --affected-by / --repeat: pick the cases a governance edit can move
and judge the change by flipped cases (design 2026-10-26-evolution-loop-admission §3.6).
Pure helpers only — nothing runs against the deployed system."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import eval_runner as er  # noqa: E402


CASES = [
    {"id": "A", "affected_by": ["AGENT.R20", "backend/core/prompt_builder.py"]},
    {"id": "B", "affected_by": ["SOUL.P1"]},
    {"id": "C", "affected_by": []},
    {"id": "D"},
]


def test_select_by_affected_by():
    assert er.select_by_affected_by(CASES, ["AGENT"]) == ["A"]
    assert er.select_by_affected_by(CASES, ["SOUL", "prompt_builder"]) == ["A", "B"]
    assert er.select_by_affected_by(CASES, ["STEERING"]) == []


def _run(**statuses):
    return {"cases": [{"id": k, "status": v} for k, v in statuses.items()]}


def test_compare_repeats_finds_a_break_in_one_run_of_two():
    history = [_run(A="failed", B="passed"), _run(A="passed", C="skipped")]   # latest status per case wins
    runs = [_run(A="failed", B="passed", E="failed"), _run(A="passed", B="passed", E="failed")]
    flips = er.compare_repeats(history, runs)
    assert flips["pass_to_fail"] == ["A"]          # passed before, failed in one repeat
    assert flips["unstable"] == ["A"]
    assert flips["fail_to_pass"] == []


def test_compare_repeats_fail_to_pass_needs_every_repeat():
    history = [_run(A="failed", B="failed")]
    runs = [_run(A="passed", B="passed"), _run(A="passed", B="failed")]
    flips = er.compare_repeats(history, runs)
    assert flips["fail_to_pass"] == ["A"]
    assert flips["unstable"] == ["B"]


def test_cli_accepts_the_flags(monkeypatch):
    seen = {}
    monkeypatch.setattr(er, "cmd_run", lambda args: seen.update(vars(args)))
    monkeypatch.setattr(sys, "argv", ["eval_runner", "run", "--trigger", "deploy",
                                      "--affected-by", "AGENT,SOUL", "--repeat", "2"])
    er.main()
    assert (seen["affected_by"], seen["repeat"]) == ("AGENT,SOUL", 2)


def test_affected_by_with_no_match_runs_nothing(monkeypatch, tmp_path):
    """An empty filter means "run everything" to run_eval — cmd_run must stop first."""
    import argparse
    calls = []
    monkeypatch.setattr(er, "_find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(er, "load_merged_golden_set", lambda root: {"cases": CASES})
    monkeypatch.setattr(er, "run_eval", lambda *a, **k: calls.append(a) or {})
    args = argparse.Namespace(cases=None, tags=None, trigger="deploy", affected_by="STEERING",
                              repeat=2, programmatic_only=False, include_behavior=False,
                              verify_teeth=False, json=False)
    er.cmd_run(args)
    assert calls == []


def test_affected_by_drops_behavior_cases_without_consent(monkeypatch, tmp_path):
    import argparse
    seen = []
    cases = [{"id": "P", "affected_by": ["AGENT"]},
             {"id": "B", "affected_by": ["AGENT"], "eval_method": "behavior"}]
    monkeypatch.setattr(er, "_find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(er, "load_merged_golden_set", lambda root: {"cases": cases})
    monkeypatch.setattr(er, "write_run", lambda r, root: tmp_path / "x.json")
    monkeypatch.setattr(er, "generate_html_report", lambda *a: None)

    def _run_eval(gs, trigger, case_filter, root, **k):
        seen.append(list(case_filter))
        return {"overall_score": 0, "cases_passed": 0, "cases_failed": 0, "cases_skipped": 0,
                "dimensions": {}, "duration_seconds": 0, "cases": []}
    monkeypatch.setattr(er, "run_eval", _run_eval)
    base = dict(cases=None, tags=None, trigger="deploy", affected_by="AGENT", repeat=1,
                programmatic_only=False, verify_teeth=False, json=False)
    er.cmd_run(argparse.Namespace(include_behavior=False, **base))
    er.cmd_run(argparse.Namespace(include_behavior=True, **base))
    assert seen == [["P"], ["P", "B"]]
