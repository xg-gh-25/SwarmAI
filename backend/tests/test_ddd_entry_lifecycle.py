"""Tests for ddd_entry_lifecycle — per-entry knowledge parsing and maintenance.

Tests cover: parsing, type classification, metadata injection, supersession and the
exact-duplicate selector. Uses real IMPROVEMENT.md format. Entries no longer decay
with age (design 2026-10-30-brain-driven §2.3).
"""

from datetime import date

from core.ddd_entry_lifecycle import (
    EntryMetadata,
    classify_entry_type,
    parse_entries,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

SAMPLE_CONTENT = """\
## What Worked
<!-- maturity: growing | sources: 3 | verified: true | used: true | days: 45 | trust: high | promoted: 2026-05-01 -->

- [guideline] **Adversarial review caught dead code** — builder's own review found 0 findings. Sub-agent found 14. (2026-05-16, run_0bbba678)
  <!-- ref:3 | last:2026-05-18 | decay:active -->

- [pitfall] **5 rounds of review = process failure** — not thoroughness. Root cause: never ran full suite. (2026-04-28, run_91a6fb7e)
  <!-- ref:7 | last:2026-05-14 | decay:active -->

- [decision] **Copy-then-own > fork-and-depend** — copied 73 files from video-podcast-maker. (2026-04-26, run_d72c69f9)
  <!-- ref:1 | last:2026-04-26 | decay:active -->

- [guideline] **Old entry no refs** — something old with no references ever. (2025-11-01, run_old)
  <!-- ref:0 | last:none | decay:active -->

## What Failed
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- [pitfall] **dry_run=True that never flips** — feature permanently disabled. (2026-05-03, run_a27acb60)
  <!-- ref:2 | last:2026-05-10 | decay:active -->
"""

SAMPLE_NO_METADATA = """\
## What Worked
<!-- maturity: sparse | sources: 1 | verified: false | used: false | days: 0 | trust: moderate | promoted: none -->

- **Adversarial review caught dead code** — builder's own review found 0 findings. (2026-05-16, run_0bbba678)

- **5 rounds of review = process failure** — Root cause: never ran full suite. (2026-04-28, run_91a6fb7e)
"""


# ── AC2: parse_entries extracts metadata correctly ───────────────────────────

class TestParseEntries:
    def test_parses_all_entries(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert len(entries) == 5

    def test_extracts_type(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].entry_type == "guideline"
        assert entries[1].entry_type == "pitfall"
        assert entries[2].entry_type == "decision"

    def test_extracts_title(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].title == "Adversarial review caught dead code"
        assert entries[1].title == "5 rounds of review = process failure"

    def test_extracts_ref_count(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].ref_count == 3
        assert entries[1].ref_count == 7
        assert entries[3].ref_count == 0

    def test_extracts_last_referenced(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].last_referenced == date(2026, 5, 18)
        assert entries[3].last_referenced is None  # "none"

    def test_extracts_decay_state(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].decay_state == "active"

    def test_extracts_created_date(self):
        entries = parse_entries(SAMPLE_CONTENT)
        assert entries[0].created_date == date(2026, 5, 16)
        assert entries[2].created_date == date(2026, 4, 26)

    def test_entries_without_metadata_get_defaults(self):
        entries = parse_entries(SAMPLE_NO_METADATA)
        assert len(entries) == 2
        assert entries[0].ref_count == 0
        assert entries[0].last_referenced is None
        assert entries[0].decay_state == "active"
        assert entries[0].title == "Adversarial review caught dead code"

    def test_entries_without_type_default_guideline(self):
        entries = parse_entries(SAMPLE_NO_METADATA)
        # No [type] prefix → classified by heuristic or default
        assert entries[0].entry_type in ("guideline", "pitfall", "decision")

    def test_empty_content_returns_empty(self):
        assert parse_entries("") == []
        assert parse_entries("# No sections here") == []


# ── AC6: classify_entry_type returns correct type ────────────────────────────

class TestClassifyEntryType:
    def test_guideline_signals(self):
        assert classify_entry_type("Pattern: always use atomic commits") == "guideline"
        assert classify_entry_type("Rule: never skip adversarial review") == "guideline"

    def test_pitfall_signals(self):
        assert classify_entry_type("The bug was a race condition in async path") == "pitfall"
        assert classify_entry_type("This broke CI because of failed import") == "pitfall"

    def test_decision_signals(self):
        assert classify_entry_type("We chose httpx over requests because of async") == "decision"
        assert classify_entry_type("Selected event-driven approach vs polling") == "decision"

    def test_ambiguous_defaults_to_guideline(self):
        assert classify_entry_type("Something happened in the code") == "guideline"


# ── AC7: evaluate_demotion ───────────────────────────────────────────────────

class TestParseEntriesProseOptIn:
    """run_748f14a7 (Gate-2 convergence): emoji-prefixed prose bullets parse ONLY
    with include_prose=True. The DEFAULT (narrow) matcher must NOT see them — that
    is what keeps the autonomous inject path off curated prose. The
    prose matcher is ADDITIVE for real entries: [type]/plain-bold parse identically."""

    OT = """## Open Threads
- 🟡 **Frontend reconcile race** — #1 recurring bug. (2026-06-25, run_x)
- 🟢 **WS1 DEPLOYED** — verified live. (2026-07-01, run_y)
- 🔵 **MCP Gateway** — deferred. (2026-06-01, run_z)
"""

    def test_default_narrow_does_NOT_parse_prose(self):
        """CORE SAFETY: default parse_entries must ignore emoji-prose (autonomous paths use this)."""
        entries = parse_entries(self.OT)  # include_prose defaults False
        assert entries == [], f"default matcher wrongly parsed prose: {[e.title for e in entries]}"

    def test_include_prose_parses_emoji_entries(self):
        entries = parse_entries(self.OT, include_prose=True)
        titles = [e.title for e in entries]
        assert titles == ["Frontend reconcile race", "WS1 DEPLOYED", "MCP Gateway"], titles

    def test_emoji_stripped_from_title(self):
        """group(2) title must NOT leak the leading emoji/space."""
        content = "## Open Threads\n- 🟡 **Clean title** — text. (2026-06-25, run_x)\n"
        entries = parse_entries(content, include_prose=True)
        assert len(entries) == 1
        assert entries[0].title == "Clean title", f"emoji leaked into title: {entries[0].title!r}"

    def test_bracket_in_bold_stays_in_title_not_type(self):
        """Gate-1 HIGH: '- ✅ **[CLOSED] x**' — [CLOSED] is INSIDE the bold → part of
        the TITLE, not the [type] group."""
        content = "## Open Threads\n- ✅ **[CLOSED] gate skeptics** — done. (2026-07-01, run_x)\n"
        entries = parse_entries(content, include_prose=True)
        assert len(entries) == 1
        assert entries[0].title == "[CLOSED] gate skeptics"
        assert entries[0].entry_type != "CLOSED", "must NOT parse [CLOSED] as a type"

    def test_existing_entries_parse_identically_both_modes(self):
        """ADDITIVE: normal [type]/plain-bold entries unchanged in BOTH modes (no group drift)."""
        for prose in (False, True):
            entries = parse_entries(SAMPLE_CONTENT, include_prose=prose)
            assert len(entries) == 5, f"prose={prose}"
            assert entries[0].entry_type == "guideline"
            assert entries[0].title == "Adversarial review caught dead code"
            assert entries[2].entry_type == "decision"

    def test_prose_mode_covers_arrows_and_shapes(self):
        """Gate-2 re-review MED: the glyph range must cover arrow/shape/media leads that
        appear in REAL curated content (e.g. IMPROVEMENT.md '- → **BLOCK...**', U+2192;
        also ▶️ ◀️ ⏸), not just circle/check emoji — else those entries are un-retirable."""
        content = """## Open Threads
- → **Arrow-led decision** — real curated shape. (2026-06-25, run_x)
- ▶️ **Play-led** — media control lead. (2026-07-01, run_y)
- ✅ **Check-led** — common status. (2026-07-01, run_z)
"""
        titles = [e.title for e in parse_entries(content, include_prose=True)]
        assert titles == ["Arrow-led decision", "Play-led", "Check-led"], titles

    def test_prose_mode_rejects_structural_markdown(self):
        """Gate-2 MED-1: the emoji class is an explicit allowlist, NOT a broad negation —
        it must NOT swallow blockquote '>', table '|', ':' '@' '#' leads as false entries."""
        content = """## Open Threads
- 🟡 **Real entry** — text. (2026-06-25, run_x)
  - nested plain bullet should not parse
- just plain text no bold
- `code snippet` in a bullet
- 2026-07-03: a no-bold date line
- > **Blockquote in bullet** should not parse
- | **Table header** | col |
- @ **at-lead** not an entry
"""
        entries = parse_entries(content, include_prose=True)
        titles = [e.title for e in entries]
        assert titles == ["Real entry"], f"prose matcher over-matched: {titles}"


# ── Bi-temporal supersession (失效但留链, run_24299917) ──────────────────────
#
# A superseded DDD entry keeps its lineage: it is FILTERED from default recall and
# NEVER deleted. Storage-first / human-marked (方案A). Field vocabulary
# (superseded_by) aligns with memory_index's temporal format but the DDD engine owns
# its own _META_RE + disposition (filter), not memory_index's key-anchor + 0.1x
# down-weight.

class TestSupersession:
    def _entry(self, **kw):
        base = dict(title="T", entry_type="guideline", ref_count=0,
                    decay_state="active", created_date=date(2026, 1, 1))
        base.update(kw)
        return EntryMetadata(**base)

    # AC1 — round-trip byte-stability
    def test_marked_entry_roundtrips(self):
        e = self._entry(valid_until=date(2026, 7, 30), superseded_by="new-anchor")
        parsed = parse_entries(f"## S\n- **T** — body (2026-01-01)\n{e.to_comment()}\n")
        assert len(parsed) == 1
        assert parsed[0].valid_until == date(2026, 7, 30)
        assert parsed[0].superseded_by == "new-anchor"

    def test_unmarked_comment_byte_identical_to_legacy(self):
        # The critical backward-compat guarantee: an entry with no supersession
        # emits EXACTLY today's comment shape (no valid_until/superseded_by segment).
        # The dead ref/last/decay fields are read, never written (2026-11-02).
        e = self._entry(ref_count=3, last_referenced=date(2026, 5, 18),
                        decay_state="active", source="auto")
        assert e.to_comment() == "  <!-- source:auto -->"
        e2 = self._entry(ref_count=0, last_referenced=None, decay_state="active")
        assert e2.to_comment() == ""

    def test_legacy_comment_still_parses(self):
        # Existing on-disk comments (no new fields) must parse with None defaults.
        parsed = parse_entries(
            "## S\n- [guideline] **Old** — x (2026-01-01)\n"
            "  <!-- ref:5 | last:2026-05-18 | decay:active | source:auto -->\n"
        )
        assert len(parsed) == 1
        assert parsed[0].valid_until is None
        assert parsed[0].superseded_by is None
        assert parsed[0].ref_count == 5

    def test_superseded_without_source_parses(self):
        # source is optional AND comes before the new fields — a comment with
        # superseded_by but NO source must still parse (independent optional groups).
        parsed = parse_entries(
            "## S\n- **T** — body (2026-01-01)\n"
            "  <!-- ref:0 | last:none | decay:active | superseded_by:new-x -->\n"
        )
        assert len(parsed) == 1
        assert parsed[0].superseded_by == "new-x"
        assert parsed[0].source == ""


class TestSupersessionRecall:
    def _doc(self, superseded: bool):
        mark = " | superseded_by:new-x" if superseded else ""
        return {
            "IMPROVEMENT.md": (
                "## What Failed\n"
                "- [guideline] **Zebra quokka mechanism** — a very distinctive lesson"
                " about zebra quokka handling (2026-01-01)\n"
                f"  <!-- ref:0 | last:none | decay:active{mark} -->\n"
            )
        }

    def test_recall_never_filters_a_superseded_mark(self):
        """Every entry in a main file is in force (brain-driven §2.3, I4): a mark never
        hides an entry from recall; a replaced entry leaves the file instead."""
        from core.recall_multi import _ddd_entry_hits
        q = "zebra quokka mechanism"
        assert len(_ddd_entry_hits(q, self._doc(superseded=False), 5)) >= 1
        assert len(_ddd_entry_hits(q, self._doc(superseded=True), 5)) >= 1


class TestSupersessionGate2Fixes:
    """Gate-2 adversarial findings (run_24299917): spaced-title anchor (HIGH) +
    recall body false-positive (MED)."""

    # HIGH — a real title anchor has SPACES; must round-trip + preserve ref/last

    # MED — an ACTIVE entry whose BODY contains "superseded_by:x" must NOT be filtered
    def test_recall_not_fooled_by_body_mentioning_superseded(self):
        from core.recall_multi import _ddd_entry_hits
        # This mirrors THIS feature's own lesson entry, which discusses superseded_by.
        doc = {"IMPROVEMENT.md": (
            "## What Worked\n"
            "- [guideline] **Wombat telemetry pipeline** — the fix sets"
            " superseded_by:new_anchor on replaced wombat entries (2026-01-01)\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
        )}
        hits = _ddd_entry_hits("wombat telemetry pipeline", doc, 5)
        assert len(hits) >= 1, "active entry wrongly filtered because its BODY mentions superseded_by"

    # meta-review MED — anchor with '|' or '-->' would void the comment; reject it


# ── AC2: reclaim_duplicate_entries — exact-dup sweep (run_2816ab1c) ───────────
#
# Sweeps ALREADY-ACCUMULATED exact duplicates (content_signature collision) out of
# a doc — distinct from cultivation's intake-time dedup (which only blocks NEW
# writes, never cleans the backlog). Survivor = highest ref_count, tie → newest
# created_date. Every type is dedup-eligible within one section; COE Registry is
# excluded.
class TestReclaimDuplicateEntries:
    def _meta(self, ref=0, last="2026-01-01", decay="active"):
        return f"  <!-- ref:{ref} | last:{last} | decay:{decay} -->"

    def test_exact_dup_stripped_survivor_kept(self, tmp_path):
        from core.ddd_entry_lifecycle import reclaim_duplicate_entries
        today = date(2026, 8, 8)
        # Two plain guidelines with the SAME content_signature (identical text,
        # different attribution date). Survivor = higher ref.
        content = (
            "## Guidelines\n"
            "- [guideline] **Cache warmup** — preload the index at boot (2026-01-01, run_aaa)\n"
            + self._meta(ref=5) + "\n"
            "- [guideline] **Cache warmup** — preload the index at boot (2026-06-01, run_bbb)\n"
            + self._meta(ref=0) + "\n"
        )
        report = reclaim_duplicate_entries(
            content, today, tmp_path, archive_name="T-archive.md",
            source_path=None
        )
        # exactly ONE non-survivor selected for reclaim (the ref=0 dup)
        assert len(report.candidates) == 1

    def test_keep_class_dup_in_one_section_is_selected(self, tmp_path):
        """Design 2026-10-25-memory-write-entry-dedup §3.2: every type is dedup-eligible
        within one section (a second identical principle carries nothing the first
        lacks); only COE Registry is excluded."""
        from core.ddd_entry_lifecycle import reclaim_duplicate_entries
        today = date(2026, 8, 8)
        content = (
            "## Principles\n"
            "- [principle] **Verify dont infer** — read the source, not memory (2026-01-01)\n"
            + self._meta(ref=0) + "\n"
            "- [principle] **Verify dont infer** — read the source, not memory (2026-06-01)\n"
            + self._meta(ref=0) + "\n"
            "## COE Registry\n"
            "- [pitfall] **Disk full** — 🔍 Investigating. Sessions: 2026-06-01 (2026-06-01)\n"
            + self._meta(ref=0) + "\n"
            "- [pitfall] **Disk full** — 🔍 Investigating. Sessions: 2026-06-01 (2026-06-01)\n"
            + self._meta(ref=0) + "\n"
        )
        report = reclaim_duplicate_entries(
            content, today, tmp_path, archive_name="T-archive.md",
            source_path=None
        )
        assert report.candidates == ["Verify dont infer"]

    def test_no_dup_is_noop(self, tmp_path):
        from core.ddd_entry_lifecycle import reclaim_duplicate_entries
        today = date(2026, 8, 8)
        content = (
            "## Guidelines\n"
            "- [guideline] **Alpha** — first distinct lesson (2026-01-01)\n"
            + self._meta(ref=0) + "\n"
            "- [guideline] **Beta** — second distinct lesson (2026-01-01)\n"
            + self._meta(ref=0) + "\n"
        )
        report = reclaim_duplicate_entries(
            content, today, tmp_path, archive_name="T-archive.md",
            source_path=None
        )
        assert report.candidates == []
