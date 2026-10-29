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

``record_sync(rows)`` (and its awaitable twin ``record``) hands rows to one writer
thread, which inserts them into ``usage_ledger`` (schema v18) in batches; a row whose
``dedup_key`` is already there is not written again. A caller never waits on the
database and never touches the async connection pool (it is per event loop: a write
from a short-lived loop would leave a pool and its connection thread behind). The
queue is bounded: when full, rows are dropped and counted (``stats()``), never
blocking the work they account for. A failed write is logged once a minute at most.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import queue
import threading
import time
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
    total). State is per session: a new pid there is a new process, and a result with
    no pid (the process already gone) continues the session's last process. Keys carry
    a nonce of this tracker, so a daemon restart never reuses a key already written.
    Bounded: the oldest sessions are forgotten first."""

    def __init__(self, max_sessions: int = 512) -> None:
        self._last: OrderedDict[Optional[str], dict] = OrderedDict()
        self._max = max_sessions
        self._nonce = uuid.uuid4().hex[:12]

    def rows(self, message: Any, *, session_id: Optional[str], pid: Optional[int], surface: str,
             default_model: Optional[str] = None, **refs: Any) -> list[dict[str, Any]]:
        state = self._last.pop(session_id, None)
        if state is None or (pid is not None and state["pid"] is not None and pid != state["pid"]):
            state = {"pid": pid, "models": {}, "cost": None, "seq": 0}
        if pid is not None:
            state["pid"] = pid
        state["seq"] += 1
        self._last[session_id] = state
        while len(self._last) > self._max:
            self._last.popitem(last=False)
        base = f"cli:{self._nonce}:{session_id}:{state['pid']}:{state['seq']}"
        total = getattr(message, "total_cost_usd", None)
        total = float(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else None
        common = dict(surface=surface, session_id=session_id, cli_pid=state["pid"], cost_source="cli", **refs)
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


def surface_of(feature: Optional[str]) -> str:
    """A feature's surface: its prefix when that is one (``cdf.s2`` → cdf, ``eval.judge`` →
    eval, ``job.signal_digest`` → job), else background."""
    head = (feature or "").split(".", 1)[0]
    return head if head in SURFACES else "background"


def bedrock_row(usage: Mapping[str, Any], *, model: Optional[str], feature: Optional[str] = None,
                cost: Optional[float] = None, **refs: Any) -> dict[str, Any]:
    """One direct Bedrock Converse call, attributed to ``feature`` (else the enclosing
    ``attribute`` block, else "unattributed"). ``cost`` when known (a price table)."""
    tokens = {k: _n((usage or {}).get(src)) for k, src in _CONVERSE_KEYS.items()}
    feat = feature or current_feature() or "unattributed"
    return _row(surface=surface_of(feat), model=model, tokens=tokens, cost=cost, cost_source="priced",
                dedup_key=f"bedrock:{uuid.uuid4().hex}", feature=feat, **refs)


# ── recording ────────────────────────────────────────────────────────────────

class _Writer:
    """One thread that writes queued rows to ``usage_ledger`` in batches, through its own
    sqlite3 connection (``mode=rw``: it never creates the database file). Callers only
    enqueue. A row with no cost is priced first (core/usage_prices). A batch that fails is retried while the database is busy, then dropped and
    counted; failures are logged at most once a minute."""

    MAX_QUEUED = 20_000
    BATCH = 500
    BUSY_TIMEOUT_S = 5.0
    LOG_EVERY_S = 60.0

    def __init__(self) -> None:
        self._q: "queue.Queue[dict]" = queue.Queue(self.MAX_QUEUED)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.written = self.dropped = self.lost = 0
        self._last_log = 0.0
        self._db_path: Optional[str] = None

    def put(self, rows: list[dict[str, Any]]) -> None:
        for r in rows:
            try:
                self._q.put_nowait(r)
            except queue.Full:
                self.dropped += 1
                self._log("usage ledger queue full: %d row(s) dropped so far", self.dropped)
        self._start()

    def _start(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="usage-ledger-writer", daemon=True)
                self._thread.start()

    def _path(self) -> str:
        if self._db_path is None:
            import database
            return str(database.db.db_path)
        return self._db_path

    def _run(self) -> None:
        while True:
            batch = [self._q.get()]
            while len(batch) < self.BATCH:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            self._write(batch)
            for _ in batch:
                self._q.task_done()

    def _price(self, batch: list[dict[str, Any]]) -> None:
        """A row with no cost (a direct model call) gets its book cost at the prices the
        CLI's own rows teach (core/usage_prices); unpriced it stays cost "none"."""
        if not any(r.get("cost_usd") is None and r.get("model") for r in batch):
            return
        try:
            from core import usage_prices
            prices = usage_prices.current(self._path())
            for r in batch:
                if r.get("cost_usd") is None and r.get("model"):
                    c = usage_prices.price(prices, r["model"], r)
                    if c is not None:
                        r["cost_usd"], r["cost_source"] = c, "priced"
        except Exception:  # noqa: BLE001 — a row unpriced is still a row
            logger.debug("usage ledger: pricing skipped", exc_info=True)

    def _write(self, batch: list[dict[str, Any]]) -> None:
        import sqlite3
        from database.sqlite import usage_ledger_insert_sql, usage_ledger_values
        self._price(batch)
        for attempt in range(3):
            try:
                conn = sqlite3.connect(f"file:{self._path()}?mode=rw", uri=True, timeout=self.BUSY_TIMEOUT_S)
                try:
                    spend = [r for r in batch if r.get("kind") != "attribution"]
                    split = [r for r in batch if r.get("kind") == "attribution"]
                    if spend:
                        conn.executemany(usage_ledger_insert_sql(), usage_ledger_values(spend))
                    if split:                   # a later measurement of the same run replaces
                        conn.executemany(usage_ledger_insert_sql(replace=True), usage_ledger_values(split))
                    conn.commit()
                finally:
                    conn.close()
                self.written += len(batch)
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e) or "busy" in str(e):
                    time.sleep(0.5 * (attempt + 1))
                    continue
                err: Exception = e
                break
            except Exception as e:  # noqa: BLE001 — accounting never breaks the work it accounts for
                err = e
                break
        else:
            err = RuntimeError("database busy")
        self.lost += len(batch)
        self._log("usage ledger: %d row(s) not written (%s); %d lost so far", len(batch), err, self.lost)

    def _log(self, msg: str, *args: Any) -> None:
        now = time.monotonic()
        if now - self._last_log >= self.LOG_EVERY_S:
            self._last_log = now
            logger.warning(msg, *args)

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until every queued row is written or given up (tests, shutdown)."""
        end = time.monotonic() + timeout
        while self._q.unfinished_tasks:
            if time.monotonic() > end:
                return False
            time.sleep(0.01)
        return True


_writer = _Writer()
# a process that exits (a standalone job run) writes what it queued first
import atexit  # noqa: E402
atexit.register(lambda: _writer.flush(3.0))


def record_sync(rows: list[dict[str, Any]]) -> None:
    """Hand ``rows`` to the ledger writer; returns at once and never raises."""
    if not rows:
        return
    try:
        _writer.put(rows)
    except Exception:  # noqa: BLE001
        logger.debug("usage_accounting: rows not queued", exc_info=True)


async def record(rows: list[dict[str, Any]]) -> None:
    """``record_sync`` for an async caller (it does not wait on the database either)."""
    record_sync(rows)


def flush(timeout: float = 5.0) -> bool:
    return _writer.flush(timeout)


def stats() -> dict[str, int]:
    """Rows written, dropped (queue full) and lost (a write that failed) since start."""
    return {"written": _writer.written, "dropped": _writer.dropped, "lost": _writer.lost,
            "queued": _writer._q.qsize()}


#: the CLI results of this daemon (one tracker: a process's results all pass here)
cli_results = CliResults()
