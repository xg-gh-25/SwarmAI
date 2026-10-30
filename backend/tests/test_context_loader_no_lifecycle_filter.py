"""Every MEMORY entry in the file reaches the assembled prompt, whatever lifecycle mark it
carries: what is in a main cognition file is in force, and what is not in force has left
the file (design docs/design/2026-10-30-brain-driven.md §2.3). An old ``decay:dormant``
mark no longer hides an entry.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.context_directory_loader import ContextDirectoryLoader  # noqa: E402
from utils.backend_bin import bundled_path  # noqa: E402

ACTIVE = "ACTIVE_ENTRY_SENTINEL_d41"
OLD_MARK = "OLD_MARK_ENTRY_SENTINEL_d41"


def test_every_memory_entry_is_injected_whatever_its_mark(tmp_path):
    ctx = tmp_path / ".context"
    loader = ContextDirectoryLoader(ctx, templates_dir=bundled_path("context"))
    loader.ensure_directory()
    (ctx / "MEMORY.md").write_text(
        "# Memory\n\n## Guidelines\n"
        f"- [guideline] **{ACTIVE}** still used. (2026-10-01)\n"
        "  <!-- ref:2 | last:2026-10-20 | decay:active | source:manual -->\n"
        f"- [guideline] **{OLD_MARK}** still in the file, so still in force. (2025-01-01)\n"
        "  <!-- ref:0 | last:none | decay:dormant | source:manual -->\n", encoding="utf-8")
    text = loader.load_all(model_context_window=200_000)
    assert ACTIVE in text
    assert OLD_MARK in text


def test_an_entry_with_an_old_mark_is_injected(tmp_path):
    """An old ``decay:dormant`` mark is not a reason to leave a unit out: the whole file
    is injected (design 2026-10-30-cdf-agent-decides D11)."""
    import sqlite3
    from core.memory_injection import select_context_file

    path = tmp_path / ".context" / "MEMORY.md"
    path.parent.mkdir()
    path.write_text(
        "# Memory\n\n## Guidelines\n"
        f"- [guideline] **{OLD_MARK}** short and still in force. (2025-01-01)\n"
        "  <!-- ref:0 | last:none | decay:dormant | source:manual -->\n\n"
        "## Processes\n"
        f"- [process] **long** {'x' * 1200} (2026-10-01)\n"
        "  <!-- ref:2 | last:2026-10-20 | decay:active | source:manual -->\n", encoding="utf-8")
    result = select_context_file(path, estimate=len, conn=sqlite3.connect(":memory:"))
    assert result.omitted == 0 and OLD_MARK in result.text and "x" * 1200 in result.text

