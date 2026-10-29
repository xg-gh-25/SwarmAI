"""Brain sync to a Hive (design docs/design/2026-10-28-project-agent-flow.md §6).

The host is the only writer; a Hive's copy of a Brain is a cache of one version. One
module holds both sides so they share one whitelist and one digest:

- **What travels** (``whitelisted``): a Brain's ``AGENTS.md``, ``SYSTEM_PROMPT.md``,
  ``CONTEXT.md``, ``REFRESHER.md``, ``project-agent.yaml``, ``.project-agent-check.json``,
  ``bindings.yaml``, ``aim.json``, ``.project.json``; ``2-understanding/**/*.md`` (the
  ``*-archive*.md`` files included) and ``assets/data-source/data-contract/**/*.md`` -- every
  file ``core.addressing`` reads into a DDD's unit table, so ``knowledge find`` on the Hive
  sees the same units (and the same ``unit_id`` values) as on the host; every
  file of ``3-gates/`` and of each domain skill under ``4-capabilities/`` (byte caches
  and archives excluded). The General Brain is ``Knowledge/Library``: its ``**/*.md``
  plus its definition and source check. Private context, ``.artifacts/``, code graphs,
  binary assets and anything a secret-name rule matches never travel.
- **The digest** (``content_digest``): sha256 over the sorted (path, sha256(bytes))
  pairs, so the same content always has the same digest, however it is packed, and a
  Hive can recompute it from the files on its disk.
- **Host** (``collect`` → ``pack``): read the whitelisted files; a document whose
  ``.md.lock`` is held (being written) stops the package; ``bindings.yaml`` travels
  without its machine-local ``worktree`` paths. ``pack`` is a deterministic tar.gz.
- **Hive** (``unpack`` → ``install``): refuse a package over the size limits, with any
  member that is not a regular file, whose path leaves the whitelist, or
  whose digest is not the one sent. Install writes each file through directory file
  descriptors opened without following links (a symlink placed in the Brain cannot
  redirect a write or a delete), deletes whitelisted files the package no longer has,
  leaves everything else alone and records ``.brain.json``
  ``{digest, revision, installed_at, files: {path: sha256}}``.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import os
import re
import secrets
import stat
import tarfile
import time
import zlib
from pathlib import Path
from typing import Any, Optional

import yaml

from core.brain_bootstrap.repo_read import is_secret_path

logger = logging.getLogger(__name__)

MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 5000
_TAR_SLACK = MAX_MEMBERS * 1536        # tar headers and padding on top of the file bytes
BRAIN_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
STATE_FILE = ".brain.json"
TOP_FILES = frozenset({"AGENTS.md", "SYSTEM_PROMPT.md", "CONTEXT.md", "REFRESHER.md",
                       "project-agent.yaml", ".project-agent-check.json", "bindings.yaml",
                       "aim.json", ".project.json"})
GENERAL_FILES = frozenset({"project-agent.yaml", ".project-agent-check.json"})
_SKIP_PARTS = frozenset({"__pycache__", ".git", "node_modules", ".DS_Store"})
_SKIP_SUFFIXES = (".pyc", ".pyo", ".lock", ".tmp")


class SyncError(ValueError):
    """The package cannot be made or installed; ``status`` is the HTTP answer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _skipped(parts: list[str]) -> bool:
    name = parts[-1]
    return (any(p in _SKIP_PARTS for p in parts) or name.endswith(_SKIP_SUFFIXES)
            or name.startswith(".") and name not in (".project-agent-check.json", ".project.json"))


def whitelisted(rel: str, general: bool) -> bool:
    """True when *rel* (``/``-separated, relative to the Brain) travels to a Hive."""
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts) or _skipped(parts) or is_secret_path(rel):
        return False
    if general:
        return rel in GENERAL_FILES or rel.endswith(".md") and "-archive" not in parts[-1]
    if len(parts) == 1:
        return rel in TOP_FILES
    head = parts[0]
    if head == "2-understanding":
        return rel.endswith(".md")
    if head == "3-gates":
        return "-archive" not in parts[-1]
    if head == "assets":       # the data-source contract (ddd_paths.data_contract_dir)
        return parts[1:3] == ["data-source", "data-contract"] and len(parts) >= 4 and rel.endswith(".md")
    if head == "4-capabilities":
        from core.ddd_skill_registry import _is_enablement
        return len(parts) >= 3 and not _is_enablement(parts[1]) and "-archive" not in parts[-1]
    return False


def content_digest(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(files[rel]).hexdigest().encode() + b"\n")
    return h.hexdigest()


def _walk(root: Path, general: bool) -> list[str]:
    """Whitelisted regular files under *root* (never through a symlink), sorted."""
    out: list[str] = []
    if not root.is_dir() or root.is_symlink():
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_PARTS and not (base / d).is_symlink())
        for name in sorted(filenames):
            p = base / name
            rel = p.relative_to(root).as_posix()
            if not p.is_symlink() and p.is_file() and whitelisted(rel, general):
                out.append(rel)
    return sorted(out)


def _strip_worktrees(raw: bytes) -> bytes:
    """``bindings.yaml`` without the machine-local ``worktree`` of each binding (the
    Hive clones repositories under its own directory)."""
    try:
        doc = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError):
        raise SyncError("bindings.yaml cannot be read") from None
    if isinstance(doc, dict) and isinstance(doc.get("bindings"), list):
        for b in doc["bindings"]:
            if isinstance(b, dict):
                b.pop("worktree", None)
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True).encode("utf-8")


def _read_plain(p: Path, limit: int) -> bytes:
    """The bytes of *p*, opened without following a link and only if it is a regular
    file (a file swapped for a link after the walk is not followed)."""
    try:
        fd = os.open(str(p), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise SyncError(f"{p.name} cannot be read ({exc.strerror})", 409) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SyncError(f"{p.name} is not a regular file", 409)
        if st.st_size > limit:
            raise SyncError("the Brain is larger than a Hive accepts", 413)
        chunks, n = [], 0
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            n += len(b)
            if n > limit:
                raise SyncError("the Brain is larger than a Hive accepts", 413)
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def old_layout(root: Path) -> bool:
    """True when a canonical doc sits at the Brain's root instead of 2-understanding/
    (a Brain not yet migrated). The whitelist knows the numbered layout only, so such
    a Brain does not travel: its Hive copy would miss the docs."""
    from core.ddd_paths import UNDERSTANDING_DIR
    from core.project_registry import DDD_CANONICAL_DOCS
    return any((Path(root) / d).is_file() and not (Path(root) / UNDERSTANDING_DIR / d).is_file()
               for d in DDD_CANONICAL_DOCS)


def collect(root: Path, general: bool) -> dict[str, bytes]:
    """The files of the Brain at *root* that travel, as they travel (host side)."""
    from utils.file_lock import md_lock
    if not general and old_layout(root):
        raise SyncError("this Brain is in the old layout (docs at its root); migrate it to "
                        "2-understanding/ first", 409)
    files: dict[str, bytes] = {}
    total = 0
    for rel in _walk(Path(root), general):
        p = Path(root) / rel
        if rel.endswith(".md"):
            with md_lock(p, blocking=False) as got:
                if not got:
                    raise SyncError(f"{rel} is being written; sync again in a moment", 409)
                data = _read_plain(p, MAX_UNPACKED_BYTES)
        else:
            data = _read_plain(p, MAX_UNPACKED_BYTES)
        if rel == "bindings.yaml" and not general:
            data = _strip_worktrees(data)
        total += len(data)
        if total > MAX_UNPACKED_BYTES or len(files) >= MAX_MEMBERS:
            raise SyncError("the Brain is larger than a Hive accepts", 413)
        files[rel] = data
    return files


def installed_files(root: Path, general: bool) -> dict[str, bytes]:
    """The whitelisted files a Hive has for a Brain, as they are on its disk (a file
    that is not a regular file, or vanished, is left out: it counts as changed)."""
    out: dict[str, bytes] = {}
    for rel in _walk(Path(root), general):
        try:
            out[rel] = _read_plain(Path(root) / rel, MAX_UNPACKED_BYTES)
        except SyncError:
            continue
    return out


def pack(files: dict[str, bytes]) -> bytes:
    """A deterministic tar.gz of *files* (sorted, no times, owners or modes)."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for rel in sorted(files):
                info = tarfile.TarInfo(rel)
                info.size = len(files[rel])
                info.mtime = 0
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(files[rel]))
    return buf.getvalue()


def unpack(data: bytes, general: bool, digest: str) -> dict[str, bytes]:
    """The files of a package (Hive side), after every check; raises SyncError."""
    if len(data) > MAX_PACKAGE_BYTES:
        raise SyncError("the package is larger than 20 MB", 413)
    try:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = d.decompress(data, MAX_UNPACKED_BYTES + _TAR_SLACK)
        if d.unconsumed_tail or not d.eof:
            raise SyncError("the package unpacks to more than a Hive accepts", 413)
    except zlib.error:
        raise SyncError("the package is not a tar.gz") from None
    files: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
            for i, m in enumerate(tar):
                if i >= MAX_MEMBERS:
                    raise SyncError("the package has too many files", 413)
                rel = m.name               # whitelisted() refuses "", ".", "..", "/x", "a\\b"
                if not m.isreg() or not whitelisted(rel, general):
                    raise SyncError(f"the package has a member that is not accepted: {m.name[:120]!r}")
                total += m.size
                if total > MAX_UNPACKED_BYTES:
                    raise SyncError("the package unpacks to more than a Hive accepts", 413)
                f = tar.extractfile(m)
                body = f.read() if f is not None else b""
                if len(body) != m.size or rel in files:
                    raise SyncError(f"the package member {rel!r} is malformed")
                files[rel] = body
    except (tarfile.TarError, EOFError, OSError, gzip.BadGzipFile, zlib.error):
        raise SyncError("the package is not a tar.gz") from None
    if content_digest(files) != digest:
        raise SyncError("the package does not match its digest")
    return files


# ── Hive: write through directory descriptors that never follow a link ───────

_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_dir(parent_fd: int, name: str, create: bool) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        os.mkdir(name, 0o755, dir_fd=parent_fd)
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:      # ELOOP / ENOTDIR: a link or a file where a directory belongs
        raise SyncError(f"{name} in the Brain is not a plain directory ({exc.strerror})", 409) from None


def _walk_to(root_fd: int, rel: str, create: bool) -> tuple[int, str]:
    """(fd of the directory holding *rel*, its file name); the caller closes the fd."""
    parts = rel.split("/")
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            nxt = _open_dir(fd, part, create)
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd, parts[-1]


def _write(root_fd: int, rel: str, data: bytes) -> None:
    dfd, name = _walk_to(root_fd, rel, create=True)
    tmp = None
    try:
        try:
            st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                raise SyncError(f"{rel} is a directory on this Hive", 409)
        except FileNotFoundError:
            pass
        # a fresh random name, created exclusively: an entry planted under the name
        # (a link, a hard link to a file elsewhere) is never opened
        for _attempt in range(5):
            cand = f".{name}.sync-{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(cand, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                             0o644, dir_fd=dfd)
            except FileExistsError:
                continue
            tmp = cand
            break
        else:
            raise SyncError(f"no temporary name could be created for {rel}", 409)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except FileNotFoundError:
                pass
        os.close(dfd)


def _remove(root_fd: int, rel: str) -> None:
    try:
        dfd, name = _walk_to(root_fd, rel, create=False)
    except (FileNotFoundError, SyncError):
        return
    try:
        os.unlink(name, dir_fd=dfd)
    except FileNotFoundError:
        pass
    finally:
        os.close(dfd)


def brain_root(workspace: Path, brain: str) -> tuple[Path, bool]:
    """(the Brain's directory on this machine, is it the General Brain); refuses a bad
    name and a name that differs from an existing Brain only in case."""
    from core.project_agent import GENERAL_BRAIN, general_brain_dir
    if not isinstance(brain, str) or not BRAIN_NAME_RE.fullmatch(brain):
        raise SyncError(f"not a Brain name: {brain!r}")
    if brain == GENERAL_BRAIN:
        return general_brain_dir(workspace), True
    projects = Path(workspace) / "Projects"
    if projects.is_dir():
        for p in projects.iterdir():
            if p.name != brain and p.name.lower() == brain.lower():
                raise SyncError(f"{brain} differs from the Brain {p.name} only in case", 409)
    return projects / brain, False


def install(workspace: Path, brain: str, files: dict[str, bytes], digest: str) -> dict[str, Any]:
    """Install a checked package into this Hive's copy of *brain*; returns the
    ``.brain.json`` record. The caller holds the Brain's lock."""
    root, general = brain_root(workspace, brain)
    parent = root.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:                       # O_NOFOLLOW: a linked Projects/ or Brain dir is refused
        pfd = os.open(str(parent), _DIR_FLAGS)
    except OSError as exc:
        raise SyncError(f"the directory of {brain} is not a plain directory ({exc.strerror})", 409) from None
    try:
        root_fd = _open_dir(pfd, root.name, create=True)
    finally:
        os.close(pfd)
    try:
        # a failed install leaves no record (the Brain then reads "not synced" and is not
        # dispatchable), and a link or file where a directory belongs fails here, before
        # any file of the new version is written
        _remove(root_fd, STATE_FILE)
        for rel in sorted(files):
            dfd, _name = _walk_to(root_fd, rel, create=True)
            os.close(dfd)
        for rel in sorted(files):
            _write(root_fd, rel, files[rel])
        for rel in _walk(root, general):
            if rel not in files:
                _remove(root_fd, rel)
        from core.project_agent import revision
        record = {"digest": digest, "revision": revision(root),
                  "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "files": {rel: hashlib.sha256(files[rel]).hexdigest() for rel in sorted(files)}}
        _write(root_fd, STATE_FILE, json.dumps(record, indent=1).encode("utf-8"))
    finally:
        os.close(root_fd)
    _journal_install(root)
    return record


def _journal_install(root: Path) -> None:
    """Record the install as a sync in the CDF journal, so the baseline holds the
    installed bytes and the next CDF write to the Brain plans against them (a write
    the journal does not know reads as an unknown edit). Only governed files count,
    so an install outside the live workspace records nothing. The files are already
    installed: a failed record is
    logged, and CDF's own precondition still refuses to write over bytes it did not
    expect."""
    from core.app_role import is_worker
    if is_worker():
        return      # a worker Hive's CDF writes nothing, so nothing plans against the files
    from config import get_swarmws
    from core.ingestion.governed_edit import apply_sync, sync_baseline_revision, sync_revision_of
    from core.ingestion.materializer import iter_governed_files
    from core.ingestion.writer import ensure_baseline
    try:
        baseline = ensure_baseline()
        brain = root.resolve()
        live = Path(get_swarmws()).resolve()      # baseline names are relative to it
        # what the Brain has now, and what the baseline had (a file the package dropped)
        paths = {p.resolve() for p in iter_governed_files() if brain in p.resolve().parents}
        paths |= {live / name for name in baseline.get("files", {})
                  if brain in (live / name).parents}
        if not paths:
            return
        before = {str(p): sync_baseline_revision(p, baseline) for p in paths}
        after = {str(p): sync_revision_of(p) for p in paths}
        apply_sync(sorted(paths), before, after, "brain-sync")
    except Exception:  # noqa: BLE001 - logged, see docstring
        logger.exception("brain sync: recording the install of %s in the CDF journal failed", root.name)


def installed_state(workspace: Path, brain: str) -> Optional[dict[str, Any]]:
    """What this Hive has for *brain*: the recorded digest, revision and time, and
    how many whitelisted files differ from what was installed (changed, added or
    missing). None when it was never installed."""
    root, general = brain_root(workspace, brain)
    try:
        rec = json.loads(_read_plain(root / STATE_FILE, 4 << 20).decode("utf-8"))
    except (SyncError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    want = rec.get("files") if isinstance(rec.get("files"), dict) else {}
    on_disk = installed_files(root, general)
    have = {rel: hashlib.sha256(data).hexdigest() for rel, data in on_disk.items()}
    changed = sum(1 for rel in set(want) | set(have) if want.get(rel) != have.get(rel))
    return {"brain": brain, "digest": rec.get("digest"), "revision": rec.get("revision"),
            "installed_at": rec.get("installed_at"), "changed_files": changed,
            "current_digest": content_digest(on_disk)}
