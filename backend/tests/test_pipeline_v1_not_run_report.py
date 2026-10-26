"""Tests the agent did not run are stated, never passed (design 2026-10-26-pipeline-
review-closure-and-visibility §4.3, step 8).

Before: a TEST command marked ``not_run`` (for instance "red at base") showed in REPORT
only as a count and among the first 10 commands; the audit's Quality could still be PASS.

What is tested, on ``audit.build`` / ``render``:
- with a not_run command in the latest TEST report: Quality is WARN and says "n not run";
- REPORT lists every not_run command with its reason under its own heading;
- with none: Quality PASS and no such heading;
- a ``fail`` still blocks before any of this (the TEST stage refuses it: TS-1, LG-1).
"""
from __future__ import annotations

from core.run_protocol.audit import build, render


def _audit(commands: list[dict]):
    n = {r: sum(c["result"] == r for c in commands) for r in ("pass", "fail", "not_run")}
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    events = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}},
              {"kind": "validation_report", "at": "2026-10-22T00:00:20+00:00",
               "payload": {"stage": "test", "repo": "repo", "code_tree": "1" * 40,
                           "n_pass": n["pass"], "n_fail": n["fail"], "n_not_run": n["not_run"]}}]
    return build(run_id="run_x", projection=projection, events=events, repos_isolated=1,
                 contract=contract, open_checks={}, findings=[],
                 audit_through="COMPLETED", commits={"repo": "a" * 40},
                 test_reports={"repo": {"commands": commands}})


PASS = {"command": "pytest tests/test_a.py", "result": "pass"}


def test_a_not_run_command_is_never_a_pass_and_is_listed_with_its_reason():
    skipped = [{"command": f"npm test -- file{i}", "result": "not_run",
                "reason": f"red on main before this change ({i})"} for i in range(12)]
    value = _audit([PASS] + skipped)
    quality = value["summary"]["quality"]
    assert quality["verdict"] == "WARN" and quality["verification_not_run"] == 12, quality
    report = render(value, fmt="report")
    assert "12 not run" in report
    section = report.split("## Not run (the agent's reason, not checked)")[1]
    for i in range(12):            # every one, past the first 10 commands too
        assert f"red on main before this change ({i})" in section


def test_without_not_run_quality_passes_and_no_section():
    value = _audit([PASS])
    assert value["summary"]["quality"]["verdict"] == "PASS", value["summary"]["quality"]
    assert "## Not run" not in render(value, fmt="report")
