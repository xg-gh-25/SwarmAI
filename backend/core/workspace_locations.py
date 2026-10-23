"""Workspace overlay locations: SwarmWS plus the user's declared local repos.

Design: docs/design/workspace-working-location.md (v5). Each location is a
standalone workspace the user browses by hand. A location reflects the LOCAL MAIN
checkout only; nothing here reads pipeline runs, sessions or run worktrees.

- ``list_locations(swarmws_root)``: SwarmWS first, then every repo a DDD declares
  (``bindings.yaml`` ``worktree``, via ``repo_changes._bindings``) whose ``.git`` is a
  directory (a main checkout, not a linked worktree) and whose ``.git/logs/HEAD`` moved
  within ``WINDOW_DAYS``; newest activity first. Only stats, never runs git.
- ``resolve(key, swarmws_root)``: an opaque key back to ``(root, kind)``; LookupError
  for an unknown key, so the client can never name an arbitrary path.
- ``status_map(root)`` / ``summary(root)``: one ``git status`` per location, read-only
  (``GIT_OPTIONAL_LOCKS=0``), cached ``STATUS_TTL_S`` per root and single-flight per
  root, so the summary, tree and expand of one overlay open share one scan.
- The repo list is cached ``REPOS_TTL_S`` (it parses every ``bindings.yaml``), so a
  tree expand resolves its key without re-reading them.
- ``REPO_SKIP_DIRS``: directories a repo tree never lists.

Blocking (stat + subprocess): call from the ``nav`` pool.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
STATUS_TTL_S = 10.0
GIT_TIMEOUT_S = 5.0
SWARMWS_KEY = "swarmws"
REPO_SKIP_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "target", ".next", ".turbo",
})

REPOS_TTL_S = 30.0
_repos_cache: dict[str, tuple[float, list[dict]]] = {}
_status_cache: dict[str, tuple[float, dict[str, str]]] = {}
_status_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def repo_key(root: str) -> str:
    return "r_" + hashlib.sha1(os.path.realpath(root).encode(), usedforsecurity=False).hexdigest()[:12]


def _head_mtime(root: str) -> float | None:
    """mtime of ``.git/logs/HEAD`` for a main checkout; None for anything else."""
    git = os.path.join(root, ".git")
    if not os.path.isdir(git):
        return None
    try:
        return os.stat(os.path.join(git, "logs", "HEAD")).st_mtime
    except OSError:
        return None


def _repos(swarmws_root: str) -> list[dict]:
    """Active declared repos; cached REPOS_TTL_S (every bindings.yaml is parsed)."""
    hit = _repos_cache.get(swarmws_root)
    if hit and time.monotonic() - hit[0] < REPOS_TTL_S:
        return hit[1]
    rows = _scan_repos(swarmws_root)
    _repos_cache[swarmws_root] = (time.monotonic(), rows)
    return rows


def _scan_repos(swarmws_root: str) -> list[dict]:
    from core import repo_changes
    ws_real = os.path.realpath(swarmws_root)
    cutoff = time.time() - WINDOW_DAYS * 86400
    out = []
    for root, (name, _project) in repo_changes._bindings().items():
        if root == ws_real:
            continue
        mtime = _head_mtime(root)
        if mtime is None or mtime < cutoff:
            continue
        out.append({"key": repo_key(root), "label": name, "kind": "repo",
                    "root": root, "last_commit_at": mtime})
    out.sort(key=lambda r: -r["last_commit_at"])
    return out


def list_locations(swarmws_root: str) -> list[dict]:
    """[{key, label, kind, path}] — SwarmWS first, then active declared repos. ``path``
    is shown and copied; the client still names a location only by its key."""
    rows = [{"key": SWARMWS_KEY, "label": "SwarmWS", "kind": "default",
             "path": os.path.realpath(swarmws_root)}]
    rows += [{"key": r["key"], "label": r["label"], "kind": r["kind"], "path": r["root"]}
             for r in _repos(swarmws_root)]
    return rows


def resolve(key: str, swarmws_root: str) -> tuple[Path, str]:
    """(root, kind) for a key from ``list_locations``; LookupError otherwise."""
    if key == SWARMWS_KEY:
        return Path(swarmws_root), "default"
    for r in _repos(swarmws_root):
        if r["key"] == key:
            return Path(r["root"]), "repo"
    raise LookupError(key)


def _parse_porcelain_z(out: str) -> dict[str, str]:
    status: dict[str, str] = {}
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        if len(entry) < 4:
            i += 1
            continue
        xy, path = entry[:2], entry[3:]
        if "R" in xy and i + 1 < len(entries):
            i += 1
            path = entries[i]
        if "U" in xy or xy in ("AA", "DD"):
            st = "conflicting"
        elif "R" in xy:
            st = "renamed"
        elif xy == "??":
            st = "untracked"
        elif "D" in xy:
            st = "deleted"
        elif "A" in xy:
            st = "added"
        else:
            st = "modified"
        status[path.replace("\\", "/").rstrip("/")] = st
        i += 1
    return status


def _scan(root: str) -> dict[str, str]:
    if not os.path.exists(os.path.join(root, ".git")):
        return {}
    try:
        r = subprocess.run(["git", "status", "--porcelain", "-z", "-unormal"], cwd=root,
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("workspace_locations: git status failed in %s: %s", root, exc)
        return {}
    if r.returncode != 0:
        logger.warning("workspace_locations: git status in %s: %s", root, r.stderr.strip()[:200])
        return {}
    return _parse_porcelain_z(r.stdout)


def status_map(root: Path | str, *, fresh: bool = False) -> dict[str, str]:
    """{repo-relative path: status} for one location's local main; cached per root."""
    key = os.path.realpath(str(root))
    with _locks_guard:
        lock = _status_locks.setdefault(key, threading.Lock())
    with lock:                       # single-flight: parallel tree + summary share one scan
        hit = _status_cache.get(key)
        if hit and not fresh and time.monotonic() - hit[0] < STATUS_TTL_S:
            return hit[1]
        status = _scan(key)
        _status_cache[key] = (time.monotonic(), status)
        return status


def invalidate(root: Path | str | None = None) -> None:
    """Drop the cached scan of one root (after a write) or of all roots."""
    if root is None:
        _status_cache.clear()
        _repos_cache.clear()
    else:
        _status_cache.pop(os.path.realpath(str(root)), None)


_commit_cache: dict[str, tuple[float, float | None]] = {}


def last_commit_at(root: str) -> float | None:
    """Committer time of HEAD (epoch s). ``.git/logs/HEAD``'s mtime also moves on a
    checkout, reset or rebase, so it is only the cache key, never the answer."""
    key = os.path.realpath(root)
    mtime = _head_mtime(key)
    if mtime is None:
        return None
    hit = _commit_cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        r = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=key, capture_output=True,
                           text=True, timeout=GIT_TIMEOUT_S,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
        value = float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        value = None
    _commit_cache[key] = (mtime, value)
    return value


def summary(root: Path | str, kind: str, *, fresh: bool = False) -> dict:
    """{changed, last_commit_at?} for ONE location."""
    out: dict = {"changed": len(status_map(root, fresh=fresh))}
    if kind == "repo":
        out["last_commit_at"] = last_commit_at(str(root))
    return out


def changed_under(status: dict[str, str], rel_dir: str) -> int:
    """How many changed entries sit under *rel_dir* (a repo-relative directory)."""
    prefix = rel_dir.rstrip("/") + "/"
    return sum(1 for p in status if p.startswith(prefix))
