#!/usr/bin/env python3
"""One-time reconciliation: fix runs the OLD stale-detector mislabeled as failed.

BACKGROUND
----------
Before the forward fix (routers/pipelines.py now honors ``is_terminal_run``), the
dashboard's stale-detector flipped any ``status="running"`` run older than the
threshold to ``status="failed"`` on disk — even runs that had finished every stage
but crashed before ``run-update --status completed`` ran. Those runs genuinely
delivered AND reflected; ``failed`` is a permanent lie, and analytics raw-status
paths undercount them as failures.

This script rewrites ONLY those mislabeled runs back to ``completed``. It is the
opt-in, auditable, reversible counterpart to the forward fix (which only prevents
NEW mislabels — per its AC4 it never rewrites history; this script is that history
rewrite, gated and explicit).

SELECTION — the triple gate (ALL three, AND-ed)
-----------------------------------------------
1. ``status == "failed"``
2. ``failure_reason == _STALE_FAILURE_REASON`` (imported from the sole writer —
   byte-parity guaranteed, never hardcoded here)
3. a ``reflect`` stage exists with ``status == "completed"`` — the honest
   end-marker (reflect is the LAST stage in every profile). A run that only
   reached ``deliver`` (no reflect) is PARTIAL and is deliberately NOT rewritten:
   for a permanent disk write, "delivered but not reflected" is not "finished".

SAFETY
------
- **Dry-run is the default.** Nothing is written without ``--apply``.
- **Backup**: each rewritten ``run.json`` is copied to ``run.json.bak`` first.
- **Atomic write**: tmp-file + ``os.replace`` (mirrors _mark_failed).
- **Idempotent**: a rewritten run is ``completed`` and no longer matches gate 1,
  so a re-run is a no-op.
- **Audit**: an ``reconciled_from: "failed"`` marker is added to each rewritten
  run, and a manifest (id / project / before→after) is printed.

USAGE
-----
    python backend/scripts/reconcile_mislabeled_runs.py            # dry-run (lists matches)
    python backend/scripts/reconcile_mislabeled_runs.py --apply    # rewrite on disk
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Sole source of the signature string — import, never copy (skeptic: byte drift).
from routers.pipelines import _STALE_FAILURE_REASON

__all__ = ["select_mislabeled", "reconcile", "_STALE_FAILURE_REASON"]


def _default_workspace() -> Path:
    """Workspace root, matching artifact_cli._get_workspace resolution."""
    from config import get_app_data_dir

    ws = os.environ.get("SWARM_WORKSPACE", str(get_app_data_dir() / "SwarmWS"))
    return Path(ws).expanduser().resolve()


def _has_completed_reflect(state: dict) -> bool:
    """True iff a ``reflect`` stage is present and completed — the honest
    end-of-run marker (reflect is the last stage in every profile). Hardened
    against a malformed stages list (non-list / non-dict entries)."""
    stages = state.get("stages")
    if not isinstance(stages, list):
        return False
    return any(
        isinstance(s, dict)
        and s.get("stage") == "reflect"
        and s.get("status") == "completed"
        for s in stages
    )


def _is_mislabeled(state: dict) -> bool:
    """The triple gate. All three must hold."""
    return (
        isinstance(state, dict)
        and state.get("status") == "failed"
        and state.get("failure_reason") == _STALE_FAILURE_REASON
        and _has_completed_reflect(state)
    )


def _iter_run_files(workspace_root: Path):
    """Yield (project_name, run_file Path, parsed state dict) for every run.json
    under ``<workspace_root>/Projects/*/.artifacts/runs/*/run.json``."""
    projects = workspace_root / "Projects"
    if not projects.exists():
        return
    for project_dir in sorted(projects.iterdir()):
        runs = project_dir / ".artifacts" / "runs"
        if not runs.is_dir():
            continue
        for run_dir in sorted(runs.iterdir()):
            rf = run_dir / "run.json"
            if not rf.is_file():
                continue
            try:
                state = json.loads(rf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue  # unreadable/corrupt: never touch
            if not isinstance(state, dict):
                continue
            yield project_dir.name, rf, state


def select_mislabeled(workspace_root: Path) -> list[dict]:
    """Return a manifest row per mislabeled run: {id, project, path}. Read-only."""
    out = []
    for project, rf, state in _iter_run_files(workspace_root):
        if _is_mislabeled(state):
            out.append({"id": state.get("id", rf.parent.name), "project": project, "path": str(rf)})
    return out


def _rewrite(rf: Path, state: dict) -> None:
    """Backup then atomically rewrite one run.json: failed -> completed + marker."""
    bak = rf.with_suffix(".json.bak")
    if not bak.exists():  # never clobber an existing backup
        bak.write_text(json.dumps(state, indent=2), encoding="utf-8")
    new = dict(state)
    new["status"] = "completed"
    new["reconciled_from"] = "failed"
    new.pop("failure_reason", None)  # the reason was the stale-detector's, no longer true
    tmp = rf.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new, indent=2), encoding="utf-8")
    os.replace(tmp, rf)


def reconcile(workspace_root: Path, apply: bool = False) -> int:
    """Reconcile mislabeled runs. Returns the count selected (dry-run) or rewritten.

    apply=False (default): report only, write nothing.
    apply=True: backup + atomic-rewrite each match. Idempotent (completed self-excludes).
    """
    matches = select_mislabeled(workspace_root)
    for m in matches:
        rf = Path(m["path"])
        try:
            state = json.loads(rf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        verb = "REWRITE" if apply else "would rewrite"
        print(f"  {verb}: {m['project']}/{m['id']}  failed -> completed")
        if apply:
            _rewrite(rf, state)
    return len(matches)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="Actually rewrite on disk. Default is dry-run (report only).")
    ap.add_argument("--workspace", default=None,
                    help="Workspace root (defaults to $SWARM_WORKSPACE or the app data dir).")
    args = ap.parse_args(argv)

    root = Path(args.workspace).expanduser().resolve() if args.workspace else _default_workspace()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[reconcile-mislabeled-runs] {mode} — workspace={root}")
    n = reconcile(root, apply=args.apply)
    if n == 0:
        print("  no mislabeled runs found (nothing to do).")
    elif not args.apply:
        print(f"  {n} run(s) would be rewritten. Re-run with --apply to write.")
    else:
        print(f"  {n} run(s) rewritten (backups: run.json.bak alongside each).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
