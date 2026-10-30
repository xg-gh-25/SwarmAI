"""Tests for ContextHealthHook's MEMORY admission gate.

The decay->archive->strip lifecycle these tests used to drive is gone: every entry
in a main file is in force (design 2026-10-30-brain-driven §2.3). What remains is
the Step-6 episodic war-story gate on the MEMORY hot path.
"""
from __future__ import annotations


class TestEpisodicWarStoryGate:
    """Step-6 MEMORY admission gate (run_117bcdf4): a REFLECT lesson that OPENS by
    narrating a single run-event is HELD BACK from the MEMORY hot path; a genuine
    semantic rule (even one citing a run as trailing attribution) is ADMITTED.

    Mutation contract: remove the Step-6 clause in _admit_lesson_to_memory →
    test_warstory_lessons_held_back goes RED (all 8 would re-ADMIT).
    """

    # Real archived war-stories (the 92-entry decay-archive source, 2026-07-28).
    WARSTORIES = [
        "Gate-2 caught a case-sensitivity false-drift: comparing a git SHA against a verbatim-stored anchor fires a false positive",
        "Gate-1 blocked a layer-2 that was head-position bias in disguise",
        "5th consecutive C042 catch this session: Gate-1 fresh-context skeptic BLOCKED a NEW wrapper function",
        "GUI122 RECURRED (mine, this run): I used git checkout to revert a mutation-test change",
        "Gate-2 又抓到 CLASS-A test-theater: 我自写的 cross-turn-bleed green test 是 vacuous",
        "M3 skeptic caught 2 CLASS-B framing errors pre-code",
        "adversarial gate corrected my root-cause NARRATIVE not just found bugs",
        "Gate-0 M3 skeptic flipped a WRONG-FRAME frontend-primary to backend-primary",
    ]
    # War-story shapes with a NON-opener actor position — verb-before-actor (passive)
    # and leading run-id / session deixis. The first cut (opener-anchored) MISSED
    # these (Gate-2 adversarial HIGH, run_117bcdf4); they must HOLD-BACK.
    WARSTORIES_NONOPENER = [
        "In run_x, Gate-2 caught a case-sensitivity false-drift",
        "Caught by Gate-2: a vacuous cross-turn test",
        "This session's 3rd catch: Gate-1 blocked a wrapper",
    ]
    # Genuine semantic rules — must be ADMITTED (run-id cited as attribution ≠ episodic).
    # The trailing block (RULES ABOUT THE GATING SYSTEM) are the Gate-2 adversarial
    # CRITICAL false-positives (run_117bcdf4): a gate ACTOR but NO run-event verb → a
    # reusable rule, not a war-story. A bare-topic detector wrongly dropped them;
    # keeping them here is the anti-test-theater guard (they exercise the FP branch).
    SEMANTIC = [
        "A fix that ADDS a liveness/verification check can REINTRODUCE the very false-positive it targets: make unknown a distinct safe state",
        "A new enum value on a widely-consumed type needs a GREP of every consumer, not just the primary one",
        "write→read mismatch is a recurring bug class — close BOTH halves in one change",
        "fail-closed has a silent-death twin — always pair it with a positive counter",
        "护栏的 verified 字段必须 isinstance(bool) 显式判类型,LLM 常把 bool 序列化成字符串绕过 is True",
        "Gate-2 must always run before merge; a fresh-context reviewer is the only defense against author bias",
        "Gate-1 blocks are cheaper than production incidents: invest in the skeptic pass",
        "adversarial gate design should assume the author is overconfident and wrong",
        "M3 skeptic passes must precede any code write in the pipeline",
    ]

    def test_detector_flags_warstories(self):
        from hooks.context_health_hook import _is_episodic_warstory
        for l in self.WARSTORIES + self.WARSTORIES_NONOPENER:
            assert _is_episodic_warstory(l), f"war-story not flagged: {l[:60]}"

    def test_detector_passes_semantic(self):
        from hooks.context_health_hook import _is_episodic_warstory
        for l in self.SEMANTIC:
            assert not _is_episodic_warstory(l), f"semantic wrongly flagged: {l[:60]}"
