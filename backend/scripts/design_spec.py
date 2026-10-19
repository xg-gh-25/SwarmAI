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

Before them, two root-cause checks at the intent → requirement end (``ROOT_CUTOVER``):

0a. **EVALUATE root cause** (``root_cause_errors``). ``understanding.root_cause`` carries a
    ``why_chain`` (>= 3 levels ``W<n>`` "X because Y", each with evidence) that ends at a
    DESIGN level (``root_layer``) and names the ``missed_invariant``; and ``recurrence``:
    where it searched (>= 2 sources), ``prior_instances`` and ``siblings`` (``C<n>``: the
    same shape elsewhere). The profile follows from it: ``bugfix`` (a smallest-diff patch)
    is only legal when the root is a ``code-line`` with no prior instance and no sibling —
    anything else is whack-a-mole and must run ``full``.
0b. **THINK solution class** (``solution_errors``). ``solution_class`` says whether the
    recommendation is ``systemic`` or a ``patch``: which ``W<n>`` it addresses (the deepest,
    or why not), how it covers every sibling ``C<n>`` (or why one is excluded), and the
    ``structural_change`` (none / refactor / redesign) — a design-level root with none needs
    a reason. A ``patch`` is legal only as a declared P0 stop-the-bleeding with a follow-up
    root-fix. ``alternatives`` needs >= 2 entries, >= 1 of them ``targets_root: true``.
    PLAN then names the ``root_invariant`` (the ``I<n>`` that encodes the missed invariant).

Profiles: full / goal need the whole spec; bugfix / direct need ``flow``, ``states``,
``invariants`` and ``failure_modes``; trivial / docs / research are exempt. Runs created
before ``CUTOVER`` (the ledger date, not run.json's writable copy) get warnings instead
of errors; a run with no readable date is judged. Findings recorded as "Not a bug: …"
and a ``skipped`` adversarial tier carry nothing to fix by class.

Public: ``CUTOVER``, ``ROOT_CUTOVER``, ``ROOT_TEMPLATE``, ``SOLUTION_TEMPLATE``,
``root_cause_errors``, ``solution_errors``, ``PLAN_TEMPLATE``, ``BUILD_TEMPLATE``, ``FINDING_TEMPLATE``,
``plan_errors``, ``build_errors``, ``finding_errors``, ``stage_errors``,
``FIXSPEC_TEMPLATE``, ``fixing_spec_errors``.

4. FIXING_SPEC (docs/design/pipeline-anti-whack-a-mole.md §7.3): between a Gate-2 round
   with blocking findings and any fix, ONE spec groups the round's open items by cause —
   ``fixing_spec_errors``: every open item in exactly one cluster (a NOT RESOLVED
   re-check item through a cluster that ``revises`` the one it re-checked), each cluster
   bound to a design invariant (or a new one in ``design_updates``) with its sites and the
   search that found them, one solution, its reuse decision, why it is no patch, and a
   break test; N single-finding clusters (N > 2), or > 2 on one file, must say why they
   share no cause (``unrelated``); only a non-blocking finding may be ``deferred``; a
   blocking finding shown not to be a bug goes in ``refuted`` with its evidence (the
   skeptic checks it) instead of a cluster.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

CUTOVER = "2026-10-19T08:30:00Z"
ROOT_CUTOVER = "2026-10-19T10:00:00Z"
ROOT_LAYERS = ("code-line", "module-design", "contract", "architecture", "process", "user-need")
_DESIGN_LAYERS = frozenset({"module-design", "contract", "architecture"})
_W_ID = re.compile(r"^W\d+$")
_C_ID = re.compile(r"^C\d+$")
_LIGHT = frozenset({"bugfix", "direct"})
_MERMAID = re.compile(r"\b(graph|flowchart|sequenceDiagram|stateDiagram(?:-v2)?)\b")
_ARROW = re.compile(r"-->|->>|-\.->|==>|--x|-x")
_INV_ID = re.compile(r"^I\d+$")
_FM_ID = re.compile(r"^F\d+$")
_TEST_REF = re.compile(r"^[\w./-]+\.(py|ts|tsx|js|jsx)::[\w\[\]\-.:]+$")
_MIN = 15

ROOT_TEMPLATE = {
    "why_chain": [
        {"id": "W1", "because": "the symptom: X happens because Y (what the code does)",
         "evidence": "file.py:120 / command output / log count"},
        {"id": "W2", "because": "Y happens because the module does Z for every caller",
         "evidence": "file.py:88 + grep of the 3 callers"},
        {"id": "W3", "because": "Z was designed without the rule that ... must hold",
         "evidence": "design doc / git log -S of the commit that chose it"}],
    "root_layer": "module-design",
    "missed_invariant": "the rule whose absence lets the whole class happen",
    "recurrence": {
        "searched": ["git log -S'<pattern>' --oneline", "IMPROVEMENT.md What Failed", "grep -rn '<shape>'"],
        "prior_instances": ["commit abc123: the same symptom patched in file.py:130"],
        "siblings": [{"id": "C1", "where": "other.py:40", "shape": "does the same Z without the rule"}]},
}
SOLUTION_TEMPLATE = {
    "verdict": "systemic",
    "addresses": "W3",
    "why_root": "fixes the design level every instance comes from, not the reported one",
    "covers": [{"id": "C1", "how": "other.py moves onto the same enforced path"}],
    "structural_change": "refactor",
    "structural_reason": "the rule must live in one place every caller goes through",
}

PLAN_TEMPLATE = {
    "root_invariant": "I1",
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


def _enforced(run_created_at: "str | None", cutover: str = CUTOVER) -> bool:
    """True from CUTOVER on. A missing or unreadable date is judged, never exempted:
    the caller passes the LEDGER created_at (run.json's copy is writable)."""
    if not run_created_at:
        return True
    try:
        ts = datetime.fromisoformat(str(run_created_at).strip().replace("Z", "+00:00"))
        cut = datetime.fromisoformat(cutover.replace("Z", "+00:00"))
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


def _root(evaluate) -> "dict | None":
    und = evaluate.get("understanding") if isinstance(evaluate, dict) else None
    rc = und.get("root_cause") if isinstance(und, dict) else None
    return rc if isinstance(rc, dict) else None


def root_cause_errors(evaluate: dict, profile: str) -> "list[str]":
    """EVALUATE root cause (module docstring, 0a)."""
    if _tier(profile) is None:
        return []
    rc = _root(evaluate)
    if rc is None:
        return ["Root cause: understanding.root_cause is missing — dig to the root before "
                "THINK: why_chain (>= 3 levels W<n>, each with evidence), root_layer, "
                "missed_invariant, recurrence (template: design_spec.ROOT_TEMPLATE, rules: "
                "stages/ref/plan/design-spec.md §0)"]
    errs: list[str] = []
    wc = rc.get("why_chain")
    good = [w for w in _list(wc) if isinstance(w, dict) and _W_ID.match(str(w.get("id") or ""))
            and _text(w.get("because"), 15) and _text(w.get("evidence"), 8)]
    if len(good) < 3 or len(good) != len(_list(wc)) or not isinstance(wc, list):
        errs.append("Root cause: why_chain needs >= 3 levels, each {id: W<n>, because (>= 15 "
                    "chars: X because Y), evidence (an observation)} — the first is the symptom, "
                    "the last is where the design let it happen")
    layer = str(rc.get("root_layer") or "")
    if layer not in ROOT_LAYERS:
        errs.append(f"Root cause: root_layer must be one of {', '.join(ROOT_LAYERS)} (got {layer!r})")
    if not _text(rc.get("missed_invariant"), 15):
        errs.append("Root cause: missed_invariant (>= 15 chars) — the rule whose absence lets "
                    "this whole class happen; PLAN encodes it as design.root_invariant")
    rec = rc.get("recurrence") if isinstance(rc.get("recurrence"), dict) else None
    if rec is None:
        errs.append("Root cause: recurrence is missing — search for the same symptom before "
                    "(git log, IMPROVEMENT What Failed, EVOLUTION) and the same shape elsewhere "
                    "(grep): {searched, prior_instances, siblings}")
        prior, sib = [], []
    else:
        if len([x for x in _list(rec.get("searched")) if _text(x, 8)]) < 2:
            errs.append("Root cause: recurrence.searched must name >= 2 searches actually run "
                        "(the command / the store), not a claim that none exist")
        prior = [x for x in _list(rec.get("prior_instances")) if _text(x, 8)]
        sib = _list(rec.get("siblings"))
        if not isinstance(rec.get("prior_instances"), list) or not isinstance(rec.get("siblings"), list):
            errs.append("Root cause: recurrence.prior_instances and recurrence.siblings must be "
                        "lists ([] when the search found none)")
        bad = [x for x in sib if not (isinstance(x, dict) and _C_ID.match(str(x.get("id") or ""))
                                      and _text(x.get("where"), 3))]
        if bad:
            errs.append("Root cause: each sibling is {id: C<n>, where, shape} — the same shape "
                        "somewhere else, which a point fix would leave standing")
    return errs + _bugfix_rule_errors(rc, profile)


def _bugfix_rule_errors(rc: dict, profile: str) -> "list[str]":
    """bugfix is a smallest-diff patch: legal only for a code-line root with no prior
    instance and no sibling. Checked at EVALUATE, THINK and PLAN against the CURRENT
    profile, so a `--profile bugfix` switched after EVALUATE is caught too."""
    try:
        from core.pipeline_profiles import normalize_profile
        prof = normalize_profile(profile)
    except ImportError:
        prof = str(profile or "").strip().lower()
    if prof != "bugfix":
        return []
    layer = str(rc.get("root_layer") or "")
    rec = rc.get("recurrence") if isinstance(rc.get("recurrence"), dict) else {}
    prior = [x for x in _list(rec.get("prior_instances")) if _text(x, 8)]
    sib = _list(rec.get("siblings"))
    if layer == "code-line" and not prior and not sib:
        return []
    why = (f"root_layer is {layer!r}" if layer != "code-line" else
           f"{len(prior)} prior instance(s) and {len(sib)} sibling(s)")
    return [f"Root cause: profile bugfix is a smallest-diff patch, but {why} — fixing one "
            "instance is whack-a-mole. Run the class fix as full: "
            "`artifact_cli.py run-update --project <P> --run-id <R> --profile full`"]


def solution_errors(think: dict, evaluate: "dict | None", profile: str) -> "list[str]":
    """THINK solution class (module docstring, 0b)."""
    if _tier(profile) is None or not isinstance(think, dict):
        return []
    rc = _root(evaluate)
    if rc is None:
        # A strict run reaching THINK without one (EVALUATE ran under a relaxed profile and
        # was upgraded since). Older runs only get this as a warning (ROOT_CUTOVER).
        return ["Solution class: EVALUATE recorded no understanding.root_cause, so there is "
                "no root to fix — re-enter EVALUATE (`stage-start --stage evaluate`), record "
                "root_cause, then THINK"]
    errs: list[str] = _bugfix_rule_errors(rc, profile)
    alts = think.get("alternatives")
    if not (isinstance(alts, list) and len([a for a in alts if isinstance(a, dict)]) >= 2):
        errs.append("Solution class: think.alternatives needs >= 2 approaches")
    elif not any(isinstance(a, dict) and a.get("targets_root") is True for a in alts):
        errs.append("Solution class: no alternative has targets_root: true — at least one "
                    "approach must fix the root (the last why_chain level), not the symptom")
    sc = think.get("solution_class")
    if not isinstance(sc, dict):
        return errs + ["Solution class: think.solution_class is missing — is the recommendation "
                       "a systemic fix or a patch? (template: design_spec.SOLUTION_TEMPLATE)"]
    w_ids = _ids(rc.get("why_chain"), _W_ID)
    deepest = w_ids[-1] if w_ids else None
    verdict = str(sc.get("verdict") or "")
    if verdict == "patch":
        if not (_text(sc.get("p0_reason"), 20) and _text(sc.get("follow_up"), 10)):
            errs.append("Solution class: a patch is legal only as a declared P0 stop-the-"
                        "bleeding: p0_reason (>= 20 chars) + follow_up (the root-fix run / ToDo)")
        return errs
    if verdict != "systemic":
        return errs + [f"Solution class: verdict must be 'systemic' or 'patch' (got {verdict!r})"]
    addr = str(sc.get("addresses") or "")
    if addr not in w_ids:
        errs.append(f"Solution class: addresses must name a why_chain level ({', '.join(w_ids)}), "
                    f"got {addr!r}")
    elif addr != deepest and not _text(sc.get("why_not_deepest"), 20):
        errs.append(f"Solution class: addresses {addr}, not the root {deepest} — fix the root, "
                    "or say why_not_deepest (>= 20 chars)")
    if not _text(sc.get("why_root"), 20):
        errs.append("Solution class: why_root (>= 20 chars) — why this removes the class, not "
                    "one instance")
    rec = rc.get("recurrence") if isinstance(rc.get("recurrence"), dict) else {}
    sib = {str(x.get("id")) for x in _list(rec.get("siblings")) if isinstance(x, dict) and x.get("id")}
    covered = {str(c.get("id")) for c in _list(sc.get("covers")) if isinstance(c, dict)
               and (_text(c.get("how"), 10) or _text(c.get("excluded"), 15))}
    miss = sorted(sib - covered)
    if miss:
        errs.append("Solution class: covers leaves sibling(s) " + ", ".join(miss) + " standing — "
                    "each needs {id, how} or {id, excluded: <why>}; a fix that leaves the same "
                    "shape elsewhere is whack-a-mole")
    st = str(sc.get("structural_change") or "")
    if st not in ("none", "refactor", "redesign"):
        errs.append("Solution class: structural_change must be none / refactor / redesign")
    elif not _text(sc.get("structural_reason"), 15):
        errs.append("Solution class: structural_reason (>= 15 chars) — why this structural change "
                    "(or none) is the right size")
    elif (st == "none" and str(rc.get("root_layer")) in _DESIGN_LAYERS
          and not _text(sc.get("structural_reason"), 40)):
        errs.append(f"Solution class: the root is at {rc.get('root_layer')} but structural_change "
                    "is none — refactor / redesign it, or explain in structural_reason (>= 40 "
                    "chars) how a non-structural change removes a design-level cause")
    return errs


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


def _root_invariant_errors(plan: dict, evaluate: "dict | None", profile: str) -> "list[str]":
    """PLAN encodes EVALUATE's missed invariant (module docstring, 0b)."""
    if _tier(profile) is None or _root(evaluate) is None:
        return []   # missing upstream root cause: THINK already refused it
    errs = _bugfix_rule_errors(_root(evaluate), profile)
    d = plan.get("design") if isinstance(plan.get("design"), dict) else {}
    ri = str(d.get("root_invariant") or "")
    if ri not in _ids(d.get("invariants"), _INV_ID):
        errs.append("Design spec: design.root_invariant must name the I<n> that encodes "
                    "EVALUATE's root_cause.missed_invariant (got " + repr(ri) + ") — the plan "
                    "fixes the root, so the root's rule is one of its invariants")
    return errs


def stage_errors(stage: str, artifact: dict, *, profile: str, plan: "dict | None",
                 record: "dict | None" = None, run_created_at: "str | None" = None,
                 warnings: "list[str] | None" = None,
                 evaluate: "dict | None" = None) -> "list[str]":
    """All design-spec errors for one stage-complete candidate. Runs created before
    the matching cutover get them as warnings (appended to *warnings*) instead."""
    if not isinstance(artifact, dict):
        return []
    rec = record if isinstance(record, dict) else {}
    updates = artifact.get("design_updates") or rec.get("design_updates")
    root_errs: list[str] = []
    if stage == "evaluate":
        root_errs = root_cause_errors(artifact, profile)
        errs = []
    elif stage == "think":
        root_errs = solution_errors(artifact, evaluate, profile)
        errs = []
    elif stage == "plan":
        errs = plan_errors(artifact, profile) + _root_invariant_errors(artifact, evaluate, profile)
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
    out: list[str] = []
    for found, cut in ((errs, CUTOVER), (root_errs, ROOT_CUTOVER)):
        if found and not _enforced(run_created_at, cut):
            if warnings is not None:
                warnings.extend("(not enforced for this run) " + e for e in found)
        else:
            out += found
    return out


# ── 4. FIXING_SPEC (Gate-2, between the parallel review and any fix) ─────────────
FIXSPEC_TEMPLATE = {
    "round": 1,
    "clusters": [{
        "id": "K1", "findings": ["<ledger id>", "<expert>-R<n> (with revises)"],
        "cause": {"invariant": "I2", "why": "two writers build the args; nothing makes one own them"},
        "scope": {"sites": ["backend/cli.py::build", "backend/retry.py::rebuild"],
                  "evidence": "grep -rn 'build_args\\|rebuild' backend -> 2 sites, both listed"},
        "solution": "retry.py calls cli.build; retry.rebuild is deleted",
        "reuse": {"mechanism": "backend/cli.py::build", "decision": "reuse"},
        "anti_patch": "no new guard: the second writer is removed, not checked",
        "design_impact": "none",
        "break_test": "backend/tests/test_args.py::test_one_writer_for_every_caller"}],
    "unrelated": [], "deferred": [], "design_updates": [],
    "refuted": [{"id": "<ledger id>", "evidence": "a.py:3 never receives a token: the caller masks it first"}]}
_K_ID = re.compile(r"^K\d+$")
_REUSE = ("reuse", "extend", "refactor", "replace", "none-exists")
_IMPACT = ("none", "amend", "redesign")


def _added_invariants(design_updates) -> set:
    return {str(u.get("id")) for u in _list(design_updates) if isinstance(u, dict)
            and _INV_ID.match(str(u.get("id") or "")) and _text(u.get("statement"))
            and _text(u.get("why_missed"), 10)}


def fixing_spec_errors(spec, items, plan: "dict | None", prior_clusters,
                       known_ids=frozenset()) -> "list[str]":
    """FIXING_SPEC rules (module docstring, 4). ``items``: the round's open items
    (core.gate2_fixspec.round_items); ``prior_clusters``: the clusters of this cycle's
    earlier accepted specs; ``known_ids``: every ledger id (non-blocking entries may be
    listed in a cluster too)."""
    if not isinstance(spec, dict):
        return ["Fixing spec: the spec must be a JSON object (template: design_spec.FIXSPEC_TEMPLATE)"]
    d = (plan or {}).get("design") if isinstance((plan or {}).get("design"), dict) else {}
    known_inv = set(_ids(d.get("invariants"), _INV_ID)) | _added_invariants(spec.get("design_updates"))
    open_ids = {str(i.get("id")) for i in items or [] if isinstance(i, dict)}
    by_id = {str(i.get("id")): i for i in items or [] if isinstance(i, dict)}
    prior_ids = {str(c.get("id")) for c in _list(prior_clusters) if isinstance(c, dict)}
    clusters = [c for c in _list(spec.get("clusters")) if isinstance(c, dict)]
    errs: list[str] = []
    where: dict[str, list[str]] = {}
    seen_k: set[str] = set()
    for c in clusters:
        cid = str(c.get("id") or "")
        tag = f"cluster {cid or '?'}"
        if not _K_ID.match(cid) or cid in seen_k:
            errs.append(f"Fixing spec: {tag} needs a unique id K<n>")
        elif cid in prior_ids:
            errs.append(f"Fixing spec: {tag} is already the id of an accepted cluster of this "
                        "cycle: give a new cluster a new id (a revision names the old one in "
                        "revises)")
        seen_k.add(cid)
        fids = [str(x) for x in _list(c.get("findings"))]
        if not fids:
            errs.append(f"Fixing spec: {tag} lists no findings")
        for fid in fids:
            where.setdefault(fid, []).append(cid)
            if fid not in open_ids and fid not in set(known_ids):
                errs.append(f"Fixing spec: {tag} names {fid}, which is not an open item of this "
                            "round nor a ledger entry — use the ids gate2-fixspec printed")
            it = by_id.get(fid)
            if it and it.get("kind") == "recheck":
                rv = str(c.get("revises") or "")
                if it.get("cluster") == "refuted":
                    # the reviewer says the refuted finding IS a bug: fix it in a new
                    # cluster (no accepted cluster owned it, so there is nothing to revise)
                    if rv and rv not in prior_ids:
                        errs.append(f"Fixing spec: {tag} revises {rv}, which is no accepted "
                                    "cluster of this cycle")
                    continue
                want = str(it.get("cluster") or "")
                if not rv or rv not in prior_ids or (want and rv != want):
                    errs.append(f"Fixing spec: {tag} holds the NOT RESOLVED re-check {fid}, so it must "
                                f"revise the cluster it re-checked ({want or 'an earlier accepted K<n>'}): "
                                "set revises, a revised solution and a re-grepped scope")
        cause = c.get("cause") if isinstance(c.get("cause"), dict) else {}
        if str(cause.get("invariant") or "") not in known_inv:
            errs.append(f"Fixing spec: {tag} cause.invariant {cause.get('invariant')!r} is no design "
                        "invariant — name the I<n> it violates, or add it to design_updates "
                        "[{id, statement, why_missed}]")
        if not _text(cause.get("why"), 20):
            errs.append(f"Fixing spec: {tag} cause.why (>= 20 chars): why the design let it happen")
        scope = c.get("scope") if isinstance(c.get("scope"), dict) else {}
        if not [x for x in _list(scope.get("sites")) if str(x).strip()]:
            errs.append(f"Fixing spec: {tag} scope.sites lists no site — every place the cause lives")
        if not _text(scope.get("evidence"), 20):
            errs.append(f"Fixing spec: {tag} scope.evidence (>= 20 chars): the grep / trace you RAN to find "
                        "every site")
        if not _text(c.get("solution"), 20):
            errs.append(f"Fixing spec: {tag} solution (>= 20 chars): one solution for the whole cluster")
        reuse = c.get("reuse") if isinstance(c.get("reuse"), dict) else {}
        dec = str(reuse.get("decision") or "")
        if dec not in _REUSE:
            errs.append(f"Fixing spec: {tag} reuse.decision must be one of {', '.join(_REUSE)}")
        elif dec == "none-exists" and not _text(reuse.get("why"), 30):
            errs.append(f"Fixing spec: {tag} reuse none-exists needs why (>= 30 chars): what you searched")
        if not _text(c.get("anti_patch"), 20):
            errs.append(f"Fixing spec: {tag} anti_patch (>= 20 chars): why this is not a patch on the symptom")
        if str(c.get("design_impact") or "") not in _IMPACT:
            errs.append(f"Fixing spec: {tag} design_impact must be none, amend or redesign")
        elif c.get("design_impact") == "amend" and not _added_invariants(spec.get("design_updates")):
            errs.append(f"Fixing spec: {tag} design_impact amend needs design_updates "
                        "[{id, statement, why_missed}]")
        if not _TEST_REF.match(str(c.get("break_test") or "")):
            errs.append(f"Fixing spec: {tag} break_test 'path::test' proving the class is closed")
    for x in _list(spec.get("refuted")):
        fid = str(x.get("id") if isinstance(x, dict) else x)
        it = by_id.get(fid, {})
        if not (it.get("kind") == "finding" or (it.get("kind") == "recheck"
                                                 and it.get("cluster") == "refuted")):
            errs.append(f"Fixing spec: refuted {fid} is not a blocking finding of this round (a NOT "
                        "RESOLVED re-check of a cluster is answered by a cluster that revises it; "
                        "only the re-check of a refuted finding may be refuted again)")
        elif not _text(x.get("evidence") if isinstance(x, dict) else "", 20):
            errs.append(f"Fixing spec: refuted {fid} needs evidence (>= 20 chars): the line that "
                        "shows it is not a bug")
        where.setdefault(fid, []).append("refuted")
    for oid in sorted(open_ids):
        if oid not in where:
            errs.append(f"Fixing spec: open item {oid} is not in any cluster — every blocking finding "
                        "and NOT RESOLVED re-check of the round is in exactly one")
    for fid, cs in sorted(where.items()):
        if len(cs) > 1:
            errs.append(f"Fixing spec: {fid} is in more than one cluster ({', '.join(cs)})")
    for x in _list(spec.get("deferred")):
        fid = str(x.get("id") if isinstance(x, dict) else x)
        if fid in open_ids:
            errs.append(f"Fixing spec: {fid} is blocking and cannot be deferred here (only the SOUND "
                        "root-cause path leaves blocking findings open)")
    # degenerate split: more than 2 single-finding clusters, or > 2 on one file
    singles = [c for c in clusters if len(_list(c.get("findings"))) == 1]
    files: dict[str, list[str]] = {}
    for c in singles:
        sites = _list((c.get("scope") or {}).get("sites")) if isinstance(c.get("scope"), dict) else []
        f = str(sites[0]).split("::", 1)[0].split(":", 1)[0] if sites else ""
        files.setdefault(f, []).append(str(c.get("id")))
    suspect: set[str] = set()
    if len(singles) > 2:
        suspect |= {str(c.get("id")) for c in singles}
    for f, ks in files.items():
        if f and len(ks) > 2:
            suspect |= set(ks)
    if suspect:
        said = set()
        for u in _list(spec.get("unrelated")):
            if isinstance(u, dict) and _text(u.get("why"), 20):
                said |= {str(u.get("a")), str(u.get("b"))}
        missing = sorted(suspect - said)
        if missing:
            errs.append("Fixing spec: single-finding clusters " + ", ".join(missing) + " look like one "
                        "cause split up — merge them, or say in unrelated [{a, b, why}] why each pair "
                        "shares no cause")
    return errs
