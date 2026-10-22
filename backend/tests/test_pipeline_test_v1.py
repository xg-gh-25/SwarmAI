"""Tests Pipeline v1 wiring inside the existing pipeline_test entrypoint.

Methodology: parser plus fake V1Runner; no application subprocess or data.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import pipeline_test


class FakeRunner:
    repo_name = "repo"
    run_dir = Path("/run")
    repo_root = Path("/nonexistent-repo")   # no backend tests → no dependency map
    code_tree = "1" * 40

    def __init__(self):
        self.calls = []
        self.conflicts = []

    def required_validations(self, changed):
        return [{"id": "lint", "argv": ["lint"], "call_type": "lint", "cwd": "."}]

    def run_validation(self, entry):
        self.calls.append(entry)
        return {"seq": 1, "result": "pass"}

    def record_pipeline_call(self, **kwargs):
        self.calls.append(kwargs)
        output, code = kwargs["output"], kwargs["exit_code"]
        result = ("not_run" if output.get("continue") or output.get("skipped_for_budget_total")
                  else "pass" if code == 0 else "fail" if code == 1 else "error")
        return {"seq": len(self.calls), "result": result}

    def record_cache_conflict(self, **kwargs):
        self.conflicts.append(kwargs)
        return kwargs

    def manifest_identity(self, changed):
        return {"sha256": "2" * 64, "base": "3" * 40,
                "required_validation_ids": []}

    def finalize(self, **kwargs):
        self.finalize_args = kwargs
        return {"tests": {"verdict": "green"},
                "validations": [{"id": "lint", "verdict": "pass", "seq": 1}]}

    def prior_selection(self):
        return {"files": ["backend/tests/a.py"], "nodeids": ["backend/tests/a.py::test_a"],
                "summary": {"passed": 1}, "dependency_scope": {}, "risk_rules": [],
                "risk_status": "within_cap", "mode": "verify"}


def test_parser_accepts_validation_mode_and_name():
    ns = pipeline_test._parser().parse_args(
        ["--project", "P", "--run-id", "run_x", "--mode", "validation", "--name", "lint"])
    assert ns.mode == "validation" and ns.name == "lint"


def test_validation_mode_runs_only_named_required_entry(monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(pipeline_test, "_v1_finalize", lambda *a, **k: {"repo": "repo"})
    ns = SimpleNamespace(name="lint")
    out, code = pipeline_test._run_v1_validation(ns, {}, runner, ["backend/a.py"])
    assert code == 0 and out["ok"] is True and runner.calls[0]["id"] == "lint"
    ns.name = "other"
    out, code = pipeline_test._run_v1_validation(ns, {}, runner, [])
    assert code == 2 and out["required"] == ["lint"]


def test_test_result_records_incomplete_continuation(monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(pipeline_test, "_v1_finalize", lambda *a, **k: {"repo": "repo"})
    ns = SimpleNamespace(mode="regression")
    output = pipeline_test._record_v1_test_result(
        ns, {}, runner, {"continue": True, "counts": {}, "failures": []}, 1,
        ["backend/a.py"])
    assert runner.calls[0]["output"]["continue"] is True
    assert output["v1"] == {"repo": "repo"}


def test_test_result_passes_actual_selection_provenance(monkeypatch):
    runner = FakeRunner()
    captured = {}
    def finalize(*args, **kwargs):
        captured.update(kwargs)
        return {"repo": "repo"}
    monkeypatch.setattr(pipeline_test, "_v1_finalize", finalize)
    ns = SimpleNamespace(mode="verify")
    pipeline_test._record_v1_test_result(ns, {}, runner, {
        "selected_files": ["backend/tests/a.py"],
        "selected_nodeids": ["backend/tests/a.py::test_a"],
        "counts": {"passed": 1}, "dependency_scope": {"files": ["backend/tests/a.py"]},
        "risk_rules": ["security"], "cache_key": "a" * 64,
    }, 0, ["backend/a.py"])
    assert captured["selection"]["files"] == ["backend/tests/a.py"]
    assert captured["selection"]["nodeids"] == ["backend/tests/a.py::test_a"]
    assert captured["cache_keys"] == ["a" * 64]


def test_active_v1_cache_uses_immutable_key_and_records_conflict(tmp_path):
    runner = FakeRunner()
    ctx = SimpleNamespace(cache=tmp_path, python="python3", warnings=[])
    plan = {"smoke": {}, "l2": ["backend/tests/a.py"], "risk_rules": ["security"],
            "changed": ["backend/a.py"]}
    key = pipeline_test._v1_cache_key(
        ctx, runner, "verify", plan, {"backend/tests/a.py": None},
        {"backend/a.py": "digest"}, [], "")
    assert len(key) == 64
    first = {"schema": 1, "mode": "verify", "counts": {"passed": 1},
             "results": [{"nodeid": "backend/tests/a.py::test_a", "outcome": "passed",
                          "duration": 0.1}], "failures": [], "runner_errors": []}
    pipeline_test._v1_cache_put(ctx, runner, key, first)
    assert pipeline_test._v1_cache_read(ctx, runner, key)["results"][0].get("duration") is None
    pipeline_test._v1_cache_put(ctx, runner, key, {**first, "counts": {"passed": 2}})
    assert runner.conflicts and runner.conflicts[-1]["key"] == key
    assert pipeline_test._v1_cache_read(ctx, runner, key) is None
