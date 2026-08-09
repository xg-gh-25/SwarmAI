"""Knowledge Admission — Component C: the decision band (trust is the SOLE authority).

admission_band(proposal, project_dir) -> "auto" | "review" | "discard".
Trust REPLACES the hardcoded doc-whitelist: a trust=passed proposal that also
passes the reused quality checks may AUTO-apply into ANY doc (incl SELF.md,
PROJECT.md, PRODUCT.md/Vision). trust!=passed → review. is_noise → discard.
Any gate error → review (fail-closed). (Design AC4/AC11.)
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _prop(**kw):
    from core.ddd_cultivation import CultivationProposal
    base = dict(
        target_doc="TECH.md", target_section="Architecture",
        content="A silent race drops a bubble when two writes interleave; a recurring "
                "bug whenever a fallback path is added without a client_id.",
        source_run_id="run_x", confidence=0.7,
    )
    base.update(kw)
    return CultivationProposal(**base)


class TestAdmissionBand:
    def _fn(self):
        from core.ddd_cultivation import admission_band
        return admission_band

    def test_noise_is_discard(self):
        band = self._fn()
        p = _prop(content="exit_code: 0")
        assert band(p, None)[0] == "discard"

    def test_trust_passed_into_formerly_locked_zone_is_auto(self, tmp_path):
        # AC11: trust=passed → AUTO even into TECH.md/Architecture (was _PROTECTED_ZONES).
        # (quality checks stubbed to clean via a mature section + small content)
        band = self._fn()
        p = _prop(passed_adversarial_gate="passed", target_doc="TECH.md",
                  target_section="Architecture")
        verdict, _reason = band(p, tmp_path)
        assert verdict == "auto", f"trust=passed should auto, got {verdict}"

    def test_trust_passed_into_self_md_is_auto(self, tmp_path):
        # AC11: NO doc carved out — SELF.md is auto-eligible at trust=passed.
        band = self._fn()
        p = _prop(passed_adversarial_gate="passed", target_doc="SELF.md",
                  target_section="What I Am")
        verdict, _ = band(p, tmp_path)
        assert verdict == "auto"

    def test_trust_na_is_review_not_auto(self, tmp_path):
        # the fail-closed floor: un-vetted source → review, never auto.
        band = self._fn()
        p = _prop(passed_adversarial_gate="n/a", target_doc="TECH.md",
                  target_section="Architecture")
        assert band(p, tmp_path)[0] == "review"

    def test_trust_failed_is_review(self, tmp_path):
        band = self._fn()
        p = _prop(passed_adversarial_gate="failed")
        assert band(p, tmp_path)[0] == "review"

    def test_trust_is_the_gate_not_the_doc(self, tmp_path):
        # the mutation that proves trust (not doc) decides: same proposal, flip trust.
        band = self._fn()
        auto_p = _prop(passed_adversarial_gate="passed", target_doc="PROJECT.md",
                       target_section="Recent Decisions")
        na_p = _prop(passed_adversarial_gate="n/a", target_doc="PROJECT.md",
                     target_section="Recent Decisions")
        assert band(auto_p, tmp_path)[0] == "auto"
        assert band(na_p, tmp_path)[0] == "review"
