"""Governed corpus path classification and journal-authorized materialization.

- ``is_governed`` classifies resolved live corpus paths.
- ``get_materializer`` returns the sole atomic filesystem mutation API.
- ``iter_governed_files`` enumerates the current governed corpus.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from config import get_swarmws
from core.context_directory_loader import CONTEXT_FILES
from core.ddd_paths import UNDERSTANDING_DIR, ddd_path
from core.project_registry import DDD_CANONICAL_DOCS

_CONTEXT_NAMES = frozenset(
    spec.filename
    for spec in CONTEXT_FILES
    if spec.filename in {"MEMORY.md", "EVOLUTION.md", "STEERING.md", "USER.md", "TOOLS.md", "TIMELINE.md"}
)
_KNOWLEDGE_ROOTS = ("Notes", "Library", "Learned", "Inbox")


def _resolved(path: Path | str) -> Path:
    """Resolve symlinks and ``..`` even when the final path does not exist."""
    return Path(path).expanduser().resolve(strict=False)


def _relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _workspace_root() -> Path:
    return _resolved(get_swarmws())


def is_governed(path: Path | str) -> bool:
    """Return whether *path* resolves to a live governed corpus file.

    Product templates are deliberately outside the live workspace and therefore
    never governed.  A symlink is classified by its resolved target, not by the
    spelling of the link.
    """
    candidate = _resolved(path)
    root = _workspace_root()
    context = root / ".context"
    if candidate.parent == context and candidate.name in _CONTEXT_NAMES:
        return True

    knowledge = root / "Knowledge"
    for name in _KNOWLEDGE_ROOTS:
        store = knowledge / name
        if candidate != store and _relative_to(candidate, store):
            # the corpus is text units: an image or a mounted database beside them is not
            return candidate.suffix.lower() == ".md"

    projects = root / "Projects"
    if not _relative_to(candidate, projects):
        return False
    rel = candidate.relative_to(projects)
    if len(rel.parts) < 2:
        return False
    project = projects / rel.parts[0]
    for name in DDD_CANONICAL_DOCS:
        if candidate == _resolved(ddd_path(project, name)):
            return True
        if candidate == _resolved(project / UNDERSTANDING_DIR / name):
            return True
        if candidate == _resolved(project / name):
            return True
    return False


def iter_governed_files() -> Iterable[Path]:
    """Yield existing governed files under the active workspace, once each."""
    root = _workspace_root()
    candidates: list[Path] = [root / ".context" / name for name in sorted(_CONTEXT_NAMES)]
    projects = root / "Projects"
    if projects.is_dir():
        for project in sorted(projects.iterdir()):
            if not project.is_dir() or project.is_symlink():
                continue
            candidates.extend(ddd_path(project, name) for name in DDD_CANONICAL_DOCS)
    for store_name in _KNOWLEDGE_ROOTS:
        store = root / "Knowledge" / store_name
        if store.is_dir():
            candidates.extend(sorted(item for item in store.rglob("*") if item.is_file()))
    seen: set[Path] = set()
    for item in candidates:
        resolved = _resolved(item)
        if resolved not in seen and resolved.is_file() and is_governed(resolved):
            seen.add(resolved)
            yield resolved


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_directory(path: Path) -> None:
    """Best-effort directory fsync on platforms that permit directory handles."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _plan(op_id: str) -> dict[str, str | None] | None:
    """Read a registered immutable write plan without creating the database."""
    if not op_id:
        return None
    try:
        from .api import connect_ro

        conn = connect_ro()
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    try:
        row = conn.execute(
            "SELECT op_id,target_path,archive_path FROM ingestion_writes WHERE op_id=?",
            (op_id,),
        ).fetchone()
        if row is None:
            return None
        keys = ("op_id", "target_path", "archive_path")
        return {key: row[index] for index, key in enumerate(keys)}
    finally:
        conn.close()


def _plan_paths(plan: dict[str, str | None]) -> set[Path]:
    root = _workspace_root()
    paths: set[Path] = set()
    for key in ("target_path", "archive_path"):
        value = plan.get(key)
        if value:
            item = Path(value)
            paths.add(_resolved(item if item.is_absolute() else root / item))
    return paths


def _archive_destination(path: Path, op_id: str, plan: dict[str, str | None] | None) -> Path:
    if plan and plan.get("archive_path"):
        configured = Path(str(plan["archive_path"]))
        return _resolved(configured if configured.is_absolute() else _workspace_root() / configured)
    data = path.read_bytes() if path.exists() else b""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return path.parent / ".archive" / f"{path.name}.{stamp}.{_sha256(data)[:12]}"


@dataclass(frozen=True)
class _Materializer:
    """Atomic implementation of the journal-authorized materializer protocol."""

    def _authorize(self, op_id: str, *paths: Path) -> dict[str, str | None] | None:
        if not op_id:
            raise PermissionError("materializer requires op_id")
        resolved = {_resolved(path) for path in paths}
        if not any(is_governed(path) for path in resolved):
            return _plan(op_id)
        plan = _plan(op_id)
        if plan is None:
            raise PermissionError(f"unknown governed write op_id: {op_id}")
        allowed = _plan_paths(plan)
        if allowed and not resolved <= allowed:
            raise PermissionError("governed write path is outside the registered plan")
        return plan

    def write_bytes(self, path: Path, data: bytes, *, op_id: str) -> str:
        target = _resolved(path)
        self._authorize(op_id, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = stat.S_IMODE(target.stat().st_mode)
        except FileNotFoundError:
            mode = 0o644
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        temp_path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_path, mode)
            os.replace(temp_path, target)
            _fsync_directory(target.parent)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise
        return _sha256(data)

    def archive(self, path: Path, *, op_id: str) -> tuple[Path, str]:
        source = _resolved(path)
        plan = self._authorize(op_id, source)
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = _archive_destination(source, op_id, plan)
        data = source.read_bytes()
        self._authorize(op_id, source, destination)
        self.write_bytes(destination, data, op_id=op_id)
        return destination, _sha256(data)

    def unlink(self, path: Path, *, op_id: str) -> None:
        target = _resolved(path)
        self._authorize(op_id, target)
        target.unlink(missing_ok=True)
        _fsync_directory(target.parent)

    def move(self, src: Path, dst: Path, *, op_id: str) -> None:
        source, destination = _resolved(src), _resolved(dst)
        self._authorize(op_id, source, destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        _fsync_directory(source.parent)
        if destination.parent != source.parent:
            _fsync_directory(destination.parent)

    def copy(self, src: Path, dst: Path, *, op_id: str) -> None:
        source, destination = _resolved(src), _resolved(dst)
        self._authorize(op_id, source, destination)
        data = source.read_bytes()
        self.write_bytes(destination, data, op_id=op_id)
        shutil.copystat(source, destination, follow_symlinks=False)
        _fsync_directory(destination.parent)


_MATERIALIZER = _Materializer()


def get_materializer() -> _Materializer:
    """Return the process-wide stateless materializer."""
    return _MATERIALIZER
