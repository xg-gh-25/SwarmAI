"""One definition of model usage and spend for the whole platform, and the one place
that writes it (design docs/design/2026-10-29-usage-accounting.md).

A usage row is one model's use in one billed call, or in one CLI result: the four
kinds of token kept apart (``input`` not read from the cache, ``output``,
``cache_read``, ``cache_write``) and that row's own book cost (``cost_usd``, never a
running total). Every reader computes the derived figures here:

- ``billed(row)`` — every token billed: the four kinds together
- ``cache_hit_rate(row)`` — cache reads over the input side (output is not input)
- ``comparable(row)`` — input + cache writes + output: what runs compare on (a call's
  cache reads depend on how long its host session already was)

The cost is the book cost the CLI computes from its own price list (``costBasis:
"list"``), not the provider's bill.

Writers:

- ``CliResults`` — turns a CLI ``ResultMessage`` into rows. The SDK's
  ``model_usage`` is the PROCESS's running total per model (measured: a second result
  carries the first one's tokens and cost too), so each result's row is the increase
  over the previous result of the same process; a total that went down means a new
  process. Without ``model_usage`` (an older CLI) the result's own ``usage`` and the
  increase of ``total_cost_usd`` are used, as one row.
- ``rows_from_cli_json`` — the rows of one ``claude --print --output-format json`` run
  (its ``modelUsage`` covers the whole process).
- ``bedrock_row`` — one direct Bedrock Converse call.
- ``attribute(feature)`` — names what the calls inside a block are spent for.

``record(rows)`` / ``record_sync(rows)`` insert them into ``usage_ledger`` (schema
v18); a row whose ``dedup_key`` is already there is not written again. Recording never
raises into the caller: accounting must not break the work it accounts for.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping, Optional

logger = logging.getLogger(__name__)

KINDS = ("input", "output", "cache_read", "cache_write")
SURFACES = frozenset({"chat", "channel", "pipeline", "job", "background", "cdf", "eval", "hive"})
#: the SDK / CLI ``modelUsage`` keys of each kind
_MODEL_USAGE_KEYS = {"input": "inputTokens", "output": "outputTokens",
                     "cache_read": "cacheReadInputTokens", "cache_write": "cacheCreationInputTokens"}
#: the CLI ``usage`` keys of each kind
_USAGE_KEYS = {"input": "input_tokens", "output": "output_tokens",
               "cache_read": "cache_read_input_tokens", "cache_write": "cache_creation_input_tokens"}
#: the Bedrock Converse ``usage`` keys of each kind
_CONVERSE_KEYS = {"input": "inputTokens", "output": "outputTokens",
                  "cache_read": "cacheReadInputTokens", "cache_write": "cacheWriteInputTokens"}


# ── the definition ────────────────────────────────────────────────────────────

def _n(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def billed(row: Mapping[str, Any]) -> int:
    """Every token billed: input, output, cache reads and cache writes."""
    return sum(_n(row.get(k)) for k in KINDS)


def comparable(row: Mapping[str, Any]) -> int:
    """Input + cache writes + output: what runs are compared on (cache reads apart)."""
    return _n(row.get("input")) + _n(row.get("cache_write")) + _n(row.get("output"))


def cache_hit_rate(row: Mapping[str, Any]) -> Optional[float]:
    """Cache reads over the input side (input + cache reads + cache writes); None with
    no input side."""
    side = _n(row.get("input")) + _n(row.get("cache_read")) + _n(row.get("cache_write"))
    return _n(row.get("cache_read")) / side if side else None


# ── attribution ──────────────────────────────────────────────────────────────

_FEATURE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("usage_feature", default=None)


@contextlib.contextmanager
def attribute(feature: Optional[str]) -> Iterator[None]:
    """Name what the model calls inside this block are spent for (e.g. ``judge.session``);
    an inner block wins."""
    token = _FEATURE.set(str(feature) if feature else None)
    try:
        yield
    finally:
        _FEATURE.reset(token)


def current_feature() -> Optional[str]:
    return _FEATURE.get()


# ── rows ─────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(*, surface: str, model: Optional[str], tokens: Mapping[str, int], cost: Optional[float],
         cost_source: str, dedup_key: str, at_utc: Optional[str] = None, machine: str = "host",
         **refs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "at_utc": at_utc or _now(), "machine": machine,
        "surface": surface if surface in SURFACES else "background",
        "model": model, "cost_usd": cost, "cost_source": cost_source if cost is not None else "none",
        "kind": "spend", "imported": 0, "dedup_key": dedup_key,
        **{k: _n(tokens.get(k)) for k in KINDS},
    }
    for k in ("feature", "session_id", "run_id", "stage", "job_id", "dispatch_id", "cli_pid", "cli_cost_total"):
        if refs.get(k) is not None:
            row[k] = refs[k]
    return row


def _model_usage(model_usage: Any) -> dict[str, dict[str, float]]:
    """``modelUsage`` as {model: {input, output, cache_read, cache_write, cost}}."""
    out: dict[str, dict[str, float]] = {}
    if not isinstance(model_usage, Mapping):
        return out
    for model, u in model_usage.items():
        if not isinstance(u, Mapping):
            continue
        cost = u.get("costUSD")
        out[str(model)] = {**{k: _n(u.get(src)) for k, src in _MODEL_USAGE_KEYS.items()},
                           "cost": float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0}
    return out


def _increase(prev: Optional[Mapping[str, float]], cur: Mapping[str, float]) -> dict[str, float]:
    """This result's share of a running total: the increase over ``prev``; when any part
    went down the total is a new process's, and is this result's whole."""
    if prev is None or any(cur[k] < prev.get(k, 0) for k in (*KINDS, "cost")):
        return dict(cur)
    return {k: cur[k] - prev.get(k, 0) for k in (*KINDS, "cost")}


class CliResults:
    """The usage rows of CLI results, one per model per result, each the increase over
    the previous result of the same process (``model_usage`` is the process's running
    total). Bounded: the oldest processes are forgotten first; a forgotten process's
    next result would then count from zero, which only a process with more than
    ``max_processes`` newer ones can reach."""

    def __init__(self, max_processes: int = 512) -> None:
        self._last: OrderedDict[tuple, dict] = OrderedDict()
        self._max = max_processes

    def rows(self, message: Any, *, session_id: Optional[str], pid: Optional[int], surface: str,
             default_model: Optional[str] = None, **refs: Any) -> list[dict[str, Any]]:
        key = (session_id, pid)
        state = self._last.pop(key, None) or {"seq": 0, "models": {}, "cost": None}
        state["seq"] += 1
        self._last[key] = state
        while len(self._last) > self._max:
            self._last.popitem(last=False)
        seq = state["seq"]
        base = f"cli:{session_id}:{pid}:{seq}"
        total = getattr(message, "total_cost_usd", None)
        total = float(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else None
        common = dict(surface=surface, session_id=session_id, cli_pid=pid, cost_source="cli", **refs)
        models = _model_usage(getattr(message, "model_usage", None))
        out: list[dict[str, Any]] = []
        if models:
            for model, cur in models.items():
                d = _increase(state["models"].get(model), cur)
                state["models"][model] = cur
                if not any(d[k] for k in (*KINDS, "cost")):
                    continue                      # a model idle since the previous result
                out.append(_row(model=model, tokens=d, cost=d["cost"], dedup_key=f"{base}:{model}",
                                cli_cost_total=total, **common))
        else:
            usage = getattr(message, "usage", None) or {}
            tokens = {k: _n(usage.get(src)) for k, src in _USAGE_KEYS.items()} if isinstance(usage, Mapping) else {}
            prev = state["cost"]
            cost = None if total is None else (total - prev if prev is not None and total >= prev else total)
            if any(tokens.values()) or cost:
                out.append(_row(model=default_model, tokens=tokens, cost=cost, dedup_key=f"{base}:-",
                                cli_cost_total=total, **common))
        if total is not None:
            state["cost"] = total
        return out


def rows_from_cli_json(output: Mapping[str, Any], *, surface: str, run_key: Optional[str] = None,
                       **refs: Any) -> list[dict[str, Any]]:
    """The rows of one ``claude --print --output-format json`` run: its ``modelUsage``
    covers the whole process, so each model's figures are this run's. Without it, the
    ``usage`` and ``total_cost_usd`` as one row. ``run_key`` makes the rows of one run
    idempotent (a new id otherwise)."""
    base = f"clijson:{run_key or uuid.uuid4().hex}"
    total = output.get("total_cost_usd")
    total = float(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else None
    models = _model_usage(output.get("modelUsage"))
    common = dict(surface=surface, cost_source="cli", feature=refs.pop("feature", None) or current_feature(), **refs)
    if models:
        return [_row(model=m, tokens=u, cost=u["cost"], dedup_key=f"{base}:{m}", **common)
                for m, u in models.items() if any(u[k] for k in (*KINDS, "cost"))]
    usage = output.get("usage") or {}
    tokens = {k: _n(usage.get(src)) for k, src in _USAGE_KEYS.items()} if isinstance(usage, Mapping) else {}
    if not any(tokens.values()) and not total:
        return []
    return [_row(model=None, tokens=tokens, cost=total, dedup_key=f"{base}:-", **common)]


def bedrock_row(usage: Mapping[str, Any], *, model: Optional[str], feature: Optional[str] = None,
                surface: str = "background", cost: Optional[float] = None, **refs: Any) -> dict[str, Any]:
    """One direct Bedrock Converse call. ``cost`` when known (a price table); else none."""
    tokens = {k: _n((usage or {}).get(src)) for k, src in _CONVERSE_KEYS.items()}
    feat = feature or current_feature()
    return _row(surface="cdf" if (feat or "").startswith("cdf.") else surface, model=model, tokens=tokens,
                cost=cost, cost_source="priced", dedup_key=f"bedrock:{uuid.uuid4().hex}", feature=feat, **refs)


# ── recording ────────────────────────────────────────────────────────────────

async def record(rows: list[dict[str, Any]]) -> None:
    """Insert ``rows`` into the ledger; never raises."""
    if not rows:
        return
    try:
        import database
        await database.db.insert_usage_rows(rows)
    except Exception:  # noqa: BLE001 — accounting never breaks the work it accounts for
        logger.warning("usage_accounting: %d row(s) not recorded", len(rows), exc_info=True)


def record_sync(rows: list[dict[str, Any]]) -> None:
    """``record`` from a synchronous caller: scheduled on the running loop, or run to the
    end when there is none (a standalone job)."""
    if not rows:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    try:
        if loop is not None:
            loop.create_task(record(rows))
        else:
            asyncio.run(record(rows))
    except Exception:  # noqa: BLE001
        logger.warning("usage_accounting: %d row(s) not recorded", len(rows), exc_info=True)


#: the CLI results of this daemon (one tracker: a process's results all pass here)
cli_results = CliResults()
