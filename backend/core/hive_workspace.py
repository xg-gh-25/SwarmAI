"""A dispatched task's workspace on a Hive, prepared by the Hive's backend before the
session starts (design docs/design/2026-10-30-run-spec.md §4.3, Hive side).

The Host writes a work order (core/task_spec): which repositories, at which commit,
read-only or writable. Before the session starts the Host calls ``POST /api/hive/work``
(routers/hive_brains); this module makes the order real, or says why it cannot, and
nothing starts then:

    Hive/repos/<host>/<path>/      the shared clone: only cloned and fetched into (also
                                   the Host's bundle, core/hive_repo_inbox); nobody works here
    Hive/work/<work_id>/order.json the workspace as prepared (``load``)
    Hive/work/<work_id>/repos/<name>/  this task's worktree of each repository
    Outputs/<work_id>/             deliverables that do not go into a repository

A ``read`` repository is a detached worktree at the commit: it is never bundled, so a
commit made in it never comes back. A ``write`` one is on the branch ``hive/<work_id>``
made at the commit; its commits come back as ``commit..hive/<work_id>`` (core/hive_bundle).
The agent runs no clone, fetch or checkout.

**The daemon's git runs nothing the agent configured.** The agent works in the worktrees,
and a worktree writes the shared clone's ``.git/config``; the agent can also write
``~/.gitconfig``. A filter driver, hook, fsmonitor or ``include.path`` put there would
otherwise run here, in the daemon, outside the agent's gates. So every git command here
runs with the global and system config off and hooks off, and before each use the
shared clone's ``.git/config`` is rewritten by this module from the URL alone (an
allowlist: remote, fetch refspec, object format), and ``info/attributes`` is removed.
A committed ``.gitattributes`` naming a filter finds no filter defined and checks out
plain bytes. The shared clone's path must be a plain directory (no link in it) with a
real ``.git`` directory.

One file lock per shared clone serializes clone, fetch, config and ``worktree add``;
the worktrees themselves are independent. Every failure is one of the fixed ``ERR_*``
texts plus the repository's name; a half-made workspace is removed (only
``Hive/work/<work_id>/`` and ``Outputs/<work_id>/``, and a branch made for it).

- ``prepare(workspace, work_id, repos)`` — the order; raises WorkError. Blocking (git).
- ``bind(workspace, work_id, session_id)`` — record the session that runs the order.
- ``load(workspace, work_id)``           — the order, or None.
- ``latest_worktree(workspace, url)``    — the newest order's worktree of a repository.
- ``release(workspace, work_id, busy)``  — remove the workspace; WorkError while ``busy``.
- ``sweep(workspace, busy)``             — release orders untouched for RETENTION_DAYS.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

WORK_ROOT = ("Hive", "work")
OUTPUTS_ROOT = "Outputs"
WORK_ID_RE = re.compile(r"so_[0-9a-f]{12}")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
ACCESS = ("read", "write")
ORDER_FILE = "order.json"
BRANCH_PREFIX = "hive/"
#: an order no one released is removed this long after it last changed
RETENTION_DAYS = 30
#: hang guards: a clone or fetch over the network, and a local git command
NET_TIMEOUT_S = 900
LOCAL_TIMEOUT_S = 60

ERR_BAD_ORDER = "the work order is not valid"
ERR_EXISTS = "this work order was prepared already"
ERR_PATH = "the shared clone's path is not a plain directory"
ERR_CLONE = "the repository could not be cloned"
ERR_FETCH = "the repository could not be fetched"
ERR_BUNDLE = "the Host's bundle could not be fetched"
ERR_NO_COMMIT = "the commit is not in the repository"
ERR_NO_REF = "the branch is not among the repository's remote branches"
ERR_WORKTREE = "the worktree could not be made"
ERR_BUSY = "the session that runs this work order is still running"
ERR_WRITE = "the workspace could not be written"
ERRORS = (ERR_BAD_ORDER, ERR_EXISTS, ERR_PATH, ERR_CLONE, ERR_FETCH, ERR_BUNDLE, ERR_NO_COMMIT,
          ERR_NO_REF, ERR_WORKTREE, ERR_BUSY, ERR_WRITE)

_SHA_RE = re.compile(r"[0-9a-f]{40,64}")
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1", "LC_ALL": "C"}
#: the global config the daemon's git reads: none (module doc); a test points it at its own
GLOBAL_CONFIG = os.devnull
_GIT_OPTS = ("-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
             "-c", "protocol.ext.allow=never", "-c", "submodule.recurse=false")


class WorkError(ValueError):
    """The order is not prepared (or not released); the message is safe to return."""


def work_dir(workspace: Path, work_id: str) -> Path:
    if not WORK_ID_RE.fullmatch(work_id or ""):
        raise WorkError(ERR_BAD_ORDER)
    return Path(workspace).joinpath(*WORK_ROOT, work_id)


def repo_rel(work_id: str, name: str) -> str:
    """``Hive/work/<work_id>/repos/<name>``: where a repository of the order is worked on."""
    return "/".join((*WORK_ROOT, work_id, "repos", name))


def outputs_rel(work_id: str) -> str:
    return f"{OUTPUTS_ROOT}/{work_id}"


def branch(work_id: str) -> str:
    return BRANCH_PREFIX + work_id


def _git(cwd: Optional[Path], *args: str, timeout: int = LOCAL_TIMEOUT_S) -> Optional[subprocess.CompletedProcess]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(_GIT_ENV, GIT_CONFIG_GLOBAL=GLOBAL_CONFIG)
    try:
        return subprocess.run(["git", *_GIT_OPTS, *args], cwd=str(cwd) if cwd else None, env=env,
                              capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def _ok(p: Optional[subprocess.CompletedProcess]) -> bool:
    return p is not None and p.returncode == 0


def _plain(ws: Path, rel: str) -> bool:
    """No component of ``ws/rel`` that exists is a link."""
    p = ws
    for part in rel.split("/"):
        p = p / part
        if p.is_symlink():
            return False
    return True


def _clean_config(clone: Path, url: str) -> None:
    """The shared clone's ``.git/config`` from the URL alone (module doc), keeping its
    object format; ``info/attributes`` removed."""
    git_dir = clone / ".git"
    cfg = git_dir / "config"
    fmt = "sha1"
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace") if cfg.is_file() and not cfg.is_symlink() else ""
    except OSError:
        text = ""
    if re.search(r"(?im)^\s*objectformat\s*=\s*sha256\s*$", text):
        fmt = "sha256"
    lines = ["[core]", f"\trepositoryformatversion = {1 if fmt == 'sha256' else 0}",
             "\tfilemode = true", "\tbare = false", "\tlogallrefupdates = true"]
    if fmt == "sha256":
        lines += ["[extensions]", "\tobjectformat = sha256"]
    lines += ['[remote "origin"]', f"\turl = {url}", "\tfetch = +refs/heads/*:refs/remotes/origin/*"]
    tmp = git_dir / ".config.swarm.tmp"
    if cfg.is_symlink():
        cfg.unlink()
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, cfg)
    attrs = git_dir / "info" / "attributes"
    if attrs.is_symlink() or attrs.exists():
        attrs.unlink()


class _CloneLock:
    """One exclusive lock per shared clone (its directory's key), across threads too."""

    def __init__(self, ws: Path, rel: str) -> None:
        import hashlib
        d = ws.joinpath(*WORK_ROOT, ".locks")
        self.path = d / (hashlib.sha256(rel.encode()).hexdigest()[:16] + ".lock")

    def __enter__(self):
        from utils.file_lock import flock_exclusive
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "a+")  # noqa: SIM115
        flock_exclusive(self.f)
        return self

    def __exit__(self, *exc):
        from utils.file_lock import flock_unlock
        try:
            flock_unlock(self.f)
        finally:
            self.f.close()


def _ensure_clone(ws: Path, r: dict[str, Any]) -> Path:
    """The shared clone of *r*, cloned or fetched, with the Host's bundle fetched; the
    commit the worktree starts from is set on ``r["sha"]``. Call under its lock."""
    from core import task_repos
    from core.hive_repo_inbox import bundle_rel
    rel = task_repos.repo_dir(r["url"])
    if not _plain(ws, rel):
        raise WorkError(ERR_PATH)
    clone = ws / rel
    if clone.exists() and not (clone.is_dir() and (clone / ".git").is_dir() and not (clone / ".git").is_symlink()):
        raise WorkError(ERR_PATH)
    bundle = ws / bundle_rel(r["sha"]) if r.get("sha") else None
    has_bundle = bundle is not None and bundle.is_file() and not bundle.is_symlink()
    if not clone.exists():
        clone.parent.mkdir(parents=True, exist_ok=True)
        tmp = clone.parent / f".{clone.name}.cloning"
        shutil.rmtree(tmp, ignore_errors=True)
        cloned = _ok(_git(None, "clone", "--no-checkout", "--no-tags", "--", r["url"], str(tmp),
                          timeout=NET_TIMEOUT_S))
        if not cloned and has_bundle:
            # the remote is out of reach: a whole-branch bundle (the host sends one when
            # its checkout tracks no remote branch) is a repository of its own
            shutil.rmtree(tmp, ignore_errors=True)
            cloned = _ok(_git(None, "clone", "--no-checkout", "--no-tags", "--", str(bundle), str(tmp),
                              timeout=NET_TIMEOUT_S))
        if not cloned:
            shutil.rmtree(tmp, ignore_errors=True)
            raise WorkError(ERR_CLONE)
        os.replace(tmp, clone)
    _clean_config(clone, r["url"])
    fetched = _ok(_git(clone, "fetch", "--prune", "--no-tags", "origin", timeout=NET_TIMEOUT_S))
    if r.get("sha"):
        if has_bundle and not _ok(_git(clone, "fetch", "--no-tags", str(bundle),
                                       f"+HEAD:refs/swarm/incoming/{r['sha']}", timeout=NET_TIMEOUT_S)):
            raise WorkError(ERR_BUNDLE)
        if not _ok(_git(clone, "cat-file", "-e", f"{r['sha']}^{{commit}}")):
            # the commit is what matters; an unreachable remote is the reason when it is missing
            raise WorkError(ERR_NO_COMMIT if fetched else ERR_FETCH)
    elif not fetched:
        raise WorkError(ERR_FETCH)
    if not r.get("sha"):
        ref = f"refs/remotes/origin/{r['ref']}" if r.get("ref") else "refs/remotes/origin/HEAD"
        if not r.get("ref"):
            _git(clone, "remote", "set-head", "origin", "--auto", timeout=NET_TIMEOUT_S)
        p = _git(clone, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
        sha = p.stdout.decode().strip() if _ok(p) else ""
        if not _SHA_RE.fullmatch(sha):
            raise WorkError(ERR_NO_REF)
        r["sha"] = sha
    return clone


def _parse(work_id: str, repos: Any) -> list[dict[str, Any]]:
    from core import task_repos
    if not WORK_ID_RE.fullmatch(work_id or "") or not isinstance(repos, list):
        raise WorkError(ERR_BAD_ORDER)
    try:
        declared = task_repos.parse([{k: v for k, v in r.items() if k in ("url", "ref", "sha")}
                                     for r in repos if isinstance(r, dict)])
    except task_repos.TaskRepoError:
        raise WorkError(ERR_BAD_ORDER) from None
    if len(declared) != len(repos):
        raise WorkError(ERR_BAD_ORDER)               # a malformed or repeated entry
    out, names = [], set()
    for r, d in zip(repos, declared):
        name, access = r.get("name"), r.get("access")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name in names or access not in ACCESS:
            raise WorkError(ERR_BAD_ORDER)
        names.add(name)
        out.append({"name": name, "url": d["url"], "ref": d["ref"], "sha": d.get("sha"), "access": access})
    return out


def _remove(ws: Path, work_id: str, made_branches: list[tuple[Path, str]]) -> None:
    """Remove this order's directories, then let each clone forget their worktrees and
    delete the branches made for it (``b`` non-empty); nothing else."""
    for rel in ("/".join((*WORK_ROOT, work_id)), outputs_rel(work_id)):
        p = ws / rel
        if p.is_symlink():
            p.unlink()
        elif p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for clone, b in made_branches:
        _git(clone, "worktree", "prune")
        if b:
            _git(clone, "branch", "-D", "--", b)


def _write_order(d: Path, order: dict[str, Any]) -> None:
    tmp = d / f".{ORDER_FILE}.tmp"
    tmp.write_text(json.dumps(order, indent=1), encoding="utf-8")
    os.replace(tmp, d / ORDER_FILE)


def prepare(workspace: Path, work_id: str, repos: Any) -> dict[str, Any]:
    """Make the work order real (module doc); the order as written to ``order.json``."""
    ws = Path(workspace)
    items = _parse(work_id, repos)
    d = work_dir(ws, work_id)
    if not (_plain(ws, "/".join((*WORK_ROOT, work_id))) and _plain(ws, outputs_rel(work_id))):
        raise WorkError(ERR_PATH)
    if d.exists() or (ws / outputs_rel(work_id)).exists():
        raise WorkError(ERR_EXISTS)
    made: list[tuple[Path, str]] = []
    try:
        (d / "repos").mkdir(parents=True)
        (ws / outputs_rel(work_id)).mkdir(parents=True)
        from core import task_repos
        listed = []
        for r in items:
            with _CloneLock(ws, task_repos.repo_dir(r["url"])):
                try:
                    clone = _ensure_clone(ws, r)
                except WorkError as e:
                    raise WorkError(f"{r['name']}: {e}") from None
                target = ws / repo_rel(work_id, r["name"])
                if r["access"] == "write":
                    p = _git(clone, "worktree", "add", "-b", branch(work_id), str(target), r["sha"])
                    if _ok(p):
                        made.append((clone, branch(work_id)))
                else:
                    p = _git(clone, "worktree", "add", "--detach", str(target), r["sha"])
                if not _ok(p):
                    made.append((clone, ""))                  # prune what a failed add left
                    raise WorkError(f"{r['name']}: {ERR_WORKTREE}")
            listed.append({**r, "dir": repo_rel(work_id, r["name"]),
                           "branch": branch(work_id) if r["access"] == "write" else None})
        order = {"work_id": work_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "session_id": None, "repos": listed, "outputs": outputs_rel(work_id)}
        _write_order(d, order)
        return order
    except WorkError:
        _remove(ws, work_id, made)
        raise
    except OSError as exc:
        logger.warning("hive_workspace: preparing %s failed (%s)", work_id, type(exc).__name__)
        _remove(ws, work_id, made)
        raise WorkError(ERR_WRITE) from None


def load(workspace: Path, work_id: str) -> Optional[dict[str, Any]]:
    """The prepared order, or None when there is none (or it does not read)."""
    try:
        d = work_dir(Path(workspace), work_id)
    except WorkError:
        return None
    f = d / ORDER_FILE
    if not _plain(Path(workspace), "/".join((*WORK_ROOT, work_id, ORDER_FILE))) or not f.is_file():
        return None
    try:
        order = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return order if isinstance(order, dict) and order.get("work_id") == work_id else None


def bind(workspace: Path, work_id: str, session_id: str) -> bool:
    """Record the session that runs the order (read by ``release``); False when there is
    no order or it names another session already."""
    order = load(workspace, work_id)
    if order is None or order.get("session_id") not in (None, session_id):
        return False
    order["session_id"] = session_id
    try:
        _write_order(work_dir(Path(workspace), work_id), order)
    except OSError:
        return False
    return True


def task_repos_of(order: dict[str, Any]) -> list[dict[str, Any]]:
    """The order's repositories as ``sessions.metadata.task_repos`` entries
    (core/task_repos.parse reads ``dir`` and ``access``)."""
    return [{"url": r["url"], "ref": r["ref"], "sha": r["sha"], "name": r["name"],
             "access": r["access"], "dir": r["dir"]} for r in order.get("repos") or []]


def latest_worktree(workspace: Path, url: str) -> Optional[str]:
    """The workspace-relative worktree of *url* in the newest prepared order that has
    one (the code a Hive's review reads, core/code_intel/local_roots), or None."""
    from core import task_repos
    ws = Path(workspace)
    root = ws.joinpath(*WORK_ROOT)
    if not root.is_dir() or not _plain(ws, "/".join(WORK_ROOT)):
        return None
    best: tuple[str, str] = ("", "")
    for d in root.iterdir():
        if d.is_symlink() or not WORK_ID_RE.fullmatch(d.name):
            continue
        order = load(ws, d.name)
        for r in (order or {}).get("repos") or []:
            if (isinstance(r, dict) and isinstance(r.get("dir"), str) and task_repos._same_repo(str(r.get("url")), url)
                    and str(order.get("created_at")) > best[0] and (ws / r["dir"]).is_dir()):
                best = (str(order.get("created_at")), r["dir"])
    return best[1] or None


def release(workspace: Path, work_id: str, busy: Callable[[str], bool]) -> bool:
    """Remove the order's worktrees, ``Hive/work/<work_id>/`` and ``Outputs/<work_id>/``
    (the branch ``hive/<work_id>`` stays: its commits are compared and landed later).
    WorkError(ERR_BUSY) while ``busy(session_id)``; False when there was nothing."""
    ws = Path(workspace)
    d = work_dir(ws, work_id)
    order = load(ws, work_id)
    if order is None and not d.exists() and not (ws / outputs_rel(work_id)).exists():
        return False
    sid = (order or {}).get("session_id")
    if isinstance(sid, str) and busy(sid):
        raise WorkError(ERR_BUSY)
    from core import task_repos
    clones = []
    for r in (order or {}).get("repos") or []:
        try:
            clones.append(ws / task_repos.repo_dir(r["url"]))
        except (task_repos.TaskRepoError, KeyError, TypeError):
            continue
    # worktree directories are deleted as files (never through git, which would read the
    # agent's config there); each clone then forgets them with ``worktree prune``
    _remove(ws, work_id, [(c, "") for c in clones if c.is_dir() and _plain(ws, str(c.relative_to(ws)))])
    return True


def sweep(workspace: Path, busy: Callable[[str], bool], now: Optional[float] = None) -> int:
    """Release orders whose directory did not change for RETENTION_DAYS; the count."""
    ws = Path(workspace)
    root = ws.joinpath(*WORK_ROOT)
    now = time.time() if now is None else now
    if not root.is_dir() or not _plain(ws, "/".join(WORK_ROOT)):
        return 0
    n = 0
    for d in root.iterdir():
        if d.is_symlink() or not d.is_dir() or not WORK_ID_RE.fullmatch(d.name):
            continue
        try:
            if now - d.lstat().st_mtime <= RETENTION_DAYS * 86400:
                continue
            if release(ws, d.name, busy):
                n += 1
        except (WorkError, OSError):
            continue
    return n
