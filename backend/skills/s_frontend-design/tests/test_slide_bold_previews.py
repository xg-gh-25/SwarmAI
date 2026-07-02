"""Validation for the 34 bold-template preview.md cards ingested into s_frontend-design.

Tested:
  - All 34 canonical ids (from slide_bold_templates.csv) have a matching
    data/slide_bold_previews/<id>.md file.
  - Each preview file is non-empty and is a REAL card (contains the
    "Selection Metadata" + "Preview Ingredients" markers) — NOT a 404 page.
  - Every preview id maps 1:1 to a bold-templates CSV id (no orphans either way).
  - INSTRUCTIONS.md references the slide_bold_previews/ directory.

Methodology: filesystem + content-marker validation. A silently-stored GitHub
404 page would have neither marker → caught. Cross-checks the preview set against
the canonical CSV id list so the two selection-layer assets stay in sync.
"""
import csv
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
DATA = SKILL / "data"
PREVIEWS = DATA / "slide_bold_previews"


def _canonical_ids():
    text = (DATA / "slide_bold_templates.csv").read_text(encoding="utf-8")
    reader = csv.DictReader(text.splitlines())
    return [r["ID"].strip() for r in reader]


def test_all_34_previews_present():
    ids = _canonical_ids()
    assert len(ids) == 34, f"expected 34 canonical ids, got {len(ids)}"
    missing = [i for i in ids if not (PREVIEWS / f"{i}.md").exists()]
    assert not missing, f"missing preview cards: {missing}"


def test_each_preview_is_real_card():
    ids = _canonical_ids()
    for i in ids:
        p = PREVIEWS / f"{i}.md"
        text = p.read_text(encoding="utf-8")
        assert len(text) > 500, f"{i}.md suspiciously small ({len(text)} bytes) — possible 404"
        assert "Selection Metadata" in text, f"{i}.md missing 'Selection Metadata' — not a real card"
        assert "Preview Ingredients" in text, f"{i}.md missing 'Preview Ingredients' — not a real card"
        # 404 pages from GitHub contain "404" in an HTML title — real cards don't lead with it
        assert not text.lstrip().startswith("<"), f"{i}.md starts with HTML — likely an error page"


def test_previews_map_one_to_one_with_csv():
    ids = set(_canonical_ids())
    on_disk = {p.stem for p in PREVIEWS.glob("*.md")}
    orphan_files = on_disk - ids
    missing_files = ids - on_disk
    assert not orphan_files, f"preview files with no CSV id: {orphan_files}"
    assert not missing_files, f"CSV ids with no preview file: {missing_files}"


def test_instructions_reference_previews_dir():
    text = (SKILL / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "slide_bold_previews" in text, "INSTRUCTIONS.md does not reference slide_bold_previews/"
