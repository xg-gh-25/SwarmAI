"""Knowledge Admission Subsystem — step 0 + Component B: canonical adversarial
outcome + fail-closed trust stamp.

RED-first. The trust stamp is the SOLE authority that lets a proposal auto-write
into ANY doc (incl. SELF.md) — so its derivation must be deterministic and
fail-closed: only an explicit machine-readable pass/pass_with_fixes earns
trust=passed; absent/un-parseable/prose-only/block → failed or n/a, NEVER passed.
(Design: 2026-08-09-knowledge-admission-subsystem-design.md, AC3/AC10.)
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class TestGate2OutcomeDerivation:
    """step0: derive a canonical gate2_outcome enum from a run's stages,
    fail-closed on anything non-explicit."""

    def _fn(self):
        from core.ddd_cultivation import derive_gate2_outcome
        return derive_gate2_outcome

    def test_explicit_enum_pass(self):
        derive = self._fn()
        run = {"stages": [{"stage": "adversarial", "status": "completed",
                           "gate2_outcome": "pass"}]}
        assert derive(run) == "pass"

    def test_explicit_enum_pass_with_fixes(self):
        derive = self._fn()
        run = {"stages": [{"stage": "deliver", "status": "completed",
                           "gate2_outcome": "pass_with_fixes"}]}
        assert derive(run) == "pass_with_fixes"

    def test_explicit_enum_block(self):
        derive = self._fn()
        run = {"stages": [{"stage": "adversarial", "status": "completed",
                           "gate2_outcome": "block"}]}
        assert derive(run) == "block"

    def test_block_wins_over_document_order(self):
        # HOLE #1 (adversarial): a pass must NEVER shadow a block, regardless of order.
        derive = self._fn()
        run = {"stages": [
            {"stage": "deliver", "status": "completed", "gate2_outcome": "pass"},
            {"stage": "adversarial", "status": "completed", "gate2_outcome": "block"},
        ]}
        assert derive(run) == "block"
        # and the reverse order:
        run2 = {"stages": [
            {"stage": "adversarial", "status": "completed", "gate2_outcome": "block"},
            {"stage": "deliver", "status": "completed", "gate2_outcome": "pass"},
        ]}
        assert derive(run2) == "block"

    def test_incomplete_stage_outcome_is_ignored(self):
        # HOLE #2 (adversarial): a stale pass on an in-progress stage is NOT authoritative.
        derive = self._fn()
        run = {"stages": [{"stage": "adversarial", "status": "in_progress",
                           "gate2_outcome": "pass"}]}
        assert derive(run) == "n/a"

    def test_stale_incomplete_pass_cannot_shadow_completed_block(self):
        # HOLE #3 (adversarial, worst case): combined order+status attack.
        derive = self._fn()
        run = {"stages": [
            {"stage": "deliver", "status": "in_progress", "gate2_outcome": "pass"},
            {"stage": "adversarial", "status": "completed", "gate2_outcome": "block"},
        ]}
        assert derive(run) == "block"

    def test_prose_only_verdict_is_not_parsed_to_pass(self):
        # CRITICAL fail-closed: a free-text gate2_verdict with NO canonical enum
        # must NOT be heuristically parsed to pass — it returns n/a.
        derive = self._fn()
        run = {"stages": [{"stage": "adversarial", "status": "completed",
                           "gate2_verdict": "2 CRIT (mine) fixed + 3 findings adjudicated"}]}
        assert derive(run) == "n/a"
        # prose present on a COMPLETED stage still must not be parsed to a pass

    def test_no_adversarial_stage_is_na(self):
        # docs/research profile — no adversarial stage by design.
        derive = self._fn()
        run = {"stages": [{"stage": "deliver", "status": "completed"}]}
        assert derive(run) == "n/a"

    def test_empty_or_missing_stages_is_na(self):
        derive = self._fn()
        assert derive({}) == "n/a"
        assert derive({"stages": []}) == "n/a"

    def test_unknown_enum_value_is_na_not_pass(self):
        # a garbage enum value must fail closed, never pass.
        derive = self._fn()
        run = {"stages": [{"stage": "adversarial", "status": "completed",
                           "gate2_outcome": "looks_good"}]}
        assert derive(run) == "n/a"


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
