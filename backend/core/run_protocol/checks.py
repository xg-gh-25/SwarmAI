"""Pipeline v1 check registry and effect routing (v13 §A2.1-§A2.4).

Public symbols:
- ``CHECKS`` — every v13 check with document, effects, stages, variants and profiles.
- ``EXEMPTIONS`` — every non-applicable profile/check pair with a reason.
- ``stage_checks`` / ``land_checks`` / ``push_checks`` — effect-specific routing.
- ``coverage_errors`` — prove each profile/check pair applies or is exempt.
- ``evaluate`` — run a check's pure callable against supplied facts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from core.run_protocol.lifecycle import PROFILE_STAGES

EFFECTS = frozenset({"stage", "land", "push"})
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
    final: Mapping[str, Any]
    run: Mapping[str, Any]
    collected_tests: frozenset[str]
    test_results: Mapping[str, str]
    trace_covers: frozenset[str]
    decision_documents: Mapping[str, Mapping[str, Any]]
    known_refs: Mapping[str, Mapping[str, Any]]
    expected_repos: frozenset[str]
    validation_manifest: Mapping[str, frozenset[str]]
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
                 final: Mapping[str, Any] | None = None,
                 run: Mapping[str, Any] | None = None,
                 collected_tests: set[str] | frozenset[str] = frozenset(),
                 test_results: Mapping[str, str] | None = None,
                 trace_covers: set[str] | frozenset[str] = frozenset(),
                 decision_documents: Mapping[str, Mapping[str, Any]] | None = None,
                 known_refs: Mapping[str, Mapping[str, Any]] | None = None,
                 expected_repos: set[str] | frozenset[str] = frozenset(),
                 validation_manifest: Mapping[str, set[str] | frozenset[str]] | None = None,
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
        tuple(events), authority, final or {}, run or {}, frozenset(collected_tests),
        test_results or {}, frozenset(trace_covers), decision_documents or {}, known_refs or {},
        frozenset(expected_repos),
        {repo: frozenset(ids) for repo, ids in (validation_manifest or {}).items()},
        frozenset(existing_files),
    )


def _c(check_id: str, document: str, effects: str, stages: str,
       variants: str, profiles: str) -> CheckSpec:
    return CheckSpec(
        check_id, document, frozenset(effects.split()) if effects else frozenset(),
        tuple(stages.split()), frozenset(variants.split()), frozenset(profiles.split()),
        _fact(check_id),
    )


CODE = "full bugfix goal trivial direct"
FULL_REQ = "full bugfix goal trivial research"
ALL = "full bugfix goal trivial direct research"
# Profiles that write REQUIREMENTS / DESIGN_SPEC. direct writes neither (execution plan
# v2 N1): its requirement is the user's words in contract.json, it has no exit criteria.
DOCS = FULL_REQ
DESIGN = "full bugfix goal trivial"


_SPECS = [
    _c("RQ-1", "requirements", "stage", "evaluate plan", "full lite", DOCS),
    _c("RQ-2", "requirements", "stage", "evaluate plan", "full lite", DOCS),
    _c("RQ-3", "requirements", "", "evaluate plan", "full lite", DOCS),
    _c("RQ-4", "requirements", "stage", "evaluate", "full", "bugfix"),
    _c("RQ-5", "requirements", "stage", "evaluate", "full", FULL_REQ),
    _c("RQ-6", "requirements", "stage", "evaluate", "full", FULL_REQ),
    _c("RQ-8", "requirements", "stage", "evaluate plan", "full lite", DOCS),
    _c("RQ-8b", "requirements", "", "evaluate plan", "full lite", DOCS),

    _c("DS-1", "design_spec", "push", "plan", "full lite", DESIGN),
    _c("DS-2a", "design_spec", "stage", "plan", "full lite", DESIGN),
    _c("DS-2b", "design_spec", "stage", "build goal_cycle", "full lite", DESIGN),
    _c("DS-3", "design_spec", "", "plan", "full lite", DESIGN),
    _c("DS-4a", "design_spec", "stage", "plan", "full", "bugfix"),
    _c("DS-4b", "design_spec", "stage", "build", "full", "bugfix"),
    _c("DS-5", "design_spec", "stage", "plan", "full lite", DESIGN),
    _c("DS-6", "design_spec", "stage", "plan", "full", "full bugfix goal"),
    _c("DS-7", "design_spec", "stage", "plan", "full lite", DESIGN),
    _c("DS-9a", "design_spec", "stage", "plan", "full lite", DESIGN),
    _c("DS-9b", "design_spec", "stage", "build goal_cycle review", "full lite", DESIGN),
    _c("BX-1", "design_spec", "stage", "build goal_cycle", "full lite", DESIGN),
    _c("BX-2", "final", "stage", "build", "run", "direct"),

    _c("RV-1a", "review", "push", "review", "review", CODE),
    _c("RV-1b", "review", "", "evaluate", "review", "full bugfix goal"),
    _c("RV-1c", "review", "", "review", "review", CODE),
    _c("DC-1", "decision", "", "evaluate think plan", "decision", DOCS),
    _c("DC-2", "decision", "push", "evaluate think plan", "decision", DOCS),
    _c("RV-2", "review", "push", "review", "review", CODE),
    _c("FX-1", "fixing_spec", "", "review", "review", CODE),
]
_SPECS += [
    _c("TS-1", "test_report", "stage", "test", "test", CODE),
    _c("TS-2", "test_report", "push", "test", "test", DESIGN),
    _c("TS-3", "test_report", "", "test", "test", DESIGN),
    _c("DV-1", "report", "stage", "deliver", "report", ALL),
    _c("LG-1", "final", "land", "land run_land", "final", CODE),
    _c("LG-2", "final", "land push", "land run_land", "final", CODE),
    _c("LG-3", "report", "land", "land run_land", "report", CODE),
    _c("LG-4", "final", "land", "land run_land", "final", CODE),
    _c("LG-5", "final", "land push", "land run_land", "final", CODE),
    _c("QX-1", "test_report", "push", "test", "test", CODE),
    _c("QX-2", "test_report", "push", "test", "test", CODE),
    _c("MD-1", "run", "push", "deliver", "run", CODE),
    _c("MD-2", "final", "push", "deliver", "final", CODE),
    _c("MD-3", "authorization", "push", "run_land", "authorization", CODE),
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
    if check_id in ("RQ-4", "DS-4a", "DS-4b"):
        return "profile is not bugfix and has no current/expected/unchanged triad"
    if profile == "direct" and (check_id.startswith(("RQ-", "DS-", "DC-"))
                                or check_id in ("BX-1", "TS-2", "TS-3")):
        return ("direct writes no REQUIREMENTS / DESIGN_SPEC and has no exit criteria; its "
                "requirement is the user's words in contract.json, an ambiguous one is "
                "abandoned and re-run as trivial (execution plan v2 N1)")
    if check_id == "BX-2":
        return "BX-2 is direct's change/tdd check; other profiles prove BUILD by BX-1"
    if profile == "research":
        return "research produces no code, design spec, review, tests or landing"
    if check_id in ("DS-6",) and profile in ("direct", "trivial"):
        return "lite DESIGN_SPEC has no design or states section"
    if check_id == "RV-1b" and profile in ("direct", "trivial"):
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
    if spec.check_id in ("DS-2b", "BX-1"):
        return ("goal_cycle",) if profile == "goal" else ("build",)
    if spec.check_id == "DS-9b":
        first = "goal_cycle" if profile == "goal" else "build"
        return (first, "review")
    return tuple(stage for stage in spec.stages
                 if stage in PROFILE_STAGES[profile] or stage in ("land", "run_land"))


def land_checks(profile: str) -> tuple[CheckSpec, ...]:
    """Checks evaluated only by land/run-land, never by stage-complete."""
    return tuple(_for(profile, "land"))


def push_checks(profile: str) -> tuple[CheckSpec, ...]:
    """Checks whose failed result lowers push_ready."""
    return tuple(_for(profile, "push"))


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


def _test_refs(context: CheckContext, kinds=("test", "mutation")) -> list[str]:
    return [row.get("ref") for row in (context.design or {}).get("exit_criteria", [])
            if isinstance(row, Mapping) and row.get("kind") in kinds
            and isinstance(row.get("ref"), str)]


def _collected(ref: Any, context: CheckContext) -> bool:
    """A design ref is collected when pytest found it, or found it parametrized
    (``ref[...]``): the same rule the runner uses for refs elsewhere."""
    return isinstance(ref, str) and (ref in context.collected_tests or any(
        node.startswith(ref + "[") for node in context.collected_tests))


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


def _bx2(context: CheckContext) -> Result:
    """direct's BUILD check (execution plan v2 N1). The run changed something; and
    unless every changed path is a pure-doc path, each repo has a final on its CURRENT
    code tree whose ``tdd_required`` files (the runner's frozen selection for the
    change: changed tests + direct importers of changed modules) all have results on
    that tree and none of them failed or is incomplete. Facts only from the sealed
    final and the run's own changed paths; no model field."""
    from core.doc_paths import is_doc_only_path
    changed = [p for p in context.run.get("changed_paths") or [] if isinstance(p, str)]
    if not changed:
        return False, "no change in the run's worktree"
    if all(is_doc_only_path(p) for p in changed):
        return True, "pure-doc change: no test to run"
    repos = context.final.get("repos") if isinstance(context.final, Mapping) else None
    trees = context.run.get("code_trees") or {}
    if not isinstance(repos, Mapping) or not context.expected_repos \
            or set(repos) != set(context.expected_repos):
        return False, "no tdd result yet: run pipeline_test.py --mode tdd"
    problems = []
    for name, row in repos.items():
        if not isinstance(row, Mapping) or row.get("code_tree") != trees.get(name):
            problems.append(f"{name}: tdd results are for an older tree; rerun --mode tdd")
            continue
        required = row.get("tdd_required")
        tests = row.get("tests") or {}
        nodes, whole = tests.get("nodes"), tests.get("whole_files")
        if not isinstance(required, list) or not isinstance(nodes, Mapping) \
                or not isinstance(whole, list):
            problems.append(f"{name}: test selection failed or no tdd result; rerun --mode tdd")
            continue
        if any(k.startswith("call:") and (v or {}).get("result") != "pass"
               for k, v in nodes.items() if isinstance(k, str)):
            problems.append(f"{name}: a tdd call did not finish")
        # nothing selected (no importer, non-Python code): a test the agent names must
        # still have run whole and passed, so a code change never leaves BUILD untested
        targets = required or [f for f in whole if any(
            isinstance(k, str) and k.split("::")[0] == f for k in nodes)]
        if not targets:
            problems.append(f"{name}: no test selected for this change; write one and run "
                            "it with --mode tdd --files <test file>")
        for test_file in targets:
            mine = {node: value for node, value in nodes.items()
                    if isinstance(node, str) and node.split("::")[0] == test_file}
            if test_file not in whole:
                problems.append(f"{test_file}: not run whole (run the file, not single nodes)")
            elif not mine:
                problems.append(f"{test_file}: not run")
            elif any((value or {}).get("result") != "pass" for value in mine.values()):
                problems.append(f"{test_file}: failed or incomplete")
    return _result(not problems, "selected tests pass on the current tree" if not problems
                   else "; ".join(problems[:5]))


def _evaluate_check(check_id: str, context: CheckContext) -> Result:
    schema_ids = {cid for cid in CHECKS if cid.startswith(("RQ-", "DS-"))} | {"BX-1"}
    if check_id in schema_ids - {"RQ-3", "RQ-8b"} \
            and check_id not in ("DS-2b", "DS-4b", "DS-9b", "BX-1"):
        return _document_check(check_id, context)
    if check_id == "DS-2b":
        wanted = [row.get("test") for key in ("properties", "failure_modes")
                  for row in (context.design or {}).get(key, []) if isinstance(row, Mapping)]
        missing = sorted({ref for ref in wanted if not _collected(ref, context)})
        return _result(not missing, "all property/failure tests collect" if not missing
                       else f"uncollected tests: {missing}")
    if check_id == "DS-4b":
        unchanged = {row.get("id") for row in ((context.requirements.get("bugfix") or {})
                     .get("unchanged", [])) if isinstance(row, Mapping)}
        refs = [row.get("ref") for row in (context.design or {}).get("exit_criteria", [])
                if isinstance(row, Mapping) and row.get("kind") == "test"
                and unchanged & set(row.get("covers") or [])]
        missing = [ref for ref in refs if not _collected(ref, context)]
        return _result(bool(refs) and not missing,
                       "unchanged regression tests collect" if refs and not missing
                       else f"unchanged tests missing/uncollected: {missing}")
    if check_id in ("DS-9b", "BX-1"):
        exits = [row for row in (context.design or {}).get("exit_criteria", [])
                 if isinstance(row, Mapping) and row.get("kind") in
                 (("test", "probe") if check_id == "DS-9b" else ("test",))]
        wanted = [(row.get("ref") if row.get("kind") == "test" else row.get("id"))
                  for row in exits]
        failed = sorted(ref for ref in wanted if context.test_results.get(ref) != "pass")
        return _result(not failed, "all exit tests pass" if not failed
                       else f"exit tests not passed: {failed}")
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
            return _result(not blocking, "no push-effect finding" if not blocking
                           else f"push-effect findings: {blocking}")
        clustered = {cid for cluster in findings.cluster_findings(projected)
                     for cid in cluster["findings"]}
        expected = {row["canonical_id"] for row in projected if row["status"] != "refuted"}
        return _result(clustered == expected, "every finding clustered or refuted")
    if check_id == "TS-1":
        repos = context.final.get("repos")
        ok = isinstance(repos, Mapping) and bool(context.expected_repos) \
            and set(repos) == set(context.expected_repos) and all(
            isinstance(row, Mapping) and (row.get("tests") or {}).get("verdict")
            in ("green", "red", "incomplete") for row in repos.values()
        )
        return _result(ok, "every repo has terminal final" if ok else "final is incomplete")
    if check_id == "TS-2":
        failed = [ref for ref in _test_refs(context, ("test",))
                  if context.test_results.get(ref) != "pass"]
        return _result(not failed, "all test exits pass" if not failed else f"not passed: {failed}")
    if check_id == "TS-3":
        required = {row.get("id") for req in context.requirements.get("requirements", [])
                    if isinstance(req, Mapping) for row in req.get("acceptance", [])
                    if isinstance(row, Mapping)}
        # §A2.3: the trace covers every acceptance R, property P and failure mode F
        for key in ("properties", "failure_modes"):
            required |= {row.get("id") for row in (context.design or {}).get(key, [])
                         if isinstance(row, Mapping) and isinstance(row.get("id"), str)}
        missing = sorted(i for i in required - context.trace_covers if isinstance(i, str))
        return _result(not missing, "trace covers requirements" if not missing
                       else f"trace missing: {missing}")
    if check_id == "BX-2":
        return _bx2(context)
    if check_id == "DV-1":
        ok = any(event.get("kind") == "render" and event["payload"].get("document") == "REPORT"
                 for event in context.events)
        return _result(ok, "REPORT render event exists" if ok else "REPORT not rendered")
    repos = context.final.get("repos") if isinstance(context.final, Mapping) else None
    if check_id == "LG-1":
        current = context.run.get("code_trees")
        ok = isinstance(repos, Mapping) and bool(context.expected_repos) \
            and set(repos) == set(context.expected_repos) \
            and isinstance(current, Mapping) and set(current) == set(context.expected_repos) and all(
            isinstance(row, Mapping) and row.get("code_tree") == current.get(name)
            for name, row in repos.items())
        return _result(ok, "final binds current code trees" if ok else "; ".join(
            context.run.get("final_unbound") or ["final code tree is stale"]))
    if check_id == "LG-2":
        verdicts = [(row.get("tests") or {}).get("verdict") for row in repos.values()] \
            if isinstance(repos, Mapping) else []
        ok = bool(verdicts) and all(value == "green" for value in verdicts)
        return _result(ok, "final tests green" if ok else f"test verdicts: {verdicts}")
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
        if context.run.get("skip_tests") is True:
            # a skip-tests landing needs no final (C5): main must still be at the
            # run's fork point, so the commit is a fast-forward of what was reviewed
            ok = bool(context.expected_repos) and isinstance(landing_bases, Mapping) \
                and isinstance(run_bases, Mapping) \
                and set(landing_bases) == set(run_bases) == set(context.expected_repos) \
                and all(isinstance(run_bases[n], str) and run_bases[n]
                        and landing_bases[n] == run_bases[n] for n in run_bases)
            return _result(ok, "main is still at the run's base" if ok else
                           "main moved since the run started; restore and rebase first")
        ok = isinstance(repos, Mapping) and bool(context.expected_repos) \
            and set(repos) == set(context.expected_repos) \
            and isinstance(landing_bases, Mapping) \
            and set(landing_bases) == set(context.expected_repos) \
            and all(isinstance(row, Mapping)
                    and isinstance(row.get("base"), str) and bool(row.get("base"))
                    and row["base"] == landing_bases.get(name)
                    for name, row in repos.items())
        return _result(ok, "landing bases match final" if ok else "landing base changed")
    if check_id == "LG-5":
        ok = isinstance(repos, Mapping) and bool(context.expected_repos) \
            and set(repos) == set(context.expected_repos) \
            and set(context.validation_manifest) == set(context.expected_repos)
        if ok:
            for repo, required_ids in context.validation_manifest.items():
                rows = repos[repo].get("validations", []) if isinstance(repos[repo], Mapping) else []
                got = {item.get("id"): item.get("verdict") for item in rows
                       if isinstance(item, Mapping) and isinstance(item.get("id"), str)}
                if set(got) != set(required_ids) or any(got[item] != "pass" for item in required_ids):
                    ok = False
                    break
        return _result(ok, "required validations pass exactly" if ok
                       else "required validation set missing/failing")
    if check_id == "QX-1":
        rows = context.final.get("repos") if isinstance(context.final, Mapping) else None
        statuses = [((row.get("provenance") or {}).get("selection") or {}).get("risk_status")
                    for row in rows.values()] if isinstance(rows, Mapping) else []
        ok = bool(statuses) and all(status == "within_cap" for status in statuses)
        return _result(ok, "risk expansion within cap"
                       if ok else "risk expansion missing/incomplete")
    if check_id == "QX-2":
        violations = context.run.get("rule_violations")
        ok = isinstance(violations, list) and not violations
        return _result(ok, "validation rules and pipeline code unchanged" if ok
                       else "; ".join(violations[:5]) if isinstance(violations, list)
                       else "rule provenance unknown")
    if check_id == "MD-1":
        mode = context.run.get("mode")
        return _result(mode == "isolated", "isolated" if mode == "isolated"
                       else "mode missing or shared")
    if check_id == "MD-2":
        rows = context.final.get("repos") if isinstance(context.final, Mapping) else None
        kinds = []
        if isinstance(rows, Mapping):
            for row in rows.values():
                tests = [call.get("target") for call in row.get("calls", [])
                         if isinstance(call, Mapping) and call.get("call_type") == "test"
                         and call.get("code_tree") == row.get("code_tree")]
                kinds.append("regression" if "regression" in tests else
                             "recheck" if "recheck" in tests else None)
        ok = bool(kinds) and all(kind == "regression" for kind in kinds)
        return _result(ok, "full final" if ok else "final kind missing or repair-only")
    if check_id == "MD-3":
        landing = context.run.get("landing")
        return _result(landing is not None and landing != "human_override",
                       "normal landing" if landing is not None and landing != "human_override"
                       else "landing missing or human override")
    if check_id.startswith("RF-"):
        lessons = context.run.get("lessons")
        if check_id == "RF-5":
            bad = [row for row in lessons or [] if row.get("judge") in ("noise", "suspect")]
            return _result(not bad, "no noisy lesson" if not bad else "judge flagged lesson")
        if not isinstance(lessons, list):
            return False, "lessons must be a list"
        if check_id == "RF-1":
            ok = all(isinstance(row, Mapping) and {"id", "type", "scope", "statement",
                     "tell", "evidence", "relation"} <= set(row) for row in lessons)
        elif check_id == "RF-2":
            event_ids = {event["event_id"] for event in context.events}
            ok = all(row.get("evidence") and set(row["evidence"]) <= event_ids for row in lessons)
        elif check_id == "RF-3":
            ok = all(isinstance(row.get("statement"), str)
                     and len(row["statement"]) <= 400 and "run_" not in row["statement"]
                     for row in lessons)
        else:
            ok = all(isinstance(row.get("relation"), Mapping)
                     and row["relation"].get("kind") in
                     ("new", "reinforces", "refines", "contradicts") for row in lessons)
        return _result(ok, f"{check_id} {'passed' if ok else 'failed'}")
    if check_id == "RQ-3":
        vague = ("fast", "easy", "appropriate", "快速", "简单", "合适")
        bad = [row.get("id") for req in context.requirements.get("requirements", [])
               if isinstance(req, Mapping) for row in req.get("acceptance", [])
               if isinstance(row, Mapping) and any(word in str(row.get("response", "")).lower()
                                                   for word in vague)
               and not any(ch.isdigit() for ch in str(row.get("response", "")))]
        return _result(not bad, "no unbounded vague acceptance" if not bad else f"vague: {bad}")
    if check_id == "RQ-8b":
        terms = ("不得", "不再", "禁止", "全部", "所有", "shall not", "never", "every")
        bad = [row.get("id") for req in context.requirements.get("requirements", [])
               if isinstance(req, Mapping) for row in req.get("acceptance", [])
               if isinstance(row, Mapping) and any(term in str(row.get("response", "")).lower()
                                                   for term in terms)
               and row.get("negative") is not True and row.get("universal") is not True]
        return _result(not bad, "structured flags match text" if not bad else f"unmarked: {bad}")
    raise CheckError(f"no evaluator for {check_id}")
