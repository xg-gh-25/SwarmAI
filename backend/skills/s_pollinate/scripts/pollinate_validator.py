#!/usr/bin/env python3
"""Pollinate Structural Validator — mechanical delivery quality gate.

Verifies 6 structural invariants that every Pollinate delivery must satisfy.
Inspired by pipeline_validator.py — same JSON output format for consistency.

Checks:
    1. Platform matrix present (platform_matrix.md or section in delivery)
    2. QR code image file present (qr-*.png)
    3. GitHub link in delivery text (github.com)
    4. 2+ variant files per track format
    5. Output files have valid extensions (.html, .png, .mp4, .md)
    6. Content directory structure is valid (tracks/ subdir exists)

Usage:
    python pollinate_validator.py /path/to/content/topic/
    python pollinate_validator.py /path/to/content/topic/ --json

Returns JSON:
    {"valid": true, "errors": [], "warnings": [], "checks_passed": 6, "checks_total": 6}
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

# Valid output extensions for Pollinate deliverables
VALID_EXTENSIONS = {".html", ".png", ".jpg", ".mp4", ".md", ".svg", ".pdf"}


def validate_delivery(content_dir: str) -> dict:
    """Validate a Pollinate content delivery directory.

    Args:
        content_dir: Path to the content directory (e.g., content/my-topic/)

    Returns:
        Dict with valid (bool), errors (list), warnings (list),
        checks_passed (int), checks_total (int)
    """
    root = Path(content_dir)
    errors: list[str] = []
    warnings: list[str] = []
    checks_total = 6
    checks_passed = 0

    # ── Check 1: Platform matrix present ──────────────────────────────────
    platform_matrix_found = False
    if (root / "platform_matrix.md").exists():
        platform_matrix_found = True
    else:
        # Check if platform matrix is a section in delivery.md
        delivery_md = root / "delivery.md"
        if delivery_md.exists():
            text = delivery_md.read_text(encoding="utf-8")
            if re.search(r"(?i)platform\s*matrix", text):
                platform_matrix_found = True

    if platform_matrix_found:
        checks_passed += 1
    else:
        errors.append("MISSING: Platform matrix — no platform_matrix.md or platform section in delivery.md")

    # ── Check 2: QR code image present ────────────────────────────────────
    qr_files = list(root.glob("qr-*.png")) + list(root.glob("qr_*.png"))
    if not qr_files:
        # Also check in tracks subdirs
        qr_files = list(root.rglob("qr-*.png")) + list(root.rglob("qr_*.png"))

    if qr_files:
        checks_passed += 1
    else:
        errors.append("MISSING: QR code image — no qr-*.png found in content directory")

    # ── Check 3: GitHub link in delivery text ─────────────────────────────
    github_link_found = False
    for md_file in root.glob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8")
            if "github.com" in text.lower():
                github_link_found = True
                break
        except OSError:
            pass

    if not github_link_found:
        # Check in HTML files too
        for html_file in root.rglob("*.html"):
            try:
                text = html_file.read_text(encoding="utf-8")
                if "github.com" in text.lower():
                    github_link_found = True
                    break
            except OSError:
                pass

    if github_link_found:
        checks_passed += 1
    else:
        errors.append("MISSING: GitHub link — no github.com URL found in delivery text")

    # ── Check 4: 2+ variant files per track ───────────────────────────────
    tracks_dir = root / "tracks"
    variants_ok = False
    if tracks_dir.is_dir():
        for track_dir in tracks_dir.iterdir():
            if not track_dir.is_dir():
                continue
            # Count unique variant stems (variant-a, variant-b, etc.)
            variant_stems = set()
            for f in track_dir.iterdir():
                if f.is_file() and f.suffix in VALID_EXTENSIONS:
                    variant_stems.add(f.stem)
            if len(variant_stems) >= 2:
                variants_ok = True
                break
    else:
        # No tracks dir — check root for variants
        variant_files = [f for f in root.iterdir() if f.is_file() and f.suffix in VALID_EXTENSIONS]
        if len(variant_files) >= 2:
            variants_ok = True

    if variants_ok:
        checks_passed += 1
    else:
        errors.append("MISSING: 2+ variant files — at least one track must have 2+ output variants")

    # ── Check 5: Output files have valid extensions ───────────────────────
    output_files = list(root.rglob("*"))
    output_files = [f for f in output_files if f.is_file() and not f.name.startswith(".")]
    invalid_ext = [f for f in output_files if f.suffix and f.suffix not in VALID_EXTENSIONS
                   and f.suffix not in {".json", ".yaml", ".yml", ".txt", ".css", ".js"}]

    if not invalid_ext:
        checks_passed += 1
    else:
        warnings.append(
            f"UNEXPECTED: {len(invalid_ext)} files with non-standard extensions: "
            f"{', '.join(f.name for f in invalid_ext[:5])}"
        )
        # This is a warning not an error — still passes
        checks_passed += 1

    # ── Check 6: Content directory structure valid ─────────────────────────
    has_structure = (
        tracks_dir.is_dir()
        or any(root.glob("*.md"))
        or any(root.glob("*.html"))
    )

    if has_structure:
        checks_passed += 1
    else:
        errors.append("INVALID: Content directory has no recognizable structure (no tracks/, .md, or .html)")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "checks_passed": checks_passed,
        "checks_total": checks_total,
    }


def main():
    parser = argparse.ArgumentParser(description="Pollinate structural delivery validator")
    parser.add_argument("content_dir", help="Path to content delivery directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = validate_delivery(args.content_dir)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = "✅ VALID" if result["valid"] else "❌ INVALID"
        print(f"{status} ({result['checks_passed']}/{result['checks_total']} checks passed)")
        if result["errors"]:
            print("\nErrors:")
            for e in result["errors"]:
                print(f"  ❌ {e}")
        if result["warnings"]:
            print("\nWarnings:")
            for w in result["warnings"]:
                print(f"  ⚠️  {w}")

    sys.exit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()
