"""repo_changes — the Canvas "Changes" tab: each code repo's local state, read-only.

One row per bound code repo (Projects/*/bindings.yaml, the same set
turn_changes.code_roots starts from) that has something not yet on its upstream:
commits ahead, uncommitted files, or extra worktrees. Facts only, from git:

- ``repos()`` — [{name, project, root, branch, upstream, ahead, dirty[], worktrees[],
  commits[]}]. ``ahead`` is None when the branch has no upstream. ``commits`` are the
  newest MAX_COMMITS of ``@{u}..HEAD`` (all of HEAD's history when there is no
  upstream is NOT listed: without an upstream there is no "not yet pushed" set).
- ``commit_files(root, sha)`` — the files one commit changed, with +/- counts.
- ``commit_diff(root, sha, path)`` — one file's patch in that commit, size-capped.

Which session made a commit is NOT known here (commits from every session carry the
same Co-Authored-By trailer), so nothing claims "this session's commits".

Every call is read-only (``GIT_OPTIONAL_LOCKS=0``: a status never takes index.lock),
bounded by GIT_TIMEOUT_S, and accepts only a root from ``allowed_roots()``, a hex sha
and a repo-relative path after ``--``. Blocking: run it in an executor pool.

Public: ``repos``, ``commit_files``, ``commit_diff``, ``allowed_roots``,
``RepoChangesError``, ``MAX_COMMITS``, ``MAX_DIFF_CHARS``.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_COMMITS = 50
MAX_DIRTY = 50
MAX_DIFF_CHARS = 400_000
GIT_TIMEOUT_S = 10.0
_SHA = re.compile(r"[0-9a-f]{7,40}")


class RepoChangesError(Exception):
    """Bad input (unknown repo, malformed sha or path) or a failed git call."""


def _git(root: str, *args: str, ok: tuple[int, ...] = (0,)) -> str:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                           timeout=GIT_TIMEOUT_S, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RepoChangesError(f"git {args[0]} failed: {exc}") from exc
    if r.returncode not in ok:
        raise RepoChangesError(f"git {args[0]}: {(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def allowed_roots() -> dict[str, str]:
    """{realpath root: repo name} of every bound code repo with a checkout on disk."""
    return {root: name for root, (name, _project) in _bindings().items()}


def _bindings() -> dict[str, tuple[str, str]]:
    """{realpath root: (repo name, project)} from every Projects/<P>/bindings.yaml —
    the project is the one whose bindings name the repo, not a name match."""
    try:
        from core.ddd_bindings import load_bindings
        from core.project_registry import get_swarmws
        projects = os.path.join(str(get_swarmws()), "Projects")
        names = sorted(os.listdir(projects))
    except Exception as exc:  # noqa: BLE001 — no workspace = no rows
        logger.debug("repo_changes: bindings unavailable: %s", exc)
        return {}
    out: dict[str, tuple[str, str]] = {}
    for project in names:
        path = os.path.join(projects, project, "bindings.yaml")
        if not os.path.isfile(path):
            continue
        try:
            doc = load_bindings(path)
        except Exception:  # noqa: BLE001 — one bad project never hides the others
            continue
        for b in doc.bindings:
            wt = getattr(b, "worktree", None)
            if not wt:
                continue
            real = os.path.realpath(os.path.expanduser(wt))
            if os.path.isdir(real):
                out.setdefault(real, (b.repo, project))
    return out


def _checked_root(root: str) -> str:
    real = os.path.realpath(root or "")
    if real not in allowed_roots():
        raise RepoChangesError("unknown repo")
    return real


def _project_of(root: str) -> Optional[str]:
    hit = _bindings().get(root)
    return hit[1] if hit else None


def _dirty(root: str) -> list[dict[str, str]]:
    out = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
    rows = []
    for rec in out.split("\0"):
        if len(rec) < 4:
            continue
        x, y, path = rec[0], rec[1], rec[3:]
        status = "?" if x == "?" else ("D" if "D" in (x, y) else "A" if "A" in (x, y) else "M")
        rows.append({"path": path, "status": status})
    return rows


def _worktrees(root: str) -> list[dict[str, Any]]:
    """Worktrees other than *root* itself: path, branch, prunable (dir gone)."""
    out = _git(root, "worktree", "list", "--porcelain")
    rows, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur and os.path.realpath(cur.get("path", "")) != root:
                rows.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur = {"path": val, "branch": None, "prunable": False, "detached": False}
        elif key == "branch":
            cur["branch"] = val.removeprefix("refs/heads/")
        elif key == "detached":
            cur["detached"] = True
        elif key == "prunable":
            cur["prunable"] = True
    # pipeline-test caches are runner scratch, not anyone's work in progress
    return [w for w in rows if "/cache/pipeline_test/" not in w["path"]]


def _commits(root: str, rng: str) -> list[dict[str, Any]]:
    out = _git(root, "log", rng, f"-n{MAX_COMMITS}", "--format=%x1e%H%x1f%h%x1f%s%x1f%cI",
               "--shortstat")
    rows = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip()
        if not chunk:
            continue
        head, _, stat = chunk.partition("\n")
        parts = head.split("\x1f")
        if len(parts) != 4:
            continue
        ins = re.search(r"(\d+) insertion", stat)
        dels = re.search(r"(\d+) deletion", stat)
        files = re.search(r"(\d+) files? changed", stat)
        rows.append({"sha": parts[0], "short": parts[1], "subject": parts[2], "at": parts[3],
                     "files": int(files.group(1)) if files else 0,
                     "insertions": int(ins.group(1)) if ins else 0,
                     "deletions": int(dels.group(1)) if dels else 0})
    return rows


def _repo(root: str, name: str) -> Optional[dict[str, Any]]:
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
                    ok=(0, 128)).strip() or None
    ahead = int(_git(root, "rev-list", "--count", "@{u}..HEAD").strip()) if upstream else None
    dirty = _dirty(root)
    worktrees = _worktrees(root)
    if not ahead and not dirty and not worktrees:
        return None  # nothing local: not a Changes row
    return {"name": name, "project": _project_of(root), "root": root, "branch": branch,
            "upstream": upstream, "ahead": ahead,
            "dirty_count": len(dirty), "dirty": dirty[:MAX_DIRTY], "worktrees": worktrees,
            "commits": _commits(root, "@{u}..HEAD") if ahead else []}


def repos() -> list[dict[str, Any]]:
    """Every bound repo with local state (module docstring); a repo whose git call
    fails is reported with ``error`` instead of being dropped."""
    rows = []
    for root, name in sorted(allowed_roots().items(), key=lambda kv: kv[1]):
        try:
            row = _repo(root, name)
        except RepoChangesError as exc:
            row = {"name": name, "project": _project_of(root), "root": root, "error": str(exc)}
        if row is not None:
            rows.append(row)
    return rows


def commit_files(root: str, sha: str) -> list[dict[str, Any]]:
    root = _checked_root(root)
    if not _SHA.fullmatch(sha or ""):
        raise RepoChangesError("bad sha")
    out = _git(root, "show", "--numstat", "--format=", "--no-renames", sha)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        a, d, path = parts
        rows.append({"path": path, "insertions": None if a == "-" else int(a),
                     "deletions": None if d == "-" else int(d)})
    return rows


def commit_diff(root: str, sha: str, path: str) -> dict[str, Any]:
    root = _checked_root(root)
    if not _SHA.fullmatch(sha or ""):
        raise RepoChangesError("bad sha")
    if not path or path.startswith(("/", "-")) or ".." in path.split("/") or "\0" in path:
        raise RepoChangesError("bad path")
    text = _git(root, "show", "--format=", "--no-renames", "--no-ext-diff", sha, "--", path)
    truncated = len(text) > MAX_DIFF_CHARS
    return {"diff": text[:MAX_DIFF_CHARS], "truncated": truncated}
