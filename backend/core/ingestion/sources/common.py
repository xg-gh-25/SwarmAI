"""Common source boundary for CDF v2 extraction attempts and enqueue calls.

``extract_and_enqueue`` is the only shared side-effect path used by S2-S6. It
records exactly one extraction attempt, including failures and zero-unit output.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from core.ingestion import api_v2

from .extract_llm import EXTRACTOR_VERSION, extract_units


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_and_enqueue(source: str, source_ref: str, source_text: str,
                        evidence_anchors: tuple[str, ...], *, evidence_range: dict[str, Any],
                        session_id: str | None = None, run_id: str | None = None,
                        read_root_ids: tuple[str, ...] = (),
                        project_snapshot: dict[str, Any] | None = None,
                        extraction_pass: str = "main", allowed_kinds: tuple[str, ...],
                        llm: Any | None = None, causal_text_is_inferred: bool = False,
                        unit_filter: Any | None = None) -> list[Any]:
    """Extract one source input, enqueue valid units, and finish one audit attempt."""
    attempt = api_v2.ExtractionAttempt(
        source=source, evidence_range=evidence_range,
        extractor_version=EXTRACTOR_VERSION,
        attempt_id=f"attempt:{uuid.uuid4().hex}",
    )
    try:
        extracted = extract_units(
            source, source_text, evidence_anchors, allowed_kinds=allowed_kinds,
            llm=llm, causal_text_is_inferred=causal_text_is_inferred,
        )
        if unit_filter is not None:
            kept = []
            for unit in extracted.units:
                reason = unit_filter(unit)
                if reason:
                    extracted.invalid_units.append({
                        "raw": unit.model_dump(mode="json"), "reason": reason})
                else:
                    kept.append(unit)
            extracted.units = kept
        outcomes = [api_v2.enqueue_stored(
            source, source_ref, unit, session_id=session_id, run_id=run_id,
            read_root_ids=read_root_ids, project_snapshot=project_snapshot,
            attempt_id=attempt.attempt_id, extraction_pass=extraction_pass,
        ) for unit in extracted.units]
        api_v2.record_extraction_attempt(
            attempt, status="ok" if extracted.units else "no_unit",
            units_emitted=len(extracted.units), invalid_units=extracted.invalid_units,
            splits=extracted.splits,
        )
        return outcomes
    except Exception as exc:
        api_v2.record_extraction_attempt(
            attempt, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
