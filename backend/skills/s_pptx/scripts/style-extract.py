#!/usr/bin/env python3
"""
Extract style summary from an existing PowerPoint presentation.

Analyzes slides to produce a style guide including:
- Color palette (background, text, accent colors)
- Font stack (typefaces, sizes, weights)
- Layout patterns (header bars, footers, margins)
- Image/background references per slide

Usage:
    python style-extract.py presentation.pptx [output.json]
    python style-extract.py presentation.pptx --slide 3  # analyze single slide
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import lxml.etree as ET
except ImportError:
    import xml.etree.ElementTree as ET

import zipfile
import tempfile
import shutil


NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def extract_colors(root: Any) -> list[str]:
    """Extract all srgbClr values from a slide XML."""
    colors = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "srgbClr":
            val = elem.get("val")
            if val:
                colors.append(val.upper())
    return colors


def extract_fonts(root: Any) -> list[dict]:
    """Extract font usage from run properties."""
    fonts = []
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag == "rPr":
            entry: dict[str, Any] = {}
            sz = elem.get("sz")
            if sz:
                entry["size_pt"] = int(sz) / 100
            if elem.get("b") == "1":
                entry["bold"] = True
            for child in elem:
                ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if ctag == "latin" or ctag == "ea" or ctag == "cs":
                    tf = child.get("typeface")
                    if tf and not tf.startswith("+"):
                        entry["typeface"] = tf
                if ctag == "solidFill":
                    for fc in child:
                        fctag = fc.tag.split("}")[-1] if "}" in fc.tag else fc.tag
                        if fctag == "srgbClr":
                            entry["color"] = fc.get("val", "").upper()
            if entry:
                fonts.append(entry)
    return fonts


def extract_shapes_summary(root: Any) -> list[dict]:
    """Extract shape positions, sizes, and fills for layout analysis."""
    shapes = []
    for sp in root.iter():
        tag = sp.tag.split("}")[-1] if "}" in sp.tag else sp.tag
        if tag not in ("sp", "pic"):
            continue
        shape: dict[str, Any] = {"type": tag}

        # Get name
        for child in sp.iter():
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag == "cNvPr":
                shape["name"] = child.get("name", "")
                break

        # Get position/size from xfrm
        for xfrm in sp.iter():
            xtag = xfrm.tag.split("}")[-1] if "}" in xfrm.tag else xfrm.tag
            if xtag == "xfrm":
                for c in xfrm:
                    ct = c.tag.split("}")[-1] if "}" in c.tag else c.tag
                    if ct == "off":
                        shape["x"] = int(c.get("x", 0))
                        shape["y"] = int(c.get("y", 0))
                    elif ct == "ext":
                        shape["cx"] = int(c.get("cx", 0))
                        shape["cy"] = int(c.get("cy", 0))
                break

        # Get fill color
        for child in sp.iter():
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag == "solidFill":
                for fc in child:
                    fctag = fc.tag.split("}")[-1] if "}" in fc.tag else fc.tag
                    if fctag == "srgbClr":
                        shape["fill"] = fc.get("val", "").upper()
                        alpha = None
                        for ac in fc:
                            act = ac.tag.split("}")[-1] if "}" in ac.tag else ac.tag
                            if act == "alpha":
                                alpha = ac.get("val")
                        if alpha:
                            shape["fill_alpha"] = int(alpha) / 1000
                break

        # Check if it's a picture (has blip)
        if tag == "pic":
            for blip in sp.iter():
                btag = blip.tag.split("}")[-1] if "}" in blip.tag else blip.tag
                if btag == "blip":
                    embed = None
                    for attr, val in blip.attrib.items():
                        if attr.split("}")[-1] == "embed":
                            embed = val
                    if embed:
                        shape["image_rId"] = embed
                    break

        if "x" in shape:
            shapes.append(shape)
    return shapes


def extract_image_refs(rels_path: Path) -> dict[str, str]:
    """Extract rId -> image file mapping from a .rels file."""
    refs = {}
    if not rels_path.exists():
        return refs
    tree = ET.parse(str(rels_path))
    root = tree.getroot()
    for rel in root:
        tag = rel.tag.split("}")[-1] if "}" in rel.tag else rel.tag
        if tag == "Relationship":
            rtype = rel.get("Type", "")
            if "image" in rtype:
                refs[rel.get("Id", "")] = rel.get("Target", "")
    return refs


def get_slide_layout_ref(rels_path: Path) -> str:
    """Get the slideLayout reference from a slide's .rels file."""
    if not rels_path.exists():
        return ""
    tree = ET.parse(str(rels_path))
    root = tree.getroot()
    for rel in root:
        rtype = rel.get("Type", "")
        if "slideLayout" in rtype:
            return rel.get("Target", "")
    return ""



def analyze_slide(slide_xml: Path, rels_path: Path) -> dict:
    """Analyze a single slide and return its style data."""
    tree = ET.parse(str(slide_xml))
    root = tree.getroot()

    colors = extract_colors(root)
    fonts = extract_fonts(root)
    shapes = extract_shapes_summary(root)
    image_refs = extract_image_refs(rels_path)
    layout_ref = get_slide_layout_ref(rels_path)

    # Resolve image rIds in shapes
    for s in shapes:
        if "image_rId" in s and s["image_rId"] in image_refs:
            s["image_file"] = image_refs[s["image_rId"]]

    return {
        "colors": colors,
        "fonts": fonts,
        "shapes": shapes,
        "layout_ref": layout_ref,
        "image_refs": image_refs,
    }


def aggregate_style(slides_data: list[dict]) -> dict:
    """Aggregate style data across all slides into a summary."""
    all_colors = Counter()
    all_fonts = Counter()
    all_sizes = Counter()
    bg_images = []
    header_bars = []
    footers = []

    for i, sd in enumerate(slides_data):
        all_colors.update(sd["colors"])

        for f in sd["fonts"]:
            if "typeface" in f:
                all_fonts[f["typeface"]] += 1
            if "size_pt" in f:
                all_sizes[f["size_pt"]] += 1

        for s in sd["shapes"]:
            name = s.get("name", "").lower()
            # Detect background images (full-slide sized pictures)
            if s.get("type") == "pic" and s.get("cx", 0) > 8000000 and s.get("cy", 0) > 4000000:
                bg_images.append({
                    "slide": i + 1,
                    "image": s.get("image_file", s.get("image_rId", "unknown")),
                })
            # Detect header bars (wide rectangles at top with fill)
            if (s.get("type") == "sp" and s.get("y", 999) < 100000
                    and s.get("cx", 0) > 8000000 and "fill" in s):
                header_bars.append({
                    "slide": i + 1,
                    "fill": f"#{s['fill']}",
                    "alpha": s.get("fill_alpha"),
                    "height_emu": s.get("cy", 0),
                })
            # Detect footer text (small text near bottom)
            if s.get("y", 0) > 4500000 and s.get("cy", 0) < 400000:
                footers.append({"slide": i + 1, "name": s.get("name", "")})

    # Build color palette (top colors, excluding very common white/black)
    palette = [
        {"color": f"#{c}", "count": n}
        for c, n in all_colors.most_common(15)
    ]

    font_stack = [
        {"typeface": f, "count": n}
        for f, n in all_fonts.most_common(10)
    ]

    size_distribution = [
        {"size_pt": s, "count": n}
        for s, n in sorted(all_sizes.items())
    ]

    return {
        "color_palette": palette,
        "font_stack": font_stack,
        "font_sizes": size_distribution,
        "background_images": bg_images,
        "header_bars": header_bars,
        "footer_elements": footers,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract style summary from a PPTX")
    parser.add_argument("pptx_file", help="Path to .pptx file")
    parser.add_argument("output", nargs="?", default=None, help="Output JSON file (default: stdout)")
    parser.add_argument("--slide", type=int, default=None, help="Analyze a single slide (1-based)")
    args = parser.parse_args()

    pptx_path = Path(args.pptx_file)
    if not pptx_path.exists():
        print(f"Error: {pptx_path} not found", file=sys.stderr)
        sys.exit(1)

    # Unpack to temp dir
    tmp = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(pptx_path, "r") as zf:
            zf.extractall(tmp)

        tmp_path = Path(tmp)
        slides_dir = tmp_path / "ppt" / "slides"
        rels_dir = slides_dir / "_rels"

        # Find all slide files
        slide_files = sorted(slides_dir.glob("slide*.xml"), key=lambda p: int("".join(filter(str.isdigit, p.stem)) or "0"))

        if args.slide:
            slide_files = [f for f in slide_files if f.name == f"slide{args.slide}.xml"]
            if not slide_files:
                print(f"Error: slide{args.slide}.xml not found", file=sys.stderr)
                sys.exit(1)

        slides_data = []
        for sf in slide_files:
            rels_path = rels_dir / f"{sf.name}.rels"
            slides_data.append(analyze_slide(sf, rels_path))

        if args.slide:
            result = {
                "slide": args.slide,
                "detail": slides_data[0],
                "summary": aggregate_style(slides_data),
            }
        else:
            result = {
                "total_slides": len(slides_data),
                "summary": aggregate_style(slides_data),
                "per_slide": {
                    f"slide-{i+1}": {
                        "layout_ref": sd["layout_ref"],
                        "color_count": len(set(sd["colors"])),
                        "font_count": len(sd["fonts"]),
                        "shape_count": len(sd["shapes"]),
                        "images": list(sd["image_refs"].values()),
                    }
                    for i, sd in enumerate(slides_data)
                },
            }

        output = json.dumps(result, indent=2)
        if args.output:
            Path(args.output).write_text(output)
            print(f"Style summary saved to {args.output}")
        else:
            print(output)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
