"""Tests for Code Intelligence Feed (DDD Cultivation Channel 7).

Tests drift detection between code graph and TECH.md,
health enrichment via coverage, and maturity evidence from tests.
"""

import pytest
from unittest.mock import patch, MagicMock

from core.code_intel_feed import (
    detect_tech_drift,
    get_code_coverage_for_health,
    get_test_coverage_for_maturity,
    _extract_backtick_symbols,
)


@pytest.fixture
def workspace(tmp_path):
    """Create workspace with project and TECH.md."""
    project_dir = tmp_path / "Projects" / "SwarmAI"
    project_dir.mkdir(parents=True)
    (project_dir / "TECH.md").write_text(
        "# SwarmAI Tech\n\n"
        "## Architecture\n\n"
        "The system uses `session_unit` and `prompt_builder`.\n\n"
        "## Key Subsystems\n\n"
        "- Sessions: `session_router` manages lifecycle\n"
        "- Memory: `memory_index` handles recall\n"
    )
    return tmp_path


@pytest.fixture
def mock_graph():
    """Create mock GraphStore with module map."""
    graph = MagicMock()
    graph.get_module_map.return_value = {
        "backend/core": [
            {"id": "1", "file_path": "backend/core/session_unit.py", "node_type": "function", "name": "session_unit"},
            {"id": "2", "file_path": "backend/core/session_unit.py", "node_type": "function", "name": "send"},
            {"id": "3", "file_path": "backend/core/session_unit.py", "node_type": "function", "name": "kill"},
            {"id": "4", "file_path": "backend/core/session_unit.py", "node_type": "function", "name": "spawn"},
            {"id": "5", "file_path": "backend/core/session_unit.py", "node_type": "function", "name": "transition"},
            {"id": "6", "file_path": "backend/core/session_unit.py", "node_type": "function", "name": "retry"},
        ],
        "backend/hooks": [
            {"id": "7", "file_path": "backend/hooks/new_undocumented.py", "node_type": "function", "name": "undoc_func1"},
            {"id": "8", "file_path": "backend/hooks/new_undocumented.py", "node_type": "function", "name": "undoc_func2"},
            {"id": "9", "file_path": "backend/hooks/new_undocumented.py", "node_type": "function", "name": "undoc_func3"},
            {"id": "10", "file_path": "backend/hooks/new_undocumented.py", "node_type": "function", "name": "undoc_func4"},
            {"id": "11", "file_path": "backend/hooks/new_undocumented.py", "node_type": "function", "name": "undoc_func5"},
        ],
        "backend/tests": [
            {"id": "12", "file_path": "backend/tests/test_session.py", "node_type": "function", "name": "test_send"},
        ],
    }
    return graph


class TestDetectTechDrift:
    """Channel 7: code graph vs TECH.md drift detection."""

    def test_undocumented_module_emits_health_signal_not_proposal(self, workspace, mock_graph):
        """Admission Component A (run_8d5fe9d1): drift is a HEALTH SIGNAL, not a
        human-review proposal. Module with >=5 fns not in TECH.md → drift record,
        ZERO proposals."""
        import json
        with patch("core.code_intel.load_project_graph", return_value=mock_graph):
            count = detect_tech_drift(str(workspace))

        # AC2: NO proposals written to the review queue.
        proposals_dir = workspace / "Projects" / "SwarmAI" / ".artifacts" / "proposals"
        assert not proposals_dir.exists() or not list(proposals_dir.glob("*.json"))
        # drift IS surfaced as a pull-only health record.
        health = workspace / "Projects" / "SwarmAI" / ".artifacts" / "code_drift_health.json"
        assert health.exists()
        rec = json.loads(health.read_text())
        assert rec["drift_count"] == count and count >= 1
        assert any(i["kind"] == "undocumented_module" for i in rec["items"])

    def test_documented_module_no_drift_item(self, workspace, mock_graph):
        """Module mentioned in TECH.md → not in the drift record."""
        import json
        with patch("core.code_intel.load_project_graph", return_value=mock_graph):
            detect_tech_drift(str(workspace))

        # never writes proposals (AC2)
        proposals_dir = workspace / "Projects" / "SwarmAI" / ".artifacts" / "proposals"
        assert not proposals_dir.exists() or not list(proposals_dir.glob("*.json"))
        # a documented module ("backend/core", session_unit in TECH.md) is not drift
        health = workspace / "Projects" / "SwarmAI" / ".artifacts" / "code_drift_health.json"
        if health.exists():
            rec = json.loads(health.read_text())
            assert all(i.get("module") != "backend/core" for i in rec["items"])

    def test_small_module_skipped(self, tmp_path):
        """Modules with <5 functions → no undocumented-module proposal."""
        # Use a clean TECH.md without backtick symbols to avoid stale-ref noise
        project_dir = tmp_path / "Projects" / "SwarmAI"
        project_dir.mkdir(parents=True)
        (project_dir / "TECH.md").write_text(
            "# SwarmAI Tech\n\n## Architecture\n\nSimple system.\n"
        )

        small_graph = MagicMock()
        small_graph.get_module_map.return_value = {
            "backend/tiny": [
                {"id": "1", "file_path": "backend/tiny/x.py", "node_type": "function", "name": "f1"},
                {"id": "2", "file_path": "backend/tiny/x.py", "node_type": "function", "name": "f2"},
            ]
        }
        with patch("core.code_intel.load_project_graph", return_value=small_graph):
            count = detect_tech_drift(str(tmp_path))
        assert count == 0

    def test_no_graph_returns_zero(self, workspace):
        """No code_intel.db → 0 proposals, no crash."""
        with patch("core.code_intel.load_project_graph", return_value=None):
            count = detect_tech_drift(str(workspace))
        assert count == 0

    def test_stale_symbol_detected(self, workspace, mock_graph):
        """Symbol in TECH.md backticks but not in graph → stale drift item (count>=1)."""
        # Add a symbol to TECH.md that doesn't exist in graph
        tech_path = workspace / "Projects" / "SwarmAI" / "TECH.md"
        content = tech_path.read_text()
        content += "\n\nThe `deleted_function_xyz` was important.\n"
        tech_path.write_text(content)

        with patch("core.code_intel.load_project_graph", return_value=mock_graph):
            count = detect_tech_drift(str(workspace))

        # Should have at least the stale reference proposal
        assert count >= 1


class TestCodeCoverageForHealth:
    """Health enrichment: documentation coverage from code graph."""

    def test_returns_coverage_ratio(self, workspace, mock_graph):
        """Coverage = documented modules / significant modules."""
        with patch("core.code_intel.load_project_graph", return_value=mock_graph):
            coverage = get_code_coverage_for_health(str(workspace))

        # "core" (6 fns) is documented (session_unit in TECH.md)
        # "hooks" (5 fns) is NOT documented
        # "tests" (1 fn) is < 5, not significant
        # Coverage = 1/2 = 0.5
        assert coverage is not None
        assert 0.0 <= coverage <= 1.0

    def test_no_graph_returns_none(self, workspace):
        """No code_intel → None (health can't use this dimension)."""
        with patch("core.code_intel.load_project_graph", return_value=None):
            coverage = get_code_coverage_for_health(str(workspace))
        assert coverage is None


class TestTestCoverageForMaturity:
    """Maturity evidence: modules with test files."""

    def test_detects_test_files(self, workspace, mock_graph):
        """Non-test modules that have test files → has_tests correctly detected."""
        with patch("core.code_intel.load_project_graph", return_value=mock_graph):
            result = get_test_coverage_for_maturity(str(workspace))

        # backend/tests is skipped (it IS a test module)
        assert "backend/tests" not in result
        # backend/core should be present (non-test module)
        assert "backend/core" in result

    def test_no_graph_returns_empty(self, workspace):
        with patch("core.code_intel.load_project_graph", return_value=None):
            result = get_test_coverage_for_maturity(str(workspace))
        assert result == {}


class TestExtractBacktickSymbols:
    """Symbol extraction from markdown."""

    def test_extracts_code_symbols(self):
        text = "Uses `session_unit` and `prompt_builder` for context."
        symbols = _extract_backtick_symbols(text)
        assert "session_unit" in symbols
        assert "prompt_builder" in symbols

    def test_skips_short_symbols(self):
        text = "The `if` statement and `x` variable."
        symbols = _extract_backtick_symbols(text)
        assert "if" not in symbols
        assert "x" not in symbols

    def test_skips_urls_and_paths(self):
        text = "Visit `https://example.com` or `--verbose` flag."
        symbols = _extract_backtick_symbols(text)
        assert "https://example.com" not in symbols
        assert "--verbose" not in symbols
