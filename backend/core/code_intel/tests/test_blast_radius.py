"""Tests for blast_radius module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.code_intel.blast_radius import (
    BlastRadiusResult,
    ImpactNode,
    _classify_risk,
    _get_module,
    _parse_diff_line_ranges,
    analyze_diff,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/backend/core/auth.py b/backend/core/auth.py
--- a/backend/core/auth.py
+++ b/backend/core/auth.py
@@ -10,0 +11,3 @@
+    new_line_1
+    new_line_2
+    new_line_3
@@ -30,2 +33,2 @@
-    old_line
+    new_line
diff --git a/backend/api/views.py b/backend/api/views.py
--- a/backend/api/views.py
+++ b/backend/api/views.py
@@ -5,0 +6,1 @@
+    added_line
"""


@pytest.fixture
def mock_graph_store():
    gs = MagicMock()

    # get_nodes_by_file: returns dicts with id, name, line_start, line_end
    gs.get_nodes_by_file.side_effect = lambda fp: {
        "backend/core/auth.py": [
            {"id": "backend/core/auth.py:login", "name": "login",
             "file_path": "backend/core/auth.py", "line_start": 8, "line_end": 20,
             "node_type": "function", "language": "python"},
            {"id": "backend/core/auth.py:logout", "name": "logout",
             "file_path": "backend/core/auth.py", "line_start": 25, "line_end": 35,
             "node_type": "function", "language": "python"},
        ],
        "backend/api/views.py": [
            {"id": "backend/api/views.py:index", "name": "index",
             "file_path": "backend/api/views.py", "line_start": 1, "line_end": 10,
             "node_type": "function", "language": "python"},
        ],
        "backend/api/handler.py": [
            {"id": "backend/api/handler.py:handle_request", "name": "handle_request",
             "file_path": "backend/api/handler.py", "line_start": 1, "line_end": 20,
             "node_type": "function", "language": "python"},
        ],
        "backend/middleware/check.py": [
            {"id": "backend/middleware/check.py:check", "name": "check",
             "file_path": "backend/middleware/check.py", "line_start": 1, "line_end": 10,
             "node_type": "function", "language": "python"},
        ],
    }.get(fp, [])

    # blast_radius returns list[tuple[node_id, depth]]
    gs.blast_radius.return_value = [
        ("backend/core/auth.py:login", 0),
        ("backend/core/auth.py:logout", 0),
        ("backend/api/views.py:index", 0),
        ("backend/api/handler.py:handle_request", 1),
        ("backend/middleware/check.py:check", 2),
    ]

    # find_callers returns list[tuple[caller_id, hop]]
    # handler has a test caller, check does not
    def _find_callers(node_id, depth=1):
        if node_id == "backend/api/handler.py:handle_request":
            return [("test_handler.py:test_handle_request", 1)]
        return []

    gs.find_callers.side_effect = _find_callers
    return gs


# ---------------------------------------------------------------------------
# Diff parsing tests
# ---------------------------------------------------------------------------

class TestParseDiffLineRanges:
    def test_basic_diff(self):
        result = _parse_diff_line_ranges(SAMPLE_DIFF)
        assert "backend/core/auth.py" in result
        assert "backend/api/views.py" in result

    def test_line_ranges(self):
        result = _parse_diff_line_ranges(SAMPLE_DIFF)
        auth_ranges = result["backend/core/auth.py"]
        assert (11, 13) in auth_ranges   # +11,3
        assert (33, 34) in auth_ranges   # +33,2

    def test_single_line_hunk(self):
        result = _parse_diff_line_ranges(SAMPLE_DIFF)
        views_ranges = result["backend/api/views.py"]
        assert (6, 6) in views_ranges    # +6,1

    def test_empty_diff(self):
        assert _parse_diff_line_ranges("") == {}


# ---------------------------------------------------------------------------
# Module extraction
# ---------------------------------------------------------------------------

class TestGetModule:
    def test_deep_path(self):
        assert _get_module("backend/core/auth.py") == "backend/core"

    def test_shallow_path(self):
        assert _get_module("src/app.py") == "src"

    def test_root_file(self):
        assert _get_module("setup.py") == "root"


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------

class TestClassifyRisk:
    def test_low(self):
        assert _classify_risk(total_affected=2, untested_count=0, modules_crossed=1) == "LOW"

    def test_medium_by_affected(self):
        assert _classify_risk(total_affected=4, untested_count=0, modules_crossed=1) == "MEDIUM"

    def test_medium_by_modules(self):
        assert _classify_risk(total_affected=1, untested_count=0, modules_crossed=3) == "MEDIUM"

    def test_high_by_affected(self):
        assert _classify_risk(total_affected=11, untested_count=0, modules_crossed=1) == "HIGH"

    def test_high_by_untested(self):
        assert _classify_risk(total_affected=1, untested_count=3, modules_crossed=1) == "HIGH"

    def test_critical_by_affected(self):
        assert _classify_risk(total_affected=21, untested_count=0, modules_crossed=1) == "CRITICAL"

    def test_critical_by_untested(self):
        assert _classify_risk(total_affected=1, untested_count=6, modules_crossed=1) == "CRITICAL"


# ---------------------------------------------------------------------------
# Integration: analyze_diff
# ---------------------------------------------------------------------------

class TestAnalyzeDiff:
    @patch("core.code_intel.blast_radius.subprocess.run")
    def test_full_analysis(self, mock_run, mock_graph_store, tmp_path):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=SAMPLE_DIFF, stderr=""
        )
        result = analyze_diff(mock_graph_store, tmp_path, base_ref="HEAD~1")

        # Changed nodes should include overlapping ones
        assert "backend/core/auth.py:login" in result.changed_nodes
        assert "backend/core/auth.py:logout" in result.changed_nodes
        assert "backend/api/views.py:index" in result.changed_nodes

        # Affected callers populated from blast_radius (depth>0 nodes not in changed set)
        assert result.total_affected == 2
        affected_names = [c.name for c in result.affected_callers]
        assert "handle_request" in affected_names
        assert "check" in affected_names

        # check has no test caller
        assert "check" in result.untested_callers

    @patch("core.code_intel.blast_radius.subprocess.run")
    def test_empty_diff(self, mock_run, mock_graph_store, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = analyze_diff(mock_graph_store, tmp_path)
        assert result.changed_nodes == []
        assert result.risk_level == "LOW"

    @patch("core.code_intel.blast_radius.subprocess.run")
    def test_git_failure(self, mock_run, mock_graph_store, tmp_path):
        mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="fatal: bad ref")
        result = analyze_diff(mock_graph_store, tmp_path)
        assert result.changed_nodes == []


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

class TestBlastRadiusResult:
    def test_minimal_context(self):
        r = BlastRadiusResult(
            changed_nodes=["a", "b"],
            untested_callers=["c"],
            modules_crossed={"mod1", "mod2"},
            total_affected=5,
            risk_level="MEDIUM",
        )
        ctx = r.to_minimal_context()
        assert "MEDIUM" in ctx
        assert "2 changed" in ctx
        assert "5 affected" in ctx
        assert "1 untested" in ctx

    def test_full_context_includes_callers(self):
        r = BlastRadiusResult(
            changed_nodes=["fn_a"],
            affected_callers=[
                ImpactNode(id="c1", file_path="x.py", name="caller1",
                           depth=1, has_test=False, module="mod"),
            ],
            untested_callers=["caller1"],
            modules_crossed={"mod"},
            total_affected=1,
            risk_level="LOW",
        )
        full = r.to_full_context()
        assert "caller1" in full
        assert "fn_a" in full


# ---------------------------------------------------------------------------
# Bidirectional blast radius test
# ---------------------------------------------------------------------------

class TestBidirectionalBlast:
    @patch("core.code_intel.blast_radius.subprocess.run")
    def test_callers_and_callees_included(self, mock_run, tmp_path):
        """blast_radius from graph_store should include both upstream and downstream."""
        gs = MagicMock()
        gs.get_nodes_by_file.return_value = [
            {"id": "src/app.py:fn1", "name": "fn1", "file_path": "src/app.py",
             "line_start": 1, "line_end": 10, "node_type": "function", "language": "python"}
        ]
        # blast_radius returns bidirectional results as tuples
        gs.blast_radius.return_value = [
            ("src/app.py:fn1", 0),
            ("a/b.py:caller", 1),
            ("c/d.py:callee", 1),
        ]
        # For node resolution
        gs.get_nodes_by_file.side_effect = lambda fp: {
            "src/app.py": [
                {"id": "src/app.py:fn1", "name": "fn1", "file_path": "src/app.py",
                 "line_start": 1, "line_end": 10, "node_type": "function", "language": "python"}
            ],
            "a/b.py": [
                {"id": "a/b.py:caller", "name": "caller", "file_path": "a/b.py",
                 "line_start": 1, "line_end": 5, "node_type": "function", "language": "python"}
            ],
            "c/d.py": [
                {"id": "c/d.py:callee", "name": "callee", "file_path": "c/d.py",
                 "line_start": 1, "line_end": 5, "node_type": "function", "language": "python"}
            ],
        }.get(fp, [])
        gs.find_callers.return_value = []

        diff = (
            "diff --git a/src/app.py b/src/app.py\n"
            "@@ -1,0 +2,1 @@\n"
            "+new\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=diff, stderr="")

        result = analyze_diff(gs, tmp_path)
        names = [c.name for c in result.affected_callers]
        assert "caller" in names
        assert "callee" in names
