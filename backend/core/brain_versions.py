"""The versions of a Brain a Hive keeps for turns still using them (design
docs/design/2026-10-28-project-agent-flow.md §6, "安装和 turn 互斥").

``Projects/<brain>`` (the General Brain: ``Knowledge/Library``) is always the newest
installed version: the stable path every writer and every reader that is not running
for one session uses. When an install would change the files turns are using, it first
keeps a copy of the current version here, and the turns that hold it read the copy
until they end:

- ``Hive/brains/<brain>/versions/<id>/`` -- the Brain's allowlisted files as they were
  on disk when the install came (Hive edits included: that is what the turns read),
  built in a staging directory and renamed in, so a reader never sees half a copy.
- ``Hive/brains/<brain>/turns/<session>`` -- the id of the version the session's turn
  holds; only sessions whose turn holds a kept version have one.

``view`` is the one place a reader learns which directory is its session's Brain: the
kept version its marker names, else the stable path. The session is the caller's (the
Brain gate hook passes its own) or, in a process run for one session (the agent's
``cognition find``, the pipeline CLI), ``SWARM_APP_SESSION_ID``. Off a Hive the store
never exists, so ``view`` costs one failed open.

The backend's registry (``core.brain_use``) decides what is kept and which session
reads what; this module only makes the disk match (``keep``, ``reconcile``) and empties
the store when a Hive backend starts (``clear``: no turn of an earlier process is
alive). Every call here runs on the ``brain_versions`` executor, one at a time, so a
cleanup never removes a copy being made. Every write and delete goes through directory
descriptors opened without following links: a link planted in the store redirects
no write or delete. ``view`` refuses a version directory that is reached through a link
when it is called (a link swapped in after that call is not seen; it would point the
session's own reads elsewhere, which the agent can do anyway, and the Brain gates still
have to match the session's pinned digest).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Optional

from core import brain_sync

logger = logging.getLogger(__name__)

SESSION_ENV = "SWARM_APP_SESSION_ID"
STORE = ("Hive", "brains")
VERSIONS = "versions"
TURNS = "turns"
VERSION_ID_RE = re.compile(r"[0-9a-f]{16}")
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)


def _store_dir(workspace: Path, brain: str, create: bool) -> int:
    """An fd of ``Hive/brains/<brain>`` opened without following a link in
    ``Hive/brains/<brain>``; FileNotFoundError when it is not there and not *create*."""
    if not brain_sync.BRAIN_NAME_RE.fullmatch(brain or ""):
        raise brain_sync.SyncError(f"not a Brain name: {brain!r}")
    fd = os.open(str(workspace), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in (*STORE, brain):
            nxt = brain_sync._open_dir(fd, part, create)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _remove_at(dir_fd: int, name: str) -> None:
    """Remove the entry *name* of *dir_fd* (a tree, a file or a link; a link is removed,
    never followed)."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(name, dir_fd=dir_fd)
    else:
        os.unlink(name, dir_fd=dir_fd)


def _read_at(dir_fd: int, name: str) -> Optional[str]:
    """The text of the small regular file *name* of *dir_fd* (not followed if a link),
    or None."""
    try:
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                     dir_fd=dir_fd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, 64).decode("ascii", "replace").strip()
    finally:
        os.close(fd)


def keep(workspace: Path, brain: str, vid: str) -> Optional[str]:
    """Copy the live version of *brain* to ``versions/<vid>/``; returns the digest its
    ``.brain.json`` records (None when it has none). Raises SyncError or OSError when
    the copy cannot be made; then nothing of it is left."""
    if not VERSION_ID_RE.fullmatch(vid):
        raise brain_sync.SyncError(f"not a version id: {vid!r}")
    live, general = brain_sync.brain_root(workspace, brain)
    files = brain_sync.installed_files(live, general)
    try:
        rec = json.loads(brain_sync._read_plain(live / brain_sync.STATE_FILE, 4 << 20).decode("utf-8"))
        digest = rec.get("digest") if isinstance(rec, dict) else None
    except (brain_sync.SyncError, ValueError):
        digest = None
    bfd = _store_dir(Path(workspace), brain, create=True)
    try:
        vfd = brain_sync._open_dir(bfd, VERSIONS, True)
    finally:
        os.close(bfd)
    stage = f".{vid}.tmp"
    try:
        _remove_at(vfd, stage)              # a staging dir a killed copy left
        os.mkdir(stage, 0o755, dir_fd=vfd)
        sfd = os.open(stage, brain_sync._DIR_FLAGS, dir_fd=vfd)
        try:
            for rel in sorted(files):
                dfd, name = brain_sync._walk_to(sfd, rel, create=True)
                try:
                    fd = os.open(name, _FILE_FLAGS, 0o644, dir_fd=dfd)
                    try:
                        view = memoryview(files[rel])
                        while view:
                            view = view[os.write(fd, view):]
                    finally:
                        os.close(fd)
                finally:
                    os.close(dfd)
        finally:
            os.close(sfd)
        os.rename(stage, vid, src_dir_fd=vfd, dst_dir_fd=vfd)
    except BaseException:
        try:
            _remove_at(vfd, stage)
        except OSError:
            logger.warning("brain versions: staging copy of %s not removed", brain, exc_info=True)
        raise
    finally:
        os.close(vfd)
    return digest if isinstance(digest, str) else None


def view(workspace: Path, brain: str, live: Path, session_id: Optional[str] = None) -> Path:
    """The directory the session's turn reads *brain* from: the kept version its marker
    names, else *live* (the stable path). *session_id* defaults to
    ``SWARM_APP_SESSION_ID``. A marker that cannot be read, names no valid id, or a
    version reached through a link gives *live*. Never raises."""
    sid = session_id if session_id is not None else os.environ.get(SESSION_ENV, "")
    if not sid or not SESSION_ID_RE.fullmatch(sid) or not isinstance(brain, str) \
            or not brain_sync.BRAIN_NAME_RE.fullmatch(brain):
        return live
    base = Path(workspace).joinpath(*STORE, brain)
    try:
        vid = brain_sync._read_plain(base / TURNS / sid, 64).decode("ascii").strip()
    except (brain_sync.SyncError, UnicodeDecodeError, OSError):
        return live
    if not VERSION_ID_RE.fullmatch(vid):
        return live
    d = base / VERSIONS / vid
    try:
        canonical = Path(os.path.realpath(workspace)).joinpath(*STORE, brain, VERSIONS, vid)
        if not d.is_dir() or Path(os.path.realpath(d)) != canonical:
            return live
    except OSError:
        return live
    return d


def in_version(workspace: Path, path: Path) -> Optional[tuple[str, Path]]:
    """(the Brain, the path relative to the version) when the file *path* (resolved) is
    in a kept version, else None."""
    try:
        rel = Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(workspace)).joinpath(*STORE))
    except (OSError, ValueError):
        return None
    parts = rel.parts
    if len(parts) >= 4 and parts[1] == VERSIONS and VERSION_ID_RE.fullmatch(parts[2]) \
            and brain_sync.BRAIN_NAME_RE.fullmatch(parts[0]):
        return parts[0], Path(*parts[3:])
    return None


def brain_of(workspace: Path, path: Path) -> Optional[str]:
    """The Brain whose kept version holds the file *path* (resolved), or None."""
    found = in_version(workspace, path)
    return found[0] if found else None


def reconcile(workspace: Path, brain: str, markers: dict[str, str], keep_ids: set[str]) -> None:
    """Make the store of *brain* hold exactly the kept versions *keep_ids* and the
    session markers *markers* ``{session: version id}``; everything else under it is
    removed."""
    try:
        bfd = _store_dir(Path(workspace), brain, create=bool(markers or keep_ids))
    except FileNotFoundError:
        return
    try:
        tfd = brain_sync._open_dir(bfd, TURNS, True)
        try:
            for name in os.listdir(tfd):
                want = markers.get(name)
                if want is None or not SESSION_ID_RE.fullmatch(name):
                    _remove_at(tfd, name)
                    continue
                if _read_at(tfd, name) != want:
                    _remove_at(tfd, name)
            present = set(os.listdir(tfd))
            for sid, vid in sorted(markers.items()):
                if sid not in present and SESSION_ID_RE.fullmatch(sid) and VERSION_ID_RE.fullmatch(vid):
                    brain_sync._write(tfd, sid, vid.encode("ascii"))
        finally:
            os.close(tfd)
        vfd = brain_sync._open_dir(bfd, VERSIONS, True)
        try:
            for name in os.listdir(vfd):
                if name not in keep_ids:
                    _remove_at(vfd, name)
        finally:
            os.close(vfd)
    finally:
        os.close(bfd)


def clear(workspace: Path) -> None:
    """Remove every kept version and marker (a Hive backend's start: no turn of an
    earlier process is alive, so none of them is read any more)."""
    try:
        fd = os.open(str(workspace), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except FileNotFoundError:
        return
    try:
        try:
            hfd = brain_sync._open_dir(fd, STORE[0], False)
        except (FileNotFoundError, brain_sync.SyncError):
            return
        try:
            _remove_at(hfd, STORE[1])
        finally:
            os.close(hfd)
    finally:
        os.close(fd)
