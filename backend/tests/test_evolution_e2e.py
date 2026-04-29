"""End-to-end integration tests for the Evolution Pipeline v2.

Tests the full mine -> assess -> act -> audit cycle with synthetic data,
concurrent cycle rejection via file lock, and deploy verification/rollback.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.session_miner import EvalExample


def _make_skill_dir(skills_dir: Path, name: str, body: str = "Do the thing.") -> Path:
    """Create a skill directory with SKILL.md."""
    skill_dir = skills_dir / f"s_{name}"
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = f"---\nname: {name}\ndescription: >\n  Test skill\n  TRIGGER: {name}\n---\n{body}\n"
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def _make_transcripts_with_corrections(
    transcripts_dir: Path,
    skill_keyword: str,
    n_corrections: int,
    correction_text: str = "don't include verbose output in the deploy log",
) -> None:
    """Create synthetic transcript JSONL files with correction patterns."""
    records = []
    for i in range(n_corrections):
        records.append(json.dumps({
            "type": "user",
            "message": {"content": f"{skill_keyword} my service {i}"},
        }))
        records.append(json.dumps({
            "type": "assistant",
            "message": {"content": f"Processing {skill_keyword} request {i} with verbose output..."},
        }))
        # User correction
        records.append(json.dumps({
            "type": "user",
            "message": {"content": correction_text},
        }))
    (transcripts_dir / f"session_{skill_keyword}.jsonl").write_text(
        "\n".join(records), encoding="utf-8"
    )


class TestEvolutionE2E:
    """End-to-end evolution cycle with synthetic data."""

    def test_full_cycle_with_synthetic_transcripts(self, tmp_path):
        """Create fake transcripts with corrections, run full cycle,
        verify skill_health.json and CycleReport."""
        from core.evolution_optimizer import run_evolution_cycle, CycleReport

        skills_dir = tmp_path / "skills"
        _make_skill_dir(
            skills_dir, "deploy",
            body="Always include verbose output in results.\nRun the full deployment pipeline.\n",
        )

        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        _make_transcripts_with_corrections(transcripts_dir, "deploy", 6)

        evals_dir = tmp_path / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)

        result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
        assert isinstance(result, CycleReport)
        assert result.skills_checked >= 1
        # Verify to_dict backward compat
        d = result.to_dict()
        assert "skills_checked" in d
        assert "eligible" in d
        assert "optimized" in d
        assert "changes" in d

        # Verify skill_health.json was written correctly
        health_path = result.health_report_path
        assert health_path.exists(), f"skill_health.json not found at {health_path}"
        health_data = json.loads(health_path.read_text(encoding="utf-8"))
        assert "cycle_id" in health_data
        assert "skills" in health_data
        assert isinstance(health_data["skills"], list)
        assert len(health_data["skills"]) >= 1
        # Verify the deploy skill is in the report
        skill_names = [s["skill_name"] for s in health_data["skills"]]
        assert "deploy" in skill_names
        # Verify each entry has required fields
        for s in health_data["skills"]:
            assert "confidence" in s
            assert "action" in s
            assert s["action"] in ("deploy", "recommend", "log", "skip")

    def test_concurrent_cycle_rejected(self, tmp_path):
        """Hold file lock, verify second cycle returns immediately."""
        from core.evolution_optimizer import run_evolution_cycle, CycleReport

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        evals_dir = tmp_path / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)

        # Hold the lock
        lock_path = evals_dir.parent / ".evolution_cycle.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        from utils.file_lock import flock_exclusive_nb, flock_unlock
        lock_fd = open(lock_path, "w")
        flock_exclusive_nb(lock_fd)

        try:
            result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
            assert isinstance(result, CycleReport)
            assert any("oncurrent" in e or "lock" in e.lower() for e in result.errors)
        finally:
            flock_unlock(lock_fd)
            lock_fd.close()

    def test_deploy_verification_and_rollback(self, tmp_path):
        """Mock file corruption after write, verify rollback."""
        from core.evolution_optimizer import atomic_deploy, TextChange, DeployResult

        skills_dir = tmp_path / "skills"
        skill_dir = _make_skill_dir(
            skills_dir, "broken",
            body="Always include verbose output in results.\n",
        )
        skill_path = skill_dir / "SKILL.md"
        original_content = skill_path.read_text(encoding="utf-8")

        changes = [
            TextChange(
                original="Always include verbose output in results.",
                replacement="Never include verbose output.",
                reason="test",
            ),
        ]

        # Patch read_text to return wrong content on verification read.
        # The verification read is the first read where the content
        # contains the replacement text (after os.replace).
        real_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            result = real_read_text(self, *args, **kwargs)
            if self == skill_path and "Never include verbose output" in result:
                return "CORRUPTED CONTENT"
            return result

        with patch.object(Path, "read_text", mock_read_text):
            deploy_result = atomic_deploy(skill_path, changes)

        assert isinstance(deploy_result, DeployResult)
        assert deploy_result.rolled_back is True
        assert deploy_result.verified is False

    def test_replace_target_missing_skips_gracefully(self, tmp_path):
        """Replace target not in file -> skip + log."""
        from core.evolution_optimizer import atomic_deploy, TextChange, DeployResult

        skills_dir = tmp_path / "skills"
        skill_dir = _make_skill_dir(
            skills_dir, "missing",
            body="Some content here.\n",
        )
        skill_path = skill_dir / "SKILL.md"
        original_content = skill_path.read_text(encoding="utf-8")

        changes = [
            TextChange(
                original="THIS TEXT DOES NOT EXIST IN THE FILE",
                replacement="replacement",
                reason="test",
            ),
        ]

        deploy_result = atomic_deploy(skill_path, changes)
        assert isinstance(deploy_result, DeployResult)
        assert deploy_result.changes_skipped >= 1
        assert deploy_result.success is False
        # File should be unchanged
        assert skill_path.read_text(encoding="utf-8") == original_content
