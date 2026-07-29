"""Tests for the shared dedup helper in scripts/locked_write.py.

R3-C (run_55c6ab8f): memory_extractor and distillation_hook both write entries
into MEMORY.md; only distillation had mechanical dedup. This extracts that
dedup into ONE shared place (single-source, no drift) and wires it into the
extractor path via locked_read_modify_write(dedup=True).

Parity requirements (Gate-1 verified vs distillation_hook.py:1360-1399):
- prefix key = line.strip()[:120].lower(), prefix-set is STATIC (built once from existing)
- title key = lowercased bold-title (**...**), title-set is MUTATED intra-batch
- a fully-filtered batch returns "" (caller must guard against empty write)
"""
import pytest
from pathlib import Path


class TestEntryDedupKeys:
    def test_prefix_and_title(self):
        from scripts.locked_write import entry_dedup_keys
        pk, tk = entry_dedup_keys("- 2026-06-28: **CJK boundary** — text here")
        assert pk == "- 2026-06-28: **cjk boundary** — text here"[:120]
        assert tk == "cjk boundary"

    def test_no_title(self):
        from scripts.locked_write import entry_dedup_keys
        pk, tk = entry_dedup_keys("- plain line no bold")
        assert pk == "- plain line no bold"
        assert tk is None

    def test_blank_line(self):
        from scripts.locked_write import entry_dedup_keys
        pk, tk = entry_dedup_keys("   ")
        assert pk is None and tk is None


class TestFilterDuplicateEntries:
    def test_exact_prefix_dup_dropped(self):
        from scripts.locked_write import filter_duplicate_entries
        existing = "## Decisions\n- 2026-06-01: existing decision text\n"
        cand = "- 2026-06-01: existing decision text\n- 2026-06-28: brand new one"
        out = filter_duplicate_entries(existing, cand)
        assert "brand new one" in out
        assert out.count("existing decision text") == 0  # dup filtered

    def test_bold_title_reworded_dup_dropped(self):
        from scripts.locked_write import filter_duplicate_entries
        existing = "- 2026-06-01: **Shared title** — original detail"
        cand = "- 2026-06-28: **Shared title** — totally reworded detail"
        out = filter_duplicate_entries(existing, cand)
        # same bold title → dropped even though detail differs
        assert out.strip() == ""

    def test_intra_batch_title_dedup(self):
        # TWO candidate lines share a bold title not in existing → second dropped
        # Parity note: distillation gates dedup on `if existing:` — existing must
        # be non-empty for the dedup block (incl. intra-batch title mutation) to
        # run at all. Seed an unrelated existing line.
        from scripts.locked_write import filter_duplicate_entries
        existing = "- unrelated existing line\n"
        cand = "- a: **Dup Title** — first\n- b: **Dup Title** — second"
        out = filter_duplicate_entries(existing, cand)
        assert out.count("**Dup Title**") == 1
        assert "first" in out and "second" not in out

    def test_empty_existing_skips_dedup_parity(self):
        # Distillation parity: empty existing → dedup block skipped entirely
        # (`if existing:` gate), so NO intra-batch dedup. Both kept.
        from scripts.locked_write import filter_duplicate_entries
        cand = "- a: **Dup** — first\n- b: **Dup** — second"
        assert filter_duplicate_entries("", cand) == cand

    def test_prefix_set_is_static_not_intrabatch(self):
        # Two candidate lines with SAME 120-char prefix but NO bold title:
        # prefix-set is static (built from existing only), so intra-batch
        # prefix collisions are NOT deduped (matches distillation asymmetry).
        # Needs non-empty existing for the dedup block to run at all.
        from scripts.locked_write import filter_duplicate_entries
        existing = "- unrelated existing line\n"
        line = "- identical plain line with no bold title at all here"
        cand = f"{line}\n{line}"
        out = filter_duplicate_entries(existing, cand)
        assert out.count(line) == 2  # both kept — prefix set not mutated intra-batch

    def test_all_filtered_returns_empty(self):
        from scripts.locked_write import filter_duplicate_entries
        existing = "- 2026-06-01: dup line"
        cand = "- 2026-06-01: dup line"
        out = filter_duplicate_entries(existing, cand)
        assert out.strip() == ""

    def test_empty_existing_keeps_all(self):
        from scripts.locked_write import filter_duplicate_entries
        out = filter_duplicate_entries("", "- a\n- b")
        assert out == "- a\n- b"


class TestLockedWriteDedupParam:
    """AC3: locked_read_modify_write(dedup=True) filters inside the lock;
    dedup=False (default) is byte-identical to today for all callers."""

    def _mem(self, tmp_path: Path) -> Path:
        p = tmp_path / "MEMORY.md"
        p.write_text("## Decisions\n- 2026-06-01: alpha decision\n", encoding="utf-8")
        return p

    def test_dedup_true_filters_existing(self, tmp_path):
        from scripts.locked_write import locked_read_modify_write
        mem = self._mem(tmp_path)
        # candidate has one dup (alpha) + one novel — only novel should land
        locked_read_modify_write(
            mem, "Decisions",
            "- 2026-06-01: alpha decision\n- 2026-06-28: beta decision",
            mode="prepend", dedup=True,
        )
        body = mem.read_text(encoding="utf-8")
        assert body.count("alpha decision") == 1   # dup NOT re-added
        assert "beta decision" in body

    def test_dedup_true_all_dup_is_noop_not_blankline(self, tmp_path):
        # Gate-1 mandatory empty-guard: when dedup filters everything, the write
        # must be a no-op — NOT a bare blank line injected into the section.
        from scripts.locked_write import locked_read_modify_write
        mem = self._mem(tmp_path)
        before = mem.read_text(encoding="utf-8")
        locked_read_modify_write(
            mem, "Decisions", "- 2026-06-01: alpha decision",
            mode="prepend", dedup=True,
        )
        after = mem.read_text(encoding="utf-8")
        assert after == before  # exact no-op, no blank-line corruption

    def test_dedup_false_default_unchanged(self, tmp_path):
        # Regression guard: default dedup=False writes even a duplicate verbatim
        # (byte-identical to today's behavior for the 4 non-opted callers).
        from scripts.locked_write import locked_read_modify_write
        mem = self._mem(tmp_path)
        locked_read_modify_write(
            mem, "Decisions", "- 2026-06-01: alpha decision",
            mode="prepend",
        )
        body = mem.read_text(encoding="utf-8")
        assert body.count("alpha decision") == 2  # dup added — no dedup by default


# run_b356b552: writers that pass reindex_memory=True now prepend a
# <!-- MEMORY_INDEX --> block whose entries echo section titles/text. A
# whole-body substring count therefore double-counts (section body + index). To
# assert DEDUP intent we must count within the ## <section> body only, below the
# index block. This helper scopes to the last "## <section>" occurrence (the real
# section header always follows the index block, which uses "## Memory Index").
def _section_body(content: str, section: str) -> str:
    marker = f"## {section}"
    idx = content.rfind(marker)
    return content[idx:] if idx != -1 else content


class TestExtractorWritePathDedup:
    """AC1: memory_extractor._write_entries dedups via the shared mechanism
    (passes dedup=True), so a re-saved session does not re-append a dup."""

    def test_write_entries_filters_dup(self, tmp_path, monkeypatch):
        from core import memory_extractor
        mem = tmp_path / "MEMORY.md"
        mem.write_text("## Decisions\n- 2026-06-01: alpha decision\n", encoding="utf-8")
        # _write_entries(entries, section, memory_path) — write one dup + one new
        ok = memory_extractor._write_entries(
            ["- 2026-06-01: alpha decision", "- 2026-06-28: gamma decision"],
            "Decisions", mem,
        )
        assert ok is True
        # Count within the Decisions section body only — reindex_memory=True now
        # prepends an index block that also echoes "alpha decision" (run_b356b552).
        section = _section_body(mem.read_text(encoding="utf-8"), "Decisions")
        assert section.count("alpha decision") == 1   # dup filtered by dedup=True
        assert "gamma decision" in section

    def test_write_entries_collapses_multiline_entry(self, tmp_path):
        # Gate-2 LOW: an LLM entry with an embedded newline must NOT be split
        # into independent physical lines (which dedup/_modify_content would
        # treat separately, orphaning half). It is collapsed to one line.
        from core import memory_extractor
        mem = tmp_path / "MEMORY.md"
        mem.write_text("## Decisions\n", encoding="utf-8")
        ok = memory_extractor._write_entries(
            ["- 2026-06-28: header part\n  continued detail part"],
            "Decisions", mem,
        )
        assert ok is True
        # Scope to the Decisions section body — the reindex index block echoes
        # the title too (run_b356b552), so a whole-body scan would see 2 lines.
        section = _section_body(mem.read_text(encoding="utf-8"), "Decisions")
        lines = [ln for ln in section.splitlines() if "header part" in ln]
        assert len(lines) == 1
        assert "continued detail part" in lines[0]

    def test_write_entries_mutation_dedup_off_reappends(self, tmp_path):
        # Mutation proof (non-vacuous): if dedup were OFF, the dup WOULD reappear.
        from scripts.locked_write import locked_read_modify_write
        mem = tmp_path / "MEMORY.md"
        mem.write_text("## Decisions\n- 2026-06-01: alpha decision\n", encoding="utf-8")
        locked_read_modify_write(
            mem, "Decisions", "- 2026-06-01: alpha decision",
            mode="prepend", dedup=False,
        )
        assert mem.read_text(encoding="utf-8").count("alpha decision") == 2


class TestDistillationDedupParity:
    """AC2/AC5: distillation_hook._run_locked_write migrated to the shared
    filter_duplicate_entries must produce byte-identical output to its prior
    inline dedup. These cases capture the exact behaviors the inline code had:
    prefix dup, bold-title reworded dup, intra-batch title dedup, all-dup no-op."""

    def _write(self, tmp_path, existing: str, text: str) -> str:
        from hooks.distillation_hook import DistillationTriggerHook
        mem = tmp_path / "MEMORY.md"
        mem.write_text(existing, encoding="utf-8")
        DistillationTriggerHook._run_locked_write(mem, "Guidelines", text)
        return mem.read_text(encoding="utf-8")

    # A write that adds ≥1 fresh entry now also prepends a reindex block that
    # echoes section text (run_b356b552) — so dedup-intent assertions count within
    # the ## Guidelines section body only. (All-dup / whitespace cases hit the
    # empty-guard `return` BEFORE any write+reindex, so their `out == existing`
    # full-equality still holds — those are left unchanged below.)
    def test_prefix_dup_filtered(self, tmp_path):
        existing = "## Guidelines\n- 2026-06-01: existing guideline line\n"
        out = self._write(tmp_path, existing, "- 2026-06-01: existing guideline line\n- 2026-06-28: novel guideline")
        section = _section_body(out, "Guidelines")
        assert section.count("existing guideline line") == 1
        assert "novel guideline" in section

    def test_bold_title_reworded_dup_filtered(self, tmp_path):
        existing = "## Guidelines\n- 2026-06-01: **Shared Title** — original\n"
        out = self._write(tmp_path, existing, "- 2026-06-28: **Shared Title** — reworded")
        # all-dup → no-op (the empty-guard `return`), existing unchanged (no reindex)
        assert out.count("Shared Title") == 1
        assert "reworded" not in out

    def test_intra_batch_title_dedup(self, tmp_path):
        existing = "## Guidelines\n- seed unrelated line\n"
        out = self._write(tmp_path, existing, "- a: **Dup** — first\n- b: **Dup** — second")
        section = _section_body(out, "Guidelines")
        assert section.count("**Dup**") == 1
        assert "first" in section and "second" not in section

    def test_all_dup_is_noop(self, tmp_path):
        existing = "## Guidelines\n- 2026-06-01: only line\n"
        out = self._write(tmp_path, existing, "- 2026-06-01: only line")
        assert out == existing  # no-op, no blank line

    def test_whitespace_candidate_is_noop_not_blankline(self, tmp_path):
        # Gate-2 LOW (documented divergence): the OLD inline dedup gated on
        # `if not new_lines` (list emptiness) and would WRITE a bare blank line
        # when the candidate was whitespace-only. The shared helper + empty-guard
        # gate on `if not text.strip()` — strictly SAFER: a whitespace-only
        # candidate is a no-op, never injects a blank line. This asserts the
        # improved behavior (not byte-identical to OLD, deliberately).
        existing = "## Guidelines\n- 2026-06-01: real line\n"
        out = self._write(tmp_path, existing, "   \n\n")
        assert out == existing  # no-op — OLD would have injected a blank line


class TestReindexMemoryInLock:
    """run_b356b552: reindex_memory=True rebuilds the MEMORY_INDEX block IN-LOCK
    so every writer's entries are visible in the index (was a bug: distillation
    + memory_extractor wrote but never reindexed). Uses the PURE
    inject_index_into_memory — no _refresh_memory_index (would deadlock on the
    same MEMORY.md.lock)."""

    def _mem_with_index(self, tmp_path):
        # A MEMORY.md that already has an index block + a real section.
        from core.memory_index import inject_index_into_memory
        base = "## Guidelines\n- [GUI01] 2026-06-01: seed line\n"
        mem = tmp_path / "MEMORY.md"
        mem.write_text(inject_index_into_memory(base), encoding="utf-8")
        return mem

    def test_reindex_true_adds_new_entry_to_index(self, tmp_path):
        from scripts.locked_write import locked_read_modify_write
        from core.memory_index import MEMORY_INDEX_START, MEMORY_INDEX_END
        mem = self._mem_with_index(tmp_path)
        locked_read_modify_write(
            mem, "Guidelines", "- [GUI02] 2026-06-28: freshly added rule",
            mode="prepend", reindex_memory=True,
        )
        out = mem.read_text(encoding="utf-8")
        idx = out[out.find(MEMORY_INDEX_START):out.find(MEMORY_INDEX_END)]
        # the new entry's marker is now reflected in the rebuilt index block
        assert "GUI02" in idx, "new entry missing from rebuilt index"

    def test_reindex_false_leaves_index_stale(self, tmp_path):
        # Mutation/contrast (non-vacuous): WITHOUT reindex_memory the index does
        # NOT pick up the new entry — proving the flag is load-bearing.
        from scripts.locked_write import locked_read_modify_write
        from core.memory_index import MEMORY_INDEX_START, MEMORY_INDEX_END
        mem = self._mem_with_index(tmp_path)
        locked_read_modify_write(
            mem, "Guidelines", "- [GUI02] 2026-06-28: freshly added rule",
            mode="prepend", reindex_memory=False,
        )
        out = mem.read_text(encoding="utf-8")
        idx = out[out.find(MEMORY_INDEX_START):out.find(MEMORY_INDEX_END)]
        assert "GUI02" not in idx, "index updated despite reindex_memory=False"

    def test_reindex_noop_for_non_memory_file(self, tmp_path):
        # reindex only applies to MEMORY.md — a KNOWLEDGE.md write is untouched.
        from scripts.locked_write import locked_read_modify_write
        f = tmp_path / "KNOWLEDGE.md"
        f.write_text("## Topic\n- seed\n", encoding="utf-8")
        locked_read_modify_write(
            f, "Topic", "- new fact", mode="prepend", reindex_memory=True,
        )
        out = f.read_text(encoding="utf-8")
        assert "MEMORY_INDEX" not in out  # no index block injected into a non-MEMORY file
        assert "new fact" in out
