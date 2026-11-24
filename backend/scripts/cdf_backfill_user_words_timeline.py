"""One-time: apply the person's-words rule and the third-party TIMELINE rule to what CDF
already wrote.

Before this change a unit that cited any tool output lost its ``user_quote``: every
correction a session review produced (it cites the work's tool output beside the person's
message) was rerouted from EVOLUTION / MEMORY to a project DDD, and every TIMELINE event
citing tool output went under "## Not injected", where the month summary never reads.
The live pipeline now admits the person's verified words with user_check's confirmation
whatever else is cited (pipeline_v2), and keeps out of the month only events whose cited
evidence is explicitly third-party (provenance.lineage_third_party). This script runs the
same decisions over what is already written:

1. **Rerouted every-turn units.** Each written unit whose destination says it was
   rerouted from EVOLUTION or MEMORY, whose ``user_quote`` is found verbatim in a cited
   user message (``pipeline_v2._facts``), whose inbox record still exists and whose DDD
   copy is still in its file. With ``--apply`` it is decided again by
   ``pipeline_v2.run_unit_v2`` (user_check included: the one model call) as a new
   candidate ``cand-<sha256("cdf-backfill-b1:" + old id)[:24]>`` carrying the same
   payload and evidence; when the decision writes to an every-turn file, the journaled
   writer writes it there and the DDD copy is withdrawn as overridden
   (``governed_edit.withdraw``: its text, reason and status kept in the write journal and
   the vault). Any other decision is recorded and nothing is written.
2. **TIMELINE events under "Not injected".** Each whose cited lineage is known to hold
   no explicitly third-party content moves under its month section with its
   ``taint:external`` mark removed, in one ``governed_edit.apply_user_edit`` of
   TIMELINE.md (archive first, baseline revised); its active relations are carried to
   its new anchor (the old ones are ended, kept), its unit's destination says injectable,
   and the move is recorded (``ingestion_events`` kind ``backfill_timeline``). For a ledger row written before the third-party flag, the answer is
   told from what is recorded (root kind, the ledger's kind and ref, the session
   transcript's tool call); an event whose answer cannot be told stays where it is.

Dry run by default: prints what it would do (no model call, nothing written, the stores
opened read-only). ``--apply`` first takes a backup of ``ingestion.db`` and ``data.db``
(SQLite backup, opened, ``integrity_check`` ok, every table's row count equal) and copies
every file it will touch (SHA-256 compared), and stops before any write when a copy
fails. It never deletes a row or a file. Running it again changes nothing: a decided unit
is recorded (``ingestion_events`` kind ``backfill_b1``), a moved event is no longer under
"Not injected". Run it on the person's machine, with the app running or not.

Usage: ``python -m scripts.cdf_backfill_user_words_timeline [--apply]`` from ``backend/``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

EVERY_TURN_DECLARED = ("EVOLUTION", "MEMORY")
TIMELINE = ".context/TIMELINE.md"
NOT_INJECTED = "Not injected"
B1_EVENT = "backfill_b1"
TIMELINE_EVENT = "backfill_timeline"
_TAINT = re.compile(r"\s*\|\s*taint:external(?=\s*(?:\||-->))")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else (value if value is not None else default)
    except (TypeError, ValueError):
        return default


def new_candidate_id(old: str) -> str:
    """The backfill's candidate for *old*: fixed, so a second run finds it."""
    return "cand-" + hashlib.sha256(f"cdf-backfill-b1:{old}".encode("utf-8")).hexdigest()[:24]


# ── backups ──────────────────────────────────────────────────────────────────

class BackupFailed(RuntimeError):
    """A copy could not be taken or did not verify: nothing is written."""


def _backup_db(db: Path, dest: Path) -> dict[str, Any]:
    if not db.is_file():
        raise BackupFailed(f"{db} not found")
    src = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(dest)
        try:
            src.backup(copy)
        finally:
            copy.close()
        check = sqlite3.connect(f"file:{dest.resolve().as_posix()}?mode=ro", uri=True)
        try:
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupFailed(f"{dest}: integrity_check failed")
            tables = [r[0] for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            counts = {}
            for table in tables:
                want = src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                got = check.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                if want != got:
                    raise BackupFailed(f"{dest}: {table} holds {got} rows, the store {want}")
                counts[table] = want
        finally:
            check.close()
    except sqlite3.Error as exc:
        raise BackupFailed(f"{db}: {exc}") from exc
    finally:
        src.close()
    return {"path": str(dest), "tables": len(counts)}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def take_backups(data_dir: Path, workspace: Path, files: list[Path], stamp: str) -> dict[str, Any]:
    """Back up both stores and *files* into ``<data dir>/backups/cdf-backfill-<stamp>/``;
    raise BackupFailed (before anything is written) when a copy does not verify."""
    from core.ingestion.store import ingestion_db_path
    from core import session_inputs
    root = data_dir / "backups" / f"cdf-backfill-{stamp}"
    root.mkdir(parents=True, exist_ok=False)
    out: dict[str, Any] = {"dir": str(root), "databases": [], "files": []}
    for db in (ingestion_db_path(), session_inputs._db_path()):
        out["databases"].append(_backup_db(db, root / db.name))
    for path in dict.fromkeys(files):
        if not path.is_file():
            continue
        try:
            rel = path.resolve().relative_to(workspace.resolve())
        except ValueError:
            rel = Path("outside") / path.name
        dest = root / "files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, dest)
            same = _sha(dest) == _sha(path)
        except OSError as exc:
            raise BackupFailed(f"{path}: {exc}") from exc
        if not same:
            raise BackupFailed(f"{path}: the copy's SHA-256 differs")
        out["files"].append(str(rel))
    return out


# ── 1. rerouted every-turn units ─────────────────────────────────────────────

def _copy_anchor(destination: dict[str, Any], unit_id: str) -> str:
    return f"{destination.get('relative_path')}#{destination.get('section') or ''}#cand:{unit_id}"


def _anchor_in_file(workspace: Path, anchor: str) -> bool:
    from core.ingestion.units import parse_units
    path = workspace / anchor.split("#", 1)[0]
    if not path.is_file():
        return False
    identity = anchor.rsplit("#", 1)[-1]
    return any(u.identity == identity for u in parse_units(path, root=workspace))


def _decided_before(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    out = {}
    for (detail,) in conn.execute("SELECT detail FROM ingestion_events WHERE kind=?", (B1_EVENT,)):
        value = _json(detail, {})
        if value.get("unit_id"):
            out[str(value["unit_id"])] = value
    return out


def plan_units(conn: sqlite3.Connection, workspace: Path) -> list[dict[str, Any]]:
    """Every rerouted every-turn unit and what the backfill does with it (``action``:
    ``redecide``, ``withdraw_copy`` for one written by an earlier run whose copy is still
    there, or ``skip`` with its ``reason``). Read-only: no model call."""
    from core.ingestion import pipeline_v2
    from core.ingestion.contracts_v2 import SOURCE_UNIT
    decided = _decided_before(conn)
    rows = conn.execute(
        """SELECT unit_id, candidate_id, destination, state FROM ingestion_units
           WHERE state='written' AND COALESCE(json_extract(destination,'$.rerouted_from'),
                                              rerouted_from) IN (?,?)
           ORDER BY unit_id""", EVERY_TURN_DECLARED).fetchall()
    out: list[dict[str, Any]] = []
    for unit_id, candidate_id, destination_raw, _state in rows:
        destination = _json(destination_raw, {})
        item = {"unit_id": unit_id, "declared_for": destination.get("rerouted_from"),
                "copy": _copy_anchor(destination, unit_id), "new_candidate": new_candidate_id(candidate_id)}
        out.append(item)
        new_unit = conn.execute("SELECT state FROM ingestion_units WHERE unit_id=?",
                                (f"{item['new_candidate']}.unit",)).fetchone()
        if new_unit is not None:
            in_file = _anchor_in_file(workspace, item["copy"])
            item.update(action="withdraw_copy" if new_unit[0] == "written" and in_file else "skip",
                        reason="written_before" if new_unit[0] == "written" else f"backfill_unit_{new_unit[0]}")
            continue
        if unit_id in decided:
            item.update(action="skip", reason=f"decided_before: {decided[unit_id].get('outcome')}")
            continue
        inbox = conn.execute("SELECT * FROM ingestion_inbox WHERE id=?", (candidate_id,)).fetchone()
        if inbox is None:
            item.update(action="skip", reason="record_purged")
            continue
        inbox = dict(inbox) if isinstance(inbox, sqlite3.Row) else inbox
        try:
            unit = SOURCE_UNIT.validate_python(_json(inbox["payload"], {}))
        except ValueError:
            item.update(action="skip", reason="payload_unreadable")
            continue
        item["title"] = getattr(unit, "title", "")
        if not getattr(unit, "user_quote", None):
            item.update(action="skip", reason="no_user_quote")
            continue
        envelope = _json(inbox.get("envelope"), {})
        resolved = SimpleNamespace(cited_external=bool(envelope.get("cited_external", True)),
                                   strength=envelope.get("strength", "inferred"),
                                   read_external=bool(envelope.get("read_external", True)))
        facts = pipeline_v2._facts(unit, inbox, resolved)
        if not facts.user_words:
            item.update(action="skip", reason="quote_not_in_the_persons_messages")
            continue
        if not _anchor_in_file(workspace, item["copy"]):
            item.update(action="skip", reason="copy_not_in_its_file")
            continue
        item.update(action="redecide", reason="verified_quote")
    return out


def _record(conn: sqlite3.Connection, detail: dict[str, Any]) -> None:
    conn.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES (?,?,?,1)",
                 (_now(), B1_EVENT, json.dumps(detail, ensure_ascii=False, sort_keys=True)))
    conn.commit()


def _withdraw_copy(item: dict[str, Any]) -> str:
    from core.ingestion.governed_edit import withdraw
    withdraw(item["copy"], f"written to {item['declared_for']} as cand:{item['new_candidate']}.unit "
                           "by the user-words backfill", actor="cdf", status="overridden")
    return "withdrawn"


def apply_unit_item(conn: sqlite3.Connection, workspace: Path, item: dict[str, Any]) -> str:
    """Decide one planned unit again and, when it goes to an every-turn file, write it
    there and withdraw its DDD copy. Returns the outcome."""
    from core.ingestion import pipeline_v2
    from core.ingestion.worker import _decision_fields
    from core.ingestion.writer import apply_unit
    if item["action"] == "withdraw_copy":
        return _withdraw_copy(item)
    old_candidate = conn.execute("SELECT candidate_id FROM ingestion_units WHERE unit_id=?",
                                 (item["unit_id"],)).fetchone()[0]
    inbox = dict(conn.execute("SELECT * FROM ingestion_inbox WHERE id=?", (old_candidate,)).fetchone())
    new_id = item["new_candidate"]
    unit_id = f"{new_id}.unit"
    row = {**inbox, "id": new_id, "unit_id": unit_id}
    decision = pipeline_v2.run_unit_v2(row, workspace)
    store = getattr(decision.destination, "store", None)
    every_turn = pipeline_v2.AGENT_SELF_STORES | pipeline_v2.USER_CONTEXT_STORES
    if decision.decision != "write" or store not in every_turn:
        outcome = f"stays: {decision.decision} {decision.reason} -> {store}"
        _record(conn, {"unit_id": item["unit_id"], "outcome": outcome})
        return outcome
    epoch_row = conn.execute("SELECT epoch FROM ingestion_owner WHERE id=1").fetchone()
    epoch = int(epoch_row[0]) if epoch_row else 0
    now = _now()
    claim = dict(_json(inbox["payload"], {}))
    claim["domain"] = _json(inbox.get("envelope"), {}).get("domain")
    claim["evidence_root_ids"] = _json(inbox.get("evidence_root_ids"), [])
    columns = [c for c in inbox if c not in ("id", "idem_key", "state", "owner_epoch", "created_at",
                                             "versions")]
    conn.execute(
        f"INSERT INTO ingestion_inbox(id,idem_key,state,owner_epoch,created_at,{','.join(columns)}) "
        f"VALUES (?,?,?,?,?,{','.join('?' * len(columns))})",
        (new_id, hashlib.sha256(f"cdf-backfill-b1|{inbox['idem_key']}".encode()).hexdigest(),
         "in_progress", epoch, now, *[inbox[c] for c in columns]))
    fields = _decision_fields(decision)
    conn.execute(
        """INSERT INTO ingestion_units(unit_id,candidate_id,claim_group,decision,destination,project,
                                       action,reason,state,owner_epoch,updated_at)
           VALUES (?,?,?,?,?,?,?,?,'judged',?,?)""",
        (unit_id, new_id, json.dumps(claim, ensure_ascii=False, sort_keys=True), fields["decision"],
         fields["destination"], fields["project"], fields["action"], fields["reason"], epoch, now))
    conn.commit()
    unit_row = conn.execute("SELECT * FROM ingestion_units WHERE unit_id=?", (unit_id,)).fetchone()
    step = apply_unit(decision, unit_row, conn)
    if step != "done":
        return f"write_not_done: {step}"   # the new unit keeps its state; a rerun withdraws later
    conn.execute("UPDATE ingestion_inbox SET state='done' WHERE id=?", (new_id,))
    conn.commit()
    outcome = f"written to {store}; copy {_withdraw_copy(item)}"
    _record(conn, {"unit_id": item["unit_id"], "outcome": outcome, "new_unit_id": unit_id})
    return outcome


# ── 2. TIMELINE events under "Not injected" ──────────────────────────────────

def plan_events(conn: sqlite3.Connection, workspace: Path) -> list[dict[str, Any]]:
    """Every CDF event under "Not injected", and whether it moves (``move``: its lineage
    is known to hold no explicitly third-party content). Read-only."""
    from core.ingestion.provenance import lineage_third_party
    from core.ingestion.units import parse_units
    path = workspace / TIMELINE
    if not path.is_file():
        return []
    out = []
    for unit in parse_units(path, root=workspace):
        if unit.section != NOT_INJECTED or not str(unit.identity).startswith("cand:"):
            continue
        unit_id = str(unit.identity)[len("cand:"):]
        head = re.match(r"###\s+(\d{4}-\d{2})", unit.text.lstrip())
        item: dict[str, Any] = {"anchor": unit.anchor, "unit_id": unit_id,
                                "title": unit.text.lstrip().split("\n", 1)[0][4:],
                                "month": head.group(1) if head else None}
        out.append(item)
        row = conn.execute("""SELECT i.envelope FROM ingestion_units u JOIN ingestion_inbox i
                              ON i.id=u.candidate_id WHERE u.unit_id=?""", (unit_id,)).fetchone()
        lineage = _json(row[0], {}).get("lineage_root_ids") if row else None
        if not item["month"]:
            item.update(move=False, reason="no_month")
        elif lineage is None:
            item.update(move=False, reason="record_purged")
        else:
            answer = lineage_third_party(lineage, conn=conn)
            item.update(move=answer is False,
                        reason={True: "third_party", None: "cannot_tell", False: "own_work"}[answer])
    return out


def _moved_text(content: str, workspace: Path, moves: list[dict[str, Any]]) -> str:
    """TIMELINE.md with each of *moves* under its month, its taint mark removed."""
    from core.ingestion.units import parse_units
    from core.ingestion.writer import _append_timeline
    path = workspace / TIMELINE
    data = content.encode("utf-8")
    spans = {u.anchor: (u.byte_start, u.byte_end) for u in parse_units(path, root=workspace)}
    blocks: list[tuple[str, str]] = []
    for item in sorted(moves, key=lambda m: spans[m["anchor"]][0], reverse=True):
        start, end = spans[item["anchor"]]
        block = data[start:end].decode("utf-8")
        blocks.append((item["month"], _TAINT.sub("", block).strip()))
        data = data[:start].rstrip(b"\n") + b"\n\n" + data[end:].lstrip(b"\n")
    text = data.decode("utf-8")
    for month, block in reversed(blocks):
        text = _append_timeline(text, {"when": month, "timeline_injectable": True}, block)
    return text


def apply_events(conn: sqlite3.Connection, workspace: Path, moves: list[dict[str, Any]]) -> int:
    from core.ingestion.governed_edit import apply_user_edit, revision_of
    if not moves:
        return 0
    path = workspace / TIMELINE
    revision = revision_of(path)
    new_text = _moved_text(path.read_text(encoding="utf-8"), workspace, moves)
    carried = []
    for item in moves:
        new_anchor = f"{TIMELINE}#{item['month']}#cand:{item['unit_id']}"
        for subject, predicate, obj in conn.execute(
                "SELECT subject_anchor,predicate,object_anchor FROM ingestion_relations "
                "WHERE inactive=0 AND (subject_anchor=? OR object_anchor=?)", (item["anchor"], item["anchor"])):
            carried.append((new_anchor if subject == item["anchor"] else subject, predicate,
                            new_anchor if obj == item["anchor"] else obj))
    # the edit ends the relations of each old anchor (the baseline patch marks them
    # inactive, kept); the same relations are carried to the new anchor
    apply_user_edit(path, new_text.encode("utf-8"), expected_revision=revision, actor="cdf-backfill")
    for subject, predicate, obj in carried:
        conn.execute("INSERT OR IGNORE INTO ingestion_relations(subject_anchor,predicate,object_anchor,"
                     "created,inactive) VALUES (?,?,?,?,0)", (subject, predicate, obj, _now()))
    for item in moves:
        # the unit's record says where it now is (read by the flow views)
        conn.execute("UPDATE ingestion_units SET destination=json_set(destination,'$.injectable',"
                     "json('true')),updated_at=? WHERE unit_id=?", (_now(), item["unit_id"]))
        conn.execute("INSERT INTO ingestion_events(at,kind,detail,resolved) VALUES (?,?,?,1)",
                     (_now(), TIMELINE_EVENT, json.dumps({"unit_id": item["unit_id"], "from": item["anchor"],
                                                          "to": f"{TIMELINE}#{item['month']}#cand:{item['unit_id']}"},
                                                         ensure_ascii=False, sort_keys=True)))
    conn.commit()
    return len(moves)


# ── the run ──────────────────────────────────────────────────────────────────

def run(workspace: Path, *, apply: bool) -> dict[str, Any]:
    from config import get_app_data_dir
    from core.ingestion.store import connect_ro, connect_rw
    workspace = Path(workspace)
    conn = connect_ro()
    try:
        units = plan_units(conn, workspace)
        events = plan_events(conn, workspace)
    finally:
        conn.close()
    todo = [u for u in units if u["action"] in ("redecide", "withdraw_copy")]
    moves = [e for e in events if e["move"]]
    report: dict[str, Any] = {
        "applied": apply,
        "units": {"redecide": [u for u in units if u["action"] == "redecide"],
                  "withdraw_copy": [u for u in units if u["action"] == "withdraw_copy"],
                  "skipped": [u for u in units if u["action"] == "skip"]},
        "events": {"move": moves, "stay": [e for e in events if not e["move"]]},
    }
    if not apply or not (todo or moves):
        return report
    from core.ingestion.writer import baseline_path
    files = [workspace / TIMELINE, workspace / ".context/EVOLUTION.md", workspace / ".context/MEMORY.md",
             *(workspace / u["copy"].split("#", 1)[0] for u in todo), baseline_path()]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report["backup"] = take_backups(Path(get_app_data_dir()), workspace, files, stamp)
    conn = connect_rw()
    conn.row_factory = sqlite3.Row
    try:
        for item in todo:
            try:
                item["outcome"] = apply_unit_item(conn, workspace, item)
            except Exception as exc:  # noqa: BLE001 -- reported per unit; the rest go on
                if conn.in_transaction:
                    conn.rollback()
                item["outcome"] = f"failed: {type(exc).__name__}: {exc}"
        report["events_moved"] = apply_events(conn, workspace, moves)
    finally:
        conn.close()
    return report


def _summary(report: dict[str, Any]) -> str:
    lines = [f"{'APPLIED' if report['applied'] else 'DRY RUN (nothing written; --apply to run)'}"]
    units = report["units"]
    lines.append(f"rerouted every-turn units to decide again: {len(units['redecide'])}"
                 f" (+{len(units['withdraw_copy'])} copies to withdraw), skipped {len(units['skipped'])}")
    for u in units["redecide"] + units["withdraw_copy"]:
        lines.append(f"  {u['unit_id']} [{u['declared_for']}] {u.get('title', '')}: "
                     f"{u.get('outcome', 'would ask user_check, then write and withdraw ' + u['copy'])}")
    reasons: dict[str, int] = {}
    for u in units["skipped"]:
        reasons[u["reason"]] = reasons.get(u["reason"], 0) + 1
    for reason, n in sorted(reasons.items()):
        lines.append(f"  skipped ({reason}): {n}")
    events = report["events"]
    lines.append(f"TIMELINE events under Not injected to move under their month: {len(events['move'])},"
                 f" staying: {len(events['stay'])}")
    for e in events["move"]:
        lines.append(f"  -> ## {e['month']}: {e['title']}")
    stay: dict[str, int] = {}
    for e in events["stay"]:
        stay[e["reason"]] = stay.get(e["reason"], 0) + 1
    for reason, n in sorted(stay.items()):
        lines.append(f"  staying ({reason}): {n}")
    if report.get("backup"):
        lines.append(f"backup: {report['backup']['dir']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="write (after a verified backup)")
    parser.add_argument("--json", action="store_true", help="print the whole report as JSON")
    args = parser.parse_args(argv)
    from config import get_swarmws
    try:
        report = run(Path(get_swarmws()), apply=args.apply)
    except BackupFailed as exc:
        print(f"stopped before any write: the backup failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str) if args.json else _summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
