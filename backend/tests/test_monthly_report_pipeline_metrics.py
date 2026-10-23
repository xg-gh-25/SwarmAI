"""Monthly report pipeline metrics read Pipeline v1 runs (R6 review).

- lessons come from each completed run's ``artifacts/reflect.json`` (v1's REFLECT
  record), not from retired run.json stage records;
- the old "confidence" score is gone with the old pipeline; the report counts
  push-ready runs from each run's ``run_audit.json`` (``outcome.push_ready``).
"""
import json
from datetime import datetime, timezone

from jobs.handlers import swarmai_monthly_report as report


def _run(runs, rid, *, created, status="completed", lessons=0, push_ready=True):
    d = runs / rid
    (d / "artifacts").mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({
        "id": rid, "status": status, "profile": "full", "created_at": created,
        "stages": {"deliver": "completed", "reflect": "completed"}}))
    (d / "artifacts" / "reflect.json").write_text(json.dumps(
        {"lessons": [{"statement": f"lesson {i}"} for i in range(lessons)]}))
    (d / "run_audit.json").write_text(json.dumps({"outcome": {"push_ready": push_ready}}))


def test_v1_runs_give_lessons_and_push_ready_counts(tmp_path, monkeypatch):
    runs = tmp_path / "P" / ".artifacts" / "runs"
    _run(runs, "run_a", created="2026-10-02T00:00:00+00:00", lessons=2)
    _run(runs, "run_b", created="2026-10-03T00:00:00+00:00", lessons=1, push_ready=False)
    _run(runs, "run_c", created="2026-10-04T00:00:00+00:00", status="abandoned", lessons=5)
    _run(runs, "run_d", created="2026-09-04T00:00:00+00:00", lessons=7)   # last month
    monkeypatch.setattr(report, "PROJECTS_DIR", tmp_path)
    got = report._collect_pipeline_metrics(
        datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc), "P")
    assert got["runs_completed"] == 2
    assert got["total_lessons"] == 3
    assert got["push_ready"] == 1
    assert "avg_confidence" not in got
    assert got["profiles"] == {"full": 2}


def test_no_runs_dir_gives_zeroes(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "PROJECTS_DIR", tmp_path)
    got = report._collect_pipeline_metrics(
        datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 11, 1, tzinfo=timezone.utc), "P")
    assert got == {"runs_completed": 0, "push_ready": 0, "total_lessons": 0, "profiles": {}}
