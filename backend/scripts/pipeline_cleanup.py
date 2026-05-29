#!/usr/bin/env python3
"""One-time pipeline data cleanup: reclassify stale runs.

Fixes historical data:
- 14 zero-stage "failed" runs → "abandoned" (session crashed before execution)
- 6 orphan "running" runs → "abandoned" (session crashed mid-execution)

Run once:
    python backend/scripts/pipeline_cleanup.py [--dry-run]

Safe to re-run (idempotent — only touches status=failed with no stages,
or status=running older than 2h).
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


def _get_workspace() -> Path:
    """Resolve workspace root."""
    import os
    ws = os.environ.get("SWARM_WORKSPACE")
    if ws:
        return Path(ws).expanduser().resolve()
    # Default
    return Path.home() / ".swarm-ai" / "SwarmWS"


def reclassify_stale_runs(workspace: Path = None, dry_run: bool = False) -> dict:
    """Reclassify stale pipeline runs.

    - status='failed' with zero stages → 'abandoned' (never executed)
    - status='running' with updated_at > 2h → 'abandoned' (orphaned)

    Returns dict with counts.
    """
    ws = workspace or _get_workspace()
    projects_dir = ws / "Projects"
    if not projects_dir.exists():
        return {"reclassified_failed": 0, "reclassified_running": 0}

    threshold = datetime.now(timezone.utc) - timedelta(hours=2)
    reclassified_failed = 0
    reclassified_running = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        runs_dir = project_dir / ".artifacts" / "runs"
        if not runs_dir.exists():
            continue

        for rd in runs_dir.iterdir():
            if not rd.is_dir():
                continue
            run_file = rd / "run.json"
            if not run_file.exists():
                continue

            try:
                data = json.loads(run_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            status = data.get("status", "")
            stages = data.get("stages", [])
            changed = False

            # Case 1: "failed" with zero stages = session crash, never executed
            if status == "failed" and not stages:
                data["status"] = "abandoned"
                data["abandon_reason"] = "zero_stages_reclassified"
                data["abandoned_at"] = now_iso
                reclassified_failed += 1
                changed = True

            # Case 2: "running" and stale = orphaned
            elif status == "running":
                updated_str = data.get("updated_at", data.get("created_at", ""))
                if updated_str:
                    try:
                        updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
                        if updated_at.tzinfo is None:
                            updated_at = updated_at.replace(tzinfo=timezone.utc)
                        if updated_at < threshold:
                            data["status"] = "abandoned"
                            data["abandon_reason"] = "stale_orphan_reclassified"
                            data["abandoned_at"] = now_iso
                            reclassified_running += 1
                            changed = True
                    except (ValueError, TypeError):
                        pass

            if changed and not dry_run:
                run_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    return {
        "reclassified_failed": reclassified_failed,
        "reclassified_running": reclassified_running,
        "total": reclassified_failed + reclassified_running,
    }


def main():
    dry_run = "--dry-run" in sys.argv
    result = reclassify_stale_runs(dry_run=dry_run)
    prefix = "[DRY RUN] " if dry_run else ""
    print(f"{prefix}Reclassified {result['reclassified_failed']} zero-stage 'failed' → 'abandoned'")
    print(f"{prefix}Reclassified {result['reclassified_running']} stale 'running' → 'abandoned'")
    print(f"{prefix}Total: {result['total']} runs fixed")


if __name__ == "__main__":
    main()
