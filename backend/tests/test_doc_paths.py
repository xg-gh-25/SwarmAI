"""Tests for core.doc_paths.is_doc_path — the one documentation-path definition
(design 2026-11-01-pipeline-delivery-friction D5).

Table-driven: ``*.md`` / ``*.rst`` anywhere are documentation; ``*.txt`` (requirements,
CMakeLists), code and config wherever they live, lockfiles, and absolute / drive-
qualified / traversing paths are not. A structural test keeps the pipeline on this one
definition.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.doc_paths import is_doc_path


@pytest.mark.parametrize("rel", [
    "README.md", "docs/design/x.md", ".kiro/steering/s.md", "a/b/C.MD", "./x.md",
    "backend\\skills\\s\\SKILL.md", "backend/context/AGENT.md", "docs/index.rst",
])
def test_doc(rel):
    assert is_doc_path(rel) is True


@pytest.mark.parametrize("rel", [
    "", "a.py", "backend/core/x.py", ".claude/skills/s/run.py", "scripts/x.sh",
    "package-lock.json", "uv.lock", "requirements.txt", "CMakeLists.txt", "docs/notes.txt",
    "foo.md/evil.py", "backend/context/x.py", "backend/context/EVOLUTION_CHANGELOG.jsonl",
    "../x.md", "/abs/x.md", "C:\\tmp\\x.md", "C:x.md", "\\\\host\\share\\x.md", "md",
    "x.markdown",
])
def test_not_doc(rel):
    assert is_doc_path(rel) is False


def test_the_pipeline_has_no_other_doc_suffix_list():
    root = Path(__file__).resolve().parents[1] / "core" / "run_protocol"
    offenders = []
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "_DOC_SUFFIXES" in text or re.search(r"""["']\.md["']\s*,\s*["']\.txt["']""", text):
            offenders.append(path.name)
    assert offenders == []


def test_a_requirements_txt_change_is_reviewed_as_source():
    from core.run_protocol import review
    picked = review.reviewers(profile="full", changed=[
        {"path": "requirements.txt", "insertions": 60, "deletions": 0}])
    assert [row["id"] for row in picked] == ["correctness"]
    assert review.reviewers(profile="full", changed=[
        {"path": "docs/x.md", "insertions": 60, "deletions": 0}]) == []
