"""Which files the code graph holds has one answer: parser.is_indexed_path.

Every path that writes the graph (the full rebuild, the incremental update) asks it;
the hand-copied extension and skip lists it replaced had drifted (a file watcher, since
deleted, missed .sql and `target/`; a commit hook re-indexed 5 languages and no test
files).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.code_intel import parser as P

BACKEND = Path(__file__).resolve().parents[1]

# The modules that decide what goes into the graph, beside parser.py itself.
_WRITERS = ["core/code_intel", "jobs/handlers/code_intel_reindex.py"]
# A suffix list there with another purpose, named with that purpose.
_OTHER_PURPOSE: set = set()


@pytest.mark.parametrize("rel,held", [
    ("svc/main.go", True), ("tests/test_x.py", True), ("src/A.tsx", True),
    ("db/pkg.pks", True), ("schema.sql", True),
    ("node_modules/x/i.js", False), ("crate/target/debug/b.rs", False),
    ("desktop/src-tauri/binaries/x/_internal/a.py", False),
    ("README.md", False), ("a/b.egg-info/x.py", False),
])
def test_is_indexed_path(rel, held) -> None:
    assert P.is_indexed_path(rel) is held


def test_sql_is_not_both_source_and_non_source() -> None:
    assert not set(P.LANGUAGE_MAP) & P._NON_SOURCE_EXTENSIONS


def _suffix_collections(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    names[id(node.value)] = t.id
    for node in ast.walk(tree):
        elts = (node.keys if isinstance(node, ast.Dict)
                else node.elts if isinstance(node, (ast.Set, ast.Tuple, ast.List)) else [])
        vals = {e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
        if {".py", ".ts"} <= vals:
            yield node.lineno, names.get(id(node), "")


def test_no_writer_keeps_its_own_list_of_code_files() -> None:
    bad = []
    for entry in _WRITERS:
        root = BACKEND / entry
        for path in ([root] if root.is_file() else sorted(root.glob("*.py"))):
            rel = path.relative_to(BACKEND).as_posix()
            if rel == "core/code_intel/parser.py":
                continue
            for line, name in _suffix_collections(path):
                if (rel, name) not in _OTHER_PURPOSE:
                    bad.append(f"{rel}:{line} {name or '<literal>'}")
    assert bad == [], "derive from parser.LANGUAGE_MAP / is_indexed_path: " + "; ".join(bad)


def test_the_scan_sees_a_tuple_a_set_and_a_dict(tmp_path) -> None:
    f = tmp_path / "m.py"
    f.write_text('A = (".py", ".ts")\nB = {".py", ".ts"}\nC = {".py": 1, ".ts": 2}\n')
    assert [n for _, n in _suffix_collections(f)] == ["A", "B", "C"]
