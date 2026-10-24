"""repo_changes — one commit's patches, for the Canvas Changes tab.

The Canvas Changes tab (core/session_changes) lists the commits a session's messages
name; a file of a commit opens here: ``commit_diff(root, sha, path)`` (its patch,
size-capped).

Read-only (``GIT_OPTIONAL_LOCKS=0``), bounded by GIT_TIMEOUT_S, and only for a root
that is a bound repo or the main checkout of a bound repo's object store
(``authorized_root``: Projects/*/bindings.yaml only, never run.json), a hex sha and a
repo-relative path after ``--``. Blocking: run it in an executor pool.

Public: ``commit_diff``, ``allowed_roots``, ``repo_identities``, ``authorized_root``,
``RepoChangesError``, ``MAX_DIFF_CHARS``.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
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
        except Exception as exc:  # noqa: BLE001 — one bad project never hides the others
            logger.warning("repo_changes: %s unreadable, skipped: %s", path, exc)
            continue
        for b in doc.bindings:
            wt = getattr(b, "worktree", None)
            if not wt:
                continue
            real = os.path.realpath(os.path.expanduser(wt))
            if os.path.isdir(real):
                out.setdefault(real, (b.repo, project))
    return out


# Repo identity (design canvas-changes-from-turns.md D3): one object store = one
# repo. Two bindings that are worktrees of one store are one repo; a turn card's
# gitRoot (the store's main checkout, turn_changes._main_checkout) is authorized
# when its store is bound. git runs only in bound paths, never in a path a request
# names. Cached for the same set of bound paths.
_IDENT_CACHE: tuple[tuple, list[dict]] = ((), [])
_IDENT_LOCK = threading.Lock()


def repo_identities() -> list[dict]:
    """One entry per object store among the bound repos: ``{common, root, name,
    roots}`` — ``root`` the store's main checkout (else its first bound path),
    ``roots`` every bound path of it plus that main checkout. Blocking."""
    global _IDENT_CACHE
    bound = allowed_roots()
    key = tuple(sorted(bound.items()))
    with _IDENT_LOCK:
        if _IDENT_CACHE[0] == key and key:
            return _IDENT_CACHE[1]
    by_common: dict[str, dict] = {}
    for root, name in sorted(bound.items()):
        try:
            common = os.path.realpath(_git(root, "rev-parse", "--path-format=absolute",
                                           "--git-common-dir").strip())
        except RepoChangesError as exc:
            logger.info("repo_changes: %s has no git store: %s", root, exc)
            continue
        main = os.path.dirname(common) if os.path.basename(common) == ".git" else root
        ident = by_common.setdefault(common, {"common": common, "root": main if os.path.isdir(main) else root,
                                              "name": name, "roots": set()})
        ident["roots"].update({root, ident["root"]})
    out = list(by_common.values())
    with _IDENT_LOCK:
        _IDENT_CACHE = (key, out)
    return out


def authorized_root(root: str) -> str | None:
    """*root* as a real path when it is a bound repo or the main checkout of a bound
    repo's object store, else None. No git call on *root*; no run.json."""
    real = os.path.realpath(root or "")
    if not real:
        return None
    for ident in repo_identities():
        if real in ident["roots"]:
            return real
    return None


def _checked_root(root: str) -> str:
    real = authorized_root(root)
    if real is None:
        raise RepoChangesError("unknown repo")
    return real


def commit_diff(root: str, sha: str, path: str) -> dict[str, Any]:
    root = _checked_root(root)
    if not _SHA.fullmatch(sha or ""):
        raise RepoChangesError("bad sha")
    if not path or path.startswith(("/", "-")) or ".." in path.split("/") or "\0" in path:
        raise RepoChangesError("bad path")
    text = _git(root, "show", "--format=", "--no-renames", "--no-ext-diff", sha, "--", path)
    truncated = len(text) > MAX_DIFF_CHARS
    return {"diff": text[:MAX_DIFF_CHARS], "truncated": truncated}
