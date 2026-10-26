#!/usr/bin/env python3
"""probe_memory — product eval probes for memory.

Contract: scripts/_probe_kit.py. Subcommands:
  size_valve  MEMORY over its size limit moves the lowest-value operational
              entries into an archive and keeps every principle
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
        parts.append(f"- [PRI{i:02d}] evergreen principle {i} — load-bearing judgment kept always\n")
        parts.append("  <!-- ref:3 | last:2026-08-01 | decay:active | sessions:4 -->\n")
    parts.append("\n## Guidelines\n")
    for i in range(n_ops):
        parts.append(f"- [GUI{i:04d}] operational guideline {i} — {_PAD}\n")
        parts.append("  <!-- ref:0 | last:none | decay:active | sessions:0 -->\n")
    parts.append("\n## Open Threads\n- one open thread\n")
    return "".join(parts)


# ── size_valve ────────────────────────────────────────────────────────────────

def _valve_holds(valve) -> tuple[bool, str]:
    from core.context_directory_loader import ContextDirectoryLoader as C
    from core.memory_index import extract_body_without_index
    from hooks.distillation_hook import SIZE_ARCHIVE_HIGH_WATER, SIZE_ARCHIVE_LOW_WATER

    with tempdir() as d:
        mem = d / "MEMORY.md"
        mem.write_text(_big_memory(260), encoding="utf-8")
        before = C.estimate_tokens(extract_body_without_index(mem.read_text()))
        if before <= SIZE_ARCHIVE_HIGH_WATER:
            return False, f"fixture too small ({before} tokens)"
        valve(mem, d)
        body = mem.read_text(encoding="utf-8")
        after = C.estimate_tokens(extract_body_without_index(body))
        if after > SIZE_ARCHIVE_LOW_WATER * 1.05:
            return False, f"MEMORY still {after} tokens after the valve (limit {SIZE_ARCHIVE_LOW_WATER})"
        if any(f"[PRI{i:02d}]" not in body for i in range(5)):
            return False, "a principle was archived while operational entries remained"
        archived = "".join(p.read_text(encoding="utf-8") for p in d.rglob("MEMORY-archive*.md"))
        if "[GUI" not in archived:
            return False, "evicted entries did not reach an archive file"
    return True, ""


def size_valve(negative: bool) -> int:
    from hooks.distillation_hook import DistillationTriggerHook

    return verdict("MEM_SIZE_VALVE", negative,
                   lambda: _valve_holds(DistillationTriggerHook._enforce_size_valve),
                   lambda: _valve_holds(lambda mem, ws: None))  # a valve that does nothing


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
        patch = (mock.patch("core.context_directory_loader._filter_dormant_entries",
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
    raise SystemExit(run({"size_valve": size_valve, "inject": inject, "immune": immune,
                          "reclaim": reclaim}))
