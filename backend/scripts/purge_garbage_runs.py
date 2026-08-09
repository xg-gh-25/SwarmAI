#!/usr/bin/env python3
"""One-time (and scheduled) purge of GARBAGE pipeline run directories.

BACKGROUND (run_0e68e235)
-------------------------
Garbage runs — ``status="abandoned"`` (crash_zombie / crash_residue_empty_shell /
superseded_by_* / orphaned_no_resume) OR crash-residue paused runs — that NEVER
delivered a result were polluting EVERY pipeline statistic: the "Needs you" count,
completion_rate denominator, token totals, trend, profile_mix, by-project rollups.
XG directive: garbage data must be cleaned AND kept out of all stats, on a schedule.

This script is the thin CLI wrapper over ``artifact_cli.purge_garbage_runs`` (the
SSOT — one definition, no drift). It is BOTH the manual cleanup CLI AND the command
run by the scheduled ``pipeline-retention`` system job (``jobs/system_jobs.py``,
weekdays, ``--apply``) — one entrypoint, so the schedule can never drift from the manual
run. The analytics endpoints already EXCLUDE garbage from stats at read time; this
script removes the on-disk clutter so it stops accumulating.

WHAT COUNTS AS GARBAGE (``artifact_cli._is_garbage_run`` — the single definition)
---------------------------------------------------------------------------------
  (status == 'abandoned'  OR  (status == 'paused' AND crash-auto-checkpoint reason))
  AND NOT delivered (no completed reflect/deliver stage)

NEVER purged: completed / cancelled (a real user decision) / failed (a real signal) /
running / genuine decision-pauses / delivered-but-mislabeled runs (abandoned AFTER
they reached a completed reflect/deliver — those are real completions). Nor FRESH
garbage newer than the retention window (a just-crashed run may still be diagnosable).

SAFETY
------
- **Dry-run is the DEFAULT.** Nothing is deleted without ``--apply``.
- **Recoverable delete.** Uses macOS ``trash`` (undoable) when available, falling
  back to ``rmtree`` only if the trash tool is absent (STEERING safety: trash > rm).
- **🔒 Git-tracked runs are SKIPPED by default** (Gate-2 CRITICAL run_0e68e235).
  NOT all run dirs are gitignored: SwarmAI's ``Projects/SwarmAI/.artifacts/runs/*``
  ARE git-tracked (origin = the PUBLIC repo — verified with ``git ls-files``, an
  earlier "gitignored" assumption was wrong). Deleting a tracked run would surface
  as an unattended ``git rm`` on the public working tree. So this only trashes
  UNTRACKED local garbage; touching tracked garbage is a deliberate, user-owned
  decision behind ``--include-tracked`` (and even then produces a working-tree
  deletion the USER must review + commit — never an unattended job).

USAGE
-----
    python backend/scripts/purge_garbage_runs.py                       # dry-run (lists count)
    python backend/scripts/purge_garbage_runs.py --apply               # trash UNTRACKED garbage >30d
    python backend/scripts/purge_garbage_runs.py --retention-days 7 --apply
    python backend/scripts/purge_garbage_runs.py --include-tracked --apply  # also git-rm tracked garbage (review + commit!)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from artifact_cli import purge_garbage_runs  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Purge garbage pipeline run directories (recoverable).")
    ap.add_argument("--retention-days", type=float, default=30.0,
                    help="Purge garbage older than this many days (default 30).")
    ap.add_argument("--apply", action="store_true",
                    help="Actually trash the dirs (default: dry-run, reports would_purge only).")
    ap.add_argument("--include-tracked", action="store_true",
                    help="Also purge git-TRACKED garbage (default: skip — a tracked delete "
                         "is an unattended git-rm on the public repo; review + commit yourself).")
    args = ap.parse_args()

    result = purge_garbage_runs(retention_days=args.retention_days, apply=args.apply,
                                include_tracked=args.include_tracked)
    print(json.dumps(result, indent=2))
    skipped = result.get("skipped_tracked", 0)
    if not args.apply:
        print(f"\nDRY-RUN: {result['would_purge']} UNTRACKED garbage run(s) older than "
              f"{args.retention_days:g}d would be trashed"
              + (f"; {skipped} tracked garbage run(s) SKIPPED (use --include-tracked to git-rm them)."
                 if skipped else ".")
              + " Re-run with --apply to purge.", file=sys.stderr)
    else:
        print(f"\nPurged {result['purged']} garbage run(s) "
              f"({result.get('errors', 0)} error(s); {skipped} tracked skipped).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
