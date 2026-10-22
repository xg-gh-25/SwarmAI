"""The one definition of a pure-documentation path.

Used by every gate that lets a documentation change skip a pipeline run — the
worktree write gate's rule 4 (a write to a main checkout) and direct_commit_gate (a
commit from a session with no run). Both call ``is_doc_only_path``; neither keeps a
rule of its own (execution plan v2 §2, XG 2026-10-22).

Public symbols:
- ``is_doc_only_path(rel)`` — True for a ``*.md`` file or any file under ``backend/context/``.
"""
from __future__ import annotations

import ntpath

_CONTEXT_DIR = "backend/context/"


def is_doc_only_path(rel: str) -> bool:
    """*rel* is repo-root-relative. A ``.md`` file anywhere (the suffix is
    case-insensitive), or any file under ``backend/context/``. Nothing else: not
    ``.py`` beside a skill, not ``scripts/``, not a lockfile."""
    if not isinstance(rel, str) or not rel:
        return False
    if ntpath.splitdrive(rel)[0]:          # C:\x.md, C:x.md, \\host\share: not repo-relative
        return False
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if rel.startswith("/") or any(part == ".." for part in rel.split("/")):
        return False
    return rel.lower().endswith(".md") or rel.startswith(_CONTEXT_DIR)
