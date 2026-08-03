"""Tests for Gate Promotion — soft→hard lifecycle management.

Covers:
- GateStatus properties (fp_rate, overrides, promotion criteria)
- GateManager persistence (load/save)
- Promotion eligibility (all 4 criteria)
- Auto-demotion (3 overrides in 7d)
- Record methods (trigger, false_positive, override, incident)
"""
from datetime import date, timedelta

import pytest

from core.gate_promotion import (
    GATE_NAMES,
    GateManager,
    GateStatus,
    MIN_TRIGGERS,
)


class TestGateStatus:
    def test_new_gate_not_promoted(self):
        gate = GateStatus(installed_at="2026-05-16")
        assert not gate.is_promoted
        assert gate.fp_rate == 0.0
        assert gate.overrides_in_last_14d == 0

    def test_fp_rate_calculation(self):
        gate = GateStatus(trigger_count=20, false_positive_count=2)
        assert gate.fp_rate == pytest.approx(0.1)

    def test_fp_rate_zero_triggers(self):
        gate = GateStatus(trigger_count=0, false_positive_count=0)
        assert gate.fp_rate == 0.0

    def test_overrides_in_window(self):
        today = date.today().isoformat()
        old = (date.today() - timedelta(days=20)).isoformat()
        gate = GateStatus(override_dates=[old, today, today])
        assert gate.overrides_in_last_14d == 2
        assert gate.overrides_in_last_7d == 2

    def test_meets_promotion_all_criteria(self):
        gate = GateStatus(
            trigger_count=25,
            false_positive_count=1,  # 4% FP rate
            user_overrides=0,
            override_dates=[],
            incidents_prevented=["2026-05-10: caught bad proposal"],
        )
        assert gate.meets_promotion_criteria()

    def test_fails_promotion_low_triggers(self):
        gate = GateStatus(
            trigger_count=15,  # < 20
            false_positive_count=0,
            incidents_prevented=["incident"],
        )
        assert not gate.meets_promotion_criteria()

    def test_fails_promotion_high_fp(self):
        gate = GateStatus(
            trigger_count=20,
            false_positive_count=5,  # 25% FP > 10%
            incidents_prevented=["incident"],
        )
        assert not gate.meets_promotion_criteria()

    def test_fails_promotion_recent_overrides(self):
        today = date.today().isoformat()
        gate = GateStatus(
            trigger_count=25,
            false_positive_count=1,
            override_dates=[today],  # 1 override in last 14d
            incidents_prevented=["incident"],
        )
        assert not gate.meets_promotion_criteria()

    def test_fails_promotion_no_incidents(self):
        gate = GateStatus(
            trigger_count=25,
            false_positive_count=1,
            incidents_prevented=[],  # No real incidents prevented
        )
        assert not gate.meets_promotion_criteria()

    def test_should_demote(self):
        today = date.today().isoformat()
        gate = GateStatus(
            promoted_at="2026-05-10T00:00:00",
            override_dates=[today, today, today],  # 3 in 7d
        )
        assert gate.should_demote()

    def test_no_demote_when_not_promoted(self):
        today = date.today().isoformat()
        gate = GateStatus(
            promoted_at=None,
            override_dates=[today, today, today],
        )
        assert not gate.should_demote()


class TestGateManager:
    def test_init_creates_all_gates(self, tmp_path):
        mgr = GateManager(tmp_path)
        for name in GATE_NAMES:
            gate = mgr.get(name)
            assert gate is not None
            assert gate.installed_at == date.today().isoformat()

    def test_persistence_roundtrip(self, tmp_path):
        mgr = GateManager(tmp_path)
        mgr.record_trigger("noise_filter")
        mgr.record_trigger("noise_filter")

        # Reload from disk
        mgr2 = GateManager(tmp_path)
        gate = mgr2.get("noise_filter")
        assert gate.trigger_count == 2

    def test_record_trigger(self, tmp_path):
        mgr = GateManager(tmp_path)
        mgr.record_trigger("trust_annotation")
        assert mgr.get("trust_annotation").trigger_count == 1

    def test_record_false_positive(self, tmp_path):
        mgr = GateManager(tmp_path)
        mgr.record_false_positive("file_tracker")
        assert mgr.get("file_tracker").false_positive_count == 1

    def test_record_override_triggers_demotion(self, tmp_path):
        mgr = GateManager(tmp_path)
        # Manually promote a gate first
        gate = mgr.get("noise_filter")
        gate.promoted_at = "2026-05-10T00:00:00"
        mgr._save()

        # 3 overrides in 7d → should demote
        mgr.record_override("noise_filter")
        mgr.record_override("noise_filter")
        mgr.record_override("noise_filter")

        gate = mgr.get("noise_filter")
        assert gate.demoted_at is not None
        assert not gate.is_promoted  # Demoted = not promoted

    def test_record_incident(self, tmp_path):
        mgr = GateManager(tmp_path)
        mgr.record_incident("trust_annotation", "blocked bad auto-apply")
        gate = mgr.get("trust_annotation")
        assert len(gate.incidents_prevented) == 1
        assert "blocked bad auto-apply" in gate.incidents_prevented[0]

    def test_check_promotions_promotes_eligible(self, tmp_path):
        mgr = GateManager(tmp_path)
        gate = mgr.get("noise_filter")
        gate.trigger_count = 25
        gate.false_positive_count = 1
        gate.incidents_prevented = ["2026-05-10: caught noise"]
        mgr._save()

        promoted = mgr.check_promotions()
        assert "noise_filter" in promoted
        assert mgr.is_gate_hard("noise_filter")

    def test_check_promotions_skips_already_promoted(self, tmp_path):
        mgr = GateManager(tmp_path)
        gate = mgr.get("noise_filter")
        gate.promoted_at = "2026-05-10T00:00:00"
        gate.trigger_count = 30
        gate.incidents_prevented = ["x"]
        mgr._save()

        promoted = mgr.check_promotions()
        assert "noise_filter" not in promoted  # Already promoted

    def test_check_promotions_skips_ineligible(self, tmp_path):
        mgr = GateManager(tmp_path)
        # All gates start with 0 triggers → ineligible
        promoted = mgr.check_promotions()
        assert promoted == []

    def test_is_gate_hard_false_for_soft(self, tmp_path):
        mgr = GateManager(tmp_path)
        assert not mgr.is_gate_hard("trust_annotation")

    def test_get_promotion_summary(self, tmp_path):
        mgr = GateManager(tmp_path)
        mgr.record_trigger("noise_filter")
        summary = mgr.get_promotion_summary()

        assert "noise_filter" in summary
        assert summary["noise_filter"]["status"] == "soft"
        assert summary["noise_filter"]["trigger_count"] == 1
        assert f"1/{MIN_TRIGGERS}" in summary["noise_filter"]["progress"]

    def test_corrupted_file_handled(self, tmp_path):
        """Corrupted JSON → fresh initialization, no crash."""
        (tmp_path / "gate_promotion_data.json").write_text("not json{{{")
        mgr = GateManager(tmp_path)
        # Should have fresh gates, not crash
        assert mgr.get("trust_annotation") is not None
        assert mgr.get("trust_annotation").trigger_count == 0
