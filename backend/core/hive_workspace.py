"""A dispatched task's workspace on a Hive, prepared by the Hive's backend before the
session starts (design docs/design/2026-10-30-run-spec.md §4.3 and its r4.2 notes, Hive
side).

The Host writes a work order (core/task_spec): which repositories, at which commit,
read-only or writable. Before the session starts the Host calls ``POST /api/hive/work``
(routers/hive_brains); this module makes the order real, or says why it cannot, and
nothing starts then:

    <data>/hive/mirrors/<host>/<path>.git  a bare mirror per repository: the only place
                                           the daemon's git fetches into (also the
                                           Host's bundle, core/hive_repo_inbox)
    <data>/hive/orders/<work_id>.json      the order as prepared (``load``), and its
                                           ``.claim`` once a session takes it
    Hive/work/<work_id>/repos/<name>/      this task's own clone of each repository
    Outputs/<work_id>/                     deliverables that do not go into a repository

``<data>`` is the daemon's data directory, outside the workspace the agent works in.
A task's clone is made from the mirror (``--reference``: the objects are shared, its
``.git`` is its own) and checked out by the daemon before the session starts, so its
config is the one the daemon just wrote. A ``read`` repository's clone is detached at
the commit and is never bundled (core/hive_bundle), so a commit made in it never comes
back; a ``write`` one is on the branch ``hive/<work_id>`` made at the commit, and its
commits come back as ``commit..hive/<work_id>``. The agent runs no clone, fetch or
checkout.

**The daemon's git runs nothing the agent configured.** The agent works only in its
task's clone, never in a mirror, and the orders are not in the workspace. As defence in
depth every git command here runs with a minimal environment (no daemon secret reaches
a command git starts), the global and system config off, hooks off, and the config keys
that start a command fixed on the command line (credential helper, askpass, ssh
command, proxy, upload-pack, alternate refs, fsmonitor, the ``ext`` transport); a mirror
whose ``.git`` holds a ``commondir``, a ``gitdir`` or an ``alternates`` file, or a link,
is refused. One file lock per mirror serializes its fetches and the clones made from it.

Every failure is one of the fixed ``ERR_*`` texts, after the repository's name when it
is about one; a half-made workspace is removed (only ``Hive/work/<work_id>/``,
``Outputs/<work_id>/`` and the order's files).

- ``prepare(workspace, work_id, repos)`` — the order; raises WorkError. Blocking (git).
- ``claim(work_id)``                     — the one session start an order takes.
- ``bind(workspace, work_id, session_id)`` — record the session that runs the order.
- ``load(workspace, work_id)``           — the order, or None.
- ``latest_worktree(workspace, url)``    — the newest order's clone of a repository.
- ``release(workspace, work_id, busy)``  — remove the workspace; WorkError while ``busy``.
- ``sweep(workspace, busy)``             — release orders idle for RETENTION_DAYS.
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
BRANCH_PREFIX = "hive/"
#: an order no one released is removed once nothing in it changed for this long
RETENTION_DAYS = 30
#: hang guards: a clone or fetch over the network, and a local git command
NET_TIMEOUT_S = 900
LOCAL_TIMEOUT_S = 120

ERR_BAD_ORDER = "the work order is not valid"
ERR_EXISTS = "this work order was prepared already"
ERR_PATH = "the workspace path is not a plain directory"
ERR_MIRROR = "the repository's mirror is not one this Hive made"
ERR_CLONE = "the repository could not be cloned"
ERR_FETCH = "the repository could not be fetched"
ERR_BUNDLE = "the Host's bundle could not be fetched"
ERR_NO_COMMIT = "the commit is not in the repository"
ERR_NO_REF = "the branch is not among the repository's remote branches"
ERR_WORKTREE = "the task's copy of the repository could not be made"
ERR_BUSY = "the session that runs this work order is still running"
ERR_WRITE = "the workspace could not be written"
ERRORS = (ERR_BAD_ORDER, ERR_EXISTS, ERR_PATH, ERR_MIRROR, ERR_CLONE, ERR_FETCH, ERR_BUNDLE,
          ERR_NO_COMMIT, ERR_NO_REF, ERR_WORKTREE, ERR_BUSY, ERR_WRITE)

_SHA_RE = re.compile(r"[0-9a-f]{40,64}")
#: the only variables a git command here sees (module doc)
_ENV_KEEP = ("PATH", "HOME", "USER", "LANG", "TMPDIR", "SSH_AUTH_SOCK")
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_ATTR_NOSYSTEM": "1",
            "GIT_ASKPASS": "", "SSH_ASKPASS": "", "LC_ALL": "C"}
#: the global config the daemon's git reads: none (module doc); a test points it at its own
GLOBAL_CONFIG = os.devnull
_GIT_OPTS = ("-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
             "-c", "credential.helper=", "-c", "core.askPass=", "-c", "core.gitProxy=",
             "-c", "core.sshCommand=ssh -F /dev/null -o BatchMode=yes",
             "-c", "core.alternateRefsCommand=", "-c", "remote.origin.uploadpack=git-upload-pack",
             "-c", "protocol.ext.allow=never", "-c", "submodule.recurse=false",
             "-c", "gc.auto=0", "-c", "maintenance.auto=false")


class WorkError(ValueError):
    """The order is not prepared (or not released); the message is safe to return."""


def _state() -> Path:
    from config import get_app_data_dir
    return Path(get_app_data_dir()) / "hive"


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


def _order_path(work_id: str, suffix: str = ".json") -> Path:
    if not WORK_ID_RE.fullmatch(work_id or ""):
        raise WorkError(ERR_BAD_ORDER)
    return _state() / "orders" / f"{work_id}{suffix}"


def _git(cwd: Optional[Path], *args: str, timeout: int = LOCAL_TIMEOUT_S) -> Optional[subprocess.CompletedProcess]:
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env.update(_GIT_ENV, GIT_CONFIG_GLOBAL=GLOBAL_CONFIG)
    try:
        return subprocess.run(["git", *_GIT_OPTS, *args], cwd=str(cwd) if cwd else None, env=env,
                              capture_output=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None


def _ok(p: Optional[subprocess.CompletedProcess]) -> bool:
    return p is not None and p.returncode == 0


def _plain(root: Path, rel: str) -> bool:
    """No component of ``root/rel`` that exists is a link."""
    p = root
    for part in rel.split("/"):
        p = p / part
        if p.is_symlink():
            return False
    return True


def _mirror_rel(url: str) -> str:
    from core import task_repos
    return task_repos.repo_dir(url).split("/", 2)[2] + ".git"        # <host>/<owner>/<name>.git


def _mirror_sound(m: Path) -> bool:
    """A bare repository this module made: a plain ``config``, ``objects``, ``refs`` and
    ``HEAD``, and none of the files that point git at another repository."""
    if not m.is_dir() or m.is_symlink():
        return False
    for name in ("config", "objects", "refs", "HEAD"):
        if (m / name).is_symlink() or not (m / name).exists():
            return False
    return not any((m / x).exists() or (m / x).is_symlink()
                   for x in ("commondir", "gitdir", "objects/info/alternates", "objects/info/http-alternates"))


def _ensure_mirror(ws: Path, r: dict[str, Any]) -> Path:
    """The mirror of *r*, cloned or fetched, with the Host's bundle fetched; the commit
    the task starts from is set on ``r["sha"]``. Call under its lock."""
    from core.hive_repo_inbox import bundle_rel
    rel = _mirror_rel(r["url"])
    if not _plain(_state(), "mirrors/" + rel):
        raise WorkError(ERR_MIRROR)
    m = _state() / "mirrors" / rel
    bundle = ws / bundle_rel(r["sha"]) if r.get("sha") else None
    has_bundle = bundle is not None and _plain(ws, bundle_rel(r["sha"])) and bundle.is_file()
    if not m.exists():
        m.parent.mkdir(parents=True, exist_ok=True)
        tmp = m.parent / f".{m.name}.cloning"
        shutil.rmtree(tmp, ignore_errors=True)
        cloned = _ok(_git(None, "clone", "--bare", "--no-tags", "--", r["url"], str(tmp),
                          timeout=NET_TIMEOUT_S))
        if not cloned and has_bundle:
            # the remote is out of reach: a whole-branch bundle (the host sends one when
            # its checkout tracks no remote branch) is a repository of its own
            shutil.rmtree(tmp, ignore_errors=True)
            cloned = _ok(_git(None, "clone", "--bare", "--no-tags", "--", str(bundle), str(tmp),
                              timeout=NET_TIMEOUT_S)) and _ok(_git(tmp, "remote", "set-url", "origin", r["url"]))
        if not cloned or not _ok(_git(tmp, "config", "remote.origin.fetch",
                                      "+refs/heads/*:refs/remotes/origin/*")):
            shutil.rmtree(tmp, ignore_errors=True)
            raise WorkError(ERR_CLONE)
        os.replace(tmp, m)
    if not _mirror_sound(m):
        raise WorkError(ERR_MIRROR)
    fetched = _ok(_git(m, "fetch", "--prune", "--no-tags", "origin", timeout=NET_TIMEOUT_S))
    if r.get("sha"):
        if has_bundle and not _ok(_git(m, "fetch", "--no-tags", str(bundle),
                                       f"+HEAD:refs/swarm/incoming/{r['sha']}", timeout=NET_TIMEOUT_S)):
            raise WorkError(ERR_BUNDLE)
        if not _ok(_git(m, "cat-file", "-e", f"{r['sha']}^{{commit}}")):
            # the commit is what matters; an unreachable remote is the reason when it is missing
            raise WorkError(ERR_NO_COMMIT if fetched else ERR_FETCH)
    else:
        if not fetched:
            raise WorkError(ERR_FETCH)
        if not r.get("ref"):
            _git(m, "remote", "set-head", "origin", "--auto", timeout=NET_TIMEOUT_S)
        ref = f"refs/remotes/origin/{r['ref']}" if r.get("ref") else "refs/remotes/origin/HEAD"
        p = _git(m, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
        sha = p.stdout.decode().strip() if _ok(p) else ""
        if not _SHA_RE.fullmatch(sha):
            raise WorkError(ERR_NO_REF)
        r["sha"] = sha
    return m


def _task_clone(m: Path, r: dict[str, Any], target: Path, work_id: str) -> None:
    """The task's own clone of the mirror at ``r["sha"]``: on ``hive/<work_id>`` when
    writable, detached when read-only; its origin is the repository's URL and its
    remote-tracking branches are the mirror's."""
    if not _ok(_git(None, "clone", "--no-checkout", "--no-tags", "--reference", str(m), "--",
                    str(m), str(target))):
        raise WorkError(ERR_WORKTREE)
    steps = [("remote", "set-url", "origin", r["url"]),
             ("fetch", "--no-tags", "--quiet", str(m), "+refs/remotes/origin/*:refs/remotes/origin/*"),
             ("checkout", "-q", "-b", branch(work_id), r["sha"]) if r["access"] == "write"
             else ("checkout", "-q", "--detach", r["sha"])]
    for args in steps:
        if not _ok(_git(target, *args)):
            raise WorkError(ERR_WORKTREE)


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


def _rm_tree(root: Path, rel: str) -> None:
    """Remove ``root/rel`` when no component of it is a link (a link is never followed)."""
    if _plain(root, rel) and (root / rel).exists():
        shutil.rmtree(root / rel, ignore_errors=True)


def _remove(ws: Path, work_id: str) -> None:
    """Remove this order's directories and its files; nothing else."""
    _rm_tree(ws, "/".join((*WORK_ROOT, work_id)))
    _rm_tree(ws, outputs_rel(work_id))
    for suffix in (".json", ".claim"):
        try:
            _order_path(work_id, suffix).unlink(missing_ok=True)
        except OSError:
            pass


def _write_order(order: dict[str, Any]) -> None:
    f = _order_path(order["work_id"])
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.parent / f".{f.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(order, indent=1))
    os.replace(tmp, f)


class _MirrorLock:
    """One exclusive lock per mirror, across threads too (flock on its own fd)."""

    def __init__(self, url: str) -> None:
        import hashlib
        self.path = _state() / "locks" / (hashlib.sha256(_mirror_rel(url).encode()).hexdigest()[:16] + ".lock")

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


def prepare(workspace: Path, work_id: str, repos: Any) -> dict[str, Any]:
    """Make the work order real (module doc); the order as recorded."""
    ws = Path(workspace)
    items = _parse(work_id, repos)
    d = work_dir(ws, work_id)
    if not (_plain(ws, "/".join((*WORK_ROOT, work_id))) and _plain(ws, outputs_rel(work_id))):
        raise WorkError(ERR_PATH)
    if d.exists() or (ws / outputs_rel(work_id)).exists() or _order_path(work_id).exists():
        raise WorkError(ERR_EXISTS)
    try:
        (d / "repos").mkdir(parents=True)
        (ws / outputs_rel(work_id)).mkdir(parents=True)
        listed = []
        for r in items:
            try:
                with _MirrorLock(r["url"]):
                    m = _ensure_mirror(ws, r)
                    _task_clone(m, r, ws / repo_rel(work_id, r["name"]), work_id)
            except WorkError as e:
                raise WorkError(f"{r['name']}: {e}") from None
            listed.append({**r, "dir": repo_rel(work_id, r["name"]),
                           "branch": branch(work_id) if r["access"] == "write" else None})
        order = {"work_id": work_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "prepared_at": time.time(), "session_id": None, "repos": listed,
                 "outputs": outputs_rel(work_id)}
        _write_order(order)
        return order
    except WorkError:
        _remove(ws, work_id)
        raise
    except OSError as exc:
        logger.warning("hive_workspace: preparing %s failed (%s)", work_id, type(exc).__name__)
        _remove(ws, work_id)
        raise WorkError(ERR_WRITE) from None


def load(workspace: Path, work_id: str) -> Optional[dict[str, Any]]:
    """The prepared order, or None when there is none (or it does not read)."""
    try:
        f = _order_path(work_id)
    except WorkError:
        return None
    if f.is_symlink() or not f.is_file():
        return None
    try:
        order = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return order if isinstance(order, dict) and order.get("work_id") == work_id else None


def claim(work_id: str) -> bool:
    """Take the order for one session start (atomic: the ``.claim`` file is created
    exclusively); False when there is no order or a start took it already."""
    try:
        if not _order_path(work_id).is_file():
            return False
        fd = os.open(str(_order_path(work_id, ".claim")),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except (OSError, WorkError):
        return False
    os.close(fd)
    return True


def bind(workspace: Path, work_id: str, session_id: str) -> bool:
    """Record the session that runs the order (read by ``release``); False when there is
    no order or it names another session already."""
    order = load(workspace, work_id)
    if order is None or order.get("session_id") not in (None, session_id):
        return False
    order["session_id"] = session_id
    try:
        _write_order(order)
    except OSError:
        return False
    return True


def task_repos_of(order: dict[str, Any]) -> list[dict[str, Any]]:
    """The order's repositories as ``sessions.metadata.task_repos`` entries
    (core/task_repos.parse reads ``dir``, ``name`` and ``access``)."""
    return [{"url": r["url"], "ref": r["ref"], "sha": r["sha"], "name": r["name"],
             "access": r["access"], "dir": r["dir"]} for r in order.get("repos") or []]


def _orders() -> list[str]:
    root = _state() / "orders"
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(f.stem for f in root.glob("so_*.json") if WORK_ID_RE.fullmatch(f.stem))


def latest_worktree(workspace: Path, url: str) -> Optional[str]:
    """The workspace-relative clone of *url* in the newest prepared order that has one
    (the code a Hive's review reads, core/code_intel/local_roots), or None. Ties go to
    the larger work id."""
    from core import task_repos
    ws = Path(workspace)
    best: tuple[float, str, str] = (0.0, "", "")
    for wid in _orders():
        order = load(ws, wid) or {}
        at = order.get("prepared_at")
        for r in order.get("repos") or []:
            if (isinstance(r, dict) and isinstance(r.get("dir"), str) and isinstance(at, (int, float))
                    and task_repos._same_repo(str(r.get("url")), url) and (float(at), wid) > best[:2]
                    and _plain(ws, r["dir"]) and (ws / r["dir"]).is_dir()):
                best = (float(at), wid, r["dir"])
    return best[2] or None


def release(workspace: Path, work_id: str, busy: Callable[[str], bool]) -> bool:
    """Remove the order's clones (``Hive/work/<work_id>/``, with ``hive/<work_id>``: its
    commits were sealed into the run record at each turn's end, core/hive_bundle),
    ``Outputs/<work_id>/`` and its record; the mirror and every other order are
    untouched. WorkError(ERR_BUSY) while ``busy(session_id)``; False when there was
    nothing."""
    ws = Path(workspace)
    d = work_dir(ws, work_id)
    order = load(ws, work_id)
    if order is None and not d.exists() and not (ws / outputs_rel(work_id)).exists():
        return False
    sid = (order or {}).get("session_id")
    if isinstance(sid, str) and busy(sid):
        raise WorkError(ERR_BUSY)
    _remove(ws, work_id)
    return True


def _last_change(ws: Path, order: dict[str, Any]) -> float:
    """When the order was prepared or last worked on: the newest change to a clone's
    HEAD, index or refs, or to anything directly in its outputs directory."""
    times = [float(order.get("prepared_at") or 0)]
    paths = []
    for r in order.get("repos") or []:
        if isinstance(r, dict) and isinstance(r.get("dir"), str) and _plain(ws, r["dir"]):
            g = ws / r["dir"] / ".git"
            paths += [g / "HEAD", g / "index", g / "logs" / "HEAD"]
    out = ws / outputs_rel(order["work_id"])
    if _plain(ws, outputs_rel(order["work_id"])) and out.is_dir():
        paths += [out, *out.iterdir()]
    for p in paths:
        try:
            times.append(p.lstat().st_mtime)
        except OSError:
            continue
    return max(times)


def sweep(workspace: Path, busy: Callable[[str], bool], now: Optional[float] = None) -> int:
    """Release orders nothing changed in for RETENTION_DAYS (``_last_change``); the count."""
    ws = Path(workspace)
    now = time.time() if now is None else now
    n = 0
    for wid in _orders():
        order = load(ws, wid)
        if order is None or now - _last_change(ws, order) <= RETENTION_DAYS * 86400:
            continue
        try:
            if release(ws, wid, busy):
                n += 1
        except (WorkError, OSError):
            continue
    return n
