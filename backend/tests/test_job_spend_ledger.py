"""The Jobs page's monthly spend is the usage ledger's (core/usage_report.month_spend),
not a counter of its own: the old counter skipped failed runs and priced a run the CLI
reported no cost for at Sonnet's rates whatever model it ran."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from core import usage_report


def _conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE usage_ledger (kind TEXT, machine TEXT, surface TEXT, at_utc TEXT, cost_usd REAL, "
                 "input INT DEFAULT 0, output INT DEFAULT 0, cache_read INT DEFAULT 0, cache_write INT DEFAULT 0)")
    conn.executemany("INSERT INTO usage_ledger (kind, machine, surface, at_utc, cost_usd, input) "
                     "VALUES (?, ?, ?, ?, ?, 10)", rows)
    return conn


def _utc(local: str) -> str:
    return datetime.fromisoformat(local).astimezone().astimezone(timezone.utc).isoformat(timespec="seconds")


def test_month_spend_is_this_hosts_job_rows_since_the_first_of_the_local_month():
    now = datetime(2026, 10, 30, 12, 0).astimezone()
    conn = _conn([
        ("spend", "host", "job", _utc("2026-10-01T00:00:01"), 1.5),        # this month
        ("spend", "host", "job", _utc("2026-10-29T23:00:00"), 2.25),       # a failed run's spend too
        ("spend", "host", "job", _utc("2026-09-30T23:59:00"), 100.0),      # last month
        ("spend", "host", "chat", _utc("2026-10-10T10:00:00"), 50.0),      # another surface
        ("spend", "xg-hive", "job", _utc("2026-10-10T10:00:00"), 50.0),    # a Hive's job
        ("attribution", "host", "job", _utc("2026-10-10T10:00:00"), 50.0),  # a split, never added
        ("spend", "host", "job", _utc("2026-10-11T10:00:00"), None),       # unpriced
    ])
    # the unpriced row is counted, so the page shows the sum as "at least"
    assert usage_report.month_spend(conn, "job", now) == {"cost_usd": 3.75, "unpriced": 1}
