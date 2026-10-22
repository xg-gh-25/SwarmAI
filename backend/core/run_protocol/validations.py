"""Base-pinned validation manifest for Pipeline v1 TEST finalization.

Public symbols:
- ``load_manifest`` — read and strictly validate ``validations.json`` at a git base.
- ``required_for`` — select required validation entries from changed paths.
- ``manifest_digest`` — identity included in final and cache provenance.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping, Sequence

CALL_TYPES = frozenset({"build", "lint", "typecheck", "security_scan"})


class ValidationError(ValueError):
    """Validation manifest is missing, malformed, unsafe, or cannot be read."""


def _match(path: str, pattern: str) -> bool:
    item = PurePosixPath(path)
    if item.match(pattern):
        return True
    # pathlib treats ``a/**/*.py`` as requiring one nested directory; the protocol
    # means zero-or-more directories, so also test the zero-directory spelling.
    return "/**/" in pattern and item.match(pattern.replace("/**/", "/"))


def validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema") != 1:
        raise ValidationError("validations.json must be a schema-1 object")
    entries = value.get("entries")
    if not isinstance(entries, list):
        # an empty list is a repo that requires no validation (fixing log G-2)
        raise ValidationError("validations.json entries must be a list")
    ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(entries):
        if not isinstance(raw, Mapping) or set(raw) != {"id", "paths", "cwd", "argv", "call_type"}:
            raise ValidationError(f"entry {index} has wrong fields")
        entry_id = raw["id"]
        if not isinstance(entry_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", entry_id):
            raise ValidationError(f"entry {index} has invalid id")
        if entry_id in ids:
            raise ValidationError(f"duplicate validation id {entry_id}")
        ids.add(entry_id)
        paths, argv, cwd, call_type = raw["paths"], raw["argv"], raw["cwd"], raw["call_type"]
        if not isinstance(paths, list) or not paths or any(
            not isinstance(path, str) or path.startswith(("/", ":")) or ".." in PurePosixPath(path).parts
            for path in paths
        ):
            raise ValidationError(f"entry {entry_id} has unsafe paths")
        if not isinstance(argv, list) or not argv or any(
            not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv
        ):
            raise ValidationError(f"entry {entry_id} has invalid argv")
        if not isinstance(cwd, str) or cwd.startswith("/") or ".." in PurePosixPath(cwd).parts:
            raise ValidationError(f"entry {entry_id} has unsafe cwd")
        if call_type not in CALL_TYPES:
            raise ValidationError(f"entry {entry_id} has invalid call_type")
        normalized.append({"id": entry_id, "paths": list(paths), "cwd": cwd,
                           "argv": list(argv), "call_type": call_type})
    return {"schema": 1, "entries": normalized}


def load_manifest(repo: str, base: str, *, read_git: Callable[[str, str], bytes] | None = None) -> dict[str, Any]:
    """Read ``validations.json`` from *base*, never from the run's modified tree."""
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValidationError("base must be a full git commit id")
    if read_git is None:
        def read_git(root: str, spec: str) -> bytes:
            rev, _, path = spec.partition(":")
            try:
                listed = subprocess.run(
                    ["git", "-C", root, "ls-tree", "--name-only", rev, "--", path],
                    check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValidationError(f"cannot read base validation manifest: {exc}") from exc
            if not listed:   # the base has no manifest: it requires nothing (G-2)
                return b'{"schema": 1, "entries": []}'
            try:
                return subprocess.run(
                    ["git", "-C", root, "show", spec], check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                ).stdout
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValidationError(f"cannot read base validation manifest: {exc}") from exc
    raw = read_git(repo, f"{base}:validations.json")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid base validation manifest: {exc}") from exc
    return validate_manifest(value)


def required_for(manifest: Mapping[str, Any], changed_paths: Sequence[str]) -> list[dict[str, Any]]:
    """Select each base entry whose path patterns match at least one changed path."""
    value = validate_manifest(manifest)
    paths = tuple(dict.fromkeys(changed_paths))
    if any(not isinstance(path, str) or path.startswith("/") or ".." in PurePosixPath(path).parts
           for path in paths):
        raise ValidationError("changed path leaves the repository")
    return [entry for entry in value["entries"]
            if any(_match(path, pattern) for path in paths for pattern in entry["paths"])]


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    value = validate_manifest(manifest)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
