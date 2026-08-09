"""Knowledge Admission — Component D: close the calibration loop.

(AC6) admission_band uses a PER-CHANNEL calibrated auto threshold (from
proposal_feedback.get_adjusted_threshold), not a single hardcoded constant — so a
channel with poor precision gets a HIGHER auto bar on the main cultivation path
(not only code_intel_feed).

(AC7) check_self_correction's recommendation is CONSUMED (not dead code): a channel
past the rejection batch produces an applied/logged self-correction signal.
"""
import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _prop(**kw):
    from core.ddd_cultivation import CultivationProposal
    base = dict(
        target_doc="TECH.md", target_section="Architecture",
        content="A genuinely load-bearing architectural lesson worth keeping in the brain over time.",
        source_run_id="run_x", confidence=0.72, passed_adversarial_gate="passed",
        source_stage="reflect",
    )
    base.update(kw)
    return CultivationProposal(**base)


class TestCalibratedThreshold:
    def _band(self):
        from core.ddd_cultivation import admission_band
        return admission_band

    def test_channel_auto_threshold_helper_reads_stats(self, tmp_path):
        # the per-channel threshold helper: low-precision channel → raised threshold
        from core.ddd_cultivation import _channel_auto_threshold
        artifacts = tmp_path / ".artifacts"
        artifacts.mkdir(parents=True)
        # a channel with terrible precision (many rejects, dominant false_positive)
        stats = {"reflect": {"generated": 20, "approved": 2, "rejected": 18,
                             "rejection_breakdown": {"false_positive": 18}}}
        (artifacts / "channel_stats.json").write_text(json.dumps(stats))
        base = 0.7
        raised = _channel_auto_threshold("reflect", base, tmp_path)
        assert raised > base, "low-precision channel must raise the auto threshold"

    def test_no_stats_falls_back_to_default(self, tmp_path):
        from core.ddd_cultivation import _channel_auto_threshold
        # no channel_stats.json → default (bounded by floor)
        t = _channel_auto_threshold("reflect", 0.7, tmp_path)
        assert t >= 0.7

    def test_band_uses_calibrated_threshold(self, tmp_path):
        # AC6 end-to-end: a proposal at 0.72 that would AUTO at the 0.7 default is sent
        # to REVIEW once the channel's calibrated threshold rises above 0.72.
        band = self._band()
        artifacts = tmp_path / ".artifacts"
        artifacts.mkdir(parents=True)
        stats = {"reflect": {"generated": 30, "approved": 1, "rejected": 29,
                             "rejection_breakdown": {"false_positive": 29}}}
        (artifacts / "channel_stats.json").write_text(json.dumps(stats))
        p = _prop(confidence=0.72, source_stage="reflect")
        verdict, reason = band(p, tmp_path)
        assert verdict == "review", f"calibrated (raised) threshold should demote to review, got {verdict} ({reason})"

    def test_healthy_channel_still_autos(self, tmp_path):
        # a high-precision channel keeps the default bar → a trusted 0.72 proposal autos.
        band = self._band()
        artifacts = tmp_path / ".artifacts"
        artifacts.mkdir(parents=True)
        stats = {"reflect": {"generated": 20, "approved": 19, "rejected": 1,
                             "rejection_breakdown": {"false_positive": 1}}}
        (artifacts / "channel_stats.json").write_text(json.dumps(stats))
        p = _prop(confidence=0.72, source_stage="reflect")
        assert band(p, tmp_path)[0] == "auto"


class TestSelfCorrectionConsumed:
    def test_apply_self_corrections_is_callable_and_returns_actions(self, tmp_path):
        # AC7: the dead check_self_correction is now consumed by a real function that
        # returns the corrections it acted on (surfaced, not logged-into-void).
        from core.ddd_cultivation import apply_channel_self_corrections
        artifacts = tmp_path / ".artifacts"
        artifacts.mkdir(parents=True)
        stats = {"reflect": {"generated": 15, "approved": 2, "rejected": 13,
                             "rejection_breakdown": {"false_positive": 13}}}
        (artifacts / "channel_stats.json").write_text(json.dumps(stats))
        actions = apply_channel_self_corrections(tmp_path)
        assert isinstance(actions, list)
        # the reflect channel is past the batch (13 rejects) with a dominant reason →
        # a self-correction action is surfaced
        assert any(a.get("channel") == "reflect" and a.get("fix_type") for a in actions)

    def test_no_corrections_when_below_batch(self, tmp_path):
        from core.ddd_cultivation import apply_channel_self_corrections
        artifacts = tmp_path / ".artifacts"
        artifacts.mkdir(parents=True)
        stats = {"reflect": {"generated": 3, "approved": 2, "rejected": 1,
                             "rejection_breakdown": {"false_positive": 1}}}
        (artifacts / "channel_stats.json").write_text(json.dumps(stats))
        assert apply_channel_self_corrections(tmp_path) == []
