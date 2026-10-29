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


def _ledger_attribution(store: AuditStore, by_model: dict, stage: str) -> "float | None":
    """The run's share of its host session's spend, per model, as usage-ledger
    attribution rows (core/usage_accounting: they split spend rows, never add to a
    total); one row per run and model, the latest measurement replacing the earlier.
    Returns the run's book cost at the prices the CLI's own rows teach, or None when a
    model has no price."""
    from core import usage_accounting, usage_prices
    run_id = getattr(getattr(store, "paths", None), "run_id", None)
    if not run_id:
        return None
    prices = usage_prices.current()
    rows, cost, known = [], 0.0, True
    for model, u in by_model.items():
        tokens = {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                  "cache_read": u.get("cache_read_tokens", 0), "cache_write": u.get("cache_creation_tokens", 0)}
        c = usage_prices.price(prices, model or None, tokens)
        if c is None:
            known = False
        else:
            cost += c
        row = usage_accounting._row(surface="pipeline", model=model or None, tokens=tokens, cost=c,
                                    cost_source="priced", dedup_key=f"attr:pipeline:{run_id}:{model}",
                                    run_id=run_id, stage=stage)
        row["kind"] = "attribution"
        rows.append(row)
    usage_accounting.record_sync(rows)
    return cost if known and by_model else None


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
    billed = combined_input + value["output_tokens"] + value["cache_read_tokens"]
    try:
        book_cost = _ledger_attribution(store, value.get("by_model") or {}, stage)
    except Exception:  # noqa: BLE001 — accounting never breaks the measurement
        book_cost = None
    metrics = {"input_tokens": combined_input,
               "output_tokens": value["output_tokens"],
               "cache_read_tokens": value["cache_read_tokens"],
               "accounted_calls": value["responses"],
               # every token billed (cache reads too) and its book cost: what the run
               # spent, beside the comparable figures above (design 2026-10-29-usage-accounting)
               "billed_tokens": billed}
    if book_cost is not None:
        metrics["book_cost_usd"] = round(book_cost, 6)
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
