"""Tests for the scheduled eval drift-alert handler (run_5edf2cc0 C6, gap G7)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from jobs.handlers.eval_scheduled import run_eval_scheduled, _DRIFT_TOLERANCE  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_alert_state(tmp_path, monkeypatch):
    """Each test gets its own alert-state file — the real one is a shared
    singleton path that would leak fingerprints across tests (and pollute the
    live workspace). autouse so no test can forget it."""
    import jobs.handlers.eval_scheduled as mod
    monkeypatch.setattr(mod, "_ALERT_STATE", tmp_path / ".eval-scheduled-alert.json")
    # Isolate the biweekly-gate timestamp too: absent file → fail-open → the
    # alert-logic tests always RUN (they don't exercise the cadence gate). The
    # dedicated guard tests set their own _BIWEEKLY_STATE.
    monkeypatch.setattr(mod, "_BIWEEKLY_STATE", tmp_path / ".eval-biweekly-last-run.json")


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
    r = run_eval_scheduled(
        runner=_runner({"overall_score": 98, "bvt": {"green": True, "total": 55, "passed": 54}},
                       {"overall_score": 97}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "healthy"
    assert spy.calls == []  # green + no drift → silent


def test_bvt_red_alerts(tmp_path):
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"overall_score": 98,
                        "bvt": {"green": False, "total": 55, "passed": 53, "failed": 2, "error": 0}},
                       {"overall_score": 98}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "regression"
    assert any("BVT RED" in r2 for r2 in r["reasons"])
    assert len(spy.calls) == 1 and spy.calls[0]["channels"] == ["slack"]


def test_score_drift_alerts(tmp_path):
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"overall_score": 80, "bvt": {"green": True, "total": 55, "passed": 55}},
                       {"overall_score": 95}),  # 15-pt drop > tolerance
        notifier=spy, root=tmp_path)
    assert r["status"] == "regression"
    assert any("drift" in r2 for r2 in r["reasons"])
    assert len(spy.calls) == 1


def test_drift_within_tolerance_no_alert(tmp_path):
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"overall_score": 95 - _DRIFT_TOLERANCE + 0.1,
                        "bvt": {"green": True, "total": 55, "passed": 55}},
                       {"overall_score": 95}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "healthy"
    assert spy.calls == []


def test_dedup_no_repeat_alert(tmp_path, monkeypatch):
    """Same regression two nights running → only ONE alert (no storm)."""
    import jobs.handlers.eval_scheduled as mod
    monkeypatch.setattr(mod, "_ALERT_STATE", tmp_path / ".alert.json")
    spy = _Spy()
    red = _runner({"overall_score": 98,
                   "bvt": {"green": False, "total": 55, "passed": 53, "failed": 2, "error": 0}},
                  {"overall_score": 98})
    run_eval_scheduled(runner=red, notifier=spy, root=tmp_path)
    run_eval_scheduled(runner=red, notifier=spy, root=tmp_path)
    assert len(spy.calls) == 1  # second run deduped


def test_no_baseline_no_drift_alert(tmp_path):
    """First-ever run (no baseline) must not false-alert on drift."""
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"overall_score": 50, "bvt": {"green": True, "total": 55, "passed": 55}},
                       None),
        notifier=spy, root=tmp_path)
    assert r["status"] == "healthy"
    assert spy.calls == []


def test_judge_infra_collapse_alerts(tmp_path):
    """REPRO of 2026-06-28 silent judge-infra collapse: 90/146 LLM-judge cases
    errored on AWS creds, but the 56 deterministic cases still scored 100 and BVT
    canaries (deterministic) stayed green → no drift (score went UP vs baseline 95),
    no bvt_red → fingerprint stayed "" → Slack was NEVER alerted. Coverage collapse
    is the ONLY signal that distinguishes infra-failure from a healthy run.
    This test REDs without the coverage-collapse alert (it produces no reason)."""
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"overall_score": 100, "scored_count": 56, "cases_error": 90,
                        "bvt": {"green": True, "total": 55, "passed": 55}},
                       {"overall_score": 95}),  # score UP, not down → no drift
        notifier=spy, root=tmp_path)
    assert r["status"] == "regression"
    assert any("coverage" in r2.lower() or "infra" in r2.lower() for r2 in r["reasons"])
    assert len(spy.calls) == 1 and spy.calls[0]["channels"] == ["slack"]


def test_coverage_above_threshold_no_alert(tmp_path):
    """A few偶发 errors (below threshold) must NOT false-alarm. 2/146 errored =
    1.4% error ratio, far below the 20% trigger → coverage healthy, silent."""
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"overall_score": 96, "scored_count": 144, "cases_error": 2,
                        "bvt": {"green": True, "total": 55, "passed": 55}},
                       {"overall_score": 95}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "healthy"
    assert spy.calls == []


def test_coverage_reason_separate_from_drift(tmp_path):
    """A night that is BOTH drifted AND low-coverage must produce TWO distinct
    reasons (P6: infra-failure ≠ agent-quality-regression, never conflated)."""
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"overall_score": 70, "scored_count": 56, "cases_error": 90,
                        "bvt": {"green": True, "total": 55, "passed": 55}},
                       {"overall_score": 95}),  # 25-pt drop = drift AND low coverage
        notifier=spy, root=tmp_path)
    assert r["status"] == "regression"
    assert any("drift" in r2 for r2 in r["reasons"])
    assert any("coverage" in r2.lower() or "infra" in r2.lower() for r2 in r["reasons"])
    assert len(spy.calls) == 1  # combined into ONE notify (no double-send)


def test_fully_skipped_run_no_crash(tmp_path):
    """Gate-1 finding: a run with scored_count=0 AND cases_error=0 (fully skipped)
    must NOT ZeroDivisionError — coverage guards intended>0 (eval_service parity)."""
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"overall_score": 0, "scored_count": 0, "cases_error": 0,
                        "bvt": {"green": True, "total": 55, "passed": 55}},
                       {"overall_score": 0}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "healthy"  # no division crash, no false coverage alarm


def test_runner_crash_returns_error(tmp_path):
    def boom(root):
        raise RuntimeError("eval blew up")
    r = run_eval_scheduled(runner=boom, notifier=_Spy(), root=tmp_path)
    assert r["status"] == "error" and "eval blew up" in r["reason"]


def test_default_runner_imports_resolve():
    """REGRESSION GUARD (adversarial CRITICAL #3): the prior tests all injected a
    fake runner=, so the REAL _default_runner's imports were never exercised —
    a `load_history` vs `_load_history` typo shipped a dead-on-arrival scheduled job.
    This forces the real import path to resolve (without running the heavy eval)."""
    import jobs.handlers.eval_scheduled as mod
    import inspect
    src = inspect.getsource(mod._default_runner)
    # the names _default_runner imports must all exist in eval_runner
    import scripts.eval_runner as er
    for name in ("load_golden_set", "_golden_set_path", "run_eval", "write_run", "_load_history"):
        assert hasattr(er, name), f"eval_runner missing {name} — _default_runner import would crash"
    # and the buggy bare name must NOT be what we import
    assert "import load_history" not in src or "_load_history as load_history" in src


# ─── run_6980cb35: every-2-week guard (Gate-1 F fix — own timestamp, no deadlock) ──
class TestBiweeklyGuard:
    """_should_run_biweekly must NOT rely on JobState.last_run (a skip-result
    resets it → permanent deadlock, Gate-1 F). It reads its OWN timestamp file
    and is fail-open."""

    def test_no_prior_timestamp_runs(self):
        from jobs.handlers.eval_scheduled import _should_run_biweekly
        from datetime import datetime, timezone
        now = datetime(2026, 7, 6, tzinfo=timezone.utc)
        assert _should_run_biweekly(None, now) is True  # never run → run

    def test_recent_run_skips(self):
        from jobs.handlers.eval_scheduled import _should_run_biweekly
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 7, 6, tzinfo=timezone.utc)
        last = now - timedelta(days=7)  # only 1 week ago
        assert _should_run_biweekly(last, now) is False

    def test_stale_run_runs(self):
        from jobs.handlers.eval_scheduled import _should_run_biweekly
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 7, 6, tzinfo=timezone.utc)
        last = now - timedelta(days=15)  # >14d → run
        assert _should_run_biweekly(last, now) is True

    def test_exactly_14_days_runs(self):
        from jobs.handlers.eval_scheduled import _should_run_biweekly
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 7, 6, tzinfo=timezone.utc)
        assert _should_run_biweekly(now - timedelta(days=14), now) is True  # >=14 → run

    def test_unparseable_timestamp_fails_open(self):
        from jobs.handlers.eval_scheduled import _should_run_biweekly
        from datetime import datetime, timezone
        now = datetime(2026, 7, 6, tzinfo=timezone.utc)
        # a garbage stored value must RUN (fail-open — never silently never-run)
        assert _should_run_biweekly("not-a-date", now) is True

    def test_skip_does_not_run_eval(self, tmp_path, monkeypatch):
        """Integration (Gate-1 C+F): when the guard says skip, the heavy runner
        is NOT invoked AND the guard's own timestamp is NOT rewritten (so the
        clock isn't reset → no deadlock)."""
        import jobs.handlers.eval_scheduled as mod
        from datetime import datetime, timezone, timedelta
        ts_file = tmp_path / ".eval-biweekly-last-run.json"
        # seed a recent run (7d ago) → guard should skip
        recent = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        ts_file.write_text(f'{{"last_run": "{recent}"}}')
        monkeypatch.setattr(mod, "_BIWEEKLY_STATE", ts_file)
        called = {"ran": False}

        def _runner(root):
            called["ran"] = True
            return {"this": {"overall_score": 99, "bvt": {"green": True}}, "baseline": None}

        r = run_eval_scheduled(runner=_runner, notifier=_Spy(), root=tmp_path)
        assert called["ran"] is False, "guard must skip the heavy runner within 14d"
        assert r.get("status") == "skipped"
        # timestamp file unchanged (clock NOT reset) — the deadlock fix
        assert ts_file.read_text().find(recent[:10]) != -1


class TestBehaviorRedSegregation:
    """Gate-1 E + decision B: a behavior-only failure must NOT fire the
    deterministic hard-regression alarm; it's a distinct labeled line."""

    def _run_with_behavior_fail(self, tmp_path, spy):
        # run_result whose ONLY failure is a behavior-method case
        this = {
            "overall_score": 96, "scored_count": 55, "cases_error": 0,
            "bvt": {"green": True, "total": 55, "passed": 55},
            "cases": [
                {"id": "GS_NORM", "status": "passed", "eval_method": "programmatic"},
                {"id": "GS_TRAJ", "status": "failed", "eval_method": "behavior"},
            ],
        }
        return run_eval_scheduled(runner=_runner(this, {"overall_score": 96}),
                                  notifier=spy, root=tmp_path)

    def test_behavior_only_failure_not_hard_regression(self, tmp_path):
        spy = _Spy()
        r = self._run_with_behavior_fail(tmp_path, spy)
        # deterministic layer clean → NOT a hard regression
        assert r["status"] != "regression", "behavior-only failure must not be hard-regression"

    def test_behavior_failure_labeled_non_deterministic(self, tmp_path):
        spy = _Spy()
        r = self._run_with_behavior_fail(tmp_path, spy)
        assert "behavior" in r.get("behavior_note", "").lower(), \
            "behavior failure must be surfaced in a distinct labeled note"
        assert "GS_TRAJ" in r.get("behavior_failed", []), "failed behavior id must be listed"
        # and it must NOT be conflated into the hard-alert reasons[]
        assert not any("behavior" in x.lower() for x in r.get("reasons", [])), \
            "behavior note must NOT enter the deterministic hard-alert reasons"

    def test_deterministic_failure_still_hard_alerts(self, tmp_path):
        # a deterministic (programmatic/llm) failure still triggers the hard path.
        # det score = 0/1 passed = 0% vs baseline 95% → drift fires.
        this = {
            "overall_score": 80, "scored_count": 1, "cases_error": 0,
            "bvt": {"green": True, "total": 55, "passed": 55},
            "cases": [{"id": "GS_NORM", "status": "failed", "eval_method": "programmatic"}],
        }
        spy = _Spy()
        r = run_eval_scheduled(runner=_runner(this, {"overall_score": 95}),
                               notifier=spy, root=tmp_path)
        assert r["status"] == "regression"  # deterministic drift → hard alert intact

    def test_behavior_fail_does_not_trip_drift(self, tmp_path):
        # Gate-2 HIGH fix: behavior failures must NOT drag the drift score. All
        # deterministic cases pass (100%); only behavior fails. run-wide
        # overall_score is dragged to 60 but the DETERMINISTIC score is 100 →
        # NO drift alert (baseline 98).
        this = {
            "overall_score": 60,  # run-wide (behavior-dragged) — must NOT be used for drift
            "scored_count": 4, "cases_error": 0,
            "bvt": {"green": True, "total": 3, "passed": 3},
            "cases": [
                {"id": "P1", "status": "passed", "eval_method": "programmatic"},
                {"id": "P2", "status": "passed", "eval_method": "llm"},
                {"id": "P3", "status": "passed", "eval_method": "programmatic"},
                {"id": "B1", "status": "failed", "eval_method": "behavior"},
            ],
        }
        spy = _Spy()
        r = run_eval_scheduled(runner=_runner(this, {"overall_score": 98}),
                               notifier=spy, root=tmp_path)
        assert r["status"] != "regression", "behavior-dragged score must not fire drift"
        assert spy.calls == [], "no hard Slack alert from behavior non-determinism"
        assert "B1" in r["behavior_failed"]

    def test_behavior_errors_do_not_trip_coverage_collapse(self, tmp_path):
        # Gate-2 HIGH fix: behavior spawn-errors must NOT feed the coverage-collapse
        # alarm. 3 behavior cases error (flaky spawns) + 2 deterministic clean.
        # run-wide error ratio = 3/5 = 60% (>20% → would false-alarm); but
        # DETERMINISTIC error ratio = 0/2 = 0% → NO coverage alert.
        this = {
            "overall_score": 96, "scored_count": 2, "cases_error": 3,
            "bvt": {"green": True, "total": 2, "passed": 2},
            "cases": [
                {"id": "P1", "status": "passed", "eval_method": "programmatic"},
                {"id": "P2", "status": "passed", "eval_method": "llm"},
                {"id": "B1", "status": "error", "eval_method": "behavior"},
                {"id": "B2", "status": "error", "eval_method": "behavior"},
                {"id": "B3", "status": "error", "eval_method": "behavior"},
            ],
        }
        spy = _Spy()
        r = run_eval_scheduled(runner=_runner(this, {"overall_score": 96}),
                               notifier=spy, root=tmp_path)
        assert r["status"] != "regression", "behavior spawn-errors must not fire coverage collapse"
        assert not any("infra degraded" in x for x in r.get("reasons", []))
        assert {"B1", "B2", "B3"} <= set(r["behavior_failed"])

    def test_real_judge_infra_collapse_still_alerts(self, tmp_path):
        # Guard the fix didn't over-correct: a DETERMINISTIC coverage collapse
        # (llm cases erroring = real Bedrock/creds failure) still fires.
        this = {
            "overall_score": 100, "scored_count": 2, "cases_error": 8,
            "bvt": {"green": True, "total": 2, "passed": 2},
            "cases": [{"id": "P1", "status": "passed", "eval_method": "programmatic"}]
                     + [{"id": f"L{i}", "status": "error", "eval_method": "llm"} for i in range(8)]
                     + [{"id": "P2", "status": "passed", "eval_method": "programmatic"}],
        }
        spy = _Spy()
        r = run_eval_scheduled(runner=_runner(this, {"overall_score": 100}),
                               notifier=spy, root=tmp_path)
        assert r["status"] == "regression"
        assert any("infra degraded" in x for x in r["reasons"]), "real deterministic collapse must alert"
