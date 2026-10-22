"""Tests for core.doc_paths.is_doc_only_path — the one pure-doc definition.

Table-driven: what counts as a pure-doc path (``*.md`` anywhere, any file under
``backend/context/``) and every input that must not (code beside docs, lockfiles,
absolute / drive-qualified / traversing paths, a ``.md`` directory holding code).
"""
from __future__ import annotations

import pytest

from core.doc_paths import is_doc_only_path


@pytest.mark.parametrize("rel", [
    "README.md", "docs/design/x.md", ".kiro/steering/s.md", "a/b/C.MD", "./x.md",
    "backend\\skills\\s\\SKILL.md", "backend/context/AGENT.md",
    "backend/context/EVOLUTION_CHANGELOG.jsonl", "./backend/context/x.json",
])
def test_pure_doc(rel):
    assert is_doc_only_path(rel) is True


@pytest.mark.parametrize("rel", [
    "", "a.py", "backend/core/x.py", ".claude/skills/s/run.py", "scripts/x.sh",
    "package-lock.json", "uv.lock", "docs/notes.txt", "foo.md/evil.py",
    "backend/context/../core/x.py", "../x.md", "/abs/x.md", "C:\\tmp\\x.md", "C:x.md",
    "\\\\host\\share\\x.md", "backend/contexts/x.json", "md", "x.markdown",
])
def test_not_pure_doc(rel):
    assert is_doc_only_path(rel) is False
