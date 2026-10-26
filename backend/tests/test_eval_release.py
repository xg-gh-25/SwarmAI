"""Release decision (core/eval_release): tiers, five-dimension summary, the four
checks in order, and the run record written by eval_runner.run_eval.

Covers the run_85e81059 acceptance R1.1, R1.2 (reader side), R1.4, R2.2, R3.1–R3.6
and R4.1."""
from __future__ import annotations

import itertools

import pytest

from core.eval_release import (
    CHECK_ORDER, FIVE_DIMENSIONS, decide_release, failing_check, summarize_dimensions,
    tier_of,
)
from scripts.golden_case_validator import compute_case_stamp


def _stamped(c):
    c = dict(c)
    c["validated_by_4gate"] = compute_case_stamp(c)
    return c


_QUICK = {"tier": "stable", "eval_method": "programmatic", "evaluators": ["canary_pass"]}
RED = _stamped({"id": "R", "redline": True, "gating": "gate", "dimension": "responsibility", **_QUICK})
GATE = _stamped({"id": "G", "gating": "gate", "dimension": "cognition", **_QUICK})
WATCH = {"id": "W", "gating": "watch", "dimension": "quality", "eval_method": "behavior",
         "decision_rubric": "PASS only if …"}
CASES = [RED, GATE, WATCH]
OUTCOMES = ("passed", "failed", "error", "timeout", "skipped", None)  # None = no result


def _results(**status):
    return [{"id": k, "status": v} for k, v in status.items() if v is not None]


# ── tiers ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("case, tier", [
    ({"redline": True}, "red"),
    ({"redline": True, "gating": "watch"}, "red"),
    ({"redline": True, "tier": "draft"}, "watch"),                   # a draft never runs
    ({"redline": True, "tier": "archived"}, "watch"),
    ({"gating": "gate", **_QUICK}, "gate"),
    ({"gating": "gate", **dict(_QUICK, tier="active")}, "gate"),
    ({"gating": "watch", **_QUICK}, "watch"),
    (dict(_QUICK), "watch"),                                         # no gating: a new case
    ({"gating": "gate", **dict(_QUICK, eval_method="behavior")}, "watch"),   # model-judged
    ({"gating": "gate", **dict(_QUICK, eval_method="llm")}, "watch"),
    ({"gating": "gate", "decision_rubric": "x", **_QUICK}, "watch"),
    ({"gating": "gate", **dict(_QUICK, tier="draft")}, "watch"),    # same conditions as the BVT
    ({"gating": "gate", **dict(_QUICK, tier="live")}, "watch"),
    ({"gating": "gate", **dict(_QUICK, evaluators=["runtime_health"])}, "watch"),
    ({"gating": "bogus", **_QUICK}, "watch"),
])
def test_every_case_resolves_to_exactly_one_tier(case, tier):
    assert tier_of(case) == tier


def test_every_gate_case_is_a_bvt_member_and_back():
    """The release decision and the BVT count the same gate cases."""
    from scripts.eval_runner import bvt_member

    variants = [dict(_QUICK, tier=t, evaluators=e, gating=g)
                for t in ("stable", "active", "draft", "live", "archived")
                for e in (["canary_pass"], ["runtime_health"])
                for g in ("gate", "watch")]
    for v in variants:
        c = _stamped(dict(v, id="V"))
        assert (tier_of(c) == "gate") == bvt_member(c), v


def test_a_gate_case_edited_without_revalidation_is_not_evidence():
    stale = dict(GATE, verification={"command": "edited"})       # body changed, stamp not
    rel = decide_release([stale], _results(G="passed"))
    assert rel["verdict"] == "incomplete"
    assert rel["checks"][2]["cases"] == [{"id": "G", "status": "stale_stamp"}]


def test_a_run_with_no_red_or_gate_case_cannot_release():
    rel = decide_release([WATCH], _results(W="passed"))
    assert rel["verdict"] == "incomplete" and failing_check(rel)["id"] == "denominator"
    assert "no red-line or gate case" in rel["reason"]


def test_bvt_counts_only_red_and_gate_cases():
    from scripts.eval_runner import bvt_member
    from scripts.golden_case_validator import compute_case_stamp

    base = {"id": "Q", "tier": "stable", "eval_method": "programmatic", "evaluators": ["canary_pass"]}
    for gating, member in (("gate", True), ("watch", False), (None, False)):
        c = dict(base, **({"gating": gating} if gating else {}))
        c["validated_by_4gate"] = compute_case_stamp(c)
        assert bvt_member(c) is member, gating
    red = dict(base, redline=True)
    red["validated_by_4gate"] = compute_case_stamp(red)
    assert bvt_member(red) is True


# ── five dimensions ───────────────────────────────────────────────────────────

def test_dimension_summary_counts_every_case_once_by_tier_and_holds_no_percentage():
    cases = [RED, GATE, WATCH, _stamped({"id": "X", "gating": "gate", "dimension": "cognition", **_QUICK})]
    s = summarize_dimensions(cases, _results(R="passed", G="failed", W="timeout"))
    assert set(FIVE_DIMENSIONS) <= set(s)
    assert s["responsibility"]["red"] == {"passed": 1, "failed": 0, "not_run": 0}
    assert s["cognition"]["gate"] == {"passed": 0, "failed": 1, "not_run": 1}
    assert s["quality"]["watch"] == {"passed": 0, "failed": 0, "not_run": 1}
    total = sum(v for d in s.values() for t in d.values() for v in t.values())
    assert total == len(cases)
    flat = [v for d in s.values() for t in d.values() for v in t.values()]
    assert all(isinstance(v, int) for v in flat)            # counts, never a percentage


def test_a_case_outside_the_five_dimensions_is_not_dropped():
    s = summarize_dimensions([{"id": "O", "dimension": "capability"}], _results(O="passed"))
    assert s["other"]["watch"]["passed"] == 1


# ── the four checks ───────────────────────────────────────────────────────────

def _expected(r, g):
    if r in ("failed", "error", "timeout"):
        return "blocked", "redline"
    if g == "failed":
        return "blocked", "dimensions"
    if r in (None, "skipped") or g in (None, "error", "timeout", "skipped"):
        return "incomplete", "denominator"
    return "release", None


@pytest.mark.parametrize("r, g, w", list(itertools.product(OUTCOMES, repeat=3)))
def test_the_first_failing_check_decides_and_watch_never_does(r, g, w):
    rel = decide_release(CASES, _results(R=r, G=g, W=w))
    assert [c["id"] for c in rel["checks"]] == list(CHECK_ORDER)
    verdict, check = _expected(r, g)
    assert rel["verdict"] == verdict
    assert (failing_check(rel) or {}).get("id") == check
    record = rel["checks"][-1]
    assert record["passed"] is True and record["reason"] == rel["reason"] and rel["reason"]
    known = {k["id"] for k in record["known_issues"]}
    assert known == (set() if w == "passed" else {"W"})


def test_blocked_names_the_case_and_its_dimension():
    rel = decide_release(CASES, _results(R="passed", G="failed", W="passed"))
    assert rel["checks"][1]["cases"] == [{"id": "G", "dimension": "cognition"}]


def test_denominator_reports_selected_and_with_result():
    rel = decide_release(CASES, _results(R="passed", G="timeout", W="passed"))
    den = rel["checks"][2]
    assert (den["selected"], den["with_result"]) == (2, 1)
    assert den["cases"] == [{"id": "G", "status": "timeout"}]


def test_a_quick_run_does_not_count_unselected_cases_as_missing():
    """The cases passed in are the run's selection: an offline case a quick run left
    out is not in the denominator."""
    rel = decide_release([RED, GATE], _results(R="passed", G="passed"))
    assert rel["verdict"] == "release"


def test_failing_check_of_a_legacy_record_is_none():
    assert failing_check(None) is None
    assert failing_check({}) is None


# ── the run record ────────────────────────────────────────────────────────────

def test_run_record_has_release_and_dimension_summary_and_no_score(tmp_path, monkeypatch):
    from scripts import eval_runner as er

    cases = [_stamped(dict(GATE, id="G1", category="recall")),
             dict(WATCH, id="W1", category="judgment", tier="behavior")]
    status = {"G1": "passed", "W1": "failed"}
    monkeypatch.setattr(er, "evaluate_case",
                        lambda case, root, **kw: {"status": status[case["id"]], "duration_ms": 1})
    monkeypatch.setattr(er, "compute_code_digest", lambda root: "d")
    rec = er.run_eval({"cases": cases}, "manual", None, tmp_path, include_behavior=True)
    assert "overall_score" not in rec
    assert "dimensions" not in rec
    assert rec["release"]["verdict"] == "release"
    assert [k["id"] for k in rec["release"]["checks"][-1]["known_issues"]] == ["W1"]
    assert rec["dimension_summary"]["cognition"]["gate"]["passed"] == 1
    assert rec["dimension_summary"]["quality"]["watch"]["failed"] == 1


def test_history_loads_records_with_and_without_a_score(tmp_path):
    import json

    from scripts import eval_runner as er

    hist = er._eval_history_dir(tmp_path)
    hist.mkdir(parents=True, exist_ok=True)
    (hist / "a.json").write_text(json.dumps({"run_id": "old", "cases": [], "overall_score": 90}))
    (hist / "b.json").write_text(json.dumps({"run_id": "new", "cases": [], "release": {"verdict": "release"}}))
    (hist / "state.json").write_text(json.dumps({"something": 1}))
    assert sorted(r["run_id"] for r in er._load_history(tmp_path)) == ["new", "old"]
