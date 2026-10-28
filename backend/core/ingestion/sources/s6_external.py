"""S6 out-of-session external Reference source.

External content seen inside a session belongs to S2 and inside a run to S3. S6
never fetches a URL; callers provide content already obtained by their own boundary.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from core.ingestion import api_v2

from . import common
from .extract_llm import EXTRACTOR_VERSION


def _record_empty(source_ref: str, reason: str) -> None:
    attempt = api_v2.ExtractionAttempt(
        source="S6", evidence_range={"external": source_ref, "reason": reason},
        extractor_version=EXTRACTOR_VERSION,
        attempt_id=f"attempt:{uuid.uuid4().hex}")
    api_v2.record_extraction_attempt(attempt, status="no_unit")


def extract_external(source_ref: str, content: str, *,
                     evidence_anchor: str | None = None,
                     source_url: str | None = None,
                     session_id: str | None = None, run_id: str | None = None,
                     project_hint: str | None = None,
                     llm: Any | None = None,
                     root_ids: tuple[str, ...] | None = None) -> list[Any]:
    """Extract Reference units from content that is outside every session and run."""
    if session_id:
        _record_empty(source_ref, "owned_by_S2")
        return []
    if run_id:
        _record_empty(source_ref, "owned_by_S3")
        return []
    anchor = evidence_anchor or (f"web:{source_url}" if source_url else None)
    if not anchor:
        raise ValueError("S6 requires an external evidence anchor")
    payload = {"content": content}
    if source_url:
        payload["source_url"] = source_url
    snapshot = {"project": project_hint} if project_hint else {}
    return common.extract_and_enqueue(
        "S6", source_ref, json.dumps(payload, ensure_ascii=False), (anchor,),
        evidence_range={"external": source_ref, "source_url": source_url},
        read_root_ids=root_ids if root_ids is not None else (anchor,),
        project_snapshot=snapshot, extraction_pass="main",
        allowed_kinds=("reference",), llm=llm)
