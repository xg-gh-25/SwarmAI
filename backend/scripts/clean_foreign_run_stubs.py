#!/usr/bin/env python3
"""Remove foreign auto-recorded stage stubs from pipeline run.json files.

Background (run_f3975b8b / run_3caef1d3): before guessing was eradicated,
`artifact_cli publish --stage` with no `--run-id` auto-recorded a stage stub into
the project-wide *newest active* run — which could be a SIBLING session's run.
This left stub records (`status="recorded"`, `auto_recorded=True`) in run.json
files that do NOT own the artifact: the SAME artifact_id is `completed`/`done` in
a DIFFERENT run (its real owner).

This script finds and strips ONLY those foreign stubs. It is the one-shot
remediation for contamination already on disk (DoD#4).

Foreign-stub signature (strict — run_id inequality, never artifact_id alone):
    a stage record S in run R where
      S.auto_recorded is True
      AND the SAME S.artifact_id appears with status in {completed, done}
          in some run R' where R' != R.
A stub whose artifact is completed in its OWN run R is NOT foreign (legit upgrade
in flight). A record without auto_recorded is never touched (it was explicitly
recorded by run-update).

Safety:
  - Idempotent: a second run finds nothing to strip (verified empirically).
  - Best-effort lock: each run.json is rewritten under an exclusive fcntl.flock on
    a sibling `.clean.lock` file, and re-reads the file under that lock (TOCTOU).
    ⚠️ This does NOT mutually exclude against the live writer: `_append_stage_to_run`
    in artifact_cli.py writes run.json with NO lock, and auto-resume uses a THIRD
    lock (`.resume.lock`) — three non-overlapping locks do not serialize. The
    re-read-under-lock + match-by-identity only guards against a concurrent COPY of
    THIS script. This is a one-shot remediation: RUN IT WHEN NO PIPELINE SESSIONS
    ARE ACTIVELY WRITING (it is safe to re-run, so prefer a quiet moment).
  - --dry-run prints the plan without writing.

Usage:
    python backend/scripts/clean_foreign_run_stubs.py [--dry-run] [--workspace PATH]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils.file_lock import flock_exclusive, flock_unlock


def _default_workspace() -> Path:
    env = os.environ.get("SWARMAI_WORKSPACE")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~/.swarm-ai/SwarmWS"))


def find_foreign_stubs(workspace: Path) -> tuple[dict[str, list[dict]], int]:
    """Scan all run.json under workspace/Projects/*/.artifacts/runs/*/.

    Returns:
        (per_run_foreign, total_runs) where per_run_foreign maps a run.json path
        to the list of foreign stub records (dicts) found in it.
    """
    runs_glob = str(workspace / "Projects" / "*" / ".artifacts" / "runs" / "*" / "run.json")

    # Pass 1: index artifact_id -> set of run_ids where it is completed/done,
    # and remember each run's (path, id, stages).
    completed_in: dict[str, set[str]] = defaultdict(set)
    run_records: list[tuple[str, str, list[dict]]] = []  # (path, run_id, stages)
    total = 0
    for rj in glob.glob(runs_glob):
        total += 1
        try:
            data = json.loads(Path(rj).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rid = data.get("id") or os.path.basename(os.path.dirname(rj))
        stages = data.get("stages", [])
        run_records.append((rj, rid, stages))
        for s in stages:
            aid = s.get("artifact_id")
            if aid and s.get("status") in ("completed", "done"):
                completed_in[aid].add(rid)

    # Pass 2: a record is foreign iff it is an auto_recorded stub whose artifact_id
    # is completed in a STRICTLY DIFFERENT run.
    per_run_foreign: dict[str, list[dict]] = {}
    for rj, rid, stages in run_records:
        foreign = []
        for s in stages:
            aid = s.get("artifact_id")
            if not aid or not s.get("auto_recorded"):
                continue
            owners = completed_in.get(aid, set())
            if any(owner != rid for owner in owners):
                foreign.append(s)
        if foreign:
            per_run_foreign[rj] = foreign
    return per_run_foreign, total


def _strip_under_lock(run_path: str, foreign: list[dict]) -> int:
    """Re-read run.json under an exclusive lock and remove the foreign stubs.

    Re-reads inside the lock (TOCTOU): a sibling session may have mutated the file
    since the scan. We match foreign records by (stage, artifact_id, auto_recorded)
    and recompute against the fresh content's own foreign signature is NOT needed —
    the scan already proved these are foreign; we only need to not clobber a
    concurrent legitimate edit, so we re-match by identity. Returns count removed.
    """
    # Build a match set keyed on the stable identity of each foreign stub.
    foreign_keys = {
        (s.get("stage"), s.get("artifact_id"), True)
        for s in foreign
    }
    lock_path = Path(run_path).with_suffix(".clean.lock")
    fd = None
    try:
        fd = open(lock_path, "w")
        flock_exclusive(fd)
        # Re-read under lock
        data = json.loads(Path(run_path).read_text(encoding="utf-8"))
        stages = data.get("stages", [])
        kept = []
        removed = 0
        for s in stages:
            key = (s.get("stage"), s.get("artifact_id"), bool(s.get("auto_recorded")))
            if s.get("auto_recorded") and key in foreign_keys:
                removed += 1
                continue
            kept.append(s)
        if removed:
            data["stages"] = kept
            Path(run_path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        return removed
    finally:
        if fd is not None:
            try:
                flock_unlock(fd)
                fd.close()
            except Exception:
                pass
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Strip foreign auto-recorded run stubs.")
    ap.add_argument("--dry-run", action="store_true", help="Report only; do not write.")
    ap.add_argument("--workspace", default=None, help="Workspace root (default ~/.swarm-ai/SwarmWS).")
    args = ap.parse_args(argv)

    workspace = Path(args.workspace) if args.workspace else _default_workspace()
    per_run_foreign, total = find_foreign_stubs(workspace)

    n_stubs = sum(len(v) for v in per_run_foreign.values())
    print(json.dumps({
        "workspace": str(workspace),
        "total_runs_scanned": total,
        "runs_with_foreign_stubs": len(per_run_foreign),
        "foreign_stubs_found": n_stubs,
        "dry_run": bool(args.dry_run),
    }, indent=2))

    for rj, foreign in sorted(per_run_foreign.items()):
        rid = os.path.basename(os.path.dirname(rj))
        ids = [f"{s.get('stage')}::{s.get('artifact_id')}" for s in foreign]
        print(f"  {rid}: {len(foreign)} foreign stub(s) → {ids}")

    if args.dry_run:
        print("DRY RUN — no files modified.")
        return 0

    total_removed = 0
    for rj, foreign in per_run_foreign.items():
        total_removed += _strip_under_lock(rj, foreign)
    print(json.dumps({"stubs_removed": total_removed}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
