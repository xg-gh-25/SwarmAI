"""eval_release — decide whether a version can be released from one eval run.

Pure functions, no I/O. The single authority for three questions every reader of an
eval run asks (the run record, the push gate, the scheduled alert, the dashboard):

1. **Tier of a case** (``tier_of``): how much power its result has.
   - ``red``   — a red line (``redline: true``): any failure vetoes the release.
   - ``gate``  — must pass for the release (``gating: gate``).
   - ``watch`` — shown, never blocks (``gating: watch``, or no ``gating`` at all, so a
     new case starts here). A declared ``gate`` is read as watch when the case cannot
     gate (the validator refuses these on add; this is the reader's fail-safe):
     a model judges it (``decision_rubric``, or eval_method ``llm`` / ``behavior``:
     its judge is not calibrated against human verdicts), its tier is not active or
     stable (a draft is unfinished and always skipped; a live case depends on runtime
     state), or its evaluator is not one the BVT accepts (fast and deterministic).
     These are the same conditions as BVT membership (eval_runner.bvt_member), so
     the release decision and the BVT count the same gate cases. A red line is
     tier-blind as compute_redline is, except a draft or archived case, which never
     runs.

2. **Five dimensions** (``summarize_dimensions``): cognition, quality, responsibility,
   cost, performance. Each dimension holds passed / failed / not-run counts per tier.
   There is no percentage and no total: one dimension's good result must not average
   away another's bad one.

3. **Release verdict** (``decide_release``): four checks in a fixed order.
   1. ``redline``     — a red case failed, errored or timed out → ``blocked``.
   2. ``dimensions``  — a gate case failed → ``blocked``.
   3. ``denominator`` — a red or gate case the run selected has no definite result
      (error, timeout, skipped, or missing), a gate case was edited without being
      re-validated (its stamp is stale: its result is not evidence), or the run
      selected no red or gate case at all → ``incomplete``. A check that did not run
      is never silently left out of the count.
   4. ``record``      — always passes; holds the one-sentence reason for the verdict
      and the known issues the release carries (watch failures, watch cases that did
      not run).
   The first failing check decides the verdict; later checks are still reported.
   Watch results never change the verdict.

The cases passed in are the cases the run SELECTED (after tag / id / behavior
filters), so a quick run that excludes offline cases by design does not count them
as missing.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

TIERS = ("red", "gate", "watch")
FIVE_DIMENSIONS = ("cognition", "quality", "responsibility", "cost", "performance")
GATING_VALUES = frozenset({"gate", "watch"})
CHECK_ORDER = ("redline", "dimensions", "denominator", "record")
_MODEL_METHODS = frozenset({"llm", "behavior"})
_NOT_RUN = ("error", "timeout", "skipped")
GATE_TIERS = frozenset({"active", "stable"})       # mirrors eval_runner._GATE_TIERS
_NEVER_RUN_TIERS = frozenset({"draft", "archived"})


def model_judged(case: Mapping[str, Any]) -> bool:
    """True when a model decides (part of) the case's result: a judge rubric, or an
    llm / behavior eval method."""
    return bool(case.get("decision_rubric")) or case.get("eval_method") in _MODEL_METHODS


def can_gate(case: Mapping[str, Any]) -> bool:
    """True when a case may hold the gate tier: not model-judged, an active or stable
    tier, and a BVT-eligible evaluator (golden_case_validator._is_gate_eligible)."""
    from scripts.golden_case_validator import _is_gate_eligible

    return (not model_judged(case) and case.get("tier", "active") in GATE_TIERS
            and _is_gate_eligible(dict(case)))


def tier_of(case: Mapping[str, Any]) -> str:
    """``red`` / ``gate`` / ``watch`` for one case (see the module docstring)."""
    if case.get("redline") is True and case.get("tier") not in _NEVER_RUN_TIERS:
        return "red"
    if case.get("gating") == "gate" and can_gate(case):
        return "gate"
    return "watch"


def _stamp_stale(case: Mapping[str, Any]) -> bool:
    """A gate case edited outside the validator: its stamp does not match its body."""
    from scripts.golden_case_validator import compute_case_stamp

    return case.get("validated_by_4gate") != compute_case_stamp(dict(case))


def _status_by_id(results: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    return {r.get("id"): r.get("status") for r in results if isinstance(r, Mapping)}


def _bucket(status: str | None) -> str:
    if status == "passed":
        return "passed"
    if status == "failed":
        return "failed"
    return "not_run"


def summarize_dimensions(cases: Sequence[Mapping[str, Any]],
                         results: Iterable[Mapping[str, Any]]) -> dict[str, dict]:
    """``{dimension: {tier: {passed, failed, not_run}}}`` for the five dimensions.

    Every selected case is counted once, under its own dimension and tier. A case
    whose dimension is not one of the five is counted under ``other`` so nothing is
    dropped silently."""
    status = _status_by_id(results)
    out: dict[str, dict] = {d: {t: {"passed": 0, "failed": 0, "not_run": 0} for t in TIERS}
                            for d in FIVE_DIMENSIONS}
    for c in cases:
        dim = c.get("dimension") if c.get("dimension") in FIVE_DIMENSIONS else "other"
        row = out.setdefault(dim, {t: {"passed": 0, "failed": 0, "not_run": 0} for t in TIERS})
        row[tier_of(c)][_bucket(status.get(c.get("id")))] += 1
    return out


def decide_release(cases: Sequence[Mapping[str, Any]],
                   results: Iterable[Mapping[str, Any]]) -> dict:
    """The run's ``release`` block: ``{verdict, reason, checks: [...]}``."""
    status = _status_by_id(results)
    red_violations, gate_failures, missing, known = [], [], [], []
    for c in cases:
        cid, tier = c.get("id"), tier_of(c)
        st = status.get(cid)
        if tier == "red" and st in ("failed", "error", "timeout"):
            red_violations.append({"id": cid, "status": st})
        if tier == "gate" and st == "failed":
            gate_failures.append({"id": cid, "dimension": c.get("dimension")})
        if tier == "gate" and _stamp_stale(c):
            missing.append({"id": cid, "status": "stale_stamp"})
        elif tier in ("red", "gate") and (st is None or st in _NOT_RUN):
            # a red case that errored/timed out is already a red-line violation;
            # it is listed here too so the denominator is complete
            missing.append({"id": cid, "status": st or "missing"})
        if tier == "watch" and st != "passed":
            known.append({"id": cid, "kind": "failed" if st == "failed" else "not_run",
                          "status": st or "missing"})
    selected = sum(1 for c in cases if tier_of(c) in ("red", "gate"))
    denominator_ok = not missing and selected > 0
    checks = [
        {"id": "redline", "passed": not red_violations, "cases": red_violations},
        {"id": "dimensions", "passed": not gate_failures, "cases": gate_failures},
        {"id": "denominator", "passed": denominator_ok, "selected": selected,
         "with_result": selected - len(missing), "cases": missing},
    ]
    if red_violations:
        verdict = "blocked"
        reason = f"{len(red_violations)} red-line case(s) did not pass: {_ids(red_violations)}"
    elif gate_failures:
        verdict = "blocked"
        reason = f"{len(gate_failures)} gate case(s) failed: {_ids(gate_failures)}"
    elif missing:
        verdict = "incomplete"
        reason = (f"{len(missing)} of {selected} red-line or gate case(s) have no result: "
                  f"{_ids(missing)}")
    elif not selected:
        verdict = "incomplete"
        reason = "no red-line or gate case was selected, so this run cannot decide a release"
    else:
        verdict = "release"
        reason = (f"all {selected} red-line and gate case(s) passed"
                  + (f"; {len(known)} watch case(s) carried as known issues" if known else ""))
    checks.append({"id": "record", "passed": True, "reason": reason, "known_issues": known})
    return {"verdict": verdict, "reason": reason, "checks": checks}


def _ids(rows: Sequence[Mapping[str, Any]], limit: int = 5) -> str:
    ids = [str(r.get("id")) for r in rows]
    return ", ".join(ids[:limit]) + (f" (+{len(ids) - limit} more)" if len(ids) > limit else "")


def failing_check(release: Mapping[str, Any] | None) -> dict | None:
    """The first check that did not pass, or None (also for a missing block)."""
    if not isinstance(release, Mapping):
        return None
    return next((c for c in release.get("checks") or []
                 if isinstance(c, Mapping) and not c.get("passed")), None)
