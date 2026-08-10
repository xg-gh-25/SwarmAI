"""Knowledge Admission — Component C: the decision band (AUTONOMY-FIRST, run_86f44f35).

admission_band(proposal, project_dir) -> "auto" | "review" | "discard".

XG directive (overrides run_8dea0dd5): the adversarial judge IS the authority. There is
NO protected zone — a judge-pass proposal auto-writes ANY doc incl SELF/PRODUCT/TECH.
  • inherited_gate2 (trust=passed)         → auto (any doc), subject to quality floor.
  • trust=n/a / failed  → run the self_adversarial judge:
        judge pass    → auto (any doc), subject to quality floor.
        judge non-pass (suspect OR noise)  → DISCARD (recoverable archive), NEVER review.
  • is_noise (structural) → discard before any work.
Human-review queue → 0: admission_band no longer returns "review" for a trust/judge
outcome (only a genuine gate ERROR fails closed to review — DEC19).
"""
import sys
from pathlib import Path
from unittest.mock import patch

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


def _judge(verdict):
    import core.ddd_cultivation as dc
    return patch.object(dc, "self_adversarial_judge", lambda *a, **k: (verdict, "t"))


def _clean_quality():
    # stub the reused quality gate (magnitude/circuit) clean so the band reaches its verdict
    import unittest.mock as m
    return m.patch("core.ddd_auto_approval.evaluate_auto_approval")


class TestAdmissionBandAutonomyFirst:
    def _fn(self):
        from core.ddd_cultivation import admission_band
        return admission_band

    def test_noise_is_discard(self):
        band = self._fn()
        assert band(_prop(content="exit_code: 0"), None)[0] == "discard"

    def test_inherited_gate2_auto_into_any_doc(self, tmp_path):
        band = self._fn()
        with _clean_quality() as mq:
            mq.return_value = type("D", (), {"criteria_met": {"small_magnitude": True, "circuit_breaker_ok": True}})()
            for doc, sec in [("TECH.md", "Architecture"), ("SELF.md", "What I Am"),
                             ("PRODUCT.md", "Vision")]:
                p = _prop(passed_adversarial_gate="passed", trust_source="inherited_gate2",
                          target_doc=doc, target_section=sec)
                assert band(p, tmp_path)[0] == "auto", f"{doc}>{sec} inherited_gate2 must auto"

    def test_judge_pass_auto_into_formerly_protected_doc(self, tmp_path):
        # THE directive: judge-pass (trust=n/a) auto-writes SELF/PRODUCT/TECH — NO protected zone.
        band = self._fn()
        with _judge("pass"), _clean_quality() as mq:
            mq.return_value = type("D", (), {"criteria_met": {"small_magnitude": True, "circuit_breaker_ok": True}})()
            for doc, sec in [("SELF.md", "What I Am"), ("PRODUCT.md", "Vision"),
                             ("TECH.md", "Architecture")]:
                p = _prop(passed_adversarial_gate="n/a", target_doc=doc, target_section=sec)
                assert band(p, tmp_path)[0] == "auto", f"judge-pass must auto into {doc}>{sec}"

    def test_judge_suspect_is_DISCARD_not_review(self, tmp_path):
        band = self._fn()
        with _judge("suspect"):
            assert band(_prop(passed_adversarial_gate="n/a"), tmp_path)[0] == "discard"

    def test_judge_noise_is_discard(self, tmp_path):
        band = self._fn()
        with _judge("noise"):
            assert band(_prop(passed_adversarial_gate="n/a"), tmp_path)[0] == "discard"

    def test_no_review_verdict_for_trust_outcomes(self, tmp_path):
        # human-review → 0: neither n/a+suspect, n/a+noise, nor failed yields "review".
        band = self._fn()
        for gate, jverdict in [("n/a", "suspect"), ("n/a", "noise"), ("failed", "suspect")]:
            with _judge(jverdict):
                v = band(_prop(passed_adversarial_gate=gate), tmp_path)[0]
                assert v != "review", f"{gate}+{jverdict} must not be review (got {v})"

    def test_gate_error_still_fails_closed_to_review(self, tmp_path):
        # the ONE remaining review path: a genuine quality-gate exception (DEC19 fail-closed).
        band = self._fn()
        with _judge("pass"), _clean_quality() as mq:
            mq.side_effect = RuntimeError("gate boom")
            v = band(_prop(passed_adversarial_gate="n/a"), tmp_path)[0]
            assert v == "review", "a gate ERROR still fails closed to review"
