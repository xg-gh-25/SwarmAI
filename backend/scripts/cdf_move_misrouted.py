"""One-time: move entries a project DDD holds for a subject that is no project into the
General Brain.

Until the routing fix of 2026-10-30 a unit whose scope named a project that does not
exist went to the project of the session it came from. A document written in a SwarmAI
session ("CNTech IVT Autonomous Tenets") then put its decisions into SwarmAI's DDD, where
they answer SwarmAI questions with another subject's decisions. Such a unit now goes to
the General Brain under its subject (contracts_v2.route_general).

What it does, for the units given by ``--match`` (a slug prefix of the scope's project
name, e.g. ``cntech-ivt``; only names that are no project are taken):

1. finds each written unit's entry in the project DDD file it was written to;
2. appends the entries, unchanged (their ``cand:`` marker and metadata line with them),
   to ``Knowledge/Library/<subject>.md`` under the section they had, through
   ``governed_edit.apply_user_edit`` (archive first, baseline revised);
3. then removes them from the DDD files the same way (their old anchors' relations are
   made inactive by the baseline revision);
4. records each unit's new destination and a ``moved`` event with the old one, and
   records again, under the new anchors, the relations whose other end still exists.

Writing the destination first means an interruption leaves a copy, never a loss; running
it again skips entries already in the destination and finishes the removals. Dry run by
default. ``--apply`` first copies ``ingestion.db`` (SQLite backup, checked to open and to
hold every row) and refuses to continue if the copy fails. It runs only when the store is
already at this code's schema (the daemon migrated it): it never migrates the store
itself, which would leave the running daemon on a schema it refuses.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _backup(db: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = db.with_name(f"{db.name}.pre-move-misrouted-{stamp}.bak")
    # one read transaction for the copy and the counts: the daemon keeps writing, and a
    # count read after the copy compared a snapshot with a live table (never equal while
    # the worker runs). The copy must hold every row of the snapshot it was taken from.
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, isolation_level=None)
    try:
        src.execute("BEGIN")
        tables = [name for (name,) in src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts = {table: src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
        copy = sqlite3.connect(dest)
        src.backup(copy)
        copy.close()
        src.execute("COMMIT")
        check = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
        try:
            assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            for table in tables:
                assert check.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] == counts[table], table
        finally:
            check.close()
    finally:
        src.close()
    return dest


def _scope_name(group: dict) -> str | None:
    scope = str(group.get("scope") or "")
    return scope.split(":", 1)[1].strip() if scope.startswith("project:") else None


def find(conn: sqlite3.Connection, workspace: Path, match: str) -> list[dict]:
    """The written units to move: scope names no project, its slug starts with *match*,
    and the entry was written to a project DDD file."""
    from core.ingestion.contracts_v2 import slugify
    from core.ingestion.pipeline_v2 import _resolve_project
    out = []
    for unit_id, target, group in conn.execute(
            "SELECT u.unit_id, w.target_path, u.claim_group FROM ingestion_writes w "
            "JOIN ingestion_units u ON u.unit_id=w.unit_id WHERE w.step='done' "
            "AND w.op_kind='ingestion_unit' AND w.target_path LIKE 'Projects/%' ORDER BY w.created_at"):
        value = json.loads(group or "{}")
        name = _scope_name(value)
        if not name or not slugify(name).startswith(match) or _resolve_project(workspace, name):
            continue
        out.append({"unit_id": unit_id, "source": target, "scope": name})
    return out


def run(workspace: Path, *, match: str, subject: str, apply: bool) -> dict:
    from core.ingestion import governed_edit, writer
    from core.ingestion.contracts_v2 import GENERAL_BRAIN_DIR, slugify
    from core.ingestion.api_v2 import SCHEMA_V2_VERSION
    from core.ingestion.store import connect_ro, connect_rw, ingestion_db_path
    from core.ingestion.units import parse_units

    # the script never migrates the store: a run from code newer than the daemon would
    # move the live store to a schema the running daemon refuses (every connect fails)
    probe = connect_ro()
    try:
        version = probe.execute("PRAGMA user_version").fetchone()[0]
    finally:
        probe.close()
    if version != SCHEMA_V2_VERSION:
        raise SystemExit(f"the store is at schema {version}, this code at {SCHEMA_V2_VERSION}: "
                         "build and restart the daemon first (it migrates the store)")
    backup = _backup(ingestion_db_path()) if apply else None
    conn = connect_rw() if apply else connect_ro()
    try:
        units = find(conn, workspace, match)
        dest_rel = f"{GENERAL_BRAIN_DIR}/{slugify(subject)}.md"
        dest = workspace / dest_rel
        held = {u.identity for u in parse_units(dest, root=workspace)} if dest.is_file() else set()

        # the entries as they are in their files now (a later edit may have removed one)
        by_source: dict[str, list[dict]] = {}
        for unit in units:
            by_source.setdefault(unit["source"], []).append(unit)
        found: list[dict] = []
        for source, items in by_source.items():
            path = workspace / source
            if not path.is_file():
                continue
            parsed = {u.identity: u for u in parse_units(path, root=workspace)}
            data = path.read_bytes()
            for item in items:
                entry = parsed.get(f"cand:{item['unit_id']}")
                if entry is None:
                    continue
                start, end, _identity = governed_edit._redaction_span(path, entry.anchor)
                found.append({**item, "old_anchor": entry.anchor, "section": entry.section,
                              "start": start, "end": end,
                              "block": data[start:end].decode("utf-8").strip()})
        report = {"units": len(units), "in_files": len(found), "destination": dest_rel,
                  "by_source": {s: sum(f["source"] == s for f in found) for s in by_source},
                  "already_in_destination": sum(f"cand:{f['unit_id']}" in held for f in found),
                  "backup": str(backup) if backup else None}
        if not apply or not found:
            return report

        # the relations in force now: removing the entries makes their old anchors' inactive
        active = conn.execute("SELECT subject_anchor, predicate, object_anchor FROM "
                              "ingestion_relations WHERE inactive=0").fetchall()

        # 1. the destination first: an interruption leaves a copy, never a loss
        content = dest.read_text(encoding="utf-8") if dest.is_file() else ""
        before = governed_edit.revision_of(dest)
        for item in found:
            if f"cand:{item['unit_id']}" in held:
                continue
            content = writer._ensure_common_ddd(content, dest, item["section"])
            content = writer._append_section(content, item["section"], item["block"])
        if content.encode("utf-8") != (dest.read_bytes() if dest.is_file() else b""):
            governed_edit.apply_user_edit(dest, content.encode("utf-8"), expected_revision=before,
                                          actor="cdf-move-misrouted")
        new_anchor = {u.identity: u.anchor for u in parse_units(dest, root=workspace)}
        missing = [f for f in found if f"cand:{f['unit_id']}" not in new_anchor]
        if missing:
            raise SystemExit(f"{len(missing)} entries are not in {dest_rel}; nothing removed")

        # 2. then out of the DDD files, last entry first so the spans stay valid
        for source in sorted({f["source"] for f in found}):
            path = workspace / source
            revision = governed_edit.revision_of(path)
            data = path.read_bytes()
            for item in sorted((f for f in found if f["source"] == source), key=lambda f: -f["start"]):
                data = governed_edit._without(data, item["start"], item["end"])
            governed_edit.apply_user_edit(path, data, expected_revision=revision,
                                          actor="cdf-move-misrouted")

        # 3. the records: where each unit is now, and its relations under the new anchor
        now = datetime.now(timezone.utc).isoformat()
        moved = {f["old_anchor"]: new_anchor[f"cand:{f['unit_id']}"] for f in found}
        relinked = 0
        for item in found:
            anchor = moved[item["old_anchor"]]
            row = conn.execute("SELECT destination FROM ingestion_units WHERE unit_id=?",
                               (item["unit_id"],)).fetchone()
            old_destination = json.loads(row[0] or "{}") if row else {}
            destination = {**old_destination, "store": "COMMON_DDD", "relative_path": dest_rel,
                           "section": item["section"], "project": subject, "injectable": False}
            conn.execute("UPDATE ingestion_units SET destination=? WHERE unit_id=?",
                         (json.dumps(destination, ensure_ascii=False, sort_keys=True), item["unit_id"]))
            conn.execute("INSERT INTO ingestion_events(at,kind,detail) VALUES(?,'moved',?)",
                         (now, json.dumps({"unit_id": item["unit_id"], "from": item["old_anchor"],
                                           "to": anchor, "scope": item["scope"],
                                           "why": "its subject is no project; the session's "
                                                  "project had been taken for it"},
                                          ensure_ascii=False, sort_keys=True)))
        for subject_anchor, predicate, object_anchor in active:
            if subject_anchor not in moved and object_anchor not in moved:
                continue
            s = moved.get(subject_anchor, subject_anchor)
            o = moved.get(object_anchor, object_anchor)
            if writer._anchor_exists(s) and writer._anchor_exists(o):
                relinked += conn.execute(
                    "INSERT OR IGNORE INTO ingestion_relations(subject_anchor,predicate,object_anchor,"
                    "created,inactive) VALUES(?,?,?,?,0)", (s, predicate, o, now)).rowcount
        conn.commit()
        report.update(moved=len(found), relations_recorded=relinked)
        return report
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--match", required=True, help="slug prefix of the scope's project name")
    parser.add_argument("--subject", required=True, help="the General Brain subject they move to")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    from config import get_swarmws
    print(json.dumps(run(Path(get_swarmws()), match=args.match, subject=args.subject,
                         apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
