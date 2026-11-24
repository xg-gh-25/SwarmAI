"""Migration v23, hive_instances.carrier_config (design
docs/design/2026-11-24-swarm-out-ssh-machine.md §4.2).

Question: from a store at v21, at v22 with the idle stop's column, and at v22 as an earlier
build of the SSH change stamped it (carrier_config, no idle_stop_minutes), does opening it end
at the current version with both columns and its rows unchanged?
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from database.sqlite import CURRENT_SCHEMA_VERSION, SQLiteDatabase


def _open(path) -> None:
    asyncio.run(SQLiteDatabase(path).initialize())


def _cols(path) -> set:
    with sqlite3.connect(path) as c:
        return {r[1] for r in c.execute("PRAGMA table_info(hive_instances)")}


@pytest.mark.parametrize("drop, version", [
    (("carrier_config", "idle_stop_minutes"), 21),     # before both
    (("carrier_config",), 22),                         # main's v22 (the idle stop)
    (("idle_stop_minutes",), 22),                      # an earlier build's v22 (carrier_config)
])
def test_v23_ends_with_both_columns(tmp_path, drop, version):
    db = tmp_path / "data.db"
    _open(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO hive_instances (id, name, account_ref, created_at, updated_at) "
                  "VALUES ('h1', 'one', 'swarmai', '2026-11-24', '2026-11-24')")
        for col in drop:
            c.execute(f"ALTER TABLE hive_instances DROP COLUMN {col}")
        c.execute(f"PRAGMA user_version = {version}")
    _open(db)
    assert {"carrier_config", "idle_stop_minutes"} <= _cols(db)
    with sqlite3.connect(db) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION >= 23
        assert c.execute("SELECT id, name, carrier_config, idle_stop_minutes FROM hive_instances").fetchall() \
            == [("h1", "one", None, None)]
