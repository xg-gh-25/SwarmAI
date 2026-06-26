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


# ── run_4596411e: ① schedule-lock + ② new fault-injection harnesses ────────

import subprocess as _sp
import sys as _sys


def test_ac1_rth_cases_in_biweekly_scheduled_set():
    """① GS_RTH001/002/003 must survive the biweekly (non-behavior) filter — so
    they run automatically in CI, not just on-demand. Loads the REAL golden_set."""
    from pathlib import Path as _P
    from scripts.eval_runner import load_golden_set
    gs_path = _P.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "SwarmAI" / "golden_set.yaml"
    gs = load_golden_set(gs_path)
    nonbehavior = {c["id"] for c in gs["cases"] if c.get("eval_method") != "behavior"}
    for cid in ("GS_RTH001", "GS_RTH002", "GS_RTH003"):
        assert cid in nonbehavior, f"{cid} not in biweekly scheduled set"


def test_ac1_runtime_health_timeout_is_adequate():
    """The biweekly path passes no override → runtime_health gets ≥15s (the cold
    harness needs it; the 3s canary divider would false-time-out)."""
    from scripts import eval_runner
    # No override → 30s default.
    case = {"id": "T", "evaluators": ["runtime_health"],
            "verification": {"command": "python -c \"print('RECOVERY_EXECUTED ok')\"",
                             "expected_contains": "RECOVERY_EXECUTED ok"}, "affected_by": []}
    r = eval_runner.eval_runtime_health(case, _REPO, timeout_override=None)
    assert r["status"] == "passed"
    # A tiny override must be floored to ≥15, not capped to 3.
    import inspect
    src = inspect.getsource(eval_runner.eval_runtime_health)
    assert "max(15" in src, "runtime_health timeout must floor at 15s (H1)"


def _run_harness(name: str, *args) -> _sp.CompletedProcess:
    path = _BACKEND / "scripts" / name
    return _sp.run([_sys.executable, str(path), *args],
                   capture_output=True, text=True, timeout=40, cwd=str(_REPO))


def test_ac2_dual_tab_harness_isolation_holds():
    p = _run_harness("fault_inject_dual_tab.py")
    assert p.returncode == 0, f"{p.stdout}\n{p.stderr}"
    assert "ISOLATION_OK" in p.stdout


def test_ac3_dual_tab_harness_non_vacuous():
    """Negative mode: an orphan MUST be evictable, proving the guard discriminates."""
    p = _run_harness("fault_inject_dual_tab.py", "--negative")
    assert p.returncode == 0, f"{p.stdout}\n{p.stderr}"
    assert "NON_VACUOUS ok" in p.stdout


def test_ac4_dumb_spawn_harness_watchdog_fires():
    p = _run_harness("fault_inject_dumb_spawn.py")
    assert p.returncode == 0, f"{p.stdout}\n{p.stderr}"
    assert "WATCHDOG_KILLED" in p.stdout


def test_ac5_dumb_spawn_harness_non_vacuous():
    """Negative mode: a within-window spawn must NOT be killed (discrimination)."""
    p = _run_harness("fault_inject_dumb_spawn.py", "--negative")
    assert p.returncode == 0, f"{p.stdout}\n{p.stderr}"
    assert "NON_VACUOUS ok" in p.stdout


def test_ac6_all_three_rth_cases_pass_via_evaluate_case():
    """All 3 runtime_health cases dispatch + pass through evaluate_case."""
    from pathlib import Path as _P
    from scripts.eval_runner import load_golden_set, evaluate_case
    gs = load_golden_set(_P.home() / ".swarm-ai" / "SwarmWS" / "Projects" / "SwarmAI" / "golden_set.yaml")
    by_id = {c["id"]: c for c in gs["cases"]}
    for cid in ("GS_RTH001", "GS_RTH002", "GS_RTH003"):
        r = evaluate_case(by_id[cid], _REPO)
        assert r["status"] == "passed", f"{cid}: {r}"
        assert r["evaluator"] == "runtime_health"
