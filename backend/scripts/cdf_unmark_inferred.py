"""One-time: take the ``taint: external`` mark off DDD entries that cite no external content.

Before cdf-agent-decides step 2 (C1) the writer marked an entry external when it was
external, but also when it had been rerouted to a project DDD, judged unsupported, or
was a non-injected event. The baseline then registered every marked entry as external
evidence, so a unit citing it was external too. Now the mark means one thing: the
entry's own evidence includes external content (``cited_external``); what an entry
rests on otherwise is its recorded basis (``unit_basis``: inferred here, never user).

What it does, for the CDF-written entries (a ``cand:`` marker) of every project DDD
(``Projects/*/2-understanding/*.md``):

1. finds the marked entries whose candidate's evidence lineage holds no external root;
2. sets the evidence roots of those entries (``corpus_unit`` roots whose locator ends
   in the entry's identity) to ``inferred``, and repeats from 1 until nothing changes
   (an entry that cited a now-inferred entry may itself cite nothing external);
3. records each entry's basis: ``inferred`` for those, ``external`` for the rest;
4. removes the mark from those entries through ``governed_edit.apply_user_edit``
   (archive first, baseline revised, so the entries are re-registered as inferred).

Dry run by default (prints the counts). ``--apply`` first copies ``ingestion.db``
(SQLite backup, checked to open and to hold every row) and refuses to continue if the
copy fails. Run once, after the daemon has migrated the store to schema v9. Running it
again changes nothing (the fixed point is reached, marks already gone are skipped).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_CAND = re.compile(r"cand:(cand-[0-9a-f]+)\.unit")
_MARK = " | taint:external"


def _entries(workspace: Path) -> dict[Path, list[tuple[int, str, bool]]]:
    """Per DDD file: (line of the entry, candidate id, marked) of each CDF entry."""
    out: dict[Path, list[tuple[int, str, bool]]] = {}
    for path in sorted(workspace.glob("Projects/*/2-understanding/*.md")):
        if "-archive" in path.name:
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            m = _CAND.search(line)
            if not m or not line.lstrip().startswith("- ["):
                continue
            meta = lines[i + 1] if i + 1 < len(lines) else ""
            marked = meta.lstrip().startswith("<!--") and "taint:external" in meta
            out.setdefault(path, []).append((i, m.group(1), marked))
    return out


def _lineage(conn: sqlite3.Connection, candidate: str) -> list[str] | None:
    row = conn.execute("SELECT envelope FROM ingestion_inbox WHERE id=?", (candidate,)).fetchone()
    if row is None:
        return None
    try:
        return list(json.loads(row[0] or "{}").get("lineage_root_ids") or [])
    except ValueError:
        return None


def plan(conn: sqlite3.Connection, workspace: Path) -> tuple[set[str], set[str]]:
    """(candidates to unmark, candidates that stay external), at the fixed point. Roots
    are updated in *conn* (the caller commits or rolls back)."""
    entries = _entries(workspace)
    marked = {cand for rows in entries.values() for _, cand, m in rows if m}
    unmark: set[str] = set()
    while True:
        trust = dict(conn.execute("SELECT root_id, trust FROM evidence_roots"))
        new = set()
        for cand in marked - unmark:
            lineage = _lineage(conn, cand)
            if lineage is None:
                continue                              # no record of what it cites: stays
            if not any(trust.get(r, "external") == "external" for r in lineage):
                new.add(cand)
        if not new:
            break
        for cand in new:
            conn.execute("UPDATE evidence_roots SET trust='inferred' WHERE root_kind='corpus_unit' "
                         "AND trust='external' AND locator LIKE ?", (f"%#cand:{cand}.unit",))
        unmark |= new
    return unmark, marked - unmark


def _backup(db: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = db.with_name(f"{db.name}.pre-unmark-inferred-{stamp}.bak")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(dest)
        src.backup(copy)
        copy.close()
        check = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
        try:
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            for (table,) in src.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                        "AND name NOT LIKE 'sqlite_%'"):
                assert (check.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                        == src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]), table
        finally:
            check.close()
    finally:
        src.close()
    return dest


def run(workspace: Path, *, apply: bool) -> dict:
    from core.ingestion.store import connect_rw, ingestion_db_path
    db = ingestion_db_path()
    if apply:
        backup = _backup(db)
    conn = connect_rw()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='unit_basis'").fetchone():
            raise SystemExit("the store is not at schema v9 yet (no unit_basis): restart the daemon first")
        unmark, external = plan(conn, workspace)
        if not apply:
            conn.rollback()
            return {"unmark": len(unmark), "stay_external": len(external), "applied": False}
        now = datetime.now(timezone.utc).isoformat()
        for cand, basis in [(c, "inferred") for c in unmark] + [(c, "external") for c in external]:
            conn.execute("INSERT INTO unit_basis(unit_id,basis,recorded_at) VALUES(?,?,?) "
                         "ON CONFLICT(unit_id) DO UPDATE SET basis=excluded.basis,"
                         "recorded_at=excluded.recorded_at", (f"{cand}.unit", basis, now))
        conn.commit()
    finally:
        conn.close()
    from core.ingestion.governed_edit import apply_user_edit, revision_of
    edited = 0
    for path, rows in _entries(workspace).items():
        lines = path.read_text(encoding="utf-8").split("\n")
        changed = False
        for i, cand, marked in rows:
            if marked and cand in unmark and _MARK in lines[i + 1]:
                lines[i + 1] = lines[i + 1].replace(_MARK, "", 1)
                changed = True
        if changed:
            data = "\n".join(lines).encode("utf-8")
            apply_user_edit(path, data, expected_revision=revision_of(path), actor="cdf-unmark-inferred")
            edited += 1
    return {"unmark": len(unmark), "stay_external": len(external), "files": edited,
            "backup": str(backup), "applied": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    from config import get_swarmws
    print(json.dumps(run(Path(get_swarmws()), apply=args.apply)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
