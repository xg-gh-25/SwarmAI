"""Tests for DDD Auto-Approval Gate and ObservationMiner.

Step 2: Auto-approval adds maturity, precision, magnitude, circuit breaker gates.
Step 3: ObservationMiner extracts RETRY + LONG_TOOL patterns from observation data.
"""
import json
import time
from unittest.mock import MagicMock

import pytest


# ── DDD Auto-Approval Gate tests ──────────────────────────────────────────


class TestAutoApprovalGate:
    """Auto-approval criteria enforcement."""

    @pytest.fixture
    def proposal(self):
        """A safe additive proposal targeting IMPROVEMENT.md."""
        from core.ddd_cultivation import CultivationProposal
        return CultivationProposal(
            target_doc="IMPROVEMENT.md",
            target_section="What Worked",
            content="Short additive content under 500 chars",
            source_run_id="run_test123",
            confidence=0.8,
            source_stage="reflect",
        )

    @pytest.fixture
    def project_dir(self, tmp_path):
        """Project dir with DDD docs containing maturity annotations."""
        improvement = tmp_path / "IMPROVEMENT.md"
        improvement.write_text(
            "# Improvements\n\n"
            "## What Worked\n"
            "<!-- maturity: growing | sources: 5 | verified: true -->\n"
            "- Previous entry\n\n"
            "## What Failed\n"
            "<!-- maturity: sparse | sources: 1 | verified: false -->\n"
            "- Old failure\n"
        )
        tech = tmp_path / "TECH.md"
        tech.write_text("# Tech\n\n## Runtime Traps\n<!-- maturity: growing -->\n- trap1\n")
        # Create artifacts dir for changelog
        (tmp_path / ".artifacts").mkdir()
        return tmp_path

    def test_safe_proposal_auto_approved(self, proposal, project_dir):
        """Proposal meeting all 6 criteria is approved."""
        from core.ddd_auto_approval import evaluate_auto_approval
        decision = evaluate_auto_approval(proposal, project_dir)
        assert decision.approved is True

    def test_product_md_never_approved(self, proposal, project_dir):
        """PRODUCT.md proposals are NEVER auto-approved."""
        from core.ddd_auto_approval import evaluate_auto_approval
        proposal.target_doc = "PRODUCT.md"
        decision = evaluate_auto_approval(proposal, project_dir)
        assert decision.approved is False
        assert "safe_target_doc" in str(decision.reason)

    def test_project_md_never_approved(self, proposal, project_dir):
        """PROJECT.md proposals are NEVER auto-approved."""
        from core.ddd_auto_approval import evaluate_auto_approval
        proposal.target_doc = "PROJECT.md"
        decision = evaluate_auto_approval(proposal, project_dir)
        assert decision.approved is False

    def test_large_content_rejected(self, proposal, project_dir):
        """Content > 500 chars is NOT auto-approved."""
        from core.ddd_auto_approval import evaluate_auto_approval
        proposal.content = "x" * 501
        decision = evaluate_auto_approval(proposal, project_dir)
        assert decision.approved is False
        assert "small_magnitude" in str(decision.reason)

    def test_sparse_maturity_soft_gate(self, proposal, project_dir):
        """Sections with [sparse] maturity fail maturity check (soft gate)."""
        from core.ddd_auto_approval import evaluate_auto_approval
        proposal.target_section = "What Failed"  # sparse maturity in fixture
        decision = evaluate_auto_approval(proposal, project_dir)
        # Maturity is a soft gate — decision still reports criteria_met breakdown
        assert decision.criteria_met["maturity_growing"] is False
        # But overall approval depends on all criteria — with sparse maturity it's rejected
        assert decision.approved is False

    def test_circuit_breaker_disables_channel(self, proposal, project_dir):
        """3 reverts in 7 days from same source → auto-approval disabled."""
        from core.ddd_auto_approval import evaluate_auto_approval, record_revert

        # Record 3 reverts for this source
        for _ in range(3):
            record_revert(proposal.source_stage, project_dir)

        decision = evaluate_auto_approval(proposal, project_dir)
        assert decision.approved is False
        assert "circuit_breaker" in str(decision.reason)


# ── ObservationMiner tests ────────────────────────────────────────────────


class TestObservationMiner:
    """Pattern extraction from observation data."""

    def test_retry_detected(self):
        """Same tool+intent 3+ times with errors → RETRY pattern."""
        from core.observation_miner import ObservationMiner
        from core.observation_ring import Observation

        observations = []
        # 4 retries of same command (3 error + 1 success)
        for i in range(4):
            obs = Observation(
                ts=time.monotonic() + i,
                tool_name="Bash",
                intent="$ pytest tests/test_foo.py",
                files=["tests/test_foo.py"],
                completed=True,
                result_status="error" if i < 3 else "success",
                duration_ms=2000,
            )
            observations.append(obs)
        # Add filler to meet min 5 observations threshold
        for i in range(3):
            observations.append(Observation(
                ts=time.monotonic() + 10 + i, tool_name="Read",
                intent=f"Read: /filler{i}.py", files=[], completed=True,
                result_status="success", duration_ms=50,
            ))

        miner = ObservationMiner()
        patterns = miner.mine(observations)
        retry_patterns = [p for p in patterns if p.type == "RETRY"]
        assert len(retry_patterns) >= 1
        assert retry_patterns[0].confidence >= 0.7

    def test_long_tool_detected(self):
        """Tool call >30s → LONG_TOOL pattern."""
        from core.observation_miner import ObservationMiner
        from core.observation_ring import Observation

        observations = [
            Observation(
                ts=time.monotonic(),
                tool_name="Bash",
                intent="$ pytest --timeout=60",
                files=[],
                completed=True,
                result_status="success",
                duration_ms=45_000,  # 45 seconds
            ),
            # Need at least 5 observations for mine() to run
            *[Observation(ts=time.monotonic() + i, tool_name="Read", intent=f"Read: /f{i}.py",
                          files=[], completed=True, result_status="success", duration_ms=50)
              for i in range(5)],
        ]

        miner = ObservationMiner()
        patterns = miner.mine(observations)
        long_patterns = [p for p in patterns if p.type == "LONG_TOOL"]
        assert len(long_patterns) >= 1
        assert "45.0s" in long_patterns[0].description

    def test_no_patterns_below_threshold(self):
        """Normal diverse tool calls produce no patterns."""
        from core.observation_miner import ObservationMiner
        from core.observation_ring import Observation

        observations = [
            Observation(ts=time.monotonic() + i, tool_name=tool, intent=f"{tool}: /f{i}.py",
                        files=[f"/f{i}.py"], completed=True, result_status="success", duration_ms=100)
            for i, tool in enumerate(["Read", "Edit", "Bash", "Grep", "Read", "Write"])
        ]

        miner = ObservationMiner()
        patterns = miner.mine(observations)
        # No retries (all different), no long tools (all 100ms)
        assert len(patterns) == 0

    def test_mine_empty_observations(self):
        """Less than 5 observations → empty result (no crash)."""
        from core.observation_miner import ObservationMiner

        miner = ObservationMiner()
        assert miner.mine([]) == []
        assert miner.mine([MagicMock()] * 3) == []

    def test_patterns_jsonl_format(self, tmp_path):
        """Miner output can be serialized to JSONL."""
        from core.observation_miner import Pattern

        pattern = Pattern(
            type="RETRY",
            confidence=0.85,
            description="Stuck: pytest test_foo.py failed 4x",
            tool="Bash",
            session_id="test-session",
        )
        jsonl_path = tmp_path / "patterns.jsonl"
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(pattern.to_dict()) + "\n")

        # Verify readable
        with open(jsonl_path) as f:
            data = json.loads(f.readline())
        assert data["type"] == "RETRY"
        assert data["confidence"] == 0.85
