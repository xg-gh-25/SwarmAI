"""Tests for jobs.handlers.skill_proposer — L4.1 autonomous skill proposals.

Tests: gap filtering, skill dedup, proposal output, integration gates.
"""
import json
from pathlib import Path

import pytest

from jobs.handlers.skill_proposer import (
    run_skill_proposer,
    _filter_qualifying_gaps,
    _find_existing_skill,
    _write_skill_proposal,
    _load_gaps_from_findings,
    MIN_OCCURRENCES,
    MIN_CONFIDENCE,
)


@pytest.fixture
def ws_dir(tmp_path, monkeypatch):
    """Create minimal workspace + projects structure."""
    projects = tmp_path / "Projects" / "SwarmAI" / ".artifacts"
    projects.mkdir(parents=True)
    services = tmp_path / "Services" / "swarm-jobs"
    services.mkdir(parents=True)
    monkeypatch.setattr("jobs.handlers.skill_proposer.SWARMWS", tmp_path)
    monkeypatch.setattr("jobs.handlers.skill_proposer.PROJECTS_DIR", tmp_path / "Projects")
    return tmp_path


def _make_gap(pattern="test gap", occurrences=5, priority="high", action="build skill"):
    return {
        "pattern": pattern,
        "occurrences": occurrences,
        "priority": priority,
        "suggested_action": action,
        "evidence": ["session 1: failed at X", "session 2: failed at X again"],
    }


class TestFilterQualifyingGaps:
    def test_high_priority_high_occurrences_qualifies(self):
        gaps = [_make_gap()]
        result = _filter_qualifying_gaps(gaps)
        assert len(result) == 1

    def test_low_occurrences_rejected(self):
        gaps = [_make_gap(occurrences=1)]
        result = _filter_qualifying_gaps(gaps)
        assert len(result) == 0

    def test_low_priority_rejected(self):
        gaps = [_make_gap(priority="low")]
        result = _filter_qualifying_gaps(gaps)
        assert len(result) == 0

    def test_wrong_action_rejected(self):
        gaps = [_make_gap(action="add correction")]
        result = _filter_qualifying_gaps(gaps)
        assert len(result) == 0

    def test_sorted_by_occurrences(self):
        gaps = [_make_gap(pattern="A", occurrences=3), _make_gap(pattern="B", occurrences=10)]
        result = _filter_qualifying_gaps(gaps)
        assert result[0]["pattern"] == "B"

    def test_max_one_per_run(self):
        gaps = [_make_gap(f"gap-{i}") for i in range(5)]
        result = _filter_qualifying_gaps(gaps)
        assert len(result) == 1

    def test_critical_priority_qualifies(self):
        gaps = [_make_gap(priority="critical")]
        result = _filter_qualifying_gaps(gaps)
        assert len(result) == 1


class TestFindExistingSkill:
    def test_no_match_returns_none(self):
        gap = _make_gap(pattern="quantum entanglement debugging")
        # Will search real skills dir — unlikely to match quantum
        result = _find_existing_skill(gap)
        assert result is None

    def test_weather_gap_matches_weather_skill(self):
        gap = _make_gap(pattern="weather forecast temperature checking")
        result = _find_existing_skill(gap)
        assert result == "s_weather"

    def test_short_pattern_no_crash(self):
        gap = _make_gap(pattern="ab")
        result = _find_existing_skill(gap)
        assert result is None  # No keywords >3 chars


class TestWriteSkillProposal:
    def test_writes_skill_md_and_metadata(self, ws_dir):
        proposal = {
            "skill_name": "s_test-skill",
            "skill_md": "---\nname: test-skill\n---\n# Test\nDoes stuff.\n",
            "trigger_patterns": ["test this", "do the thing"],
            "confidence": 8,
            "reasoning": "Addresses recurring test failures",
        }
        gap = _make_gap()
        _write_skill_proposal("s_test-skill", proposal, gap)

        skill_dir = ws_dir / "Projects" / "SwarmAI" / ".artifacts" / "skill-proposals" / "s_test-skill"
        assert (skill_dir / "SKILL.md").exists()
        assert (skill_dir / "metadata.json").exists()

        skill_content = (skill_dir / "SKILL.md").read_text()
        assert "test-skill" in skill_content

        meta = json.loads((skill_dir / "metadata.json").read_text())
        assert meta["confidence"] == 8
        assert meta["gap_pattern"] == "test gap"
        assert meta["model"] == "us.anthropic.claude-opus-4-6-v1"


class TestLoadGapsFromFindings:
    def test_no_file_returns_empty(self, ws_dir):
        result = _load_gaps_from_findings()
        assert result == []

    def test_reads_gaps_from_file(self, ws_dir):
        findings = {
            "memory_health": {
                "capability_gaps": [
                    _make_gap("found gap"),
                ]
            }
        }
        findings_path = ws_dir / "Services" / "swarm-jobs" / "health_findings.json"
        findings_path.write_text(json.dumps(findings))
        result = _load_gaps_from_findings()
        assert len(result) == 1
        assert result[0]["pattern"] == "found gap"


class TestRunSkillProposer:
    def test_no_gaps_skips(self, ws_dir):
        result = run_skill_proposer(gaps=[])
        assert result["status"] == "skipped"

    def test_no_qualifying_gaps_skips(self, ws_dir):
        result = run_skill_proposer(gaps=[_make_gap(occurrences=1)])
        assert result["status"] == "skipped"
        assert "none qualify" in result["reason"]

    def test_existing_skill_match_skips(self, ws_dir):
        gap = _make_gap(pattern="weather forecast temperature checking")
        result = run_skill_proposer(gaps=[gap])
        assert result["status"] == "skipped"
        assert "s_weather" in result.get("reason", "")

    def test_dry_run_no_llm(self, ws_dir):
        gap = _make_gap(pattern="quantum entanglement debugging framework")
        result = run_skill_proposer(gaps=[gap], dry_run=True)
        assert result["status"] == "dry_run"
        assert result["would_propose"] is True
