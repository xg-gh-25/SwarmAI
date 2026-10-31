"""One-time: move MEMORY.md entries that are not read-in-every-session knowledge to where
they are read when relevant (design docs/design/2026-10-31-memory-chain-audit.md M2).

MEMORY.md is injected whole into every session of every project. An entry belongs there
only if it changes how the agent judges in most tasks, whatever the project. The entries
in ``PLAN`` were judged one by one (2026-10-31, the agent with the full audit): each is
either knowledge only one project uses (SwarmAI's own modules, tools and pipeline) or a
fact about one tool, library or subject. They go to:

- ``ddd:<topic>`` — the SwarmAI DDD document and section CDF itself would pick for that
  type and topic (``contracts.DDD_MATRIX``), recalled when a session works on SwarmAI;
- ``general:<subject>`` — ``Knowledge/Library/<subject>.md`` (the General Brain), under
  the section the General Brain uses for that type, recalled when the subject comes up.

For each entry, matched by the start of its title (exactly one match, else refused):

1. the destination first, unchanged (its metadata line and any ``cand:`` marker with it),
   through ``governed_edit.apply_user_edit`` (archive first, baseline revised); an entry
   the destination already holds under the same title is not written again;
2. then out of MEMORY.md the same way;
3. a CDF entry's unit records its new destination and a ``moved`` event, and its
   relations whose other end still exists are recorded again under the new anchor.

Writing the destination first means an interruption leaves a copy, never a loss; running
it again finds the moved entries no longer in MEMORY and skips them. Dry run by default
(prints the plan with each entry's destination and whether it is already there).
``--apply`` copies ``ingestion.db`` first (SQLite backup, checked) and runs only when the
store is at this code's schema.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

PROJECT = "SwarmAI"
MEMORY = ".context/MEMORY.md"
EVOLUTION = ".context/EVOLUTION.md"

# (start of the entry's title, where it goes, why)
PLAN: list[tuple[str, str, str]] = [
    ("The READ path (recall + context-assembly", "ddd:product", "SwarmAI's own differentiator"),
    ("Knowledge extraction: confident-only", "ddd:architecture", "how SwarmAI's extraction works"),
    ("Memory sovereignty is a first principle", "ddd:product", "a SwarmAI product decision"),
    ("A reviewer prompt that does not start with the EXPERT-ID", "ddd:lesson", "SwarmAI's review tooling"),
    ("我的 open-thread 旧理论", "ddd:lesson", "SwarmAI's chat streaming"),
    ("Defense OUTSIDE the agent", "ddd:lesson", "SwarmAI's gate history"),
    ("When SwarmAI uses tool X as its engine", "ddd:lesson", "how SwarmAI compares to its engine"),
    ("The evolution loop proposes only concrete", "ddd:decision", "SwarmAI's evolution loop"),
    ("A surface/visibility classifier must anchor", "ddd:decision", "a SwarmAI classifier"),
    ("A DDD grown from real internal/customer work", "ddd:decision", "SwarmAI's public sample"),
    ("Single-agent with role-switching", "ddd:decision", "SwarmAI's architecture"),
    ("session_worktree land runs check_no_revert", "ddd:architecture", "a SwarmAI tool"),
    ("Cleaning an already-curated store", "ddd:lesson", "SwarmAI's CDF"),
    ("BedrockJudge sending both temperature", "ddd:lesson", "SwarmAI's eval judge"),
    ("Deploying one change at a time during active use", "ddd:lesson", "SwarmAI's deploys"),
    ("Classifying a turn as a pipeline from the presence", "ddd:lesson", "SwarmAI's chat view"),
    ("Gate findings must be serialized to strings", "ddd:lesson", "SwarmAI's pipeline"),
    ("needs_human_review resolves its owning tree", "ddd:lesson", "SwarmAI's pipeline"),
    ("mock not reached due to sys.platform guard", "ddd:lesson", "a SwarmAI test"),
    ("Slack bot users:read scope", "ddd:lesson", "SwarmAI's Slack channel"),
    ("Handing a missing nodeid straight to pytest", "general:General knowledge", "a pytest fact"),
    ("npm ENOTEMPTY error during rename", "general:General knowledge", "an npm fact"),
    ("Summing only input+output tokens", "general:General knowledge", "an API billing fact"),
    ("Lowercase path inconsistency between macOS and Linux", "general:General knowledge", "a file system fact"),
    ("pydantic ValidationError echoes refused field values", "general:General knowledge", "a pydantic fact"),
    ("Three-layer eval for autonomous loops", "general:CNTech IVT Autonomous Tenets",
     "the tenets document's content"),
]

# EVOLUTION.md entries (design A/B §7 E2), matched by their heading ("C070 |"); EVOLUTION is
# read in every session like MEMORY
EVOLUTION_PLAN: list[tuple[str, str, str]] = [
    ("K005 |", "general:General knowledge", "a .secrets.baseline tool fact"),
    ("C067 |", "ddd:lesson", "a constraint on SwarmAI's own memory design"),
    ("C070 |", "ddd:lesson", "SwarmAI's pipeline stages"),
    ("C071 |", "ddd:lesson", "SwarmAI's pipeline result view"),
]

# Open Threads (design §6 A1): not a MEMORY section any more. The threads that hold durable
# knowledge (a diagnosed mechanism, theories the source refuted) move whole, their marker
# turned into an entry type; the open work they listed became ToDos; the section leaves.
OPEN_THREADS = "Open Threads"
THREADS_TO_KEEP: list[tuple[str, str]] = [
    ("streaming freeze + overall slowness", "model"),
    ("\"Streaming looks stuck / backend offline", "pitfall"),
    ("Frontend reconcile race", "pitfall"),
]

_TYPE = re.compile(r"^\s*-\s*\[([a-z]+)\]")
# an EVOLUTION block: ``### C070 | 2026-10-29`` then ``- **Correction**: **Title** body``
_EVO = re.compile(r"^###\s+([CK]\d+)\s*\|\s*(\S+)[^\n]*\n-\s*\*\*\w+\*\*:\s*\*\*(.+?)\*\*\s*(.*)\Z", re.DOTALL)


def _as_entry(block: str, kind: str) -> tuple[str, str] | None:
    """(title, entry) for an EVOLUTION block moved into a document of entries: one
    ``- [kind] **Title** body (EVOLUTION C070, date)`` line, its ``cand:`` marker kept."""
    match = _EVO.match(block.strip())
    if match is None:
        return None
    number, day, title, body = match.groups()
    marker = re.search(r"<!--\s*cand:[^>]*-->", body)
    body = " ".join(re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).split())
    entry = f"- [{kind}] **{title}** {body} (EVOLUTION {number}, {day})"
    return title, entry + (f" {marker.group(0)}" if marker else "")


def _norm(title: str) -> str:
    return " ".join(str(title).replace("**", "").split()).casefold().rstrip(".")


def _destination(workspace: Path, where: str, kind: str) -> tuple[str, str, str | None]:
    """(workspace-relative file, section, General Brain subject or None) for *where*."""
    from core.ingestion.contracts import DDD_MATRIX
    from core.ingestion.contracts_v2 import GENERAL_BRAIN_DIR, _DEFAULT_TOPIC, slugify
    if where.startswith("ddd:"):
        from core.ddd_paths import ddd_path
        file_name, section = DDD_MATRIX[where[4:]][kind]
        path = ddd_path(workspace / "Projects" / PROJECT, file_name)
        return path.relative_to(workspace).as_posix(), section, None
    subject = where[len("general:"):]
    section = DDD_MATRIX[_DEFAULT_TOPIC[kind]][kind][1]
    return f"{GENERAL_BRAIN_DIR}/{slugify(subject)}.md", section, subject


def plan(workspace: Path, source: str = MEMORY, entries: list[tuple[str, str, str]] | None = None) -> list[dict]:
    """Each planned entry found in *source* now, with its span and destination; an entry
    no longer there (moved before) is left out."""
    from core.ingestion import governed_edit
    from core.ingestion.units import parse_units
    memory = workspace / source
    units = parse_units(memory, root=workspace)
    data = memory.read_bytes()
    out = []
    for prefix, where, why in (PLAN if entries is None else entries):
        hits = [u for u in units if _norm(u.title).startswith(_norm(prefix))]
        if not hits:
            continue
        if len(hits) > 1:
            raise SystemExit(f"{prefix!r} matches {len(hits)} entries; make it unique")
        unit = hits[0]
        match = _TYPE.match(unit.text)
        kind = match.group(1) if match else ("correction" if prefix.startswith("C") and "|" in prefix
                                             else "guideline")
        rel, section, subject = _destination(workspace, where, kind)
        dest = workspace / rel
        held = ({_norm(u.title) for u in parse_units(dest, root=workspace)} if dest.is_file() else set())
        start, end, _identity = governed_edit._redaction_span(memory, unit.anchor)
        block = data[start:end].decode("utf-8").strip()
        dest_title = unit.title
        converted = _as_entry(block, kind) if block.startswith("###") else None
        if converted is not None:
            dest_title, block = converted
        out.append({"title": unit.title, "dest_title": dest_title, "anchor": unit.anchor,
                    "identity": unit.identity, "kind": kind, "why": why, "dest": rel,
                    "section": section, "subject": subject,
                    "already_there": _norm(dest_title) in held, "start": start, "end": end,
                    "block": block})
    return out


def _backup(db: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = db.with_name(f"{db.name}.memory-relocate-{stamp}.bak")
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        live = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for table in ("ingestion_units", "ingestion_writes", "ingestion_relations"):
                a = check.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                b = live.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if a < b:
                    raise SystemExit(f"backup {target} misses rows of {table}")
        finally:
            live.close()
    finally:
        check.close()
    return target


def run(workspace: Path, *, apply: bool, source: str = MEMORY,
        entries: list[tuple[str, str, str]] | None = None) -> dict:
    from core.ingestion import governed_edit, writer
    from core.ingestion.api_v2 import SCHEMA_V2_VERSION
    from core.ingestion.store import connect_ro, connect_rw, ingestion_db_path
    from core.ingestion.units import parse_units

    items = plan(workspace, source, entries)
    report = {"to_move": len(items), "already_in_destination": sum(i["already_there"] for i in items),
              "entries": [{k: i[k] for k in ("title", "kind", "dest", "section", "already_there", "why")}
                          for i in items]}
    if not apply or not items:
        return report
    probe = connect_ro()
    try:
        version = probe.execute("PRAGMA user_version").fetchone()[0]
    finally:
        probe.close()
    if version != SCHEMA_V2_VERSION:
        raise SystemExit(f"the store is at schema {version}, this code at {SCHEMA_V2_VERSION}: "
                         "build and restart the daemon first")
    report["backup"] = str(_backup(ingestion_db_path()))
    conn = connect_rw()
    try:
        active = conn.execute("SELECT subject_anchor, predicate, object_anchor FROM "
                              "ingestion_relations WHERE inactive=0").fetchall()
        # 1. every destination first: an interruption leaves a copy, never a loss
        for rel in sorted({i["dest"] for i in items}):
            dest = workspace / rel
            content = dest.read_text(encoding="utf-8") if dest.is_file() else ""
            before = governed_edit.revision_of(dest)
            for item in (i for i in items if i["dest"] == rel and not i["already_there"]):
                if item["subject"] is not None:
                    content = writer._ensure_common_ddd(content, dest, item["section"])
                content = writer._append_section(content, item["section"], item["block"])
            if content.encode("utf-8") != (dest.read_bytes() if dest.is_file() else b""):
                governed_edit.apply_user_edit(dest, content.encode("utf-8"), expected_revision=before,
                                              actor="memory-relocate")
        new_anchor: dict[str, str] = {}
        for rel in {i["dest"] for i in items}:
            for u in parse_units(workspace / rel, root=workspace):
                new_anchor.setdefault(_norm(u.title), u.anchor)
        missing = [i["title"] for i in items if _norm(i["dest_title"]) not in new_anchor]
        if missing:
            raise SystemExit(f"not in their destination, nothing removed: {missing}")
        # 2. then out of MEMORY, last entry first so the spans stay valid
        memory = workspace / source
        revision = governed_edit.revision_of(memory)
        data = memory.read_bytes()
        for item in sorted(items, key=lambda i: -i["start"]):
            data = governed_edit._without(data, item["start"], item["end"])
        governed_edit.apply_user_edit(memory, data, expected_revision=revision, actor="memory-relocate")
        # 3. a CDF entry's records: where its unit is now, and its relations
        now = datetime.now(timezone.utc).isoformat()
        moved = {i["anchor"]: new_anchor[_norm(i["dest_title"])] for i in items if i["identity"].startswith("cand:")}
        for item in items:
            if not item["identity"].startswith("cand:"):
                continue
            unit_id = item["identity"][len("cand:"):]
            row = conn.execute("SELECT destination FROM ingestion_units WHERE unit_id=?", (unit_id,)).fetchone()
            if row is None:
                continue
            old = json.loads(row[0] or "{}")
            store = "COMMON_DDD" if item["subject"] is not None else "DDD"
            destination = {**old, "store": store, "relative_path": item["dest"], "section": item["section"],
                           "project": item["subject"] or PROJECT, "injectable": False}
            conn.execute("UPDATE ingestion_units SET destination=? WHERE unit_id=?",
                         (json.dumps(destination, ensure_ascii=False, sort_keys=True), unit_id))
            conn.execute("INSERT INTO ingestion_events(at,kind,detail) VALUES(?,'moved',?)",
                         (now, json.dumps({"unit_id": unit_id, "from": item["anchor"], "to": moved[item["anchor"]],
                                           "why": "MEMORY is read in every session; " + item["why"]},
                                          ensure_ascii=False, sort_keys=True)))
        relinked = 0
        for subject_anchor, predicate, object_anchor in active:
            if subject_anchor not in moved and object_anchor not in moved:
                continue
            s, o = moved.get(subject_anchor, subject_anchor), moved.get(object_anchor, object_anchor)
            if writer._anchor_exists(s) and writer._anchor_exists(o):
                relinked += conn.execute(
                    "INSERT OR IGNORE INTO ingestion_relations(subject_anchor,predicate,object_anchor,"
                    "created,inactive) VALUES(?,?,?,?,0)", (s, predicate, o, now)).rowcount
        conn.commit()
        report.update(moved=len(items), relations_recorded=relinked)
        return report
    finally:
        conn.close()


def _open_threads(text: str) -> tuple[int, int, list[tuple[str, str]]] | None:
    """(start, end, [(kind, block)]) of the Open Threads section: its bounds (up to the
    next ``##`` heading or the closing footer) and the threads to keep, each one bullet
    with its indented continuation lines, its marker turned into ``[kind]``."""
    head = re.search(rf"^## {OPEN_THREADS}\s*$", text, re.MULTILINE)
    if head is None:
        return None
    rest = text[head.end():]
    nxt = re.search(r"^## |^---[ \t]*$", rest, re.MULTILINE)
    end = head.end() + (nxt.start() if nxt else len(rest))
    section = text[head.start():end]
    bullets = re.findall(r"^- .*(?:\n(?:[ \t]+\S.*|[ \t]*<!--.*))*", section, re.MULTILINE)
    keep = []
    for bullet in bullets:
        title = re.sub(r"^- \S+\s+\*\*", "", bullet, count=1)
        for start, kind in THREADS_TO_KEEP:
            if title.startswith(start):
                keep.append((kind, re.sub(r"^- \S+\s+\*\*", f"- [{kind}] **", bullet, count=1)))
    return head.start(), end, keep


def move_open_threads(workspace: Path, *, apply: bool) -> dict:
    """Move the kept threads to SwarmAI's IMPROVEMENT ``What to Watch For`` (once), then
    take the Open Threads section out of MEMORY.md."""
    from core.ddd_paths import ddd_path
    from core.ingestion import governed_edit, writer
    memory = workspace / MEMORY
    text = memory.read_text(encoding="utf-8")
    found = _open_threads(text)
    if found is None:
        return {"open_threads": False}
    start, end, keep = found
    dest = ddd_path(workspace / "Projects" / PROJECT, "IMPROVEMENT.md")
    report = {"open_threads": True, "kept": [b.split("**")[1][:80] for _k, b in keep],
              "destination": dest.relative_to(workspace).as_posix(), "section_chars": end - start}
    if not apply:
        return report
    content = dest.read_text(encoding="utf-8")
    before = governed_edit.revision_of(dest)
    for _kind, block in keep:
        title = block.split("**")[1]
        if f"**{title}**" not in content:
            content = writer._append_section(content, "What to Watch For", block)
    if content != dest.read_text(encoding="utf-8"):
        governed_edit.apply_user_edit(dest, content.encode("utf-8"), expected_revision=before,
                                      actor="memory-relocate")
    landed = dest.read_text(encoding="utf-8")
    missing = [b.split("**")[1] for _k, b in keep if f"**{b.split('**')[1]}**" not in landed]
    if missing:
        raise SystemExit(f"not in {dest}, MEMORY unchanged: {missing}")
    revision = governed_edit.revision_of(memory)
    left = (text[:start].rstrip("\n") + "\n\n" + text[end:].lstrip("\n")) if text[end:].strip() else text[:start].rstrip("\n") + "\n"
    governed_edit.apply_user_edit(memory, left.encode("utf-8"), expected_revision=revision, actor="memory-relocate")
    report["moved"] = len(keep)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--part", choices=("memory", "evolution", "open-threads"), default="memory")
    args = parser.parse_args(argv)
    from config import get_swarmws
    ws = Path(get_swarmws())
    if args.part == "open-threads":
        result = move_open_threads(ws, apply=args.apply)
    elif args.part == "evolution":
        result = run(ws, apply=args.apply, source=EVOLUTION, entries=EVOLUTION_PLAN)
    else:
        result = run(ws, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
