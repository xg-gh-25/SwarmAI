"""This machine's commit of a Brain's repository, for a Hive (design
docs/design/2026-10-30-hive-task-repos-from-host.md §2).

A task dispatched to a Hive works on the commit the person's checkout has, not on what
its remote has (a Brain whose commits land on local main is often far ahead of it). The
host reads the checkout's branch and HEAD (``state``) and writes a git bundle of that
commit (``pack``): only what the remote does not have when the checkout tracks a branch
of the repository's URL (``<base>..HEAD``, base = their merge-base), else the whole
branch. The Hive keeps the bundle (core/hive_repo_inbox); its backend clones the URL for
the rest of the history and fetches the bundle when it prepares the task's workspace
(core/hive_workspace).

Only read-only git commands run in the checkout (``rev-parse``, ``status``, ``remote``,
``merge-base``; ``bundle create`` writes only the output file), with fixed arguments, no
shell and hooks off. A bundle over ``MAX_BUNDLE_BYTES`` is refused, never cut.

A task may also start from a commit other than HEAD (Swarm Run's ``base``: a contract
the lead run wrote, a snapshot ref of its run; design 2026-10-31-swarm-run §5.1).
``resolve`` turns that ref into a commit of the checkout, and ``pack(..., pinned=True)``
bundles exactly that commit: in a temporary bare repository that borrows the checkout's
objects (``objects/info/alternates``) with its ``HEAD`` set to the commit, so the bundle's
one ref is still ``HEAD`` (the Hive fetches ``+HEAD:``, unchanged) and the checkout gets
no ref and no write.

- ``state(checkout)`` — {branch, sha, dirty} of a checkout; raises ShipError
- ``resolve(checkout, ref)`` — the commit a ref or sha names in the checkout; raises ShipError
- ``contains(checkout, sha, branch)`` — whether the commit is in the branch's history
- ``pack(checkout, url, branch, sha, full=False, pinned=False)`` — (bundle path or None,
  base or None, bytes)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

MAX_BUNDLE_BYTES = 100 * 1024 * 1024
GIT_TIMEOUT_S = 300
_SHA_RE = re.compile(r"[0-9a-f]{40,64}")
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}


class ShipError(ValueError):
    """The commit cannot be shipped; the message is safe to show the person."""


def _git(cwd: Path, *args: str, timeout: float = 60) -> str:
    try:
        p = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args],
                           cwd=str(cwd), env={**os.environ, **_GIT_ENV}, capture_output=True,
                           timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise ShipError(f"git {args[0]} timed out in {cwd.name}") from None
    except OSError as e:
        raise ShipError(f"git could not run in {cwd.name} ({type(e).__name__})") from None
    if p.returncode != 0:
        raise ShipError(f"git {args[0]} failed in {cwd.name}: {p.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return p.stdout.decode("utf-8", "replace")


def state(checkout: Path) -> dict:
    """The checkout's branch, HEAD commit and number of paths with uncommitted changes
    (they are not sent)."""
    checkout = Path(checkout)
    ref = _git(checkout, "symbolic-ref", "--quiet", "HEAD").strip()
    if not ref.startswith("refs/heads/"):
        raise ShipError(f"{checkout.name} is not on a branch")
    sha = _git(checkout, "rev-parse", "--verify", "HEAD^{commit}").strip()
    if not _SHA_RE.fullmatch(sha):
        raise ShipError(f"{checkout.name} has no commit checked out")
    dirty = _git(checkout, "status", "--porcelain", "-z", "--untracked-files=no")
    return {"branch": ref[len("refs/heads/"):], "sha": sha, "dirty": sum(1 for e in dirty.split("\0") if e)}



def resolve(checkout: Path, ref: str) -> str:
    """The commit *ref* names in *checkout* (a sha, a branch, a tag, a snapshot ref such
    as ``refs/swarm/snapshots/<run>/<label>``). Raises ShipError when it names none."""
    checkout = Path(checkout)
    ref = (ref or "").strip()
    # ``--verify`` takes exactly one revision (a range or a pathspec fails) and
    # ``--end-of-options`` keeps a value starting with "-" from being read as an option
    if not ref or len(ref) > 200:
        raise ShipError(f"{ref[:60]!r} is not a commit or a ref name")
    try:
        sha = _git(checkout, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}").strip()
    except ShipError:
        raise ShipError(f"{checkout.name} has no commit {ref[:60]!r}") from None
    if not _SHA_RE.fullmatch(sha):
        raise ShipError(f"{checkout.name} has no commit {ref[:60]!r}")
    return sha


def contains(checkout: Path, sha: str, branch: str) -> bool:
    """Whether *sha* is in the history of *checkout*'s *branch*."""
    try:
        p = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "merge-base", "--is-ancestor", sha,
                            f"refs/heads/{branch}"], cwd=str(checkout), env={**os.environ, **_GIT_ENV},
                           capture_output=True, timeout=60, check=False)
    except (subprocess.TimeoutExpired, OSError):
        raise ShipError(f"git merge-base could not run in {Path(checkout).name}") from None
    if p.returncode not in (0, 1):
        raise ShipError(f"git merge-base failed in {Path(checkout).name}")
    return p.returncode == 0


def _pinned_repo(checkout: Path, sha: str, left) -> Path:
    """A temporary bare repository whose HEAD is *sha*, borrowing *checkout*'s objects.
    The caller removes it."""
    fmt = _git(checkout, "rev-parse", "--show-object-format", timeout=left()).strip() or "sha1"
    common = Path(_git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir",
                       timeout=left()).strip())
    tmp = Path(tempfile.mkdtemp(prefix="swarm-pin-"))
    try:
        _git(tmp, "init", "-q", "--bare", f"--object-format={fmt}", str(tmp / "r.git"), timeout=left())
        repo = tmp / "r.git"
        (repo / "objects" / "info" / "alternates").write_text(str(common / "objects") + "\n")
        (repo / "HEAD").write_text(sha + "\n")
        return repo
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def _tracking_base(checkout: Path, url: str, branch: str, sha: str,
                   left=lambda: 60.0) -> Optional[str]:
    """The merge-base of ``sha`` with the tracking branch of a remote of the checkout
    whose URL is ``url``; None when there is none. ``left()``: how long the next git
    call may run (``pack``'s time left)."""
    from core import task_repos
    try:
        # the configured URLs (``remote -v`` shows them after any ``insteadOf`` rewrite)
        lines = _git(checkout, "config", "--get-regexp", r"^remote\..*\.url$", timeout=left()).splitlines()
    except ShipError:
        return None                          # no remote at all
    for line in lines:
        key, _, value = line.partition(" ")
        if not task_repos._same_repo(task_repos._origin_key(value.strip()), url):
            continue
        remote = key[len("remote."):-len(".url")]
        for ref in (f"refs/remotes/{remote}/{branch}", f"refs/remotes/{remote}/HEAD"):
            try:
                _git(checkout, "rev-parse", "--verify", "--quiet", ref + "^{commit}", timeout=left())
            except ShipError:
                continue
            try:
                base = _git(checkout, "merge-base", sha, ref, timeout=left()).strip()
            except ShipError:
                continue                     # no common history
            if _SHA_RE.fullmatch(base):
                return base
    return None


def pack(checkout: Path, url: str, branch: str, sha: str,
         full: bool = False, timeout: float = GIT_TIMEOUT_S,
         pinned: bool = False) -> tuple[Optional[Path], Optional[str], int]:
    """A bundle whose ``HEAD`` is ``sha``: thin (``base..sha``) unless ``full`` or there is
    no base. ``(None, sha, 0)`` when the remote has ``sha`` already (nothing to send). The
    caller removes the file. Raises ShipError, also when HEAD moved off ``sha`` (unless
    ``pinned``: then ``sha`` is the commit asked for, whatever HEAD is; see the module doc).
    ``timeout``: how long its git calls may run in all (a dispatch passes what is left
    of its deadline, core/swarm_out); each also keeps its own cap."""
    checkout = Path(checkout)
    end = time.monotonic() + timeout

    def left(cap: float = 60.0) -> float:
        return max(0.001, min(cap, end - time.monotonic()))
    base = None if full else _tracking_base(checkout, url, branch, sha, left)
    if base == sha:
        return None, sha, 0
    fd, name = tempfile.mkstemp(prefix="swarm-repo-", suffix=".bundle")
    os.close(fd)
    path = Path(name)
    pin = None
    try:
        # HEAD, not a ref written for the purpose: the checkout is never changed. The
        # bundle is checked to hold exactly ``sha`` (HEAD may have moved since ``state``).
        if pinned:
            pin = _pinned_repo(checkout, sha, left)
        src = pin or checkout
        _git(src, "bundle", "create", str(path), "HEAD", *([f"^{base}"] if base else []),
             timeout=left(GIT_TIMEOUT_S))
        heads = _git(src, "bundle", "list-heads", str(path), timeout=left()).split()
        if sha not in heads:
            raise ShipError(f"{checkout.name} moved while it was being packed; send the task again")
        size = path.stat().st_size
        if size > MAX_BUNDLE_BYTES:
            raise ShipError(f"{checkout.name}'s commit is {size // (1024 * 1024)} MB as a bundle, "
                            f"more than a Hive takes ({MAX_BUNDLE_BYTES // (1024 * 1024)} MB)")
        return path, base, size
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        if pin is not None:
            shutil.rmtree(pin.parent, ignore_errors=True)
