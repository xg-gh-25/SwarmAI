"""Scheduled S4 scan for architecture drift in project-bound repositories.

The job reads complete recent commit records and hands them to ``s4_code``. It no
longer writes DDD proposal files or edits governed content.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from core.ddd_bindings import governed_repo_roots
from core.ingestion.sources.s4_code import extract_commits
from jobs.paths import PROJECTS_DIR, SWARMWS

_GIT_TIMEOUT = 30
_GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


def _recent_commits(repo: Path, since: str = "24 hours ago") -> list[dict[str, Any]]:
    """Read complete patches for commits in the window (the scheduled lane's 24 hours;
    the rehearsal replay passes its own)."""
    listed = subprocess.run(
        ["git", "log", f"--since={since}", "--format=%H"], cwd=repo,
        capture_output=True, text=True, timeout=_GIT_TIMEOUT, env=_GIT_ENV)
    if listed.returncode != 0:
        return []
    rows = []
    for sha in (line.strip() for line in listed.stdout.splitlines() if line.strip()):
        shown = subprocess.run(
            ["git", "show", "--format=fuller", "--no-ext-diff", sha], cwd=repo,
            capture_output=True, text=True, timeout=_GIT_TIMEOUT, env=_GIT_ENV)
        if shown.returncode == 0:
            rows.append({"sha": sha, "repository": str(repo), "record": shown.stdout})
    return rows


def run_ddd_refresh(dry_run: bool = False) -> dict[str, Any]:
    """Run S4 for every bound repo; dry-run reports counts and enqueues nothing."""
    if not PROJECTS_DIR.is_dir():
        return {"status": "skipped", "reason": "no Projects/ directory"}
    checked = commits = queued = 0
    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        for repo in governed_repo_roots(project_dir):
            checked += 1
            rows = _recent_commits(repo)
            commits += len(rows)
            if not dry_run:
                queued += len(extract_commits(
                    rows, workspace=SWARMWS, project_hint=project_dir.name,
                    project_snapshot={"project": project_dir.name,
                                      "repo": str(repo)}))
    return {"status": "success", "repositories_checked": checked,
            "commits_seen": commits, "units_queued": queued,
            "dry_run": dry_run}
