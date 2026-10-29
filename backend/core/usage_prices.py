"""The book price of each kind of token, per model, learned from the CLI's own figures
(design docs/design/2026-10-29-usage-accounting.md §6).

A model call made without the CLI (a direct Bedrock call, a pipeline run's share of a
turn) has tokens but no cost. The CLI prices every result per model (``model_usage``:
the four kinds and ``costUSD``; the usage ledger keeps one row per model per result),
so each model's unit prices are the solution of a small linear system over its own
rows. That holds for any model, region, discount or cache setting the CLI knows (a
1-hour cache write is billed at 2× input, a 5-minute one at 1.25×: measured, the fit
picks the blend that applies), and needs no table to keep up to date.

A fit is used only when it explains its rows: at least ``MIN_ROWS``, no negative price,
and its cost within ``MAX_ERROR`` of the CLI's on nine rows in ten. A kind a model's
rows never used has no price; a row that used such a kind is not priced. ``FALLBACK``
holds the list prices measured on a real call for models that may be called directly
before the CLI ever used them.

- ``fit(rows)`` — {kind: USD per token} for one model's (input, output, cache_read,
  cache_write, cost) rows, or None
- ``learn(conn)`` — the fits of every model from a usage ledger connection
- ``price(prices, model, row)`` — a row's cost, or None
- ``current()`` — the fits, refreshed from the ledger at most once an hour
"""
from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any, Mapping, Optional, Sequence

KINDS = ("input", "output", "cache_read", "cache_write")
MIN_ROWS = 8
MAX_ERROR = 0.05
WINDOW_DAYS = 30
ROWS_PER_MODEL = 2000
REFRESH_S = 3600.0

#: USD per million tokens, measured 2026-10-29 on a real Bedrock call through the CLI
#: (cost matched to the cent: 3 / 15 / 0.30 / 6.00 with a 1-hour cache write)
FALLBACK: dict[str, dict[str, float]] = {
    "us.anthropic.claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 6.0},
}


def _solve(a: list[list[float]], b: list[float]) -> Optional[list[float]]:
    """Gaussian elimination with partial pivoting; None when singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def fit(rows: Sequence[Sequence[float]]) -> Optional[dict[str, float]]:
    """Unit prices (USD per token) of one model from (input, output, cache_read,
    cache_write, cost) rows; None when they do not explain the rows."""
    rows = [r for r in rows if r[4] is not None and r[4] >= 0]
    if len(rows) < MIN_ROWS:
        return None
    used = [k for k in range(4) if any(r[k] for r in rows)]
    if not used:
        return None
    # normal equations, each kind scaled to millions (keeps the system well conditioned)
    xs = [[r[k] / 1e6 for k in used] for r in rows]
    ys = [float(r[4]) for r in rows]
    ata = [[sum(x[i] * x[j] for x in xs) for j in range(len(used))] for i in range(len(used))]
    aty = [sum(x[i] * y for x, y in zip(xs, ys)) for i in range(len(used))]
    sol = _solve(ata, aty)
    if sol is None or any(p < -1e-9 for p in sol):
        return None
    errs = sorted(abs(sum(p * v for p, v in zip(sol, x)) - y) / y for x, y in zip(xs, ys) if y > 0.01)
    if errs and errs[int(0.9 * (len(errs) - 1))] > MAX_ERROR:
        return None
    return {KINDS[k]: max(0.0, p) / 1e6 for k, p in zip(used, sol)}


def learn(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """Every model's fit from the ledger's live CLI rows of the last ``WINDOW_DAYS``."""
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - WINDOW_DAYS * 86400))
    out: dict[str, dict[str, float]] = {}
    models = [r[0] for r in conn.execute(
        "SELECT DISTINCT model FROM usage_ledger WHERE cost_source='cli' AND imported=0 "
        "AND kind='spend' AND model IS NOT NULL AND cost_usd IS NOT NULL AND at_utc >= ?", (since,))]
    for m in models:
        rows = conn.execute(
            "SELECT input, output, cache_read, cache_write, cost_usd FROM usage_ledger WHERE model=? "
            "AND cost_source='cli' AND imported=0 AND kind='spend' AND cost_usd IS NOT NULL AND at_utc >= ? "
            "ORDER BY id DESC LIMIT ?", (m, since, ROWS_PER_MODEL)).fetchall()
        f = fit(rows)
        if f:
            out[m] = f
    return out


def price(prices: Mapping[str, Mapping[str, float]], model: Optional[str], row: Mapping[str, Any]) -> Optional[float]:
    """A row's cost at ``model``'s prices (learned, else ``FALLBACK``); None when the model
    has none, or the row used a kind it has no price for."""
    p = prices.get(model or "") if model else None
    if p is None and model in FALLBACK:
        p = {k: v / 1e6 for k, v in FALLBACK[model].items()}
    if p is None:
        return None
    total = 0.0
    for k in KINDS:
        n = row.get(k) or 0
        if n:
            if k not in p:
                return None
            total += n * p[k]
    return total


_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "prices": {}}


def current(db_path: Optional[str] = None) -> dict[str, dict[str, float]]:
    """The learned prices, refreshed from the ledger at most once an hour (never raises:
    an unreadable ledger keeps the last fits)."""
    with _lock:
        if time.monotonic() - _cache["at"] < REFRESH_S and _cache["at"]:
            return _cache["prices"]
        _cache["at"] = time.monotonic()
        try:
            if db_path is None:
                import database
                db_path = str(database.db.db_path)
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
            try:
                _cache["prices"] = learn(conn)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — pricing is best effort; the row keeps cost "none"
            pass
        return _cache["prices"]
