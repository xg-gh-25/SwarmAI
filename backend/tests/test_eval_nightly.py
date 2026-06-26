"""Tests for the nightly eval drift-alert handler (run_5edf2cc0 C6, gap G7)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from jobs.handlers.eval_nightly import run_eval_nightly, _DRIFT_TOLERANCE  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_alert_state(tmp_path, monkeypatch):
    """Each test gets its own alert-state file — the real one is a shared
    singleton path that would leak fingerprints across tests (and pollute the
    live workspace). autouse so no test can forget it."""
    import jobs.handlers.eval_nightly as mod
    monkeypatch.setattr(mod, "_ALERT_STATE", tmp_path / ".eval-nightly-alert.json")


def _runner(this, baseline=None):
    return lambda root: {"this": this, "baseline": baseline}


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return {"ok": True}


def test_clean_run_no_alert(tmp_path):
    spy = _Spy()
    r = run_eval_nightly(
        runner=_runner({"overall_score": 98, "bvt": {"green": True, "total": 55, "passed": 54}},
                       {"overall_score": 97}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "healthy"
    assert spy.calls == []  # green + no drift → silent


def test_bvt_red_alerts(tmp_path):
    spy = _Spy()
    r = run_eval_nightly(
        runner=_runner({"overall_score": 98,
                        "bvt": {"green": False, "total": 55, "passed": 53, "failed": 2, "error": 0}},
                       {"overall_score": 98}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "regression"
    assert any("BVT RED" in r2 for r2 in r["reasons"])
    assert len(spy.calls) == 1 and spy.calls[0]["channels"] == ["slack"]


def test_score_drift_alerts(tmp_path):
    spy = _Spy()
    r = run_eval_nightly(
        runner=_runner({"overall_score": 80, "bvt": {"green": True, "total": 55, "passed": 55}},
                       {"overall_score": 95}),  # 15-pt drop > tolerance
        notifier=spy, root=tmp_path)
    assert r["status"] == "regression"
    assert any("drift" in r2 for r2 in r["reasons"])
    assert len(spy.calls) == 1


def test_drift_within_tolerance_no_alert(tmp_path):
    spy = _Spy()
    r = run_eval_nightly(
        runner=_runner({"overall_score": 95 - _DRIFT_TOLERANCE + 0.1,
                        "bvt": {"green": True, "total": 55, "passed": 55}},
                       {"overall_score": 95}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "healthy"
    assert spy.calls == []


def test_dedup_no_repeat_alert(tmp_path, monkeypatch):
    """Same regression two nights running → only ONE alert (no storm)."""
    import jobs.handlers.eval_nightly as mod
    monkeypatch.setattr(mod, "_ALERT_STATE", tmp_path / ".alert.json")
    spy = _Spy()
    red = _runner({"overall_score": 98,
                   "bvt": {"green": False, "total": 55, "passed": 53, "failed": 2, "error": 0}},
                  {"overall_score": 98})
    run_eval_nightly(runner=red, notifier=spy, root=tmp_path)
    run_eval_nightly(runner=red, notifier=spy, root=tmp_path)
    assert len(spy.calls) == 1  # second run deduped


def test_no_baseline_no_drift_alert(tmp_path):
    """First-ever run (no baseline) must not false-alert on drift."""
    spy = _Spy()
    r = run_eval_nightly(
        runner=_runner({"overall_score": 50, "bvt": {"green": True, "total": 55, "passed": 55}},
                       None),
        notifier=spy, root=tmp_path)
    assert r["status"] == "healthy"
    assert spy.calls == []


def test_runner_crash_returns_error(tmp_path):
    def boom(root):
        raise RuntimeError("eval blew up")
    r = run_eval_nightly(runner=boom, notifier=_Spy(), root=tmp_path)
    assert r["status"] == "error" and "eval blew up" in r["reason"]


def test_default_runner_imports_resolve():
    """REGRESSION GUARD (adversarial CRITICAL #3): the prior tests all injected a
    fake runner=, so the REAL _default_runner's imports were never exercised —
    a `load_history` vs `_load_history` typo shipped a dead-on-arrival nightly job.
    This forces the real import path to resolve (without running the heavy eval)."""
    import jobs.handlers.eval_nightly as mod
    import inspect
    src = inspect.getsource(mod._default_runner)
    # the names _default_runner imports must all exist in eval_runner
    import scripts.eval_runner as er
    for name in ("load_golden_set", "_golden_set_path", "run_eval", "write_run", "_load_history"):
        assert hasattr(er, name), f"eval_runner missing {name} — _default_runner import would crash"
    # and the buggy bare name must NOT be what we import
    assert "import load_history" not in src or "_load_history as load_history" in src
