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


def _run(at=None, **statuses):
    r = {"cases": [{"id": k, "status": v} for k, v in statuses.items()]}
    if at:
        r["triggered_at"] = at
    return r


GROUPS = ("definite_regression", "possible_regression", "baseline_unstable",
          "fail_to_pass", "baseline_short")


def test_compare_repeats_reports_the_five_groups():
    assert set(er.compare_repeats([], [])) == set(GROUPS)


def test_passed_twice_then_failed_twice_is_a_definite_regression():
    history = [_run(A="passed"), _run(A="passed")]
    runs = [_run(A="failed"), _run(A="failed")]
    flips = er.compare_repeats(history, runs)
    assert flips["definite_regression"] == ["A"]
    assert flips["possible_regression"] == []


def test_one_failure_after_the_change_is_only_a_possible_regression():
    history = [_run(A="passed"), _run(A="passed")]
    runs = [_run(A="failed"), _run(A="passed")]
    flips = er.compare_repeats(history, runs)
    assert flips["possible_regression"] == ["A"]
    assert flips["definite_regression"] == []


def test_a_baseline_that_disagrees_with_itself_judges_nothing():
    """The latest status alone was "passed": with one baseline run, A would be a regression."""
    history = [_run(A="failed"), _run(A="passed")]
    runs = [_run(A="failed"), _run(A="failed")]
    flips = er.compare_repeats(history, runs)
    assert flips["baseline_unstable"] == ["A"]
    assert flips["definite_regression"] == [] and flips["possible_regression"] == []


def test_the_baseline_is_the_two_newest_runs_by_time_not_by_list_order():
    """Records from the service (UTC names) and the CLI (local names) do not sort by file
    name; the two newest by triggered_at are the baseline."""
    history = [_run("2026-10-27T09:00:00+00:00", A="passed"),
               _run("2026-10-27T10:00:00+00:00", A="passed"),
               _run("2026-10-26T08:00:00+00:00", A="failed")]
    runs = [_run(A="failed"), _run(A="failed")]
    assert er.compare_repeats(history, runs)["definite_regression"] == ["A"]


def test_skipped_error_and_timeout_are_not_baseline_or_result():
    history = [_run(A="passed"), _run(A="timeout"), _run(A="passed"), _run(A="skipped")]
    runs = [_run(A="failed"), _run(A="error"), _run(A="failed")]
    assert er.compare_repeats(history, runs)["definite_regression"] == ["A"]


def test_fail_to_pass_needs_two_failed_baselines_and_every_repeat_passing():
    history = [_run(A="failed", B="failed"), _run(A="failed", B="failed")]
    runs = [_run(A="passed", B="passed"), _run(A="passed", B="failed")]
    flips = er.compare_repeats(history, runs)
    assert flips["fail_to_pass"] == ["A"]
    assert "B" not in sum((flips[g] for g in GROUPS), [])


def test_a_case_without_two_baseline_runs_is_listed_not_judged():
    """New case or first evaluation: it needs `--repeat 2` before the change (§3.6)."""
    history = [_run(A="passed")]
    runs = [_run(A="failed", N="failed"), _run(A="failed", N="failed")]
    flips = er.compare_repeats(history, runs)
    assert flips["baseline_short"] == ["A", "N"]
    assert flips["definite_regression"] == [] and flips["possible_regression"] == []


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
                              with_behavior=False, verify_teeth=False, json=False)
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
                programmatic_only=False, with_behavior=False, verify_teeth=False, json=False)
    er.cmd_run(argparse.Namespace(include_behavior=False, **base))
    er.cmd_run(argparse.Namespace(include_behavior=True, **base))
    assert seen == [["P"], ["P", "B"]]


def test_with_behavior_adds_every_behavior_case(monkeypatch, tmp_path):
    """--affected-by AGENT --with-behavior = the affected_by hits plus all behavior cases."""
    import argparse
    seen = []
    cases = [{"id": "P", "affected_by": ["AGENT"]},
             {"id": "Q", "affected_by": ["SOUL"]},
             {"id": "B1", "affected_by": ["AGENT"], "eval_method": "behavior"},
             {"id": "B2", "affected_by": [], "eval_method": "behavior"}]
    monkeypatch.setattr(er, "_find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(er, "load_merged_golden_set", lambda root: {"cases": cases})
    monkeypatch.setattr(er, "write_run", lambda r, root: tmp_path / "x.json")
    monkeypatch.setattr(er, "generate_html_report", lambda *a: None)

    def _run_eval(gs, trigger, case_filter, root, **k):
        seen.append(sorted(case_filter))
        return {"cases_passed": 0, "cases_failed": 0, "cases_skipped": 0,
                "duration_seconds": 0, "cases": []}
    monkeypatch.setattr(er, "run_eval", _run_eval)
    er.cmd_run(argparse.Namespace(cases=None, tags=None, trigger="deploy", affected_by="AGENT",
                                  repeat=1, programmatic_only=False, include_behavior=False,
                                  with_behavior=True, verify_teeth=False, json=False))
    assert seen == [["B1", "B2", "P"]]


def test_cli_accepts_with_behavior(monkeypatch):
    seen = {}
    monkeypatch.setattr(er, "cmd_run", lambda args: seen.update(vars(args)))
    monkeypatch.setattr(sys, "argv", ["eval_runner", "run", "--trigger", "deploy",
                                      "--affected-by", "AGENT", "--with-behavior", "--repeat", "2"])
    er.main()
    assert seen["with_behavior"] is True
