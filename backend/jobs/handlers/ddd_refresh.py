"""Scheduled S4 scan for architecture drift in project-bound repositories.

The job reads complete recent commit records and hands them to ``s4_code``. It no
longer writes DDD proposal files or edits governed content.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from core.ddd_bindings import governed_repo_roots
from core.ingestion.sources.s4_code import extract_commits
from jobs.paths import PROJECTS_DIR, SWARMWS

_GIT_TIMEOUT = 30
_GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
# A repository never scanned before: the job runs weekly, so a week and a day.
FIRST_SCAN = "8 days ago"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          timeout=_GIT_TIMEOUT, env=_GIT_ENV)


def _recent_commits(repo: Path, since: str = FIRST_SCAN, *,
                    after: str | None = None) -> list[dict[str, Any]]:
    """Complete patches of the commits after *after* (a commit this scan already read),
    else since *since*; the rehearsal replay passes its own *since*."""
    listed = _git(repo, "log", f"{after}..HEAD" if after else f"--since={since}", "--format=%H")
    if listed.returncode != 0:
        return []
    rows = []
    for sha in (line.strip() for line in listed.stdout.splitlines() if line.strip()):
        shown = _git(repo, "show", "--format=fuller", "--no-ext-diff", sha)
        if shown.returncode == 0:
            rows.append({"sha": sha, "repository": str(repo), "record": shown.stdout})
    return rows


def _state_path() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "state" / "s4_scanned.json"


def _scanned() -> dict[str, Any]:
    """{repository: {"head": the last commit read, "at": when}} of earlier scans."""
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _mark_scanned(repo: Path, head: str, at: str) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _scanned()
    state[str(repo)] = {"head": head, "at": at}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _new_commits(repo: Path, seen: dict[str, Any] | None) -> tuple[list[dict[str, Any]], str | None]:
    """(the commits since the last scan of *repo*, its HEAD now). The last scan's head
    bounds it exactly while it is still an ancestor of HEAD; after a rewrite of history
    the time of the last scan does; a repository never scanned starts at FIRST_SCAN.
    (The job runs weekly: a fixed "24 hours" read one day in seven.)"""
    head = _git(repo, "rev-parse", "HEAD")
    if head.returncode != 0:
        return [], None
    head_sha = head.stdout.strip()
    last = (seen or {}).get("head")
    if last and _git(repo, "merge-base", "--is-ancestor", str(last), "HEAD").returncode == 0:
        return _recent_commits(repo, after=str(last)), head_sha
    return _recent_commits(repo, since=str((seen or {}).get("at") or FIRST_SCAN)), head_sha


def run_ddd_refresh(dry_run: bool = False) -> dict[str, Any]:
    """Run S4 for every bound repo over the commits since its last scan; a repo's scan
    is marked done only after its commits were handed to S4. Dry-run reports counts and
    enqueues nothing."""
    from datetime import datetime, timezone
    if not PROJECTS_DIR.is_dir():
        return {"status": "skipped", "reason": "no Projects/ directory"}
    checked = commits = queued = 0
    scanned = _scanned()
    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        for repo in governed_repo_roots(project_dir):
            checked += 1
            started = datetime.now(timezone.utc).isoformat()
            rows, head = _new_commits(repo, scanned.get(str(repo)))
            commits += len(rows)
            if dry_run:
                continue
            queued += len(extract_commits(
                rows, workspace=SWARMWS, project_hint=project_dir.name,
                project_snapshot={"project": project_dir.name, "repo": str(repo)}))
            if head:
                _mark_scanned(repo, head, started)
    return {"status": "success", "repositories_checked": checked,
            "commits_seen": commits, "units_queued": queued,
            "dry_run": dry_run}
