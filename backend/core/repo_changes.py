"""repo_changes — which repos are bound, and which roots a diff may be read from.

The bound code repos come from Projects/*/bindings.yaml only (never run.json). One
object store is one repo (``repo_identities``); ``authorized_root`` admits a bound
repo or the main checkout of a bound repo's store, and is what the Canvas Changes
tree diffs (core/session_changes.tree_diff) and the terminal's working repo check.

Read-only (``GIT_OPTIONAL_LOCKS=0``), bounded by GIT_TIMEOUT_S. Blocking: run it in
an executor pool.

Public: ``allowed_roots``, ``repo_identities``, ``authorized_root``, ``RepoChangesError``.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading

logger = logging.getLogger(__name__)

GIT_TIMEOUT_S = 10.0


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


def bound_projects() -> dict[str, str]:
    """{realpath root: project} of every bound code repo with a checkout on disk."""
    return {root: project for root, (_name, project) in _bindings().items()}


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


def _git_entry_id(root: str) -> tuple[int, int] | None:
    """(device, inode) of *root*/.git (a directory or a worktree's file); None if absent."""
    try:
        st = os.stat(os.path.join(root, ".git"))
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def repo_identities() -> list[dict]:
    """One entry per object store among the bound repos: ``{common, store, root, name,
    roots}`` — ``store`` the common dir plus its inode (a cache key that changes when
    the store is replaced), ``root`` the store's main checkout (else its first bound
    path), ``roots`` every bound path of it plus that main checkout. Blocking."""
    global _IDENT_CACHE
    bound = allowed_roots()
    # the .git entry's identity is in the key: a repo replaced at the same path
    # (re-cloned, relinked) is looked up again
    key = tuple(sorted((root, name, _git_entry_id(root)) for root, name in bound.items()))
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
        try:
            st = os.stat(common)
            store = f"{common}|{st.st_dev}:{st.st_ino}"
        except OSError:
            store = common
        ident = by_common.setdefault(common, {"common": common, "store": store,
                                              "root": main if os.path.isdir(main) else root,
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

