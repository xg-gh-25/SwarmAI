"""Tests for the scheduled eval handler: it alerts on the release verdict
(core/eval_release) and on judge-infra coverage collapse, never on a score."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from jobs.handlers.eval_scheduled import run_eval_scheduled, summary_line  # noqa: E402
from core.eval_release import decide_release  # noqa: E402


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


def _runner(this):
    return lambda root: {"this": this}


from scripts.golden_case_validator import compute_case_stamp  # noqa: E402

_GATE = {"id": "G", "gating": "gate", "dimension": "cognition", "eval_method": "programmatic",
         "tier": "stable", "evaluators": ["canary_pass"]}
_GATE["validated_by_4gate"] = compute_case_stamp(_GATE)
_WATCH = {"id": "W", "gating": "watch", "dimension": "quality", "eval_method": "behavior"}


def _release(g="passed", w="passed"):
    res = [{"id": i, "status": s} for i, s in (("G", g), ("W", w)) if s is not None]
    return decide_release([_GATE, _WATCH], res)


OK = _release()


class _Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, **kw):
        self.calls.append(kw)
        return {"ok": True}


def test_clean_run_no_alert(tmp_path):
    spy = _Spy()
    r = run_eval_scheduled(runner=_runner({"release": OK}), notifier=spy, root=tmp_path)
    assert r["status"] == "healthy" and r["release"] == "release"
    assert spy.calls == []


def test_blocked_release_alerts_naming_the_check(tmp_path):
    spy = _Spy()
    r = run_eval_scheduled(runner=_runner({"release": _release(g="failed")}),
                           notifier=spy, root=tmp_path)
    assert r["status"] == "regression"
    assert any("blocked at check dimensions" in x and "G" in x for x in r["reasons"])
    assert len(spy.calls) == 1 and spy.calls[0]["channels"] == ["slack"]


def test_incomplete_release_alerts(tmp_path):
    spy = _Spy()
    r = run_eval_scheduled(runner=_runner({"release": _release(g="timeout")}),
                           notifier=spy, root=tmp_path)
    assert r["status"] == "regression"
    assert any("incomplete at check denominator" in x for x in r["reasons"])


def test_a_watch_failure_does_not_alert(tmp_path):
    """Offline cases are watch tier: a failure is a known issue, not an alert."""
    spy = _Spy()
    r = run_eval_scheduled(runner=_runner({"release": _release(w="failed")}),
                           notifier=spy, root=tmp_path)
    assert r["status"] == "healthy" and spy.calls == []


def test_no_score_comparison_between_runs(tmp_path):
    """A run record that still carries a score is not compared against anything:
    with a releasable verdict it is healthy whatever the number says."""
    spy = _Spy()
    r = run_eval_scheduled(runner=_runner({"release": OK, "overall_score": 10}),
                           notifier=spy, root=tmp_path)
    assert r["status"] == "healthy"
    assert not any("drift" in x or "score" in x for x in r["reasons"])
    assert "overall_score" not in r and "baseline_score" not in r


def test_a_record_without_a_release_block_alerts(tmp_path):
    r = run_eval_scheduled(runner=_runner({"overall_score": 99}), notifier=_Spy(), root=tmp_path)
    assert r["status"] == "regression"
    assert any("release decision missing" in x for x in r["reasons"])


def test_dedup_no_repeat_alert(tmp_path, monkeypatch):
    """Same regression two nights running → only ONE alert (no storm)."""
    import jobs.handlers.eval_scheduled as mod
    monkeypatch.setattr(mod, "_ALERT_STATE", tmp_path / ".alert.json")
    spy = _Spy()
    red = _runner({"release": _release(g="failed")})
    run_eval_scheduled(runner=red, notifier=spy, root=tmp_path)
    run_eval_scheduled(runner=red, notifier=spy, root=tmp_path)
    assert len(spy.calls) == 1  # second run deduped


def test_recovery_message_carries_no_score(tmp_path, monkeypatch):
    import jobs.handlers.eval_scheduled as mod
    monkeypatch.setattr(mod, "_ALERT_STATE", tmp_path / ".alert.json")
    monkeypatch.setattr(mod, "_should_run_biweekly", lambda last, now: True)
    spy = _Spy()
    run_eval_scheduled(runner=_runner({"release": _release(g="failed")}), notifier=spy, root=tmp_path)
    run_eval_scheduled(runner=_runner({"release": OK}), notifier=spy, root=tmp_path)
    assert len(spy.calls) == 2 and "recovered" in spy.calls[1]["message"]
    assert "%" not in spy.calls[1]["message"] and "score" not in spy.calls[1]["message"]


def test_summary_line_names_the_verdict_and_no_score():
    line = summary_line({"status": "regression", "release": "blocked", "reasons": ["release blocked at check redline: R"]})
    assert "release=blocked" in line and "redline" in line and "score" not in line


def test_judge_infra_collapse_alerts(tmp_path):
    """REPRO of 2026-06-28 silent judge-infra collapse: 90/146 LLM-judge cases
    errored on AWS creds, but the 56 deterministic cases still scored 100 and BVT
    canaries (deterministic) stayed green → no drift (score went UP vs baseline 95),
    no bvt_red → fingerprint stayed "" → Slack was NEVER alerted. Coverage collapse
    is the ONLY signal that distinguishes infra-failure from a healthy run.
    This test REDs without the coverage-collapse alert (it produces no reason)."""
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"release": OK, "scored_count": 56, "cases_error": 90}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "regression"
    assert any("coverage" in r2.lower() or "infra" in r2.lower() for r2 in r["reasons"])
    assert len(spy.calls) == 1 and spy.calls[0]["channels"] == ["slack"]


def test_coverage_above_threshold_no_alert(tmp_path):
    """A few偶发 errors (below threshold) must NOT false-alarm. 2/146 errored =
    1.4% error ratio, far below the 20% trigger → coverage healthy, silent."""
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"release": OK, "scored_count": 144, "cases_error": 2}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "healthy"
    assert spy.calls == []


def test_coverage_reason_separate_from_the_release_reason(tmp_path):
    """A night that is BOTH blocked AND low-coverage gives TWO distinct reasons
    (infra failure is not agent quality), sent as one notification."""
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"release": _release(g="failed"), "scored_count": 56, "cases_error": 90}),
        notifier=spy, root=tmp_path)
    assert r["status"] == "regression"
    assert any("release blocked" in x for x in r["reasons"])
    assert any("coverage" in x.lower() or "infra" in x.lower() for x in r["reasons"])
    assert len(spy.calls) == 1


def test_fully_skipped_run_no_crash(tmp_path):
    """Gate-1 finding: a run with scored_count=0 AND cases_error=0 (fully skipped)
    must NOT ZeroDivisionError — coverage guards intended>0 (eval_service parity)."""
    spy = _Spy()
    r = run_eval_scheduled(
        runner=_runner({"release": OK, "scored_count": 0, "cases_error": 0}),
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
    for name in ("load_merged_golden_set", "run_eval", "write_run", "_load_history"):
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

    def test_late_start_does_not_push_the_run_a_week(self):
        """The stamp is the time the job STARTED, which can be well after the cron
        fire (the scheduler is serial). Two weeks later the cron fires a little
        before that stamp turns 14 days old; that fire must still run, or the
        biweekly run slips to three weeks (observed: stamp 11:11 UTC, cron 10:30)."""
        from jobs.handlers.eval_scheduled import _should_run_biweekly
        from datetime import datetime, timezone, timedelta
        last = datetime(2026, 10, 12, 11, 11, tzinfo=timezone.utc)
        assert _should_run_biweekly(last, datetime(2026, 10, 26, 10, 30, tzinfo=timezone.utc)) is True
        # one week after a late start is still a skip
        assert _should_run_biweekly(last, datetime(2026, 10, 19, 10, 30, tzinfo=timezone.utc)) is False
        assert _should_run_biweekly(last, datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc)) is False

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
            return {"this": {"release": OK}}

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
            "release": _release(w="failed"), "scored_count": 55, "cases_error": 0,
            "cases": [
                {"id": "GS_NORM", "status": "passed", "eval_method": "programmatic"},
                {"id": "GS_TRAJ", "status": "failed", "eval_method": "behavior"},
            ],
        }
        return run_eval_scheduled(runner=_runner(this),
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
        # a gate (deterministic) failure blocks the release → hard alert intact
        this = {"release": _release(g="failed"), "scored_count": 1, "cases_error": 0,
                "cases": [{"id": "G", "status": "failed", "eval_method": "programmatic"}]}
        r = run_eval_scheduled(runner=_runner(this), notifier=_Spy(), root=tmp_path)
        assert r["status"] == "regression"

    def test_behavior_fail_does_not_block(self, tmp_path):
        # behavior cases are watch tier: their failure stays a known issue
        this = {
            "release": _release(w="failed"), "scored_count": 4, "cases_error": 0,
            "cases": [
                {"id": "P1", "status": "passed", "eval_method": "programmatic"},
                {"id": "P2", "status": "passed", "eval_method": "llm"},
                {"id": "P3", "status": "passed", "eval_method": "programmatic"},
                {"id": "B1", "status": "failed", "eval_method": "behavior"},
            ],
        }
        spy = _Spy()
        r = run_eval_scheduled(runner=_runner(this), notifier=spy, root=tmp_path)
        assert r["status"] != "regression"
        assert spy.calls == []
        assert "B1" in r["behavior_failed"]

    def test_behavior_errors_do_not_trip_coverage_collapse(self, tmp_path):
        # Gate-2 HIGH fix: behavior spawn-errors must NOT feed the coverage-collapse
        # alarm. 3 behavior cases error (flaky spawns) + 2 deterministic clean.
        # run-wide error ratio = 3/5 = 60% (>20% → would false-alarm); but
        # DETERMINISTIC error ratio = 0/2 = 0% → NO coverage alert.
        this = {
            "release": OK, "scored_count": 2, "cases_error": 3,
            "cases": [
                {"id": "P1", "status": "passed", "eval_method": "programmatic"},
                {"id": "P2", "status": "passed", "eval_method": "llm"},
                {"id": "B1", "status": "error", "eval_method": "behavior"},
                {"id": "B2", "status": "error", "eval_method": "behavior"},
                {"id": "B3", "status": "error", "eval_method": "behavior"},
            ],
        }
        spy = _Spy()
        r = run_eval_scheduled(runner=_runner(this),
                               notifier=spy, root=tmp_path)
        assert r["status"] != "regression", "behavior spawn-errors must not fire coverage collapse"
        assert not any("infra degraded" in x for x in r.get("reasons", []))
        assert {"B1", "B2", "B3"} <= set(r["behavior_failed"])

    def test_real_judge_infra_collapse_still_alerts(self, tmp_path):
        # Guard the fix didn't over-correct: a DETERMINISTIC coverage collapse
        # (llm cases erroring = real Bedrock/creds failure) still fires.
        this = {
            "release": OK, "scored_count": 2, "cases_error": 8,
            "cases": [{"id": "P1", "status": "passed", "eval_method": "programmatic"}]
                     + [{"id": f"L{i}", "status": "error", "eval_method": "llm"} for i in range(8)]
                     + [{"id": "P2", "status": "passed", "eval_method": "programmatic"}],
        }
        spy = _Spy()
        r = run_eval_scheduled(runner=_runner(this),
                               notifier=spy, root=tmp_path)
        assert r["status"] == "regression"
        assert any("infra degraded" in x for x in r["reasons"]), "real deterministic collapse must alert"


def test_default_runner_writes_the_html_report(tmp_path, monkeypatch):
    """The scheduled full run leaves a report for the Reports tab (design
    eval-as-product §3.4): before this, only hand-run evals wrote one."""
    import jobs.handlers.eval_scheduled as mod
    import scripts.eval_runner as er

    result = {"run_id": "r", "cases": []}
    written = []
    monkeypatch.setattr(er, "load_merged_golden_set", lambda root: {"cases": []})
    monkeypatch.setattr(er, "run_eval", lambda *a, **k: result)
    monkeypatch.setattr(er, "write_run", lambda r, root: None)
    monkeypatch.setattr(er, "generate_html_report", lambda r, gs, root: written.append(r))
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("no daemon")))
    out = mod._default_runner(tmp_path)
    assert out["this"] is result and written == [result]
