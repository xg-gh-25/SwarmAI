"""Tests for code_intel_hook.py — PreToolUse context injection."""

from unittest.mock import MagicMock, patch

import pytest

from core.code_intel.code_intel_hook import (
    _build_context,
    create_code_intel_hook,
)


@pytest.fixture
def mock_graph():
    """Mock GraphStore for hook tests."""
    graph = MagicMock()
    graph.get_meta.return_value = "/tmp/test_repo"
    graph.get_nodes_by_file.return_value = [
        {"id": "path.py::func_a", "file_path": "path.py", "name": "func_a",
         "node_type": "function", "line_start": 1, "line_end": 10, "language": "python"},
        {"id": "path.py::ClassB", "file_path": "path.py", "name": "ClassB",
         "node_type": "class", "line_start": 12, "line_end": 50, "language": "python"},
    ]
    # count_callers_by_file returns {node_id: caller_count}
    graph.count_callers_by_file.return_value = {"path.py::func_a": 3, "path.py::ClassB": 2}
    return graph


class TestCreateCodeIntelHook:
    """Test the hook factory function."""

    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_non_read_tool_passthrough(self, mock_detect):
        hook = create_code_intel_hook()
        result = hook("Bash", {"command": "ls"})
        assert result == {"decision": "approve"}
        mock_detect.assert_not_called()

    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_no_project_detected(self, mock_detect):
        mock_detect.return_value = None
        hook = create_code_intel_hook()
        result = hook("Read", {"file_path": "/tmp/unknown/file.py"})
        assert result == {"decision": "approve"}

    @patch("core.code_intel.code_intel_hook.load_project_graph")
    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_no_graph_available(self, mock_detect, mock_load):
        mock_detect.return_value = "TestProject"
        mock_load.return_value = None
        hook = create_code_intel_hook()
        result = hook("Read", {"file_path": "/tmp/repo/file.py"})
        assert result == {"decision": "approve"}

    @patch("core.code_intel.code_intel_hook.load_project_graph")
    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_injects_context(self, mock_detect, mock_load, mock_graph):
        mock_detect.return_value = "TestProject"
        mock_load.return_value = mock_graph
        mock_graph.get_meta.return_value = "/tmp/test_repo"
        mock_graph.get_nodes_by_file.return_value = [
            {"id": "core/foo.py::bar", "file_path": "core/foo.py", "name": "bar",
             "node_type": "function", "line_start": 1, "line_end": 10, "language": "python"},
        ]
        # count_callers_by_file returns {node_id: caller_count}
        mock_graph.count_callers_by_file.return_value = {"core/foo.py::bar": 3}

        hook = create_code_intel_hook()
        result = hook("Read", {"file_path": "/tmp/test_repo/core/foo.py"})
        hso = result["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert "Code Intel" in hso["additionalContext"]
        assert "3 callers on 1/1 symbols" in hso["additionalContext"]

    @patch("core.code_intel.code_intel_hook.load_project_graph")
    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_grep_tool_supported(self, mock_detect, mock_load, mock_graph):
        mock_detect.return_value = "TestProject"
        mock_load.return_value = mock_graph
        mock_graph.get_nodes_by_file.return_value = []

        hook = create_code_intel_hook()
        result = hook("Grep", {"path": "/tmp/test_repo/core/foo.py"})
        assert result == {"decision": "approve"}

    @patch("core.code_intel.code_intel_hook.load_project_graph")
    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_empty_file_path(self, mock_detect, mock_load):
        hook = create_code_intel_hook()
        result = hook("Read", {"file_path": ""})
        assert result == {"decision": "approve"}
        mock_detect.assert_not_called()

    @patch("core.code_intel.code_intel_hook.load_project_graph")
    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_graph_cached_between_calls(self, mock_detect, mock_load, mock_graph):
        mock_detect.return_value = "TestProject"
        mock_load.return_value = mock_graph
        mock_graph.get_nodes_by_file.return_value = []

        hook = create_code_intel_hook()
        hook("Read", {"file_path": "/tmp/test_repo/a.py"})
        hook("Read", {"file_path": "/tmp/test_repo/b.py"})

        # load_project_graph called once (cached on second call)
        assert mock_load.call_count == 1

    @patch("core.code_intel.code_intel_hook.load_project_graph")
    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_error_returns_empty(self, mock_detect, mock_load):
        mock_detect.return_value = "TestProject"
        mock_load.side_effect = Exception("db corrupt")

        hook = create_code_intel_hook()
        result = hook("Read", {"file_path": "/tmp/test_repo/a.py"})
        assert result == {"decision": "approve"}


class TestFileTypeGate:
    """AC1/AC4 (R1, run_071e54c8): non-source files must not reach _build_context.

    The code_intel graph only indexes files whose extension is in parser.LANGUAGE_MAP.
    A Read/Grep on a non-source file (.md/.json/.env/…) has zero graph nodes, so
    running _build_context (up to 41s of SQLite JOINs that block the event loop) is
    pure waste. The gate must short-circuit BEFORE detect_project/_build_context, and
    must NOT break the Grep URL-route shortcut (which carries no file_path).
    """

    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_non_source_file_skipped_before_project_detection(self, mock_detect):
        # A .md file must be approved WITHOUT even detecting the project — the gate
        # sits before detect_project_from_path, so it is never called.
        hook = create_code_intel_hook()
        result = hook("Read", {"file_path": "/tmp/test_repo/README.md"})
        assert result == {"decision": "approve"}
        mock_detect.assert_not_called()

    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_json_and_env_files_skipped(self, mock_detect):
        hook = create_code_intel_hook()
        for fname in ("config.json", ".env", "notes.txt", "data.yaml"):
            result = hook("Read", {"file_path": f"/tmp/test_repo/{fname}"})
            assert result == {"decision": "approve"}
        mock_detect.assert_not_called()

    @patch("core.code_intel.code_intel_hook.load_project_graph")
    @patch("core.code_intel.code_intel_hook.detect_project_from_path")
    def test_source_file_still_processed(self, mock_detect, mock_load, mock_graph):
        # A .py file must still flow through to project detection (gate lets it pass).
        mock_detect.return_value = "TestProject"
        mock_load.return_value = mock_graph
        mock_graph.get_meta.return_value = "/tmp/test_repo"
        mock_graph.get_nodes_by_file.return_value = [
            {"id": "core/foo.py::bar", "file_path": "core/foo.py", "name": "bar",
             "node_type": "function", "line_start": 1, "line_end": 10, "language": "python"},
        ]
        mock_graph.count_callers_by_file.return_value = {"core/foo.py::bar": 3}
        hook = create_code_intel_hook()
        result = hook("Read", {"file_path": "/tmp/test_repo/core/foo.py"})
        mock_detect.assert_called_once()
        assert "hookSpecificOutput" in result

    @patch("core.code_intel.code_intel_hook._route_query")
    def test_grep_url_route_not_broken_by_gate(self, mock_route):
        # The Grep URL-route shortcut carries a pattern, NOT a file_path — the
        # file-type gate must not intercept it (regression guard, Gate-1 finding).
        mock_route.return_value = "route: GET /api/x → handler.py:42"
        hook = create_code_intel_hook()
        result = hook("Grep", {"pattern": "/api/x"})
        mock_route.assert_called_once()
        assert result["hookSpecificOutput"]["additionalContext"].startswith("route:")


class TestBuildContext:
    """Test the context string builder."""

    def test_no_repo_root(self):
        graph = MagicMock()
        graph.get_meta.return_value = None
        result = _build_context(graph, "/tmp/file.py", "test")
        assert result is None

    def test_no_nodes_in_file(self):
        graph = MagicMock()
        graph.get_meta.return_value = "/tmp/repo"
        graph.get_nodes_by_file.return_value = []
        result = _build_context(graph, "/tmp/repo/empty.py", "test")
        assert result is None

    def test_builds_context_string(self, mock_graph):
        result = _build_context(mock_graph, "/tmp/test_repo/path.py", "test")
        assert result is not None
        assert "📊 Code Intel" in result
        assert "5 callers" in result
        assert "2/2 symbols" in result
