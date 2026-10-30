#!/usr/bin/env python3
"""One-time brain cleanup (design docs/design/2026-10-30-brain-driven.md §2.6).

Brings the main cognition files (every project's four DDD docs, MEMORY.md, EVOLUTION.md)
to "the current valid whole":

1. an entry another one replaced in the same file, whose full content the new one holds,
   is withdrawn as ``overridden`` (only the anchors given with ``--override``: the person
   chose them from the list);
2. a ticked ``- [x]`` item is withdrawn (a gap as ``closed``, anything else ``done``);
3. an old withdrawal tombstone (``- [redacted] **Withdrawn** …``) is removed — its
   original is already in the withdrawal records;
4. ``decay:dormant`` / ``decay:archived`` become ``decay:active`` (the mechanical inverse
   of the age mark; nothing decays any more);
5. ``<!-- maturity: … -->`` lines are removed (no reader).

Every change goes through the governed paths (``governed_edit.withdraw`` and
``apply_user_edit``), so it is journaled and the CDF baseline follows. Default is a dry
run that prints what would change. ``--apply`` first writes a backup of every main file
(a tar.gz under ``<data dir>/backups/``) and checks that it opens and holds the same
bytes; with no verified backup nothing is changed. Running it twice changes nothing the
second time.

Usage:
    python -m scripts.brain_cleanup [--workspace DIR] [--override ANCHOR ...] [--apply]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

_TOMBSTONE = re.compile(r"^- \[redacted\] \*\*Withdrawn\*\*")
_DECAY = re.compile(r"(<!-- ref:\d+ \| last:[\w\-]+ \| decay:)(?:dormant|archived)\b")
_MATURITY_ONLY = re.compile(r"^[ \t]*<!-- maturity:[^\n]*-->[ \t]*\r?\n?$")


def cleaned_text(text: str) -> str:
    """*text* with tombstones and maturity lines removed and age marks set to active."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _MATURITY_ONLY.match(lines[i]):
            i += 1
            if out and not out[-1].strip() and i < len(lines) and not lines[i].strip():
                i += 1                   # one blank line, not two, where it stood
            continue
        if _TOMBSTONE.match(lines[i]):
            i += 1                       # the tombstone line and its continuation lines
            while i < len(lines) and lines[i].startswith(("  ", "\t")) and not lines[i].lstrip().startswith("- "):
                i += 1
            if out and not out[-1].strip() and i < len(lines) and not lines[i].strip():
                i += 1                   # one blank line, not two, where it stood
            continue
        out.append(lines[i])
        i += 1
    text = "".join(out)
    return _DECAY.sub(r"\1active", text)


def counts(text: str) -> dict[str, int]:
    return {"dormant": len(re.findall(r"decay:(?:dormant|archived)\b", text)),
            "maturity": sum(1 for line in text.splitlines(keepends=True) if _MATURITY_ONLY.match(line)),
            "tombstone": sum(1 for line in text.splitlines() if _TOMBSTONE.match(line)),
            "ticked": len(re.findall(r"^- \[x\]", text, re.M))}


def _backup(files: list[Path], data_dir: Path) -> Path:
    """A tar.gz of *files*, checked: it opens and every member has the file's bytes."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = data_dir / "backups" / f"brain-cleanup-{stamp}.tar.gz"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=str(f))
    want = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    with tarfile.open(dest, "r:gz") as tar:
        got = {m.name.lstrip("/"): hashlib.sha256(tar.extractfile(m).read()).hexdigest()
               for m in tar.getmembers() if m.isfile()}
    if {k.lstrip("/"): v for k, v in want.items()} != got:
        raise RuntimeError(f"backup {dest} does not hold the files; nothing changed")
    return dest


def run(workspace: Path, overrides: list[str], apply: bool) -> dict:
    from core.ingestion.gaps import main_cognition_files, withdraw_ticked
    from core.ingestion.governed_edit import apply_user_edit, revision_of, withdraw
    files = main_cognition_files(workspace)
    before = {str(f.relative_to(workspace)): counts(f.read_text(encoding="utf-8")) for f in files}
    report: dict = {"files": len(files), "before": _total(before), "overrides": overrides,
                    "per_file": {k: v for k, v in before.items() if any(v.values())}}
    if not apply:
        report["dry_run"] = True
        return report
    from config import get_app_data_dir
    report["backup"] = str(_backup(files, Path(get_app_data_dir())))
    withdrawn = 0
    for anchor in overrides:
        withdraw(anchor, "one-time cleanup: the newer entry in the same file holds its full content",
                 actor="cdf:cleanup", status="overridden")
        withdrawn += 1
    report["overridden"] = withdrawn
    report["ticked_withdrawn"] = withdraw_ticked(workspace, files=files)
    edited = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        new = cleaned_text(text)
        if new != text:
            apply_user_edit(f, new.encode("utf-8"), expected_revision=revision_of(f), actor="cdf:cleanup")
            edited += 1
    report["edited_files"] = edited
    after = {str(f.relative_to(workspace)): counts(f.read_text(encoding="utf-8")) for f in files}
    report["after"] = _total(after)
    return report


def _total(per_file: dict) -> dict:
    keys = ("dormant", "maturity", "tombstone", "ticked")
    return {k: sum(v[k] for v in per_file.values()) for k in keys}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--override", action="append", default=[], help="an anchor to withdraw as overridden")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    from config import get_swarmws
    ws = Path(args.workspace) if args.workspace else Path(get_swarmws())
    print(json.dumps(run(ws, args.override, args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
