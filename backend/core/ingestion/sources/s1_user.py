"""S1 user-directive source.

S1 accepts already-typed v2 units, requires one current-turn user evidence anchor,
and delegates freshness, turn-kind, cap, entailment, routing, and echo generation to
``api_v2.enqueue_unit``. It never writes a governed file.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

from core.ingestion import api_v2, contracts_v2

from .extract_llm import EXTRACTOR_VERSION


def submit_units(units: Iterable[dict[str, Any] | contracts_v2.SourceUnit], *,
                 session_id: str, evidence: str, turn_kind: str,
                 source_ref: str | None = None,
                 project_snapshot: dict[str, Any] | None = None) -> list[Any]:
    """Validate and enqueue at most three units for one explicit user message."""
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
    valid: list[contracts_v2.SourceUnit] = []
    for raw in values:
        try:
            candidate = ({**raw, "evidence": [anchor]} if isinstance(raw, dict)
                         else raw.model_copy(update={"evidence": [anchor]}))
            valid.append(contracts_v2.SOURCE_UNIT.validate_python(candidate))
        except Exception as exc:
            invalid.append({"raw": raw, "reason": str(exc)})
    if len(valid) > contracts_v2.S1_MAX_UNITS_PER_MESSAGE:
        invalid.extend({"raw": unit.model_dump(mode="json"), "reason": "s1_unit_cap"}
                       for unit in valid[contracts_v2.S1_MAX_UNITS_PER_MESSAGE:])
        valid = valid[:contracts_v2.S1_MAX_UNITS_PER_MESSAGE]
    outcomes = [api_v2.enqueue_unit(
        "S1", source_ref or anchor, unit, session_id=session_id, run_id=None,
        read_root_ids=(), project_snapshot=project_snapshot,
        attempt_id=attempt.attempt_id, extraction_pass="s1",
    ) for unit in valid]
    api_v2.record_extraction_attempt(
        attempt, status="ok" if valid else "no_unit", units_emitted=len(valid),
        invalid_units=invalid)
    return outcomes
