#!/usr/bin/env python3
"""probe_memory — product eval probes for memory.

Contract: scripts/_probe_kit.py. Subcommands:
  selection   over-budget MEMORY is selected as whole units without rewriting the
              file, and every meta-cognitive unit remains present
  inject      active MEMORY entries reach the prompt intact; dormant ones do not
  immune      judgment entries (decision / correction / pitfall) never decay with
              age; an operational guideline does
  reclaim     a long-unused dormant entry is moved out to the archive file: the
              memory file gets smaller, the entry is in the archive, used entries stay
"""
from __future__ import annotations

from datetime import date
from unittest import mock

from scripts._probe_kit import run, tempdir, verdict

_PAD = "operational guideline detail lorem ipsum dolor sit amet consectetur " * 12


def _big_memory(n_ops: int) -> str:
    parts = ["# Memory\n\n## Principles\n"]
    for i in range(5):
        parts.append(f"- [principle] **PRI{i:02d}** evergreen principle {i} — load-bearing judgment kept always\n")
        parts.append("  <!-- ref:3 | last:2026-08-01 | decay:active | sessions:4 -->\n")
    parts.append("\n## Guidelines\n")
    for i in range(n_ops):
        parts.append(f"- [guideline] **GUI{i:04d}** operational guideline {i} — {_PAD}\n")
        parts.append("  <!-- ref:0 | last:none | decay:active | sessions:0 -->\n")
    parts.append("\n## Open Threads\n- [process] **open thread** one open thread\n")
    return "".join(parts)


# ── deterministic selection ───────────────────────────────────────────────────

def _selection_conn():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE injection_selection(
        id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT NOT NULL,
        file_hash TEXT NOT NULL, selected_anchors TEXT NOT NULL,
        score_snapshot_version TEXT NOT NULL, computed_at TEXT NOT NULL,
        omitted_count INTEGER NOT NULL DEFAULT 0)""")
    return conn


def _selection_holds(selector) -> tuple[bool, str]:
    from core.context_directory_loader import ContextDirectoryLoader as C
    from core.ingestion.units import parse_units
    from core.memory_injection import Selection

    with tempdir() as d:
        mem = d / "MEMORY.md"
        mem.write_text(_big_memory(40), encoding="utf-8")
        before_bytes = mem.read_bytes()
        budget = 1000
        result = selector(mem, budget, C.estimate_tokens, _selection_conn())
        if mem.read_bytes() != before_bytes:
            return False, "selection rewrote MEMORY.md"
        if C.estimate_tokens(result.text) > budget:
            return False, "selected prompt exceeds the 1000-token fixture budget"
        if any(f"PRI{i:02d}" not in result.text for i in range(5)):
            return False, "a meta-cognitive principle was omitted"
        if result.omitted <= 0:
            return False, "over-budget fixture omitted no units"
        for unit in parse_units(mem, root=d):
            prompt_unit = C._HTML_COMMENT_RE.sub("", unit.text).strip()
            prefix = prompt_unit[:40]
            if prefix and prefix in result.text and prompt_unit not in result.text:
                return False, f"unit {unit.title!r} was cut"
    return True, ""


def selection(negative: bool) -> int:
    from core.memory_injection import Selection, select_context_file

    def real(path, budget, estimate, conn):
        return select_context_file(path, budget=budget, estimate=estimate,
                                   now=date(2026, 10, 28), conn=conn)

    def cutting(path, budget, estimate, conn):
        text = path.read_text(encoding="utf-8")[:budget]
        return Selection(text=text, omitted=1, used_tokens=estimate(text))

    return verdict("MEM_INJECTION_SELECTION", negative,
                   lambda: _selection_holds(real), lambda: _selection_holds(cutting))


# ── inject ────────────────────────────────────────────────────────────────────

_ACTIVE = "ACTIVE_MEMORY_ENTRY_7f3"
_DORMANT = "DORMANT_MEMORY_ENTRY_7f3"


def _inject_holds(break_filter: bool) -> tuple[bool, str]:
    from core.context_directory_loader import ContextDirectoryLoader
    from utils.backend_bin import bundled_path

    with tempdir() as d:
        ctx = d / ".context"
        loader = ContextDirectoryLoader(ctx, templates_dir=bundled_path("context"))
        loader.ensure_directory()
        (ctx / "MEMORY.md").write_text(
            "# Memory\n\n## Guidelines\n"
            f"- [guideline] **{_ACTIVE}** keep using it. (2026-10-01)\n"
            "  <!-- ref:2 | last:2026-10-20 | decay:active | source:manual -->\n"
            f"- [guideline] **{_DORMANT}** nobody uses this. (2025-01-01)\n"
            "  <!-- ref:0 | last:none | decay:dormant | source:manual -->\n", encoding="utf-8")
        patch = (mock.patch("core.memory_injection._filter_inactive_memory",
                            lambda text, *a, **k: text)
                 if break_filter else mock.patch.dict({}))
        with patch:
            text = loader.load_all(model_context_window=200_000)
    if _ACTIVE not in text:
        return False, "an active MEMORY entry is missing from the prompt"
    if _DORMANT in text:
        return False, "a dormant MEMORY entry reached the prompt"
    return True, ""


def inject(negative: bool) -> int:
    return verdict("MEM_INJECT", negative, lambda: _inject_holds(False),
                   lambda: _inject_holds(True))


# ── immune ────────────────────────────────────────────────────────────────────

def _immune_holds(protect: bool) -> tuple[bool, str]:
    from core import ddd_entry_lifecycle as lc

    text = "## Lessons\n\n" + "".join(
        f"- [{t}] **old {t} entry** body text. (2025-01-01)\n"
        "  <!-- ref:0 | last:none | decay:active | source:manual -->\n"
        for t in ("decision", "correction", "pitfall", "guideline"))
    entries = lc.parse_entries(text)
    types = lc.EVERGREEN_TYPES if protect else frozenset()
    with mock.patch.object(lc, "EVERGREEN_TYPES", types):
        moved = {t.entry.title for t in lc.assess_decay(entries, date(2026, 10, 26))}
    moved_text = " ".join(moved)
    for t in ("decision", "correction", "pitfall"):
        if f"old {t} entry" in moved_text:
            return False, f"a {t} entry decayed with age"
    if "old guideline entry" not in moved_text:
        return False, "an unused guideline did not decay (decay is not running)"
    return True, ""


def immune(negative: bool) -> int:
    return verdict("MEM_JUDGMENT_IMMUNE", negative, lambda: _immune_holds(True),
                   lambda: _immune_holds(False))


# ── reclaim ───────────────────────────────────────────────────────────────────

def _reclaim_holds(reclaim) -> tuple[bool, str]:
    today = date(2026, 10, 26)
    original = ("## Guidelines\n"
                "- [guideline] **Keep me, still used** recent. (2026-10-24)\n"
                "  <!-- ref:5 | last:2026-10-24 | decay:active -->\n\n"
                "## Pitfalls\n"
                "- [pitfall] **Reclaim me, long unused** stale. (2025-09-01)\n"
                "  <!-- ref:0 | last:none | decay:dormant -->\n")
    with tempdir() as d:
        src = d / "MEMORY.md"
        src.write_text(original, encoding="utf-8")
        reclaim(original, today, d, archive_name="MEMORY-archive.md", source_path=src,
                dry_run=False)
        now = src.read_text(encoding="utf-8")
        archive = d / "MEMORY-archive.md"
        archived = archive.exists() and "Reclaim me" in archive.read_text(encoding="utf-8")
    if "Reclaim me" in now or len(now) >= len(original):
        return False, "the unused entry stayed in the memory file"
    if "Keep me" not in now:
        return False, "a used entry was reclaimed"
    if not archived:
        return False, "the reclaimed entry is not in the archive file"
    return True, ""


def reclaim(negative: bool) -> int:
    from core.ddd_entry_lifecycle import reclaim_noise_entries

    def relabel_only(*a, **k):  # reports what it would move, moves nothing
        return reclaim_noise_entries(*a, **{**k, "dry_run": True})
    return verdict("MEM_RECLAIM", negative, lambda: _reclaim_holds(reclaim_noise_entries),
                   lambda: _reclaim_holds(relabel_only))


if __name__ == "__main__":
    raise SystemExit(run({"selection": selection, "inject": inject, "immune": immune,
                          "reclaim": reclaim}))
