"""Recall metrics substrate — per-recall latency samples, lock-guarded, flushed to SQLite.

Unified-recall Run 2 (run_40091f5c; design Knowledge/Designs/2026-08-09-unified-recall-
architecture.md §3.3-3.4). Records ONE sample per recall — {context, domains, latency_ms,
hit_count, degraded_reason} — into a per-context bounded ring, drained every ~5 min to
the ``recall_metrics`` table by a background loop. Percentiles (p50/p95) are computed
DOWNSTREAM (Run 3) from the raw rows — this layer must therefore retain SAMPLES, never a
running sum (a counter cannot yield percentiles).

Why a standalone module (not session_router's counter section): the overlay path
(``routers/library_api.py``) and the ``s_library`` CLI record too, and routing through
session_router would force ``recall_multi`` → ``session_router`` (a cycle). This module is
a leaf both recall surfaces import.

Why ``threading.Lock`` (not ``asyncio.Lock``): recall is recorded from BOTH an io-pool
worker thread (the session-prompt path runs ``recall_all`` via ``run_in("io", …)``) AND
the event loop (the overlay path calls ``recall_library_hits`` synchronously). A scalar
``dict[k]+=1`` survives that by GIL luck, but ``deque.append`` + a drain swap-and-clear is
a read-modify-write across two contexts — it needs a real lock that both a thread and the
loop honor. Recall is low-frequency, so the lock is uncontended in practice.

Fire-and-forget: recording must NEVER raise into the recall path (an observability metric
is never worth breaking a user's recall) — every public call swallows internally.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any, Iterable, Optional

# Per-context bounded ring. maxlen bounds memory between flushes: recall is
# once-per-session (session_prompt) or per-user-search (overlays), so a few hundred
# per 5-min window is generous headroom; older samples drop rather than grow unbounded
# if a flush is ever delayed. Percentiles are computed from the flushed TABLE rows, not
# from this in-memory ring, so a rare cap-drop only loses resolution for one window.
_MAXLEN_PER_CONTEXT = 2000

_LOCK = threading.Lock()
_samples: dict[str, deque] = {}


def _serialize_domains(domains: Any) -> str:
    """Stable TEXT form for the ``domains`` column. Tuple/list → sorted csv; anything
    else → its str(). Never raises (fire-and-forget contract)."""
    try:
        if isinstance(domains, (tuple, list)):
            return ",".join(str(d) for d in domains)
        return str(domains)
    except Exception:  # noqa: BLE001
        return ""


def record_recall_metric(
    context: str,
    domains: Any,
    latency_ms: float,
    hit_count: int = 0,
    degraded_reason: Optional[str] = None,
) -> None:
    """Record ONE recall sample. Lock-guarded, fire-and-forget (never raises).

    ``context`` — the recall surface. WIRED TODAY: ``session_prompt`` (the non-ddd
    unified fan-out) + ``session_ddd`` (the per-project DDD leg) + ``library_overlay``.
    ``brainhub_overlay`` / ``memory_overlay`` are Run 3 surfaces (not yet recorded);
    ``cli`` is deliberately NOT recorded (a one-shot process exits before the 5-min
    flush, so its samples would never persist). ``domains`` — the domain tuple fanned
    over (serialized to TEXT). ``latency_ms`` — total wall-clock for this recall.
    """
    try:
        sample = {
            "context": str(context),
            "domains": _serialize_domains(domains),
            "latency_ms": float(latency_ms),
            "hit_count": int(hit_count) if hit_count is not None else 0,
            "degraded_reason": str(degraded_reason) if degraded_reason else None,
        }
        with _LOCK:
            ring = _samples.get(context)
            if ring is None:
                ring = deque(maxlen=_MAXLEN_PER_CONTEXT)
                _samples[context] = ring
            ring.append(sample)
    except Exception:  # noqa: BLE001 — a metric must NEVER break recall
        pass


def drain_samples() -> list[dict]:
    """Atomically swap-and-clear the rings, returning all buffered samples.

    Done under the lock so a concurrent ``record_recall_metric`` from a pool thread
    can't interleave with the clear (a sample is either in this drain or the next,
    never lost between them). The caller writes the returned list to SQLite OUTSIDE
    the lock (never hold the lock across a DB write). Never raises.
    """
    try:
        with _LOCK:
            out: list[dict] = []
            for ring in _samples.values():
                out.extend(ring)
                ring.clear()
            return out
    except Exception:  # noqa: BLE001
        return []


def reset_for_test() -> None:
    """Test-only: clear all rings so cases don't bleed into each other."""
    with _LOCK:
        _samples.clear()


_FLUSH_INTERVAL_S = 300  # 5 min — a batch flush, never per-recall (design §3.4)
_RETENTION_DAYS = 30  # age out recall_metrics rows older than this on each flush (Run 3
# retention — bounds the table's otherwise-unbounded growth; row volume is tiny so 30d is
# generous. Legitimate age-based cleanup, NOT an O030/STEERING#2 truncating control.)


async def flush_once() -> int:
    """Drain the rings, batch-write to recall_metrics, then age out old rows. Returns
    rows written (the insert count; pruning is a side effect, not part of the return).

    Split from the loop so it's unit-testable (drive one flush, assert rows landed
    + rings emptied + old rows pruned). Drain is atomic swap-and-clear (in
    recall_metrics); the DB write happens OUTSIDE the lock via the pooled batch writer.
    Retention (prune) is folded in here — no separate scheduler (design §3.4/Run 3).
    Never raises.
    """
    samples = drain_samples()
    try:
        from database import db  # module-level singleton (same as record_token_usage callers)
        written = await db.bulk_insert_recall_metrics(samples) if samples else 0
        # Retention: prune AFTER inserting, on the same 5-min cadence. Runs even when
        # there were no new samples this cycle, so growth is bounded regardless of
        # recall traffic. Fire-and-forget (prune_recall_metrics never raises).
        await db.prune_recall_metrics(_RETENTION_DAYS)
        return written
    except Exception:  # noqa: BLE001 — observability flush must never crash the loop
        import logging
        logging.getLogger(__name__).debug(
            "recall metrics flush failed (non-critical)", exc_info=True)
        return 0


async def recall_metrics_flush_loop() -> None:
    """Background loop (mirror readiness_sampler_loop): flush samples every 5 min.

    Single asyncio task (mounted in main.lifespan), so flushes never overlap each
    other; a concurrent record from a pool thread is safe because drain is
    lock-guarded swap-and-clear. Runs for the daemon's lifetime.
    """
    import asyncio as _asyncio
    while True:
        await _asyncio.sleep(_FLUSH_INTERVAL_S)
        await flush_once()
