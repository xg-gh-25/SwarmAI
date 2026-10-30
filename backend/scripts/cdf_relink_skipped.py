"""One-time: record again the relations skipped as "anchor not found" before the event
anchor fix of 2026-10-30, and close those events.

Until that fix a TIMELINE event's write plan anchored the new entry with an empty section
(``.context/TIMELINE.md##cand:<id>``), which names no entry, so every relation it carried
was skipped with a ``relation_skipped`` event. The entries themselves were written; only
the links between them are missing, and the Cognition flow view shows the events as an
anomaly although the cause is fixed.

For each unresolved ``relation_skipped`` event:

- ``derived_from`` whose two entries are both still on disk: each end is re-anchored at
  the section its entry is in now (found by its ``cand:`` identity) and recorded through
  the writer's own relation step (``writer._write_relations``), which checks the anchors
  again and emits a fresh event if one still names no entry;
- ``supersedes``: not recorded. TIMELINE only appends, and the entry it would replace is
  still in its file; recording it now would make the old entry resolve to the new one
  after the fact (``usage._active_anchor``), a change no write made;
- an end that is no longer on disk, or a refusal for a cycle: nothing to record.

Every handled event is marked resolved, and one ``relations_repaired`` event records what
was done with each. Dry run by default; ``--apply`` first copies ``ingestion.db`` (checked
backup, as ``cdf_move_misrouted``) and refuses to continue if the copy fails. It runs only
on a store already at this code's schema (the daemon migrated it).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def _real_anchor(anchor: str, workspace: Path) -> str | None:
    """*anchor* re-anchored at the section its entry is in now, or None when no entry
    with its identity is in its file."""
    from core.ingestion.units import parse_units
    try:
        path_value, _section, identity = anchor.rsplit("#", 2)
    except ValueError:
        return None
    path = workspace / path_value
    if not path.is_file():
        return None
    for unit in parse_units(path, root=workspace):
        if unit.identity == identity:
            return unit.anchor
    return None


def plan(conn: sqlite3.Connection, workspace: Path) -> list[dict]:
    """What to do with each unresolved skipped relation."""
    out = []
    for event_id, detail in conn.execute(
            "SELECT id, detail FROM ingestion_events WHERE kind='relation_skipped' AND resolved=0 ORDER BY id"):
        value = json.loads(detail or "{}")
        relation = value.get("relation") or {}
        predicate = relation.get("predicate")
        subject = _real_anchor(str(relation.get("subject_anchor") or ""), workspace)
        object_anchor = _real_anchor(str(relation.get("object_anchor") or ""), workspace)
        if value.get("reason") == "cycle":
            action = "cycle_refused"
        elif predicate == "supersedes":
            action = "supersedes_not_replayed"
        elif subject is None or object_anchor is None:
            action = "entry_gone"
        else:
            action = "relink"
        out.append({"event_id": event_id, "op_id": value.get("op_id"), "action": action,
                    "relation": {"subject_anchor": subject, "predicate": predicate,
                                 "object_anchor": object_anchor}})
    return out


def run(workspace: Path, *, apply: bool) -> dict:
    from core.ingestion.api_v2 import SCHEMA_V2_VERSION
    from core.ingestion.store import connect_ro, connect_rw, ingestion_db_path
    from scripts.cdf_move_misrouted import _backup
    from contextlib import closing
    with closing(connect_ro()) as probe:
        version = probe.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_V2_VERSION:
        raise SystemExit(f"store is at v{version}, this code is v{SCHEMA_V2_VERSION}: restart the daemon first")
    backup = _backup(ingestion_db_path()) if apply else None
    conn = connect_rw() if apply else connect_ro()
    try:
        items = plan(conn, workspace)
        counts = Counter(item["action"] for item in items)
        if apply:
            from core.ingestion.writer import _write_relations
            conn.execute("BEGIN IMMEDIATE")
            before = conn.execute("SELECT COUNT(*) FROM ingestion_relations").fetchone()[0]
            for item in items:
                if item["action"] == "relink":
                    _write_relations(conn, {"op_id": item["op_id"], "relations": [item["relation"]]})
                conn.execute("UPDATE ingestion_events SET resolved=1 WHERE id=?", (item["event_id"],))
            recorded = conn.execute("SELECT COUNT(*) FROM ingestion_relations").fetchone()[0] - before
            conn.execute(
                "INSERT INTO ingestion_events(at, kind, detail, resolved) VALUES (?, 'relations_repaired', ?, 1)",
                (datetime.now(timezone.utc).isoformat(), json.dumps(
                    {"counts": dict(counts), "recorded": recorded, "backup": str(backup),
                     "events": [{"id": i["event_id"], "action": i["action"]} for i in items]})))
            conn.execute("COMMIT")
            counts["recorded"] = recorded
        return {"apply": apply, "events": len(items), "counts": dict(counts),
                "backup": str(backup) if backup else None}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    from config import get_swarmws
    print(json.dumps(run(args.workspace or get_swarmws(), apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
