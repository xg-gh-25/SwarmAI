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
        assert structural_noise("✅ done") is True              # emoji-prefix BARE status marker

    def test_emoji_prefix_bare_status_is_noise(self):
        # run_85beeb04: a BARE emoji status marker (short, no titled content) is noise.
        from core.ingestion_gate import structural_noise
        assert structural_noise("✅ done") is True
        assert structural_noise("🟡 wip") is True
        assert structural_noise("❌ failed") is True

    def test_emoji_prefixed_titled_entry_is_NOT_noise(self):
        # run_85beeb04 BUGFIX: a curated MEMORY entry that merely OPENS with an emoji but
        # carries a **bold title** + substantial content is NOT structural noise. The old
        # _EMOJI_PREFIX rule flagged these identically to '✅ done' → 8 real MEMORY
        # entries wrongly audited as noise.
        from core.ingestion_gate import structural_noise
        assert structural_noise("🟡 **Frontend reconcile race** — GUARDED (not fixed)") is False
        assert structural_noise("✅ **R6 Session arbitration** — IMPLEMENTED + DEPLOYED (2026-06)") is False
        assert structural_noise("⚠️ **E2E chain missing** — PARTIALLY RESOLVED: smoke_e2e.py added") is False

    def test_emoji_prefixed_monologue_is_STILL_noise(self):
        # Gate-2 over-correction guard (run_85beeb04): the emoji prefix must NOT let agent
        # monologue escape — AGENT_MONOLOGUE is ^-anchored so the emoji hides it; the fix
        # re-checks the stripped rest. These are long (>30 chars) with no bold, so they'd
        # escape the length/bold discriminator if the monologue re-check were missing.
        from core.ingestion_gate import structural_noise
        assert structural_noise("✅ I'll diagnose the root cause of this issue and fix it") is True
        assert structural_noise("🟡 Let me check the logs and then update the cache layer") is True

    def test_emoji_bold_check_requires_balanced_span(self):
        # Gate-2: the bold-title whitelist requires a BALANCED **…** span, not a stray '**'
        # substring. A single dangling '**' does NOT count as a title (so it can't be the
        # thing that rescues an entry from the noise floor).
        from core.extraction_patterns import _EMOJI_PREFIX
        import re
        # a real balanced span → recognized as a title
        assert bool(re.search(r"\*\*[^*]+\*\*", "**Real Title** — desc")) is True
        # a stray/dangling '**' → NOT a title
        assert bool(re.search(r"\*\*[^*]+\*\*", "** dangling no close")) is False

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
    def test_all_triggers_declared(self):
        from core.ingestion_gate import TRIGGER_TIERS
        # every store's triggers are declared (DDD spec + dispatcher-served + carve-outs)
        for t in ("ddd_reflect", "ddd_writeback", "ddd_orch_llm_refresh",
                  "ddd_orch_mechanical", "memory_distill", "memory_save_button",
                  "evolution_distill"):
            assert t in TRIGGER_TIERS, f"{t} missing from TRIGGER_TIERS"

    def test_dispatcher_serves_only_memory_and_evolution(self):
        """C7 honesty (post-C4): the DISPATCHER (ingestion_gate) serves MEMORY + EVOLUTION
        triggers; DDD goes through admission_band; orchestrator triggers are carve-outs.
        _DISPATCHER_TRIGGERS must NOT include any ddd_* trigger."""
        from core.ingestion_gate import _DISPATCHER_TRIGGERS
        assert _DISPATCHER_TRIGGERS == {
            "memory_distill", "memory_save_button", "memory_persist",
            "evolution_distill", "evolution_persist",
        }
        assert not any(t.startswith("ddd_") for t in _DISPATCHER_TRIGGERS), \
            "DDD triggers are served by admission_band, not the dispatcher"

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

    def test_ddd_trigger_rejected_by_dispatcher_guard(self):
        # C7 (post-C4): the dispatcher serves MEMORY/EVOLUTION only. DDD goes through
        # admission_band, so a ddd_* trigger reaching ingestion_gate() is a mis-wire →
        # fail-closed to review (NOT run the DDD tier-spec through the dispatcher).
        from core.ingestion_gate import ingestion_gate
        v = ingestion_gate("done", store="DDD", trigger="ddd_reflect",
                           context={"proposal": {"content": "done"}})
        assert v.verdict == "review"
        assert "non_dispatcher_trigger" in v.reason

    def test_ddd_value_floor_applied_via_admission_band(self):
        # The DDD value-floor (short fragment → discard) lives in admission_band now,
        # not the dispatcher. Verify the tier PRIMITIVE still works (ddd_value_floor).
        from core.ingestion_gate import ddd_value_floor
        assert ddd_value_floor("done") is True  # <5 words → floored (DDD-only)

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


# ══════════════════════════════════════════════════════════════════════════════
# C3 — DDD migration: admission_band grows the self_adversarial tree + gate delegates
# ══════════════════════════════════════════════════════════════════════════════
import pytest
from pathlib import Path


class TestC3TrustSourceField:
    def test_proposal_has_trust_source_default_none(self):
        from core.ddd_cultivation import CultivationProposal
        p = CultivationProposal(target_doc="TECH.md", target_section="Conventions",
                                content="Use async executors for blocking IO calls always.",
                                source_run_id="run_x", confidence=0.9)
        assert p.trust_source == "none"

    def test_trust_source_roundtrips(self):
        from core.ddd_cultivation import CultivationProposal
        p = CultivationProposal(target_doc="TECH.md", target_section="Conventions",
                                content="Use async executors for blocking IO calls always.",
                                source_run_id="run_x", confidence=0.9,
                                passed_adversarial_gate="passed", trust_source="self_adversarial")
        p2 = CultivationProposal.from_dict(p.to_dict())
        assert p2.trust_source == "self_adversarial"

    def test_old_json_without_trust_source_defaults_none(self):
        from core.ddd_cultivation import CultivationProposal
        d = {"id": "proposal_x", "target_doc": "TECH.md", "target_section": "Conventions",
             "content": "x" * 40, "source_run_id": "run_x", "confidence": 0.9,
             "created_at": "2026-01-01T00:00:00+00:00"}
        p = CultivationProposal.from_dict(d)
        assert p.trust_source == "none"


class TestC3SelfAdversarialTree:
    """admission_band: trust=n/a now runs the self_adversarial judge (non-protected only)."""

    def _p(self, doc="TECH.md", section="Conventions", gate="n/a", conf=0.9):
        from core.ddd_cultivation import CultivationProposal
        return CultivationProposal(target_doc=doc, target_section=section,
                                   content="Prefer dedicated ThreadPoolExecutor for blocking calls over the default pool.",
                                   source_run_id="run_x", confidence=conf,
                                   passed_adversarial_gate=gate)

    def test_protected_zone_trust_na_review_judge_NOT_run(self, monkeypatch):
        """SELF.md + trust=n/a → review, and the judge must NOT be called (R1 gate)."""
        import core.ingestion_gate as ig
        called = {"n": 0}
        def _spy(*a, **k):
            called["n"] += 1
            return ("pass", "judged")
        monkeypatch.setattr(ig, "self_adversarial_judge", _spy)
        from core.ddd_cultivation import admission_band
        verdict, reason = admission_band(self._p(doc="SELF.md", section="Anything"), None)
        assert verdict == "review"
        assert called["n"] == 0, "judge must NOT run for a protected-zone trust=n/a proposal"

    def test_nonprotected_trust_na_judge_pass_can_auto(self, monkeypatch):
        import core.ddd_cultivation as dc
        monkeypatch.setattr(dc, "self_adversarial_judge", lambda *a, **k: ("pass", "judged"))
        # magnitude/circuit clean + confidence high
        verdict, reason = admission_band_helper(dc, self._p(gate="n/a", conf=0.95))
        assert verdict == "auto"
        assert "self_adversarial" in reason or "self" in reason

    def test_nonprotected_trust_na_judge_suspect_review(self, monkeypatch):
        import core.ddd_cultivation as dc
        monkeypatch.setattr(dc, "self_adversarial_judge", lambda *a, **k: ("suspect", "dubious"))
        verdict, reason = dc.admission_band(self._p(gate="n/a"), None)
        assert verdict == "review"

    def test_nonprotected_trust_na_judge_noise_discard(self, monkeypatch):
        import core.ddd_cultivation as dc
        monkeypatch.setattr(dc, "self_adversarial_judge", lambda *a, **k: ("noise", "junk"))
        verdict, reason = dc.admission_band(self._p(gate="n/a"), None)
        assert verdict == "discard"

    def test_inherited_gate2_still_autos_without_judge(self, monkeypatch):
        """trust=passed(inherited) → judge NOT run, original path preserved."""
        import unittest.mock as m
        import core.ddd_cultivation as dc
        called = {"n": 0}
        monkeypatch.setattr(dc, "self_adversarial_judge",
                            lambda *a, **k: (called.__setitem__("n", called["n"] + 1), ("pass", ""))[1])
        p = self._p(gate="passed", conf=0.95)
        p.trust_source = "inherited_gate2"
        # project_dir=None would fail-close evaluate_auto_approval to review (a real gate
        # error, not the path under test) — mock the quality gate clean like the auto helper.
        with m.patch("core.ddd_auto_approval.evaluate_auto_approval") as mock_eval:
            mock_eval.return_value = type("D", (), {"criteria_met": {
                "small_magnitude": True, "circuit_breaker_ok": True}})()
            verdict, reason = dc.admission_band(p, None)
        assert verdict == "auto"
        assert called["n"] == 0, "inherited_gate2 must not invoke the judge"


def admission_band_helper(dc, proposal):
    """admission_band needs magnitude/circuit clean; evaluate_auto_approval is patched
    to clean in the fixture-less path via monkeypatching inside each test that needs it."""
    import unittest.mock as m
    with m.patch("core.ddd_auto_approval.evaluate_auto_approval") as mock_eval:
        mock_eval.return_value = type("D", (), {"criteria_met": {"small_magnitude": True, "circuit_breaker_ok": True}})()
        return dc.admission_band(proposal, None)


class TestC3PreDropHardened:
    """The protected-zone pre-drop must require inherited_gate2 (not just passed),
    else a self_adversarial 'passed' stamp reopens the protected-zone hole (run_8dea0dd5 R2)."""

    def test_self_adversarial_passed_still_predropped_in_protected_zone(self, tmp_path):
        from core.ddd_cultivation import _predrop_is_protected_untrusted, CultivationProposal
        p = CultivationProposal(target_doc="SELF.md", target_section="X",
                                content="x" * 40, source_run_id="r", confidence=0.9,
                                passed_adversarial_gate="passed", trust_source="self_adversarial")
        # self_adversarial passed → still pre-dropped (only inherited_gate2 falls through)
        assert _predrop_is_protected_untrusted(p) is True

    def test_inherited_gate2_passed_falls_through_predrop(self):
        from core.ddd_cultivation import _predrop_is_protected_untrusted, CultivationProposal
        p = CultivationProposal(target_doc="SELF.md", target_section="X",
                                content="x" * 40, source_run_id="r", confidence=0.9,
                                passed_adversarial_gate="passed", trust_source="inherited_gate2")
        assert _predrop_is_protected_untrusted(p) is False


class TestC3AdversarialFixes:
    """Gate-2 CRITICAL fixes: protected-zone auto-entry is inherited_gate2-ONLY,
    unconditionally — even if a proposal ENTERS with passed+self_adversarial (re-read
    from disk / retry / second call site)."""

    def _p(self, doc, section, gate, source, conf=0.95):
        from core.ddd_cultivation import CultivationProposal
        return CultivationProposal(target_doc=doc, target_section=section,
                                   content="Prefer dedicated executors for blocking IO over the default pool.",
                                   source_run_id="run_x", confidence=conf,
                                   passed_adversarial_gate=gate, trust_source=source)

    def test_selfadv_passed_ENTRY_still_barred_from_protected_zone(self, monkeypatch):
        """The CRITICAL: a proposal arriving ALREADY stamped passed+self_adversarial
        (e.g. re-read from disk) targeting SELF.md must be barred, judge NOT run."""
        import core.ddd_cultivation as dc
        called = {"n": 0}
        monkeypatch.setattr(dc, "self_adversarial_judge",
                            lambda *a, **k: (called.__setitem__("n", called["n"]+1), ("pass",""))[1])
        p = self._p("SELF.md", "What I Am", gate="passed", source="self_adversarial")
        verdict, reason = dc.admission_band(p, None)
        assert verdict == "review"
        assert "inherited_gate2" in reason
        assert called["n"] == 0

    def test_inherited_gate2_still_enters_protected_zone(self):
        """Regression guard for my creation-path fix: a legit inherited_gate2 proposal
        MUST still be able to auto-enter a protected zone (else I broke the main path)."""
        import unittest.mock as m
        import core.ddd_cultivation as dc
        p = self._p("SELF.md", "What I Am", gate="passed", source="inherited_gate2")
        with m.patch("core.ddd_auto_approval.evaluate_auto_approval") as me:
            me.return_value = type("D", (), {"criteria_met": {"small_magnitude": True, "circuit_breaker_ok": True}})()
            verdict, reason = dc.admission_band(p, None)
        assert verdict == "auto", f"inherited_gate2 must enter protected zone, got {verdict}:{reason}"

    def test_creation_path_sets_trust_source_for_inherited_pass(self, tmp_path, monkeypatch):
        """filter_lessons_for_ddd must set trust_source=inherited_gate2 when the source
        run passed — else the pre-drop wrongly bars every legit gate2 proposal."""
        import core.ddd_cultivation as dc
        monkeypatch.setattr(dc, "stamp_trust_from_run", lambda *a, **k: "passed")
        props = dc.filter_lessons_for_ddd(
            ["Prefer dedicated executors for blocking IO over the default pool."],
            run_id="run_abc", project="SwarmAI", project_dir=tmp_path)
        assert props, "expected a proposal"
        assert props[0].passed_adversarial_gate == "passed"
        assert props[0].trust_source == "inherited_gate2"

    def test_creation_path_na_source_is_none(self, tmp_path, monkeypatch):
        import core.ddd_cultivation as dc
        monkeypatch.setattr(dc, "stamp_trust_from_run", lambda *a, **k: "n/a")
        props = dc.filter_lessons_for_ddd(
            ["Prefer dedicated executors for blocking IO over the default pool."],
            run_id="run_abc", project="SwarmAI", project_dir=tmp_path)
        assert props and props[0].trust_source == "none"

    def test_predrop_selfadv_passed_protected_still_dropped(self):
        from core.ddd_cultivation import _predrop_is_protected_untrusted
        p = self._p("SELF.md", "X", gate="passed", source="self_adversarial")
        assert _predrop_is_protected_untrusted(p) is True

    def test_predrop_inherited_falls_through(self):
        from core.ddd_cultivation import _predrop_is_protected_untrusted
        p = self._p("SELF.md", "X", gate="passed", source="inherited_gate2")
        assert _predrop_is_protected_untrusted(p) is False
