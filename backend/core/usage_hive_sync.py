"""The Hives' spend in the host's usage ledger (design
docs/design/2026-10-29-usage-accounting.md §4 "Hive").

A Hive runs the same product and keeps its own usage ledger: its chat turns, CDF and
Bedrock calls, jobs, pipeline runs. This host pulls each running Hive's new rows
(``GET /api/hive/usage?after_id=``, served by the Hive from its own rows only) every
``INTERVAL_S`` and writes them under ``machine = <Hive name>``, each keyed by the
Hive and its row id, so a row is never taken twice. A row of a session this host
dispatched is tied to the dispatch (``dispatch_id``). How far each Hive was read is
kept in ``state/swarm-out/usage_sync.json``.

Everything a Hive sends is untrusted data: each field is checked for type, length and
the values it may take, and a row that does not pass is skipped. A Hive that does not
serve the endpoint yet (an older version) is skipped until it does.

- ``clean(row, hive_name, hive_id, dispatch_of)`` — a Hive row as a host ledger row, or None
- ``sync_once()`` — one pass over the running Hives (async; never raises)
- ``run_loop()`` — the host's background task
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from core import usage_accounting

logger = logging.getLogger(__name__)

INTERVAL_S = 900
FIRST_DELAY_S = 120
PAGES_PER_PASS = 20
PAGE = 1000
_TEXT_RE = re.compile(r"^[A-Za-z0-9_.:\-\[\]/@ ]{1,160}$")
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00$")
_COST_SOURCES = frozenset({"cli", "priced", "none"})
_MAX_TOKENS = 10**12


def _state_path() -> Path:
    from core.swarm_out import _state_dir
    return _state_dir() / "usage_sync.json"


def _read_state() -> dict[str, int]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in data.items() if isinstance(v, int) and v >= 0}
    except (OSError, ValueError, TypeError):
        return {}


def _write_state(state: Mapping[str, int]) -> None:
    p = _state_path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(dict(state), sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _text(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and _TEXT_RE.fullmatch(v) else None


def _count(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= _MAX_TOKENS else None


def clean(row: Any, hive_name: str, hive_id: str,
          dispatch_of: Callable[[Optional[str]], Optional[str]]) -> Optional[dict[str, Any]]:
    """One row a Hive served, as a host ledger row; None when a field is not as expected."""
    if not isinstance(row, Mapping):
        return None
    rid = row.get("id")
    if not isinstance(rid, int) or isinstance(rid, bool) or rid <= 0:
        return None
    at = row.get("at_utc")
    if not isinstance(at, str) or not _TIME_RE.fullmatch(at):
        return None
    tokens = {k: _count(row.get(k)) for k in usage_accounting.KINDS}
    if any(v is None for v in tokens.values()):
        return None
    cost = row.get("cost_usd")
    if cost is not None and (not isinstance(cost, (int, float)) or isinstance(cost, bool)
                             or not math.isfinite(cost) or not 0 <= cost < 1e6):
        return None
    kind = row.get("kind")
    if kind not in ("spend", "attribution"):
        return None
    surface = row.get("surface")
    session = _text(row.get("session_id"))
    out = usage_accounting._row(
        surface=surface if surface in usage_accounting.SURFACES else "background",
        model=_text(row.get("model")), tokens=tokens, cost=cost,
        cost_source=row.get("cost_source") if row.get("cost_source") in _COST_SOURCES else "none",
        dedup_key=f"hive:{hive_id}:{rid}", at_utc=at, machine=hive_name,
        feature=_text(row.get("feature")), session_id=session, run_id=_text(row.get("run_id")),
        stage=_text(row.get("stage")), job_id=_text(row.get("job_id")), dispatch_id=dispatch_of(session))
    out["kind"] = kind
    out["imported"] = 1 if row.get("imported") in (1, True) else 0
    return out


def _dispatches() -> dict[tuple[str, str], str]:
    """(hive_id, session_id) → dispatch_id, from this host's dispatch log."""
    from core import swarm_out
    out: dict[tuple[str, str], str] = {}
    for r in swarm_out._read_log():
        if r.get("hive_id") and r.get("session_id") and r.get("dispatch_id"):
            out[(str(r["hive_id"]), str(r["session_id"]))] = str(r["dispatch_id"])
    return out


async def _pull(hive_id: str, after: int, dispatches: dict) -> tuple[list[dict], int, bool]:
    """The rows of one Hive after ``after`` (up to ``PAGES_PER_PASS`` pages): (rows, last
    id read, whether the Hive serves usage)."""
    from core import swarm_out
    inst = await swarm_out._instance(hive_id)
    name = _text(inst.get("name")) or hive_id
    rows: list[dict] = []
    async with swarm_out._client(inst) as client:
        for _ in range(PAGES_PER_PASS):
            r = await client.get("/api/hive/usage", params={"after_id": after, "limit": PAGE})
            if r.status_code == 404:
                return rows, after, False
            if r.status_code != 200:
                break
            body = r.json()
            page = body.get("rows") if isinstance(body, dict) else None
            if not isinstance(page, list):
                break
            for raw in page:
                row = clean(raw, name, hive_id, lambda s: dispatches.get((hive_id, s)) if s else None)
                if row is not None:
                    rows.append(row)
                if isinstance(raw, Mapping) and isinstance(raw.get("id"), int) and raw["id"] > after:
                    after = raw["id"]
            if not (isinstance(body, dict) and body.get("more") is True):
                break
    return rows, after, True


async def sync_once() -> dict[str, int]:
    """One pass: every running Hive's new rows into the ledger. {hive_id: rows taken}."""
    from core import swarm_out
    taken: dict[str, int] = {}
    try:
        statuses = await swarm_out._hive_statuses()
    except Exception as e:  # noqa: BLE001 — no Hive store: nothing to do this pass
        logger.debug("usage_hive_sync: no Hive list (%s)", type(e).__name__)
        return taken
    state = await asyncio.to_thread(_read_state)
    dispatches = await asyncio.to_thread(_dispatches)
    for hive_id, status in sorted(statuses.items()):
        if status != "running":
            continue
        try:
            rows, last, served = await _pull(hive_id, state.get(hive_id, 0), dispatches)
        except Exception as e:  # noqa: BLE001 — a Hive slow or down: try it next pass
            logger.info("usage_hive_sync: %s not read (%s)", hive_id, type(e).__name__)
            continue
        if not served:
            continue
        if rows and not await asyncio.to_thread(usage_accounting.insert_now, rows):
            continue                       # not written: read the same rows next pass
        state[hive_id] = last
        taken[hive_id] = len(rows)
    await asyncio.to_thread(_write_state, state)
    return taken


async def run_loop() -> None:
    """The host's background task: a pass every ``INTERVAL_S``."""
    await asyncio.sleep(FIRST_DELAY_S)
    last_log = 0.0
    while True:
        try:
            taken = await sync_once()
            if any(taken.values()):
                logger.info("usage_hive_sync: took %s row(s) from the Hives", taken)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            if time.time() - last_log > 3600:
                logger.warning("usage_hive_sync: pass failed (%s)", type(e).__name__)
                last_log = time.time()
        await asyncio.sleep(INTERVAL_S)
