"""Tests for evolution/governance_miner.py — L1 governance pattern mining.

Tests that the miner correctly:
1. Parses EVOLUTION.md correction classes with occurrence counts
2. Identifies patterns that meet the 3+ recurrence threshold
3. Generates governance proposals (never auto-writes to SOUL/AGENT/STEERING)
4. Skips patterns that already have corresponding STEERING/AGENT rules
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def evolution_md(tmp_path: Path) -> Path:
    """Create a realistic EVOLUTION.md with correction classes."""
    content = textwrap.dedent("""\
        # SwarmAI Evolution Registry

        ## Corrections Captured

        ### CLASS A: Confidence → Skip Process [12 occurrences, SHIP-UNTESTED VARIANT]
        - **Chain**: C011 (04-25) → C021 (05-09) → C025 (05-15) → C037 (06-17)
        - **Pattern**: "I know this well enough to skip the prescribed workflow."
        - **Structural fix**: P5 (SOUL), STEERING R13 (adversarial non-negotiable)
        - **Status**: 12th occurrence.

        ### CLASS B: Symptom-Level Fix / Inference Without Verification [5 entries]
        - **C034** (05-29): Didn't know own runtime environment.
        - **C033** (05-26): Coded against 3 non-existent internal APIs.
        - **C031** (05-20): Shell patching instead of architectural fix.

        ### CLASS C: Shallow/Wrong-Layer Execution [3 entries]
        - **C024** (05-14): README-level research declared done.
        - **C022** (05-10): Backend fix for frontend problem.
        - **C020** (05-08): Combined extract + extend in one commit.

        ### Standalone
        - **C027** (05-19): Satisficing.
        - **C018** (05-05): Shallow insights = data restating.

        ## Governance Candidates

        | ID | Proposed Rule | Evidence | Count | Since |
        |----|--------------|----------|-------|-------|
        | GC05 | "Blocking >1s in async → dedicated executor" | O020, O006, pool exhaustion | 1/3 | 2026-05-20 |
        | GC10 | "Non-terminal states awaiting external input must have force-recovery" | O024, COE10, COE06 | 2/3 | 2026-05-28 |
        | GC19 | "Crash/recovery path no blanket except" | COE10, COE06, GUI46 | 3/3 | 2026-06-17 |
        | GC12 | "Pipeline profile IMMUTABLE" | C036 | ✅ PROMOTED | 2026-06-01 |

        ## Competence Learned
        ### K001
        - SSE Streaming Pipeline
    """)
    evo_path = tmp_path / ".context" / "EVOLUTION.md"
    evo_path.parent.mkdir(parents=True)
    evo_path.write_text(content, encoding="utf-8")
    return evo_path


@pytest.fixture
def steering_md(tmp_path: Path) -> Path:
    """Create a STEERING.md with existing rules to test dedup."""
    content = textwrap.dedent("""\
        ## Standing Rules

        ### 13. Adversarial Review Non-Negotiable (ALL code paths)
        ANY code change MUST spawn adversarial sub-agent BEFORE commit.
    """)
    steering_path = tmp_path / ".context" / "STEERING.md"
    steering_path.parent.mkdir(parents=True, exist_ok=True)
    steering_path.write_text(content, encoding="utf-8")
    return steering_path


class TestMineCorrections:
    """Test parsing of EVOLUTION.md correction classes."""

    def test_extracts_classes_with_counts(self, evolution_md: Path):
        from core.evolution.governance_miner import mine_correction_classes

        classes = mine_correction_classes(evolution_md)
        assert len(classes) >= 3
        # CLASS A should have count 12
        class_a = next(c for c in classes if "CLASS A" in c.name)
        assert class_a.occurrence_count == 12
        # CLASS B should have count 5
        class_b = next(c for c in classes if "CLASS B" in c.name)
        assert class_b.occurrence_count == 5
        # CLASS C should have count 3
        class_c = next(c for c in classes if "CLASS C" in c.name)
        assert class_c.occurrence_count == 3

    def test_skips_standalone_corrections(self, evolution_md: Path):
        from core.evolution.governance_miner import mine_correction_classes

        classes = mine_correction_classes(evolution_md)
        names = [c.name for c in classes]
        assert not any("Standalone" in n for n in names)

    def test_extracts_pattern_description(self, evolution_md: Path):
        from core.evolution.governance_miner import mine_correction_classes

        classes = mine_correction_classes(evolution_md)
        class_a = next(c for c in classes if "CLASS A" in c.name)
        assert "skip" in class_a.pattern.lower() or "process" in class_a.pattern.lower()


class TestMineGCCandidates:
    """Test parsing of Governance Candidates table."""

    def test_extracts_gc_with_evidence_count(self, evolution_md: Path):
        from core.evolution.governance_miner import mine_gc_candidates

        candidates = mine_gc_candidates(evolution_md)
        # GC19 has 3/3 evidence — should be included
        assert any(c.gc_id == "GC19" for c in candidates)

    def test_skips_promoted_gc(self, evolution_md: Path):
        from core.evolution.governance_miner import mine_gc_candidates

        candidates = mine_gc_candidates(evolution_md)
        # GC12 is already PROMOTED — should be excluded
        assert not any(c.gc_id == "GC12" for c in candidates)

    def test_skips_low_evidence_gc(self, evolution_md: Path):
        from core.evolution.governance_miner import mine_gc_candidates

        candidates = mine_gc_candidates(evolution_md)
        # GC05 has 1/3 — below threshold
        assert not any(c.gc_id == "GC05" for c in candidates)


class TestGovernanceProposalGeneration:
    """Test that proposals are generated correctly."""

    def test_generates_proposals_from_mature_patterns(self, evolution_md: Path, steering_md: Path):
        from core.evolution.governance_miner import generate_governance_proposals

        proposals = generate_governance_proposals(
            evolution_md, steering_md, threshold=3
        )
        # Should have proposals (CLASS B has 5, CLASS C has 3, GC19 has 3/3)
        assert len(proposals) >= 1

    def test_proposal_has_required_fields(self, evolution_md: Path, steering_md: Path):
        from core.evolution.governance_miner import generate_governance_proposals

        proposals = generate_governance_proposals(
            evolution_md, steering_md, threshold=3
        )
        for p in proposals:
            assert p.target == "governance"
            assert p.source_class or p.gc_id
            assert p.occurrence_count >= 3
            assert p.proposed_rule  # non-empty
            assert p.evidence  # non-empty list

    def test_skips_classes_already_ruled(self, evolution_md: Path, steering_md: Path):
        """CLASS A mentions STEERING R13 as fix — should be excluded."""
        from core.evolution.governance_miner import generate_governance_proposals

        proposals = generate_governance_proposals(
            evolution_md, steering_md, threshold=3
        )
        # CLASS A already has "STEERING R13" structural fix — should be filtered
        assert not any(
            p.source_class and "CLASS A" in p.source_class
            for p in proposals
        )

    def test_axis_guard_excludes_non_cognitive_class(self, evolution_md, steering_md, monkeypatch):
        """Defense-in-depth (run_685db747 Gate-2 MED): even if mine_correction_classes
        yields a non-cognitive class (e.g. a future regex loosening lets OPERATIONAL
        through), generate_governance_proposals must NOT emit a proposal for it.
        Forces the guard to fire so it isn't dead code (R28)."""
        import core.evolution.governance_miner as gm
        from core.evolution.governance_miner import (
            CorrectionClass,
            generate_governance_proposals,
        )

        cognitive = CorrectionClass(
            name="CLASS B: Symptom fix", occurrence_count=5,
            pattern="does X", structural_fix="", evidence_chain=["e1"],
        )
        non_cognitive = CorrectionClass(
            name="CLASS OPERATIONAL: tool noise", occurrence_count=99,
            pattern="op noise", structural_fix="", evidence_chain=["e2"],
        )
        # canonical_class_key("CLASS OPERATIONAL: ...") is "CLASS_OPERATIONAL" which
        # would BE cognitive by prefix — so force the raw non-cognitive label the
        # guard actually rejects, proving the guard fires on a real non-cognitive key.
        non_cognitive.name = "OPERATIONAL"
        monkeypatch.setattr(gm, "mine_correction_classes", lambda _p: [cognitive, non_cognitive])
        monkeypatch.setattr(gm, "mine_gc_candidates", lambda _p: [])

        proposals = generate_governance_proposals(evolution_md, steering_md, threshold=3)
        classes = {p.source_class for p in proposals}
        assert "OPERATIONAL" not in classes  # guard fired
        assert any("CLASS_B" == c or "CLASS B" in (c or "") for c in classes)  # cognitive kept

    def test_skips_class_with_empty_pattern(self, evolution_md, steering_md, monkeypatch):
        """Admission root-fix (run_97519f7c): a correction class with NO real rule
        text (empty pattern) must NOT emit a contentless 'Address recurring X'
        meta-instruction proposal — it is not approvable, pure queue noise."""
        import core.evolution.governance_miner as gm
        from core.evolution.governance_miner import (
            CorrectionClass,
            generate_governance_proposals,
        )

        with_text = CorrectionClass(
            name="CLASS B: real", occurrence_count=5,
            pattern="Any runtime claim must cite an observation", structural_fix="",
            evidence_chain=["e1"],
        )
        empty_pattern = CorrectionClass(
            name="CLASS C: no rule text", occurrence_count=9,
            pattern="", structural_fix="", evidence_chain=["e2"],
        )
        monkeypatch.setattr(gm, "mine_correction_classes", lambda _p: [with_text, empty_pattern])
        monkeypatch.setattr(gm, "mine_gc_candidates", lambda _p: [])

        proposals = generate_governance_proposals(evolution_md, steering_md, threshold=3)
        rules = [p.proposed_rule for p in proposals]
        # the empty-pattern class produced NO proposal (no Address-recurring placeholder)
        assert not any("Address recurring" in (r or "") for r in rules)
        assert any("must cite an observation" in (r or "") for r in rules)  # real one kept

    def test_empty_evolution_md_returns_empty(self, tmp_path: Path):
        from core.evolution.governance_miner import generate_governance_proposals

        empty_evo = tmp_path / ".context" / "EVOLUTION.md"
        empty_evo.parent.mkdir(parents=True, exist_ok=True)
        empty_evo.write_text("# Empty\n", encoding="utf-8")
        empty_steering = tmp_path / ".context" / "STEERING.md"
        empty_steering.write_text("# Empty\n", encoding="utf-8")

        proposals = generate_governance_proposals(empty_evo, empty_steering)
        assert proposals == []

    def test_never_writes_to_governance_files(self, evolution_md: Path, steering_md: Path):
        """Core safety invariant: proposals are data, never file writes."""
        from core.evolution.governance_miner import generate_governance_proposals

        steering_before = steering_md.read_text()
        generate_governance_proposals(evolution_md, steering_md, threshold=3)
        assert steering_md.read_text() == steering_before


class TestProposalSerialization:
    """Test that proposals serialize to the format expected by session briefing."""

    def test_to_dict_matches_evolution_proposal_format(self, evolution_md: Path, steering_md: Path):
        from core.evolution.governance_miner import generate_governance_proposals

        proposals = generate_governance_proposals(
            evolution_md, steering_md, threshold=3
        )
        if not proposals:
            pytest.skip("No proposals generated")

        d = proposals[0].to_proposal_dict()
        assert d["target"] == "governance"
        assert "proposed_rule" in d
        assert "evidence" in d
        assert "confidence" in d
        assert isinstance(d["confidence"], float)
        # Must be JSON-serializable
        json.dumps(d)
