"""Which files the code graph holds has one answer: parser.is_indexed_path.

Every path that writes the graph (full rebuild, watcher, the commit hook) asks it;
the hand-copied extension and skip lists it replaced had drifted (the watcher missed
.sql and `target/`, the commit hook re-indexed 5 languages and no test files).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.code_intel import parser as P

BACKEND = Path(__file__).resolve().parents[1]

# The modules that decide what goes into the graph, beside parser.py itself.
_WRITERS = ["core/code_intel", "hooks/code_change_feed.py",
            "jobs/handlers/code_intel_reindex.py", "hooks/context_health_hook.py"]
# A suffix list there with another purpose, named with that purpose.
_OTHER_PURPOSE = {("hooks/code_change_feed.py", "_ARCH_CHANGE_EXTENSIONS")}


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


class TestWatcherFilter:
    def _watcher(self, root: Path):
        from core.code_intel.watcher import CodeIntelWatcher
        return CodeIntelWatcher("P", root, object())

    def test_a_repo_under_a_swarm_ai_dir_is_watched(self, tmp_path) -> None:
        """Bound repos are cloned under ~/.swarm-ai/bindings/: the skip segments match
        inside the repo only, never on the absolute path."""
        root = tmp_path / ".swarm-ai" / "bindings" / "repo"
        root.mkdir(parents=True)
        w = self._watcher(root)
        assert w._filter(None, str(root / "src" / "a.go")) is True
        assert w._filter(None, str(root / "Knowledge" / "a.py")) is False
        assert w._filter(None, str(root / "node_modules" / "a.js")) is False
        assert w._filter(None, str(root / "a.md")) is False
        assert w._filter(None, str(root / "q.sql")) is True
        assert w._filter(None, str(tmp_path / "elsewhere.py")) is False
