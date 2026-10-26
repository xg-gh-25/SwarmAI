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
