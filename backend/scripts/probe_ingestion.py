#!/usr/bin/env python3
"""probe_ingestion — product eval probes for the stores knowledge is admitted into.

Contract: scripts/_probe_kit.py. Subcommands:
  preserve    a store that looks corrupt is renamed aside with its data intact,
              never deleted; a locked database is not treated as corrupt
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts._probe_kit import run, tempdir, verdict  # first: it isolates the data dir

# ── preserve ──────────────────────────────────────────────────────────────────

def _preserve_holds(isolate) -> tuple[bool, str]:
    from core.data_safety import is_corruption_error

    if is_corruption_error(sqlite3.OperationalError("database is locked")):
        return False, "a locked database was classified as corrupt"
    if not is_corruption_error(sqlite3.DatabaseError("file is not a database")):
        return False, "real corruption was not recognised"
    with tempdir() as d:
        db = d / "store.db"
        db.write_bytes(b"USER DATA 7f3")
        Path(str(db) + "-wal").write_bytes(b"WAL 7f3")
        isolate(db)
        kept = [p for p in d.iterdir() if ".corrupt-" in p.name]
        data = b"".join(p.read_bytes() for p in kept)
        if b"USER DATA 7f3" not in data or b"WAL 7f3" not in data:
            return False, "the store or its WAL was not preserved under a .corrupt- name"
    return True, ""


def preserve(negative: bool) -> int:
    from core.data_safety import isolate_store

    return verdict("ING_PRESERVE", negative, lambda: _preserve_holds(isolate_store),
                   lambda: _preserve_holds(lambda p: Path(p).unlink()))  # a recovery that deletes


if __name__ == "__main__":
    raise SystemExit(run({"preserve": preserve}))
