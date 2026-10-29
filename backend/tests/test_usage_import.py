"""core/usage_import (design 2026-10-29-usage-accounting §7, §10 invariant 8).

Questions: is only what came before the ledger's first live row brought in (rows
written to both tables after it never counted twice); is a token_usage row's cost its
increase over the session's previous row, a lower total a new process; is the CDF's day
of the cutover only the part the ledger does not hold; does running it again write
nothing; are the old tables left byte for byte as they were.
"""
from __future__ import annotations

import hashlib
import sqlite3

from core import usage_import
from database.sqlite import _USAGE_LEDGER_DDL


def _make(tmp_path):
    data, ing = tmp_path / "data.db", tmp_path / "ingestion.db"
    with sqlite3.connect(data) as c:
        c.execute("""CREATE TABLE token_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                     session_id TEXT, source TEXT NOT NULL DEFAULT 'cli', input_tokens INTEGER NOT NULL DEFAULT 0,
                     output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                     cache_create_tokens INTEGER NOT NULL DEFAULT 0, cost_usd REAL, model TEXT)""")
        for stmt in _USAGE_LEDGER_DDL:
            c.execute(stmt)
    with sqlite3.connect(ing) as c:
        c.execute("CREATE TABLE ingestion_llm_usage (utc_day TEXT, source TEXT, calls INTEGER, "
                  "input_tokens INTEGER, output_tokens INTEGER, PRIMARY KEY (utc_day, source))")
    return data, ing


def _tu(data, rows):
    with sqlite3.connect(data) as c:
        c.executemany("INSERT INTO token_usage(timestamp, session_id, source, input_tokens, output_tokens, "
                      "cache_read_tokens, cache_create_tokens, cost_usd, model) VALUES (?,?,?,?,?,?,?,?,?)", rows)


def _live(data, at, feature=None, i=0, o=0, key="live-1"):
    with sqlite3.connect(data) as c:
        c.execute("INSERT INTO usage_ledger(at_utc, surface, feature, input, output, kind, imported, dedup_key) "
                  "VALUES (?, ?, ?, ?, ?, 'spend', 0, ?)", (at, "cdf" if feature else "chat", feature, i, o, key))


def _hash(path, table):
    with sqlite3.connect(path) as c:
        return hashlib.sha256(repr(c.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()).encode()).hexdigest()


def test_only_what_came_before_the_ledger_is_brought_in_once(tmp_path):
    data, ing = _make(tmp_path)
    cut = usage_import._utc("2026-10-29T12:00:00")          # local noon: the same UTC day anywhere
    day = cut[:10]
    _tu(data, [
        ("2026-10-28T10:00:00", "a", "cli", 1, 10, 100, 5, 1.0, "opus"),
        ("2026-10-28T10:05:00", "a", "cli", 1, 20, 200, 5, 1.5, "opus"),    # +0.5
        ("2026-10-28T10:06:00", "b", "cli", 2, 30, 300, 5, 2.0, "opus"),    # another session: 2.0
        ("2026-10-28T11:00:00", "a", "cli", 1, 5, 50, 1, 0.25, "opus"),     # a new process: 0.25
        ("2026-10-28T11:30:00", None, "background_job", 7, 3, 0, 0, None, "sonnet"),
        ("2026-10-29T13:00:00", "a", "cli", 1, 1, 1, 1, 0.5, "opus"),       # after the cutover: written twice
    ])
    _live(data, cut)
    with sqlite3.connect(ing) as c:
        c.executemany("INSERT INTO ingestion_llm_usage VALUES (?,?,?,?,?)",
                      [("2026-10-01", "S2", 10, 1000, 100), (day, "S2", 20, 2000, 200)])
    _live(data, cut, feature="cdf.s2", i=1500, o=150, key="live-cdf")
    before = (_hash(data, "token_usage"), _hash(ing, "ingestion_llm_usage"))

    assert usage_import.run(data, ing) == {"token_usage": 5, "cdf": 2}
    with sqlite3.connect(data) as c:
        got = c.execute("SELECT session_id, surface, cost_usd, cache_read FROM usage_ledger "
                        "WHERE dedup_key LIKE 'import:token_usage:%' ORDER BY id").fetchall()
        cdf = c.execute("SELECT feature, at_utc, input, output FROM usage_ledger "
                        "WHERE dedup_key LIKE 'import:cdf:%' ORDER BY at_utc").fetchall()
        imported = c.execute("SELECT MIN(imported) FROM usage_ledger WHERE dedup_key LIKE 'import:%'").fetchone()[0]
    assert got == [("a", "chat", 1.0, 100), ("a", "chat", 0.5, 200), ("b", "chat", 2.0, 300),
                   ("a", "chat", 0.25, 50), (None, "background", None, 0)]
    # the cutover day: only what the ledger's live CDF rows of the day do not hold
    assert cdf == [("cdf.s2", "2026-10-01T12:00:00+00:00", 1000, 100), ("cdf.s2", f"{day}T12:00:00+00:00", 500, 50)]
    assert imported == 1
    assert usage_import.run(data, ing) == {"token_usage": 0, "cdf": 0}      # again: nothing new
    assert (_hash(data, "token_usage"), _hash(ing, "ingestion_llm_usage")) == before


def test_an_install_without_the_old_tables_imports_nothing(tmp_path):
    data = tmp_path / "data.db"
    with sqlite3.connect(data) as c:
        for stmt in _USAGE_LEDGER_DDL:
            c.execute(stmt)
    assert usage_import.run(data, tmp_path / "missing.db") == {"token_usage": 0, "cdf": 0}
