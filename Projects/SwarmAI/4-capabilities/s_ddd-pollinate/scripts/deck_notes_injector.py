#!/usr/bin/env python3
"""
deck_notes_injector.py — OOXML post-processor for Pollinate Track E (Deck).

Injects speaker notes and progressive reveal animations into an unpacked .pptx directory.
Designed to be called after unpack.py, before pack.py.

Usage:
    python deck_notes_injector.py <unpacked_dir> --notes <notes_json> [--animations] [--json]

Arguments:
    unpacked_dir    Path to unpacked .pptx directory (from unpack.py)
    --notes         JSON file with per-slide speaker notes
    --animations    Also inject progressive reveal timing (Tier 1). Omit for Tier 2 (notes only).
    --json          Output results as JSON
    --validate      After injection, check XML well-formedness

Notes JSON format:
    [
        {"slide": 1, "notes": "Full speaker notes text for slide 1..."},
        {"slide": 2, "notes": "Full speaker notes text for slide 2..."},
        ...
    ]

No external dependencies beyond Python stdlib + defusedxml.
"""

import argparse
import json
import sys
from pathlib import Path
from xml.dom import minidom

try:
    import defusedxml.minidom as safe_minidom
except ImportError:
    safe_minidom = minidom  # Fallback if defusedxml not available


# ─── Templates ────────────────────────────────────────────────────────────────

NOTES_SLIDE_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
         xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
         xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr>
        <p:cNvPr id="1" name=""/>
        <p:cNvGrpSpPr/>
        <p:nvPr/>
      </p:nvGrpSpPr>
      <p:grpSpPr/>
      <p:sp>
        <p:nvSpPr>
          <p:cNvPr id="2" name="Notes Placeholder"/>
          <p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>
          <p:nvPr><p:ph type="body" idx="1"/></p:nvPr>
        </p:nvSpPr>
        <p:spPr/>
        <p:txBody>
          <a:bodyPr/>
          <a:lstStyle/>
{PARAGRAPHS}
        </p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
</p:notes>"""

PARAGRAPH_TEMPLATE = """\
          <a:p>
            <a:r>
              <a:rPr lang="zh-CN" dirty="0"/>
              <a:t>{TEXT}</a:t>
            </a:r>
          </a:p>"""

CONTENT_TYPE_ENTRY = '<Override PartName="/ppt/notesSlides/notesSlide{N}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'

RELATIONSHIP_ENTRY = '<Relationship Id="rIdNotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide{N}.xml"/>'


# ─── Core Logic ───────────────────────────────────────────────────────────────

def _escape_xml(text: str) -> str:
    """Escape text for XML content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _generate_notes_xml(notes_text: str) -> str:
    """Generate a notesSlide XML file from speaker notes text."""
    # Split notes into paragraphs (by newline)
    paragraphs = [p.strip() for p in notes_text.split("\n") if p.strip()]
    if not paragraphs:
        paragraphs = [notes_text.strip() or "Speaker notes not provided."]

    para_xml = "\n".join(
        PARAGRAPH_TEMPLATE.replace("{TEXT}", _escape_xml(p))
        for p in paragraphs
    )

    return NOTES_SLIDE_TEMPLATE.replace("{PARAGRAPHS}", para_xml)


def _find_slides(unpacked_dir: Path) -> list[Path]:
    """Find all slide XML files in order."""
    slides_dir = unpacked_dir / "ppt" / "slides"
    if not slides_dir.exists():
        return []
    slides = sorted(
        slides_dir.glob("slide*.xml"),
        key=lambda p: int("".join(filter(str.isdigit, p.stem)) or "0"),
    )
    return slides


def _ensure_notes_dir(unpacked_dir: Path) -> Path:
    """Create ppt/notesSlides/ directory if it doesn't exist."""
    notes_dir = unpacked_dir / "ppt" / "notesSlides"
    notes_dir.mkdir(parents=True, exist_ok=True)
    return notes_dir


def _update_content_types(unpacked_dir: Path, slide_numbers: list[int]) -> None:
    """Add notesSlide entries to [Content_Types].xml."""
    ct_path = unpacked_dir / "[Content_Types].xml"
    if not ct_path.exists():
        return

    content = ct_path.read_text(encoding="utf-8")

    for n in slide_numbers:
        entry = CONTENT_TYPE_ENTRY.replace("{N}", str(n))
        if f"notesSlide{n}.xml" not in content:
            # Insert before closing </Types>
            content = content.replace("</Types>", f"  {entry}\n</Types>")

    ct_path.write_text(content, encoding="utf-8")


def _update_slide_rels(unpacked_dir: Path, slide_number: int) -> None:
    """Add notes relationship to slide{N}.xml.rels."""
    rels_dir = unpacked_dir / "ppt" / "slides" / "_rels"
    rels_dir.mkdir(parents=True, exist_ok=True)

    rels_file = rels_dir / f"slide{slide_number}.xml.rels"

    if rels_file.exists():
        content = rels_file.read_text(encoding="utf-8")
        if "notesSlide" in content:
            return  # Already has notes relationship

        # Find max rId to avoid collision
        import re
        existing_ids = re.findall(r'Id="rId(\d+)"', content)
        max_id = max(int(x) for x in existing_ids) if existing_ids else 0
        new_id = f"rId{max_id + 1}"

        entry = RELATIONSHIP_ENTRY.replace("{N}", str(slide_number)).replace(
            'Id="rIdNotes"', f'Id="{new_id}"'
        )
        content = content.replace("</Relationships>", f"  {entry}\n</Relationships>")
        rels_file.write_text(content, encoding="utf-8")
    else:
        # Create new .rels file
        new_id = "rId1"
        entry = RELATIONSHIP_ENTRY.replace("{N}", str(slide_number)).replace(
            'Id="rIdNotes"', f'Id="{new_id}"'
        )
        content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {entry}
</Relationships>"""
        rels_file.write_text(content, encoding="utf-8")


def inject_notes(unpacked_dir: Path, notes: list[dict]) -> dict:
    """
    Inject speaker notes into unpacked PPTX directory.

    Args:
        unpacked_dir: Path to unpacked .pptx
        notes: List of {"slide": int, "notes": str}

    Returns:
        {"success": bool, "injected": int, "errors": [str]}
    """
    errors = []
    injected = 0
    notes_dir = _ensure_notes_dir(unpacked_dir)
    slides = _find_slides(unpacked_dir)

    if not slides:
        return {"success": False, "injected": 0, "errors": ["No slides found in unpacked directory"]}

    slide_numbers = []

    for note_entry in notes:
        slide_num = note_entry["slide"]
        notes_text = note_entry["notes"]

        # Validate slide exists
        slide_path = unpacked_dir / "ppt" / "slides" / f"slide{slide_num}.xml"
        if not slide_path.exists():
            errors.append(f"Slide {slide_num} not found at {slide_path}")
            continue

        # Generate and write notes XML
        notes_xml = _generate_notes_xml(notes_text)
        notes_path = notes_dir / f"notesSlide{slide_num}.xml"
        notes_path.write_text(notes_xml, encoding="utf-8")

        # Update relationships
        _update_slide_rels(unpacked_dir, slide_num)
        slide_numbers.append(slide_num)
        injected += 1

    # Update Content_Types
    if slide_numbers:
        _update_content_types(unpacked_dir, slide_numbers)

    return {
        "success": injected > 0 and len(errors) == 0,
        "injected": injected,
        "total_slides": len(slides),
        "errors": errors,
    }


def validate_xml(unpacked_dir: Path) -> dict:
    """Check all XML files in unpacked dir are well-formed."""
    errors = []
    checked = 0

    for xml_file in unpacked_dir.rglob("*.xml"):
        checked += 1
        try:
            content = xml_file.read_text(encoding="utf-8")
            safe_minidom.parseString(content)
        except Exception as e:
            errors.append(f"{xml_file.relative_to(unpacked_dir)}: {e}")

    for rels_file in unpacked_dir.rglob("*.rels"):
        checked += 1
        try:
            content = rels_file.read_text(encoding="utf-8")
            safe_minidom.parseString(content)
        except Exception as e:
            errors.append(f"{rels_file.relative_to(unpacked_dir)}: {e}")

    return {"valid": len(errors) == 0, "checked": checked, "errors": errors}


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Inject speaker notes into unpacked PPTX")
    parser.add_argument("unpacked_dir", help="Path to unpacked .pptx directory")
    parser.add_argument("--notes", required=True, help="JSON file with per-slide speaker notes")
    parser.add_argument("--animations", action="store_true", help="Also inject progressive reveal (Tier 1)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--validate", action="store_true", help="Validate XML after injection")

    args = parser.parse_args()

    unpacked_dir = Path(args.unpacked_dir)
    if not unpacked_dir.is_dir():
        print(f"Error: {unpacked_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Load notes
    notes_path = Path(args.notes)
    if not notes_path.exists():
        print(f"Error: Notes file {notes_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(notes_path, "r", encoding="utf-8") as f:
        notes = json.load(f)

    # Inject notes
    result = inject_notes(unpacked_dir, notes)

    # Optionally inject animations
    if args.animations:
        # Animation injection is more complex and depends on shape IDs
        # For now, mark as "attempted" — full implementation reads slide XML to find spids
        result["animations_requested"] = True
        result["animations_note"] = (
            "Animation injection requires reading each slide XML to extract shape IDs (spid) "
            "and paragraph indices. Agent should read slide XML and construct timing blocks "
            "manually for slides with bullet lists. See track-e-deck.md Step 3 for XML template."
        )

    # Validate if requested
    if args.validate:
        validation = validate_xml(unpacked_dir)
        result["validation"] = validation
        if not validation["valid"]:
            result["success"] = False

    # Output
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        status = "✅" if result["success"] else "❌"
        print(f"{status} Injected {result['injected']}/{result['total_slides']} notes")
        if result["errors"]:
            for e in result["errors"]:
                print(f"  ERROR: {e}")
        if args.validate and not result.get("validation", {}).get("valid", True):
            print("  XML validation FAILED:")
            for e in result["validation"]["errors"]:
                print(f"    {e}")

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
