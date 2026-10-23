"""Content-addressed immutable cache primitives for Pipeline v1.

Public symbols:
- ``cache_key`` — hash common and kind-specific immutable inputs.
- ``put_once`` — atomic create; conflicting values poison the key fail closed.
- ``read`` — return only an unambiguous cached value.
- ``conflict_hashes`` — read the immutable first/new hashes of a poisoned key.
- ``value_hash`` — canonical value identity used by cache integrity events.
- ``pipeline_code_hash`` — one hash over the code that decides a v1 result:
  ``core/run_protocol/``, ``scripts/pipeline_test.py``, ``scripts/pipeline_v1.py`` (B7).
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Mapping


class CacheError(RuntimeError):
    """Cache identity is incomplete, corrupt, or has conflicting values."""


_REQUIRED = {"kind", "repo", "tree", "pipeline_code_hash", "protocol_version", "rules_version"}


_BACKEND = Path(__file__).resolve().parents[2]
# backend-relative: the code that decides a v1 result. One list: the pipeline code
# hash covers it, and QX-2 (facts.RULE_PATHS) refuses a run that changes it.
PIPELINE_CODE: tuple[str, ...] = (
    "core/run_protocol/", "scripts/pipeline_test.py", "scripts/pipeline_v1.py",
    # outside the package but on the same trust path: worktree/base, the evidence
    # seal, the person's answer, and the gate that issues authorizations
    "core/run_worktree.py", "core/evidence_seal.py", "core/pipeline_human.py",
    "core/run_owner_gate.py")


def pipeline_code_hash(backend: str | Path | None = None) -> str:
    return _pipeline_code_hash(str(Path(backend) if backend is not None else _BACKEND))


@functools.lru_cache(maxsize=4)
def _pipeline_code_hash(backend: str) -> str:
    """sha256 over every file of the v1 protocol and both CLIs, path-tagged, in order:
    a change to any of them is a new identity for caches, finals and the contract."""
    root = Path(backend)
    files: list[Path] = []
    for entry in PIPELINE_CODE:
        files += sorted((root / entry).rglob("*.py")) if entry.endswith("/") else [root / entry]
    digest = hashlib.sha256()
    for path in files:
        if "__pycache__" in path.parts or not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def cache_key(inputs: Mapping[str, Any]) -> str:
    if not isinstance(inputs, Mapping) or not _REQUIRED <= set(inputs):
        raise CacheError(f"cache key lacks {sorted(_REQUIRED - set(inputs or {}))}")
    if any(inputs.get(key) in (None, "", [], {}) for key in _REQUIRED):
        raise CacheError("cache identity fields must be non-empty")
    try:
        raw = json.dumps(dict(inputs), sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CacheError(f"cache inputs are not canonical JSON: {exc}") from exc
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CacheError(f"cache value is not canonical JSON: {exc}") from exc


def value_hash(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _safe(kind: str, key: str) -> None:
    if not kind or "/" in kind or "\\" in kind \
            or len(key) != 64 or any(char not in "0123456789abcdef" for char in key):
        raise CacheError("unsafe cache kind/key")


def _fsync_dir(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_poison(path: Path, first_hash: str, new_hash: str) -> None:
    value = {"first_hash": first_hash, "new_hash": new_hash}
    data = _bytes(value)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with open(temp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
            _fsync_dir(path.parent)
        except FileExistsError:
            pass
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def put_once(root: str | Path, kind: str, key: str, value: Any) -> str:
    """Return created/replayed/conflict; a conflict permanently poisons the key."""
    _safe(kind, key)
    directory = Path(root) / kind
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / key
    poison = directory / f"{key}.conflict"
    data = _bytes(value)
    if poison.exists():
        return "conflict"
    temp = directory / f".{key}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
            _fsync_dir(directory)
            return "created"
        except FileExistsError:
            try:
                current = path.read_bytes()
            except OSError as exc:
                raise CacheError(f"cache value unreadable: {exc}") from exc
            if current == data:
                return "replayed"
            _write_poison(poison, hashlib.sha256(current).hexdigest(),
                          hashlib.sha256(data).hexdigest())
            return "conflict"
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def conflict_hashes(root: str | Path, kind: str, key: str) -> tuple[str, str] | None:
    _safe(kind, key)
    path = Path(root) / kind / f"{key}.conflict"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheError(f"cache poison marker is corrupt: {exc}") from exc
    if not isinstance(value, Mapping) or not all(
        isinstance(value.get(name), str) and len(value[name]) == 64
        and all(char in "0123456789abcdef" for char in value[name])
        for name in ("first_hash", "new_hash")
    ):
        raise CacheError("cache poison marker is corrupt")
    return str(value["first_hash"]), str(value["new_hash"])


def read(root: str | Path, kind: str, key: str) -> Any | None:
    _safe(kind, key)
    directory = Path(root) / kind
    if (directory / f"{key}.conflict").exists():
        raise CacheError("cache key is poisoned by conflicting immutable values")
    path = directory / key
    if not path.exists():
        return None
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise CacheError(f"cache value is corrupt: {exc}") from exc
