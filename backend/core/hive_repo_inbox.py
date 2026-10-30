"""Where a Hive keeps the host's commit of a dispatched task's repository (design
docs/design/2026-10-30-hive-task-repos-from-host.md §3).

The host sends a git bundle of its commit (core/repo_ship); this daemon only stores it,
as ``Hive/incoming/<sha>.bundle`` in the workspace, and the Hive's agent fetches it into
the repository itself (the task text says how, core/task_repos.task_text). The daemon
never runs git and never reads what the agent wrote: the agent's git runs as the agent,
under the agent's own gates, so anything planted in a repository's ``.git`` reaches no
one but the agent.

The file is written like a Brain's files (core/brain_sync): through directory
descriptors opened without following a link (a linked ``Hive/`` or ``incoming/`` is
refused), to a fresh random name created exclusively, then renamed into place. Bundles
older than ``KEEP_S`` are removed by name in that directory when a new one arrives.

- ``bundle_rel(sha)``                 — the workspace-relative path of a commit's bundle
- ``Inbox(workspace, sha)``           — ``write(chunk)``, then ``commit()``; ``abort()`` on failure
"""
from __future__ import annotations

import os
import re
import secrets
import stat
import time
from pathlib import Path

from core import brain_sync

#: the directory, relative to the workspace (the agent reads the bundle from there)
INCOMING = ("Hive", "incoming")
#: a bundle older than this is removed when the next one arrives (the agent fetches it at
#: the start of its task)
KEEP_S = 2 * 24 * 3600
#: what a git bundle file starts with (v2 or v3); anything else is refused before it is kept
_HEADERS = (b"# v2 git bundle\n", b"# v3 git bundle\n")
_SHA_RE = re.compile(r"[0-9a-f]{40,64}")


class InboxError(ValueError):
    """The bundle is not kept; ``status`` is the HTTP answer, the message is safe to return."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def bundle_rel(sha: str) -> str:
    if not _SHA_RE.fullmatch(sha or ""):
        raise InboxError("not a commit sha", 422)
    return "/".join((*INCOMING, f"{sha}.bundle"))


class Inbox:
    """One bundle being received. ``write`` the body in chunks, then ``commit`` (checks the
    header, renames into place); any failure: ``abort`` (removes the partial file)."""

    def __init__(self, workspace: Path, sha: str):
        self.name = bundle_rel(sha).rsplit("/", 1)[1]
        ws = Path(workspace)
        try:
            root = os.open(str(ws), brain_sync._DIR_FLAGS)
        except OSError as exc:
            raise InboxError(f"the workspace is not a plain directory ({exc.strerror})") from None
        try:
            self._dfd, _ = brain_sync._walk_to(root, "/".join((*INCOMING, self.name)), create=True)
        except brain_sync.SyncError as exc:
            raise InboxError(str(exc)) from None
        finally:
            os.close(root)
        self._tmp = f".{self.name}.{secrets.token_hex(8)}.tmp"
        try:
            self._fd = os.open(self._tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                               0o644, dir_fd=self._dfd)
        except OSError:
            os.close(self._dfd)
            raise InboxError("no file could be created for the bundle") from None
        self._head = b""

    def write(self, chunk: bytes) -> None:
        if len(self._head) < 16:
            self._head += chunk[:16 - len(self._head)]
        view = memoryview(chunk)
        while view:
            view = view[os.write(self._fd, view):]

    def commit(self) -> str:
        """Keep the bundle; returns its workspace-relative path."""
        try:
            if self._head not in _HEADERS:
                raise InboxError("the body is not a git bundle", 422)
            os.fsync(self._fd)
            os.close(self._fd)
            self._fd = -1
            os.replace(self._tmp, self.name, src_dir_fd=self._dfd, dst_dir_fd=self._dfd)
            self._tmp = None
            self._prune()
        except BaseException:
            self.abort()
            raise
        os.close(self._dfd)
        self._dfd = -1
        return "/".join((*INCOMING, self.name))

    def abort(self) -> None:
        if getattr(self, "_fd", -1) >= 0:
            os.close(self._fd)
            self._fd = -1
        if self._dfd >= 0:
            if self._tmp:
                try:
                    os.unlink(self._tmp, dir_fd=self._dfd)
                except FileNotFoundError:
                    pass
            os.close(self._dfd)
            self._dfd = -1

    def _prune(self) -> None:
        """Remove old bundles (regular files, by name, in this directory only)."""
        cutoff = time.time() - KEEP_S
        for entry in os.listdir(self._dfd):
            if entry == self.name or not entry.endswith(".bundle"):
                continue
            try:
                st = os.stat(entry, dir_fd=self._dfd, follow_symlinks=False)
                if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                    os.unlink(entry, dir_fd=self._dfd)
            except OSError:
                continue
