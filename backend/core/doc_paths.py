"""The one definition of a documentation path.

A documentation file is prose a person reads: ``*.md`` or ``*.rst`` (the suffix is
case-insensitive). Nothing else, wherever it lives: ``*.txt`` is not one
(``requirements.txt`` and ``CMakeLists.txt`` change behaviour), and neither is any
directory a repository may use for prompts or config. Used by the pipeline engine and
review (``run_protocol/engine.py``, ``run_protocol/review.py``): which changed files an
incremental review or a correctness dispatch may leave out, and which change only
documentation (design 2026-11-01-pipeline-delivery-friction D5).

It also holds the one definition of a test path (``is_test_path``): a file in a test
directory or named the way test runners find tests, in any language. The review roster
leaves tests out of the source it counts, and a pipeline proof's mutation may not take
a test out instead of the fix (``run_protocol/engine.py``).

Public symbols:
- ``DOC_SUFFIXES`` — the documentation suffixes, lower case.
- ``is_doc_path(rel)`` — True for a repo-relative path to a documentation file.
- ``TEST_DIRS`` — directory names whose files are tests.
- ``is_test_path(rel)`` — True for a repo-relative path to a test file.
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


#: a file under one of these directories is a test (test runners' common layouts)
TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "specs"})


def is_test_path(rel: str) -> bool:
    """*rel* is repo-root-relative. True for a file in a ``TEST_DIRS`` directory, or
    named as test runners find tests: ``test_*``, ``*_test.*``, ``*_spec.*``,
    ``*.test.*``, ``*.spec.*`` (any case). Not a language rule: these names mean a
    test in every ecosystem that uses them."""
    if not isinstance(rel, str) or not rel:
        return False
    parts = [part for part in rel.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts:
        return False
    if any(part.lower() in TEST_DIRS for part in parts[:-1]):
        return True
    name = parts[-1].lower()
    stem = name.split(".", 1)[0]
    return (name.startswith("test_") or stem.endswith(("_test", "_spec"))
            or ".test." in name or ".spec." in name)
