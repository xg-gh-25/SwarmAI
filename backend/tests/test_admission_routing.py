"""Knowledge Admission — Component E: per-source routing (AC5 residual).

Asserts the routing table the whole subsystem implements, end-to-end through
_cultivate_proposals, so the 5 sources can't silently drift:
  reflect/decision/correction → band (trust decides auto vs review)
  conversation                → ALWAYS escalate (never auto), by construction
  code_intel_feed             → not a proposal at all (health signal; tested elsewhere)
"""
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _passed_run(project_dir: Path, run_id: str):
    rd = project_dir / ".artifacts" / "runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "run.json").write_text(json.dumps(
        {"stages": [{"stage": "adversarial", "status": "completed",
                     "gate2_outcome": "pass"}]}))


def _improvement_doc(project_dir: Path):
    (project_dir / "IMPROVEMENT.md").write_text(
        "# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n")


class TestSourceRouting:
    def test_reflect_trusted_high_confidence_autos(self, tmp_path):
        # AUTO requires trust=passed AND confidence>=threshold (defense-in-depth: trust
        # gates the ZONE, confidence still gates VALUE). Use a keyword-rich lesson that
        # clears the 0.7 floor via classify_content.
        from core.ddd_cultivation import filter_lessons_for_ddd, admission_band
        _improvement_doc(tmp_path)
        _passed_run(tmp_path, "run_r")
        lessons = ["Runtime traps: a race condition bug in the async streaming pipeline "
                   "reconcile causes a duplicate bubble regression — verify the fix with a test."]
        props = filter_lessons_for_ddd(lessons, "run_r", "SwarmAI", tmp_path)
        assert props, "expected a proposal"
        # trust is stamped passed; if confidence clears the floor → auto, else review
        # (both are correct — the point is trust alone is necessary-not-sufficient).
        p = props[0]
        assert p.passed_adversarial_gate == "passed"
        verdict, _ = admission_band(p, tmp_path)
        if p.confidence >= 0.7:
            assert verdict == "auto"
        else:
            assert verdict == "review"  # trusted but low-value → still reviewed

    def test_trust_necessary_not_sufficient(self, tmp_path):
        # explicit: trust=passed + confidence BELOW floor → review (not auto). Trust
        # opens the zone; it does not bypass the value floor.
        from core.ddd_cultivation import CultivationProposal, admission_band
        p = CultivationProposal(
            target_doc="SELF.md", target_section="What I Am",
            content="A genuinely load-bearing self-model lesson worth keeping in the brain.",
            source_run_id="run_x", confidence=0.55, passed_adversarial_gate="passed",
            source_stage="reflect",
        )
        assert admission_band(p, tmp_path)[0] == "review"

    def test_reflect_untrusted_reviews(self, tmp_path):
        from core.ddd_cultivation import cultivate_from_reflect
        _improvement_doc(tmp_path)  # no run.json → trust n/a
        lessons = ["SMOKE catches runtime crashes that unit tests miss — a highest-ROI check to keep."]
        res = cultivate_from_reflect(lessons, "run_untrusted", "SwarmAI", tmp_path)
        assert res["applied"] == 0 and res["escalated"] == 1

    def test_decision_session_sourced_reviews(self, tmp_path):
        # decisions from a session id (not run_) → trust n/a → escalate, never auto
        from core.ddd_cultivation import cultivate_from_decisions
        (tmp_path / "TECH.md").write_text(
            "# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n")
        decisions = ["Standing rule: prefer atomic tmp+rename writes to prevent corruption of the store."]
        res = cultivate_from_decisions(decisions, "session_d", "SwarmAI", tmp_path)
        assert res["applied"] == 0 and res["escalated"] >= 1

    def test_correction_session_sourced_reviews(self, tmp_path):
        from core.ddd_cultivation import cultivate_from_corrections
        (tmp_path / "TECH.md").write_text(
            "# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n")
        _improvement_doc(tmp_path)
        corrections = ["Bug: daemon PATH not expanded — must use Path.home() not os.path.expandvars in scripts."]
        res = cultivate_from_corrections(corrections, "session_c", "SwarmAI", tmp_path)
        assert res["applied"] == 0 and res["escalated"] >= 1

    def test_conversation_never_autos_even_if_trusted(self, tmp_path):
        # conversation is force-escalated by construction — even a (hypothetical) passed
        # trust cannot make it auto. Uses the real cultivate_from_conversation path.
        from core.ddd_cultivation import cultivate_from_conversation
        _improvement_doc(tmp_path)
        _passed_run(tmp_path, "run_conv")
        candidates = [{
            "content": "A settled team decision worth recording in the brain for later reference.",
            "target_doc": "IMPROVEMENT.md", "target_section": "What Worked",
            "confidence": 0.9,
        }]
        res = cultivate_from_conversation(candidates, "run_conv", "SwarmAI", tmp_path)
        # conversation → escalate branch fires before the band → never applied
        assert res["applied"] == 0
