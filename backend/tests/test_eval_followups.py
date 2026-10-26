"""Each follow-up rule fires on its case and stays quiet otherwise; a clean state
gives an empty list (design 2026-10-26-eval-as-product §3.4, final test 6)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.eval_followups import build_follow_ups  # noqa: E402
from core.golden_set_store import PRODUCT_ORIGIN, TOTAL_CAP  # noqa: E402

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
CASES = [{"id": "P1", "tier": "stable", "_origin": PRODUCT_ORIGIN,
          "purpose": {"en": "Recall finds memory", "zh": "能找到记忆"}},
         {"id": "B1", "tier": "behavior", "_origin": PRODUCT_ORIGIN,
          "purpose": {"en": "Corrects a wrong premise", "zh": "指出错误前提"}}]
ONLINE = {"last_run": (NOW - timedelta(days=1)).isoformat(), "checks": {}, "trend": [], "low_details": []}


def _run(trigger, days_ago, statuses, full=True, bvt=True):
    cases = [{"id": cid, "status": st, "eval_method": "behavior" if cid.startswith("B") else "programmatic",
              "notes": f"{cid} note\nmore"} for cid, st in statuses.items()]
    total = 40 if full else 40
    return {"triggered_by": trigger, "triggered_at": (NOW - timedelta(days=days_ago)).isoformat(),
            "total_cases": total, "scored_count": 40 if full else 10, "cases": cases,
            "bvt": {"total": 1, "green": True} if bvt else {}}


def _clean_history():
    return [_run("canary", 0, {"P1": "passed"}, full=False),
            _run("scheduled", 3, {"P1": "passed", "B1": "passed"})]


def _rules(items):
    return sorted({i["rule"] for i in items})


def test_a_clean_state_needs_nothing():
    assert build_follow_ups(_clean_history(), CASES, ONLINE, NOW) == []


def test_rule1_a_failed_quick_check_names_its_purpose():
    h = _clean_history()
    h[0] = _run("canary", 0, {"P1": "failed"}, full=False)
    items = build_follow_ups(h, CASES, ONLINE, NOW)
    assert _rules(items) == [1] and "Recall finds memory" in items[0]["what"]["en"]
    assert items[0]["why"] == "P1 note" and items[0]["severity"] == "high"
    # a manual one-case probe that failed is not a quick check
    probe = {"triggered_by": "manual", "triggered_at": NOW.isoformat(), "total_cases": 1,
             "scored_count": 1, "cases": [{"id": "P1", "status": "failed"}], "bvt": {"total": 1}}
    assert build_follow_ups([probe] + _clean_history(), CASES, ONLINE, NOW) == []


def test_rule2_errors_timeouts_and_stuck():
    h = _clean_history()
    h[0] = _run("canary", 0, {"P1": "timeout"}, full=False)
    once = build_follow_ups(h, CASES, ONLINE, NOW)
    assert _rules(once) == [2] and once[0]["severity"] == "medium"
    h.insert(1, _run("canary", 1, {"P1": "timeout"}, full=False))
    twice = build_follow_ups(h, CASES, ONLINE, NOW)
    assert _rules(twice) == [2] and twice[0]["severity"] == "high" and "stuck" in twice[0]["what"]["en"]
    h = _clean_history()
    h[0] = _run("canary", 0, {"P1": "error"}, full=False)
    assert "not measured" in build_follow_ups(h, CASES, ONLINE, NOW)[0]["what"]["en"]


def test_rule3_offline_pass_to_fail_only():
    h = [_run("scheduled", 1, {"P1": "passed", "B1": "failed"}),
         _run("scheduled", 15, {"P1": "passed", "B1": "passed"}),
         _run("canary", 0, {"P1": "passed"}, full=False)]
    h.sort(key=lambda r: r["triggered_at"], reverse=True)
    items = build_follow_ups(h, CASES, ONLINE, NOW)
    assert _rules(items) == [3] and items[0]["case_ids"] == ["B1"]
    h[2]["cases"][1]["status"] = "failed"          # failing both times is not a new regression
    assert 3 not in _rules(build_follow_ups(h, CASES, ONLINE, NOW))


def test_rule4_online_worse_low_and_sampler_error():
    worse = dict(ONLINE, checks={"O1_LANGUAGE": {"rate": 0.3, "examples": [{"title": "Chat A"}]}},
                 trend=[{"rates": {"O1_LANGUAGE": 0.1}}, {"rates": {"O1_LANGUAGE": 0.3}}])
    items = build_follow_ups(_clean_history(), CASES, worse, NOW)
    assert _rules(items) == [4] and "Chat A" in items[0]["why"]
    better = dict(worse, trend=[{"rates": {"O1_LANGUAGE": 0.5}}, {"rates": {"O1_LANGUAGE": 0.3}}])
    assert build_follow_ups(_clean_history(), CASES, better, NOW) == []
    assert _rules(build_follow_ups(_clean_history(), CASES, dict(ONLINE, sampler_error="db locked"), NOW)) == [4]
    low = dict(ONLINE, low_details=[{"title": "Chat B"}])
    assert "Chat B" in build_follow_ups(_clean_history(), CASES, low, NOW)[0]["why"]


def test_rule5_overdue_lines():
    old = [_run("canary", 3, {"P1": "passed"}, full=False), _run("scheduled", 16, {"P1": "passed"})]
    stale_online = dict(ONLINE, last_run=(NOW - timedelta(days=9)).isoformat())
    items = build_follow_ups(old, CASES, stale_online, NOW)
    assert _rules(items) == [5] and len(items) == 3
    assert len(build_follow_ups(_clean_history(), CASES, None, NOW)) == 1   # online never ran


def test_rule6_a_case_that_always_fails():
    h = [_run("scheduled", d, {"P1": "passed", "B1": "failed"}) for d in (1, 15, 29, 43)]
    h.insert(0, _run("canary", 0, {"P1": "passed"}, full=False))
    assert 6 in _rules(build_follow_ups(h, CASES, ONLINE, NOW))
    h[4]["cases"][1]["status"] = "passed"
    assert 6 not in _rules(build_follow_ups(h, CASES, ONLINE, NOW))


def test_rule7_limits_purpose_and_hidden_cases():
    many = CASES + [{"id": f"X{i}", "tier": "active"} for i in range(TOTAL_CAP)]
    assert _rules(build_follow_ups(_clean_history(), many, ONLINE, NOW)) == [7]
    archived = CASES + [{"id": f"X{i}", "tier": "archived"} for i in range(TOTAL_CAP)]
    assert build_follow_ups(_clean_history(), archived, ONLINE, NOW) == []
    no_purpose = CASES + [{"id": "P2", "tier": "stable", "_origin": PRODUCT_ORIGIN}]
    assert "P2" in build_follow_ups(_clean_history(), no_purpose, ONLINE, NOW)[0]["case_ids"]
    assert build_follow_ups(_clean_history(), CASES, ONLINE, NOW, shadowed=["GS_X"])[0]["case_ids"] == ["GS_X"]


def test_the_service_reads_the_online_state_and_the_health_route_carries_the_list(tmp_path, monkeypatch):
    import json

    from core.eval_service import EvalService
    from tests._eval_golden_fixture import install_golden_fixture

    (tmp_path / "Eval" / "EvalHistory").mkdir(parents=True)
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []})
    (tmp_path / ".context").mkdir()
    (tmp_path / ".context" / ".session-quality-state.json").write_text(
        json.dumps({"last_run": NOW.isoformat(), "sampler_error": "db locked"}))
    svc = EvalService(workspace_root=tmp_path)
    items = svc.get_follow_ups()
    assert any(i["rule"] == 4 and "db locked" in i["why"] for i in items), items

    import asyncio
    from routers import eval as eval_router
    monkeypatch.setattr(eval_router, "get_eval_service", lambda: svc)
    health = asyncio.run(eval_router.get_eval_health())
    assert health["follow_ups"] == items


def test_reports_are_marked_routine_only_for_scheduled_and_deploy(tmp_path, monkeypatch):
    import asyncio

    from core.initialization_manager import initialization_manager
    from routers import eval as eval_router

    d = tmp_path / "Eval" / "EvalHistory"
    d.mkdir(parents=True)
    for name in ("2026-10-24_scheduled.html", "2026-10-15_manual.html", "2026-07-07___lockprobe__.html"):
        (d / name).write_text("<html></html>")
    monkeypatch.setattr(initialization_manager, "get_cached_workspace_path", lambda: str(tmp_path))
    got = {r["filename"]: r["routine"] for r in asyncio.run(eval_router.list_reports())}
    assert got == {"2026-10-24_scheduled.html": True, "2026-10-15_manual.html": False,
                   "2026-07-07___lockprobe__.html": False}
