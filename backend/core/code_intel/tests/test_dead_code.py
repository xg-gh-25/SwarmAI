"""Tests for dead_code module — entry point detection + find_dead_code."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.code_intel.dead_code import (
    DeadCodeResult,
    _is_entry_point,
    find_dead_code,
)


@pytest.fixture
def mock_graph_store():
    return MagicMock()


# ---------------------------------------------------------------------------
# Unified entry point detection (name + file_path only, no source/decorators)
# ---------------------------------------------------------------------------

class TestEntryPointDetection:
    """Test _is_entry_point — only checks that work with actual parser output."""

    # ── Python ──
    def test_python_test_function(self):
        assert _is_entry_point({"name": "test_login", "file_path": "tests/test_auth.py", "language": "python"})

    def test_python_test_file(self):
        assert _is_entry_point({"name": "setup", "file_path": "test_main.py", "language": "python"})

    def test_python_conftest(self):
        assert _is_entry_point({"name": "fixture", "file_path": "tests/conftest.py", "language": "python"})

    def test_python_init(self):
        assert _is_entry_point({"name": "reexport", "file_path": "pkg/__init__.py", "language": "python"})

    def test_python_main_name(self):
        assert _is_entry_point({"name": "main", "file_path": "cli.py", "language": "python"})

    def test_python_dunder_main(self):
        assert _is_entry_point({"name": "cli", "file_path": "__main__.py", "language": "python"})

    def test_python_regular_not_entry(self):
        assert not _is_entry_point({"name": "compute", "file_path": "utils.py", "language": "python"})

    # ── TypeScript ──
    def test_ts_test_file(self):
        assert _is_entry_point({"name": "it_works", "file_path": "app.test.ts", "language": "typescript"})

    def test_ts_spec_file(self):
        assert _is_entry_point({"name": "describe", "file_path": "app.spec.tsx", "language": "typescript"})

    def test_ts_regular_not_entry(self):
        assert not _is_entry_point({"name": "helper", "file_path": "utils.ts", "language": "typescript"})

    # ── Java ──
    def test_java_main(self):
        assert _is_entry_point({"name": "main", "file_path": "Main.java", "language": "java"})

    def test_java_test_prefix(self):
        assert _is_entry_point({"name": "testLogin", "file_path": "AuthTest.java", "language": "java"})

    def test_java_regular_not_entry(self):
        assert not _is_entry_point({"name": "process", "file_path": "Service.java", "language": "java"})

    # ── Go ──
    def test_go_main(self):
        assert _is_entry_point({"name": "main", "file_path": "main.go", "language": "go"})

    def test_go_init(self):
        assert _is_entry_point({"name": "init", "file_path": "init.go", "language": "go"})

    def test_go_test(self):
        assert _is_entry_point({"name": "TestSomething", "file_path": "foo_test.go", "language": "go"})

    def test_go_benchmark(self):
        assert _is_entry_point({"name": "BenchmarkSort", "file_path": "bench_test.go", "language": "go"})

    def test_go_regular_not_entry(self):
        assert not _is_entry_point({"name": "processQueue", "file_path": "worker.go", "language": "go"})

    # ── Unknown language ──
    def test_unknown_language_not_entry(self):
        assert not _is_entry_point({"name": "foo", "file_path": "x.rb", "language": "ruby"})


# ---------------------------------------------------------------------------
# find_dead_code integration
# ---------------------------------------------------------------------------

class TestFindDeadCode:
    @patch("core.code_intel.dead_code.subprocess.run")
    def test_identifies_dead_symbol(self, mock_run, mock_graph_store, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="1700000000", stderr="")
        mock_graph_store.find_dead_code.return_value = [
            {"id": "backend/core/utils.py::unused_helper",
             "file_path": "backend/core/utils.py",
             "node_type": "function", "name": "unused_helper"},
        ]
        mock_graph_store.get_nodes_by_file.return_value = [
            {"id": "backend/core/utils.py::unused_helper",
             "name": "unused_helper", "file_path": "backend/core/utils.py",
             "language": "python", "node_type": "function"},
        ]
        result = find_dead_code(mock_graph_store, tmp_path)
        assert result.total_scanned == 1
        assert len(result.symbols) == 1
        assert result.symbols[0].name == "unused_helper"

    @patch("core.code_intel.dead_code.subprocess.run")
    def test_excludes_entry_points(self, mock_run, mock_graph_store, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="0", stderr="")
        mock_graph_store.find_dead_code.return_value = [
            {"id": "tests/test_auth.py::test_login",
             "file_path": "tests/test_auth.py",
             "node_type": "function", "name": "test_login"},
        ]
        mock_graph_store.get_nodes_by_file.return_value = [
            {"id": "tests/test_auth.py::test_login",
             "name": "test_login", "file_path": "tests/test_auth.py",
             "language": "python", "node_type": "function"},
        ]
        result = find_dead_code(mock_graph_store, tmp_path)
        assert len(result.symbols) == 0

    @patch("core.code_intel.dead_code.subprocess.run")
    def test_sorted_oldest_first(self, mock_run, mock_graph_store, tmp_path):
        timestamps = iter(["1600000000", "1700000000", "1500000000"])
        mock_run.side_effect = lambda *a, **kw: MagicMock(
            returncode=0, stdout=next(timestamps), stderr=""
        )
        mock_graph_store.find_dead_code.return_value = [
            {"id": f"src/f{i}.py::fn{i}", "file_path": f"src/f{i}.py",
             "node_type": "function", "name": f"fn{i}"}
            for i in range(3)
        ]
        mock_graph_store.get_nodes_by_file.side_effect = lambda fp: [
            {"id": f"{fp}::fn", "name": "fn", "file_path": fp,
             "language": "python", "node_type": "function"}
        ]
        result = find_dead_code(mock_graph_store, tmp_path)
        ts_list = [s.last_commit_ts for s in result.symbols]
        assert ts_list == sorted(ts_list)

    @patch("core.code_intel.dead_code.subprocess.run")
    def test_language_filter(self, mock_run, mock_graph_store, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="0", stderr="")
        mock_graph_store.find_dead_code.return_value = [
            {"id": "a.py::py_fn", "file_path": "a.py",
             "node_type": "function", "name": "py_fn"},
            {"id": "a.ts::ts_fn", "file_path": "a.ts",
             "node_type": "function", "name": "ts_fn"},
        ]
        mock_graph_store.get_nodes_by_file.side_effect = lambda fp: {
            "a.py": [{"id": "a.py::py_fn", "name": "py_fn", "file_path": "a.py",
                       "language": "python", "node_type": "function"}],
            "a.ts": [{"id": "a.ts::ts_fn", "name": "ts_fn", "file_path": "a.ts",
                       "language": "typescript", "node_type": "function"}],
        }.get(fp, [])
        result = find_dead_code(mock_graph_store, tmp_path, languages=["python"])
        assert len(result.symbols) == 1
        assert result.symbols[0].language == "python"


class TestDeadCodeResult:
    def test_minimal_context(self):
        r = DeadCodeResult(symbols=[MagicMock()], total_scanned=10)
        ctx = r.to_minimal_context()
        assert "1 symbols" in ctx
        assert "10" in ctx
