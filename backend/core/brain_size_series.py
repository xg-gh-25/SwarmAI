"""brain_size_series — the daily size-snapshot time-series for the C&M Global Brain.

The C&M overlay's two trend charts (MEMORY.md size over time, 30-day prompt-token
growth) need a historical series. No such series existed and there is NO backfill
(XG decision 2026-08-01: history doesn't exist → count from launch date forward,
never fabricate). This module is the net-new writer + reader.

**Idempotency (Gate-1 correction, run_d0ba3f69):** the daily health hook that drives
this is per-SESSION and its "once per calendar day" guard (`_last_deep_date`) is
IN-MEMORY — it resets on every daemon restart. So multiple snapshots can be
requested for the same date. This writer is therefore idempotent PER DATE on its own:
`append_snapshot` UPSERTS by date (last-write-wins), never blindly appends, so the
series can never accumulate >1 row for a calendar day (which would double-count the
trend). Durability lives in the file, not in caller memory.

Row shape (one JSON object per line):
    {"date": "YYYY-MM-DD", "prompt_tokens": int, "memory_bytes": int,
     "per_file": {filename: tokens, ...}}

Fail-open: a corrupt line is skipped by the reader (observability, not a gate).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["append_snapshot", "read_series", "SERIES_RELPATH"]

# Canonical location under the workspace (git-tracked so the series survives; the
# daily snapshot is small — one short JSON line per day).
SERIES_RELPATH = "Knowledge/.brain-size-series.jsonl"


def read_series(series_path: Path) -> list[dict[str, Any]]:
    """Return all snapshot rows in file (date) order. Absent/empty file → [].

    Corrupt lines are skipped (fail-open) — a single bad row must never crash the
    trend chart or the endpoint that serves it.
    """
    if not series_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        text = series_path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue  # skip corrupt row, keep going
        if isinstance(obj, dict) and "date" in obj:
            rows.append(obj)
    return rows


def append_snapshot(
    series_path: Path,
    *,
    date_str: str,
    prompt_tokens: int,
    memory_bytes: int,
    per_file: dict[str, int],
) -> None:
    """UPSERT one daily snapshot keyed by ``date_str`` (last-write-wins).

    If a row for ``date_str`` already exists it is REPLACED in place (no duplicate
    row); otherwise the new row is appended. Rows stay in ascending date order.
    This is the idempotency guarantee the per-session/restart-prone daily hook needs
    — see module docstring.
    """
    row = {
        "date": date_str,
        "prompt_tokens": int(prompt_tokens),
        "memory_bytes": int(memory_bytes),
        "per_file": {str(k): int(v) for k, v in (per_file or {}).items()},
    }

    existing = read_series(series_path)
    # Replace same-date row if present, else insert; then keep date-sorted.
    by_date: dict[str, dict[str, Any]] = {r["date"]: r for r in existing if "date" in r}
    by_date[date_str] = row  # upsert
    ordered = [by_date[d] for d in sorted(by_date.keys())]

    series_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = series_path.with_suffix(series_path.suffix + ".tmp")
    tmp.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ordered),
        encoding="utf-8",
    )
    tmp.replace(series_path)  # atomic swap — no half-written series
