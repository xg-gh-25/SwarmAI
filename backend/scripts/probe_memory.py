#!/usr/bin/env python3
"""probe_memory — product eval probes for memory.

Contract: scripts/_probe_kit.py. Subcommand:
  inject      every MEMORY entry reaches the prompt intact; no entry is dropped for a
              lifecycle state (an old ``decay:dormant`` mark included)

What is in a main cognition file is in force, and what is not in force has left the file
(design docs/design/2026-10-30-brain-driven.md §2.3): nothing is filtered by a state at
injection, and nothing decays with age. (``immune`` is gone with age decay; ``reclaim``
went earlier: since CDF stage B no automated path moves an entry out of a memory file by
age.)
"""
from __future__ import annotations

from unittest import mock

from scripts._probe_kit import run, tempdir, verdict

# ── inject ────────────────────────────────────────────────────────────────────

_CURRENT = "CURRENT_MEMORY_ENTRY_7f3"
_OLD_MARK = "OLD_MARK_MEMORY_ENTRY_7f3"


def _inject_holds(drop_one: bool) -> tuple[bool, str]:
    from core.context_directory_loader import ContextDirectoryLoader
    from utils.backend_bin import bundled_path

    with tempdir() as d:
        ctx = d / ".context"
        loader = ContextDirectoryLoader(ctx, templates_dir=bundled_path("context"))
        loader.ensure_directory()
        (ctx / "MEMORY.md").write_text(
            "# Memory\n\n## Guidelines\n"
            f"- [guideline] **{_CURRENT}** keep using it. (2026-10-01)\n"
            "  <!-- ref:2 | last:2026-10-20 | decay:active | source:manual -->\n"
            f"- [guideline] **{_OLD_MARK}** still in the file, so still in force. (2025-01-01)\n"
            "  <!-- ref:0 | last:none | decay:dormant | source:manual -->\n", encoding="utf-8")
        import core.memory_injection as mi
        real = mi._prompt_form
        # the teeth: a lifecycle filter put back drops the entry with the old mark
        patch = (mock.patch.object(mi, "_prompt_form", lambda text: "\n".join(
                     line for line in real(text).splitlines() if _OLD_MARK not in line))
                 if drop_one else mock.patch.dict({}))
        with patch:
            text = loader.load_all(model_context_window=200_000)
    for marker in (_CURRENT, _OLD_MARK):
        if marker not in text:
            return False, f"a MEMORY entry in the file is missing from the prompt ({marker})"
    return True, ""


def inject(negative: bool) -> int:
    return verdict("MEM_INJECT", negative, lambda: _inject_holds(False),
                   lambda: _inject_holds(True))


if __name__ == "__main__":
    raise SystemExit(run({"inject": inject}))
