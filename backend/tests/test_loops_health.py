"""Tests for the self-loops health check engine.

Verifies that the health check engine:
- Runs without errors on real workspace
- Returns valid report with expected schema
- Covers all 7 dimensions with 31 checks
- Scoring handles n/a gracefully
- Auto-fix doesn't corrupt files
"""

import json
import sys
from pathlib import Path

import pytest

# Import the engine directly (avoids subprocess + network timeout issues in CI)
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_loops-health" / "scripts"))
from loops_health_check import SelfLoopsHealthEngine


@pytest.fixture
def run_health_check():
    """Run the health check engine and return the report."""
    engine = SelfLoopsHealthEngine()
    engine.run(auto_fix=False)
    return engine

@pytest.fixture
def health_data(run_health_check):
    """Parse engine output as JSON dict."""
    return json.loads(run_health_check.to_json())


class TestHealthCheckSchema:
    """Verify output schema conformance."""

    def test_has_required_fields(self, health_data):
        data = health_data
        assert "overall_score" in data
        assert "scores" in data
        assert "findings" in data
        assert "found" in data
        assert "fixed" in data
        assert isinstance(data["overall_score"], int)
        assert 0 <= data["overall_score"] <= 100

    def test_all_7_dimensions_present(self, health_data):
        data = health_data
        expected_dims = {"context", "memory", "knowledge", "evolution", "coherence", "brain_safety", "infrastructure"}
        actual_dims = set(data["scores"].keys())
        # brain_safety or infrastructure may be excluded if all n/a
        assert actual_dims.issubset(expected_dims)
        # At minimum, core dims should always be present
        assert {"context", "memory", "knowledge", "evolution"}.issubset(actual_dims)

    def test_31_checks(self, health_data):
        data = health_data
        # 29 after 2026-08-14: removed C3 (Active Projects & DDD in KNOWLEDGE) and
        # K1 (Index completeness) with the in-prompt index deletion.
        assert len(data["findings"]) == 29

    def test_finding_schema(self, health_data):
        data = health_data
        for f in data["findings"]:
            assert "id" in f
            assert "name" in f
            assert "dimension" in f
            assert "status" in f
            assert f["status"] in ("pass", "warn", "fail", "n/a")

    def test_score_is_min_of_dimensions(self, health_data):
        data = health_data
        scores = data["scores"]
        if scores:
            assert data["overall_score"] == min(scores.values())


class TestDimensionChecks:
    """Verify each dimension produces expected check IDs."""

    def test_context_checks(self, health_data):
        ctx_checks = [f for f in health_data["findings"] if f["dimension"] == "context"]
        assert len(ctx_checks) == 4
        ids = {f["id"] for f in ctx_checks}
        assert ids == {"C1", "C2", "C3", "C4"}

    def test_memory_checks(self, health_data):
        mem_checks = [f for f in health_data["findings"] if f["dimension"] == "memory"]
        assert len(mem_checks) == 6
        ids = {f["id"] for f in mem_checks}
        assert ids == {"M1", "M2", "M3", "M4", "M5", "M6"}

    def test_evolution_checks(self, health_data):
        evo_checks = [f for f in health_data["findings"] if f["dimension"] == "evolution"]
        assert len(evo_checks) == 4
        ids = {f["id"] for f in evo_checks}
        assert ids == {"E1", "E2", "E3", "E4"}

    def test_brain_safety_checks(self, health_data):
        bs_checks = [f for f in health_data["findings"] if f["dimension"] == "brain_safety"]
        assert len(bs_checks) == 4
        ids = {f["id"] for f in bs_checks}
        assert ids == {"B1", "B2", "B3", "B4"}

    def test_infrastructure_checks(self, health_data):
        infra_checks = [f for f in health_data["findings"] if f["dimension"] == "infrastructure"]
        assert len(infra_checks) == 6
        ids = {f["id"] for f in infra_checks}
        assert ids == {"I1", "I2", "I3", "I4", "I5", "I6"}


@pytest.mark.skipif(
    not Path.home().joinpath(".swarm-ai/SwarmWS/.context/MEMORY.md").exists(),
    reason="Requires real SwarmWS workspace (CI has no ~/.swarm-ai/)"
)
class TestAutoFix:
    """Verify auto-fix safety."""

    def test_auto_fix_doesnt_crash(self):
        """Running with --auto-fix should not crash."""
        engine = SelfLoopsHealthEngine()
        engine.run(auto_fix=True)
        assert hasattr(engine.report, "fixes_applied")

    def test_memory_not_corrupted_after_autofix(self):
        """MEMORY.md should have required headers after auto-fix."""
        engine = SelfLoopsHealthEngine()
        engine.run(auto_fix=True)
        from loops_health_check import CONTEXT_DIR
        mem = (CONTEXT_DIR / "MEMORY.md").read_text()
        assert "## Recent Context" in mem
        assert "## Key Decisions" in mem
        assert "## Lessons Learned" in mem
        assert "## Open Threads" in mem


class TestMarkdownOutput:
    """Verify markdown report format."""

    def test_markdown_has_frontmatter(self, run_health_check):
        output = run_health_check.to_markdown()
        assert output.startswith("---\n")
        assert "job_id: loops-health" in output
        assert "score:" in output
        assert "found:" in output

    def test_markdown_has_summary_table(self, run_health_check):
        output = run_health_check.to_markdown()
        assert "## Summary: Found" in output
        assert "| Dimension |" in output
