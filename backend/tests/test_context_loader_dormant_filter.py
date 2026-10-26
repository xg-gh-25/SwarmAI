"""A dormant MEMORY entry stays out of the assembled prompt, an active one goes in.

The decay marker is an HTML comment that ``_clean_content`` strips, so the filter
has to run on the raw text; run after cleaning it drops nothing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.context_directory_loader import ContextDirectoryLoader  # noqa: E402
from utils.backend_bin import bundled_path  # noqa: E402

ACTIVE = "ACTIVE_ENTRY_SENTINEL_d41"
DORMANT = "DORMANT_ENTRY_SENTINEL_d41"


def test_dormant_memory_entry_is_not_injected(tmp_path):
    ctx = tmp_path / ".context"
    loader = ContextDirectoryLoader(ctx, templates_dir=bundled_path("context"))
    loader.ensure_directory()
    (ctx / "MEMORY.md").write_text(
        "# Memory\n\n## Guidelines\n"
        f"- [guideline] **{ACTIVE}** still used. (2026-10-01)\n"
        "  <!-- ref:2 | last:2026-10-20 | decay:active | source:manual -->\n"
        f"- [guideline] **{DORMANT}** nobody uses it. (2025-01-01)\n"
        "  <!-- ref:0 | last:none | decay:dormant | source:manual -->\n", encoding="utf-8")
    text = loader.load_all(model_context_window=200_000)
    assert ACTIVE in text
    assert DORMANT not in text
