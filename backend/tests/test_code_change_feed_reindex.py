"""Tests for CodeChangeFeed code_intel re-index extension.

Verifies that after detecting changed files, the hook also
re-indexes them into the code_intel graph if a project graph exists.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hooks.code_change_feed import _reindex_changed_files
from pathlib import Path as _P_ref
from core.code_intel import CodeIntelDb as _CIDb
_REF = _CIDb(db_path=_P_ref("code_intel.db"))  # a project db ref for loader mocks


class TestReindexChangedFiles:
    """P3: Git hook → code_intel graph auto-rebuild."""

    def test_skips_when_no_graph(self, tmp_path):
        """No-op when project has no code_intel.db."""
        changed_files = [("A", "backend/core/new_module.py")]
        # no graph → no db
        with patch("hooks.code_change_feed.load_project_graphs", return_value=[]):
            result = _reindex_changed_files(changed_files, str(tmp_path), "SwarmAI")
        assert result == 0

    def test_reindexes_python_files(self, tmp_path):
        """Changed .py files are re-stored in the real graph; a missing modified file is skipped."""
        from core.code_intel.graph_store import GraphStore

        src = tmp_path / "backend" / "core" / "foo.py"
        src.parent.mkdir(parents=True)
        src.write_text("def hello(): pass\n")
        graph = GraphStore(tmp_path / "g.db")
        graph.set_meta("repo_root", str(tmp_path))  # the graph is built from this repo
        try:
            changed_files = [("A", "backend/core/foo.py"), ("M", "backend/core/bar.py")]
            with patch("hooks.code_change_feed.load_project_graphs", return_value=[(_REF, graph)]):
                result = _reindex_changed_files(changed_files, str(tmp_path), "SwarmAI")
            assert result == 1
            ids = [n["id"] for n in graph.get_nodes_by_file("backend/core/foo.py")]
            assert "backend/core/foo.py::hello" in ids
        finally:
            graph.close()

    def test_reindex_replaces_removed_symbols(self, tmp_path):
        """A symbol removed from a file leaves the graph (the old path only ADDED rows)."""
        from core.code_intel.graph_store import GraphStore

        src = tmp_path / "pkg" / "mod.py"
        src.parent.mkdir(parents=True)
        src.write_text("def old(): pass\n")
        graph = GraphStore(tmp_path / "g.db")
        graph.set_meta("repo_root", str(tmp_path))  # the graph is built from this repo
        try:
            with patch("hooks.code_change_feed.load_project_graphs", return_value=[(_REF, graph)]):
                _reindex_changed_files([("A", "pkg/mod.py")], str(tmp_path), "SwarmAI")
                src.write_text("def new(): pass\n")
                _reindex_changed_files([("M", "pkg/mod.py")], str(tmp_path), "SwarmAI")
            ids = [n["id"] for n in graph.get_nodes_by_file("pkg/mod.py")]
            assert ids == ["pkg/mod.py::new"]
        finally:
            graph.close()

    def test_skips_non_code_files(self, tmp_path):
        """Only .py and .ts/.tsx files are re-indexed."""
        changed_files = [
            ("A", "README.md"),
            ("M", "config.yaml"),
            ("A", "assets/logo.png"),
        ]

        mock_graph = MagicMock()
        mock_graph.get_meta.return_value = str(tmp_path)  # built from this repo
        with patch("hooks.code_change_feed.load_project_graphs", return_value=[(_REF, mock_graph)]):
            result = _reindex_changed_files(changed_files, str(tmp_path), "SwarmAI")

        # No code files → the graph is never touched
        assert result == 0
        mock_graph.incremental_update.assert_not_called()

    def test_test_files_and_every_parsed_language_are_reindexed(self, tmp_path):
        """The graph holds test files and every LANGUAGE_MAP language (a full rebuild
        indexes them); the hook used to skip tests and all but 5 languages, so their
        nodes went stale after every edit."""
        from core.code_intel.graph_store import GraphStore

        files = {"tests/test_foo.py": "def test_x(): pass\n",
                 "svc/main.go": "package main\n\nfunc Serve() {}\n",
                 "node_modules/x/index.js": "function x() {}\n"}
        for rel, text in files.items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(text)
        graph = GraphStore(tmp_path / "g.db")
        graph.set_meta("repo_root", str(tmp_path))
        try:
            with patch("hooks.code_change_feed.load_project_graphs", return_value=[(_REF, graph)]):
                result = _reindex_changed_files([("A", r) for r in files], str(tmp_path), "SwarmAI")
            assert result == 2
            assert graph.get_nodes_by_file("tests/test_foo.py")
            assert graph.get_nodes_by_file("svc/main.go")
            assert graph.get_nodes_by_file("node_modules/x/index.js") == []
        finally:
            graph.close()

    def test_exception_does_not_propagate(self, tmp_path):
        """Re-index errors are swallowed (non-blocking)."""
        src = tmp_path / "backend" / "foo.py"
        src.parent.mkdir(parents=True)
        src.write_text("x = 1\n")

        changed_files = [("A", "backend/foo.py")]

        with patch("hooks.code_change_feed.load_project_graphs", side_effect=Exception("boom")):
            # Should NOT raise
            result = _reindex_changed_files(changed_files, str(tmp_path), "SwarmAI")
        assert result == 0

    def test_handles_deleted_files(self, tmp_path):
        """A deleted file (status D) loses its nodes — the old path ignored deletions."""
        from core.code_intel.graph_store import GraphStore

        src = tmp_path / "backend" / "old_module.py"
        src.parent.mkdir(parents=True)
        src.write_text("def gone(): pass\n")
        graph = GraphStore(tmp_path / "g.db")
        graph.set_meta("repo_root", str(tmp_path))  # the graph is built from this repo
        try:
            with patch("hooks.code_change_feed.load_project_graphs", return_value=[(_REF, graph)]):
                _reindex_changed_files([("A", "backend/old_module.py")], str(tmp_path), "SwarmAI")
                assert graph.get_nodes_by_file("backend/old_module.py")
                src.unlink()
                result = _reindex_changed_files(
                    [("D", "backend/old_module.py")], str(tmp_path), "SwarmAI")
            assert result == 1
            assert graph.get_nodes_by_file("backend/old_module.py") == []
        finally:
            graph.close()

    def test_skips_when_code_intel_unavailable(self, tmp_path):
        """Early return when code_intel module is not importable."""
        with patch("hooks.code_change_feed._CODE_INTEL_AVAILABLE", False):
            result = _reindex_changed_files([("A", "foo.py")], str(tmp_path), "SwarmAI")
        assert result == 0


class TestFeedWritesOnlyItsOwnRepo:
    def test_commit_in_another_repo_leaves_the_graph_alone(self, tmp_path):
        """Two repos can share a relative path; the graph of repo A must not take B's file."""
        from core.code_intel.graph_store import GraphStore

        repo_a, repo_b = tmp_path / "a", tmp_path / "b"
        for r, body in ((repo_a, "def in_a(): pass\n"), (repo_b, "def in_b(): pass\n")):
            (r / "src").mkdir(parents=True)
            (r / "src" / "main.py").write_text(body)
        graph = GraphStore(tmp_path / "g.db")
        try:
            graph.set_meta("repo_root", str(repo_a))
            with patch("hooks.code_change_feed.load_project_graphs", return_value=[(_REF, graph)]):
                assert _reindex_changed_files([("A", "src/main.py")], str(repo_a), "P") == 1
                assert _reindex_changed_files([("M", "src/main.py")], str(repo_b), "P") == 0
            ids = [n["id"] for n in graph.get_nodes_by_file("src/main.py")]
            assert ids == ["src/main.py::in_a"]
        finally:
            graph.close()
