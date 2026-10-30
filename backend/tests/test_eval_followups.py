"""Each follow-up rule fires on its case and stays quiet otherwise; a clean state
gives an empty list (design 2026-10-26-eval-as-product §3.4, final test 6)."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.eval_followups import build_follow_ups  # noqa: E402
from core.golden_set_store import PRODUCT_ORIGIN, TOTAL_CAP  # noqa: E402

from scripts.golden_case_validator import compute_case_stamp  # noqa: E402

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def _stamped(c):
    c["validated_by_4gate"] = compute_case_stamp(c)
    return c


CASES = [_stamped({"id": "P1", "tier": "stable", "gating": "gate", "_origin": PRODUCT_ORIGIN, "evaluators": ["canary_pass"],
                   "eval_method": "programmatic",
                   "purpose": {"en": "Recall finds memory", "zh": "能找到记忆"}}),
         {"id": "B1", "tier": "behavior", "_origin": PRODUCT_ORIGIN, "eval_method": "behavior",
          "evaluators": ["trajectory_capture"],
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


def test_rule3_a_new_offline_case_failing_its_first_run_is_listed():
    h = [_run("scheduled", 1, {"P1": "passed", "B1": "failed"}),
         _run("scheduled", 15, {"P1": "passed"}),                 # B1 did not exist yet
         _run("canary", 0, {"P1": "passed"}, full=False)]
    h.sort(key=lambda r: r["triggered_at"], reverse=True)
    items = build_follow_ups(h, CASES, ONLINE, NOW)
    assert _rules(items) == [3] and items[0]["case_ids"] == ["B1"]
    assert "first run" in items[0]["what"]["en"] and "Corrects a wrong premise" in items[0]["what"]["en"]
    only = build_follow_ups([r for r in h if r["triggered_at"] != h[2]["triggered_at"]], CASES, ONLINE, NOW)  # first full run
    assert [i["case_ids"] for i in only if i["rule"] == 3] == [["B1"]]


def test_rule4_online_worse_low_and_sampler_error():
    worse = dict(ONLINE, checks={"O1_LANGUAGE": {"rate": 0.3, "examples": [{"title": "Chat A"}]}},
                 trend=[{"week": "2026-W42", "rates": {"O1_LANGUAGE": 0.1}},
                        {"week": "2026-W43", "rates": {"O1_LANGUAGE": 0.3}}])
    items = build_follow_ups(_clean_history(), CASES, worse, NOW)
    assert _rules(items) == [4] and "Chat A" in items[0]["why"]
    better = dict(worse, trend=[{"week": "2026-W42", "rates": {"O1_LANGUAGE": 0.5}},
                                {"week": "2026-W43", "rates": {"O1_LANGUAGE": 0.3}}])
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
    assert health["follow_ups"] == [i for i in items if i["kind"] == "eval"]


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



def test_rule1_only_bvt_members_and_private_ones_are_not_called_product():
    h = _clean_history()
    h[0] = _run("canary", 0, {"P1": "failed", "D1": "failed", "M1": "failed", "W1": "failed"}, full=False)
    cases = CASES + [_stamped({"id": "D1", "tier": "draft", "gating": "gate", "evaluators": ["canary_pass"]}),
                     _stamped({"id": "M1", "tier": "active", "gating": "gate", "evaluators": ["canary_pass"],
                               "title": "my own check"}),
                     _stamped({"id": "W1", "tier": "active", "evaluators": ["canary_pass"]})]   # no gating: watch
    items = [i for i in build_follow_ups(h, cases, ONLINE, NOW) if i["rule"] == 1]
    # the draft and the watch case are not in the BVT
    assert sorted(i["case_ids"][0] for i in items) == ["M1", "P1"]
    mine = next(i for i in items if i["case_ids"] == ["M1"])
    assert mine["what"]["en"].startswith("A quick check failed")


def test_the_daily_canary_counts_as_a_quick_check():
    old = [_run("scheduled", 3, {"P1": "passed", "B1": "passed"})]
    daily = {"updated_at": NOW.isoformat(), "results": {"P1": {"status": "failed", "notes": "boom"}}}
    items = build_follow_ups(old, CASES, ONLINE, NOW, daily_canary=daily)
    assert _rules(items) == [1] and items[0]["why"] == "boom"   # and it is not "quick check never ran"


def test_online_fields_are_bounded_and_snippets_never_leave():
    long = "T" * 300
    state = dict(ONLINE,
                 checks={"O1_LANGUAGE": {"rate": 0.4, "examples": [
                             {"title": long, "snippet": "SECRET_SNIPPET", "reason": "SECRET_REASON"}]},
                         "<script>": {"rate": 0.9}},
                 trend=[{"week": "2026-W42", "rates": {"O1_LANGUAGE": 0.1, "<script>": 0.0}},
                        {"week": "2026-W43", "rates": {}}],
                 low_details=[{"title": "Chat C", "snippet": "SECRET_SNIPPET", "reason": "SECRET_REASON"}],
                 sampler_error="OperationalError: /Users/someone/.swarm-ai/data.db is locked")
    items = build_follow_ups(_clean_history(), CASES, state, NOW)
    text = repr(items)
    assert "SECRET_SNIPPET" not in text and "SECRET_REASON" not in text
    assert "<script>" not in text and "/Users/" not in text
    assert "T" * 80 in text and "T" * 81 not in text
    on_disk = repr(build_follow_ups(_clean_history(), CASES, state, NOW, include_session_text=False))
    assert "Chat C" not in on_disk and "TTTT" not in on_disk


def test_a_filtered_or_programmatic_only_run_is_not_a_full_run():
    from scripts.eval_runner import _run_is_comparable

    base = {"total_cases": 40, "scored_count": 40}
    assert _run_is_comparable(base)
    assert not _run_is_comparable(dict(base, filtered=True))
    assert _run_is_comparable({"total_cases": 40, "scored_count": 30, "cases_timeout": 10})


def test_the_online_state_file_is_private_in_every_workspace():
    from core.swarm_workspace_manager import _PRIVACY_GITIGNORE_RULES, GITIGNORE_CONTENT

    assert ".context/.session-quality-state.json" in GITIGNORE_CONTENT.splitlines()
    assert ".context/.session-quality-state.json" in _PRIVACY_GITIGNORE_RULES   # appended to old workspaces



def test_online_compares_only_with_the_week_right_before_and_reports_a_judge_failure():
    gap = dict(ONLINE, checks={"O1_LANGUAGE": {"rate": 0.3}},
               trend=[{"week": "2026-W40", "rates": {"O1_LANGUAGE": 0.1}}, {"week": "2026-W43", "rates": {}}])
    assert build_follow_ups(_clean_history(), CASES, gap, NOW) == []
    adjacent = dict(gap, trend=[{"week": "2026-W42", "rates": {"O1_LANGUAGE": 0.1}}, {"week": "2026-W43", "rates": {}}])
    assert _rules(build_follow_ups(_clean_history(), CASES, adjacent, NOW)) == [4]
    judged = dict(ONLINE, judge_error="10/10 judged sessions failed: ThrottlingException")
    assert "judge" in build_follow_ups(_clean_history(), CASES, judged, NOW)[0]["what"]["en"]


# ── kind: the evaluation itself vs the agent's behaviour (design §8 item 2) ──────

def _kinds(items):
    return {(i["rule"], i["kind"]) for i in items}


def test_every_item_has_a_kind_and_behaviour_signals_are_evolution():
    flipped = [_run("canary", 0, {"P1": "passed"}, full=False),
               _run("scheduled", 1, {"P1": "passed", "B1": "failed"}),
               _run("scheduled", 15, {"P1": "passed", "B1": "passed"})]
    assert _kinds(build_follow_ups(flipped, CASES, ONLINE, NOW)) == {(3, "evolution")}
    new = [_run("canary", 0, {"P1": "passed"}, full=False), _run("scheduled", 1, {"P1": "passed", "B1": "failed"}),
           _run("scheduled", 15, {"P1": "passed"})]
    assert _kinds(build_follow_ups(new, CASES, ONLINE, NOW)) == {(3, "eval")}
    broken = [_run("canary", 0, {"P1": "failed"}, full=False), _run("scheduled", 3, {"P1": "passed", "B1": "passed"})]
    assert _kinds(build_follow_ups(broken, CASES, ONLINE, NOW)) == {(1, "eval")}
    worse = dict(ONLINE, checks={"O1_LANGUAGE": {"rate": 0.3}},
                 trend=[{"week": "2026-W42", "rates": {"O1_LANGUAGE": 0.1}}, {"week": "2026-W43", "rates": {}}])
    assert _kinds(build_follow_ups(_clean_history(), CASES, worse, NOW)) == {(4, "evolution")}
    assert _kinds(build_follow_ups(_clean_history(), CASES, dict(ONLINE, low_details=[{"title": "x"}]), NOW)) \
        == {(4, "evolution")}
    assert _kinds(build_follow_ups(_clean_history(), CASES, dict(ONLINE, sampler_error="x"), NOW)) == {(4, "eval")}


def test_rule8_standing_online_signals():
    def state(**checks):
        return dict(ONLINE, checks=checks)
    corrected = state(O3_CORRECTIONS={"rate": 0.05, "checked": 40, "failed": 2, "examples": [{"title": "Chat D"}]})
    items = build_follow_ups(_clean_history(), CASES, corrected, NOW)
    assert _kinds(items) == {(8, "evolution")} and "2 of 40" in items[0]["what"]["en"] and "Chat D" in items[0]["why"]
    assert build_follow_ups(_clean_history(), CASES, state(O3_CORRECTIONS={"rate": 0.0, "checked": 40, "failed": 0}),
                            NOW) == []
    habit = state(O2_SILENT_TOOLS={"rate": 0.5, "checked": 200, "failed": 100})
    items = build_follow_ups(_clean_history(), CASES, habit, NOW)
    assert _kinds(items) == {(8, "evolution")} and "50%" in items[0]["what"]["zh"]
    # under the threshold: not a signal
    assert build_follow_ups(_clean_history(), CASES, state(O2_SILENT_TOOLS={"rate": 0.09, "checked": 100,
                                                                          "failed": 9}), NOW) == []
    # already listed as worse than last week: not twice
    worse = dict(habit, trend=[{"week": "2026-W42", "rates": {"O2_SILENT_TOOLS": 0.2}},
                               {"week": "2026-W43", "rates": {}}])
    assert [i["rule"] for i in build_follow_ups(_clean_history(), CASES, worse, NOW)] == [4]
    on_disk = build_follow_ups(_clean_history(), CASES, corrected, NOW, include_session_text=False)
    assert "Chat D" not in repr(on_disk)


def test_a_repeated_correction_is_a_follow_up():
    """D12 / I9: corrections the review declared as repeating an earlier one are named."""
    repeated = dict(ONLINE, repeat_corrections={"corrections": 5, "repeats": 2, "rate": 0.4})
    items = build_follow_ups(_clean_history(), CASES, repeated, NOW)
    assert _kinds(items) == {(8, "evolution")}
    assert "2 of this week's 5 corrections repeated" in items[0]["what"]["en"]
    for quiet in ({"corrections": 5, "repeats": 0, "rate": 0.0}, None):
        assert build_follow_ups(_clean_history(), CASES, dict(ONLINE, repeat_corrections=quiet), NOW) == []


def test_health_splits_the_two_lists(tmp_path, monkeypatch):
    import asyncio
    import json

    from core.eval_service import EvalService
    from routers import eval as eval_router
    from tests._eval_golden_fixture import install_golden_fixture

    (tmp_path / "Eval" / "EvalHistory").mkdir(parents=True)
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []})
    (tmp_path / ".context").mkdir()
    (tmp_path / ".context" / ".session-quality-state.json").write_text(json.dumps(
        {"last_run": NOW.isoformat(), "judge_error": "x", "low_details": [{"title": "Chat E"}]}))
    svc = EvalService(workspace_root=tmp_path)
    monkeypatch.setattr(eval_router, "get_eval_service", lambda: svc)
    health = asyncio.run(eval_router.get_eval_health())
    assert health["follow_ups"] and all(i["kind"] == "eval" for i in health["follow_ups"])
    assert [i["rule"] for i in health["evolution"]] == [4]
    assert len(health["follow_ups"]) + len(health["evolution"]) == len(svc.get_follow_ups())


def test_the_report_shows_evolution_in_its_own_section():
    from scripts.eval_runner import _render_follow_ups

    item = {"severity": "low", "rule": 4, "what": {"en": "Low <i>", "zh": "低"}, "why": "", "action": {"en": "a"}}
    html = _render_follow_ups([dict(item, kind="evolution")])
    assert "Evolution suggestion" in html and "Nothing needs handling" in html and "Low &lt;i&gt;" in html
    assert "Evolution suggestion" not in _render_follow_ups([dict(item, kind="eval")])


def test_a_follow_up_names_a_case_by_its_title_or_the_first_sentence_of_its_purpose():
    """Up to three cases share one follow-up line, so each is named short: the
    English title, else the first sentence (what it protects); the rest of the
    purpose stays on the case."""
    case = _stamped({"id": "P1", "tier": "stable", "gating": "gate", "_origin": PRODUCT_ORIGIN,
                     "evaluators": ["canary_pass"], "eval_method": "programmatic",
                     "title": "Recall finds memory",
                     "purpose": {"en": "Recall finds a memory deep in a section. If this breaks, memory is lost.",
                                 "zh": "召回能找到长段落末尾的记忆。坏了意味着记忆等于丢了。"}})
    h = _clean_history()
    h[0] = _run("canary", 0, {"P1": "failed"}, full=False)
    item = build_follow_ups(h, [case, CASES[1]], ONLINE, NOW)[0]
    assert item["what"]["en"].endswith(": Recall finds memory")
    assert item["what"]["zh"].endswith("：召回能找到长段落末尾的记忆。")
    untitled = dict(case, title=None)
    untitled["validated_by_4gate"] = compute_case_stamp(untitled)
    item = build_follow_ups(h, [untitled, CASES[1]], ONLINE, NOW)[0]
    assert item["what"]["en"].endswith(": Recall finds a memory deep in a section.")


def test_a_list_follow_up_has_a_headline_without_the_case_names():
    """The Overview row shows the names on their own line, so the headline carries
    the count only; ``what`` keeps the names for the report."""
    h = [_run("scheduled", 1, {"P1": "passed", "B1": "failed"}),
         _run("scheduled", 15, {"P1": "passed"}),
         _run("canary", 0, {"P1": "passed"}, full=False)]
    h.sort(key=lambda r: r["triggered_at"], reverse=True)
    item = [i for i in build_follow_ups(h, CASES, ONLINE, NOW) if i["rule"] == 3][0]
    assert item["headline"]["en"] == "1 new offline case fails on the first run"
    assert item["headline"]["zh"] == "1 条新离线考题第一次运行没通过"
    assert "Corrects a wrong premise" in item["what"]["en"]
    one = build_follow_ups(_clean_history(), CASES, ONLINE, NOW)
    assert all(i["headline"] == i["what"] for i in one if i["rule"] != 3)


def test_a_follow_up_line_says_one_case_or_n_cases_never_case_s():
    """The line is read by a person: ``1 case was`` / ``2 cases were``, not ``case(s)``."""
    from core.eval_followups import _not_measured, _offline_regressions
    one = _not_measured([_run("scheduled", 0, {"P1": "error", "B1": "passed"})], {})
    two = _not_measured([_run("scheduled", 0, {"P1": "error", "B1": "error"})], {})
    assert one[0]["what"]["en"].startswith("1 case was not measured")
    assert two[0]["what"]["en"].startswith("2 cases were not measured")
    flipped = _offline_regressions([_run("scheduled", 0, {"B1": "failed"}), _run("scheduled", 7, {"B1": "passed"})], {})
    assert flipped[0]["headline"]["en"] == "1 offline case passed last time and fails now"
    for item in one + two + flipped:
        assert "(s)" not in item["what"]["en"] and "(s)" not in item["headline"]["en"]

