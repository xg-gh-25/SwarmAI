"""S1 user-directive source.

S1 accepts already-typed v2 units, requires one current-turn user evidence anchor,
and delegates freshness, turn-kind, cap, entailment, routing, and echo generation to
``api_v2.enqueue_unit``. It never writes a governed file.

``submit_units`` answers every unit it was given, in order: a unit that does not pass
the contract (or is over the per-message cap) comes back ``rejected`` with the reason
in words (``reason_of``), so the person submitting it can fix the payload; nothing is
dropped without saying so.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

from core.ingestion import api_v2, contracts_v2
from core.ingestion.api import EnqueueResult

from .extract_llm import EXTRACTOR_VERSION


def reason_of(exc: Exception) -> str:
    """Why a unit failed the contract, in one line per problem: the field and the
    complaint (``scope: invalid scope (one of ...)``, ``project: not a field``), with the
    rule written out where the contract has one."""
    errors = getattr(exc, "errors", None)
    items = errors() if callable(errors) else None
    if not items:
        return str(exc)
    parts = []
    for e in items:
        loc = ".".join(str(x) for x in e.get("loc", ()) if not isinstance(x, int))
        # a tagged union puts the kind first ("cognition.scope"): the field is the rest
        field = loc.split(".", 1)[1] if "." in loc else loc
        if e.get("type") == "extra_forbidden":
            msg = "not a field of this kind"
        else:
            msg = str(e.get("msg", "")).removeprefix("Value error, ")
        if "scope" in msg or field == "scope":
            msg += f" (scope is {contracts_v2.SCOPE_RULE})"
        parts.append(f"{field}: {msg}" if field else msg)
    return "; ".join(parts)


def submit_units(units: Iterable[dict[str, Any] | contracts_v2.SourceUnit], *,
                 session_id: str, evidence: str, turn_kind: str,
                 source_ref: str | None = None,
                 project_snapshot: dict[str, Any] | None = None) -> list[Any]:
    """Validate and enqueue at most three units for one explicit user message. Returns
    one result per given unit, in order: the enqueue's for a valid one, ``rejected``
    with ``detail`` (the reason) for one that is not."""
    if not session_id:
        raise ValueError("S1 requires SWARM_APP_SESSION_ID")
    if turn_kind != "user":
        raise ValueError("S1 is only available on a user-triggered turn")
    if not evidence:
        raise ValueError("S1 requires --evidence for the current user message")
    anchor = evidence if evidence == "last-user" or evidence.startswith("event:") else f"event:{evidence}"
    values = list(units)
    attempt = api_v2.ExtractionAttempt(
        source="S1", evidence_range={"session_id": session_id, "event_id": anchor},
        extractor_version=EXTRACTOR_VERSION, attempt_id=f"attempt:{uuid.uuid4().hex}")
    invalid: list[dict[str, Any]] = []
    # one slot per given unit: a valid unit, or the reason it was refused
    slots: list[contracts_v2.SourceUnit | str] = []
    for raw in values:
        try:
            candidate = ({**raw, "evidence": [anchor]} if isinstance(raw, dict)
                         else raw.model_copy(update={"evidence": [anchor]}))
            slots.append(contracts_v2.SOURCE_UNIT.validate_python(candidate))
        except Exception as exc:
            reason = reason_of(exc)
            invalid.append({"raw": raw, "reason": reason})
            slots.append(reason)
    cap = contracts_v2.S1_MAX_UNITS_PER_MESSAGE
    taken = 0
    for i, slot in enumerate(slots):
        if isinstance(slot, str):
            continue
        taken += 1
        if taken > cap:
            invalid.append({"raw": slot.model_dump(mode="json"), "reason": "s1_unit_cap"})
            slots[i] = (f"s1_unit_cap: at most {cap} units for one user message; "
                        "send the rest after the next message")
    valid = [s for s in slots if not isinstance(s, str)]
    outcomes = iter([api_v2.enqueue_unit(
        "S1", source_ref or anchor, unit, session_id=session_id, run_id=None,
        read_root_ids=(), project_snapshot=project_snapshot,
        attempt_id=attempt.attempt_id, extraction_pass="s1",
    ) for unit in valid])
    api_v2.record_extraction_attempt(
        attempt, status="ok" if valid else "no_unit", units_emitted=len(valid),
        invalid_units=invalid)
    return [EnqueueResult("rejected", None, slot) if isinstance(slot, str) else next(outcomes)
            for slot in slots]
