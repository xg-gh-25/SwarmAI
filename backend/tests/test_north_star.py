"""Swarm's north star at hand (core.north_star, SWARMAI.md § My North Star): one
snapshot, computed from the cognition level, the CDF health and Needs You, written to a
file; the prompt reads its three-line headline from the file only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import north_star as N

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


def _week(week, not_corrected, sessions=10):
    return {"week": week, "sessions": sessions, "not_corrected_rate": not_corrected}


def _trend(now, before, direction, margin=11, low=0.18, high=0.40):
    return {"weeks": 4, "now": {"from": "W41", "to": "W44", "judged": 140, "corrected": round(now * 140), "rate": now},
            "before": {"from": "W37", "to": "W40", "judged": 150, "corrected": round(before * 150), "rate": before},
            "difference": round(now - before, 4), "low": low, "high": high, "margin": margin,
            "direction": direction, "min_judged": 10}


LEVEL = {
    "weeks": [_week("W41", 0.6), _week("W42", 0.6), _week("W43", 0.6), _week("W44", 0.3, 29)],
    "trend": _trend(0.7, 0.4, "worse"),
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
    monkeypatch.setattr(N, "_accepted", lambda now: [])
    monkeypatch.setattr(N, "path", lambda: tmp_path / "north_star.json")


def test_the_headline_says_the_state_and_its_direction(parts):
    snap = N.compute(NOW)
    cognition, cdf, needs = snap["headline"]
    # the backend's trend: 70% corrected over 4 weeks against 40% before, interval above 0
    assert cognition.startswith("Cognition (4 weeks to W44, 140 sessions judged): you corrected me in 70%")
    assert "40% in the 4 weeks before: worse;" in cognition
    assert "repeats 8% of 122" in cognition and "missing 60%" in cognition
    assert "useful 83% vs target 90%" in cdf and "GENERAL 55%" in cdf and "DDD" not in cdf
    assert "failing: C3 review output valid" in cdf and "checks 1/3 pass" in cdf
    assert needs.startswith("Needs You: 1 for you") and "A run was dropped" in needs
    assert any("C10 knowledge is used: no_data" in line for line in snap["detail"])


def test_the_measures_carry_the_same_readings_as_numbers(parts):
    """The C&M rail reads numbers, not the headline's words (measures_for_ui)."""
    m = N.compute(NOW)["measures"]
    cog, cdf = m["cognition"], m["cdf"]
    assert cog["week"] == "W44" and cog["sessions"] == 140 and cog["corrected_rate"] == 0.7
    assert cog["window_weeks"] == 4 and cog["before_rate"] == 0.4 and cog["direction"] == "worse"
    assert (cog["margin"], cog["low"], cog["high"]) == (11, 0.18, 0.40)
    assert cog["repeat_rate"] == 0.08 and cog["corrections"] == 122
    assert cdf["useful_rate"] == 0.83 and cdf["target"] == 0.9
    assert cdf["below_target"] == ["GENERAL"] and cdf["checks_failing"] == ["C3"]
    assert cdf["checks_total"] == 3 and "needs_you" not in m


WORDING = json.loads((Path(__file__).parent / "fixtures" / "north_star_trend_wording.json").read_text())


@pytest.mark.parametrize("case", WORDING["cases"], ids=[c["name"] for c in WORDING["cases"]])
def test_the_headline_says_the_shared_verdict(parts, monkeypatch, case):
    """The headline words the backend's one trend with the verdict the UI also shows
    (CognitionLevel.test.tsx reads the same cases): better / worse only outside the 95%
    interval, otherwise no visible change within ±the interval; no band of its own."""
    from core.ingestion import cognition_level as cl
    weeks = [{"week": f"2026-W{39 + i}", **w} for i, w in enumerate([case["before_week"]] * 4 + [case["now_week"]] * 4)]
    trend = cl.trend(weeks)
    assert trend == case["trend"]
    assert N.trend_verdict(trend) == case["verdict"]
    monkeypatch.setattr(cl, "level", lambda now=None, **k: {**LEVEL, "trend": trend})
    cognition = N.compute(NOW)["headline"][0]
    assert f": {case['verdict']};" in cognition
    assert N.compute(NOW)["measures"]["cognition"]["direction"] == (
        trend["direction"] if trend["now"]["rate"] is not None else None)


def test_without_a_trend_the_headline_says_too_few(parts, monkeypatch):
    """No trend yet (fewer than eight weeks): the latest week, and no direction claimed."""
    import core.ingestion.cognition_level as cl
    monkeypatch.setattr(cl, "level", lambda now=None, **k: {**LEVEL, "trend": None})
    snap = N.compute(NOW)
    assert snap["headline"][0].startswith("Cognition (W44, 29 sessions): you corrected me in 70% of them; "
                                          "too few sessions for a trend;")
    assert snap["measures"]["cognition"]["direction"] is None


def test_the_overlay_reads_the_measures_from_the_file(parts):
    assert N.measures_for_ui() is None                         # no snapshot yet
    N.refresh(NOW)
    ui = N.measures_for_ui()
    assert ui["at"] == NOW.isoformat(timespec="seconds") and ui["measures"]["cdf"]["target"] == 0.9
    N.path().write_text(json.dumps({"at": "2026-10-01T00:00:00+00:00", "headline": []}))
    assert N.measures_for_ui()["measures"] == {}               # an older snapshot: none yet


def test_a_part_that_cannot_be_read_is_said_and_the_rest_stays(parts, monkeypatch):
    def broken():
        raise RuntimeError("db gone")
    monkeypatch.setattr(N, "_attention", broken)
    snap = N.compute(NOW)
    assert snap["headline"][2] == "needs_you: unreadable (RuntimeError)"
    assert snap["headline"][0].startswith("Cognition")
    monkeypatch.setattr(N, "_cdf", lambda overview: (_ for _ in ()).throw(RuntimeError("x")))
    assert N.compute(NOW)["measures"]["cdf"] is None             # unreadable: no numbers


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


def test_the_cdf_line_carries_the_every_turn_file_sizes(parts, monkeypatch):
    """MEMORY and EVOLUTION are injected whole with no budget; the agreed answer to a
    clear rise is that the agent consolidates by declaration (design 2026-10-30
    cdf-agent-decides §4). The agent can only do that if it sees the size every turn
    (design 2026-11-02-knowledge-reaches-judgment ⑤)."""
    from core import prompt_metrics
    rows = [{"day": "2026-10-24", "files": {"Evolution Registry": {"last": 18078}, "Memory": {"last": 26545}}},
            {"day": "2026-10-28", "files": {"Evolution Registry": {"last": 22958}}},
            {"day": "2026-10-31", "files": {"Evolution Registry": {"last": 24272}, "Memory": {"last": 17917}}}]
    monkeypatch.setattr(prompt_metrics, "daily", lambda days=8, today=None: list(rows))
    snap = N.compute(NOW)
    cdf = snap["headline"][1]
    assert "every-turn files: EVOLUTION 24.3K tokens (18.1K 7 days ago), MEMORY 17.9K (26.5K)" in cdf
    assert snap["measures"]["cdf"]["every_turn"] == {"EVOLUTION": [24272, 18078], "MEMORY": [17917, 26545]}
    # the days between the two readings, so the overlay calls the older one "7 days ago" only when it is
    assert snap["measures"]["cdf"]["every_turn_days"] == {"EVOLUTION": 7, "MEMORY": 7}
    assert len(snap["headline"]) == 3


def test_no_size_record_leaves_the_cdf_line_as_it_was(parts, monkeypatch):
    from core import prompt_metrics
    monkeypatch.setattr(prompt_metrics, "daily", lambda days=8, today=None: [])
    cdf = N.compute(NOW)["headline"][1]
    assert "every-turn files" not in cdf and cdf.startswith("CDF (7 days)")


def test_one_day_of_sizes_shows_no_earlier_value(parts, monkeypatch):
    from core import prompt_metrics
    rows = [{"day": "2026-10-31", "files": {"Evolution Registry": {"last": 24272}}}]
    monkeypatch.setattr(prompt_metrics, "daily", lambda days=8, today=None: list(rows))
    snap = N.compute(NOW)
    cdf = snap["headline"][1]
    assert "every-turn files: EVOLUTION 24.3K tokens." in cdf and "days ago" not in cdf
    assert snap["measures"]["cdf"]["every_turn_days"] == {"EVOLUTION": 0}


def test_the_detail_says_whether_an_accepted_rule_stopped_its_mistake(parts, monkeypatch):
    monkeypatch.setattr(N, "_accepted", lambda now: [
        {"gc_id": "GC9", "title": "Read the API first", "accepted_on": "2026-11-05", "measured": True,
         "days": 10, "before": 2, "after": 0},
        {"gc_id": "GC10", "title": "Old rule", "accepted_on": "2026-11-01", "measured": False,
         "reason": "no correction anchors in Evidence"}])
    detail = N.compute(NOW)["detail"]
    assert "  GC9 accepted 2026-11-05 (10 days): 2 before, 0 after — Read the API first" in detail
    assert any(line.startswith("  GC10 accepted 2026-11-01: not measured (no correction anchors") for line in detail)
    assert len(N.compute(NOW)["headline"]) == 3                   # detail only


def test_needs_you_counts_only_what_waits_on_the_person():
    """Items Swarm handles itself never read as the person's to do."""
    att = {"items": [
        {"tier": "blocking", "source": "job", "title": "A job keeps failing", "handler": "agent"},
        {"tier": "review", "source": "job_digest", "title": "A digest", "handler": "agent"},
    ]}
    line, detail, _ = N._needs_you(att)
    assert line == "Needs You: nothing for you; Swarm handles 2 itself."
    assert all("Swarm handles it" in d for d in detail[1:])
    att["items"].append({"tier": "review", "source": "governance", "title": "A rule", "handler": "person"})
    line, _, _ = N._needs_you(att)
    assert line.startswith("Needs You: 1 for you — [review] A rule") and line.endswith("Swarm handles 2 itself.")
