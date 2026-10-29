"""Tests for ObservationMiner: RETRY + LONG_TOOL patterns from observation data."""
import json
import time
from unittest.mock import MagicMock



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
