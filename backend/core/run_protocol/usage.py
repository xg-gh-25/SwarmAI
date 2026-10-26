"""Measured Pipeline v1 token usage events from CLI transcripts.

Public symbols:
- ``measure`` — resolve trusted transcript IDs, sum main/subagent responses, append metrics.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence

from core import transcript_usage
from core.run_protocol.store import AuditStore


class UsageError(RuntimeError):
    """No trustworthy transcript measurement is available."""


def _epoch(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError) as exc:
        raise UsageError("invalid run time window") from exc


def measure(store: AuditStore, *, transcript_ids: Sequence[str], created_at: str,
            finished_at: str | None = None, stage: str = "reflect") -> dict[str, Any]:
    paths = transcript_usage.resolve_transcripts(transcript_ids)
    if not paths:
        raise UsageError("no trusted transcript resolved for this run")
    hi = _epoch(finished_at) if finished_at else datetime.now(timezone.utc).timestamp()
    value = transcript_usage.usage_in_window(paths, _epoch(created_at), hi,
                                              include_subagents=True)
    if value is None:
        raise UsageError("no measured response in the run window")
    # cache reads apart: how many a call reads depends on how long the host session
    # already was, so runs compare on input + cache creation + output (design 2026-10-26-
    # pipeline-review-closure §6.2 point 3)
    combined_input = value["input_tokens"] + value["cache_creation_tokens"]
    metrics = {"input_tokens": combined_input,
               "output_tokens": value["output_tokens"],
               "cache_read_tokens": value["cache_read_tokens"],
               "accounted_calls": value["responses"]}
    existing = store.read_events()
    for name, number in metrics.items():
        attempt = 1 + sum(event.get("kind") == "metric"
                          and event.get("payload", {}).get("name") == name
                          for event in existing)
        store.append_new(writer=store.authority.writer("cli"), kind="metric",
                         at=finished_at or datetime.now(timezone.utc).isoformat(),
                         stage=stage, attempt=attempt,
                         payload={"name": name, "value": number})
        existing = store.read_events()
    return {**value, "input_tokens_with_cache_creation": combined_input,
            "transcripts": len(paths)}
