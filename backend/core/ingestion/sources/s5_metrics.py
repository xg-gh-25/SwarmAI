"""S5 cross-run metrics source.

Only aggregates spanning at least two runs are eligible. Numeric fields receive
``metric:`` evidence anchors; causal prose is explicitly treated as inferred.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from core.ingestion import api_v2

from . import common
from .extract_llm import EXTRACTOR_VERSION

_ALLOWED = ("cognition", "gap")


def _record_empty(window: str, reason: str) -> None:
    attempt = api_v2.ExtractionAttempt(
        source="S5", evidence_range={"window": window, "reason": reason},
        extractor_version=EXTRACTOR_VERSION,
        attempt_id=f"attempt:{uuid.uuid4().hex}")
    api_v2.record_extraction_attempt(attempt, status="no_unit")


def extract_metrics(window: str, aggregates: dict[str, Any], *, run_count: int,
                    llm: Any | None = None,
                    root_ids: tuple[str, ...] | None = None) -> list[Any]:
    """Extract agent EVOLUTION cognition or gaps from one cross-run aggregate window."""
    if run_count < 2:
        _record_empty(window, "single_run")
        return []
    numeric = {name: value for name, value in aggregates.items()
               if isinstance(value, (int, float)) and not isinstance(value, bool)}
    if not numeric:
        _record_empty(window, "no_numeric_fields")
        return []
    anchors = tuple(f"metric:{window}:{name}" for name in sorted(numeric))
    payload = {"window": window, "run_count": run_count,
               "numeric_fields": numeric,
               "inferred_context": {name: value for name, value in aggregates.items()
                                    if name not in numeric}}
    return common.extract_and_enqueue(
        "S5", window, json.dumps(payload, ensure_ascii=False, default=str), anchors,
        evidence_range={"window": window, "run_count": run_count,
                        "fields": sorted(numeric)},
        read_root_ids=root_ids if root_ids is not None else anchors,
        extraction_pass="main", allowed_kinds=_ALLOWED, llm=llm,
        causal_text_is_inferred=True)
