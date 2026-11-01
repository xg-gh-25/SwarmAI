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

**What this guarantees, and what it does not** (design run-spec r4.2, XG's choice A).
On a Hive the agent runs as the same OS user as the daemon and can run any command, so
no file the daemon reads is out of its reach: this module is not a boundary against an
agent that sets out to attack it (that needs the agent under its own OS user, a
separate design). What it does guarantee:
- **no daemon secret reaches a command git starts**: every git command here (and the
  turn-end ones in core/task_repos and core/hive_bundle, through ``git_env``) runs with
  a minimal environment;
- **content a repository carries never runs**: hooks off, the global, system and
  attributes/ignore files off, the config keys that start a command fixed on the command
  line (credential helper, askpass, ssh command, proxy, upload-pack, alternate refs,
  fsmonitor), and only the https, ssh and file transports allowed (no remote helper);
- **a tampered mirror is not trusted**: its config is rewritten from the URL before each
  fetch, and one with a ``commondir``, ``gitdir`` or ``alternates`` file, or a link, is
  refused; a task's clone is made and checked out in the daemon's staging directory and
  moved into the workspace only when done; the Host's bundle is copied out of the
  workspace before git opens it.
One file lock per mirror serializes its fetches and the clones made from it.

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
#: what every git command here runs with (module doc); the ssh command is ``_ssh_opt``
GIT_SAFE_OPTS = ("-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
                 "-c", "core.attributesFile=" + os.devnull, "-c", "core.excludesFile=" + os.devnull,
                 "-c", "credential.helper=", "-c", "core.askPass=", "-c", "core.gitProxy=",
                 "-c", "core.alternateRefsCommand=", "-c", "remote.origin.uploadpack=git-upload-pack",
                 "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
                 "-c", "protocol.ssh.allow=always", "-c", "protocol.file.allow=always",
                 "-c", "submodule.recurse=false", "-c", "gc.auto=0", "-c", "maintenance.auto=false")


def git_env() -> dict[str, str]:
    """The environment of a git command the daemon runs in a Hive's repositories: the
    variables in ``_ENV_KEEP`` only, global and system config off (module doc)."""
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env.update(_GIT_ENV, GIT_CONFIG_GLOBAL=GLOBAL_CONFIG)
    return env


def _ssh_opt() -> tuple[str, str]:
    import shlex
    known = _state() / "known_hosts"
    return ("-c", "core.sshCommand=ssh -F /dev/null -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
            f"-o UserKnownHostsFile={shlex.quote(str(known))}")


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
    try:
        return subprocess.run(["git", *GIT_SAFE_OPTS, *_ssh_opt(), *args], cwd=str(cwd) if cwd else None,
                              env=git_env(),
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


def _write_mirror_config(m: Path, url: str) -> None:
    """The mirror's config from the URL alone (an earlier one, whoever wrote it, is not
    read), keeping its object format."""
    cfg = m / "config"
    fmt = "sha1"
    try:
        if cfg.is_file() and not cfg.is_symlink() and re.search(
                r"(?im)^\s*objectformat\s*=\s*sha256\s*$", cfg.read_text(encoding="utf-8", errors="replace")):
            fmt = "sha256"
    except OSError:
        pass
    lines = ["[core]", f"\trepositoryformatversion = {1 if fmt == 'sha256' else 0}", "\tbare = true"]
    if fmt == "sha256":
        lines += ["[extensions]", "\tobjectformat = sha256"]
    lines += ['[remote "origin"]', f"\turl = {url}", "\tfetch = +refs/heads/*:refs/remotes/origin/*"]
    tmp = m / f".config.{os.getpid()}.{time.monotonic_ns()}"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, cfg)


def _copy_bundle(ws: Path, sha: str) -> Optional[Path]:
    """The Host's bundle copied (without following a link) to the daemon's staging
    directory, so what git opens is the file that was checked; None when there is none."""
    from core.hive_repo_inbox import bundle_rel
    rel = bundle_rel(sha)
    if not _plain(ws, rel):
        return None
    try:
        fd = os.open(str(ws / rel), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        import stat as _stat
        if not _stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        out = _state() / "staging" / f"{sha}.{os.getpid()}.{time.monotonic_ns()}.bundle"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(fd, "rb", closefd=False) as src, open(out, "xb") as dst:
            shutil.copyfileobj(src, dst)
        return out
    finally:
        os.close(fd)


def _ensure_mirror(ws: Path, r: dict[str, Any]) -> Path:
    bundle = _copy_bundle(ws, r["sha"]) if r.get("sha") else None
    try:
        return _ensure_mirror_from(ws, r, bundle)
    finally:
        if bundle is not None:
            bundle.unlink(missing_ok=True)


def _ensure_mirror_from(ws: Path, r: dict[str, Any], bundle: Optional[Path]) -> Path:
    """The mirror of *r*, cloned or fetched, with the Host's bundle (copied out of the
    workspace, ``_copy_bundle``) fetched; the commit the task starts from is set on
    ``r["sha"]``. Call under its lock."""
    rel = _mirror_rel(r["url"])
    if not _plain(_state(), "mirrors/" + rel):
        raise WorkError(ERR_MIRROR)
    m = _state() / "mirrors" / rel
    has_bundle = bundle is not None
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
                              timeout=NET_TIMEOUT_S))
        if not cloned or not _mirror_sound(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
            raise WorkError(ERR_CLONE)
        _write_mirror_config(tmp, r["url"])
        os.replace(tmp, m)
    if not _mirror_sound(m):
        raise WorkError(ERR_MIRROR)
    _write_mirror_config(m, r["url"])
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


def _task_clone(m: Path, r: dict[str, Any], ws: Path, work_id: str) -> None:
    """The task's own clone of the mirror at ``r["sha"]``: on ``hive/<work_id>`` when
    writable, detached when read-only; its origin is the repository's URL and its
    remote-tracking branches are the mirror's. Made and checked out in the daemon's
    staging directory, then moved to ``repo_rel`` (a path with a link on it is refused)."""
    stage = _state() / "staging" / work_id / r["name"]
    shutil.rmtree(stage, ignore_errors=True)
    stage.parent.mkdir(parents=True, exist_ok=True)
    try:
        _clone_into(m, r, stage, work_id)
        rel = repo_rel(work_id, r["name"])
        if not _plain(ws, rel) or (ws / rel).exists():
            raise WorkError(ERR_PATH)
        os.rename(stage, ws / rel)
        if (ws / rel).is_symlink() or not _plain(ws, rel):
            raise WorkError(ERR_PATH)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _clone_into(m: Path, r: dict[str, Any], target: Path, work_id: str) -> None:
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
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        self.f = os.fdopen(fd, "a+")
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
                    _task_clone(m, r, ws, work_id)
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
    except Exception as exc:  # noqa: BLE001 — any other failure: the fixed text, nothing left
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


def order_state(workspace: Path, work_id: str) -> dict[str, Any]:
    """What the Host may ask about one of its orders (GET /api/hive/work/{id}): whether
    it exists, whether a session start took it (``claimed``) and the session it named.
    The Host asks for a dispatch that never learned its session id: claimed with a
    session, it follows that session; no order, or one never claimed, means the task never
    started (design 2026-11-01-brain-to-hive-zero-touch §2 item 8)."""
    order = load(workspace, work_id)
    if order is None:
        return {"exists": False, "claimed": False, "session_id": None}
    try:
        claimed = _order_path(work_id, ".claim").is_file()
    except WorkError:
        claimed = False
    sid = order.get("session_id")
    return {"exists": True, "claimed": claimed, "session_id": sid if isinstance(sid, str) else None}


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
