"""Event-sourced finding projection for Pipeline v1 (v13 §A3.3-§A3.5).

Public symbols:
- ``fold`` — derive canonical findings only from validated finding/proof/auth events.
- ``push_effect`` — severity-based RV-2 effect for projected state.
- ``cluster_findings`` / ``r2_experts`` — deterministic repair grouping/routing.

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
                      "refuted", "accepted", "invalid_test"})
R2_OPEN = frozenset({"RESOLVED", "OPEN", "REFUTED"})
R2_PROVEN = frozenset({"ADEQUATE", "TEST_INADEQUATE"})
_SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


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
                "closed_proven", "closed_asserted", "refuted", "accepted"}:
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
    """Whether an R2 status line may apply to a finding in *finding_status*: ADEQUATE /
    TEST_INADEQUATE judge the test that closed a ``closed_proven`` finding; RESOLVED /
    OPEN / REFUTED judge any other. The ledger and the answer recorder both ask this."""
    allowed = R2_PROVEN if finding_status == "closed_proven" else R2_OPEN
    return str(status).upper() in allowed


def _status(row: dict[str, Any], event: Mapping[str, Any]) -> None:
    payload = event["payload"]
    status = payload["status"].upper()
    if not r2_status_allowed(row["status"], status):
        raise FindingError(f"R2 status {status!r} is invalid for {row['status']}")
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
        raise FindingError("proof cannot replace a terminal finding state")
    if etype == "reported_test":
        # S6-2: a test the agent ran and reported passing on this tree; R2 judges
        # whether it shows the fix (ADEQUATE keeps it closed, TEST_INADEQUATE reopens)
        report = reports.get(payload.get("report_event_id"))
        if event["producer"] != "cli" or row["kind"] not in ("behavior", "contract") \
                or result != "proven" or report is None \
                or report["payload"].get("code_tree") != payload["code_tree"] \
                or report["payload"].get("repo") != payload.get("repo") \
                or not isinstance(payload.get("command"), str) or not payload["command"]:
            raise FindingError("reported_test proof needs a passing command of a "
                               "validation report on the proof's tree")
        row["status"] = "closed_proven"
    elif etype == "diff_assertion":
        if row["kind"] not in ("docs", "design", "security_policy") \
                or row["r2_status"] != "RESOLVED" or result != "asserted" \
                or payload.get("touches_finding") is not True:
            raise FindingError("diff assertion lacks matching R2 and hunk")
        if row["kind"] == "docs" and payload.get("touches_executable") is not False:
            raise FindingError("docs diff assertion touches or did not classify executable lines")
        row["status"] = "closed_asserted" if row["kind"] == "docs" else "closed_unproven"
    elif etype == "refuted":
        refs = payload.get("refs")
        if row["r2_status"] != "REFUTED" or result != "refuted" \
                or not isinstance(refs, list) or not refs or not all(_resolved_ref(ref) for ref in refs):
            raise FindingError("refutation lacks R2 REFUTED and resolved refs")
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
            if answer is None or answer["payload"]["expert_id"] != row["expert"] \
                    or answer["payload"]["state"] not in USABLE_STATES \
                    or answer["payload"]["round"] != 2 or event.get("round") != 2:
                raise FindingError("finding_status does not match this expert's parsed R2 answer")
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
            expert = event["payload"]["expert_id"]
            ids = set(event["payload"]["finding_ids"])
            if any(canonical_id not in findings or findings[canonical_id]["expert"] != expert
                   for canonical_id in ids):
                raise FindingError("R2 handout roster does not match this expert's findings")
            r2_targets.update(ids)
    # R2 silence never closes: only exact canonical IDs listed in the handout become OPEN.
    for canonical_id in sorted(r2_targets - answered) if r2_silence else ():
        row = findings[canonical_id]
        if row["status"] not in ("accepted", "refuted"):
            row["status"], row["r2_status"] = "open", "OPEN"
    return [copy.deepcopy(findings[key]) for key in sorted(findings)]


def push_effect(finding: Mapping[str, Any]) -> bool:
    severity = normalize_severity(finding.get("severity"))
    status = finding.get("status")
    if status not in STATUSES:
        raise FindingError("finding has no valid status")
    return severity in ("HIGH", "CRITICAL") and status in (
        "open", "closed_unproven", "invalid_test",
    )


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


def r2_experts(findings: list[Mapping[str, Any]], addressed_ids: set[str]) -> list[str]:
    if not isinstance(addressed_ids, set):
        raise FindingError("addressed_ids must be a set")
    known = {str(row.get("canonical_id")) for row in findings}
    if addressed_ids - known:
        raise FindingError(f"addressed finding(s) are unknown: {sorted(addressed_ids - known)}")
    return sorted({str(row["expert"]) for row in findings
                   if row.get("canonical_id") in addressed_ids})
