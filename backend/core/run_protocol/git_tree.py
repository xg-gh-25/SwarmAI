"""Real git adapters for Pipeline v1 code_tree and delivery_tree.

Public symbols:
- ``write_tree`` — full tracked/untracked worktree via private temporary index.
- ``list_reserved`` — list every existing item below a delivery-reserved prefix.
- ``diff_trees`` — exact status/path/content digest for LG-3.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence


from core.run_protocol.infra import InfrastructureError


class GitTreeError(RuntimeError):
    """A git tree operation failed or received an unsafe path."""


class GitObjectWriteError(GitTreeError, InfrastructureError):
    """git could not write objects (``add`` / ``hash-object`` / ``write-tree``): the
    run's infrastructure failed (conformance F11)."""

    component = "git_objects"


_OBJECT_WRITERS = frozenset({"add", "hash-object", "write-tree"})


def _run(root: str | Path, *args: str, env: Mapping[str, str] | None = None,
         input: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], input=input, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=dict(env) if env is not None else None,
            timeout=120, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        message = f"git {' '.join(args[:2])} failed: {exc}"
        if args and args[0] in _OBJECT_WRITERS:
            raise GitObjectWriteError(message) from exc
        raise GitTreeError(message) from exc
    return result.stdout


def _safe(path: str, *, prefix: bool = False) -> str:
    if not isinstance(path, str) or not path or path.startswith(("/", ":")) \
            or "\x00" in path or ".." in PurePosixPath(path).parts:
        raise GitTreeError(f"unsafe repo-relative path {path!r}")
    value = PurePosixPath(path).as_posix()
    return value.rstrip("/") + "/" if prefix else value


def list_reserved(root: str, reserved: str) -> list[str]:
    prefix = _safe(reserved, prefix=True)
    directory = Path(root) / prefix
    if not directory.exists():
        return []
    if directory.is_symlink():
        return [prefix.rstrip("/")]
    return sorted(path.relative_to(root).as_posix() for path in directory.rglob("*")
                  if path.exists() or path.is_symlink())


def write_tree(root: str, excludes: tuple[str, ...], overlays: Mapping[str, bytes]) -> str:
    """Write a full worktree tree without mutating its real index or files."""
    root_path = Path(root).resolve()
    if not (root_path / ".git").exists():
        # linked worktrees use a .git file, so existence (not is_dir) is intentional.
        raise GitTreeError("root is not a git worktree")
    prefixes = tuple(_safe(path, prefix=True) for path in excludes)
    overlay = {_safe(path): value for path, value in overlays.items()}
    if any(not isinstance(value, bytes) for value in overlay.values()):
        raise GitTreeError("tree overlays must be bytes")
    fd, index = tempfile.mkstemp(prefix="pipeline-v1-index-")
    os.close(fd)
    os.unlink(index)  # git read-tree creates a valid index; an empty file is invalid.
    env = {**os.environ, "GIT_INDEX_FILE": index}
    try:
        _run(root_path, "read-tree", "HEAD", env=env)
        _run(root_path, "add", "-A", "--", ".", env=env)
        for prefix in prefixes:
            _run(root_path, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--",
                 prefix.rstrip("/"), env=env)
        for path, data in overlay.items():
            if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes):
                blob = _run(root_path, "hash-object", "-w", "--stdin", input=data).decode().strip()
                _run(root_path, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            else:
                raise GitTreeError("overlay must be inside an excluded reserved prefix")
        tree = _run(root_path, "write-tree", env=env).decode().strip()
        if len(tree) != 40:
            raise GitTreeError("git returned an invalid tree id")
        return tree
    finally:
        try:
            os.unlink(index)
        except FileNotFoundError:
            pass


def commit_code_tree(root: str, commit: str, excludes: tuple[str, ...]) -> str:
    """The code tree of a landed *commit*: its tree with the reserved prefixes removed,
    by the same rule as ``write_tree`` (a landed run's current code is its commit)."""
    prefixes = tuple(_safe(path, prefix=True) for path in excludes)
    fd, index = tempfile.mkstemp(prefix="pipeline-v1-index-")
    os.close(fd)
    os.unlink(index)
    env = {**os.environ, "GIT_INDEX_FILE": index}
    try:
        _run(root, "read-tree", f"{commit}^{{tree}}", env=env)
        for prefix in prefixes:
            _run(root, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--",
                 prefix.rstrip("/"), env=env)
        tree = _run(root, "write-tree", env=env).decode().strip()
        if len(tree) != 40:
            raise GitTreeError("git returned an invalid tree id")
        return tree
    finally:
        try:
            os.unlink(index)
        except FileNotFoundError:
            pass


def diff_trees(root: str, before: str, after: str) -> list[dict[str, str]]:
    raw = _run(root, "diff-tree", "--no-commit-id", "--name-status", "-r", "-z",
               before, after).decode("utf-8", "strict")
    tokens = raw.split("\0")
    out: list[dict[str, str]] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index]; index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(tokens):
                raise GitTreeError("truncated rename/copy tree diff")
            old, path = tokens[index], tokens[index + 1]; index += 2
            out.append({"status": status, "old_path": old, "path": path})
            continue
        if index >= len(tokens):
            raise GitTreeError("truncated tree diff")
        path = tokens[index]; index += 1
        row = {"status": status, "path": path}
        if status == "A":
            data = _run(root, "cat-file", "blob", f"{after}:{path}")
            row["blob_sha256"] = hashlib.sha256(data).hexdigest()
        out.append(row)
    return out
