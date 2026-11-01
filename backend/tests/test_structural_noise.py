"""structural_noise — the all-store structural noise floor (no length floor).

Tests ``core.extraction_patterns.structural_noise``: table fragments, agent
monologue, emoji-prefix bare status markers and source-type noise (UI-State
snapshots, build logs, attachment-carrying chat turns) are rejected; short real
fragments and emoji-opened titled entries are kept; empty text is rejected.
"""
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


class TestStructuralNoise:
    def test_empty_and_whitespace_are_noise(self):
        from core.extraction_patterns import structural_noise
        assert structural_noise("") is True
        assert structural_noise("   \n ") is True

    def test_structural_noise_has_no_length_floor(self):
        # A short decision fragment MEMORY keeps today must PASS structural_noise.
        from core.extraction_patterns import structural_noise
        assert structural_noise("enableMCP = always true") is False  # 4 words, NOT noise
        assert structural_noise("Batch writes per section") is False

    def test_structural_noise_catches_structural_junk(self):
        from core.extraction_patterns import structural_noise
        assert structural_noise("| col | col |") is True            # table fragment
        assert structural_noise("I'll diagnose the root cause") is True  # agent monologue
        assert structural_noise("✅ done") is True              # emoji-prefix BARE status marker

    def test_emoji_prefix_bare_status_is_noise(self):
        # run_85beeb04: a BARE emoji status marker (short, no titled content) is noise.
        from core.extraction_patterns import structural_noise
        assert structural_noise("✅ done") is True
        assert structural_noise("🟡 wip") is True
        assert structural_noise("❌ failed") is True

    def test_source_type_noise_is_caught(self):
        # ROOT-FIX (2026-08-10): the 16 entries cleaned from MEMORY this session were
        # conversation / UI-State / build-log fragments MIS-CLASSIFIED as knowledge —
        # syntactically well-formed, so the old structural_noise missed them all.
        # These have deterministic SOURCE signatures and must be caught at the door.
        from core.extraction_patterns import structural_noise
        # (a) injected UI-State snapshot
        assert structural_noise(
            "## Current UI State This is what you are currently showing the user "
            "in the app (a request-time snapshot — it may change as they interact)") is True
        assert structural_noise(
            "This is what you are currently showing the user in the app (a request-time snapshot)") is True
        # (b) build-log / gradle / brazil output
        assert structural_noise(
            "> Configure project : BrazilPlugin was loaded for project 'IVTHubClientConfig'") is True
        assert structural_noise("> Task :writeBrazilEnvironment Wrote Brazil environment variables") is True
        # (c) raw conversation carrying an attachment marker
        assert structural_noise(
            "那个左侧粗线条 不要加了 [Attached file: 08f4d5ca-d573-442d.png] saved at Attachments") is True

    def test_source_type_gate_does_not_eat_real_knowledge(self):
        # The source-type gate must be PRECISE — a real lesson that merely mentions
        # "UI state" or "build" or a file path must NOT be flagged.
        from core.extraction_patterns import structural_noise
        assert structural_noise(
            "The resume path must rebuild UI state from the backend snapshot, not client memory.") is False
        assert structural_noise(
            "A build-time claim must be verified by building, not by reading the comment.") is False
        assert structural_noise(
            "Guard at the shared-component caller, not the renderer (R27).") is False

    def test_emoji_prefixed_titled_entry_is_NOT_noise(self):
        # run_85beeb04 BUGFIX: a curated MEMORY entry that merely OPENS with an emoji but
        # carries a **bold title** + substantial content is NOT structural noise. The old
        # _EMOJI_PREFIX rule flagged these identically to '✅ done' → 8 real MEMORY
        # entries wrongly audited as noise.
        from core.extraction_patterns import structural_noise
        assert structural_noise("🟡 **Frontend reconcile race** — GUARDED (not fixed)") is False
        assert structural_noise("✅ **R6 Session arbitration** — IMPLEMENTED + DEPLOYED (2026-06)") is False
        assert structural_noise("⚠️ **E2E chain missing** — PARTIALLY RESOLVED: smoke_e2e.py added") is False

    def test_emoji_prefixed_monologue_is_STILL_noise(self):
        # Gate-2 over-correction guard (run_85beeb04): the emoji prefix must NOT let agent
        # monologue escape — AGENT_MONOLOGUE is ^-anchored so the emoji hides it; the fix
        # re-checks the stripped rest. These are long (>30 chars) with no bold, so they'd
        # escape the length/bold discriminator if the monologue re-check were missing.
        from core.extraction_patterns import structural_noise
        assert structural_noise("✅ I'll diagnose the root cause of this issue and fix it") is True
        assert structural_noise("🟡 Let me check the logs and then update the cache layer") is True

    def test_emoji_bold_check_requires_balanced_span(self):
        # Gate-2: the bold-title allowlist requires a BALANCED **…** span, not a stray '**'
        # substring. A single dangling '**' does NOT count as a title (so it can't be the
        # thing that rescues an entry from the noise floor).
        from core.extraction_patterns import _EMOJI_PREFIX
        import re
        # a real balanced span → recognized as a title
        assert bool(re.search(r"\*\*[^*]+\*\*", "**Real Title** — desc")) is True
        # a stray/dangling '**' → NOT a title
        assert bool(re.search(r"\*\*[^*]+\*\*", "** dangling no close")) is False
