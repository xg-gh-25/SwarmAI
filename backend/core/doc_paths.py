"""The one definition of a documentation path.

A documentation file is prose a person reads: ``*.md`` or ``*.rst`` (the suffix is
case-insensitive). Nothing else, wherever it lives: ``*.txt`` is not one
(``requirements.txt`` and ``CMakeLists.txt`` change behaviour), and neither is any
directory a repository may use for prompts or config. Used by the pipeline engine and
review (``run_protocol/engine.py``, ``run_protocol/review.py``): which changed files an
incremental review or a correctness dispatch may leave out, and which change only
documentation (design 2026-11-01-pipeline-delivery-friction D5).

Public symbols:
- ``DOC_SUFFIXES`` — the documentation suffixes, lower case.
- ``is_doc_path(rel)`` — True for a repo-relative path to a documentation file.
"""
from __future__ import annotations

import ntpath

DOC_SUFFIXES = (".md", ".rst")


def is_doc_path(rel: str) -> bool:
    """*rel* is repo-root-relative. True for a ``.md`` / ``.rst`` file (any case).
    Never for an absolute, drive-qualified or ``..`` path, or an empty one."""
    if not isinstance(rel, str) or not rel:
        return False
    if ntpath.splitdrive(rel)[0]:          # C:\x.md, C:x.md, \\host\share: not repo-relative
        return False
    rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    if rel.startswith("/") or any(part == ".." for part in rel.split("/")):
        return False
    return rel.lower().endswith(DOC_SUFFIXES)
