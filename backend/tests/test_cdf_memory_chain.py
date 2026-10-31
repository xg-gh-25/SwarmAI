"""Memory chain fixes (design 2026-10-31-memory-chain-audit A3, E1).

Questions:
- does a unit for MEMORY or EVOLUTION whose type's section is missing get written, with
  the section added by the writer (before the footer), instead of parked for the user?
- does a DDD document with a missing section still wait (its structure is the person's)?
- does the product MEMORY template carry every section CDF writes, and no Open Threads?
- does the review ask for the user's own words on a correction?
"""
from __future__ import annotations

from pathlib import Path

from core.ingestion import writer
from core.ingestion.contracts_v2 import DestinationV2
from core.ingestion.pipeline_v2 import _section_exists
from core.ddd_entry_lifecycle import MEMORY_TYPE_TO_SECTION

BACKEND = Path(__file__).resolve().parents[1]


def test_a_missing_memory_section_is_added_by_the_writer(tmp_path):
    target = tmp_path / ".context/MEMORY.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Memory\n\n## Recent Context\n\n---\n\n_footer_\n", encoding="utf-8")
    dest = DestinationV2(store="MEMORY", relative_path=".context/MEMORY.md", section="Pitfalls")
    assert _section_exists(tmp_path, dest)                       # not parked
    out = writer._compose_intended(target.read_text(encoding="utf-8"), target, "MEMORY", "Pitfalls",
                                   {}, "- [pitfall] **X** y.").decode()
    assert out.index("## Pitfalls") < out.index("- [pitfall] **X**") < out.index("---")
    evo = DestinationV2(store="EVOLUTION", relative_path=".context/MEMORY.md", section="Competence Learned")
    assert _section_exists(tmp_path, evo)


def test_a_missing_ddd_section_still_waits(tmp_path):
    doc = tmp_path / "Projects/P/2-understanding/TECH.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Tech\n\n## Architecture\n", encoding="utf-8")
    dest = DestinationV2(store="DDD", relative_path="Projects/P/2-understanding/TECH.md", section="Runtime Traps")
    assert not _section_exists(tmp_path, dest)
    assert writer._ensure_product_section("# T\n", "DDD", "Runtime Traps") == "# T\n"


def test_the_memory_template_has_every_cdf_section_and_no_open_threads():
    template = (BACKEND / "context/MEMORY.md").read_text(encoding="utf-8")
    for section in MEMORY_TYPE_TO_SECTION.values():
        assert f"\n## {section}\n" in template, section
    assert "Open Threads" not in template


def test_the_review_asks_for_the_users_words_on_a_correction():
    from core.ingestion.sources import s7_review
    prompt = " ".join(s7_review.build_prompt(("cognition",), {"user_messages": 1, "tool_calls": 0,
                                                              "files_written": []}, "(i)", "(r)").split())
    assert "copy the user's words that corrected you, verbatim, into user_quote" in prompt
