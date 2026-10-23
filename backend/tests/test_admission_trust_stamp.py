"""Knowledge Admission Subsystem — step 0 + Component B: canonical adversarial
outcome + fail-closed trust stamp.

RED-first. The trust stamp is the SOLE authority that lets a proposal auto-write
into ANY doc (incl. SELF.md) — so its derivation must be deterministic and
fail-closed: only a Pipeline v1 run_audit.json that shows a completed, landed run
whose review experts ran and whose quality verdict is PASS earns pass/pass_with_fixes;
a BLOCK verdict is block; everything else (no audit, the retired pipeline's stage
records, a human override, no review, WARN) is n/a, NEVER passed.
(Design: 2026-08-09-knowledge-admission-subsystem-design.md, AC3/AC10.)
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def audit(verdict="PASS", findings=0, experts=1, status="completed", landing="landed"):
    """A v1 run_audit.json (core.run_protocol.audit.build) reduced to what trust reads."""
    return {"outcome": {"status": status, "landing": landing},
            "summary": {"quality": {"verdict": verdict, "findings_total": findings}},
            "details": {"experts": [{"expert": f"e{i}"} for i in range(experts)]}}


class TestGate2OutcomeDerivation:
    """Derive the canonical gate2 outcome from a v1 run audit, fail-closed."""

    def _fn(self):
        from core.ddd_cultivation import derive_gate2_outcome
        return derive_gate2_outcome

    def test_clean_reviewed_landed_run_is_pass(self):
        assert self._fn()(audit()) == "pass"

    def test_findings_all_closed_is_pass_with_fixes(self):
        assert self._fn()(audit(findings=2)) == "pass_with_fixes"

    def test_block_verdict_is_block(self):
        assert self._fn()(audit(verdict="BLOCK")) == "block"

    def test_warn_or_na_verdict_is_na(self):
        assert self._fn()(audit(verdict="WARN")) == "n/a"
        assert self._fn()(audit(verdict="N/A")) == "n/a"

    def test_human_override_is_not_a_review_pass(self):
        assert self._fn()(audit(landing="human_override")) == "n/a"

    def test_run_without_review_experts_is_na(self):
        # a small change v1 sent no expert: nothing adversarial cleared it
        assert self._fn()(audit(experts=0)) == "n/a"

    def test_unfinished_run_is_na(self):
        assert self._fn()(audit(status="active")) == "n/a"

    def test_retired_pipeline_stage_records_are_not_trusted(self):
        old = {"stages": [{"stage": "adversarial", "status": "completed",
                           "gate2_outcome": "pass"}]}
        assert self._fn()(old) == "n/a"

    def test_malformed_input_is_na(self):
        derive = self._fn()
        for bad in (None, {}, [], "pass", {"summary": "x"}, {"outcome": [], "summary": {}}):
            assert derive(bad) == "n/a"


class TestTrustStamp:
    """Component B: map a run's outcome → passed_adversarial_gate on the proposal."""

    def _fn(self):
        from core.ddd_cultivation import trust_from_gate2_outcome
        return trust_from_gate2_outcome

    def test_pass_maps_to_passed(self):
        t = self._fn()
        assert t("pass") == "passed"
        assert t("pass_with_fixes") == "passed"

    def test_block_maps_to_failed(self):
        t = self._fn()
        assert t("block") == "failed"

    def test_na_maps_to_na(self):
        t = self._fn()
        assert t("n/a") == "n/a"

    def test_anything_unexpected_fails_closed_to_na(self):
        # the load-bearing invariant: never fabricate 'passed'.
        t = self._fn()
        for bad in ("", None, "PASS", "passed", "unknown", "looks_good"):
            assert t(bad) == "n/a", f"{bad!r} must fail closed to n/a, not passed"
