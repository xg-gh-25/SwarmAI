"""The one definition of a pure-documentation path.

Used by the pipeline's checks (``run_protocol/checks.py``: a docs-only change). The two
gates that also used it (worktree rule 4 and direct_commit_gate) were removed by design
2026-10-24-swarmai-usable-by-anyone §3.4.

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
