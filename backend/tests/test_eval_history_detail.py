"""Offline Eval release record (design 2026-10-26-eval-as-product §8 items 7–8):
each history row carries its four checks and the HTML report that belongs to it;
the quick-check line carries the daily canary's day-by-day record."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.eval_release import decide_release  # noqa: E402
from core.eval_service import EvalService  # noqa: E402
from tests._eval_golden_fixture import install_golden_fixture  # noqa: E402


def _run(ws, name, at, trigger, release):
    rec = {"run_id": name, "triggered_by": trigger, "triggered_at": at, "cases": [],
           "release": release}
    (ws / "Eval" / "EvalHistory" / f"{name}.json").write_text(json.dumps(rec))


def _local_day(iso):
    return datetime.fromisoformat(iso).astimezone().date().isoformat()


def test_history_rows_carry_their_checks_and_the_report_of_their_day(tmp_path, monkeypatch):
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []})
    hist = tmp_path / "Eval" / "EvalHistory"
    hist.mkdir(parents=True)
    from scripts.golden_case_validator import compute_case_stamp
    gate = {"id": "G", "gating": "gate", "tier": "stable", "evaluators": ["canary_pass"],
            "eval_method": "programmatic"}
    gate["validated_by_4gate"] = compute_case_stamp(gate)
    release = decide_release([gate], [{"id": "G", "status": "timeout"}])
    first, second = "2026-10-20T03:00:00+00:00", "2026-10-20T05:00:00+00:00"
    _run(tmp_path, "2026-10-20_030000_scheduled", first, "scheduled", release)
    _run(tmp_path, "2026-10-20_050000_scheduled", second, "scheduled", release)
    _run(tmp_path, "2026-10-21_050000_manual", "2026-10-21T05:00:00+00:00", "manual", release)
    (hist / f"{_local_day(second)}_scheduled.html").write_text("<html></html>")

    rows = EvalService(workspace_root=tmp_path).get_history()
    by_id = {r["run_id"]: r for r in rows}
    assert by_id["2026-10-20_050000_scheduled"]["report"] == f"{_local_day(second)}_scheduled.html"
    assert by_id["2026-10-20_030000_scheduled"]["report"] is None   # the later run owns it
    assert by_id["2026-10-21_050000_manual"]["report"] is None      # no report written
    checks = {c["id"]: c for c in by_id["2026-10-21_050000_manual"]["release_checks"]}
    assert list(checks) == ["redline", "dimensions", "denominator", "record"]
    assert checks["denominator"]["passed"] is False
    assert checks["denominator"]["cases"] == [{"id": "G", "status": "timeout"}]
    assert (checks["denominator"]["selected"], checks["denominator"]["with_result"]) == (1, 0)


def test_the_quick_line_carries_the_days(tmp_path, monkeypatch):
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []})
    (tmp_path / ".context").mkdir()
    days = [{"date": "2026-10-25", "passed": 35, "failed": 0, "timeout": 1, "total": 36,
             "not_run": 0, "verdict": "incomplete", "extra": "dropped"}]
    (tmp_path / ".context" / ".eval-canary-daily.json").write_text(json.dumps(
        {"updated_at": "2026-10-25T01:00:00+00:00", "passed": 35, "total": 36, "days": days}))
    svc = EvalService(workspace_root=tmp_path)
    monkeypatch.setattr(svc, "_next_full_run", lambda now: None)
    monkeypatch.setattr(svc, "_next_job_fire", lambda job, now: None)
    quick = svc.get_lines(now=datetime(2026, 10, 26, tzinfo=timezone.utc))["quick"]
    assert quick["days"] == [{**{k: v for k, v in days[0].items() if k != "extra"},
                              "error": None, "skipped": None}]


def test_list_rows_carry_the_release_tier_and_history_the_dimension_summary(tmp_path, monkeypatch):
    from scripts.golden_case_validator import compute_case_stamp
    gate = {"id": "G", "category": "recall", "dimension": "cognition", "gating": "gate",
            "tier": "stable", "evaluators": ["canary_pass"], "eval_method": "programmatic"}
    gate["validated_by_4gate"] = compute_case_stamp(gate)
    red = {**gate, "id": "R", "redline": True, "gating": "watch"}
    watch = {**gate, "id": "W", "gating": "watch"}
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []},
                           product_cases=[{**c, "_origin": "product"} for c in (gate, red, watch)])
    hist = tmp_path / "Eval" / "EvalHistory"
    hist.mkdir(parents=True)
    summary = {"cognition": {"gate": {"passed": 1, "failed": 0, "not_run": 0}}}
    (hist / "r.json").write_text(json.dumps({"run_id": "r", "triggered_at": "2026-10-20T03:00:00+00:00",
                                             "cases": [], "dimension_summary": summary}))
    svc = EvalService(workspace_root=tmp_path)
    tiers = {c["id"]: c["release_tier"] for c in svc.get_golden_set()["cases"]}
    assert tiers == {"G": "gate", "R": "red", "W": "watch"}
    assert svc.get_history()[0]["dimension_summary"] == summary


def test_the_newest_run_by_time_owns_the_report_whatever_the_file_name(tmp_path, monkeypatch):
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []})
    hist = tmp_path / "Eval" / "EvalHistory"
    hist.mkdir(parents=True)
    early, late = "2026-10-20T03:00:00+00:00", "2026-10-20T05:00:00+00:00"
    _run(tmp_path, "2026-10-20_scheduled", early, "scheduled", None)          # older name format
    _run(tmp_path, "2026-10-20_050000_scheduled", late, "scheduled", None)
    (hist / f"{_local_day(late)}_scheduled.html").write_text("<html></html>")
    by_id = {r["run_id"]: r for r in EvalService(workspace_root=tmp_path).get_history()}
    assert by_id["2026-10-20_050000_scheduled"]["report"] == f"{_local_day(late)}_scheduled.html"
    assert by_id["2026-10-20_scheduled"]["report"] is None


def test_the_latest_run_is_the_newest_by_time_not_by_file_name(tmp_path, monkeypatch):
    """The service names records in UTC, the CLI in local time: a canary that just ran
    (UTC 17:42) must count as newer than a scheduled run of 19:04 local (11:04 UTC)."""
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []})
    (tmp_path / "Eval" / "EvalHistory").mkdir(parents=True)
    _run(tmp_path, "2026-10-26_190458_scheduled", "2026-10-26T11:04:58+00:00", "scheduled", None)
    _run(tmp_path, "2026-10-26_174210_canary", "2026-10-26T17:42:10+00:00", "canary",
         {"verdict": "release", "reason": "ok", "checks": []})
    svc = EvalService(workspace_root=tmp_path)
    assert svc.get_health()["last_run"]["triggered_by"] == "canary"
    assert [r["run_id"] for r in svc.get_history()][0] == "2026-10-26_174210_canary"


def test_a_probe_run_never_stands_for_the_release(tmp_path, monkeypatch):
    """Re-running one case records a run whose denominator is that one case: health
    must keep the last quick check's verdict, and history marks each run's line."""
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []})
    hist = tmp_path / "Eval" / "EvalHistory"
    hist.mkdir(parents=True)
    _run(tmp_path, "2026-10-25_full", "2026-10-25T03:00:00+00:00", "scheduled",
         {"verdict": "release", "reason": "ok", "checks": []})
    full = json.loads((hist / "2026-10-25_full.json").read_text())
    full.update(total_cases=40, scored_count=40)
    (hist / "2026-10-25_full.json").write_text(json.dumps(full))
    _run(tmp_path, "2026-10-26_canary", "2026-10-26T01:00:00+00:00", "canary",
         {"verdict": "blocked", "reason": "red line", "checks": []})
    _run(tmp_path, "2026-10-26_manual", "2026-10-26T09:00:00+00:00", "manual",
         {"verdict": "release", "reason": "1/1", "checks": []})
    svc = EvalService(workspace_root=tmp_path)
    health = svc.get_health()
    assert health["release"]["verdict"] == "blocked"
    assert health["last_run"]["run_id"] == "2026-10-26_canary"
    assert {r["run_id"]: r["line"] for r in svc.get_history()} == {
        "2026-10-26_manual": "probe", "2026-10-26_canary": "quick", "2026-10-25_full": "full"}
    assert svc.get_lines()["full"]["release"] == "release"


def test_only_probe_runs_means_no_verdict_yet(tmp_path, monkeypatch):
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []})
    (tmp_path / "Eval" / "EvalHistory").mkdir(parents=True)
    _run(tmp_path, "2026-10-26_manual", "2026-10-26T09:00:00+00:00", "manual",
         {"verdict": "release", "reason": "1/1", "checks": []})
    health = EvalService(workspace_root=tmp_path).get_health()
    assert health["release"] is None and health["last_run"] is None


def test_a_full_run_where_many_cases_errored_still_decides(tmp_path, monkeypatch):
    """Scope makes a probe, never results: a 40-case run where 10 errored is not
    comparable on a trend, but its blocked verdict is the one health shows. A crashed
    full run is shown too; a crashed re-run of a few cases is a probe."""
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []})
    hist = tmp_path / "Eval" / "EvalHistory"
    hist.mkdir(parents=True)
    _run(tmp_path, "2026-10-25_canary", "2026-10-25T01:00:00+00:00", "canary",
         {"verdict": "release", "reason": "ok", "checks": []})
    _run(tmp_path, "2026-10-26_full", "2026-10-26T03:00:00+00:00", "manual",
         {"verdict": "incomplete", "reason": "10 errored", "checks": []})
    rec = json.loads((hist / "2026-10-26_full.json").read_text())
    rec.update(total_cases=40, scored_count=30, cases_error=10)
    (hist / "2026-10-26_full.json").write_text(json.dumps(rec))
    svc = EvalService(workspace_root=tmp_path)
    assert svc.get_health()["release"]["verdict"] == "incomplete"
    assert svc.get_lines()["full"]["release"] == "incomplete"

    from core.eval_followups import run_line
    assert run_line({"triggered_by": "manual", "status": "failed", "total_cases": 0, "filtered": False}) == "full"
    assert run_line({"triggered_by": "manual", "status": "failed", "total_cases": 0, "filtered": True}) == "probe"
    assert run_line({"triggered_by": "canary", "filtered": True, "total_cases": 36}) == "quick"
    assert run_line({"triggered_by": "manual", "filtered": True, "total_cases": 46}) == "probe"
    assert run_line({"triggered_by": "manual", "total_cases": 3}) == "probe"   # a record from before the flag
