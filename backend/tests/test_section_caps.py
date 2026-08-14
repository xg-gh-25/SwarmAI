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

    def test_size_valve_never_evicts_evergreen(self, tmp_path):
        from hooks.distillation_hook import DistillationTriggerHook
        content = self._big_memory(260)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        result = memory_path.read_text()
        # All 5 evergreen principles must survive.
        for i in range(5):
            assert f"[PRI{i:02d}]" in result, f"evergreen principle PRI{i:02d} was size-archived (must never happen)"

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

    def test_size_valve_evergreen_overflow_does_not_stripmine(self, tmp_path):
        """Gate-2 HIGH (D): when evergreen sections ALONE exceed the low-water mark,
        evicting operational can never reach LOW — so the valve must NOT strip-mine
        operational for zero benefit. It logs an EVERGREEN OVERFLOW warning and
        leaves operational intact (human ratcheting needed, not a size trim)."""
        from hooks.distillation_hook import DistillationTriggerHook, SIZE_ARCHIVE_LOW_WATER
        from core.context_directory_loader import ContextDirectoryLoader as C

        # Principles (evergreen) alone > LOW_WATER; plus a few operational entries.
        pad = "evergreen principle detail lorem ipsum dolor sit amet consectetur " * 12
        parts = ["# Memory\n\n## Principles\n"]
        for i in range(400):
            parts.append(f"- [PRI{i:04d}] evergreen principle {i} — {pad}\n")
        parts.append("\n## Guidelines\n")
        for i in range(6):
            parts.append(f"- [GUI{i:04d}] operational guideline {i} — small\n")
            parts.append(f"  <!-- ref:0 | last:none | decay:active | sessions:0 -->\n")
        parts.append("\n## Open Threads\n- none\n")
        content = "".join(parts)
        memory_path = tmp_path / "MEMORY.md"
        memory_path.write_text(content)
        # sanity: evergreen alone exceeds LOW
        assert C.estimate_tokens("## Principles\n" + "".join(
            p for p in parts if p.startswith("- [PRI"))) >= SIZE_ARCHIVE_LOW_WATER

        DistillationTriggerHook._enforce_size_valve(memory_path, tmp_path)

        result = memory_path.read_text()
        # ALL operational guidelines survive — NOT strip-mined.
        for i in range(6):
            assert f"[GUI{i:04d}]" in result, f"operational GUI{i:04d} strip-mined despite evergreen overflow"
        # No archive was produced (nothing evicted).
        from datetime import date
        today = date.today()
        assert not (memory_path.parent / f"MEMORY-archive-{today.strftime('%Y-%m')}.md").exists(), \
            "evergreen-overflow must not archive operational"


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

    def test_p3_judgment_types_immune_in_operational_section(self, tmp_path):
        """P3: a [pitfall]/[decision] entry sitting in an OPERATIONAL section (e.g.
        Guidelines) must NEVER be size-archived — value is type-gated, not position/
        recency-gated. Only [guideline]/[process] are evictable."""
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
            "a [pitfall] in an operational section was size-archived — type-immunity "
            "(P3) failed; value ordering is not guaranteed"
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

    def test_gate2_bold_less_judgment_type_still_immune(self, tmp_path):
        """Gate-2 MED (run_03fc3441): a bold-LESS `- [pitfall] text` entry (no
        `**title**`) must STILL be recognized as a judgment type and be immune —
        _match_entry_line requires a bold title, so the type-tag fallback must catch
        it, else a real pitfall is mistyped as guideline and evicted."""
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
            "a bold-less [pitfall] was size-archived — type-tag fallback failed; "
            "judgment types must be immune regardless of bold-title presence"
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
