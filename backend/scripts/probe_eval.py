#!/usr/bin/env python3
"""probe_eval — product eval probes for the eval system itself.

Contract: scripts/_probe_kit.py. Subcommands:
  drift_stamp  a case edited without being re-validated drops out of the BVT; an
               empty BVT, or one where every case was skipped, is red
  teeth        a gate-eligible case with no negative check is refused when added;
               at run time a negative that still prints the positive marker, or
               prints nothing, fails the case
  score_drift  a score drop beyond the tolerance raises an alert and a small dip does
               not; a failure is told apart as "the case is broken" or "a real
               regression"
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

from scripts._probe_kit import run, tempdir, verdict


def _case(cid="EVAL_PROBE_CASE", expected="PROBE_OK", negative=None, neg_expected=None):
    c = {"id": cid, "name": cid, "category": "probe", "prompt": "probe", "tier": "active",
         "eval_method": "programmatic", "evaluators": ["canary_pass"],
         "verification": {"command": "echo PROBE_OK", "expected_contains": expected}}
    if negative is not None:
        c["verification"]["negative_command"] = negative
    if neg_expected is not None:
        c["verification"]["negative_expected_contains"] = neg_expected
    return c


def _stamped(case):
    from scripts.golden_case_validator import compute_case_stamp

    case["validated_by_4gate"] = compute_case_stamp(case)
    return case


# ── drift_stamp ───────────────────────────────────────────────────────────────

def _drift_holds() -> tuple[bool, str]:
    from scripts.eval_runner import compute_bvt

    good = _stamped(_case("A"))
    edited = _stamped(_case("B"))
    edited["verification"]["expected_contains"] = "SOMETHING_ELSE"   # edited, not re-stamped
    passed = [{"id": "A", "status": "passed"}, {"id": "B", "status": "failed"}]
    bvt = compute_bvt([good, edited], passed)
    if bvt["total"] != 1 or not bvt["green"]:
        return False, f"an edited, un-revalidated case still counts in the BVT ({bvt})"
    if compute_bvt([], [])["green"]:
        return False, "an empty BVT is green"
    if compute_bvt([good], [{"id": "A", "status": "skipped"}])["green"]:
        return False, "a BVT where every case was skipped is green"
    return True, ""


def drift_stamp(negative: bool) -> int:
    def broken():  # every body gets the same stamp: edits are never noticed
        with mock.patch("scripts.golden_case_validator.compute_case_stamp", lambda c: "same"):
            return _drift_holds()
    return verdict("EVAL_DRIFT_STAMP", negative, _drift_holds, broken)


# ── teeth ─────────────────────────────────────────────────────────────────────

def _teeth_holds() -> tuple[bool, str]:
    from scripts import eval_runner as er
    from scripts.golden_case_validator import gate_teeth

    ok, _errs = gate_teeth(_case())
    if ok:
        return False, "a gate-eligible case with no negative check was admitted"
    with tempdir() as d:
        root = Path(d)
        vacuous = _case(negative="echo PROBE_OK", neg_expected="PROBE_TEETH")
        silent = _case(negative="true", neg_expected="PROBE_TEETH")
        sound = _case(negative="echo PROBE_TEETH", neg_expected="PROBE_TEETH")
        for label, c in (("a negative that prints the positive marker", vacuous),
                         ("a negative that prints nothing", silent)):
            got = er._verify_canary_teeth(c, "PROBE_OK", root, 30)
            if not got or got.get("status") != "failed":
                return False, f"{label} was accepted as teeth"
        if er._verify_canary_teeth(sound, "PROBE_OK", root, 30) is not None:
            return False, "a sound negative was rejected"
    return True, ""


def teeth(negative: bool) -> int:
    def broken():  # the run-time teeth check switched off
        with mock.patch("scripts.eval_runner._verify_canary_teeth", return_value=None):
            return _teeth_holds()
    return verdict("EVAL_TEETH", negative, _teeth_holds, broken)


# ── score_drift ───────────────────────────────────────────────────────────────

def _scheduled(base: float, now: float) -> dict:
    from jobs.handlers import eval_scheduled as es

    cases = [{"id": f"c{i}", "eval_method": "programmatic",
              "status": "passed" if i < now else "failed"} for i in range(100)]
    out = {"this": {"bvt": {"green": True}, "cases": cases},
           "baseline": {"overall_score": base}}
    with mock.patch.object(es, "_should_run_biweekly", return_value=True), \
            mock.patch.object(es, "_write_biweekly_last_run"):
        return es.run_eval_scheduled(dry_run=True, runner=lambda root: out, root=Path("."))


def _score_holds() -> tuple[bool, str]:
    from scripts.eval_runner import _classify_failure

    drop = _scheduled(90, 80)
    if drop["status"] != "regression" or not any("drift" in r for r in drop["reasons"]):
        return False, f"a 10-point drop raised no alert ({drop['reasons']})"
    dip = _scheduled(90, 87)
    if dip["status"] != "healthy":
        return False, f"a 3-point dip raised an alert ({dip['reasons']})"
    if _classify_failure("the cited rule is not present in the loaded context") != "case-broken":
        return False, "a missing-reference failure was not marked as a broken case"
    if _classify_failure("the answer handed the decision back to the user") != "regressed":
        return False, "a behaviour failure was not marked as a regression"
    return True, ""


def score_drift(negative: bool) -> int:
    def broken():  # a tolerance so wide nothing ever alerts
        with mock.patch("jobs.handlers.eval_scheduled._DRIFT_TOLERANCE", 1000.0):
            return _score_holds()
    return verdict("EVAL_SCORE_DRIFT", negative, _score_holds, broken)


if __name__ == "__main__":
    raise SystemExit(run({"drift_stamp": drift_stamp, "teeth": teeth,
                          "score_drift": score_drift}))
