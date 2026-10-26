"""A check that does not finish is ``timeout``: not scored, still red in the BVT and
for a red-line, and listed on its own in the scheduled alert (design
2026-10-26-eval-as-product §3.4)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import eval_runner as er  # noqa: E402
from scripts.golden_case_validator import compute_case_stamp  # noqa: E402


def _hang(*a, **k):
    raise subprocess.TimeoutExpired("probe", 20)


def _case(cid="C", **v):
    c = {"id": cid, "category": "recall", "dimension": "capability", "eval_method": "programmatic",
         "evaluators": ["canary_pass"], "tier": "stable", "gating": "gate", "affected_by": [],
         "verification": {"command": "probe", "expected_contains": "OK", **v}}
    c["validated_by_4gate"] = compute_case_stamp(c)
    return c


def test_each_command_evaluator_reports_a_timeout_as_timeout(tmp_path):
    with mock.patch.object(er, "_run_case_command", _hang):
        assert er.eval_canary_pass(_case(), tmp_path)["status"] == "timeout"
        rth = dict(_case(), evaluators=["runtime_health"])
        assert er.eval_runtime_health(rth, tmp_path)["status"] == "timeout"
        neg = _case(negative_command="probe negative", negative_expected_contains="TEETH")
        assert er._verify_canary_teeth(neg, "OK", tmp_path, 20)["status"] == "timeout"


def test_a_timeout_is_not_scored_but_keeps_the_bvt_and_a_red_line_red():
    a, b = _case("A"), dict(_case("B"), redline=True)
    b["validated_by_4gate"] = compute_case_stamp(b)
    results = [{"id": "A", "status": "passed"}, {"id": "B", "status": "timeout"}]
    assert er.compute_scores([a, b], results)["scored_count"] == 1   # the timeout is not definite
    bvt = er.compute_bvt([a, b], results)
    assert bvt["timeout"] == 1 and bvt["green"] is False
    assert er.compute_redline([a, b], results)["violated"] is True


def test_the_scheduled_alert_lists_timeouts_apart_from_judge_infra():
    from jobs.handlers import eval_scheduled as es

    cases = ([{"id": f"p{i}", "eval_method": "programmatic", "status": "passed"} for i in range(8)]
             + [{"id": "slow1", "eval_method": "programmatic", "status": "timeout"},
                {"id": "slow2", "eval_method": "programmatic", "status": "timeout"}])
    out = {"this": {"bvt": {"green": False, "passed": 8, "total": 10, "timeout": 2}, "cases": cases,
                    "release": {"verdict": "release", "reason": "", "checks": []}}}
    with mock.patch.object(es, "_should_run_biweekly", return_value=True), \
            mock.patch.object(es, "_write_biweekly_last_run"):
        got = es.run_eval_scheduled(dry_run=True, runner=lambda root: out, root=Path("."))
    assert "slow1" in got["timeout_note"]
    assert not any("timed out" in r for r in got["reasons"]), got["reasons"]   # not in the fingerprint
    assert not any("judge infra" in r for r in got["reasons"]), got["reasons"]
    assert got["reasons"] == [], got["reasons"]
