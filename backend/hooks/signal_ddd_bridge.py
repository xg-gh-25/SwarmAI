"""External signal and learned-content bridges into CDF v2 S6.

Both bridges read content already present locally and make no network request. Content
inside an active session or run is rejected here because S2 or S3 owns that evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from core.ingestion.sources.s6_external import extract_external
from core.project_registry import DEFAULT_PROJECT_NAME

_MIN_RELEVANCE_SCORE = 0.8
_MAX_ITEMS_PER_RUN = 3


def bridge_signals_to_ddd(workspace_path: str, *, llm: Any | None = None) -> int:
    """Extract high-relevance out-of-session signal items as S6 References."""
    digest = Path(workspace_path) / "Services" / "signals" / "signal_digest.json"
    if not digest.is_file():
        return 0
    try:
        items = json.loads(digest.read_text(encoding="utf-8")).get("items", [])
    except (OSError, ValueError, AttributeError):
        return 0
    queued = 0
    for index, item in enumerate(items):
        if index >= _MAX_ITEMS_PER_RUN or not isinstance(item, dict):
            break
        score = item.get("relevance_score") or item.get("score") or 0
        title = str(item.get("title") or "")
        summary = str(item.get("summary") or item.get("content") or "")
        if score < _MIN_RELEVANCE_SCORE or not title or not summary:
            continue
        url = str(item.get("url") or item.get("source") or "") or None
        ref = "signal:" + hashlib.sha256(
            json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        anchor = f"web:{url}" if url else f"doc:{digest.resolve()}#item:{index}"
        queued += len(extract_external(
            ref, json.dumps(item, ensure_ascii=False), evidence_anchor=anchor,
            source_url=url, llm=llm))
    return queued


def bridge_learned_content_to_ddd(title: str, summary: str, source_url: str,
                                  workspace_path: str, project: str = DEFAULT_PROJECT_NAME,
                                  *, session_id: str | None = None,
                                  run_id: str | None = None) -> bool:
    """Send an out-of-session fetched document to S6; active contexts use S2/S3."""
    _ = workspace_path
    session = session_id or os.environ.get("SWARM_APP_SESSION_ID")
    run = run_id or os.environ.get("SWARM_RUN_ID")
    content = json.dumps({"title": title, "summary": summary,
                          "source_url": source_url}, ensure_ascii=False)
    result = extract_external(
        "learned:" + hashlib.sha256(content.encode("utf-8")).hexdigest(), content,
        evidence_anchor=f"web:{source_url}", source_url=source_url,
        session_id=session, run_id=run, project_hint=project)
    return bool(result)
