"""Tests for brain_size_series — the daily C&M brain size-snapshot series.

Verifies the DoD1 contract (goal run_d0ba3f69):
- append_snapshot writes a JSONL row {date, prompt_tokens, memory_bytes, per_file}
- UPSERT-by-date: firing twice on the same calendar day overwrites (last-write-wins),
  NEVER appends a 2nd row (the daily hook is per-session / restart-prone — Gate-1
  correction #2: _last_deep_date is in-memory/non-durable, so the series writer must
  be idempotent per date on its own).
- read_series returns the rows in date order; empty/absent file → [].
- NO backfill: the series only ever contains dates that were actually snapshotted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.brain_size_series import append_snapshot, read_series


def _rows(p: Path) -> list[dict]:
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def test_append_writes_row_with_required_shape(tmp_path: Path):
    series = tmp_path / "brain-size-series.jsonl"
    append_snapshot(
        series,
        date_str="2026-08-02",
        prompt_tokens=91234,
        memory_bytes=48000,
        per_file={"MEMORY.md": 48000, "AGENT.md": 7000},
    )
    rows = _rows(series)
    assert len(rows) == 1
    r = rows[0]
    assert r["date"] == "2026-08-02"
    assert r["prompt_tokens"] == 91234
    assert r["memory_bytes"] == 48000
    assert r["per_file"] == {"MEMORY.md": 48000, "AGENT.md": 7000}


def test_upsert_same_date_overwrites_not_appends(tmp_path: Path):
    series = tmp_path / "brain-size-series.jsonl"
    append_snapshot(series, date_str="2026-08-02", prompt_tokens=100, memory_bytes=1, per_file={})
    # second fire SAME day (e.g. a daemon restart) must NOT create a 2nd row
    append_snapshot(series, date_str="2026-08-02", prompt_tokens=200, memory_bytes=2, per_file={})
    rows = _rows(series)
    assert len(rows) == 1, "same-date snapshot must upsert, not append (no double-count)"
    assert rows[0]["prompt_tokens"] == 200, "last write wins"


def test_distinct_dates_accumulate_in_order(tmp_path: Path):
    series = tmp_path / "brain-size-series.jsonl"
    append_snapshot(series, date_str="2026-08-02", prompt_tokens=100, memory_bytes=1, per_file={})
    append_snapshot(series, date_str="2026-08-03", prompt_tokens=110, memory_bytes=2, per_file={})
    append_snapshot(series, date_str="2026-08-02", prompt_tokens=105, memory_bytes=3, per_file={})  # late correction to day 1
    rows = _rows(series)
    assert len(rows) == 2, "two distinct dates → two rows (the re-fire on 08-02 upserts)"
    assert [r["date"] for r in rows] == ["2026-08-02", "2026-08-03"], "date order preserved"
    assert rows[0]["prompt_tokens"] == 105, "08-02 upserted to latest value"


def test_read_series_empty_when_absent(tmp_path: Path):
    series = tmp_path / "nope.jsonl"
    assert read_series(series) == []


def test_read_series_returns_rows_in_order(tmp_path: Path):
    series = tmp_path / "brain-size-series.jsonl"
    append_snapshot(series, date_str="2026-08-01", prompt_tokens=90, memory_bytes=1, per_file={})
    append_snapshot(series, date_str="2026-08-02", prompt_tokens=91, memory_bytes=2, per_file={})
    got = read_series(series)
    assert [r["date"] for r in got] == ["2026-08-01", "2026-08-02"]


def test_corrupt_line_is_skipped_not_crash(tmp_path: Path):
    series = tmp_path / "brain-size-series.jsonl"
    series.write_text('{"date":"2026-08-01","prompt_tokens":90,"memory_bytes":1,"per_file":{}}\nNOT JSON\n')
    # a corrupt row must not crash the reader (fail-open observability, not a gate)
    got = read_series(series)
    assert len(got) == 1 and got[0]["date"] == "2026-08-01"


def test_hook_append_writes_snapshot_from_measurement(tmp_path: Path):
    """DoD1: the health hook's _append_brain_size_snapshot forces one real write.

    Drives the actual hook method with a populated _token_measurement + a real
    MEMORY.md on disk, and asserts a series row lands with the measured numbers.
    """
    from hooks.context_health_hook import ContextHealthHook
    from core.brain_size_series import SERIES_RELPATH, read_series

    root = tmp_path
    ctx = root / ".context"
    ctx.mkdir(parents=True)
    (ctx / "MEMORY.md").write_text("x" * 4096, encoding="utf-8")  # 4096 bytes

    hook = ContextHealthHook.__new__(ContextHealthHook)  # no __init__ side effects
    hook._token_measurement = {
        "total_tokens": 91234,
        "per_file": {"MEMORY.md": 48000, "AGENT.md": 7000},
    }
    hook._append_brain_size_snapshot(root, ctx)

    rows = read_series(root / SERIES_RELPATH)
    assert len(rows) == 1
    assert rows[0]["prompt_tokens"] == 91234
    assert rows[0]["memory_bytes"] == 4096
    assert rows[0]["per_file"]["MEMORY.md"] == 48000

    # idempotent: a second same-day fire (restart) upserts, no 2nd row
    hook._append_brain_size_snapshot(root, ctx)
    assert len(read_series(root / SERIES_RELPATH)) == 1


def test_hook_append_noops_without_measurement(tmp_path: Path):
    """If budget wasn't measured this run, snapshot is a safe no-op (no crash, no row)."""
    from hooks.context_health_hook import ContextHealthHook
    from core.brain_size_series import SERIES_RELPATH, read_series

    root = tmp_path
    ctx = root / ".context"
    ctx.mkdir(parents=True)
    hook = ContextHealthHook.__new__(ContextHealthHook)
    hook._token_measurement = None  # not measured
    hook._append_brain_size_snapshot(root, ctx)  # must not crash
    assert read_series(root / SERIES_RELPATH) == []
