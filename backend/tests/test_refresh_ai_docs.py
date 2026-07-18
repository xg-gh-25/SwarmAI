"""Tests for backend/scripts/refresh_ai_docs.py.

Verifies: engine discovery from YAML, code intel stats measurement,
metrics collection, staleness detection, and section replacement.
"""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the module under test
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.refresh_ai_docs import (
    _get_code_intel_stats,
    _generate_capabilities_block,
    _generate_metrics_block,
    _load_engines,
    _replace_section,
    check_staleness,
    collect_metrics,
    ENGINES_YAML,
    REPO_ROOT,
)


class TestEngineDiscovery:
    """Engine list is loaded from engines.yaml, not hardcoded."""

    def test_load_engines_from_yaml(self):
        """Engines are loaded from the YAML registry file."""
        engines = _load_engines()
        assert len(engines) >= 10, f"Expected 10+ engines, got {len(engines)}"
        # Each engine has required fields
        for e in engines:
            assert "name" in e
            assert "path" in e
            assert "description" in e

    def test_engines_yaml_exists(self):
        """engines.yaml registry file exists alongside the script."""
        assert ENGINES_YAML.exists(), f"Missing: {ENGINES_YAML}"

    def test_new_engine_only_requires_yaml_edit(self):
        """Adding an engine doesn't require editing refresh_ai_docs.py."""
        # The script loads engines dynamically — verify no hardcoded engine list
        script_content = (Path(__file__).resolve().parent.parent / "scripts" / "refresh_ai_docs.py").read_text()
        assert "engine_checks" not in script_content, "Hardcoded engine_checks dict still present"
        assert "engine_details" not in script_content, "Hardcoded engine_details dict still present"

    def test_missing_path_excluded(self):
        """Engines whose path doesn't exist are excluded from output."""
        metrics = collect_metrics()
        for engine in metrics["engines"]:
            assert (REPO_ROOT / engine["path"]).exists(), f"Dead engine in output: {engine['name']}"


class TestCodeIntelStats:
    """Code Intelligence stats are measured from DB, not hardcoded."""

    def test_returns_zeros_when_no_db(self):
        """Returns (0, 0) when no database file exists."""
        with patch("scripts.refresh_ai_docs.CODE_INTEL_DB_PATHS", [Path("/nonexistent/db")]):
            symbols, edges = _get_code_intel_stats()
            assert symbols == 0
            assert edges == 0

    def test_reads_real_db(self):
        """Reads actual counts from a SQLite database."""
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            conn = sqlite3.connect(f.name)
            conn.execute("CREATE TABLE code_nodes (id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE code_edges (id INTEGER PRIMARY KEY)")
            for i in range(5):
                conn.execute("INSERT INTO code_nodes VALUES (?)", (i,))
            for i in range(3):
                conn.execute("INSERT INTO code_edges VALUES (?)", (i,))
            conn.commit()
            conn.close()

            with patch("scripts.refresh_ai_docs.CODE_INTEL_DB_PATHS", [Path(f.name)]):
                symbols, edges = _get_code_intel_stats()
                assert symbols == 5
                assert edges == 3

    def test_capabilities_block_uses_static_description(self):
        """Capabilities block uses the STATIC engine description — no live counts.

        Volatile code-graph counts must NOT be injected here (they belong in the
        metrics table), so the block stays stable in the context-loaded AGENTS.md.
        """
        metrics = {
            "engines": [{"name": "Code Intelligence (AST graph)", "path": "backend/core/code_intel/__init__.py", "description": "Deterministic graph traversal for code context retrieval"}],
            "code_intel_symbols": 12345,
            "code_intel_edges": 6789,
        }
        block = _generate_capabilities_block(metrics)
        assert "Deterministic graph traversal for code context retrieval" in block
        assert "12,345 symbols" not in block  # counts live in the metrics table now
        assert "6,789 edges" not in block

    def test_metrics_block_includes_code_graph_counts(self):
        """Code-graph symbol/edge counts appear in the metrics block, not capabilities."""
        metrics = collect_metrics()
        block = _generate_metrics_block(metrics)
        assert "Code graph" in block
        assert "symbols" in block and "edges" in block


class TestMetricsConsistency:
    """Verify commands in output match actual measurement approach."""

    def test_no_unused_metrics_collected(self):
        """Every collected metric either appears in output or is a computed intermediate."""
        metrics = collect_metrics()
        output = _generate_metrics_block(metrics)

        # These are consumed by the metrics output block
        used_keys = {
            "commit_count", "duration_days", "core_modules", "core_loc",
            "total_backend_loc", "test_files", "skill_count", "hooks_count",
            "react_components", "platform_modes", "pipeline_spec_lines",
            "session_unit_lines", "context_loader_lines", "job_count",
            "code_intel_symbols", "code_intel_edges",
        }
        # These are consumed by the capabilities block or staleness checks
        intermediate_keys = {"engines"}

        all_keys = set(metrics.keys())
        assert all_keys == used_keys | intermediate_keys, (
            f"Unused metrics: {all_keys - used_keys - intermediate_keys}"
        )

    def test_verify_commands_match_measurement(self):
        """How to Verify column uses the same command logic as the script."""
        metrics = collect_metrics()
        output = _generate_metrics_block(metrics)
        # total_backend_loc uses the git-tracked caliber (reproducible, excludes
        # .venv + gitignored CMHK skills). The verify-string must match the
        # command the script actually runs (git ls-files + awk-sum), NOT the old
        # `find … | xargs cat` that timed out and counted venv-polluted code.
        assert "git ls-files '*.py'" in output
        assert "grep -v '/tests/'" in output
        # The awk-sum tail is the portable, empty-safe summation the script runs
        # (skips wc's own 'total' line) — verify-string must show it, not `tail -1`.
        assert 'awk \'$2!="total"' in output
        # core_loc + core_modules now use the SAME git-tracked tests-OUT caliber
        # as total_backend_loc (run_e92f91dc — REVIEW LOW from run_7c8453a2). The
        # verify-string must show the git ls-files prefix command, NOT -exec cat.
        assert "grep '^backend/core/'" in output
        assert "-exec cat" not in output


class TestStalenessDetection:
    """Prose staleness checks detect code/documentation drift."""

    def test_no_false_positives_on_current_codebase(self):
        """All staleness checks pass on the current codebase."""
        warnings = check_staleness()
        assert warnings == [], f"Unexpected staleness warnings: {warnings}"

    def test_detects_missing_code_file(self):
        """Reports MISSING when code file doesn't exist."""
        from scripts.refresh_ai_docs import STALENESS_CHECKS

        with patch.object(
            sys.modules["scripts.refresh_ai_docs"],
            "STALENESS_CHECKS",
            [{
                "name": "test_missing",
                "description": "Test check",
                "prose_file": "AGENTS.md",
                "prose_pattern": r"SwarmAI",
                "code_file": "nonexistent/file.py",
                "code_pattern": r"anything",
            }],
        ):
            warnings = check_staleness()
            assert len(warnings) == 1
            assert warnings[0]["severity"] == "MISSING"

    def test_check_staleness_cli_flag(self):
        """--check-staleness flag works independently."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "backend/scripts/refresh_ai_docs.py", "--check-staleness"],
            capture_output=True, text=True, cwd=REPO_ROOT,
        )
        assert result.returncode == 0
        assert "Staleness check:" in result.stdout


class TestSectionReplacement:
    """Marker-delimited section replacement logic."""

    def test_replaces_between_markers(self):
        content = "before\n<!-- START -->\nold\n<!-- END -->\nafter"
        result = _replace_section(content, "<!-- START -->", "<!-- END -->", "new")
        assert result == "before\n<!-- START -->\nnew\n<!-- END -->\nafter"

    def test_preserves_content_without_markers(self):
        content = "no markers here"
        result = _replace_section(content, "<!-- START -->", "<!-- END -->", "new")
        assert result == content

    def test_handles_multiline_replacement(self):
        content = "a\n<!-- S -->\nline1\nline2\nline3\n<!-- E -->\nb"
        result = _replace_section(content, "<!-- S -->", "<!-- E -->", "replaced")
        assert "line1" not in result
        assert "replaced" in result
