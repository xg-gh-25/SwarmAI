#!/usr/bin/env python3
"""Context-file hygiene SCANNER (read-only candidate surfacer).

What it is
----------
A mechanical detector that SURFACES cleanup candidates in the 12 context files
so the agent can then READ-and-JUDGE each one semantically and delete manually.
It is the "mechanical surface" half of the read-and-judge method (see the skill's
INSTRUCTIONS.md); the "human judges + deletes" half is the agent, never this script.

🔴 HARD INVARIANT — THIS SCRIPT IS READ-ONLY.
It opens files in read mode ONLY. It has no --fix / --apply / --write mode and
MUST never grow one. Auto-modifying a curated cognitive store is forbidden by the
sedimented MEMORY principle ("cleaning an already-curated store is a READ-and-judge
job, NOT a batch auto pass") and by the C046 gut-and-summarize guard. If you are
tempted to add auto-fix here: STOP — that is the exact anti-pattern this skill exists
to prevent. The output is a candidate LIST; the agent decides and edits by hand.

Detectors (all heuristic — every hit is a CANDIDATE for human judgment, not a verdict):
  1. echoed-title  — a bold-titled entry whose body repeats the title verbatim
                     (`- [type] **T** — T — body`), pure duplication.
  2. drift-number  — a stored volatile figure (LOC/counts/sizes/%/star-snapshots)
                     that AGENT R30#4 bans from cognitive stores (store the
                     reproducible method, not the frozen output).
  3. dated-pointer — a dated one-line changelog fragment that only points at a
                     DailyActivity file (the DailyActivity IS the record; the pointer
                     is drift). Often a truncated mid-sentence residue.
  4. source-routing — for a given file, print WHERE its source-of-truth is and whether
                     editing it needs a rebuild. This is DERIVED LIVE from the loader's
                     CONTEXT_FILES list (never a frozen table — that would drift, the
                     exact sin the skill warns against; Gate-1 F1).

Usage
-----
  python scan.py --root <dir> [--file NAME] [--detector D[,D...]] [--json]
  python scan.py --routing [--file NAME]     # source-routing only (no scan)

  --root       dir holding the .md files (default: the workspace .context/)
  --file       restrict scan to one file (e.g. MEMORY.md)
  --detector   comma list of {echoed-title,drift-number,dated-pointer} (default: all)
  --json       machine-readable output
  --routing    print the live source-routing table (system vs runtime vs auto-gen)

Exit code is always 0 on a successful scan (candidates are informational, not errors);
non-zero only on a real failure (bad --root, unreadable tree).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Source-routing: DERIVED LIVE from the loader's CONTEXT_FILES (no frozen table).
# If the import fails (run outside the repo), fall back to a clearly-labelled
# static map so the scanner still runs — but the live path is preferred so the
# routing can never drift from context_directory_loader.py (Gate-1 F1 + R30#1).
# ─────────────────────────────────────────────────────────────────────────────
_AUTO_GENERATED = {
    # regenerated on startup / by context_health_hook — never hand-edit the auto part
    "PROJECTS.md": "whole file regenerated from Projects/ scan (_refresh_projects_md)",
    "KNOWLEDGE.md": "bottom 'Knowledge Index' section regenerated from Knowledge/ scan "
                    "(body ABOVE the index is agent-authored — that part IS editable)",
}


def _load_routing() -> tuple[list[dict], str]:
    """Return (rows, source_desc). rows: [{file, owner, source_of_truth, edit_effect}]."""
    try:
        # Prefer the live loader definition — single source of truth.
        repo = Path(__file__).resolve()
        for _ in range(12):
            repo = repo.parent
            if (repo / "backend" / "core" / "context_directory_loader.py").is_file():
                break
        sys.path.insert(0, str(repo / "backend"))
        from core.context_directory_loader import CONTEXT_FILES  # type: ignore

        rows = []
        for spec in CONTEXT_FILES:
            fn = spec.filename
            if not spec.user_customized:
                owner = "system"
                sot = "backend/context/%s (always-overwritten to .context/ on startup, chmod 0444)" % fn
                effect = "EDIT SOURCE backend/context/ + BUILD + RESTART (editing .context/ is overwritten)"
            else:
                owner = "runtime"
                sot = ".context/%s (copy-if-missing; the workspace copy IS the real store)" % fn
                effect = "edit .context/ directly — instant effect next session (no build)"
            if fn in _AUTO_GENERATED:
                owner = "auto"
                effect = "AUTO-GENERATED (%s) — never hand-edit the auto part" % _AUTO_GENERATED[fn]
            rows.append({"file": fn, "owner": owner, "source_of_truth": sot, "edit_effect": effect})
        return rows, "live:CONTEXT_FILES (context_directory_loader.py)"
    except Exception as exc:  # noqa: BLE001 — fall back, never crash the scan
        # Fallback static map (LABELLED as fallback so a stale row is obvious).
        # MUST mirror the live user_customized truth: SELF.md is user_customized=True
        # (runtime-owned, edit .context/ directly, NO rebuild) — it only READS like a
        # system file. Misclassifying it as system emits build-triggering edit guidance
        # for the exact file this skill advertises handling correctly (Gate-2 HIGH).
        system = ["SWARMAI.md", "IDENTITY.md", "SOUL.md", "AGENT.md"]
        runtime = ["SELF.md", "USER.md", "STEERING.md", "TOOLS.md", "MEMORY.md", "EVOLUTION.md", "KNOWLEDGE.md"]
        rows = []
        for fn in system:
            rows.append({"file": fn, "owner": "system",
                         "source_of_truth": "backend/context/%s (overwrite+chmod0444)" % fn,
                         "edit_effect": "EDIT SOURCE + BUILD + RESTART"})
        for fn in runtime:
            rows.append({"file": fn, "owner": "auto" if fn in _AUTO_GENERATED else "runtime",
                         "source_of_truth": _AUTO_GENERATED.get(fn, ".context/%s" % fn),
                         "edit_effect": "AUTO-GENERATED — don't hand-edit" if fn in _AUTO_GENERATED
                                        else "edit .context/ directly"})
        rows.append({"file": "PROJECTS.md", "owner": "auto",
                     "source_of_truth": _AUTO_GENERATED["PROJECTS.md"],
                     "edit_effect": "AUTO-GENERATED — don't hand-edit"})
        return rows, "FALLBACK static map (live import failed: %s)" % type(exc).__name__


# ─────────────────────────────────────────────────────────────────────────────
# Detectors — each yields candidate dicts {line, kind, reason, excerpt}.
# ─────────────────────────────────────────────────────────────────────────────
_ENTRY_RE = re.compile(r"^\s*- \[[a-z]+\] \*\*(.+?)\*\* — (.*)$")

# Volatile figures R30#4 bans from cognitive stores. Deliberately conservative:
# only flags numbers with a unit/context that marks them as a frozen snapshot,
# not every digit (dates, rule refs like R30, section numbers are NOT drift).
_DRIFT_RE = re.compile(
    r"(?<![\w.])("
    r"\d[\d,]*\s*(?:LOC|lines?|files?|tests?|skills?|entries|commits?|stars?|stargazers?|K\s*tokens?|KB|MB|GB)"
    r"|\d{1,3}\s*%\s*(?:util|full|coverage|consumed|used)?"
    r"|~\s*\d[\d,]*\s*(?:K|LOC|lines?|tokens?|files?)"
    r")",
    re.IGNORECASE,
)
_DATED_POINTER_RE = re.compile(r"^\s*- \d{4}-\d{2}-\d{2}: ")


def _echoed_title(lines: list[str]) -> list[dict]:
    out = []
    for i, ln in enumerate(lines):
        m = _ENTRY_RE.match(ln)
        if not m:
            continue
        title, body = m.group(1).strip(), m.group(2).strip()
        head = title[:15]
        if head and (body.startswith(title) or body.startswith(head)):
            out.append({"line": i + 1, "kind": "echoed-title",
                        "reason": "body repeats the bold title verbatim — pure duplication",
                        "excerpt": ln.strip()[:120]})
    return out


def _drift_number(lines: list[str]) -> list[dict]:
    out = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("<!--") or s.startswith("```"):
            continue
        # skip a line that already teaches the reproducible-method fix (not drift)
        if "measure live" in s.lower() or "git ls-files" in s or "reproducible method" in s.lower():
            continue
        for mt in _DRIFT_RE.finditer(ln):
            out.append({"line": i + 1, "kind": "drift-number",
                        "reason": "volatile figure (R30#4) — store the reproducible method or a qualitative fact, not the frozen output: '%s'" % mt.group(1).strip(),
                        "excerpt": s[:120]})
            break  # one hit per line is enough to flag it
    return out


def _dated_pointer(lines: list[str]) -> list[dict]:
    out = []
    for i, ln in enumerate(lines):
        if _DATED_POINTER_RE.match(ln):
            body = ln.split(":", 1)[1].strip() if ":" in ln else ""
            truncated = len(body) < 40 or body.endswith(("the", "a", "of", "→", "the SKILL", "md"))
            out.append({"line": i + 1, "kind": "dated-pointer",
                        "reason": "dated changelog pointer — the DailyActivity file IS the record"
                                  + (" (looks truncated mid-sentence)" if truncated else ""),
                        "excerpt": ln.strip()[:120]})
    return out


_DETECTORS = {
    "echoed-title": _echoed_title,
    "drift-number": _drift_number,
    "dated-pointer": _dated_pointer,
}


def scan_file(path: Path, detectors: list[str]) -> list[dict]:
    # READ-ONLY: open in text read mode only. No write path exists anywhere.
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")
    hits: list[dict] = []
    for d in detectors:
        hits.extend(_DETECTORS[d](lines))
    return sorted(hits, key=lambda h: h["line"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only context-file hygiene scanner (surfaces candidates; never fixes).")
    default_root = Path.home() / ".swarm-ai" / "SwarmWS" / ".context"
    ap.add_argument("--root", default=str(default_root), help="dir holding the .md context files")
    ap.add_argument("--file", default=None, help="restrict to one file (e.g. MEMORY.md)")
    ap.add_argument("--detector", default="echoed-title,drift-number,dated-pointer",
                    help="comma list of detectors to run")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--routing", action="store_true", help="print live source-routing table and exit (no scan)")
    args = ap.parse_args(argv)

    routing_rows, routing_src = _load_routing()

    if args.routing:
        rows = [r for r in routing_rows if not args.file or r["file"] == args.file]
        if args.json:
            print(json.dumps({"source": routing_src, "routing": rows}, indent=2))
        else:
            print("Source-routing (%s):\n" % routing_src)
            for r in rows:
                print("  %-14s [%s]" % (r["file"], r["owner"]))
                print("      SoT   : %s" % r["source_of_truth"])
                print("      edit  : %s\n" % r["edit_effect"])
        return 0

    root = Path(args.root)
    if not root.is_dir():
        print("ERROR: --root is not a directory: %s" % root, file=sys.stderr)
        return 2
    detectors = [d.strip() for d in args.detector.split(",") if d.strip() in _DETECTORS]
    if not detectors:
        print("ERROR: no valid detectors in --detector", file=sys.stderr)
        return 2

    targets = ([root / args.file] if args.file else sorted(root.glob("*.md")))
    report: dict[str, list[dict]] = {}
    total = 0
    for p in targets:
        if not p.is_file():
            continue
        hits = scan_file(p, detectors)
        if hits:
            report[p.name] = hits
            total += len(hits)

    if args.json:
        print(json.dumps({"root": str(root), "detectors": detectors,
                          "total_candidates": total, "by_file": report,
                          "note": "candidates are READ-and-JUDGE leads, not verdicts; delete manually after judging"}, indent=2))
    else:
        print("Context-hygiene scan: %s  (%d candidates across %d files)\n" % (root, total, len(report)))
        print("⚠️  Candidates only — READ and JUDGE each before deleting. This tool never edits.\n")
        for fn, hits in report.items():
            print("── %s (%d) ──" % (fn, len(hits)))
            for h in hits:
                print("  L%-5d %-13s %s" % (h["line"], h["kind"], h["reason"]))
            print()
        if not report:
            print("  (no candidates — clean, or narrow the --detector/--file scope)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
