"""Swarm's north star at hand (core.north_star, SWARMAI.md § My North Star): one
snapshot, computed from the cognition level, the CDF health and Needs You, written to a
file; the prompt reads its three-line headline from the file only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core import north_star as N

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def _week(week, not_corrected, sessions=10):
    return {"week": week, "sessions": sessions, "not_corrected_rate": not_corrected}


LEVEL = {
    "weeks": [_week("W41", 0.6), _week("W42", 0.6), _week("W43", 0.6), _week("W44", 0.3, 29)],
    "latest": {"repeat_rate": 0.08, "corrections": 122},
    "audit": {"month": "2026-10-30", "total": 10, "outcomes": {"missing": 6, "not delivered": 3, "stale": 1}},
    "used": {"used_rate": None, "mature_used": 0, "mature_written": 0, "window_days": 14},
    "agreement": {"corrected_total": 14, "corrected_agreed": 13, "clean_total": 0, "clean_agreed": 0},
}
OVERVIEW = {
    "value": {"rate": 0.83, "target": 0.9, "scored": 109, "destinations": [
        {"key": "GENERAL", "rate": 0.55, "target": 0.9, "scored": 20, "written": 84},
        {"key": "DDD", "rate": 0.95, "target": 0.9, "scored": 20, "written": 1000},
        {"key": "MEMORY", "rate": None, "target": 0.95, "scored": 0, "written": 6}]},
    "checks": [{"id": "C1", "status": "pass", "value": {}, "requirement": "0"},
               {"id": "C3", "status": "fail", "value": {"invalid": 74}, "requirement": "<= 5%"},
               {"id": "C10", "status": "no_data", "value": {}, "requirement": "> 0"}],
    "cost": {"cdf_usd": 725.3, "system_usd": 5314.9, "share": 0.136, "per_useful_usd": 0.58, "features": []},
    "reading": {"en": {"seen": ["General Brain is low."]}},
}
ATTENTION = {"items": [{"tier": "blocking", "source": "paused_run", "title": "A run was dropped",
                        "summary": "s"}], "counts": {"blocking": 1, "review": 0}}


@pytest.fixture
def parts(monkeypatch, tmp_path):
    import core.ingestion.cognition_level as cl
    import core.ingestion.health as health
    monkeypatch.setattr(cl, "level", lambda now=None, **k: LEVEL)
    monkeypatch.setattr(health, "overview", lambda window="7d", now=None: OVERVIEW)
    monkeypatch.setattr(N, "_attention", lambda: ATTENTION)
    monkeypatch.setattr(N, "path", lambda: tmp_path / "north_star.json")


def test_the_headline_says_the_state_and_its_direction(parts):
    snap = N.compute(NOW)
    cognition, cdf, needs = snap["headline"]
    # 70% corrected now against 40% before: fewer corrected sessions is better, so worse
    assert "you corrected me in 70%" in cognition and "worse than the 3 weeks before" in cognition
    assert "repeats 8% of 122" in cognition and "missing 60%" in cognition
    assert "useful 83% vs target 90%" in cdf and "GENERAL 55%" in cdf and "DDD" not in cdf
    assert "failing: C3 review output valid" in cdf and "checks 1/3 pass" in cdf
    assert needs.startswith("Needs You: 1 blocking, 0 to review") and "A run was dropped" in needs
    assert any("C10 knowledge is used: no_data" in line for line in snap["detail"])


def test_a_part_that_cannot_be_read_is_said_and_the_rest_stays(parts, monkeypatch):
    def broken():
        raise RuntimeError("db gone")
    monkeypatch.setattr(N, "_attention", broken)
    snap = N.compute(NOW)
    assert snap["headline"][2] == "needs_you: unreadable (RuntimeError)"
    assert snap["headline"][0].startswith("Cognition")


def test_the_prompt_reads_the_file_with_its_age(parts):
    assert N.headline_for_prompt(NOW) == ""                   # no snapshot yet
    N.refresh(NOW)
    text = N.headline_for_prompt(NOW + timedelta(minutes=30))
    lines = text.splitlines()
    assert "30 min ago" in lines[0] and "scripts.north_star" in lines[0]
    assert len(lines) == 4 and all(line.startswith("- ") for line in lines[1:])
    stored = json.loads(N.path().read_text())
    assert stored["at"] == NOW.isoformat(timespec="seconds")


def test_a_worker_hive_keeps_no_north_star(parts, monkeypatch):
    import core.app_role as role
    monkeypatch.setattr(role, "is_worker", lambda: True)
    assert N.refresh(NOW) == {} and not N.path().exists()


def test_the_lifecycle_sweep_refreshes_it(monkeypatch):
    """About every 30 minutes, so Needs You and the level stay current between passes."""
    import asyncio
    from unittest.mock import MagicMock
    from core.lifecycle_manager import LifecycleManager
    calls = []
    monkeypatch.setattr(N, "refresh", lambda *a, **k: calls.append(1) or {})
    asyncio.run(LifecycleManager._sweep_todos(MagicMock()))
    assert calls == [1]
