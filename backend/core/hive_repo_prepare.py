"""On a Hive: put a dispatched task's repository at the host's commit before the task
starts (design docs/design/2026-10-30-hive-task-repos-from-host.md §2.4).

The host sends, per repository, its URL, branch, commit and usually a git bundle of
what the remote does not have (core/repo_ship). ``prepare`` leaves
``<workspace>/Hive/repos/<host>/<owner>/<name>`` (core/task_repos.repo_dir) holding that
commit, checked out on that branch.

**Git never runs in a directory the Hive's agent could have written.** The agent can
write anything under its workspace, a repository's ``.git`` included, and git runs
configured commands (filters, upload-pack, alternate-refs commands) and follows
configured paths (``core.worktree``, ``config.worktree``, alternates) when it fetches or
checks out. A list of risky keys always misses one, so nothing in the workspace is read:

1. history lives in a cache that only this daemon writes, outside the workspace
   (``<data dir>/hive-repo-cache/<host>__<owner>__<name>.git``, bare): cloned from the
   URL the first time (or started empty when the bundle is the whole branch, no
   network), fetched from the URL when a thin bundle's prerequisites are missing
   (still missing → ``NeedFull``: the host then sends the whole branch), then the
   bundle's ``HEAD`` is fetched into ``refs/swarm/tip`` and must be the declared commit;
2. a new directory next to the cache gets a clone of it (``--no-local``: no object file
   is shared with the cache, so nothing the agent later does to its repository reaches
   the cache), the commit checked out on the branch, and ``origin`` set to the URL;
3. that directory is moved into the workspace. What was there before (a previous task's
   checkout; its commits went back with its turns) is kept as ``<name>.previous``,
   replacing an older one.

**Nothing in the workspace is reached by path after it is checked.** The directories
from the workspace down to ``<owner>`` are opened one by one, never following a symlink
(one in their place is refused), before anything is created; the rename into place, the
rename to ``.previous`` and the removal of an older ``.previous`` are done relative to
that open directory, so a directory swapped for a symlink while the history is fetched
changes nothing outside. The cache must not be inside the workspace.

Every git call has fixed arguments, no shell, hooks off, no global or system config,
submodules not followed and only the https, ssh and file protocols. One preparation has
one deadline (``PREPARE_DEADLINE_S``), shorter than the host's wait. Git's own error
text is logged here, never returned.

- ``prepare(workspace, url, branch, sha, bundle, cache_root=None)`` — {dir, branch, head};
  raises PrepareError (``status`` is the HTTP status to answer) or NeedFull
- ``cache_dir()`` — the cache root (also where uploads wait)
"""
from __future__ import annotations

import errno
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

#: one preparation, end to end; the host waits SHIP_TIMEOUT_S (core/swarm_out), longer
PREPARE_DEADLINE_S = 1000
GIT_TIMEOUT_S = 120
#: the text header of a bundle (its prerequisite and ref lines) is read up to this size
MAX_HEADER_BYTES = 16 * 1024 * 1024
_SHA_RE = re.compile(r"[0-9a-f]{40,64}")
_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
_OPTS = ("-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
         "-c", "submodule.recurse=false", "-c", "fetch.recurseSubmodules=false",
         "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
         "-c", "protocol.ssh.allow=always", "-c", "protocol.file.allow=always")
_TIP = "refs/swarm/tip"


class PrepareError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


class NeedFull(PrepareError):
    """The bundle needs history this Hive cannot get: send the whole branch."""

    def __init__(self) -> None:
        super().__init__("the Hive cannot get the history this bundle builds on; send the whole branch", 409)


def cache_dir() -> Path:
    from config import get_app_data_dir
    return get_app_data_dir() / "hive-repo-cache"


def _git(cwd: Optional[Path], deadline: float, *args: str, timeout: int = GIT_TIMEOUT_S,
         check: bool = True) -> subprocess.CompletedProcess:
    left = deadline - time.monotonic()
    if left <= 0:
        raise PrepareError("preparing the repository took too long", 504)
    try:
        p = subprocess.run(["git", *_OPTS, *args], cwd=str(cwd) if cwd else None,
                           env={**os.environ, **_ENV}, capture_output=True,
                           timeout=min(timeout, left), check=False)
    except subprocess.TimeoutExpired:
        raise PrepareError(f"git {args[0]} took too long", 504) from None
    except OSError as e:
        raise PrepareError(f"git could not run ({type(e).__name__})", 500) from None
    if check and p.returncode != 0:
        logger.warning("hive repos: git %s failed: %s", args[0],
                       p.stderr.decode("utf-8", "replace").strip()[:500])
        raise PrepareError(f"git {args[0]} failed", 500)
    return p


def _has(repo: Path, deadline: float, sha: str) -> bool:
    return _git(repo, deadline, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def _prerequisites(bundle: Path) -> list[str]:
    """The commits a bundle builds on, from its header (``-<sha> ...`` lines, up to the
    blank line that ends it). A header that does not end within MAX_HEADER_BYTES is
    answered with NeedFull, never read in part."""
    head = b""
    with open(bundle, "rb") as f:
        while b"\n\n" not in head and len(head) < MAX_HEADER_BYTES:
            chunk = f.read(1 << 16)
            if not chunk:
                break
            head += chunk
    if not head.startswith(b"# v") or b"git bundle" not in head.split(b"\n", 1)[0]:
        raise PrepareError("the upload is not a git bundle")
    if b"\n\n" not in head:
        raise NeedFull()
    out = []
    for line in head.split(b"\n\n", 1)[0].split(b"\n")[1:]:
        if line.startswith(b"-"):
            sha = line[1:].split(b" ", 1)[0].decode("ascii", "replace")
            if not _SHA_RE.fullmatch(sha):
                raise PrepareError("the bundle names a prerequisite oddly")
            out.append(sha)
    return out


def _cache_path(cache_root: Path, rel_dir: str) -> Path:
    """``<cache_root>/<host>/<owner>/<name>.git`` (a path segment never holds ``/``, so
    two repositories never share one)."""
    from core import task_repos
    parts = rel_dir.split("/")[len(task_repos.REPOS_ROOT):]
    return cache_root.joinpath(*parts[:-1], parts[-1] + ".git")


def _open_parent(workspace: Path, parts: list[str]) -> int:
    """An open descriptor of ``<workspace>/<parts...>``, each directory opened without
    following a symlink and made when missing. Raises PrepareError (outside)."""
    from core import task_repos
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(Path(workspace).resolve()), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            try:
                nxt = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, 0o755, dir_fd=fd)
                nxt = os.open(part, flags, dir_fd=fd)
            except OSError:                        # a symlink (ELOOP) or a file (ENOTDIR)
                raise PrepareError(task_repos.ERR_OUTSIDE) from None
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _fill_cache(cache: Path, deadline: float, url: str, sha: str, bundle: Optional[Path]) -> None:
    """``refs/swarm/tip`` of the cache = ``sha``, its history from the URL as needed."""
    prereqs = _prerequisites(bundle) if bundle else []
    if not (cache / "HEAD").is_file():
        shutil.rmtree(cache, ignore_errors=True)          # a cache this daemon left half made
        cache.parent.mkdir(parents=True, exist_ok=True)
        try:
            if bundle is not None and not prereqs:        # the whole branch: no network
                _git(None, deadline, "init", "--bare", "--quiet", str(cache))
            elif _git(None, deadline, "clone", "--bare", "--quiet", url, str(cache),
                      timeout=PREPARE_DEADLINE_S, check=False).returncode != 0:
                raise NeedFull()
        except BaseException:
            shutil.rmtree(cache, ignore_errors=True)
            raise
    fetch = ("fetch", "--no-tags", "--quiet", url, "+refs/heads/*:refs/heads/*")
    if prereqs and not all(_has(cache, deadline, s) for s in prereqs):
        _git(cache, deadline, *fetch, timeout=PREPARE_DEADLINE_S, check=False)
        if not all(_has(cache, deadline, s) for s in prereqs):
            raise NeedFull()
    if bundle is not None:
        _git(cache, deadline, "fetch", "--no-tags", "--quiet", str(bundle), f"+HEAD:{_TIP}",
             timeout=PREPARE_DEADLINE_S)
        tip = _git(cache, deadline, "rev-parse", "--verify", f"{_TIP}^{{commit}}").stdout.decode().strip()
        if tip != sha:
            raise PrepareError("the bundle does not hold the declared commit")
        return
    if not _has(cache, deadline, sha):
        _git(cache, deadline, *fetch, timeout=PREPARE_DEADLINE_S, check=False)
        if not _has(cache, deadline, sha):
            raise NeedFull()
    _git(cache, deadline, "update-ref", _TIP, sha)


def prepare(workspace: Path, url: str, branch: str, sha: str, bundle: Optional[Path],
            cache_root: Optional[Path] = None) -> dict:
    from core import task_repos
    deadline = time.monotonic() + PREPARE_DEADLINE_S
    if not _SHA_RE.fullmatch(sha or ""):
        raise PrepareError("the commit is not a sha")
    if not branch:
        raise PrepareError("a branch is required")
    try:
        declared = task_repos.parse([{"url": url, "ref": branch}])[0]
    except task_repos.TaskRepoError as e:
        raise PrepareError(str(e)) from None
    cache_root = Path(cache_root) if cache_root else cache_dir()
    if task_repos._inside(Path(workspace).resolve(), cache_root.resolve()):
        raise PrepareError("the repository cache is inside the workspace", 500)
    *parents, name = declared["dir"].split("/")
    parent_fd = _open_parent(Path(workspace), parents)
    try:
        cache = _cache_path(cache_root, declared["dir"])
        _fill_cache(cache, deadline, url, sha, bundle)
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=cache_root))
        try:
            work = stage / "repo"
            _git(None, deadline, "clone", "--no-local", "--no-checkout", "--quiet", str(cache), str(work))
            _git(work, deadline, "fetch", "--no-tags", "--quiet", str(cache), _TIP)
            _git(work, deadline, "checkout", "-f", "--quiet", "-B", branch, sha)
            _git(work, deadline, "remote", "set-url", "origin", url)
            head = _git(work, deadline, "rev-parse", "--verify", "HEAD").stdout.decode().strip()
            if head != sha:
                raise PrepareError("the checkout did not end on the declared commit", 500)
            _swap_in(parent_fd, name, work)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    finally:
        os.close(parent_fd)
    return {"dir": declared["dir"], "branch": branch, "head": head}


def _exists(name: str, fd: int) -> bool:
    try:
        os.lstat(name, dir_fd=fd)
        return True
    except FileNotFoundError:
        return False


def _swap_in(fd: int, name: str, work: Path) -> None:
    """``work`` becomes ``name`` in the open directory ``fd``; what was there becomes
    ``name.previous`` (an older one removed), all relative to ``fd``."""
    previous = f"{name}.previous"
    if _exists(name, fd):
        if _exists(previous, fd):
            st = os.lstat(previous, dir_fd=fd)
            if stat.S_ISDIR(st.st_mode):
                shutil.rmtree(previous, dir_fd=fd)     # never follows a link inside
            else:
                os.unlink(previous, dir_fd=fd)
        os.rename(name, previous, src_dir_fd=fd, dst_dir_fd=fd)
    try:
        os.rename(str(work), name, dst_dir_fd=fd)
    except OSError as e:
        if e.errno == errno.EXDEV:
            raise PrepareError("the repository cache and the workspace are on different filesystems", 500) from None
        raise PrepareError("the checkout could not be moved into place", 500) from None
