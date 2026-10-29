#!/usr/bin/env python3
"""probe_memory — product eval probes for memory.

Contract: scripts/_probe_kit.py. Subcommands:
  inject      active MEMORY entries reach the prompt intact; dormant ones do not
  immune      judgment entries (decision / correction / pitfall) never decay with
              age; an operational guideline does

(``reclaim`` is gone: since CDF stage B no automated path moves an entry out of a
memory file — the file keeps every unit and the injection budget decides what reaches
the prompt, which ``inject`` checks; design 2026-10-26-swarm-cognitive-distillation-flow
§8, §12.)
"""
from __future__ import annotations

from datetime import date
from unittest import mock

from scripts._probe_kit import run, tempdir, verdict

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


if __name__ == "__main__":
    raise SystemExit(run({"inject": inject, "immune": immune}))
