"""Tests for core/memory_decay.py — Ebbinghaus + Hebbian memory decay scoring.

Tests the mathematical model, spacing effect, archive candidate detection,
reference bumping, and session scanning.
"""

import math
from datetime import date, timedelta

import pytest


# ── Decay Score Computation ──────────────────────────────────────────────────


class TestComputeDecayScore:
    """Ebbinghaus exponential decay with stability modifier."""

    def test_fresh_entry_score_near_one(self):
        """Entry referenced today should have score ~1.0."""
        from core.memory_decay import compute_decay_score

        today = date(2026, 6, 7)
        score = compute_decay_score(
            ref_count=1,
            sessions_referenced=1,
            last_referenced=today,
            created=today,
            today=today,
        )
        assert score >= 0.95

    def test_old_unreferenced_entry_decays(self):
        """Entry never referenced after creation decays exponentially."""
        from core.memory_decay import compute_decay_score

        today = date(2026, 6, 7)
        created = today - timedelta(days=90)
        score = compute_decay_score(
            ref_count=0,
            sessions_referenced=0,
            last_referenced=None,
            created=created,
            today=today,
        )
        # With stability=1.0, exp(-90/1.0) is near 0, but floor applies
        assert score == pytest.approx(0.05, abs=0.001)

    def test_floor_never_below_threshold(self):
        """Score never drops below STRENGTH_FLOOR (0.05)."""
        from core.memory_decay import compute_decay_score

        today = date(2026, 6, 7)
        created = today - timedelta(days=365)
        score = compute_decay_score(
            ref_count=0,
            sessions_referenced=0,
            last_referenced=None,
            created=created,
            today=today,
        )
        assert score >= 0.05

    def test_high_ref_count_decays_slower(self):
        """Entry with many references has higher stability → decays slower."""
        from core.memory_decay import compute_decay_score

        today = date(2026, 6, 7)
        # Use 5 days — short enough that high-stability entry is still alive
        # but low-stability entry has decayed significantly
        last_ref = today - timedelta(days=5)
        created = today - timedelta(days=120)

        score_low_ref = compute_decay_score(
            ref_count=1, sessions_referenced=1,
            last_referenced=last_ref, created=created, today=today,
        )
        score_high_ref = compute_decay_score(
            ref_count=10, sessions_referenced=8,
            last_referenced=last_ref, created=created, today=today,
        )
        assert score_high_ref > score_low_ref


class TestSpacingEffect:
    """Cepeda spacing effect: distributed > massed reinforcement."""

    def test_spaced_refs_better_than_burst(self):
        """5 sessions × 1 ref produces higher stability than 1 session × 5 refs."""
        from core.memory_decay import compute_stability

        stability_spaced = compute_stability(ref_count=5, sessions_referenced=5)
        stability_burst = compute_stability(ref_count=5, sessions_referenced=1)
        assert stability_spaced > stability_burst

    def test_stability_capped(self):
        """Stability never exceeds MAX_STABILITY."""
        from core.memory_decay import compute_stability, MAX_STABILITY

        stability = compute_stability(ref_count=100, sessions_referenced=50)
        assert stability <= MAX_STABILITY

    def test_zero_refs_baseline_stability(self):
        """Zero references gives baseline stability of 1.0."""
        from core.memory_decay import compute_stability, STABILITY_BASE

        stability = compute_stability(ref_count=0, sessions_referenced=0)
        assert stability == pytest.approx(STABILITY_BASE)


# ── Archive Candidates ───────────────────────────────────────────────────────


class TestArchiveCandidates:
    """Filter entries below threshold + minimum age."""

    def test_low_score_old_entry_is_candidate(self):
        """Entry with low decay score AND old enough → archive candidate."""
        from core.memory_decay import get_archive_candidates, EntryDecayInfo

        today = date(2026, 6, 7)
        entry = EntryDecayInfo(
            entry_id="RC15",
            ref_count=0,
            sessions_referenced=0,
            last_referenced=None,
            created=today - timedelta(days=90),
            section="Recent Context",
        )
        candidates = get_archive_candidates([entry], today)
        assert len(candidates) == 1
        assert candidates[0].entry_id == "RC15"

    def test_young_entry_never_candidate(self):
        """Entry younger than min_age_days is never a candidate."""
        from core.memory_decay import get_archive_candidates, EntryDecayInfo

        today = date(2026, 6, 7)
        entry = EntryDecayInfo(
            entry_id="RC01",
            ref_count=0,
            sessions_referenced=0,
            last_referenced=None,
            created=today - timedelta(days=30),
            section="Recent Context",
        )
        candidates = get_archive_candidates([entry], today)
        assert len(candidates) == 0

    def test_permanent_section_immune(self):
        """Entries in PERMANENT (evergreen) sections are never archive candidates.

        PERMANENT_SECTIONS now derives from the MEMORY_SECTIONS SSoT evergreen
        flag (R3 fix). "COE Registry" is evergreen → immune. Note: "Decisions"
        is deliberately NON-evergreen in the SSoT (decisions decay), so the old
        "Key Decisions" immunity was itself wrong — this test uses a genuinely
        evergreen section.
        """
        from core.memory_decay import get_archive_candidates, EntryDecayInfo, PERMANENT_SECTIONS

        today = date(2026, 6, 7)
        evergreen_section = "COE Registry"
        assert evergreen_section in PERMANENT_SECTIONS  # guard: this IS evergreen
        entry = EntryDecayInfo(
            entry_id="COE01",
            ref_count=0,
            sessions_referenced=0,
            last_referenced=None,
            created=today - timedelta(days=200),
            section=evergreen_section,
        )
        candidates = get_archive_candidates([entry], today)
        assert len(candidates) == 0


# ── Session Scanning ─────────────────────────────────────────────────────────


class TestScanSessionForMemoryRefs:
    """Detect MEMORY entry identifiers in session messages."""

    def test_finds_entry_ids_in_messages(self):
        """Detects KD01, LL03 etc. patterns in message content."""
        from core.memory_decay import scan_session_for_memory_refs

        messages = [
            {"role": "user", "content": "check KD01 about maxTurns"},
            {"role": "assistant", "content": "Based on KD01 and LL08, the fix is..."},
        ]
        entry_ids = {"KD01", "KD02", "LL08", "RC01"}
        found = scan_session_for_memory_refs(messages, entry_ids)
        assert found == {"KD01", "LL08"}

    def test_no_false_positives_on_similar_text(self):
        """Don't match partial IDs like 'KD' without number."""
        from core.memory_decay import scan_session_for_memory_refs

        messages = [
            {"role": "assistant", "content": "This is about knowledge discovery"},
        ]
        entry_ids = {"KD01", "KD02"}
        found = scan_session_for_memory_refs(messages, entry_ids)
        assert found == set()


# ── Metadata Bumping ─────────────────────────────────────────────────────────


class TestBumpEntryReferences:
    """Update inline metadata comments in MEMORY.md content."""

    def test_bumps_ref_count_and_last(self):
        """Increments ref count and updates last_referenced date."""
        from core.memory_decay import bump_entry_references

        content = (
            "- [KD01] 2026-06-01 CLI maxTurns fix\n"
            "  <!-- ref:2 | last:2026-06-03 | decay:active | sessions:1 -->\n"
        )
        today = date(2026, 6, 7)
        result = bump_entry_references(content, {"KD01"}, today)
        assert "ref:3" in result
        assert "last:2026-06-07" in result
        assert "sessions:2" in result

    def test_creates_metadata_if_missing(self):
        """Adds metadata comment if entry has none."""
        from core.memory_decay import bump_entry_references

        content = "- [KD05] 2026-05-22 Ontology scope\n\n"
        today = date(2026, 6, 7)
        result = bump_entry_references(content, {"KD05"}, today)
        assert "<!-- ref:1 | last:2026-06-07 | decay:active | sessions:1 -->" in result

    def test_multiline_entry_body_before_metadata(self):
        """Entry with content lines between header and metadata — no duplication."""
        from core.memory_decay import bump_entry_references

        content = (
            "- [KD01] 2026-06-01 CLI maxTurns fix\n"
            "  Impact: reduced confusion in long sessions\n"
            "  <!-- ref:2 | last:2026-06-03 | decay:active | sessions:1 -->\n"
        )
        today = date(2026, 6, 7)
        result = bump_entry_references(content, {"KD01"}, today)
        # Should update existing metadata, not create duplicate
        assert result.count("<!-- ref:") == 1
        assert "ref:3" in result
        assert "sessions:2" in result


class TestListContentHandling:
    """Handle Anthropic Messages API list-type content blocks."""

    def test_list_content_blocks_scanned(self):
        """Messages with list-type content are handled correctly."""
        from core.memory_decay import scan_session_for_memory_refs

        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Based on KD01, the fix is..."},
                    {"type": "image", "source": {"data": "..."}},
                ],
            },
        ]
        entry_ids = {"KD01", "KD02"}
        found = scan_session_for_memory_refs(messages, entry_ids)
        assert found == {"KD01"}


class TestUsageRefBridge:
    """R2-real (run_77504e11): bridge the LIVE .memory-usage.json signal to body
    entry ref_count. ref is consumed by _is_reclaimable_noise (protect from strip).
    Only GENUINELY-used entries (usage >= threshold) get a log-damped ref so
    reclaim isn't neutered for all."""

    def test_build_usage_ref_map_joins_id_to_title(self):
        from core.memory_decay import build_usage_ref_map
        memory = (
            "<!-- MEMORY_INDEX_START -->\n"
            "- [COE02] Slack bot scope issue | alias, foo\n"
            "- [PIT99] Some rare pitfall | bar\n"
            "<!-- MEMORY_INDEX_END -->\n"
            "## Pitfalls\n"
            "- [pitfall] **Slack bot scope issue** — body (2026-06-01)\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
        )
        usage = {"COE02": 50, "PIT99": 2}  # PIT99 below threshold
        m = build_usage_ref_map(memory, usage, threshold=10)
        # COE02 (usage 50, >= threshold) → title mapped, log-damped
        assert ("COE Registry", "Slack bot scope issue") in m
        assert m[("COE Registry", "Slack bot scope issue")] == round(__import__("math").log2(50 + 1))  # ~6
        # PIT99 below threshold → NOT in map (stays ref:0, reclaim-eligible)
        assert ("Pitfalls", "Some rare pitfall") not in m

    def test_below_threshold_not_protected(self):
        """NEGATIVE: low-usage entries stay absent → ref:0 → reclaim still works."""
        from core.memory_decay import build_usage_ref_map
        memory = (
            "<!-- MEMORY_INDEX_START -->\n- [GUI50] Minor thing | x\n<!-- MEMORY_INDEX_END -->\n"
            "## Guidelines\n- [guideline] **Minor thing** — body (2026-06-01)\n"
        )
        m = build_usage_ref_map(memory, {"GUI50": 3}, threshold=10)
        assert m == {}

    def test_log_damping_caps_monopoly(self):
        """usage 142 must NOT become ref 142 — log-damped to keep proportion sane."""
        from core.memory_decay import build_usage_ref_map
        import math
        memory = (
            "<!-- MEMORY_INDEX_START -->\n- [MOD00] Hot entry | x\n<!-- MEMORY_INDEX_END -->\n"
            "## Models\n- [model] **Hot entry** — body (2026-06-01)\n"
        )
        m = build_usage_ref_map(memory, {"MOD00": 142}, threshold=10)
        assert m[("Models", "Hot entry")] == round(math.log2(143))  # ~7, not 142

    def test_pipe_in_title_not_broken(self):
        """Index line splits on FIRST ' | ' only; title before it preserved."""
        from core.memory_decay import build_usage_ref_map
        memory = (
            "<!-- MEMORY_INDEX_START -->\n- [DEC01] A or B decision | alias\n<!-- MEMORY_INDEX_END -->\n"
            "## Decisions\n- [decision] **A or B decision** — body (2026-06-01)\n"
        )
        m = build_usage_ref_map(memory, {"DEC01": 20}, threshold=10)
        assert ("Decisions", "A or B decision") in m


class TestUsageDecay:
    """Write-time exponential decay that kills the cumulative ratchet (run_81f6d20c).

    The producer (.memory-usage.json) counts every [ID] citation cumulatively and
    never decremented → a once-hot-now-cold entry stayed protected forever.
    decay_usage_counts applies 0.5**(days/halflife) at write time so cold entries
    fade below the protection threshold (10) and eventually below epsilon (dropped).
    """

    def test_halflife_halves_count(self):
        """After exactly one half-life, a count is halved."""
        from core.memory_decay import decay_usage_counts, USAGE_HALFLIFE_DAYS

        out = decay_usage_counts({"PIT07": 40.0}, int(USAGE_HALFLIFE_DAYS))
        assert out["PIT07"] == pytest.approx(20.0, rel=0.01)

    def test_zero_or_negative_elapsed_is_identity(self):
        """days_elapsed <= 0 → unchanged (same-day re-run must not double-decay)."""
        from core.memory_decay import decay_usage_counts

        src = {"PIT07": 40.0, "GUI99": 12.0}
        assert decay_usage_counts(src, 0) == src
        assert decay_usage_counts(src, -5) == src

    def test_epsilon_drops_faded_keys(self):
        """A key decayed below epsilon is removed entirely (file hygiene + window-out)."""
        from core.memory_decay import decay_usage_counts, USAGE_HALFLIFE_DAYS

        # 1.0 count, 5 half-lives → 0.03125 < 0.5 epsilon → dropped
        out = decay_usage_counts({"DEC01": 1.0}, int(USAGE_HALFLIFE_DAYS) * 5, epsilon=0.5)
        assert "DEC01" not in out

    def test_ratchet_broken_cold_entry_loses_protection(self):
        """THE GOAL: a once-hot entry uncited long enough drops below threshold=10.

        30 citations, decayed enough half-lives, falls under USAGE_REF_THRESHOLD →
        build_usage_ref_map no longer protects it.
        """
        from core.memory_decay import (
            decay_usage_counts,
            build_usage_ref_map,
            USAGE_HALFLIFE_DAYS,
            USAGE_REF_THRESHOLD,
        )

        # 30 → after 2 half-lives = 7.5 < 10 threshold
        decayed = decay_usage_counts({"PIT07": 30.0}, int(USAGE_HALFLIFE_DAYS) * 2)
        assert decayed["PIT07"] < USAGE_REF_THRESHOLD

        memory = (
            "<!-- MEMORY_INDEX_START -->\n- [PIT07] Cold entry | x\n<!-- MEMORY_INDEX_END -->\n"
            "## Pitfalls\n- [pitfall] **Cold entry** — body (2026-06-01)\n"
        )
        # below threshold → not protected (ratchet broken)
        assert build_usage_ref_map(memory, decayed) == {}

    def test_in_window_entry_stays_protected(self):
        """COMPANION: an entry cited recently (small elapsed) keeps its count above threshold."""
        from core.memory_decay import decay_usage_counts, USAGE_REF_THRESHOLD

        # 30 citations, only 3 days elapsed → barely decays, stays well above 10
        out = decay_usage_counts({"PIT07": 30.0}, 3)
        assert out["PIT07"] >= USAGE_REF_THRESHOLD

    def test_readers_float_safe_after_decay(self):
        """build_usage_ref_map handles float values produced by decay (round(log2)) without error."""
        from core.memory_decay import build_usage_ref_map

        memory = (
            "<!-- MEMORY_INDEX_START -->\n- [MOD00] Hot | x\n<!-- MEMORY_INDEX_END -->\n"
            "## Models\n- [model] **Hot** — body (2026-06-01)\n"
        )
        # float usage (post-decay) must not crash and must still damp
        m = build_usage_ref_map(memory, {"MOD00": 51.7})
        assert m[("Models", "Hot")] == round(math.log2(52.7))

# ── SSoT Prefix Coverage (regression: the extinct-prefix blind spot) ──────────


class TestSSoTPrefixCoverage:
    """The entry-ID patterns MUST cover every MEMORY_SECTIONS SSoT prefix.

    Regression guard for the write-side-trim outage: _ENTRY_ID_RE and
    _ENTRY_HEADER_RE were hardcoded to the pre-PRI01 set `KD|LL|RC|COE|OT`.
    After PRI01 renamed the sections the live prefixes became PRI/COR/DEC/GUI/
    PIT/PRC/MOD/COE/OT/SP, so the literal matched only COE and OT — and both of
    those sections are evergreen (decay-immune). Of 323 live body entries, 0
    decay-ELIGIBLE ones matched, so scan/bump could never write a single `ref:`
    comment, which starved _enforce_section_caps' decay-ranked eviction and let
    .context grow unbounded.

    These tests fail loudly if a future prefix rename outruns the patterns again.
    """

    def test_every_ssot_prefix_matches_entry_header(self):
        """Every SSoT prefix must match the body-entry header pattern."""
        from core.ddd_entry_lifecycle import MEMORY_PREFIX_TO_SECTION
        from core.memory_decay import _ENTRY_HEADER_RE

        uncovered = sorted(
            p for p in MEMORY_PREFIX_TO_SECTION
            if not _ENTRY_HEADER_RE.match(f"- [{p}01]")
        )
        assert uncovered == [], (
            f"SSoT prefixes invisible to _ENTRY_HEADER_RE: {uncovered}. "
            "Entries in those sections can never earn decay metadata."
        )

    def test_every_ssot_prefix_matches_inline_id(self):
        """Every SSoT prefix must be detectable when cited inline in a transcript."""
        from core.ddd_entry_lifecycle import MEMORY_PREFIX_TO_SECTION
        from core.memory_decay import _ENTRY_ID_RE

        uncovered = sorted(
            p for p in MEMORY_PREFIX_TO_SECTION
            if _ENTRY_ID_RE.findall(f"as noted in {p}42 we should") != [f"{p}42"]
        )
        assert uncovered == [], f"SSoT prefixes invisible to _ENTRY_ID_RE: {uncovered}"

    def test_high_volume_decay_eligible_prefixes_covered(self):
        """GUI/PIT/DEC carry the bulk of decay-eligible entries — named explicitly.

        These three are the non-evergreen, high-churn sections (~283 of 323 live
        body entries). They are exactly what the old literal missed, so assert
        them by name to make a regression's failure message unambiguous.
        """
        from core.memory_decay import _ENTRY_HEADER_RE, _ENTRY_ID_RE

        for entry_id in ("GUI138", "PIT106", "DEC39"):
            assert _ENTRY_HEADER_RE.match(f"- [{entry_id}] some entry"), entry_id
            assert entry_id in _ENTRY_ID_RE.findall(f"see {entry_id} for detail")

    def test_both_patterns_share_one_prefix_alternation(self):
        """Structural anti-drift: the two patterns are built from one constant.

        The original code carried a comment warning they must stay "aligned" —
        a rule a human had to remember. Sharing _ID_PREFIX_ALT makes divergence
        impossible rather than merely discouraged.
        """
        from core.memory_decay import _ID_PREFIX_ALT, _ENTRY_HEADER_RE, _ENTRY_ID_RE

        for prefix in _ID_PREFIX_ALT.split("|"):
            assert _ENTRY_HEADER_RE.match(f"- [{prefix}07]"), prefix
            assert _ENTRY_ID_RE.findall(f"{prefix}07") == [f"{prefix}07"], prefix

    def test_legacy_prefixes_still_parse_for_archives(self):
        """Pre-PRI01 IDs must still parse so archived files remain readable."""
        from core.memory_decay import _ENTRY_HEADER_RE

        for entry_id in ("KD01", "LL08", "RC15"):
            assert _ENTRY_HEADER_RE.match(f"- [{entry_id}] archived entry"), entry_id

    def test_legacy_prefix_set_is_closed(self):
        """The legacy set is historical and must never grow — new prefixes go in the SSoT."""
        from core.memory_decay import _LEGACY_ID_PREFIXES

        assert _LEGACY_ID_PREFIXES == frozenset({"KD", "LL", "RC"})

    def test_prose_does_not_produce_false_ids(self):
        """Bare prefixes without digits must not be mistaken for entry IDs."""
        from core.memory_decay import _ENTRY_ID_RE

        prose = "knowledge discovery, DECIDE quickly, SPARC review, PRINCIPLES doc"
        assert _ENTRY_ID_RE.findall(prose) == []


# ── Producer → Consumer chain (INV-5: a test that ACTUALLY enters the path) ───


class TestDecayMetadataProducerConsumerChain:
    """End-to-end: transcript citation → scan → bump → decay-ranked eviction.

    Why this test exists. The pre-existing eviction test
    (test_memory_promotion.TestUsageBasedEviction) HAND-WROTE the 5-field decay
    metadata into its fixture, so it verified the consumer's math while the
    producer was structurally incapable of emitting that metadata for any live
    prefix. Green consumer + dead producer is exactly how this outage shipped
    unnoticed: nothing tested the seam between them.

    This test drives the REAL producer (scan_session_for_memory_refs →
    bump_entry_references) and only then hands off to the REAL consumer
    (_enforce_section_caps), using CURRENT SSoT prefixes.

    Against the old `KD|LL|RC|COE|OT` pattern it fails: scan finds nothing, bump
    is a no-op, decay_available stays False, and eviction degrades to
    position-only (bottom-first) — dropping GUI03 instead of preserving it.
    """

    @staticmethod
    def _memory_with_guidelines() -> str:
        return (
            "# Memory\n\n"
            "## Guidelines\n"
            "- [GUI01] 2026-01-01: Prefer SSoT-derived patterns\n"
            "- [GUI02] 2026-01-02: Uncited filler A\n"
            "- [GUI03] 2026-01-03: Commit your own files immediately\n"
            "- [GUI04] 2026-01-04: Uncited filler B\n"
        )

    @staticmethod
    def _transcript() -> list[dict]:
        return [
            {"role": "user", "content": "does GUI01 still hold?"},
            {"role": "assistant", "content": "Yes — and GUI03 applies here too."},
        ]

    def test_producer_step1_scan_finds_current_prefix_ids(self):
        """Step 1: a transcript citing GUI ids yields exactly those ids."""
        from core.memory_decay import scan_session_for_memory_refs, _ENTRY_HEADER_RE

        content = self._memory_with_guidelines()
        entry_ids = set(_ENTRY_HEADER_RE.findall(content))
        assert entry_ids == {"GUI01", "GUI02", "GUI03", "GUI04"}

        found = scan_session_for_memory_refs(self._transcript(), entry_ids)
        assert found == {"GUI01", "GUI03"}

    def test_producer_step2_bump_emits_parseable_five_field_metadata(self):
        """Step 2: bump emits the exact comment shape the CONSUMER's parser reads.

        Asserted via the consumer's own _META_RE rather than a substring match,
        so a format drift between producer and consumer cannot pass.
        """
        from core.memory_decay import bump_entry_references, _META_RE

        result = bump_entry_references(
            self._memory_with_guidelines(), {"GUI01", "GUI03"}, date(2026, 8, 6)
        )
        assert result.count("<!-- ref:") == 2

        parsed = _META_RE.findall(result)
        assert len(parsed) == 2, "consumer parser must accept what the producer wrote"
        assert all(fields[1] == "1" for fields in parsed)   # ref:1
        assert all(fields[2] == "2026-08-06" for fields in parsed)  # last:
        assert all(fields[4] == "1" for fields in parsed)   # sessions:1

    def test_consumer_evicts_uncited_and_keeps_cited(self, tmp_path):
        """Step 3 (the seam): real producer output drives decay-ranked eviction.

        Cited entries score ~1.0; uncited ones fall to the neutral 0.5 default,
        so the cap trims the uncited pair. Under the old pattern the producer
        emitted nothing and the consumer fell back to position-only eviction,
        which drops GUI03/GUI04 — so this assertion is what discriminates the fix.
        """
        import hooks.distillation_hook as dh
        from hooks.distillation_hook import DistillationTriggerHook
        from core.memory_decay import (
            _ENTRY_HEADER_RE,
            bump_entry_references,
            scan_session_for_memory_refs,
        )

        content = self._memory_with_guidelines()
        entry_ids = set(_ENTRY_HEADER_RE.findall(content))
        referenced = scan_session_for_memory_refs(self._transcript(), entry_ids)
        # Real producer output — NOT a hand-written fixture.
        bumped = bump_entry_references(content, referenced, date.today())

        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(bumped, encoding="utf-8")
        (tmp_path / "Knowledge" / "Archives").mkdir(parents=True, exist_ok=True)

        original_caps = dh.SECTION_CAPS.copy()
        try:
            dh.SECTION_CAPS["Guidelines"] = 2
            DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)
        finally:
            dh.SECTION_CAPS.clear()
            dh.SECTION_CAPS.update(original_caps)

        result = memory_path.read_text(encoding="utf-8")
        assert "[GUI01]" in result, "cited entry must survive decay-ranked eviction"
        assert "[GUI03]" in result, "cited entry must survive decay-ranked eviction"
        assert "[GUI02]" not in result, "uncited entry should be evicted"
        assert "[GUI04]" not in result, "uncited entry should be evicted"
