"""Event-sourced finding projection for Pipeline v1 (v13 §A3.3-§A3.5).

Public symbols:
- ``fold`` — derive canonical findings only from validated finding/proof/auth events.
- ``CLOSURE`` / ``r2_allowed`` / ``is_closed`` — the closure table: which evidence
  closes each finding kind, the R2 words a state may get, and what counts as closed
  (design 2026-10-26-pipeline-review-closure-and-visibility §2.3).
- ``push_effect`` — RV-2 effect: HIGH / CRITICAL and not closed.
- ``GATE0_EXPERT`` / ``GATE0_PASS`` — a MEDIUM / LOW Gate-0 finding is closed
  (``closed_gate0``) by a later parsed Gate-0 answer with a passing verdict, never in
  REVIEW; a HIGH one waits for accept-finding.
- ``cluster_findings`` / ``r2_routes`` / ``r2_experts`` — deterministic repair grouping,
  and R2 routing by cluster (one expert judges every finding of a ±10-line cluster).
- ``cluster_counts`` — findings counted by cluster (the audit's Quality cell).
- ``mutation_summary`` — the mutation_check result a behavior/contract fix's
  ``reported_test`` proof carries, checked against the files the run changed.

No public API accepts ``authorized=True``, old/new test strings or diff booleans as
closure authority. Those facts enter only through the event kind's trusted producer.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from core.run_protocol import events as ev

KINDS = frozenset({"behavior", "contract", "absence", "docs", "design", "security_policy"})
SEVERITIES = frozenset({"HIGH", "MEDIUM", "LOW", "CRITICAL"})
STATUSES = frozenset({"open", "closed_proven", "closed_asserted", "closed_unproven",
                      "refuted", "accepted", "invalid_test", "closed_gate0"})
# Gate-0 reviews the requirements, not code: its findings are answered by revising the
# requirements, and the next Gate-0 round judges that. A parsed answer with one of these
# verdicts closes every still-open MEDIUM / LOW Gate-0 finding of an earlier answer
# (``closed_gate0``, its evidence that answer). That round reads the requirements, not
# the earlier findings, so a HIGH / CRITICAL one is not closed by it: like a reviewed-only
# HIGH (``closed_unproven``) a person accepts it (accept-finding). The passing answer's
# own findings stay open as well: no later round reads them.
GATE0_EXPERT = "gate0-skeptic"
GATE0_PASS = frozenset({"SUPPORTED", "SUPPORTED-WITH-CHANGES"})
R2_OPEN = frozenset({"RESOLVED", "OPEN", "REFUTED"})
R2_PROVEN = frozenset({"ADEQUATE", "TEST_INADEQUATE"})
# written by the answer recorder, never by an expert: the line used a word the finding's
# state does not take (RESOLVED for closed_proven). The state stays; the finding counts as
# answered, so the R2 silence rule does not reopen it; is_closed / push_effect are unchanged
# by it (a closed_proven HIGH still blocks until ADEQUATE). One re-dispatch asks again.
R2_UNANSWERED = "UNANSWERED"
_SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

# The closure table (design 2026-10-26-pipeline-review-closure-and-visibility §2.3): for
# each finding kind, the evidence the agent submits to close it and the state it leaves.
# ``reported_test``: a passing command of a validation report on the proof's tree (an
# absence finding also needs that report to have changed tests), and for a behavior /
# contract finding the mutation_check result that reverting the fix turns the test red
# (or why no mutation could be written: MUTATION_KINDS); R2 then judges the test
# (ADEQUATE / TEST_INADEQUATE). ``diff_assertion``: after R2 judged the fix RESOLVED.
# The ledger, the answer recorder, RV-2, the audit and ``findings`` all read this table.
CLOSURE: dict[str, dict[str, str]] = {
    "behavior": {"evidence": "reported_test", "state": "closed_proven"},
    "contract": {"evidence": "reported_test", "state": "closed_proven"},
    "absence": {"evidence": "reported_test", "state": "closed_proven"},
    "docs": {"evidence": "diff_assertion", "state": "closed_asserted"},
    "design": {"evidence": "diff_assertion", "state": "closed_unproven"},
    "security_policy": {"evidence": "diff_assertion", "state": "closed_unproven"},
}
assert set(CLOSURE) == KINDS
# a reported_test proof of these kinds carries a mutation_check result or the reason none
# could be written (``mutation_summary``); an absence fix is often the added test itself
MUTATION_KINDS = frozenset({"behavior", "contract"})
MAX_MUTATION_REASON = 500


def r2_allowed(finding_status: str) -> frozenset[str]:
    """The R2 words a finding in *finding_status* may get: ADEQUATE / TEST_INADEQUATE
    judge the test that closed a ``closed_proven`` finding; RESOLVED / OPEN / REFUTED
    judge any other."""
    return R2_PROVEN if finding_status == "closed_proven" else R2_OPEN


def is_closed(row: Mapping[str, Any]) -> bool:
    """Whether a projected finding counts as closed (one answer for the audit, RV-2,
    ``next`` and ``findings``): asserted, refuted, accepted, ``closed_gate0`` (a MEDIUM /
    LOW Gate-0 finding a later Gate-0 round passed over); ``closed_proven`` only after R2
    judged its test ADEQUATE; ``closed_unproven`` (reviewed, no test) only below HIGH."""
    status = row.get("status")
    if status in ("closed_asserted", "refuted", "accepted", "closed_gate0"):
        return True
    if status == "closed_proven":
        return row.get("r2_status") == "ADEQUATE"
    if status == "closed_unproven":
        return normalize_severity(str(row.get("severity") or "")) not in ("HIGH", "CRITICAL")
    return False


# An answer whose lines parsed; ``partial`` is an R2 answer missing some status lines
# (its present lines count, the missing ones stay OPEN — design §A3.2).
USABLE_STATES = frozenset({"parsed", "partial"})

class FindingError(ValueError):
    """The event stream violates finding identity, status or evidence rules."""


def normalize_severity(value: str) -> str:
    if not isinstance(value, str):
        raise FindingError("severity is required")
    value = value.strip().upper()
    if value == "MED":
        value = "MEDIUM"
    if value not in SEVERITIES:
        raise FindingError(f"unknown severity {value!r}")
    return value


PROOF_CODES = {
    "proven": ("valid", "FAILs by assertion on the snapshot (2 of 2) and PASSes now"),
    "old_pass": ("invalid", "PASSes on the snapshot: the test does not detect the defect"),
    "old_flaky": ("invalid", "flaky on the snapshot: one assertion failure, one pass"),
    "old_error": ("invalid", "errors on the snapshot before any assertion"),
    "old_timeout": ("invalid", "times out on the snapshot"),
    "old_skip": ("invalid", "is skipped on the snapshot"),
    "new_not_pass": ("invalid", "does not PASS on the current code"),
    "pending_old": ("pending", "snapshot runs not finished"),
    "pending_new": ("pending", "current-code run not finished"),
}
_OLD_LABELS = frozenset({"assert_fail", "pass", "error", "timeout", "skip"})
_OLD_INVALID = {"error": "old_error", "timeout": "old_timeout", "skip": "old_skip"}


def proof_verdict(old: list[str], new: str | None) -> tuple[str, str]:
    old = list(old or [])
    if any(value not in _OLD_LABELS for value in old):
        raise FindingError("unknown snapshot outcome")
    for value in old:
        if value in _OLD_INVALID:
            return "invalid", _OLD_INVALID[value]
    if "pass" in old:
        return ("invalid", "old_flaky") if "assert_fail" in old else ("invalid", "old_pass")
    if len(old) < 2:
        return "pending", "pending_old"
    if new is None:
        return "pending", "pending_new"
    return ("valid", "proven") if new == "pass" else ("invalid", "new_not_pass")


def reported_push_effect(row: Mapping[str, Any]) -> bool:
    """V13 E5 rule for an unprojected review row: only unresolved HIGH/CRITICAL."""
    if not isinstance(row, Mapping) or row.get("resolved") is True \
            or str(row.get("status") or "open") in {
                "closed_proven", "closed_asserted", "refuted", "accepted", "closed_gate0"}:
        return False
    try:
        return normalize_severity(str(row.get("severity") or "")) in {"HIGH", "CRITICAL"}
    except FindingError:
        return False


def old_runs_needed(old: list[str]) -> int:
    verdict, _ = proof_verdict(old, "pass")
    return 0 if verdict == "invalid" else max(0, 2 - len(old or []))


def _occurrence(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event["payload"]
    if payload["finding_kind"] not in KINDS:
        raise FindingError(f"unknown finding kind {payload['finding_kind']!r}")
    if event["ckey"] != "CF-" + event["event_id"][:8]:
        raise FindingError("finding canonical id is not its occurrence event id")
    if isinstance(payload["line"], bool) or payload["line"] < 1:
        raise FindingError("finding line must be positive")
    return {
        "canonical_id": event["ckey"], "occurrence_event_id": event["event_id"],
        "answer_event_id": payload["answer_event_id"], "expert": payload["expert"],
        "severity": normalize_severity(payload["severity"]),
        "kind": payload["finding_kind"], "file": payload["file"],
        "line": payload["line"], "claim": payload["claim"], "fix": payload.get("fix"),
        "status": "open", "evidence_event_id": None, "r2_status": None,
        "reclassification": None,
        # the reported_test proof's mutation_check summary, or why it has none
        "mutation": None, "no_mutation": None,
    }


def _row(findings: dict[str, dict[str, Any]], canonical_id: str) -> dict[str, Any]:
    try:
        return findings[canonical_id]
    except KeyError as exc:
        raise FindingError(f"event references unknown finding {canonical_id}") from exc


def state_hash(row: Mapping[str, Any]) -> str:
    """Canonical hash bound into an accept_finding authorization."""
    import hashlib
    import json

    fields = {key: row.get(key) for key in (
        "canonical_id", "occurrence_event_id", "severity", "kind", "file", "line",
        "claim", "status", "evidence_event_id", "r2_status", "reclassification",
    )}
    return hashlib.sha256(json.dumps(
        fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def _reclassify(row: dict[str, Any], event: Mapping[str, Any]) -> None:
    payload = event["payload"]
    if payload["from"] != row["kind"] or payload["to"] not in KINDS:
        raise FindingError("reclassification does not match current kind")
    row["reclassification"] = {
        "event_id": event["event_id"], "from": payload["from"], "to": payload["to"],
        "reason": payload["reason"], "pass": payload["pass"], "confirmed": False,
    }


def r2_status_allowed(finding_status: str, status: str) -> bool:
    """Whether an R2 status line may apply to a finding in *finding_status*
    (``r2_allowed``). The ledger and the answer recorder both ask this."""
    return str(status).upper() in r2_allowed(finding_status)


def _status(row: dict[str, Any], event: Mapping[str, Any]) -> None:
    payload = event["payload"]
    status = payload["status"].upper()
    if status == R2_UNANSWERED:
        row["r2_status"] = status
        return
    if not r2_status_allowed(row["status"], status):
        raise FindingError(f"{row['canonical_id']}: R2 status {status!r} is invalid for "
                           f"{row['status']} (allowed: {', '.join(sorted(r2_allowed(row['status'])))})")
    row["r2_status"] = status
    if row["status"] == "closed_proven" and status == "TEST_INADEQUATE":
        row["status"] = "invalid_test"
    elif row["status"] != "closed_proven" and status == "OPEN":
        row["status"] = "open"
    proposal = row.get("reclassification")
    confirm = payload.get("kind_confirm")
    if proposal and confirm == proposal["to"]:
        row["kind"] = proposal["to"]
        proposal["confirmed"] = True


def _proof(row: dict[str, Any], event: Mapping[str, Any],
           reports: Mapping[str, Mapping[str, Any]]) -> None:
    payload = event["payload"]
    etype, result = payload["evidence_type"], payload["result"]
    if not isinstance(payload["code_tree"], str) or len(payload["code_tree"]) != 40 \
            or any(c not in "0123456789abcdef" for c in payload["code_tree"]):
        raise FindingError("proof code_tree must be a git tree id")
    if etype in ("diff_assertion", "refuted") and event["producer"] != "cli":
        raise FindingError(f"{etype} proof must come from cli")
    if row["status"] not in ("open", "invalid_test"):
        raise FindingError(f"proof cannot replace a terminal finding state: "
                           f"{row['canonical_id']} is {row['status']} (a proof closes only "
                           "open / invalid_test findings)")
    rule = CLOSURE[row["kind"]]
    if etype in ("reported_test", "diff_assertion") and rule["evidence"] != etype:
        raise FindingError(f"a {row['kind']} finding is closed by {rule['evidence']}, "
                           f"not {etype}")
    if etype == "reported_test":
        # S6-2: a test the agent ran and reported passing on this tree; R2 judges
        # whether it shows the fix (ADEQUATE keeps it closed, TEST_INADEQUATE reopens)
        report = reports.get(payload.get("report_event_id"))
        if row["kind"] == "absence" and not (
                isinstance(payload.get("n_tests_changed"), int)
                and not isinstance(payload.get("n_tests_changed"), bool)
                and payload["n_tests_changed"] > 0):
            raise FindingError("an absence finding is closed by a reported_test whose "
                               "report lists changed tests (tests_changed is empty)")
        if event["producer"] != "cli" \
                or result != "proven" or report is None \
                or report["payload"].get("code_tree") != payload["code_tree"] \
                or report["payload"].get("repo") != payload.get("repo") \
                or not isinstance(payload.get("command"), str) or not payload["command"]:
            raise FindingError("reported_test proof needs a passing command of a "
                               "validation report on the proof's tree")
        row["status"] = rule["state"]
        mutation, reason = payload.get("mutation"), payload.get("no_mutation")
        row["mutation"] = copy.deepcopy(dict(mutation)) if isinstance(mutation, Mapping) else None
        row["no_mutation"] = reason if isinstance(reason, str) and reason.strip() else None
    elif etype == "diff_assertion":
        if row["r2_status"] != "RESOLVED" or result != "asserted" \
                or payload.get("touches_finding") is not True:
            raise FindingError(f"diff assertion lacks matching R2 and hunk: "
                               f"{row['canonical_id']} has R2 status {row['r2_status']} (needs "
                               "RESOLVED) and touches_finding "
                               f"{payload.get('touches_finding')} (needs true)")
        executable = payload.get("touches_executable") is not False
        if row["kind"] == "docs" and executable and payload.get("basis") == "file":
            # judged on the file (R2 saw its whole diff since R1): the same file also
            # changed code, so the docs fix closes like a reviewed-only one (a HIGH
            # stays open; design 2026-10-26-pipeline-review-closure §2.3 point 4)
            row["status"] = "closed_unproven"
        elif row["kind"] == "docs" and executable:
            raise FindingError("docs diff assertion touches or did not classify executable lines")
        else:
            row["status"] = rule["state"]
    elif etype == "refuted":
        refs = payload.get("refs")
        if row["r2_status"] != "REFUTED" or result != "refuted" \
                or not isinstance(refs, list) or not refs or not all(_resolved_ref(ref) for ref in refs):
            raise FindingError(f"refutation lacks R2 REFUTED and resolved refs: "
                               f"{row['canonical_id']} has R2 status {row['r2_status']} (needs "
                               "REFUTED) and needs at least one path:line or event:<id> ref")
        row["status"] = "refuted"
    else:
        raise FindingError(f"unknown proof evidence type {etype!r}")
    row["evidence_event_id"] = event["event_id"]


def _accept(findings: dict[str, dict[str, Any]], event: Mapping[str, Any]) -> None:
    payload = event["payload"]
    if payload["action"] != "accept_finding" or not payload.get("canonical_id") \
            or not payload.get("state_hash"):
        return
    row = _row(findings, payload["canonical_id"])
    current_hash = state_hash(row)
    if payload["state_hash"] != current_hash:
        raise FindingError("accept_finding authorization is stale for current state")
    from core.run_protocol.auth import binding_digest
    expected_digest = binding_digest("accept_finding", {
        "canonical_id": row["canonical_id"], "state_hash": current_hash,
    })
    if payload["binding_digest"] != expected_digest:
        raise FindingError("accept_finding authorization was rebound to another finding")
    row["status"] = "accepted"
    row["evidence_event_id"] = event["event_id"]


def _resolved_ref(ref: Any) -> bool:
    """A ref facts.resolve_refs resolved when the proof was recorded (§A3.5)."""
    if not isinstance(ref, Mapping):
        return False
    kind = ref.get("kind")
    if kind == "path":
        lines = ref.get("lines")
        return isinstance(ref.get("path"), str) and isinstance(ref.get("blob"), str) \
            and isinstance(lines, list) and len(lines) == 2 \
            and all(isinstance(n, int) and n >= 1 for n in lines) and lines[0] <= lines[1]
    if kind == "event":
        return isinstance(ref.get("event_id"), str) and bool(ref["event_id"])
    return False


def fold(events: list[Mapping[str, Any]], authority: ev.EventAuthority, *,
         r2_silence: bool = True) -> list[dict[str, Any]]:
    """Project canonical findings from a complete validated event chain.

    *r2_silence* False skips the end-of-chain rule that turns an R2 target with no
    answer yet into OPEN: the state an R2 answer being recorded now is applied to
    (the answer recorder asks which of its status lines fit, ced6caa3)."""
    errors = ev.verify_chain(list(events), authority)
    if errors:
        raise FindingError(f"invalid event chain: {errors[0]}")
    findings: dict[str, dict[str, Any]] = {}
    answers: dict[str, Mapping[str, Any]] = {}
    requests: dict[str, Mapping[str, Any]] = {}
    grants: dict[str, Mapping[str, Any]] = {}
    consumed_grants: set[str] = set()
    r2_targets: set[str] = set()
    r2_handout_ids: dict[str, set[str]] = {}
    answered: set[str] = set()
    reports: dict[str, Mapping[str, Any]] = {}
    for event in events:
        kind = event["kind"]
        if kind == "validation_report":
            reports[event["event_id"]] = event
        elif kind == "answer":
            # the CLI writes an answer only as the agent's relayed reply (standalone T6)
            if event.get("producer") == "cli" and event["payload"].get("source") != "relayed":
                raise FindingError("an answer written by the CLI must be source relayed")
            answers[event["event_id"]] = event
            payload = event["payload"]
            if payload.get("expert_id") == GATE0_EXPERT and payload.get("state") == "parsed" \
                    and payload.get("verdict") in GATE0_PASS:
                # every row here came from an earlier answer: an answer's own findings
                # are folded after it (a finding_occ needs its answer already recorded)
                for row in findings.values():
                    if row["expert"] == GATE0_EXPERT and row["status"] == "open" \
                            and row["severity"] not in ("HIGH", "CRITICAL"):
                        row["status"] = "closed_gate0"
                        row["evidence_event_id"] = event["event_id"]
        elif kind == "finding_occ":
            answer = answers.get(event["payload"]["answer_event_id"])
            # what an answer says is written by whoever wrote the answer (T6)
            if answer is not None and answer.get("producer") != event.get("producer"):
                raise FindingError("finding_occ producer differs from its answer's")
            if answer is None or answer["payload"]["expert_id"] != event["payload"]["expert"] \
                    or answer["payload"]["state"] not in USABLE_STATES \
                    or answer["payload"]["round"] != event.get("round"):
                raise FindingError("finding_occ does not match a parsed expert answer")
            row = _occurrence(event)
            if row["canonical_id"] in findings:
                raise FindingError("duplicate canonical finding")
            findings[row["canonical_id"]] = row
        elif kind == "reclassify":
            _reclassify(_row(findings, event["ckey"]), event)
        elif kind == "finding_status":
            row = _row(findings, event["ckey"])
            if event["ckey"] not in r2_targets:
                raise FindingError("finding_status canonical id was not in the R2 handout")
            answer = answers.get(event["payload"]["answer_event_id"])
            if answer is not None and answer.get("producer") != event.get("producer"):
                raise FindingError("finding_status producer differs from its answer's")
            # who may judge it: the expert whose R2 handout listed it (R2 is routed by
            # cluster, so that can be another expert than the one that raised it), or,
            # in a log from before cluster routing, the expert that raised it
            listed = event["ckey"] in r2_handout_ids.get(
                (answer or {}).get("payload", {}).get("handout_event_id"), set())
            if answer is None \
                    or not (listed or answer["payload"]["expert_id"] == row["expert"]) \
                    or answer["payload"]["state"] not in USABLE_STATES \
                    or answer["payload"]["round"] != 2 or event.get("round") != 2:
                raise FindingError("finding_status does not match a parsed R2 answer to a "
                                   "handout that listed the finding")
            _status(row, event)
            answered.add(event["ckey"])
        elif kind == "proof":
            _proof(_row(findings, event["ckey"]), event, reports)
        elif kind == "land_request":
            requests[event["payload"]["request_id"]] = event["payload"]
        elif kind == "auth":
            request = requests.get(event["payload"]["request_id"])
            if request is None or request["action"] != event["payload"]["action"] \
                    or request["binding_digest"] != event["payload"]["binding_digest"]:
                raise FindingError("auth does not match request")
            grants[event["event_id"]] = event["payload"]
        elif kind == "auth_consumed":
            payload = event["payload"]
            grant = grants.get(payload["auth_event_id"])
            if grant is None or payload["auth_event_id"] in consumed_grants \
                    or any(payload[key] != grant[key] for key in (
                        "request_id", "session_id", "run_id", "action", "binding_digest"
                    )):
                raise FindingError("auth_consumed is not a single matching grant")
            consumed_grants.add(payload["auth_event_id"])
            _accept(findings, event)
        elif kind == "handout" and event.get("round") == 2:
            # one expert judges a whole ±10-line cluster (r2_routes), so a handout may
            # list another expert's findings; every id must be a finding of the run
            ids = set(event["payload"]["finding_ids"])
            if any(canonical_id not in findings for canonical_id in ids):
                raise FindingError("R2 handout roster names a finding this run does not have")
            r2_targets.update(ids)
            r2_handout_ids[event["event_id"]] = ids
    # R2 silence never closes: only exact canonical IDs listed in the handout become OPEN.
    # A closed_proven finding keeps its state and waits for R2 (r2_status None): is_closed
    # / push_effect already hold it open until ADEQUATE, and its proof and R2 handout are
    # written together, so turning it open here would show every proven finding as open
    # (design 2026-10-26-pipeline-review-closure invariant 3).
    for canonical_id in sorted(r2_targets - answered) if r2_silence else ():
        row = findings[canonical_id]
        if row["status"] == "closed_proven":
            row["r2_status"] = None
        elif row["status"] not in ("accepted", "refuted"):
            row["status"], row["r2_status"] = "open", "OPEN"
    return [copy.deepcopy(findings[key]) for key in sorted(findings)]


def push_effect(finding: Mapping[str, Any]) -> bool:
    """RV-2: a HIGH / CRITICAL finding that is not closed (``is_closed``) blocks. A
    ``closed_proven`` one blocks until R2 judged its test ADEQUATE."""
    severity = normalize_severity(finding.get("severity"))
    if finding.get("status") not in STATUSES:
        raise FindingError("finding has no valid status")
    return severity in ("HIGH", "CRITICAL") and not is_closed(finding)


def cluster_findings(findings: list[Mapping[str, Any]], *, distance: int = 10) -> list[dict[str, Any]]:
    if not isinstance(distance, int) or isinstance(distance, bool) or distance < 0:
        raise FindingError("distance must be a non-negative integer")
    rows = [copy.deepcopy(dict(row)) for row in findings]
    if any(row.get("status") not in STATUSES for row in rows):
        raise FindingError("cannot cluster malformed findings")
    rows.sort(key=lambda row: (row["file"], row["line"], row["canonical_id"]))
    groups: list[list[dict[str, Any]]] = []
    for row in rows:
        if not groups or groups[-1][-1]["file"] != row["file"] \
                or row["line"] - groups[-1][-1]["line"] > distance:
            groups.append([row])
        else:
            groups[-1].append(row)
    result = []
    for index, group in enumerate(groups, 1):
        severity = max((normalize_severity(row["severity"]) for row in group),
                       key=_SEVERITY_RANK.get)
        result.append({"cluster_id": f"K{index}",
                       "findings": [row["canonical_id"] for row in group],
                       "sites": [{"file": row["file"], "line": row["line"]} for row in group],
                       "experts": sorted({row["expert"] for row in group}),
                       "severity": severity})
    return result


# who leads the R2 of a cluster it raised a finding in (r2_routes): security first, so a
# security finding is always judged with the security instructions; then any other
# specialist; correctness, the generalist every pipeline change gets (review.reviewers),
# last
R2_LEAD_FIRST = "security"
GENERALIST = "correctness"


def _lead_rank(expert: str) -> int:
    return 0 if expert == R2_LEAD_FIRST else 2 if expert == GENERALIST else 1


def r2_routes(findings: list[Mapping[str, Any]], addressed_ids: set[str], *,
              distance: int = 10) -> dict[str, list[str]]:
    """{expert: finding ids} for the R2 of *addressed_ids*: the addressed findings are
    clustered (``cluster_findings``: same file, within *distance* lines, across experts)
    and each cluster goes whole to one expert, so two experts that reported the same
    problem at the same place are rechecked once. Security leads every cluster it raised
    a finding in, so a security finding is never judged without the security
    instructions; otherwise another specialist leads before correctness (the generalist
    that reviews every change, ``_lead_rank``); within a rank the expert of the
    highest-severity finding, then the one with most findings in the cluster, then the
    expert name. Every finding keeps its own row (expert, claim, fix): the R2 input
    shows each one as its expert wrote it."""
    if not isinstance(addressed_ids, set):
        raise FindingError("addressed_ids must be a set")
    known = {str(row.get("canonical_id")) for row in findings}
    if addressed_ids - known:
        raise FindingError(f"addressed finding(s) are unknown: {sorted(addressed_ids - known)}")
    rows = {row["canonical_id"]: row for row in findings
            if row.get("canonical_id") in addressed_ids}
    routes: dict[str, set[str]] = {}
    for cluster in cluster_findings(list(rows.values()), distance=distance):
        members = [rows[cid] for cid in cluster["findings"]]
        counts: dict[str, int] = {}
        top: dict[str, int] = {}
        for row in members:
            expert = str(row["expert"])
            counts[expert] = counts.get(expert, 0) + 1
            rank = _SEVERITY_RANK[normalize_severity(row["severity"])]
            top[expert] = max(top.get(expert, -1), rank)
        lead = sorted(counts, key=lambda expert: (_lead_rank(expert), -top[expert],
                                                  -counts[expert], expert))[0]
        routes.setdefault(lead, set()).update(cluster["findings"])
    return {expert: sorted(ids) for expert, ids in sorted(routes.items())}


def r2_experts(findings: list[Mapping[str, Any]], addressed_ids: set[str]) -> list[str]:
    """The experts the R2 of *addressed_ids* is sent to (``r2_routes``)."""
    return sorted(r2_routes(findings, addressed_ids))


def cluster_counts(findings: list[Mapping[str, Any]]) -> dict[str, int]:
    """Findings counted by cluster (``cluster_findings``, across experts): ``total`` the
    clusters, ``closed`` the clusters whose every finding is closed (``is_closed``),
    ``confirmed`` the closed clusters with a test R2 judged ADEQUATE, ``reported`` the
    findings themselves. Two experts reporting one problem at one place count once; a
    cluster with one open finding is open."""
    rows = list(findings)
    by_id = {row["canonical_id"]: row for row in rows}
    clusters = [[by_id[cid] for cid in cluster["findings"]]
                for cluster in cluster_findings(rows)]
    closed = [members for members in clusters if all(is_closed(row) for row in members)]
    return {"total": len(clusters), "closed": len(closed),
            "confirmed": sum(any(row.get("status") == "closed_proven"
                                 and row.get("r2_status") == "ADEQUATE" for row in members)
                             for members in closed),
            "reported": len(rows)}


def _mutated_path(row: Mapping[str, Any], root: "str | None") -> str | None:
    """The repo-relative path one mutation_check row changed, or None when it names no
    file inside the repo. ``path`` (the absolute file mutation_check wrote) is resolved
    against *root*, the run's worktree; without it ``file`` must already be a path from
    the repository root. A ``..`` or absolute path is never one of the run's files."""
    import os
    import posixpath
    path = row.get("path")
    if isinstance(path, str) and path and root:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
        rel = rel.replace(os.sep, "/")
        return None if rel == ".." or rel.startswith("../") or os.path.isabs(rel) else rel
    file = row.get("file")
    if not isinstance(file, str) or not file.strip():
        return None
    norm = posixpath.normpath(file.strip().replace("\\", "/"))
    if norm == ".." or norm.startswith("../") or norm.startswith("/"):
        return None
    return norm


def mutation_summary(result: Any, changed: "set[str] | list[str]", *,
                     tests: "set[str] | list[str]" = (), root: "str | None" = None
                     ) -> tuple[dict[str, Any] | None, str | None]:
    """(summary, None) when *result* -- the JSON ``scripts/mutation_check.py`` printed --
    shows the fix's test going red with the fix taken out: a green baseline run, at
    least one mutation, every one caught (none missed, none with a bad anchor, none whose
    run hit the time cap) and every mutated file one the run changed that is not one of
    its tests (*changed*: paths from the repository root; *tests*: the test files the
    run changed, its report's ``tests_changed`` and the engine's test-path rule; *root*:
    the run's worktree, where ``path`` must lie). Else (None,
    what is wrong). The summary, recorded on the proof, is ``{"caught", "files"}``. The
    pipeline does not run it (S6-1): this checks the agent's reported result."""
    if not isinstance(result, Mapping):
        return None, "mutation must be the JSON object scripts/mutation_check printed"
    if result.get("error"):
        return None, f"mutation_check reported an error: {str(result['error'])[:200]}"
    baseline = result.get("baseline")
    if not isinstance(baseline, Mapping) or baseline.get("rc") != 0 \
            or baseline.get("timeout") is True:
        return None, ("mutation_check's baseline run is missing or not green (run it "
                      "without --no-baseline): without it a caught mutation proves nothing")
    rows = result.get("mutations")
    if not isinstance(rows, list) or not rows \
            or not all(isinstance(row, Mapping) for row in rows):
        return None, "mutation_check ran no mutation (mutations is empty)"
    statuses = [row.get("status") for row in rows]
    if "missed" in statuses:
        names = [str(row.get("name") or row.get("file")) for row in rows
                 if row.get("status") == "missed"]
        return None, ("the test stays green with the fix taken out (missed: "
                      + ", ".join(names[:5]) + "): it does not detect the fix")
    if any(status != "caught" for status in statuses):
        return None, ("a mutation's anchor was not found exactly once; fix the spec, or "
                      "give no_mutation with the reason when no anchor can be written")
    if any(row.get("timeout") is True for row in rows):
        return None, ("a mutation's run hit mutation_check's time cap: a hang is not the "
                      "test going red; fix the hang or raise --timeout")
    changed_paths = {str(path) for path in changed}
    test_paths = {str(path) for path in tests}
    files: list[str] = []
    for row in rows:
        rel = _mutated_path(row, root)
        if rel is None or rel not in changed_paths:
            return None, (f"the mutation of {row.get('path') or row.get('file')} is not in a "
                          "file this run changed (run mutation_check in the run's worktree "
                          "and take the fix itself out)")
        if rel in test_paths:
            return None, (f"the mutation changes {rel}, a test file this run changed: take "
                          "the fix out of the code, not the test")
        files.append(rel)
    files = sorted(set(files))
    return {"caught": len(rows), "files": files[:10]}, None
