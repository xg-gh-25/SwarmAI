"""Human-decision protocol for Pipeline v1 (v13 §B5.2, DC-1/DC-2).

Public symbols:
- ``validate`` — enforce the only two question types and DC-1 evidence structure.
- ``may_ask`` — bounded decision count.
- ``answer`` — bind a human answer to the current request.
- ``resolve_after_cap`` — record the agent's evidence-based choice and DC-2 failure.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

KINDS = frozenset({"requirement_ambiguity", "design_tradeoff"})
RISKS = frozenset({"low", "high"})


@dataclass(frozen=True)
class DecisionIssue:
    """One deterministic DC-1 structure/reference failure."""

    path: str
    message: str


class DecisionError(ValueError):
    """A decision operation is invalid for the current request or cap."""


def _text(obj: Mapping[str, Any], key: str, issues: list[DecisionIssue], path: str) -> str | None:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(DecisionIssue(f"{path}.{key}", "must be non-empty text"))
        return None
    return value.strip()


def validate(decision: Any, known_refs: Mapping[str, Mapping[str, Any]]) -> list[DecisionIssue]:
    """Return all DC-1 failures. ``known_refs`` is the current run's ID/event index."""
    issues: list[DecisionIssue] = []
    if not isinstance(known_refs, Mapping):
        return [DecisionIssue("known_refs", "must be an object")]
    if not isinstance(decision, Mapping):
        return [DecisionIssue("$", "decision must be an object")]
    allowed = {"request_id", "kind", "question", "options",
               "why_evidence_insufficient", "refs"}
    for key in sorted(set(decision) - allowed):
        issues.append(DecisionIssue(f"$.{key}", "unknown field"))
    _text(decision, "request_id", issues, "$")
    _text(decision, "question", issues, "$")
    _text(decision, "why_evidence_insufficient", issues, "$")
    kind = decision.get("kind")
    if kind not in KINDS:
        issues.append(DecisionIssue("$.kind", "unknown decision kind"))
    options = decision.get("options")
    valid_options: list[Mapping[str, Any]] = []
    if not isinstance(options, list) or not 2 <= len(options) <= 3:
        issues.append(DecisionIssue("$.options", "must contain 2 or 3 options"))
    else:
        option_ids = []
        for i, value in enumerate(options):
            path = f"options[{i}]"
            if not isinstance(value, Mapping):
                issues.append(DecisionIssue(path, "must be an object"))
                continue
            valid_options.append(value)
            for key in sorted(set(value) - {"id", "choice", "consequence", "reversible", "risk"}):
                issues.append(DecisionIssue(f"{path}.{key}", "unknown field"))
            option_id = _text(value, "id", issues, path)
            if option_id:
                option_ids.append(option_id)
            _text(value, "choice", issues, path)
            _text(value, "consequence", issues, path)
            if not isinstance(value.get("reversible"), bool):
                issues.append(DecisionIssue(f"{path}.reversible", "must be boolean"))
            if value.get("risk") not in RISKS:
                issues.append(DecisionIssue(f"{path}.risk", "must be low or high"))
        if len(set(option_ids)) != len(option_ids):
            issues.append(DecisionIssue("$.options", "option IDs must be unique"))
    refs = decision.get("refs")
    resolved: list[Mapping[str, Any]] = []
    if not isinstance(refs, list) or not refs:
        issues.append(DecisionIssue("$.refs", "must contain at least one reference"))
    else:
        for i, ref in enumerate(refs):
            if not isinstance(ref, str) or ref not in known_refs:
                issues.append(DecisionIssue(f"refs[{i}]", "reference does not resolve"))
            else:
                resolved.append(known_refs[ref])
    if kind == "requirement_ambiguity" and not any(
        row.get("kind") == "analysis" and row.get("resolution") == "asked"
        for row in resolved if isinstance(row, Mapping)
    ):
        issues.append(DecisionIssue("$.refs",
                                    "requirement ambiguity must reference analysis resolution=asked"))
    if kind == "design_tradeoff" and valid_options and not any(
        option.get("reversible") is False or option.get("risk") == "high"
        for option in valid_options
    ):
        issues.append(DecisionIssue("$.options",
                                    "design tradeoff needs an irreversible or high-risk option"))
    return issues


def may_ask(decisions_answered: int, cap: int) -> bool:
    """Whether another valid decision may transition the run to awaiting_human."""
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
           for value in (decisions_answered, cap)):
        raise DecisionError("decision count and cap must be non-negative integers")
    return decisions_answered < cap


def answer(decision: Mapping[str, Any], *, request_id: str,
           option_id: str, answered_by: str = "user") -> dict[str, Any]:
    """Bind an answer to the current decision and one of its declared options."""
    if request_id != decision.get("request_id"):
        raise DecisionError("answer is not for the current decision")
    options = decision.get("options") or []
    selected = next((row for row in options if isinstance(row, Mapping)
                     and row.get("id") == option_id), None)
    if selected is None:
        raise DecisionError("answer does not select a declared option")
    if answered_by not in ("user", "evidence"):
        raise DecisionError("answered_by must be user or evidence")
    return {
        "request_id": request_id, "option_id": option_id,
        "choice": selected.get("choice"), "answered_by": answered_by,
    }


def resolve_after_cap(decision: Mapping[str, Any], *, option_id: str,
                      evidence_refs: list[str], known_refs: Mapping[str, Any],
                      decisions_answered: int, cap: int) -> dict[str, Any]:
    """Record the required autonomous choice after the decision cap (DC-2 fails push)."""
    if may_ask(decisions_answered, cap):
        raise DecisionError("agent_after_cap is legal only after the decision cap")
    if not isinstance(evidence_refs, list) or not evidence_refs \
            or any(ref not in known_refs for ref in evidence_refs):
        raise DecisionError("agent-after-cap choice needs resolvable evidence refs")
    result = answer(decision, request_id=decision.get("request_id"),
                    option_id=option_id, answered_by="evidence")
    result.update({"decided_by": "agent_after_cap",
                   "evidence_refs": list(evidence_refs), "dc2_passed": False})
    return result
