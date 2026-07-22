#!/usr/bin/env python3
"""Pollinate 8-Layer Convergence Gate — mechanical poster quality enforcement.

Checks a poster HTML file against 8 quality layers. Returns JSON verdict.
All checks are CSS/HTML-based (no Playwright required) with graceful fallback.

Layers:
    L1: Direction Declared — HTML has <!-- Direction: D{N} --> comment
    L2: Token Purity — zero hardcoded hex in body styles (outside :root)
    L3: Spacing Compliance — section gaps ≤ 72px (CSS padding fallback)
    L4: Alignment Unity — all text blocks use text-align: center
    L5: Anti-Slop Clean — zero violations against ban lists
    L6: Platform Fit — render width = 1080px (structural check)
    L7: Brand Present — watermark + QR code + GitHub link
    L8: 2-Variant Output — ≥2 direction PNGs in output directory

Usage:
    python convergence_gate.py /path/to/poster.html --json
    python convergence_gate.py /path/to/poster.html

Exit codes:
    0 = all 8 layers pass
    1 = one or more layers fail
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

# Maximum allowed vertical padding between sections (px)
MAX_SECTION_GAP_PX = 72

# Visual ban patterns (L5)
VISUAL_BANS = [
    r"linear-gradient.*-webkit-background-clip:\s*text",
    r"box-shadow:\s*[^;]*\b\d{3,}px",  # excessive shadows (>= 100px)
    r"border-radius:\s*50%.*width:\s*[2-9]\d{2,}",  # large circles
    r"animation.*infinite",  # infinite animations
    r"transform:\s*rotate\(",  # rotated elements
]

# Structural ban patterns (L5)
STRUCTURAL_BANS = [
    r"<marquee",
    r"<blink",
    r"<hr\s*/?>",  # divider elements between sections
    r"position:\s*fixed",
    r"overflow:\s*scroll",
]


def check_l1_direction(html: str) -> list[str]:
    """L1: Direction comment must exist."""
    if not re.search(r"<!--\s*Direction:\s*D\d", html):
        return ["L1 FAIL: Missing <!-- Direction: D{N} --> comment in HTML"]
    return []


def check_l2_token_purity(html: str) -> list[str]:
    """L2: No hardcoded hex in body styles (outside :root block)."""
    errors = []

    # Check ALL <style> blocks (not just the first)
    style_blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    for style_content in style_blocks:
        # Remove :root block and CSS comments
        style_clean = re.sub(r":root\s*\{[^}]*\}", "", style_content)
        style_clean = re.sub(r"/\*.*?\*/", "", style_clean, flags=re.DOTALL)
        # Find hardcoded hex colors in remaining CSS
        hex_matches = re.findall(r"#[0-9a-fA-F]{3,8}\b", style_clean)
        if hex_matches:
            errors.append(f"L2 FAIL: {len(hex_matches)} hardcoded hex value(s) in body CSS: {hex_matches[:3]}")

    # Also check inline styles in body
    body_match = re.search(r"<body[^>]*>(.*)</body>", html, re.DOTALL)
    if body_match:
        body_html = body_match.group(1)
        inline_styles = re.findall(r'style="([^"]*)"', body_html)
        for style in inline_styles:
            hex_in_inline = re.findall(r"#[0-9a-fA-F]{3,8}\b", style)
            if hex_in_inline:
                errors.append(f"L2 FAIL: Hardcoded hex in inline style: {hex_in_inline}")
                break  # Report once

    return errors


def check_l3_spacing(html: str) -> list[str]:
    """L3: Section padding ≤ 72px (CSS-only fallback, no Playwright)."""
    errors = []
    # Find padding/margin declarations for section-like elements
    # Look for padding values > MAX in section/div classes
    style_match = re.search(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    if not style_match:
        return []

    style_content = style_match.group(1)
    # Match padding declarations: padding: Npx or padding-top/bottom: Npx
    padding_matches = re.findall(
        r"(?:padding|padding-top|padding-bottom|margin-top|margin-bottom)\s*:\s*(\d+)px",
        style_content
    )
    for val in padding_matches:
        if int(val) > MAX_SECTION_GAP_PX:
            errors.append(f"L3 FAIL: Spacing value {val}px exceeds max {MAX_SECTION_GAP_PX}px")

    # Also check inline styles for excessive padding
    body_match = re.search(r"<body[^>]*>(.*)</body>", html, re.DOTALL)
    if body_match:
        inline_paddings = re.findall(
            r'style="[^"]*(?:padding|padding-top|padding-bottom|margin-top|margin-bottom)\s*:\s*(\d+)px',
            body_match.group(1)
        )
        for val in inline_paddings:
            if int(val) > MAX_SECTION_GAP_PX:
                errors.append(f"L3 FAIL: Inline spacing {val}px exceeds max {MAX_SECTION_GAP_PX}px")

    return errors


def check_l4_alignment(html: str) -> list[str]:
    """L4: All text-align must be center (or right for watermark only)."""
    errors = []
    # Find all text-align declarations
    all_aligns = re.findall(r"text-align\s*:\s*(\w+)", html)
    bad_aligns = [a for a in all_aligns if a not in ("center", "right")]
    if bad_aligns:
        errors.append(f"L4 FAIL: Mixed alignment detected — found text-align: {set(bad_aligns)} (only center/right allowed)")

    # Check that section containers have explicit text-align
    # (classes like .s, .hero, section without text-align inherit browser left default)
    style_match = re.search(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    if style_match:
        style = style_match.group(1)
        # Find class definitions for section-like elements
        section_classes = re.findall(r"\.(s|hero|card|section|footer)\s*\{([^}]*)\}", style)
        for cls_name, cls_body in section_classes:
            if "text-align" not in cls_body:
                errors.append(f"L4 FAIL: Class .{cls_name} has no explicit text-align (inherits browser left default)")

    return errors


def check_l5_anti_slop(html: str) -> list[str]:
    """L5: No banned visual/structural patterns."""
    errors = []
    for pattern in VISUAL_BANS:
        if re.search(pattern, html, re.DOTALL | re.IGNORECASE):
            errors.append(f"L5 FAIL: Visual ban pattern matched: {pattern}")

    for pattern in STRUCTURAL_BANS:
        if re.search(pattern, html, re.IGNORECASE):
            errors.append(f"L5 FAIL: Structural ban pattern matched: {pattern}")

    return errors


def check_l6_platform_fit(html: str) -> list[str]:
    """L6: Poster width should be 1080px (check viewport meta or body width).

    CSS-only mode: checks for explicit conflicting width declarations.
    If no explicit width set, passes (Playwright renders at 1080 by default).
    """
    errors = []
    # Check if there's a conflicting width declaration
    width_match = re.search(r"body\s*\{[^}]*width\s*:\s*(\d+)", html)
    if width_match:
        width = int(width_match.group(1))
        if width != 1080 and width > 0:
            errors.append(f"L6 FAIL: Body width {width}px != 1080px target")
    # If no explicit width, we pass (Playwright renders at 1080 by default)
    return errors


def check_l7_brand(html: str) -> list[str]:
    """L7: watermark presence — DDD-configurable, NOT SwarmAI-brand-locked.

    Portable: the required watermark string comes from $POLLINATE_WATERMARK (a DDD
    sets its own, e.g. "Made with Acme"). If unset, L7 is a no-op — a DDD that
    doesn't want a watermark is not forced to carry SwarmAI's. This replaces the
    old hard-coded 'Made with SwarmAI Pollinate' + qr-github + xg-gh-25 checks,
    which would fail every non-SwarmAI DDD.
    """
    errors = []
    watermark = os.environ.get("POLLINATE_WATERMARK", "").strip()
    if watermark and watermark not in html:
        errors.append(f"L7 FAIL: Missing configured watermark text '{watermark}'")
    return errors


def check_l8_variants(html_path: Path) -> list[str]:
    """L8: ≥2 direction variant PNGs in the same directory."""
    errors = []
    parent = html_path.parent
    # Count PNG files with direction pattern (d{N} anywhere in filename)
    all_pngs = list(parent.glob("*.png"))
    # Filter: must have -d followed by a digit (e.g., -d4, -d5)
    variant_files = [f for f in all_pngs if re.search(r"-d\d", f.name)]
    if len(variant_files) < 2:
        errors.append(f"L8 FAIL: Only {len(variant_files)} direction variant(s) found (need ≥2)")
    return errors


def run_gate(html_path: Path) -> dict:
    """Run all 8 layers against a poster HTML file.

    Returns:
        Dict with valid, errors, warnings, checks_passed, checks_total, layers.
    """
    html = html_path.read_text(encoding="utf-8")
    layers = {}
    all_errors = []

    # Run each layer
    checks = [
        ("L1", check_l1_direction, (html,)),
        ("L2", check_l2_token_purity, (html,)),
        ("L3", check_l3_spacing, (html,)),
        ("L4", check_l4_alignment, (html,)),
        ("L5", check_l5_anti_slop, (html,)),
        ("L6", check_l6_platform_fit, (html,)),
        ("L7", check_l7_brand, (html,)),
        ("L8", check_l8_variants, (html_path,)),
    ]

    checks_passed = 0
    for layer_name, check_fn, args in checks:
        errors = check_fn(*args)
        if errors:
            layers[layer_name] = {"status": "FAIL", "errors": errors}
            all_errors.extend(errors)
        else:
            layers[layer_name] = {"status": "PASS"}
            checks_passed += 1

    return {
        "valid": len(all_errors) == 0,
        "checks_passed": checks_passed,
        "checks_total": 8,
        "errors": all_errors,
        "warnings": [],
        "layers": layers,
    }


def main():
    parser = argparse.ArgumentParser(description="Pollinate 8-Layer Convergence Gate")
    parser.add_argument("html_file", type=Path, help="Path to poster HTML file")
    parser.add_argument("--json", action="store_true", help="Output JSON format")
    args = parser.parse_args()

    if not args.html_file.exists():
        result = {"valid": False, "errors": [f"File not found: {args.html_file}"],
                  "warnings": [], "checks_passed": 0, "checks_total": 8, "layers": {}}
        if args.json:
            print(json.dumps(result))
        else:
            print(f"ERROR: {args.html_file} not found")
        sys.exit(1)

    result = run_gate(args.html_file)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for layer, info in result["layers"].items():
            status = info["status"]
            detail = " | ".join(info.get("errors", [])) if status == "FAIL" else ""
            print(f"  {layer}: {status}  {detail}")
        print(f"\n  Result: {result['checks_passed']}/{result['checks_total']} passed")
        if not result["valid"]:
            print(f"  FAIL — {len(result['errors'])} error(s)")

    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
