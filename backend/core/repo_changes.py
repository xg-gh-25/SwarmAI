"""repo_changes — one commit's files and patches, for the Canvas Changes tab.

The tab lists what a SESSION changed (core/canvas_session); a commit row there drills
down here: ``commit_files(root, sha)`` (the files it changed, +/-) and
``commit_diff(root, sha, path)`` (one file's patch, size-capped).

Read-only (``GIT_OPTIONAL_LOCKS=0``), bounded by GIT_TIMEOUT_S, and only for a root
that is a bound repo (``allowed_roots()``, Projects/*/bindings.yaml) or a code tree
turn_changes.allowed_repo accepts (a bound repo's main checkout, a run worktree), a
hex sha and a repo-relative path after ``--``. Blocking: run it in an executor pool.

Public: ``commit_files``, ``commit_diff``, ``allowed_roots``, ``RepoChangesError``,
``MAX_DIFF_CHARS``.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

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
    if real in allowed_roots():
        return real
    from core.turn_changes import allowed_repo
    if os.path.isdir(real) and allowed_repo(real):
        return real
    raise RepoChangesError("unknown repo")


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
