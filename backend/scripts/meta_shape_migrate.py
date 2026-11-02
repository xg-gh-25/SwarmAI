"""One-time: rewrite entry metadata lines from the old shape to the current one.

The old shape ``<!-- ref:N | last:<date> | decay:<state> | source:… | taint:… -->``
carried three dead fields (nothing decays with age any more); the current shape keeps
only what is set of ``source``, ``taint``, ``valid_until`` and ``superseded_by``
(``ddd_entry_lifecycle.meta_comment``). The lifecycle reads both shapes, so this is
not needed for correctness: it removes the dead fields from what recall and the
every-turn context files carry.

What it touches: the governed main files (the governed ``.context`` files, ``Knowledge/
{Notes,Library,Learned,Inbox}``, ``Projects/*`` outside ``assets/`` and ``.artifacts/``),
never an archive (``archiv`` in the file name or a directory name, any case). In a
file it rewrites only a line that ``parse_entries`` reads as an entry's own metadata
line, that is in the old shape, that is outside a code fence, and that has at least one
live field. A line with only the dead fields is kept as it is: an entry without a
metadata line is out of shape for ``ddd_format`` (``no_meta``), and writing a source it
never had would be a false provenance. Nothing else in the file changes.

Checked per file before writing, and the file is left alone when any check fails:
the entries ``parse_entries`` reads (title, section, text, source, taint, valid_until,
superseded_by) are the same; the ``cand:`` markers are the same; the unit identities
``units.parse_units`` reads are the same. Every line stays a metadata line (one with
only dead fields is never touched), so ``ddd_format``'s shape is unchanged. (Readers of the dead fields read their defaults afterwards:
``brain_graph`` shows the section instead of a last-referenced date, the duplicate
recommender breaks ref-count ties by date. Neither field carried information.) Writes go through
``governed_edit.apply_user_edit`` (archive first, baseline revised). ``--apply`` first
copies every file it will change into a backup directory and checks each copy byte
for byte. Dry run by default. Run it only after the running backend reads the new
shape (an older backend reads a new-shape line as no metadata, ``taint`` included).
Running it again changes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_SKIP_DIRS = {".git", ".artifacts", "assets", "node_modules", "__pycache__"}
_KNOWLEDGE = ("Notes", "Library", "Learned", "Inbox")
_CAND = re.compile(r"cand:([A-Za-z0-9_.:-]+)")


def candidates(workspace: Path) -> list[Path]:
    """Governed main markdown files that may carry entry metadata."""
    out: list[Path] = []
    roots = [workspace / "Knowledge" / n for n in _KNOWLEDGE] + [workspace / "Projects"]
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
                           and "archiv" not in d.lower()]
            out += [Path(dirpath) / f for f in filenames if f.endswith(".md")]
    ctx = workspace / ".context"
    if ctx.is_dir():
        from core.ingestion.materializer import _CONTEXT_NAMES  # the governed context files
        out += sorted(p for p in ctx.glob("*.md") if p.name in _CONTEXT_NAMES)
    return sorted(p for p in out if "archiv" not in p.name.lower())


def _entries(text: str) -> list[tuple]:
    """What every reader of an entry sees, read both ways (``###`` blocks skipped, as
    the lifecycle does, and not skipped)."""
    from core.ddd_entry_lifecycle import parse_entries
    return [(e.title, e.section, e.raw_text, e.source, e.taint, e.valid_until, e.superseded_by)
            for skip in (True, False) for e in parse_entries(text, skip_h3_spans=skip)]


def rewrite(text: str) -> tuple[str, int]:
    """(*text* with each old-shape entry metadata line that has a live field in the
    current shape, lines changed). Left as they are: a line with only dead fields, a
    line whose value would not survive (an unparseable valid_until), a line inside a
    code fence (a documentation example)."""
    from core.ddd_entry_lifecycle import (
        _ENTRY_RE, _META_RE, _scan_entry, meta_comment,
    )
    lines = text.split("\n")
    fenced, inside = set(), False
    for k, ln in enumerate(lines):
        if ln.lstrip().startswith(("```", "~~~")):
            inside = not inside
            fenced.add(k)
        elif inside:
            fenced.add(k)
    changed = 0
    i = 0
    while i < len(lines):
        if not _ENTRY_RE.match(lines[i]):
            i += 1
            continue
        text_end, meta_idx, span_end = _scan_entry(lines, i)
        if meta_idx is not None and meta_idx not in fenced:
            m = _META_RE.match(lines[meta_idx])
            if m and m.group(1) is not None:
                vu = m.group(6)
                ok = True
                if vu:
                    try:
                        from datetime import date
                        valid_until = date.fromisoformat(vu)
                    except ValueError:
                        ok = False
                else:
                    valid_until = None
                sup = (m.group(7) or "").strip() or None
                if sup == "null":
                    sup = None
                new = meta_comment(source=m.group(4) or "", taint=m.group(5) or "",
                                   valid_until=valid_until if ok else None, superseded_by=sup)
                if ok and new:
                    indent = re.match(r"^\s*", lines[meta_idx]).group(0)
                    lines[meta_idx] = indent + new.lstrip()
                    changed += 1
        i = max(span_end, i + 1)
    return "\n".join(lines), changed


def _identities(path: Path, text: str, workspace: Path) -> list[str]:
    from core.ingestion.units import parse_units
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / path.relative_to(workspace)
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(text, encoding="utf-8")
        return [u.identity for u in parse_units(copy, root=Path(tmp))]


def check(path: Path, before: str, after: str, workspace: Path) -> str | None:
    """None when the rewrite lost nothing, else what differs."""
    if _entries(before) != _entries(after):
        return "entries differ"
    if _CAND.findall(before) != _CAND.findall(after):
        return "cand markers differ"
    if _identities(path, before, workspace) != _identities(path, after, workspace):
        return "unit identities differ"
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--workspace", type=Path, default=None)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup-dir", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.workspace is None:
        from config import get_swarmws
        args.workspace = Path(get_swarmws())
    ws = args.workspace.expanduser().resolve()

    plan: list[tuple[Path, str, str, int]] = []
    refused: dict[str, str] = {}
    for path in candidates(ws):
        before = path.read_text(encoding="utf-8")
        if "ref:" not in before:
            continue
        after, n = rewrite(before)
        if not n:
            continue
        why = check(path, before, after, ws)
        if why:
            refused[str(path.relative_to(ws))] = why
            continue
        plan.append((path, before, after, n))

    report = {"files": len(plan), "lines": sum(n for *_, n in plan),
              "bytes_saved": sum(len(b.encode()) - len(a.encode()) for _, b, a, _ in plan),
              "refused": refused,
              "per_file": {str(p.relative_to(ws)): n for p, _, _, n in plan}}
    if not args.apply:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    from core.ingestion.governed_edit import apply_user_edit, revision_of
    backup = args.backup_dir or Path.home() / ".swarm-ai" / "backups" / (
        datetime.now().strftime("%Y-%m-%d-%H%M%S") + "-meta-shape")
    for path, before, _, _ in plan:
        dest = backup / path.relative_to(ws)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        if dest.read_bytes() != path.read_bytes():
            print(f"backup of {path} does not match; nothing written", file=sys.stderr)
            return 2
    written, failed = 0, {}
    for path, before, after, _ in plan:
        if path.read_text(encoding="utf-8") != before:
            failed[str(path.relative_to(ws))] = "changed since the plan"
            continue
        try:
            apply_user_edit(path, after.encode("utf-8"), expected_revision=revision_of(path),
                            actor="agent:meta-shape-migration")
            written += 1
        except Exception as exc:  # noqa: BLE001 — reported per file, the rest go on
            failed[str(path.relative_to(ws))] = f"{type(exc).__name__}: {exc}"
    report.update({"written": written, "failed": failed, "backup": str(backup)})
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
