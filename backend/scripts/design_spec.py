"""design_spec — think before building, break before review, fix by class after it.

Three stage-complete checks that close the gaps of an implementation that starts before
its design is thought through (skills/s_autonomous-pipeline/stages/ref/plan/design-spec.md):

1. **PLAN design spec** (``plan_errors``). The plan artifact carries ``design``:
   ``user_workflow`` (how the user / agent experiences the change, step by step),
   ``architecture`` (a mermaid diagram of the components and data flow), ``flow`` (a
   mermaid flowchart / sequence of the main path WITH at least one failure branch),
   ``states`` (every state the change adds or touches: who creates it, who removes it,
   and what happens when it meets each existing state / event — or ``no_state_reason``),
   ``invariants`` (numbered ``I<n>``: what must stay true, and where it is enforced) and
   ``failure_modes`` (numbered ``F<n>``: kill / crash, concurrency, retry, partial
   completion, stale data… with the expected outcome).
2. **BUILD break tests** (``build_errors``). ``break_tests`` lists the tests the builder
   wrote to BREAK the design before any expert sees it; every invariant and every
   failure mode of the plan is targeted by at least one.
3. **Fix by class** (``finding_errors``, REVIEW, DELIVER and GOAL_CYCLE — a goal run
   builds inside goal_cycle, so its ``break_tests`` are checked there too). Every fixed blocking finding
   (HIGH / CRITICAL, or MED at confidence >= 7) names the invariant it violated (a
   missed one is added to the design: ``design_updates``), the design-level
   ``root_cause``, the ``class_fix`` (where the invariant is now enforced for every
   instance, not the one line reported) and the ``break_test`` that proves the class.

Profiles: full / goal need the whole spec; bugfix / direct need ``flow``, ``states``,
``invariants`` and ``failure_modes``; trivial / docs / research are exempt. Runs created
before ``CUTOVER`` (the ledger date, not run.json's writable copy) get warnings instead
of errors; a run with no readable date is judged. Findings recorded as "Not a bug: …"
and a ``skipped`` adversarial tier carry nothing to fix by class.

Public: ``CUTOVER``, ``PLAN_TEMPLATE``, ``BUILD_TEMPLATE``, ``FINDING_TEMPLATE``,
``plan_errors``, ``build_errors``, ``finding_errors``, ``stage_errors``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

CUTOVER = "2026-10-19T08:30:00Z"
_LIGHT = frozenset({"bugfix", "direct"})
_MERMAID = re.compile(r"\b(graph|flowchart|sequenceDiagram|stateDiagram(?:-v2)?)\b")
_ARROW = re.compile(r"-->|->>|-\.->|==>|--x|-x")
_INV_ID = re.compile(r"^I\d+$")
_FM_ID = re.compile(r"^F\d+$")
_TEST_REF = re.compile(r"^[\w./-]+\.(py|ts|tsx|js|jsx)::[\w\[\]\-.:]+$")
_MIN = 15

PLAN_TEMPLATE = {
    "user_workflow": ["1. who does what today, one line per step", "2. what they do after the change, step by step"],
    "architecture": "flowchart LR\n  A[component] -->|data| B[component]\n  B --> C[store]",
    "flow": "flowchart TD\n  S[start] --> C{check}\n  C -->|ok| D[do]\n  C -->|fail| E[refuse + message]",
    "states": [{"id": "S1", "name": "the new state", "created_by": "file::symbol",
                "removed_by": "file::symbol (or 'never: <why that is safe>')",
                "crosses": [{"with": "an existing state / event (e.g. landed, killed, paused)",
                             "outcome": "what happens, and why it is correct"}]}],
    "invariants": [{"id": "I1", "statement": "what must always hold", "enforced_at": "file::symbol"}],
    "failure_modes": [{"id": "F1", "scenario": "the process is killed between step A and B",
                       "expected": "the next call recovers / refuses with a clear code"},
                      {"id": "F2", "scenario": "two sessions do X at once",
                       "expected": "one wins, the other ..."}],
}
BUILD_TEMPLATE = [{"targets": ["I1", "F1", "F2"], "test": "backend/tests/test_x.py::test_breaks_i1",
                   "attack": "what the test does to try to violate it"}]
FINDING_TEMPLATE = {"invariant": "I1", "root_cause": "why the DESIGN allowed it",
                    "class_fix": "where the invariant is now enforced for every instance",
                    "break_test": "backend/tests/test_x.py::test_the_class"}


def _tier(profile: str) -> "str | None":
    """None = exempt (relaxed profile); "light" = bugfix / direct; "full" = everything
    else, unknown profiles included (fail closed, the pipeline_profiles C3 rule)."""
    try:
        from core.pipeline_profiles import is_relaxed_profile, normalize_profile
        p = normalize_profile(profile)
        if is_relaxed_profile(p):
            return None
    except ImportError:   # standalone use: the literal sets
        p = str(profile or "").strip().lower()
        if p in ("trivial", "docs", "research"):
            return None
    return "light" if p in _LIGHT else "full"


def _enforced(run_created_at: "str | None") -> bool:
    """True from CUTOVER on. A missing or unreadable date is judged, never exempted:
    the caller passes the LEDGER created_at (run.json's copy is writable)."""
    if not run_created_at:
        return True
    try:
        ts = datetime.fromisoformat(str(run_created_at).strip().replace("Z", "+00:00"))
        cut = datetime.fromisoformat(CUTOVER.replace("Z", "+00:00"))
    except ValueError:
        return True   # an unreadable timestamp is judged, never exempted
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cut


def _text(v, n: int = _MIN) -> bool:
    return isinstance(v, str) and len(v.strip()) >= n


def _diagram(v) -> bool:
    return isinstance(v, str) and bool(_MERMAID.search(v)) and len(_ARROW.findall(v)) >= 2


def _list(v) -> list:
    return v if isinstance(v, list) else []


def _ids(items, rx) -> "list[str]":
    return [str(i.get("id")) for i in _list(items) if isinstance(i, dict) and rx.match(str(i.get("id") or ""))]


def plan_errors(plan: dict, profile: str) -> "list[str]":
    """The PLAN design spec (module docstring, 1)."""
    tier = _tier(profile)
    if tier is None:
        return []
    d = plan.get("design") if isinstance(plan, dict) else None
    if not isinstance(d, dict):
        return ["Design spec: plan.design is missing — think the change through before BUILD "
                "(user_workflow, architecture, flow, states, invariants, failure_modes; "
                "template: design_spec.PLAN_TEMPLATE, rules: stages/ref/plan/design-spec.md)"]
    errs: list[str] = []
    if tier == "full":
        wf = d.get("user_workflow")
        if not (isinstance(wf, list) and len([s for s in wf if _text(s, 8)]) >= 2):
            errs.append("Design spec: design.user_workflow needs >= 2 steps of how the user / "
                        "agent experiences the change (before → after)")
        if not _diagram(d.get("architecture")):
            errs.append("Design spec: design.architecture must be a mermaid diagram "
                        "(graph/flowchart/sequenceDiagram) with >= 2 edges: components + data flow")
    flow = d.get("flow")
    if not _diagram(flow):
        errs.append("Design spec: design.flow must be a mermaid flowchart / sequence of the "
                    "main path with >= 2 edges")
    elif not re.search(r"fail|error|refus|deny|timeout|kill|conflict|retry|abort|invalid",
                       flow, re.IGNORECASE):
        errs.append("Design spec: design.flow shows no failure branch — draw what happens when "
                    "the main path fails (refuse / error / retry / kill)")
    states = d.get("states")
    if not isinstance(states, list):
        errs.append("Design spec: design.states must be a list (empty only with no_state_reason)")
    elif not states:
        if not _text(d.get("no_state_reason"), 20):
            errs.append("Design spec: design.states is empty — give no_state_reason (>= 20 "
                        "chars: why the change adds and touches no state)")
    else:
        for s in states:
            sid = s.get("id") if isinstance(s, dict) else None
            if not isinstance(s, dict) or not s.get("name"):
                errs.append(f"Design spec: state {sid!r} needs id and name")
                continue
            for k in ("created_by", "removed_by"):
                if not _text(s.get(k), 5):
                    errs.append(f"Design spec: state {sid}: '{k}' is required — who "
                                f"{'creates' if k == 'created_by' else 'removes'} it "
                                "('never: <why safe>' is an answer)")
            cr = s.get("crosses")
            if not (isinstance(cr, list) and cr and all(
                    isinstance(c, dict) and _text(c.get("with"), 3) and _text(c.get("outcome"), 10)
                    for c in cr)):
                errs.append(f"Design spec: state {sid}: 'crosses' must list each existing "
                            "state / event it can meet ({with, outcome})")
    inv = d.get("invariants")
    good_inv = [i for i in _list(inv) if isinstance(i, dict) and _INV_ID.match(str(i.get("id") or ""))
                and _text(i.get("statement"))]
    if not isinstance(inv, list) or not good_inv or len(good_inv) != len(inv):
        errs.append("Design spec: design.invariants needs >= 1 entry, each {id: I<n>, "
                    "statement (>= 15 chars), enforced_at}")
    fm = d.get("failure_modes")
    need = 2 if tier == "full" else 1
    good_fm = [f for f in _list(fm) if isinstance(f, dict) and _FM_ID.match(str(f.get("id") or ""))
               and _text(f.get("scenario"), 10) and _text(f.get("expected"), 10)]
    if not isinstance(fm, list) or len(good_fm) < need or len(good_fm) != len(fm):
        errs.append(f"Design spec: design.failure_modes needs >= {need} entries, each {{id: F<n>, "
                    "scenario, expected}} — interruption, concurrency, retry, partial completion")
    return errs


def build_errors(build: dict, plan: "dict | None", profile: str) -> "list[str]":
    """BUILD break tests (module docstring, 2)."""
    if _tier(profile) is None or not isinstance(plan, dict):
        return []
    d = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    targets = set(_ids(d.get("invariants"), _INV_ID)) | set(_ids(d.get("failure_modes"), _FM_ID))
    if not targets:
        return []   # no design to break (plan_errors already reported it)
    bt = build.get("break_tests") if isinstance(build, dict) else None
    if not isinstance(bt, list) or not bt:
        return ["Break tests: build.break_tests is missing — before review, write the tests that "
                f"try to break each invariant / failure mode of the design ({', '.join(sorted(targets))})"]
    errs, hit = [], set()
    for i, t in enumerate(bt):
        if not isinstance(t, dict):
            errs.append(f"Break tests: break_tests[{i}] must be an object")
            continue
        if not _TEST_REF.match(str(t.get("test") or "")):
            errs.append(f"Break tests: break_tests[{i}].test must be 'path::test_name'")
        if not _text(t.get("attack"), 10):
            errs.append(f"Break tests: break_tests[{i}].attack must say how it tries to break the design")
        hit |= {str(x) for x in _list(t.get("targets")) if isinstance(x, str)}
    miss = sorted(targets - hit)
    if miss:
        errs.append("Break tests: no break test targets " + ", ".join(miss)
                    + " — each invariant and failure mode needs one")
    return errs


try:  # the ONE blocking rule every Gate-2 reader uses (core/gate2_findings.py)
    from core.gate2_findings import is_blocking as _blocking
except ImportError:  # pragma: no cover - standalone use without backend/ on sys.path
    def _blocking(f: dict) -> bool:
        sev = str(f.get("severity") or "").strip().upper()
        if sev.startswith(("HIGH", "CRIT", "BLOCK", "P0", "P1")):
            return True
        if sev.startswith("MED"):
            m = re.match(r"\s*(\d+(?:\.\d+)?)", str(f.get("confidence") or ""))
            return m is None or float(m.group(1)) >= 7
        return False

# A reported finding judged a false positive stays recorded (ref/deliver/gate2-findings.md):
# `finding` = "Not a bug: <evidence>"; its specialist's re-check rules on it. Not a defect →
# nothing to fix by class.
_NOT_A_BUG = re.compile(r"^\s*not\s+a\s+bug\b", re.IGNORECASE)


def finding_errors(findings, plan: "dict | None", design_updates, profile: str,
                   where: str) -> "list[str]":
    """Fix by class (module docstring, 3) for one stage's findings."""
    if _tier(profile) is None or not isinstance(findings, list):
        return []
    plan = plan if isinstance(plan, dict) else {}
    d = (plan or {}).get("design") if isinstance((plan or {}).get("design"), dict) else {}
    known = set(_ids(d.get("invariants"), _INV_ID))
    added = {str(u.get("id")) for u in _list(design_updates) if isinstance(u, dict)
             and _INV_ID.match(str(u.get("id") or "")) and _text(u.get("statement"))
             and _text(u.get("why_missed"), 10)}
    errs = []
    for i, f in enumerate(findings):
        if not isinstance(f, dict) or not f.get("resolved") or not _blocking(f):
            continue
        if _NOT_A_BUG.match(str(f.get("finding") or "")):
            continue
        tag = f"{where}.findings[{i}] ({f.get('specialist') or '?'}, {f.get('severity')})"
        inv = str(f.get("invariant") or "")
        if inv not in known | added:
            errs.append(f"Fix by class: {tag} names no invariant of the design (got {inv!r}). "
                        "Name the I<n> it violated, or add the missed invariant to "
                        "design_updates [{id, statement, why_missed}] and fix the design")
        if not _text(f.get("root_cause"), 20):
            errs.append(f"Fix by class: {tag} needs root_cause — why the DESIGN allowed it, "
                        "not what the line did")
        if not _text(f.get("class_fix"), 20):
            errs.append(f"Fix by class: {tag} needs class_fix — every place the invariant is "
                        "now enforced, not only the reported line")
        if not _TEST_REF.match(str(f.get("break_test") or "")):
            errs.append(f"Fix by class: {tag} needs break_test 'path::test' proving the class "
                        "is closed")
    return errs


def stage_errors(stage: str, artifact: dict, *, profile: str, plan: "dict | None",
                 record: "dict | None" = None, run_created_at: "str | None" = None,
                 warnings: "list[str] | None" = None) -> "list[str]":
    """All design-spec errors for one stage-complete candidate. Runs created before
    CUTOVER get them as warnings (appended to *warnings*) instead."""
    if not isinstance(artifact, dict):
        return []
    rec = record if isinstance(record, dict) else {}
    updates = artifact.get("design_updates") or rec.get("design_updates")
    if stage == "plan":
        errs = plan_errors(artifact, profile)
    elif stage == "build":
        errs = build_errors(artifact, plan, profile)
    elif stage == "review":
        errs = finding_errors(artifact.get("findings"), plan, updates, profile, "review")
        try:
            n = int(artifact.get("findings_count") or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0 and _tier(profile) is not None and not isinstance(artifact.get("findings"), list):
            errs.append(f"Fix by class: findings_count is {n} but review.findings is missing — "
                        "record each finding (severity, confidence, resolved, + for a fixed "
                        "blocking one: invariant, root_cause, class_fix, break_test)")
    elif stage in ("deliver", "goal_cycle"):
        ar = artifact.get("adversarial_review") if isinstance(artifact.get("adversarial_review"), dict) else {}
        if str(ar.get("profile_tier") or "").lower() != "skipped":   # no review ran, nothing fixed
            errs = finding_errors(ar.get("findings"), plan, updates, profile, "adversarial_review")
        else:
            errs = []
        if stage == "goal_cycle":   # goal runs build inside goal_cycle: break tests live here
            errs = build_errors(artifact, plan, profile) + errs
    else:
        errs = []
    if errs and not _enforced(run_created_at):
        if warnings is not None:
            warnings.extend("(not enforced for this run) " + e for e in errs)
        return []
    return errs
