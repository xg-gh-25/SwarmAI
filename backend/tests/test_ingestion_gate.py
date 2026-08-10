"""Unified Ingestion Gate — C1 tier primitives + dispatcher skeleton.

Tests the leaf module core/ingestion_gate.py: the two-layer noise split
(structural_noise, all-store, no length floor — vs ddd_value_floor, DDD-only,
≥5-word + instance-log + narration + machine-broadcast), the GateVerdict shape,
the TRIGGER_TIERS declaration table, and the ingestion_gate dispatcher for the
noise/dedup/confident tiers.

C1 scope only — judge/trust/magnitude/keep_type_holdback tiers land in C2.
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class TestTwoLayerNoiseSplit:
    """Gate-1 round2 ⓐ: is_noise (≥5-word floor + machine-broadcast) is STRICTER
    than is_noise_entry (structural only). Merging silently drops short MEMORY
    fragments. So the leaf must expose TWO functions with the correct strictness."""

    def test_structural_noise_has_no_length_floor(self):
        # A short decision fragment MEMORY keeps today must PASS structural_noise.
        from core.ingestion_gate import structural_noise
        assert structural_noise("enableMCP = always true") is False  # 4 words, NOT noise
        assert structural_noise("Batch writes per section") is False

    def test_structural_noise_catches_structural_junk(self):
        from core.ingestion_gate import structural_noise
        assert structural_noise("| col | col |") is True            # table fragment
        assert structural_noise("I'll diagnose the root cause") is True  # agent monologue
        assert structural_noise("✅ done") is True              # emoji-prefix marker

    def test_ddd_value_floor_rejects_short_fragment(self):
        # The ≥5-word floor is a DDD lesson-value gate — NOT applied to MEMORY.
        from core.ingestion_gate import ddd_value_floor
        assert ddd_value_floor("enableMCP = always true") is True   # <5 words → floor rejects
        assert ddd_value_floor("done") is True

    def test_ddd_value_floor_accepts_real_lesson(self):
        from core.ingestion_gate import ddd_value_floor
        assert ddd_value_floor(
            "A race condition in the streaming reconcile causes a duplicate bubble regression."
        ) is False  # real ≥5-word lesson → not floored

    def test_ddd_value_floor_catches_machine_broadcast(self):
        # Gate-1 round3 ⓕ: _MACHINE_BROADCAST_RE lives in is_noise, NOT is_quality_lesson.
        # It MUST be in ddd_value_floor so DDD keeps machine-broadcast filtering.
        from core.ingestion_gate import ddd_value_floor
        assert ddd_value_floor("Architecture change detected:\n- new_module: `x.py`") is True
        assert ddd_value_floor("Undocumented module `foo` (3 functions)") is True

    def test_ddd_value_floor_catches_instance_log_and_narration(self):
        from core.ingestion_gate import ddd_value_floor
        assert ddd_value_floor("exit 1") is True                    # instance-log
        assert ddd_value_floor("I'll check the logs and then fix it") is True  # narration


class TestGateVerdict:
    def test_verdict_shape_decision_only(self):
        # GateVerdict carries decision, NOT apply status (gate=decision, apply=execution).
        from core.ingestion_gate import GateVerdict
        v = GateVerdict(verdict="review", tiers_run=["noise"], reason="trust:n/a")
        assert v.verdict in ("auto", "review", "discard")
        assert isinstance(v.tiers_run, list)
        # must NOT carry apply_to_ddd status vocabulary
        assert not hasattr(v, "apply_status")


class TestTriggerTiers:
    def test_all_seven_triggers_declared(self):
        from core.ingestion_gate import TRIGGER_TIERS
        # the 7 ingestion triggers (KNOWLEDGE index refresh is not ingestion)
        for t in ("ddd_reflect", "ddd_writeback", "ddd_orch_llm_refresh",
                  "ddd_orch_mechanical", "memory_distill", "memory_save_button",
                  "evolution_distill"):
            assert t in TRIGGER_TIERS, f"{t} missing from TRIGGER_TIERS"

    def test_memory_distill_has_no_ddd_value_floor(self):
        # Gate-1 ⓐ: MEMORY must NOT get the ≥5-word confident floor.
        from core.ingestion_gate import TRIGGER_TIERS
        assert "confident" not in TRIGGER_TIERS["memory_distill"]
        assert "noise" in TRIGGER_TIERS["memory_distill"]

    def test_protected_zone_not_a_tier(self):
        # Gate-1 round3 HIGH: protected_zone stays in caller, never a gate tier.
        from core.ingestion_gate import TRIGGER_TIERS
        for tiers in TRIGGER_TIERS.values():
            assert "protected_zone" not in tiers


class TestDispatcherNoiseDedupConfident:
    def test_memory_short_fragment_survives_gate(self):
        # End-to-end: a short MEMORY decision fragment must NOT be discarded.
        from core.ingestion_gate import ingestion_gate
        v = ingestion_gate("enableMCP = always true", store="MEMORY",
                            trigger="memory_distill", context={})
        assert v.verdict != "discard"

    def test_structural_junk_discarded_any_store(self):
        from core.ingestion_gate import ingestion_gate
        v = ingestion_gate("| a | b |", store="MEMORY",
                            trigger="memory_distill", context={})
        assert v.verdict == "discard"
        assert "noise" in v.tiers_run

    def test_ddd_short_fragment_discarded_by_confident(self):
        # DDD DOES apply the value floor: same short fragment → discard on confident.
        from core.ingestion_gate import ingestion_gate
        v = ingestion_gate("done", store="DDD", trigger="ddd_reflect",
                           context={"proposal": {"content": "done"}})
        assert v.verdict == "discard"

    def test_unknown_trigger_fails_closed_to_review(self):
        from core.ingestion_gate import ingestion_gate
        v = ingestion_gate("some text here for testing", store="DDD",
                           trigger="nonexistent_trigger", context={})
        assert v.verdict == "review"


# ══════════════════════════════════════════════════════════════════════════════
# C2 — judge tier + keep_type_holdback + fail-closed
# ══════════════════════════════════════════════════════════════════════════════
from unittest.mock import patch, MagicMock


def _mock_bedrock(verdict_line: str):
    """Build a mock get_client() whose invoke_model returns a judge verdict body."""
    import json as _json
    client = MagicMock()
    body = MagicMock()
    body.read.return_value = _json.dumps(
        {"content": [{"type": "text", "text": verdict_line}]}
    ).encode()
    client.invoke_model.return_value = {"body": body}
    return (client, "us.anthropic.claude-sonnet-4-6")


class TestSelfAdversarialJudge:
    def test_judge_pass(self):
        from core.ingestion_gate import self_adversarial_judge
        with patch("core.ingestion_gate._judge_client", return_value=_mock_bedrock(
                "VERDICT: pass\nREASON: real load-bearing lesson")):
            verdict, reason = self_adversarial_judge("A real lesson.", "What Worked", [])
        assert verdict == "pass"

    def test_judge_suspect(self):
        from core.ingestion_gate import self_adversarial_judge
        with patch("core.ingestion_gate._judge_client", return_value=_mock_bedrock(
                "VERDICT: suspect\nREASON: too vague")):
            verdict, _ = self_adversarial_judge("meh", "What Worked", [])
        assert verdict == "suspect"

    def test_judge_noise(self):
        from core.ingestion_gate import self_adversarial_judge
        with patch("core.ingestion_gate._judge_client", return_value=_mock_bedrock(
                "VERDICT: noise\nREASON: machine fragment")):
            verdict, _ = self_adversarial_judge("frag", "What Worked", [])
        assert verdict == "noise"

    def test_judge_fail_closed_on_exception(self):
        # Bedrock raises → suspect (fail-closed), NEVER pass.
        from core.ingestion_gate import self_adversarial_judge
        with patch("core.ingestion_gate._judge_client", side_effect=RuntimeError("boom")):
            verdict, reason = self_adversarial_judge("x", "s", [])
        assert verdict == "suspect"
        assert "error" in reason.lower() or "boom" in reason.lower()

    def test_judge_fail_closed_on_unparseable(self):
        from core.ingestion_gate import self_adversarial_judge
        with patch("core.ingestion_gate._judge_client", return_value=_mock_bedrock(
                "the model rambled with no verdict line")):
            verdict, _ = self_adversarial_judge("x", "s", [])
        assert verdict == "suspect"

    def test_judge_fail_closed_on_empty(self):
        from core.ingestion_gate import self_adversarial_judge
        with patch("core.ingestion_gate._judge_client", return_value=_mock_bedrock("")):
            verdict, _ = self_adversarial_judge("x", "s", [])
        assert verdict == "suspect"


class TestKeepTypeHoldback:
    def test_keep_type_holds_back(self):
        # A [principle]/[decision] etc (KEEP_TYPES) → review (permanent write, must not auto).
        from core.ingestion_gate import keep_type_holdback
        held, etype = keep_type_holdback(
            "The system must always verify state before asserting a cause because inference is stale.")
        # classify may land it on any type; assert the CONTRACT: if KEEP_TYPE → held True
        from core.ddd_entry_lifecycle import route_lesson_type
        section, et = route_lesson_type(
            "The system must always verify state before asserting a cause because inference is stale.")
        assert held == (section is None)

    def test_operational_type_not_held(self):
        from core.ingestion_gate import keep_type_holdback
        # an operational guideline routes to a section (not None) → not held
        held, _ = keep_type_holdback("Always wrap pytest in a wall-clock timeout to avoid hangs.")
        # contract check via the SSOT
        from core.ddd_entry_lifecycle import route_lesson_type
        section, _e = route_lesson_type("Always wrap pytest in a wall-clock timeout to avoid hangs.")
        assert held == (section is None)


class TestGateJudgeTierWiring:
    def test_memory_distill_judge_suspect_routes_review(self):
        from core.ingestion_gate import ingestion_gate
        with patch("core.ingestion_gate._judge_client", return_value=_mock_bedrock(
                "VERDICT: suspect\nREASON: unclear")):
            v = ingestion_gate("A plausible but unverified memory claim about the system.",
                               store="MEMORY", trigger="memory_distill", context={})
        assert v.verdict == "review"
        assert "judge" in v.tiers_run

    def test_memory_distill_judge_noise_routes_discard(self):
        from core.ingestion_gate import ingestion_gate
        with patch("core.ingestion_gate._judge_client", return_value=_mock_bedrock(
                "VERDICT: noise\nREASON: fragment")):
            v = ingestion_gate("some borderline entry text here",
                               store="MEMORY", trigger="memory_distill", context={})
        assert v.verdict == "discard"

    def test_memory_distill_judge_failclosed_routes_review(self):
        from core.ingestion_gate import ingestion_gate
        with patch("core.ingestion_gate._judge_client", side_effect=RuntimeError("net")):
            v = ingestion_gate("some borderline entry text here",
                               store="MEMORY", trigger="memory_distill", context={})
        assert v.verdict == "review"  # judge suspect → review, never auto
