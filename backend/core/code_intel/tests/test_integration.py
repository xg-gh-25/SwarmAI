"""Integration test: parse → store → resolve → blast → review.

Tests the E2E flow without requiring tree-sitter (uses regex fallback).
"""


import pytest


class TestE2EFlow:
    """End-to-end integration: parse files → store in graph → query."""

    @pytest.fixture
    def repo_dir(self, tmp_path):
        """Create a mini Python project for integration testing."""
        # core/utils.py
        core = tmp_path / "core"
        core.mkdir()
        (core / "__init__.py").write_text("")
        (core / "utils.py").write_text(
            'def helper_func():\n'
            '    """A utility function."""\n'
            '    return 42\n'
            '\n'
            'def unused_export():\n'
            '    """Nobody calls this."""\n'
            '    pass\n'
        )

        # core/main_module.py
        (core / "main_module.py").write_text(
            'from core.utils import helper_func\n'
            '\n'
            'class MyService:\n'
            '    def process(self):\n'
            '        return helper_func()\n'
            '\n'
            '    def internal_method(self):\n'
            '        self.process()\n'
        )

        # tests/test_main.py
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("")
        (tests / "test_main.py").write_text(
            'from core.main_module import MyService\n'
            '\n'
            'def test_process():\n'
            '    svc = MyService()\n'
            '    assert svc.process() == 42\n'
        )

        return tmp_path

    @pytest.fixture
    def graph_db(self, tmp_path):
        """Create a fresh graph store."""
        from core.code_intel.graph_store import GraphStore
        db_path = tmp_path / "code_intel.db"
        return GraphStore(db_path)

    def test_parse_and_store(self, repo_dir, graph_db):
        """Parse a mini repo and store results."""
        from core.code_intel.parser import parse_repo

        results = parse_repo(repo_dir)
        assert len(results) > 0

        # Store all results
        graph_db.bulk_insert(results)

        # Verify nodes stored
        summary = graph_db.get_codebase_summary()
        assert summary["total_nodes"] > 0

    def test_symbol_search(self, repo_dir, graph_db):
        """Parse, store, and search for symbols."""
        from core.code_intel.parser import parse_repo

        results = parse_repo(repo_dir)
        graph_db.bulk_insert(results)

        # Search for a function
        hits = graph_db.search_symbols("helper")
        assert len(hits) > 0

    def test_keyword_search(self, repo_dir, graph_db):
        """Keyword LIKE search."""
        from core.code_intel.parser import parse_repo

        results = parse_repo(repo_dir)
        graph_db.bulk_insert(results)

        hits = graph_db.keyword_search("helper")
        assert len(hits) > 0

    def test_module_map(self, repo_dir, graph_db):
        """Module clustering."""
        from core.code_intel.parser import parse_repo

        results = parse_repo(repo_dir)
        graph_db.bulk_insert(results)

        modules = graph_db.get_module_map()
        assert len(modules) > 0

    def test_meta_tracking(self, repo_dir, graph_db):
        """Meta key/value storage."""
        graph_db.set_meta("repo_root", str(repo_dir))
        graph_db.set_meta("last_indexed_commit", "abc123")

        assert graph_db.get_meta("repo_root") == str(repo_dir)
        assert graph_db.get_meta("last_indexed_commit") == "abc123"
        assert graph_db.get_meta("nonexistent") is None

    def test_freshness_never_indexed(self, repo_dir, graph_db):
        """Freshness check on never-indexed graph."""
        from core.code_intel.freshness import check_freshness

        graph_db.set_meta("repo_root", str(repo_dir))
        # No last_indexed_commit → stale, suggest rebuild

        result = check_freshness(graph_db)
        assert result.stale is True

    def test_codebase_map_generation(self, repo_dir, graph_db):
        """Generate codebase briefing string."""
        from core.code_intel.codebase_map import _format_briefing
        from core.code_intel.parser import parse_repo

        results = parse_repo(repo_dir)
        graph_db.bulk_insert(results)

        summary = graph_db.get_codebase_summary()
        if summary and summary.get("total_nodes", 0) > 0:
            briefing = _format_briefing("TestProject", summary)
            assert briefing is not None
            assert "📦" in briefing
            assert "TestProject" in briefing

    def test_clear_and_rebuild(self, repo_dir, graph_db):
        """Clear graph and rebuild from scratch."""
        from core.code_intel.parser import parse_repo

        results = parse_repo(repo_dir)
        graph_db.bulk_insert(results)

        initial_count = graph_db.get_codebase_summary()["total_nodes"]
        assert initial_count > 0

        graph_db.clear()
        assert graph_db.get_codebase_summary()["total_nodes"] == 0

        graph_db.bulk_insert(results)
        assert graph_db.get_codebase_summary()["total_nodes"] == initial_count
