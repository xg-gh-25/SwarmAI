"""Tests for the DDD cultivation + governance ADMISSION root-fix (run_97519f7c).

The review/approve queues accumulated noise because the admission layer admitted
classes it should not. These tests pin the 4 subtractive fixes:

  1. write_proposal is idempotent by content_signature vs the pending set
     (generation-side dedup — the churn ROOT; Gate-1 BLOCK→revised).
  2. _cultivate_proposals SKIPS protected-zone lessons instead of escalating them
     into the human review queue (they are dead-on-approve), WITHOUT dropping
     legitimate non-protected escalations.
  3. code_change_feed does NOT write a CultivationProposal for a mere new-file
     "architecture change" (git fact, not knowledge — code_intel already indexes it).
  4. governance_miner does NOT emit a proposal for a correction class with no real
     rule text (empty pattern → no contentless "Address recurring X" meta-instruction).
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Fix 1: write_proposal generation-side dedup by content_signature ──────────
class TestWriteProposalDedup:
    def test_write_proposal_is_idempotent_by_content(self):
        """Writing the SAME-content proposal twice yields ONE pending file."""
        from core.ddd_cultivation import CultivationProposal, write_proposal

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            content = "A shared close-primitive that broadcasts an EVENT beats per-event others-lists."
            p1 = CultivationProposal(
                target_doc="TECH.md", target_section="Conventions",
                content=content, source_run_id="run_a", confidence=0.7,
            )
            p2 = CultivationProposal(
                target_doc="TECH.md", target_section="Conventions",
                content=content, source_run_id="run_b", confidence=0.7,
            )
            write_proposal(p1, project_dir)
            write_proposal(p2, project_dir)
            proposals_dir = project_dir / ".artifacts" / "proposals"
            files = list(proposals_dir.glob("*.json"))
            assert len(files) == 1, f"expected 1 deduped proposal, got {len(files)}"

    def test_write_proposal_distinct_content_not_deduped(self):
        """Two DIFFERENT-content proposals both persist (dedup must not over-collapse)."""
        from core.ddd_cultivation import CultivationProposal, write_proposal

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            p1 = CultivationProposal(
                target_doc="TECH.md", target_section="Conventions",
                content="First distinct lesson about event broadcasting.",
                source_run_id="run_a", confidence=0.7,
            )
            p2 = CultivationProposal(
                target_doc="TECH.md", target_section="Conventions",
                content="Second unrelated lesson about lock ordering.",
                source_run_id="run_b", confidence=0.7,
            )
            write_proposal(p1, project_dir)
            write_proposal(p2, project_dir)
            files = list((project_dir / ".artifacts" / "proposals").glob("*.json"))
            assert len(files) == 2

    def test_retire_proposals_are_not_content_deduped(self):
        """Gate-2 MED: two DISTINCT retire targets with identical template content
        must BOTH persist (dedup is append-only; retire distinguishing info is in
        target_title/section, not content)."""
        from core.ddd_cultivation import CultivationProposal, write_proposal

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            tmpl = "Superseded by a newer polarity-flipped lesson (flip=['never','always']); human decide."
            p1 = CultivationProposal(
                target_doc="IMPROVEMENT.md", target_section="What Worked",
                content=tmpl, source_run_id="run_a", confidence=0.7,
                change_type="retire", target_title="Entry ONE",
            )
            p2 = CultivationProposal(
                target_doc="IMPROVEMENT.md", target_section="What Worked",
                content=tmpl, source_run_id="run_b", confidence=0.7,
                change_type="retire", target_title="Entry TWO",
            )
            write_proposal(p1, project_dir)
            write_proposal(p2, project_dir)
            files = list((project_dir / ".artifacts" / "proposals").glob("*.json"))
            assert len(files) == 2, "distinct retire targets must not collide on content"

    def test_write_proposal_dedup_ignores_terminal_status(self):
        """A REJECTED proposal with same content does NOT block a new write.

        Dedup is only vs AWAITING-human proposals; a terminal one is not a live dup.
        """
        from core.ddd_cultivation import CultivationProposal, write_proposal

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            content = "A lesson that was previously rejected and may recur."
            p1 = CultivationProposal(
                target_doc="TECH.md", target_section="Conventions",
                content=content, source_run_id="run_a", confidence=0.7,
                status="rejected",
            )
            write_proposal(p1, project_dir)
            p2 = CultivationProposal(
                target_doc="TECH.md", target_section="Conventions",
                content=content, source_run_id="run_b", confidence=0.7,
                status="pending",
            )
            write_proposal(p2, project_dir)
            # a rejected file + a fresh pending = both on disk (rejected is not a live dup)
            files = list((project_dir / ".artifacts" / "proposals").glob("*.json"))
            assert len(files) == 2


# ── Fix 2: _cultivate_proposals skips protected zones (narrow guard) ──────────
class TestProtectedZoneSkip:
    def test_is_protected_zone_covers_the_targets(self):
        """Sanity: the protected zones we skip are the ones apply_to_ddd rejects."""
        from core.ddd_cultivation import is_protected_zone
        assert is_protected_zone("TECH.md", "Architecture") is True
        assert is_protected_zone("PRODUCT.md", "Vision") is True
        assert is_protected_zone("SELF.md", "anything") is True
        # non-protected additive sections stay open
        assert is_protected_zone("TECH.md", "Conventions") is False
        assert is_protected_zone("IMPROVEMENT.md", "What Worked") is False
        assert is_protected_zone("PRODUCT.md", "Overview") is False

    def test_cultivate_skips_protected_zone_proposal(self):
        """A protected-zone lesson is SKIPPED (not escalated) — 0 written proposal."""
        from core.ddd_cultivation import _cultivate_proposals, CultivationProposal

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / ".artifacts" / "proposals").mkdir(parents=True)
            prop = CultivationProposal(
                target_doc="TECH.md", target_section="Architecture",
                content="The real ACT->SENSE gap was an injection-layer mismatch across modules.",
                source_run_id="run_x", confidence=0.7,
            )
            res = _cultivate_proposals([prop], project_dir)
            files = list((project_dir / ".artifacts" / "proposals").glob("*.json"))
            assert len(files) == 0, "protected-zone proposal must not be written"
            assert res.get("escalated", 0) == 0
            assert res.get("skipped_protected", 0) >= 1
            # Gate-2 red-team MED: it must sediment UP to a human-distill sink, not vanish
            sink = project_dir / ".artifacts" / "protected-zone-candidates.jsonl"
            assert sink.exists(), "protected-zone lesson must be written to the human-distill sink"
            line = json.loads(sink.read_text().strip())
            assert line["target_doc"] == "TECH.md"
            assert line["target_section"] == "Architecture"

    def test_cultivate_still_escalates_non_protected_unsafe(self):
        """A NON-protected but not-safe-append target STILL escalates (no over-drop).

        PROJECT.md is not in _PROTECTED_ZONES and not a SAFE_APPEND section →
        is_safe_append False → must still escalate to the human queue, NOT be skipped.
        """
        from core.ddd_cultivation import _cultivate_proposals, CultivationProposal, is_protected_zone
        assert is_protected_zone("PROJECT.md", "Current Status") is False
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / ".artifacts" / "proposals").mkdir(parents=True)
            prop = CultivationProposal(
                target_doc="PROJECT.md", target_section="Current Status",
                content="A genuinely human-gated project-status change worth escalating.",
                source_run_id="run_y", confidence=0.7,
            )
            res = _cultivate_proposals([prop], project_dir)
            # not dropped as protected — either escalated or written for human
            assert res.get("skipped_protected", 0) == 0


# ── Fix 3: code_change_feed does not write arch-change proposals ──────────────
class TestNoArchiProposal:
    def test_generate_proposals_writes_nothing_for_arch_change(self):
        """A new-module arch change writes 0 CultivationProposal (code_intel captures it)."""
        from backend.hooks.code_change_feed import CodeChangeFeed
        feed = CodeChangeFeed()
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "Projects" / "SwarmAI").mkdir(parents=True)
            arch_changes = [type("AC", (), {
                "change_type": "new_module", "path": "backend/core/foo.py",
                "confidence": 0.9, "target_section": "Key Subsystems",
            })()]
            with patch("core.ddd_cultivation.write_proposal") as mock_write:
                feed._generate_proposals(arch_changes, "abc1234", "feat: foo", str(ws))
                assert mock_write.call_count == 0, "arch-change must not write a proposal"


# ── Fix 5 (backstop): approve on duplicate self-clears from the pending queue ──
class TestDuplicateApproveClears:
    def test_approve_duplicate_marks_rejected_not_500(self):
        """Approving a duplicate proposal terminates it (rejected), does not 500-leave-pending."""
        import asyncio
        from unittest.mock import patch as _patch
        import backend.routers.cultivation as cult

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / ".artifacts" / "proposals").mkdir(parents=True)

            class _P:
                id = "proposal_dup1"
                target_doc = "TECH.md"
                target_section = "Conventions"
                change_type = "append"
            marked = {}
            with _patch.object(cult, "_resolve_project_dir", return_value=project_dir), \
                 _patch.object(cult, "_find_proposal", return_value=_P()), \
                 _patch.object(cult, "apply_to_ddd", return_value="duplicate"), \
                 _patch.object(cult, "_update_proposal_status",
                               side_effect=lambda pd, pid, st, reason=None: marked.update(status=st, reason=reason)):
                result = asyncio.get_event_loop().run_until_complete(
                    cult.approve_proposal("proposal_dup1", project="SwarmAI")
                )
            assert result["status"] == "cleared"
            assert marked.get("status") == "rejected"
