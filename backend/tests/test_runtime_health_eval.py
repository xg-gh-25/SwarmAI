"""Tests for the runtime_health evaluator + fault-injection harness (run_f646b175).

AC5: runtime_health is a wired programmatic evaluator with the canary contract.
AC6: the fault-injection harness drives the REAL retry recovery path and exits 0
     only when recovery EXECUTED (STEERING #11 — not a passive observation).
AC7: a runtime_health case runs through evaluate_case and yields pass/fail.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts import eval_runner

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"


# ── AC5: evaluator is registered + honors the canary contract ──────────────

def test_ac5_runtime_health_in_programmatic_evaluators():
    assert "runtime_health" in eval_runner.PROGRAMMATIC_EVALUATORS


def test_ac5_evaluate_case_dispatches_runtime_health():
    """A case with evaluators=[runtime_health] is routed to the evaluator and
    returns the canary contract (passed on exit-0 + marker)."""
    case = {
        "id": "GS_RTH_TEST",
        "evaluators": ["runtime_health"],
        "verification": {
            "command": "python -c \"print('RECOVERY_EXECUTED ok')\"",
            "expected_contains": "RECOVERY_EXECUTED ok",
        },
        "affected_by": [],
    }
    result = eval_runner.evaluate_case(case, _REPO)
    assert result["status"] == "passed"
    assert result["evaluator"] == "runtime_health"


def test_ac5_runtime_health_fails_when_marker_absent():
    case = {
        "id": "GS_RTH_FAIL",
        "evaluators": ["runtime_health"],
        "verification": {
            "command": "python -c \"print('nope'); import sys; sys.exit(1)\"",
            "expected_contains": "RECOVERY_EXECUTED ok",
        },
        "affected_by": [],
    }
    result = eval_runner.evaluate_case(case, _REPO)
    assert result["status"] == "failed"


def test_ac5_runtime_health_rejects_missing_command():
    case = {"id": "X", "evaluators": ["runtime_health"], "verification": {}, "affected_by": []}
    result = eval_runner.evaluate_case(case, _REPO)
    assert result["status"] == "error"


# ── AC6: the fault-injection harness actually drives the recovery path ─────

def test_ac6_fault_injection_harness_recovers_and_exits_zero():
    """The harness drives the REAL SessionUnit._retry_with_resume loop with an
    injected retriable zombie error; it must print RECOVERY_EXECUTED and exit 0,
    proving the recovery path executed (not faked)."""
    harness = _BACKEND / "scripts" / "fault_inject_recovery.py"
    proc = subprocess.run(
        [sys.executable, str(harness)],
        capture_output=True, text=True, timeout=30, cwd=str(_REPO),
    )
    assert proc.returncode == 0, f"harness failed: {proc.stdout}\n{proc.stderr}"
    assert "RECOVERY_EXECUTED ok" in proc.stdout
    # The marker reports the real recovery metrics — recovery within MAX retries.
    assert "respawns=" in proc.stdout and "restreams=" in proc.stdout
