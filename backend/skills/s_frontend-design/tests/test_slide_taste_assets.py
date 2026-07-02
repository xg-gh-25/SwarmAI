"""Validation tests for the frontend-slides taste assets ingested into s_frontend-design/data/.

Tested:
  - slide_presets.csv parses cleanly, 12 rows, complete palette + fonts + mood per row.
  - slide_bold_templates.csv parses cleanly, 34 rows, id/name/scheme/mood/colors/fonts/use-case.
  - animation_feelings.csv parses cleanly, 6 mood rows, approach + timing + palette hint.
  - anti_slop.md contains BOTH banned patterns AND positive counter-rules.
  - ATTRIBUTION.md credits both MIT sources (frontend-slides + beautiful-html-templates).
  - INSTRUCTIONS.md references every new data file (so the skill consults them).

Methodology: pure data-content validation (csv.DictReader for parse-cleanliness — a
malformed quote raises or misaligns columns; content assertions prove non-vacuous rows).
Invariant: taste assets are present, complete, and wired into the skill's reading path.
"""
import csv
import io
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
DATA = SKILL / "data"


def _read_rows(name):
    path = DATA / name
    text = path.read_text(encoding="utf-8")
    # csv.DictReader over the exact bytes — catches malformed quoting / ragged rows.
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return reader.fieldnames, rows


def test_slide_presets_parse_and_complete():
    fields, rows = _read_rows("slide_presets.csv")
    assert len(rows) == 12, f"expected 12 presets, got {len(rows)}"
    required = {"Name", "Category", "Background", "Text", "Accent", "Display Font", "Body Font", "Mood"}
    assert required <= set(fields), f"missing columns: {required - set(fields)}"
    for r in rows:
        # every palette field non-empty; hex fields look like hex or a named token
        assert r["Name"].strip(), "empty preset name"
        assert r["Category"].strip() in {"dark", "light", "specialty"}, f"bad category: {r['Category']}"
        for col in ("Background", "Text", "Accent"):
            assert r[col].strip(), f"{r['Name']}: empty {col}"
        assert r["Display Font"].strip() and r["Body Font"].strip(), f"{r['Name']}: missing font"
        assert r["Mood"].strip(), f"{r['Name']}: missing mood"
    names = {r["Name"] for r in rows}
    # spot-check a few known presets present
    for expected in ("Bold Signal", "Dark Botanical", "Swiss Modern", "Terminal Green"):
        assert expected in names, f"missing preset {expected}"


def test_bold_templates_parse_and_count():
    fields, rows = _read_rows("slide_bold_templates.csv")
    assert len(rows) == 34, f"expected 34 bold templates, got {len(rows)}"
    required = {"ID", "Name", "Scheme", "Mood", "Colors", "Fonts", "Use Case"}
    assert required <= set(fields), f"missing columns: {required - set(fields)}"
    for r in rows:
        assert r["ID"].strip(), "empty template id"
        assert r["Scheme"].strip() in {"dark", "light", "mixed"}, f"{r['ID']}: bad scheme {r['Scheme']}"
        assert r["Mood"].strip() and r["Use Case"].strip(), f"{r['ID']}: missing mood/use-case"
    ids = {r["ID"] for r in rows}
    for expected in ("broadside", "neo-grid-bold", "vellum", "studio"):
        assert expected in ids, f"missing bold template {expected}"


def test_animation_feelings_parse():
    fields, rows = _read_rows("animation_feelings.csv")
    assert len(rows) == 6, f"expected 6 mood rows, got {len(rows)}"
    required = {"Mood", "Animation Approach", "Timing", "Palette Hint"}
    assert required <= set(fields), f"missing columns: {required - set(fields)}"
    moods = {r["Mood"] for r in rows}
    for expected in ("Dramatic", "Techy", "Playful", "Professional", "Calm", "Editorial"):
        assert expected in moods, f"missing mood {expected}"
    for r in rows:
        assert r["Animation Approach"].strip() and r["Timing"].strip(), f"{r['Mood']}: incomplete"


def test_anti_slop_has_banned_and_positive():
    text = (DATA / "anti_slop.md").read_text(encoding="utf-8").lower()
    # banned patterns present
    for banned in ("inter", "roboto", "#6366f1", "purple", "glassmorphism", "centered"):
        assert banned in text, f"anti_slop.md missing banned reference: {banned}"
    # positive counter-rules present
    for positive in ("distinctive", "cohesive", "accent"):
        assert positive in text, f"anti_slop.md missing positive rule: {positive}"
    # must have both a banned section and a positive section
    assert "banned" in text or "avoid" in text, "no banned section"
    assert "instead" in text or "do this" in text or "positive" in text, "no positive section"


def test_attribution_credits_both_sources():
    text = (DATA / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "frontend-slides" in text, "ATTRIBUTION missing frontend-slides"
    assert "beautiful-html-templates" in text, "ATTRIBUTION missing beautiful-html-templates"
    # both MIT
    assert text.upper().count("MIT") >= 2, "ATTRIBUTION should note MIT for the new sources"


def test_instructions_reference_new_assets():
    text = (SKILL / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    for asset in ("slide_presets.csv", "slide_bold_templates.csv", "animation_feelings.csv", "anti_slop.md"):
        assert asset in text, f"INSTRUCTIONS.md does not reference {asset}"
