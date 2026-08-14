"""Tests for section budget caps with archival.

Validates that MEMORY.md sections are trimmed to their caps,
overflow entries are archived, and under-cap sections are untouched.
"""
from __future__ import annotations

from datetime import date



def _build_memory_md(sections: dict[str, list[str]]) -> str:
    """Build a fake MEMORY.md with given sections and entry lists."""
    parts = ["# Memory\n"]
    for name, entries in sections.items():
        parts.append(f"## {name}\n")
        for entry in entries:
            parts.append(f"- {entry}\n")
        parts.append("\n")
    return "".join(parts)


class TestOrphanIndexStripChokepoint:
    """run_669fa92e — the orphan `<!-- MEMORY_INDEX_START -->` block persisted in live
    MEMORY.md because the 5 distillation_hook direct write_text sites bypassed the
    locked_write strip chokepoint. Fix: a module-level `_write_memory_stripped` helper
    that runs extract_body_without_index before every direct write, routed by all 5
    sites. These tests lock the invariant (P8 single-chokepoint) + idempotence, and are
    mutation-proof (removing the strip from the helper → the strip test goes RED)."""

    def _with_orphan_index(self, body: str) -> str:
        """Prepend a marker-wrapped orphan Memory Index block to a MEMORY body."""
        from core.memory_index import MEMORY_INDEX_START, MEMORY_INDEX_END
        index = (
            f"{MEMORY_INDEX_START}\n## Memory Index\n"
            "1 principle | 260 guidelines\n\n"
            "### Permanent\n- [PRI01] some principle — kw1, kw2\n"
            f"{MEMORY_INDEX_END}\n\n"
        )
        return index + body

    def test_helper_strips_orphan_index_before_write(self, tmp_path):
        """AC2 (direct helper): _write_memory_stripped removes the orphan MEMORY_INDEX
        block before writing. Mutation-proof: delete the strip line in the helper and
        this assertion goes RED (the block would round-trip untouched)."""
        from hooks.distillation_hook import _write_memory_stripped
        from core.memory_index import MEMORY_INDEX_START
        memory_path = tmp_path / "MEMORY.md"
        content = self._with_orphan_index(
            "# Memory\n\n## Guidelines\n- [guideline] **real entry** — kept\n"
        )
        _write_memory_stripped(memory_path, content)
        written = memory_path.read_text()
        assert MEMORY_INDEX_START not in written, (
            "orphan MEMORY_INDEX block survived _write_memory_stripped — the strip "
            "chokepoint is not applied (this is the bug the helper exists to kill)"
        )
        # The real entry must be preserved (strip removes ONLY the index block).
        assert "real entry" in written

    def test_helper_idempotent_on_clean_content(self, tmp_path):
        """AC5: on content with NO index block, the helper is a byte-exact no-op — it
        must not corrupt or reshape ordinary MEMORY content."""
        from hooks.distillation_hook import _write_memory_stripped
        memory_path = tmp_path / "MEMORY.md"
        clean = "# Memory\n\n## Guidelines\n- [guideline] **entry** — no index here\n"
        _write_memory_stripped(memory_path, clean)
        assert memory_path.read_text() == clean, (
            "helper altered clean (index-free) content — strip is not idempotent"
        )

    def test_section_caps_direct_write_strips_orphan_index(self, tmp_path):
        """AC1+AC2 (integration through a real direct-write path): _enforce_section_caps
        is one of the 5 sites. Feeding it a MEMORY that carries an orphan index block +
        an over-cap section, the rewritten file must have the index block gone — proving
        the site now routes through the strip chokepoint, not a bare write_text."""
        from hooks.distillation_hook import DistillationTriggerHook, SECTION_CAPS
        from core.memory_index import MEMORY_INDEX_START
        cap = SECTION_CAPS["Guidelines"]
        body_parts = ["# Memory\n\n## Guidelines\n"]
        for i in range(cap + 5):
            body_parts.append(f"- [guideline] **entry {i}** — detail number {i}\n")
        body_parts.append("\n## Open Threads\n- one\n")
        content = self._with_orphan_index("".join(body_parts))
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        result = memory_path.read_text()
        assert MEMORY_INDEX_START not in result, (
            "_enforce_section_caps wrote back the orphan MEMORY_INDEX block — this "
            "direct-write site still bypasses the strip chokepoint"
        )


class TestSectionCapsDecayEviction:
    """run_3cb6b9ae Cycle-1 (#3): _enforce_section_caps must run DECAY-SCORED eviction
    on the LIVE 4-field metadata (`... | source:X -->`), not silently degrade to
    oldest-first. The bug: it imported memory_decay._META_RE (5-field, reads
    group(5)=sessions) which matches ZERO live 4-field entries → decay_available never
    set → neutral 0.5 → stable-sort → oldest-first. Fix mirrors _enforce_size_valve:2476
    (4-field ddd_entry_lifecycle._META_RE)."""

    def test_caps_eviction_is_decay_aware_not_oldest_first(self, tmp_path):
        """AC1 (negative test): with live 4-field metadata, an OLD low-value entry must
        be evicted BEFORE a RECENT one — proving decay ran. A position-only (oldest-first)
        fallback would evict by FILE ORDER regardless of recency, so we place the OLD
        entry LATER in the file than a RECENT one and assert the OLD one leaves."""
        from hooks.distillation_hook import DistillationTriggerHook, SECTION_CAPS
        cap = SECTION_CAPS["Guidelines"]
        # Build cap+2 Guidelines entries, ALL with 4-field metadata. Make the entries
        # near the TOP recent, and the LAST-2 (bottom) old. If decay runs, the OLD
        # bottom-2 evict. If oldest-first-by-position runs, the bottom-2 ALSO evict —
        # so that alone can't distinguish. The discriminator: put ONE old entry NEAR
        # THE TOP and recent entries at the bottom; decay evicts the top-old one,
        # position-fallback evicts bottom entries. We assert the top-placed OLD entry
        # is gone AND a bottom-placed RECENT entry survives.
        parts = ["# Memory\n\n## Guidelines\n"]
        # entry 0: OLD, placed FIRST (position-fallback would KEEP it; decay EVICTS it)
        parts.append("- [guideline] **OLD_TOP_MARKER evict me** — stale low-value entry\n")
        parts.append("  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n")
        # entries 1..cap: RECENT filler
        for i in range(cap):
            parts.append(f"- [guideline] **recent {i}** — fresh entry number {i}\n")
            parts.append("  <!-- ref:0 | last:2026-08-13 | decay:active | source:manual -->\n")
        # entry cap+1: RECENT, placed LAST (position-fallback EVICTS it; decay KEEPS it)
        parts.append("- [guideline] **RECENT_BOTTOM_MARKER keep me** — fresh valuable entry\n")
        parts.append("  <!-- ref:0 | last:2026-08-14 | decay:active | source:manual -->\n")
        parts.append("\n## Open Threads\n- one\n")
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text("".join(parts))

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        result = memory_path.read_text()
        # Decorrelate position from recency: decay-aware eviction removes the OLD entry
        # (regardless of its TOP position) and keeps the RECENT one (regardless of BOTTOM).
        assert "OLD_TOP_MARKER" not in result, (
            "the OLD entry (placed at TOP) survived — eviction is position-based "
            "(oldest-first), NOT decay-aware; the 5-field regex bug is present"
        )
        assert "RECENT_BOTTOM_MARKER" in result, (
            "the RECENT entry (placed at BOTTOM) was evicted — eviction is position-based, "
            "not decay-aware"
        )


class TestSectionCaps:
    """Test suite for section cap enforcement with archival."""

    def test_enforce_caps_trims_oldest(self, tmp_path):
        """(cap+5) Guidelines entries -> trimmed to cap after enforcement."""
        from hooks.distillation_hook import DistillationTriggerHook, SECTION_CAPS

        cap = SECTION_CAPS["Guidelines"]
        # Build memory with cap+5 Guidelines entries (must exceed cap to trim)
        entries = [f"2026-01-{i+1:02d}: **Entry {i+1}** — detail" for i in range(cap + 5)]
        content = _build_memory_md({"Guidelines": entries})
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        # Create archives dir
        archives_dir = tmp_path / "Knowledge" / "Archives"
        archives_dir.mkdir(parents=True)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        result = memory_path.read_text()
        # Filter to only entries under the Guidelines section
        in_section = False
        count = 0
        for line in result.splitlines():
            if line.strip() == "## Guidelines":
                in_section = True
                continue
            if line.strip().startswith("## ") and in_section:
                break
            if in_section and line.strip().startswith("- ") and not line.strip().startswith("- [Archived]"):
                count += 1
        assert count <= cap

    def test_overflow_archived(self, tmp_path):
        """Trimmed entries appear in archive file."""
        from hooks.distillation_hook import DistillationTriggerHook, SECTION_CAPS

        entries = [f"2026-03-{i+1:02d}: **Entry {i+1}** — detail"
                   for i in range(SECTION_CAPS["Guidelines"] + 5)]
        content = _build_memory_md({"Guidelines": entries})
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        # CYCLE 1': archive lands as a SIBLING of MEMORY.md (private .context/ in
        # prod; here tmp_path), via the archive_raw_lines chokepoint — NEVER the
        # git-tracked Knowledge/Archives/.
        today = date.today()
        archive_name = f"MEMORY-archive-{today.strftime('%Y-%m')}.md"
        archive_path = memory_path.parent / archive_name
        assert archive_path.exists(), f"Archive file {archive_name} should exist"
        assert not (tmp_path / "Knowledge" / "Archives" / archive_name).exists(), \
            "archive must NOT land in git-tracked Knowledge/Archives/"
        archive_content = archive_path.read_text()
        assert "Guidelines" in archive_content

    def test_archive_format(self, tmp_path):
        """Archive file has proper markdown structure."""
        from hooks.distillation_hook import DistillationTriggerHook

        entries = [f"2026-03-{i+1:02d}: **Entry {i+1}** — detail" for i in range(20)]
        content = _build_memory_md({"COE Registry": entries})
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        today = date.today()
        archive_name = f"MEMORY-archive-{today.strftime('%Y-%m')}.md"
        archive_path = memory_path.parent / archive_name  # sibling of MEMORY.md (CYCLE 1')
        assert archive_path.exists()
        archive_content = archive_path.read_text()
        # Should have a section heading
        assert "## COE Registry" in archive_content or "COE Registry" in archive_content
        # Should have date header
        assert today.isoformat() in archive_content

    def test_cap_respected_for_all_sections(self, tmp_path):
        """Each capped section is enforced."""
        from hooks.distillation_hook import DistillationTriggerHook, SECTION_CAPS

        sections = {}
        for name, cap in SECTION_CAPS.items():
            # Create entries exceeding cap by 5
            sections[name] = [
                f"2026-03-{i+1:02d}: **{name} Entry {i+1}** — detail"
                for i in range(cap + 5)
            ]
        content = _build_memory_md(sections)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        archives_dir = tmp_path / "Knowledge" / "Archives"
        archives_dir.mkdir(parents=True)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        result = memory_path.read_text()
        for name, cap in SECTION_CAPS.items():
            in_section = False
            count = 0
            for line in result.splitlines():
                if line.strip() == f"## {name}":
                    in_section = True
                    continue
                if line.strip().startswith("## ") and in_section:
                    break
                if in_section and line.strip().startswith("- ") and not line.strip().startswith("- [Archived]"):
                    count += 1
            assert count <= cap, f"Section '{name}' has {count} entries, cap is {cap}"

    def test_bulk_evict_guard_limits_position_only_eviction(self, tmp_path):
        """Gate-2 F1: when entries lack decay metadata, eviction is position-only,
        so a single cycle must NOT bulk-relocate more than BULK_EVICT_LIMIT."""
        from hooks.distillation_hook import (
            DistillationTriggerHook, SECTION_CAPS, BULK_EVICT_LIMIT,
        )

        cap = SECTION_CAPS["Guidelines"]
        # Far over cap, NO decay metadata (the real-file condition that triggers
        # oldest-first fallback). Overflow = cap + BULK_EVICT_LIMIT + 50.
        n = cap + BULK_EVICT_LIMIT + 50
        entries = [f"2026-01-{(i%28)+1:02d}: **Entry {i}** — detail" for i in range(n)]
        content = _build_memory_md({"Guidelines": entries})
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)
        (tmp_path / "Knowledge" / "Archives").mkdir(parents=True)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        result = memory_path.read_text()
        in_section = False
        remaining = 0
        for line in result.splitlines():
            if line.strip() == "## Guidelines":
                in_section = True
                continue
            if line.strip().startswith("## ") and in_section:
                break
            if in_section and line.strip().startswith("- ") and not line.strip().startswith("- [Archived]"):
                remaining += 1
        # Guard caps eviction at BULK_EVICT_LIMIT, so remaining stays HIGH
        # (n - BULK_EVICT_LIMIT), NOT trimmed all the way down to cap.
        assert remaining == n - BULK_EVICT_LIMIT, (
            f"bulk-evict guard should limit removal to {BULK_EVICT_LIMIT}; "
            f"remaining={remaining}, expected={n - BULK_EVICT_LIMIT}"
        )

    def test_no_modification_under_cap(self, tmp_path):
        """20 entries in a 30-cap section -> no change."""
        from hooks.distillation_hook import DistillationTriggerHook

        entries = [f"2026-03-{i+1:02d}: **Entry {i+1}** — detail" for i in range(20)]
        content = _build_memory_md({"Guidelines": entries})
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        archives_dir = tmp_path / "Knowledge" / "Archives"
        archives_dir.mkdir(parents=True)

        original = memory_path.read_text()
        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)
        assert memory_path.read_text() == original

    def test_multiline_entries_preserved(self, tmp_path):
        """Entries with continuation lines counted as one entry."""
        from hooks.distillation_hook import DistillationTriggerHook, SECTION_CAPS

        # Build entries where some have continuation lines (indented)
        lines = []
        for i in range(20):
            lines.append(f"- 2026-03-{i+1:02d}: **Entry {i+1}** — detail")
            lines.append(f"  Detail: DailyActivity/2026-03-{i+1:02d}.md")

        content = "# Memory\n\n## COE Registry\n" + "\n".join(lines) + "\n\n## Other\n"
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        archives_dir = tmp_path / "Knowledge" / "Archives"
        archives_dir.mkdir(parents=True)

        DistillationTriggerHook._enforce_section_caps(memory_path, tmp_path)

        result = memory_path.read_text()
        # Count top-level entries (starting with "- ") in COE Registry
        in_coe = False
        count = 0
        for line in result.splitlines():
            if line.strip() == "## COE Registry":
                in_coe = True
                continue
            if line.strip().startswith("## ") and in_coe:
                break
            if in_coe and line.strip().startswith("- ") and not line.strip().startswith("- [Archived]"):
                count += 1
        assert count <= SECTION_CAPS["COE Registry"]


class TestSizeValve:
    """Size-driven archive (hysteresis) — the NEW-architecture lever that keeps the
    always-injected live MEMORY.md bounded by TOKEN SIZE, evicting lowest-value
    OPERATIONAL entries to .context archive until the body is under the low-water
    mark. Evergreen sections are never size-archived."""

    def _big_memory(self, n_ops: int) -> str:
        # Each operational entry carries decay metadata so smart (decay-ranked)
        # eviction is exercised (not the position-only fallback). Older last-ref +
        # ref_count 0 => lowest decay score => evicted first.
        parts = ["# Memory\n\n## Principles\n"]
        # Evergreen: a few principle entries that must NEVER be size-archived.
        for i in range(5):
            parts.append(f"- [PRI{i:02d}] evergreen principle {i} — load-bearing judgment kept always\n")
            parts.append(f"  <!-- ref:3 | last:2026-08-01 | decay:active | sessions:4 -->\n")
        parts.append("\n## Guidelines\n")
        pad = "operational guideline detail lorem ipsum dolor sit amet consectetur " * 12
        for i in range(n_ops):
            parts.append(f"- [GUI{i:04d}] operational guideline {i} — {pad}\n")
            # low value: never referenced, old
            parts.append(f"  <!-- ref:0 | last:none | decay:active | sessions:0 -->\n")
        parts.append("\n## Open Threads\n- one open thread\n")
        return "".join(parts)

    def test_size_valve_trims_body_to_low_water(self, tmp_path):
        from hooks.distillation_hook import (
            DistillationTriggerHook, SIZE_ARCHIVE_LOW_WATER, SIZE_ARCHIVE_HIGH_WATER,
        )
        from core.memory_index import extract_body_without_index
        from core.context_directory_loader import ContextDirectoryLoader as C

        # Build a body that exceeds the high-water mark.
        content = self._big_memory(260)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)
        before = C.estimate_tokens(extract_body_without_index(memory_path.read_text()))
        assert before > SIZE_ARCHIVE_HIGH_WATER, f"fixture too small: {before}"

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        after = C.estimate_tokens(extract_body_without_index(memory_path.read_text()))
        # Body must fall to <= low-water (with a small tolerance for whole-entry granularity)
        assert after <= SIZE_ARCHIVE_LOW_WATER * 1.05, f"body not trimmed to low-water: {after}"
        assert after < before

    def test_size_valve_keeps_evergreen_when_operational_suffices(self, tmp_path):
        """NEW CONTRACT (iron law: priority, not floor): evergreen principles are the
        LAST tier evicted. When there is enough lower-tier (guideline) content to reach
        LOW, the evergreen principles are PRESERVED — priority ordering keeps highest
        value in live. This is NOT absolute immunity (see
        test_size_valve_reaches_low_even_when_only_immune_types_remain): if guidelines
        are insufficient, evergreen CAN be archived. Here guidelines suffice, so they go
        first and every principle survives."""
        from hooks.distillation_hook import DistillationTriggerHook
        content = self._big_memory(260)  # 260 fat guidelines >> the ~5K overshoot
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        result = memory_path.read_text()
        # Guidelines are tier-0 (lowest) → evicted first → the 5 tier-2 principles
        # survive because guideline eviction alone reaches LOW.
        for i in range(5):
            assert f"[PRI{i:02d}]" in result, (
                f"evergreen principle PRI{i:02d} was archived while lower-tier "
                "guidelines were still available to evict — priority ordering broken"
            )

    def test_size_valve_archives_evicted_operational(self, tmp_path):
        from hooks.distillation_hook import DistillationTriggerHook
        from datetime import date
        content = self._big_memory(260)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        today = date.today()
        archive_path = memory_path.parent / f"MEMORY-archive-{today.strftime('%Y-%m')}.md"
        assert archive_path.exists(), "size-archived operational entries must land in .context archive"
        arch = archive_path.read_text()
        assert "[GUI" in arch, "evicted operational entries must be in the archive"
        # Must NOT land in git-tracked Knowledge/Archives/
        assert not (tmp_path / "Knowledge" / "Archives" / archive_path.name).exists()

    def test_size_valve_noop_under_high_water(self, tmp_path):
        from hooks.distillation_hook import DistillationTriggerHook
        # Small body, well under high-water → no archiving, file unchanged.
        content = "# Memory\n\n## Guidelines\n- [GUI01] tiny\n\n## Open Threads\n- none\n"
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)
        before = memory_path.read_text()

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        assert memory_path.read_text() == before, "size-valve must be a no-op under high-water"

    def test_size_valve_reaches_low_even_when_only_immune_types_remain(self, tmp_path):
        """AC1 — THE IRON LAW (reversal of the old evergreen-overflow guard): when the
        body is dominated by immune-type content (principles/pitfalls) so that lower
        tiers ALONE cannot reach LOW, the valve MUST STILL archive the immune content
        (in priority order, evergreen last) until body <= LOW. 'Cannot reach LOW' is a
        BUG, not an acceptable early-return. archive != delete (recall covers it).

        This is the exact case the OLD code refused (early-return strip-mine guard);
        the iron law forbids any floor. Restoring that early-return → this test RED."""
        from hooks.distillation_hook import DistillationTriggerHook, SIZE_ARCHIVE_LOW_WATER
        from core.context_directory_loader import ContextDirectoryLoader as C
        from core.memory_index import extract_body_without_index

        # Principles (tier-2) ALONE > LOW_WATER — the old floor case. Use the LIVE
        # TYPE-tag shape (`- [principle]`) so tiering derives principle→tier2 (an
        # ID-tag fixture would mis-tier to the default and a tier-2 floor mutation
        # wouldn't bite — PIT57 fixture-shape discipline).
        pad = "evergreen principle detail lorem ipsum dolor sit amet consectetur " * 12
        parts = ["# Memory\n\n## Principles\n"]
        for i in range(400):
            parts.append(f"- [principle] **evergreen principle PRIMARK{i:04d}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n")
        parts.append("\n## Guidelines\n")
        for i in range(6):
            parts.append(f"- [guideline] **operational guideline GUIMARK{i:04d}** — small\n")
            parts.append("  <!-- ref:0 | last:none | decay:active | source:manual -->\n")
        parts.append("\n## Open Threads\n- one open thread\n")
        content = "".join(parts)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)
        # sanity: principles (tier-2 immune-ish) alone exceed LOW → old code gave up
        assert C.estimate_tokens("## Principles\n" + "".join(
            p for p in parts if "PRIMARK" in p)) >= SIZE_ARCHIVE_LOW_WATER

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        # THE LAW: body reduced to <= LOW regardless of type. No floor.
        after = C.estimate_tokens(extract_body_without_index(memory_path.read_text()))
        assert after <= SIZE_ARCHIVE_LOW_WATER * 1.05, (
            f"body not reduced to LOW ({after} tok) — a type floor still pins it above "
            "target; the iron law requires unconditional reduction to ~25K"
        )
        # And the archive received the evicted (incl. tier-2 principle) content.
        from datetime import date
        today = date.today()
        archive_path = memory_path.parent / f"MEMORY-archive-{today.strftime('%Y-%m')}.md"
        assert archive_path.exists(), "immune-type overflow must be archived, not abandoned"


class TestSizeValveRobustness:
    """run_03fc3441 — the archive subsystem's THREE hard guarantees:
      P0  the decay-sort actually RUNS on the metadata format live MEMORY carries
          (4-field `source:X`, NOT the 5-field `sessions:N` the old fixture used —
          the mismatch made every entry default to 0.5 and the sort degenerate to
          FILE POSITION, defeating "keep the highest value in live");
      P3  VALUE ordering is TYPE-gated — judgment types (pitfall/decision/model)
          are never size-archived even inside an operational section, so only
          guideline/process are evictable and the highest-value tier stays live;
      P1  entry-boundary detection tolerates the legacy no-`- `-prefix form so a
          block move never severs an entry's semantics.
    """

    def _live_format_memory(self, n_guidelines: int) -> str:
        # 4-FIELD metadata (`source:manual`), exactly what live MEMORY.md carries.
        # If P0 regressed (valve demands `sessions:N`), these won't parse → sort
        # degenerates to position and the value-ordering guarantee is silently gone.
        pad = "operational guideline detail lorem ipsum dolor sit amet consectetur " * 12
        parts = ["# Memory\n\n## Principles\n"]
        for i in range(3):
            parts.append(f"- [PRI{i:02d}] **evergreen principle {i}** — load-bearing\n")
            parts.append("  <!-- ref:0 | last:none | decay:active | source:manual -->\n")
        parts.append("\n## Guidelines\n")
        for i in range(n_guidelines):
            # Vary recency so decay sort has a real gradient: even i = OLD (evict
            # first), odd i = RECENT (keep). If the 4-field regex parses, the OLD
            # ones leave first; if it does NOT parse, order is position (i asc).
            last = "2026-01-01" if i % 2 == 0 else "2026-08-13"
            parts.append(f"- [GUI{i:04d}] **guideline {i}** — {pad}\n")
            parts.append(f"  <!-- ref:0 | last:{last} | decay:active | source:manual -->\n")
        parts.append("\n## Open Threads\n- one open thread\n")
        return "".join(parts)

    def test_p0_decay_sort_runs_on_live_4field_format(self, tmp_path):
        """P0: with the live 4-field `source:` metadata, the decay sort must run —
        proven by OLD (even-index) guidelines being evicted before RECENT (odd) ones.
        A position-only degenerate sort would evict low indices regardless of recency."""
        from hooks.distillation_hook import DistillationTriggerHook
        content = self._live_format_memory(260)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        result = memory_path.read_text()
        # Count survivors by recency class. If decay sort truly ran, RECENT (odd)
        # entries survive at a strictly higher rate than OLD (even) ones.
        import re
        survivors = set(re.findall(r"\[GUI(\d{4})\]", result))
        old_alive = sum(1 for s in survivors if int(s) % 2 == 0)
        recent_alive = sum(1 for s in survivors if int(s) % 2 == 1)
        assert recent_alive > old_alive, (
            f"decay sort not running on 4-field format: recent={recent_alive} "
            f"old={old_alive} (position-degenerate sort would not favor recency)"
        )

    def test_p3_judgment_types_kept_when_guidelines_suffice(self, tmp_path):
        """P3 (NEW CONTRACT — priority, not absolute immunity): a [pitfall] entry is
        tier-1; [guideline] is tier-0 (evicted first). When guidelines ALONE suffice to
        reach LOW (260 fat guidelines here vs one pitfall), the pitfall is PRESERVED by
        priority ordering — highest value stays live. This is no longer 'never archived'
        (the iron law forbids floors — see
        test_size_valve_reaches_low_even_when_only_immune_types_remain); it is 'archived
        LAST, so it survives whenever a lower tier can absorb the overshoot'."""
        from hooks.distillation_hook import DistillationTriggerHook
        pad = "filler detail lorem ipsum dolor sit amet consectetur adipiscing " * 12
        parts = ["# Memory\n\n## Guidelines\n"]
        # One high-value judgment entry (pitfall) with the OLDEST possible metadata —
        # a pure decay/position sort would evict it first; type-immunity must save it.
        parts.append(f"- [pitfall] **load-bearing judgment must survive** — {pad}\n")
        parts.append("  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n")
        for i in range(260):
            parts.append(f"- [guideline] **evictable guideline {i}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-08-13 | decay:active | source:manual -->\n")
        parts.append("\n## Open Threads\n- none\n")
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text("".join(parts))

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        result = memory_path.read_text()
        assert "load-bearing judgment must survive" in result, (
            "a [pitfall] (tier-1) was archived while tier-0 guidelines were still "
            "available to evict — priority ordering broken (pitfall must be kept when "
            "guidelines alone can reach LOW)"
        )

    def test_p1_no_dash_prefix_entry_not_severed(self, tmp_path):
        """P1: a legacy no-`- `-prefix entry (`[type] ...` at col 0) must be treated
        as its OWN entry — not swallowed into the previous entry's span on the block
        move. We assert the malformed entry is either kept whole or archived whole,
        never split across the live/archive boundary."""
        from hooks.distillation_hook import DistillationTriggerHook
        pad = "filler detail lorem ipsum dolor sit amet consectetur adipiscing " * 12
        parts = ["# Memory\n\n## Guidelines\n"]
        for i in range(130):
            parts.append(f"- [guideline] **normal {i}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-08-13 | decay:active | source:manual -->\n")
        # A legacy no-dash entry wedged in the middle.
        parts.append("[guideline] LEGACY-NODASH-MARKER a malformed no-dash entry that must not be severed\n")
        for i in range(130, 260):
            parts.append(f"- [guideline] **normal {i}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-08-13 | decay:active | source:manual -->\n")
        parts.append("\n## Open Threads\n- none\n")
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text("".join(parts))

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        from datetime import date
        live = memory_path.read_text()
        archive_path = memory_path.parent / f"MEMORY-archive-{date.today().strftime('%Y-%m')}.md"
        archive = archive_path.read_text() if archive_path.exists() else ""
        marker = "LEGACY-NODASH-MARKER"
        # Exactly one home for the marker — never both (split) nor neither (lost).
        in_live = marker in live
        in_arch = marker in archive
        assert in_live != in_arch, (
            f"no-dash entry severed or lost: in_live={in_live} in_arch={in_arch} "
            "(P1 boundary detection must keep it whole in exactly one place)"
        )

    def test_gate2_bold_less_judgment_type_correctly_tiered(self, tmp_path):
        """Gate-2 MED (run_03fc3441), NEW CONTRACT: a bold-LESS `- [pitfall] text`
        entry (no `**title**`) must STILL be recognized as a [pitfall] (tier-1) via the
        type-tag fallback, NOT mistyped as [guideline] (tier-0). Correct tiering means
        it is preserved when tier-0 guidelines alone suffice to reach LOW (as here) —
        the mistype would wrongly evict it as guideline. This checks TIER CLASSIFICATION
        correctness, not absolute immunity."""
        from hooks.distillation_hook import DistillationTriggerHook
        pad = "filler detail lorem ipsum dolor sit amet consectetur adipiscing " * 12
        parts = ["# Memory\n\n## Guidelines\n"]
        # bold-LESS pitfall (no **title**), oldest metadata → pure sort would evict it
        parts.append(f"- [pitfall] a bold-less judgment entry BOLDLESS-MARKER must survive — {pad}\n")
        parts.append("  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n")
        for i in range(260):
            parts.append(f"- [guideline] **evictable {i}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-08-13 | decay:active | source:manual -->\n")
        parts.append("\n## Open Threads\n- none\n")
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text("".join(parts))

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        assert "BOLDLESS-MARKER" in memory_path.read_text(), (
            "a bold-less [pitfall] was archived while tier-0 guidelines remained — the "
            "type-tag fallback mistyped it as tier-0 guideline (tiering bug); a correctly "
            "tiered pitfall is kept when guidelines alone can reach LOW"
        )

    def test_gate2_wrapped_bracket_clause_not_phantom_entry(self, tmp_path):
        """Gate-2 MED (run_03fc3441): a wrapped body line beginning with a bracket
        that is NOT a valid [type]/[ID] (e.g. `[see also](url) ...`) must NOT be
        treated as a new entry start — else it false-splits, steals the next
        metadata line, and severs the real entry's span on rewrite."""
        from hooks.distillation_hook import DistillationTriggerHook
        pad = "filler detail lorem ipsum dolor sit amet consectetur adipiscing " * 12
        parts = ["# Memory\n\n## Guidelines\n"]
        # A real entry whose body wraps to a col-0 line starting with a NON-type bracket.
        parts.append(f"- [guideline] **entry with a wrapped bracket clause** — {pad}\n")
        parts.append("[see also](http://example.com) this is a CONTINUATION not an entry WRAPMARKER\n")
        parts.append("  <!-- ref:9 | last:2026-08-13 | decay:active | source:manual -->\n")
        for i in range(260):
            parts.append(f"- [guideline] **evictable {i}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-08-13 | decay:active | source:manual -->\n")
        parts.append("\n## Open Threads\n- none\n")
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text("".join(parts))

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        from datetime import date
        live = memory_path.read_text()
        arch_path = memory_path.parent / f"MEMORY-archive-{date.today().strftime('%Y-%m')}.md"
        arch = arch_path.read_text() if arch_path.exists() else ""
        # The `[see also]` continuation must never appear as a standalone archived
        # entry split from its parent. It stays with its parent (live or archived together).
        if "WRAPMARKER" in arch:
            assert "entry with a wrapped bracket clause" in arch, (
                "wrapped `[see also]` clause was split into a phantom entry and "
                "archived apart from its parent (P1 boundary false-positive)"
            )


class TestSizeValveIronLaw:
    """run_f6ab7207 — THE IRON LAW: every archive run unconditionally reduces the live
    body to ~LOW (25K). Gates set PRIORITY (which tier goes first), never a FLOOR (a
    type that can never go). Priority tiers: 0=guideline/process, 1=pitfall/model/
    decision, 2=principle/correction. Worklist sections (COE Registry / Open Threads /
    Standing Preferences — `### `/emoji/footer shapes) are NOT per-entry archived
    (structurally unsafe) but are only ~4K so the ~21K regular tiers always suffice."""

    def _memory_of_type(self, section: str, tag: str, n: int, worklist: bool = False) -> str:
        pad = "detail lorem ipsum dolor sit amet consectetur adipiscing elit " * 12
        parts = [f"# Memory\n\n## {section}\n"]
        for i in range(n):
            parts.append(f"- [{tag}] **{tag} entry {i}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n")
        if worklist:
            parts.append("\n## Open Threads\n### P0 — Blocking\n- 🔴 **a live P0 worklist item WORKLIST-OT** — must never be corrupted\n  Status: open\n")
            parts.append("\n## COE Registry\n- 2026-08-14: **a COE entry WORKLIST-COE** — ✅ Resolved. Sessions: 2026-08-14\n_Post-mortems. Never decays._\n")
        else:
            parts.append("\n## Open Threads\n- none\n")
        return "".join(parts)

    def test_ac3_eviction_priority_guideline_before_pitfall_before_principle(self, tmp_path):
        """AC3: with a mix of tier-0 (guideline), tier-1 (pitfall), tier-2 (principle)
        all oversized, eviction consumes tier-0 first, then tier-1, then tier-2 — the
        higher tiers survive at a strictly higher rate. Priority ordering, not floor."""
        from hooks.distillation_hook import DistillationTriggerHook, SIZE_ARCHIVE_HIGH_WATER
        from core.context_directory_loader import ContextDirectoryLoader as C
        from core.memory_index import extract_body_without_index
        import re
        pad = "detail lorem ipsum dolor sit amet consectetur adipiscing elit " * 12
        # Use the LIVE entry shape — TYPE tags (`- [principle]`), not ID tags — so
        # _entry_type_of derives the real type (PIT57: fixture must match the real
        # corpus shape; an ID-tag fixture would collapse all types to one tier).
        # A unique marker per entry lets us count survivors by type.
        parts = ["# Memory\n\n## Principles\n"]
        for i in range(120):
            parts.append(f"- [principle] **principle PRIMARK{i:04d}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n")
        parts.append("\n## Pitfalls\n")
        for i in range(120):
            parts.append(f"- [pitfall] **pitfall PITMARK{i:04d}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n")
        parts.append("\n## Guidelines\n")
        for i in range(120):
            parts.append(f"- [guideline] **guideline GUIMARK{i:04d}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n")
        parts.append("\n## Open Threads\n- none\n")
        content = "".join(parts)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)
        assert C.estimate_tokens(extract_body_without_index(content)) > SIZE_ARCHIVE_HIGH_WATER

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        result = memory_path.read_text()
        gui = len(set(re.findall(r"GUIMARK\d{4}", result)))
        pit = len(set(re.findall(r"PITMARK\d{4}", result)))
        pri = len(set(re.findall(r"PRIMARK\d{4}", result)))
        # Higher tier survives at a >= rate: guidelines evicted most, principles least.
        assert gui <= pit <= pri, (
            f"priority order broken: survivors GUI={gui} PIT={pit} PRI={pri} "
            "(expected guideline evicted before pitfall before principle)"
        )
        # And nothing is an absolute floor: at least SOME guidelines were evicted.
        assert gui < 120, "no guideline evicted — valve did not run / floor still present"

    def test_ac5_worklist_sections_untouched(self, tmp_path):
        """AC5: the worklist sections (Open Threads with `### Pn` + emoji bullets, COE
        Registry with `- date:` + prose footer) are NEVER per-entry archived — they are
        byte-preserved. The oversized REGULAR sections carry the whole trim."""
        from hooks.distillation_hook import DistillationTriggerHook, SIZE_ARCHIVE_HIGH_WATER
        from core.context_directory_loader import ContextDirectoryLoader as C
        from core.memory_index import extract_body_without_index
        # Oversized Guidelines (regular) + populated worklist sections.
        content = self._memory_of_type("Guidelines", "GUI", 260, worklist=True)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)
        assert C.estimate_tokens(extract_body_without_index(content)) > SIZE_ARCHIVE_HIGH_WATER

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        result = memory_path.read_text()
        # Worklist markers + structure survive intact.
        assert "WORKLIST-OT" in result, "Open Threads entry was archived (worklist must be untouched)"
        assert "WORKLIST-COE" in result, "COE Registry entry was archived (worklist must be untouched)"
        assert "### P0 — Blocking" in result, "Open Threads `### Pn` sub-header corrupted"
        assert "_Post-mortems. Never decays._" in result, "COE prose footer corrupted"
        # No orphaned [Archived] marker injected into a worklist section.
        assert "## Open Threads\n### P0" in result or "### P0 — Blocking" in result

    def test_gate2_fully_drained_section_not_marker_only_corrupt(self, tmp_path):
        """Gate-2 MED (run_f6ab7207): if a whole regular section is evicted, it must NOT
        be left as `## Section\\n- [Archived] ...` (a marker is not an entry → malformed).
        The [Archived] pointer is appended ONLY when real entries remain; a fully-drained
        section is left entry-free and still parses."""
        from hooks.distillation_hook import DistillationTriggerHook, SIZE_ARCHIVE_HIGH_WATER
        from core.context_directory_loader import ContextDirectoryLoader as C
        from core.memory_index import extract_body_without_index, parse_memory_sections
        pad = "detail lorem ipsum dolor sit amet consectetur adipiscing elit " * 12
        # A big evictable Guidelines section + a small Pitfalls that survives.
        parts = ["# Memory\n\n## Guidelines\n"]
        for i in range(300):
            parts.append(f"- [guideline] **guideline {i}** — {pad}\n")
            parts.append("  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n")
        parts.append("\n## Pitfalls\n- [pitfall] **survivor pitfall** — small\n")
        parts.append("  <!-- ref:0 | last:2026-08-13 | decay:active | source:manual -->\n")
        parts.append("\n## Open Threads\n- none\n")
        content = "".join(parts)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)
        assert C.estimate_tokens(extract_body_without_index(content)) > SIZE_ARCHIVE_HIGH_WATER

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        result = memory_path.read_text()
        # Still parses into sections (no structural corruption).
        secs = parse_memory_sections(result)
        assert "Guidelines" in secs
        # A drained Guidelines section must not be a bare header + marker-only line.
        gl = secs["Guidelines"].strip()
        if gl:  # if anything survived it must be a real entry or the archived pointer beside real entries
            has_real = any(ln.strip().startswith("- [") and "[Archived]" not in ln
                           for ln in gl.splitlines())
            only_marker = gl.count("\n") == 0 and "[Archived]" in gl
            assert not only_marker, (
                f"Guidelines drained to a marker-only section (corrupt): {gl!r}"
            )

    def test_ironlaw_backstop_fires_loud_not_silent_on_worklist_residue(self, tmp_path, caplog):
        """Gate-2 CRITICAL (run_f6ab7207): if regular sections are exhausted and the body
        is STILL > LOW because a worklist section is oversized, the valve must NOT return
        silently — it emits logger.critical. (Structurally this needs an uncapped worklist;
        we simulate by making Open Threads huge while regular content is tiny.)"""
        import logging
        from hooks.distillation_hook import DistillationTriggerHook, SIZE_ARCHIVE_HIGH_WATER, SIZE_ARCHIVE_LOW_WATER
        from core.context_directory_loader import ContextDirectoryLoader as C
        from core.memory_index import extract_body_without_index
        pad = "worklist bloat detail lorem ipsum dolor sit amet consectetur adipiscing " * 12
        parts = ["# Memory\n\n## Guidelines\n- [guideline] **tiny** — small\n"]
        parts.append("  <!-- ref:0 | last:2026-01-01 | decay:active | source:manual -->\n")
        # Oversized Open Threads (worklist — never per-entry archived) pushes body > LOW.
        parts.append("\n## Open Threads\n### P0 — Blocking\n")
        for i in range(500):
            parts.append(f"- 🔴 **huge worklist item {i}** — {pad}\n")
        content = "".join(parts)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)
        body = C.estimate_tokens(extract_body_without_index(content))
        assert body > SIZE_ARCHIVE_HIGH_WATER, f"fixture too small: {body}"

        with caplog.at_level(logging.CRITICAL):
            DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        # Body genuinely cannot reach LOW (worklist residue) → must be LOUD, not silent.
        after = C.estimate_tokens(extract_body_without_index(memory_path.read_text()))
        if after > SIZE_ARCHIVE_LOW_WATER * 1.05:
            assert any("IRON-LAW VIOLATION" in r.message for r in caplog.records), (
                "body left above LOW with NO critical log — silent iron-law failure"
            )
