"""Pipeline v1 check registry and effect routing (v13 §A2.1-§A2.4).

Public symbols:
- ``CHECKS`` — every v13 check with document, effects, stages, variants and profiles.
- ``EXEMPTIONS`` — every non-applicable profile/check pair with a reason.
- ``stage_checks`` / ``land_checks`` / ``audit_checks`` — effect-specific routing
  (``audit``: a failed result is one line in the audit's Quality cell, never a block).
- ``coverage_errors`` — prove each profile/check pair applies or is exempt.
- ``evaluate`` — run a check's pure callable against supplied facts.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from core.run_protocol.lifecycle import PROFILE_STAGES, caps as lifecycle_caps, profiles_where

logger = logging.getLogger(__name__)

EFFECTS = frozenset({"stage", "land", "audit"})
PROFILES = frozenset(PROFILE_STAGES)
Result = tuple[bool, str]


@dataclass(frozen=True)
class CheckContext:
    """Validated inputs from documents and a complete event projection."""

    profile: str
    requirement_variant: str
    design_variant: str | None
    requirements: Mapping[str, Any]
    design: Mapping[str, Any] | None
    events: tuple[Mapping[str, Any], ...]
    authority: Any
    run: Mapping[str, Any]
    decision_documents: Mapping[str, Mapping[str, Any]]
    known_refs: Mapping[str, Mapping[str, Any]]
    expected_repos: frozenset[str]
    existing_files: frozenset[str]


CheckFn = Callable[[CheckContext], Result]


class CheckError(ValueError):
    """The registry, query or supplied check facts violate the protocol."""


def _fact(check_id: str) -> CheckFn:
    """Bind one registry ID to its real pure evaluator."""
    def evaluate(context: CheckContext) -> Result:
        if not isinstance(context, CheckContext):
            raise CheckError(f"{check_id} requires a validated CheckContext")
        return _evaluate_check(check_id, context)
    evaluate.__name__ = f"check_{check_id.lower().replace('-', '_')}"
    return evaluate


@dataclass(frozen=True)
class CheckSpec:
    """One registered check and the effects its failure has."""

    check_id: str
    document: str
    effects: frozenset[str]
    stages: tuple[str, ...]
    variants: frozenset[str]
    profiles: frozenset[str]
    fn: CheckFn


def make_context(*, profile: str, requirement_variant: str,
                 requirements: Mapping[str, Any], design_variant: str | None = None,
                 design: Mapping[str, Any] | None = None,
                 events: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (),
                 authority: Any = None,
                 run: Mapping[str, Any] | None = None,
                 decision_documents: Mapping[str, Mapping[str, Any]] | None = None,
                 known_refs: Mapping[str, Mapping[str, Any]] | None = None,
                 expected_repos: set[str] | frozenset[str] = frozenset(),
                 existing_files: set[str] | frozenset[str] = frozenset()) -> CheckContext:
    """Build a CheckContext only after its event chain and profile are valid."""
    from core.run_protocol import events as event_protocol

    if profile not in PROFILES:
        raise CheckError(f"unknown profile {profile!r}")
    if not isinstance(authority, event_protocol.EventAuthority):
        raise CheckError("event authority is required")
    errors = event_protocol.verify_chain(list(events), authority)
    if errors:
        raise CheckError(f"invalid event chain: {errors[0]}")
    if requirement_variant not in ("full", "lite") \
            or design_variant not in (None, "full", "lite"):
        raise CheckError("unknown document variant")
    return CheckContext(
        profile, requirement_variant, design_variant, requirements, design,
        tuple(events), authority, run or {}, decision_documents or {}, known_refs or {},
        frozenset(expected_repos), frozenset(existing_files),
    )


def _c(check_id: str, document: str, effects: str, stages: str,
       variants: str, profiles: str) -> CheckSpec:
    return CheckSpec(
        check_id, document, frozenset(effects.split()) if effects else frozenset(),
        tuple(stages.split()), frozenset(variants.split()), frozenset(profiles.split()),
        _fact(check_id),
    )


CODE = profiles_where(lambda c: c.lands_code)
FULL_REQ = profiles_where(lambda c: c.requirements == "full")
ALL = profiles_where(lambda c: True)
# Profiles that write REQUIREMENTS / DESIGN_SPEC. direct writes neither (execution plan
# v2 N1): its requirement is the user's words in contract.json, it has no exit criteria.
DOCS = FULL_REQ
DESIGN = profiles_where(lambda c: c.design is not None)


_SPECS = [
    _c("RQ-1", "requirements", "stage", "evaluate plan", "full lite", DOCS),
    _c("RQ-2", "requirements", "stage", "evaluate plan", "full lite", DOCS),
    _c("RQ-4", "requirements", "stage", "evaluate", "full", profiles_where(lambda c: c.triad)),
    _c("RQ-5", "requirements", "stage", "evaluate", "full", FULL_REQ),
    _c("RQ-6", "requirements", "stage", "evaluate", "full", FULL_REQ),
    _c("RQ-8", "requirements", "stage", "evaluate plan", "full lite", DOCS),

    _c("DS-1", "design_spec", "audit", "plan", "full lite", DESIGN),
    _c("DS-2a", "design_spec", "stage", "plan", "full lite", DESIGN),
    _c("DS-3", "design_spec", "", "plan", "full lite", DESIGN),
    _c("DS-4a", "design_spec", "stage", "plan", "full", profiles_where(lambda c: c.triad)),
    _c("DS-5", "design_spec", "stage", "plan", "full lite", DESIGN),
    _c("DS-6", "design_spec", "stage", "plan", "full", profiles_where(lambda c: c.design == "full")),
    _c("DS-7", "design_spec", "stage", "plan", "full lite", DESIGN),
    _c("DS-9a", "design_spec", "stage", "plan", "full lite", DESIGN),
    # S6-1: BUILD ends with the agent's validation report on the current code tree
    _c("BX-3", "validation_report", "stage", "build", "run", CODE),

    _c("RV-1a", "review", "audit", "review", "review", CODE),
    _c("RV-1b", "review", "", "evaluate", "review", profiles_where(lambda c: c.gate0 != "never")),
    _c("RV-1c", "review", "", "review", "review", CODE),
    _c("DC-1", "decision", "", "evaluate think plan", "decision", DOCS),
    _c("DC-2", "decision", "audit", "evaluate think plan", "decision", DOCS),
    _c("RV-2", "review", "audit", "review", "review", CODE),
    # S6-2: REVIEW completes only on code a review round saw
    _c("RV-4", "review", "stage", "review", "review", CODE),
    # every addressed finding went to an R2 handout (review-closure §2.3 point 7)
    _c("RV-5", "review", "stage", "review", "review", CODE),
    _c("FX-1", "fixing_spec", "", "review", "review", CODE),
    # how much of its prompt each expert read, from its transcript (audit only, design
    # 2026-11-01-pipeline-delivery-friction D2): an incomplete review is said, never blocked
    _c("RV-6", "review", "audit", "review", "review", CODE),
]
_SPECS += [
    _c("TS-1", "validation_report", "stage audit", "test", "test", CODE),
    _c("TS-3", "test_report", "", "test", "test", DESIGN),
    # every DESIGN_SPEC test reference names a file of the tested code (DS-2a also
    # accepted a file the plan said it would create; nothing checked it was)
    _c("TS-4", "test_report", "stage", "test", "test", DESIGN),
    _c("DV-1", "report", "stage", "deliver", "report", ALL),
    _c("LG-1", "final", "land", "land run_land", "final", CODE),
    _c("LG-3", "report", "land", "land run_land", "report", CODE),
    _c("LG-4", "final", "land", "land run_land", "final", CODE),
    _c("RF-1", "reflect", "stage", "deliver", "reflect", ALL),
    _c("RF-2", "reflect", "stage", "deliver", "reflect", ALL),
    _c("RF-3", "reflect", "stage", "deliver", "reflect", ALL),
    _c("RF-4", "reflect", "stage", "deliver", "reflect", ALL),
    _c("RF-5", "reflect", "", "reflect", "reflect", ALL),
]

CHECKS: dict[str, CheckSpec] = {spec.check_id: spec for spec in _SPECS}
if len(CHECKS) != len(_SPECS):
    raise RuntimeError("duplicate pipeline check id")

# Reasons are intentionally explicit and reviewable. The table covers every profile
# outside each spec's ``profiles``; optional DS-2 checks are still registered for every
# code profile and their evaluator returns pass when the optional collection is absent.
def _exemption_reason(profile: str, check_id: str) -> str:
    if check_id in ("RQ-4", "DS-4a"):
        return "profile is not bugfix and has no current/expected/unchanged triad"
    caps = lifecycle_caps(profile)
    if caps.requirements == "words" and (check_id.startswith(("RQ-", "DS-", "DC-"))
                                or check_id in ("TS-3", "TS-4")):
        return ("direct writes no REQUIREMENTS / DESIGN_SPEC and has no exit criteria; its "
                "requirement is the user's words in contract.json, an ambiguous one is "
                "abandoned and re-run as trivial (execution plan v2 N1)")
    if not caps.lands_code:
        return "research produces no code, design spec, review, tests or landing"
    if check_id in ("DS-6",) and caps.design != "full":
        return "lite DESIGN_SPEC has no design or states section"
    if check_id == "RV-1b" and caps.gate0 == "never":
        return "profile does not run Gate-0"
    return "check does not apply to this profile's documents or stage sequence"


EXEMPTIONS: dict[tuple[str, str], str] = {
    (profile, spec.check_id): _exemption_reason(profile, spec.check_id)
    for spec in _SPECS for profile in sorted(PROFILES - spec.profiles)
}


def _for(profile: str, effect: str | None = None) -> list[CheckSpec]:
    if profile not in PROFILES:
        raise CheckError(f"unknown profile {profile!r}")
    if effect is not None and effect not in EFFECTS:
        raise CheckError(f"unknown effect {effect!r}")
    return [spec for spec in _SPECS if profile in spec.profiles
            and (effect is None or effect in spec.effects)]


def stage_checks(profile: str, stage: str, variant: str | None = None) -> tuple[CheckSpec, ...]:
    """Checks whose ``stage`` effect guards this stage; land-only never appears."""
    if profile not in PROFILES:
        raise CheckError(f"unknown profile {profile!r}")
    if stage not in PROFILE_STAGES[profile]:
        raise CheckError(f"stage {stage!r} is not in profile {profile!r}")
    return tuple(spec for spec in _for(profile, "stage") if stage in _stages_for(spec, profile)
                 and (variant is None or variant in spec.variants))


def _stages_for(spec: CheckSpec, profile: str) -> tuple[str, ...]:
    if spec.check_id.startswith("RQ-"):
        return ("evaluate",)
    if spec.check_id == "BX-3":
        return ("build",)
    return tuple(stage for stage in spec.stages
                 if stage in PROFILE_STAGES[profile] or stage in ("land", "run_land"))


def land_checks(profile: str) -> tuple[CheckSpec, ...]:
    """Checks evaluated only by land/run-land, never by stage-complete."""
    return tuple(_for(profile, "land"))


def audit_checks(profile: str) -> tuple[CheckSpec, ...]:
    """Checks whose failed result is noted in the audit (Quality), never a block."""
    return tuple(_for(profile, "audit"))


def evaluate(check_id: str, context: CheckContext) -> Result:
    """Run one registered pure evaluator over a validated context."""
    try:
        spec = CHECKS[check_id]
    except KeyError as exc:
        raise CheckError(f"unknown check {check_id!r}") from exc
    return spec.fn(context)


def coverage_errors() -> list[str]:
    """Return registry/exemption errors across every profile × check combination."""
    errors: list[str] = []
    for spec in _SPECS:
        if not spec.effects <= EFFECTS:
            errors.append(f"{spec.check_id}: invalid effect")
        if not spec.profiles <= PROFILES:
            errors.append(f"{spec.check_id}: invalid profile")
        if not spec.variants:
            errors.append(f"{spec.check_id}: no variants")
        for profile in spec.profiles:
            stages = _stages_for(spec, profile)
            if not stages:
                errors.append(f"{profile}/{spec.check_id}: no execution stage")
            bad_stages = set(stages) - set(PROFILE_STAGES[profile]) - {"land", "run_land"}
            if bad_stages:
                errors.append(f"{profile}/{spec.check_id}: invalid stages {sorted(bad_stages)}")
        if not callable(spec.fn):
            errors.append(f"{spec.check_id}: fn is not callable")
        for profile in PROFILES:
            applies = profile in spec.profiles
            exempt = (profile, spec.check_id) in EXEMPTIONS
            if applies == exempt:
                errors.append(
                    f"{profile}/{spec.check_id}: must be applicable xor explicitly exempt"
                )
            if exempt and not EXEMPTIONS[(profile, spec.check_id)].strip():
                errors.append(f"{profile}/{spec.check_id}: empty exemption reason")
    unknown = set(EXEMPTIONS) - {
        (profile, spec.check_id) for profile in PROFILES for spec in _SPECS
    }
    errors += [f"unknown exemption {item!r}" for item in sorted(unknown)]
    return errors


def _schema_issues(context: CheckContext):
    from core.run_protocol import schemas

    if context.design is None or context.design_variant is None:
        return schemas.validate_requirements(
            context.requirements, variant=context.requirement_variant, profile=context.profile,
        )
    return schemas.validate_contract(
        context.requirements, context.design,
        requirement_variant=context.requirement_variant,
        design_variant=context.design_variant, profile=context.profile,
        existing_files=set(context.existing_files),
    )


def _result(ok: bool, detail: str) -> Result:
    return bool(ok), detail


def _document_check(check_id: str, context: CheckContext) -> Result:
    failures = [issue for issue in _schema_issues(context) if issue.check_id == check_id]
    return _result(not failures, "passed" if not failures else "; ".join(
        f"{issue.path}: {issue.message}" for issue in failures[:10]
    ))


# LG-1's detail when a TEST report stood across a main move (design 2026-11-01-
# pipeline-delivery-friction E2); the audit lists those landings in the REPORT
CARRIED_MARK = "tests carried over a main move:"
# LG-1's detail when the change at landing differs from the reviewed one after the run's
# one DELIVER reopen (design 2026-11-01-pipeline-delivery-friction N1)
UNREVIEWED_MARK = "landed without review of:"


def _report_check(check_id: str, context: CheckContext) -> Result:
    """BX-3 / TS-1 / LG-1 (S6-1): for every expected repo, the latest validation report
    of BUILD (BX-3) or TEST (TS-1, LG-1) is on the repo's current code tree; TS-1 and
    LG-1 also refuse a TEST report with a failed command (the agent reported a failure,
    so the change is not delivered). LG-1 also accepts the report a landing carried over
    a main move (``run["carried_test_trees"]``, set by the engine only when main's new
    commits change none of the run's files and the rebase kept the run's patch)."""
    from core.run_protocol.validation_report import latest
    stage = "test" if check_id in ("TS-1", "LG-1") else (
        "build")
    current = context.run.get("code_trees")
    expected = sorted(context.expected_repos)
    if not expected or not isinstance(current, Mapping):
        return False, "the run's code trees are unknown"
    if check_id == "BX-3" and not context.run.get("changed_paths"):
        return False, "no change in the run's worktree"
    reports = latest(context.events, stage)
    carried = (context.run.get("carried_test_trees") or {}) if check_id == "LG-1" else {}
    problems = []
    for repo in expected:
        event = reports.get(repo)
        if event is None:
            problems.append(f"{repo}: no {stage} validation report; run your tests and pass "
                            'the report as {"reports": [...]} with --artifact -')
            continue
        payload = event["payload"]
        on_tree = bool(current.get(repo)) and payload.get("code_tree") == current.get(repo)
        if not on_tree and not (payload.get("code_tree") and carried.get(repo) == payload["code_tree"]):
            problems.append(f"{repo}: the {stage} report is for tree "
                            f"{str(payload.get('code_tree'))[:12]}, the code is now "
                            f"{str(current.get(repo))[:12]}; test again and report")
        elif stage == "test" and payload.get("n_fail"):
            problems.append(f"{repo}: the TEST report has {payload['n_fail']} failed "
                            "command(s); fix them and report again")
    if check_id == "LG-1" and not problems:
        unreviewed = _unreviewed_landing(context, expected)
        from core.run_protocol.lifecycle import DELIVER_REOPEN_CAP
        if unreviewed and unreviewed[0].startswith(NEVER_REVIEWED) \
                and int(context.run.get("deliver_reopens") or 0) >= DELIVER_REOPEN_CAP:
            # the run already went back to REVIEW once from DELIVER: what differs since
            # lands, said in the REPORT (design 2026-11-01-pipeline-delivery-friction N1;
            # SOUL P5: a review that did not run is noted, never what stops the landing)
            return True, (f"{stage} reports bind the current code; {UNREVIEWED_MARK} the "
                          "change differs from the reviewed one (changed in DELIVER after "
                          "the run's one incremental review from DELIVER)")
        problems += unreviewed
    return _result(not problems, f"{stage} reports bind the current code" if not problems
                   else "; ".join(problems))


def _unreviewed_landing(context: CheckContext, expected: list[str]) -> list[str]:
    """LG-1 (S6-2): the change that lands is one REVIEW completed. Compared by patch id,
    so a clean rebase onto a moved main keeps the binding and any edit breaks it."""
    current = context.run.get("patch_ids")
    reviewed = context.run.get("reviewed_patches") or []
    if not isinstance(current, Mapping) or any(repo not in current for repo in expected):
        return ["the run's patch is unknown"]
    if any(all(row.get(repo) == current[repo] for repo in expected)
           for row in reviewed if isinstance(row, Mapping)):
        return []
    return [NEVER_REVIEWED + ": REVIEW completed on other code (the code changed after "
            "REVIEW); it cannot land"]


NEVER_REVIEWED = "this change was never reviewed"


def _answers(context: CheckContext, expert: str | None = None) -> list[Mapping[str, Any]]:
    """The experts' answer events (cross-run answer reuse is not implemented: fixing
    log §15, conformance Q3). A parsed answer counts only when every finding and status
    line it declares is on the ledger too: an answer whose findings did not all reach
    disk could otherwise pass a review that hides them (fails closed; r6 §2.2)."""
    children: dict[tuple[str, str], int] = {}
    for event in context.events:
        if event.get("kind") in ("finding_occ", "finding_status"):
            key = (event["payload"].get("answer_event_id"), event["kind"])
            children[key] = children.get(key, 0) + 1

    def complete(event: Mapping[str, Any]) -> bool:
        payload = event["payload"]
        if payload.get("state") not in ("parsed", "partial"):
            return True
        return (children.get((event["event_id"], "finding_occ"), 0) == payload.get("n_findings", 0)
                and children.get((event["event_id"], "finding_status"), 0)
                == payload.get("n_status", 0))
    return [event for event in context.events if event.get("kind") == "answer"
            and (expert is None or event["payload"].get("expert_id") == expert)
            and complete(event)]


def _evaluate_check(check_id: str, context: CheckContext) -> Result:
    schema_ids = {cid for cid in CHECKS if cid.startswith(("RQ-", "DS-"))}
    if check_id in schema_ids:
        return _document_check(check_id, context)
    if check_id in ("BX-3", "TS-1", "LG-1"):
        return _report_check(check_id, context)
    if check_id == "RV-1a":
        handouts = [event for event in context.events if event.get("kind") == "handout"
                    and event["payload"].get("expert_id") == "correctness"
                    and event.get("stage") == "review"]
        if not handouts:
            return True, "correctness not dispatched by lightweight roster"
        rows = _answers(context, "correctness")
        ok = len(rows) >= len(handouts) and any(
            row["payload"]["state"] == "parsed" for row in rows)
        return _result(ok, "correctness answer parsed" if ok
                       else "correctness handout has no parsed answer")
    if check_id == "RV-1b":
        handouts = [event for event in context.events if event.get("kind") == "handout"
                    and event["payload"].get("expert_id") == "gate0-skeptic"]
        if not handouts:
            return True, "Gate-0 not dispatched"
        rows = _answers(context, "gate0-skeptic")
        ok = len(rows) >= len(handouts) and all(
            row["payload"]["state"] == "parsed" for row in rows[:len(handouts)]
        )
        return _result(ok, "Gate-0 handouts have parsed answers" if ok
                       else "Gate-0 handout lacks parsed answer")
    if check_id == "RV-5":
        from core.run_protocol.review import uncovered_addresses
        missing = uncovered_addresses(list(context.events))
        return _result(not missing, "every addressed finding went to R2" if not missing
                       else "addressed without an R2 handout (review-address with no items "
                            f"dispatches it): {missing}")
    if check_id == "RV-6":
        from core.run_protocol.review import coverage_rows
        short = [row for row in coverage_rows(context.events) if not row["complete"]]
        return _result(not short, "every expert read its whole prompt within its cap"
                       if not short else "review coverage: " + "; ".join(
                           f"{row['expert']} r{row['round']} read {row['read_pct']}% of its "
                           "prompt" + (f", stopped at its {row['cap']}-call cap"
                                       if row["budget_hit"] else "")
                           for row in short[:6]))
    if check_id == "RV-4":
        unreviewed = context.run.get("unreviewed") or {}
        if not isinstance(unreviewed, Mapping):
            return False, "what the last review saw is unknown"
        rows = [f"{repo}: {', '.join(paths[:8])}" + (" …" if len(paths) > 8 else "")
                for repo, paths in sorted(unreviewed.items()) if paths]
        return _result(not rows, "the code is what the last review saw" if not rows
                       else "changed since the last review (run review-start for an "
                            "incremental review): " + "; ".join(rows))
    if check_id == "RV-1c":
        handouts = {event["payload"]["expert_id"] for event in context.events
                    if event.get("kind") == "handout" and event.get("stage") == "review"
                    and event["payload"]["expert_id"] != "correctness"}
        parsed = {event["payload"]["expert_id"] for event in _answers(context)
                  if event["payload"]["state"] == "parsed"}
        return _result(handouts <= parsed, "other review answers recorded")
    if check_id == "DC-1":
        from core.run_protocol import decision
        asked = {event["payload"]["request_id"] for event in context.events
                 if event.get("kind") == "decision"}
        unrecorded = sorted(asked - set(context.decision_documents))
        if unrecorded:     # the event carries no decision to validate (pre-K1 log)
            return False, f"decision content not in its event: {unrecorded}"
        issues = [issue for doc in context.decision_documents.values()
                  for issue in decision.validate(doc, context.known_refs)]
        return _result(not issues, "decisions valid" if not issues else issues[0].message)
    if check_id == "DC-2":
        return _result(not context.run.get("agent_after_cap"),
                       "no agent-after-cap choice" if not context.run.get("agent_after_cap")
                       else "agent chose after decision cap")
    if check_id in ("RV-2", "FX-1"):
        from core.run_protocol import findings
        projected = findings.fold(list(context.events), context.authority)
        if check_id == "RV-2":
            stale = set(context.run.get("stale_closures") or ())
            blocking = [row["canonical_id"] for row in projected if findings.push_effect(row)
                        or (row["canonical_id"] in stale and findings.normalize_severity(
                            row.get("severity")) in ("HIGH", "CRITICAL"))]
            return _result(not blocking, "no open HIGH finding" if not blocking
                           else f"open HIGH findings: {blocking}")
        clustered = {cid for cluster in findings.cluster_findings(projected)
                     for cid in cluster["findings"]}
        expected = {row["canonical_id"] for row in projected if row["status"] != "refuted"}
        return _result(clustered == expected, "every finding clustered or refuted")
    if check_id == "TS-3":
        required = {row.get("id") for req in context.requirements.get("requirements", [])
                    if isinstance(req, Mapping) for row in req.get("acceptance", [])
                    if isinstance(row, Mapping)}
        # §A2.3: the trace covers every acceptance R, property P and failure mode F
        for key in ("properties", "failure_modes"):
            required |= {row.get("id") for row in (context.design or {}).get(key, [])
                         if isinstance(row, Mapping) and isinstance(row.get("id"), str)}
        # the agent's declared trace: which exit criteria cover what (S6-1: no runner
        # result behind it; Gate-2 reviews it)
        covers = {c for row in (context.design or {}).get("exit_criteria", [])
                  if isinstance(row, Mapping) for c in (row.get("covers") or [])}
        missing = sorted(i for i in required - covers if isinstance(i, str))
        return _result(not missing, "trace covers requirements" if not missing
                       else f"trace missing: {missing}")
    if check_id == "TS-4":
        from core.run_protocol import schemas
        known = set(context.existing_files)
        missing = [f"{path} {ref!r}" for path, ref in schemas.test_refs(context.design or {})
                   if schemas.resolve_test_ref(ref, known) is None]
        return _result(not missing, "every test reference names a file of the tested code"
                       if not missing else "test references that name no file of the "
                       f"tested code (create the test or fix the reference): {missing[:10]}")
    if check_id == "DV-1":
        ok = any(event.get("kind") == "render" and event["payload"].get("document") == "REPORT"
                 for event in context.events)
        return _result(ok, "REPORT render event exists" if ok else "REPORT not rendered")
    if check_id == "LG-3":
        from core.run_protocol import tree
        args = context.run.get("lg3_args")
        if not isinstance(args, Mapping):
            return False, "LG-3 tree inputs missing"
        try:
            ok, details = tree.check_all_lg3(**args)
            return ok, "; ".join(f"{repo}: {detail}" for repo, detail in details.items())
        except (TypeError, tree.TreeError) as exc:
            return False, f"LG-3 failed: {exc}"
    if check_id == "LG-4":
        landing_bases = context.run.get("landing_bases")
        run_bases = context.run.get("run_bases")
        # the commit is a fast-forward of the reviewed and tested code: main is still at
        # the base the run's worktree is on (a moved main is synced first, S6-1)
        ok = bool(context.expected_repos) and isinstance(landing_bases, Mapping) \
            and isinstance(run_bases, Mapping) \
            and set(landing_bases) == set(context.expected_repos) \
            and all(isinstance(run_bases.get(n), str) and run_bases[n]
                    and landing_bases[n] == run_bases[n] for n in context.expected_repos)
        return _result(ok, "main is still at the run's base" if ok else
                       "main moved since the run's base; sync and test again first")
    if check_id.startswith("RF-"):
        report = context.run.get("reflect_report")
        if check_id == "RF-5":
            return _result(True, "CDF records source outcomes in ingestion audit")
        try:
            from core.run_protocol import reflect
            anchors = {str(anchor) for rows in report.values() for row in rows
                       for anchor in row.get("evidence", [])}
            normalized = reflect.prepare(report, allowed_anchors=anchors)
            ok = normalized["schema"] == reflect.SCHEMA
        except Exception as exc:
            logger.warning("REFLECT validation failed for %s: %s", check_id, exc)
            ok = False
        return _result(ok, f"{check_id} {'passed' if ok else 'failed'}")
    raise CheckError(f"no evaluator for {check_id}")
