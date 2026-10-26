"""/api/eval/health's lines (design 2026-10-26-eval-as-product §3.6, final test 8):
the next full run respects the 14-day gate, and every value comes from records."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)          # a Monday, after 10:30 UTC


def _svc(tmp_path, monkeypatch, cases=()):
    from core.eval_service import EvalService
    from tests._eval_golden_fixture import install_golden_fixture

    (tmp_path / "Eval" / "EvalHistory").mkdir(parents=True, exist_ok=True)
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": list(cases)})
    jobs = [SimpleNamespace(id="eval-scheduled", schedule="30 10 * * 1", enabled=True),
            SimpleNamespace(id="session-quality", schedule="0 8 * * 5", enabled=True)]
    monkeypatch.setattr("jobs.scheduler.load_jobs", lambda: jobs)
    return EvalService(workspace_root=tmp_path)


def _last_full(monkeypatch, when):
    monkeypatch.setattr("jobs.handlers.eval_scheduled._read_biweekly_last_run",
                        lambda: when.isoformat() if when else None)


def test_the_next_full_run_waits_for_the_two_week_gate(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    _last_full(monkeypatch, NOW - timedelta(days=0, hours=1))       # ran this Monday
    assert svc.get_lines(NOW)["full"]["next_run"] == "2026-11-09T10:30:00+00:00"   # skips 11-02
    _last_full(monkeypatch, NOW - timedelta(days=7, hours=1))       # ran last Monday
    assert svc.get_lines(NOW)["full"]["next_run"] == "2026-11-02T10:30:00+00:00"
    _last_full(monkeypatch, None)                                   # never ran: the next fire
    assert svc.get_lines(NOW)["full"]["next_run"] == "2026-11-02T10:30:00+00:00"


def test_online_next_run_and_a_disabled_job_has_none(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    assert svc.get_lines(NOW)["online"]["next_run"] == "2026-10-30T08:00:00+00:00"
    monkeypatch.setattr("jobs.scheduler.load_jobs", lambda: [
        SimpleNamespace(id="eval-scheduled", schedule="30 10 * * 1", enabled=False)])
    lines = svc.get_lines(NOW)
    assert lines["full"]["next_run"] is None and lines["online"]["next_run"] is None


def test_the_quick_line_reads_the_daily_canary_and_the_counts_the_golden_set(tmp_path, monkeypatch):
    from core.golden_set_store import PRODUCT_CAP, TOTAL_CAP

    svc = _svc(tmp_path, monkeypatch, cases=[{"id": "MINE", "evaluators": ["canary_pass"], "affected_by": []},
                                             {"id": "OLD", "tier": "archived", "evaluators": ["canary_pass"],
                                              "affected_by": []}])
    (tmp_path / ".context").mkdir(exist_ok=True)
    (tmp_path / ".context" / ".eval-canary-daily.json").write_text(
        json.dumps({"updated_at": "2026-10-26T09:00:00+00:00", "passed": 30, "total": 35}))
    lines = svc.get_lines(NOW)
    assert lines["quick"] == {"last_run": "2026-10-26T09:00:00+00:00", "passed": 30, "total": 35, "days": []}
    gs = lines["golden_set"]
    assert gs["private"] == 1 and gs["total"] == gs["product"] + 1
    assert (gs["product_cap"], gs["total_cap"]) == (PRODUCT_CAP, TOTAL_CAP)


def test_the_health_route_carries_the_lines(tmp_path, monkeypatch):
    import asyncio

    from routers import eval as eval_router

    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(eval_router, "get_eval_service", lambda: svc)
    assert set(asyncio.run(eval_router.get_eval_health())["lines"]) == {"quick", "full", "online", "golden_set"}
