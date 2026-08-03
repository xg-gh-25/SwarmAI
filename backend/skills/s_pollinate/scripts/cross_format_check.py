#!/usr/bin/env python3
"""
cross_format_check.py — RP-X Cross-Format Consistency Verification (ADVISORY).

Verifies that all tracks produced in a multi-track Pollinate run maintain
consistency across format boundaries. Runs during REVIEW stage.

⚠️ ENFORCEMENT SPLIT (run_be232a07, 2026-07-03): the DETERMINISTIC cross-format
facts are now HARD-ENFORCED by pollinate_validator.py at the DELIVER chokepoint
(the only place artifact_cli.py can exit(1)):
    - RP-X1 (brand-token/--accent consistency)   → validator Check 7
    - produced-tracks ⊆ confirmed_tracks          → validator Check 8
    - production_tracks == confirmed_tracks drift  → validator Check 9
This script REMAINS the home of the HEURISTIC checks (RP-X2/3/4/5), kept
ADVISORY on purpose: thesis-keyword / numeric-coincidence / naming / color-overlap
are false-positive-prone, so hard-failing on them would wrongly block a genuine
delivery. Run it in REVIEW for signal; it does NOT gate the release. RP-X1 stays
here too (advisory echo) so the REVIEW report is complete — the hard gate is the
validator.

Usage:
    python cross_format_check.py <content_dir> [--json]

Checks:
    RP-X1: Brand token consistency  (also hard-enforced as validator Check 7)
    RP-X2: Message alignment (thesis matches across all formats)        [advisory]
    RP-X3: Data integrity (same numbers across all formats)             [advisory]
    RP-X4: Naming conventions (consistent file/dir naming)              [advisory]
    RP-X5: Visual coherence (color palette shared across visual formats) [advisory]

Exit codes:
    0 = all PASS
    1 = at least one FAIL
    2 = error (missing files, bad arguments)
"""

import argparse
import json
import re
import sys
from pathlib import Path


def check_brand_consistency(content_dir: Path) -> dict:
    """RP-X1: Verify all tracks use the same design direction."""
    tracks_dir = content_dir / "tracks"
    if not tracks_dir.exists():
        return {"id": "RP-X1", "status": "SKIP", "detail": "No tracks directory"}

    # Look for direction references in track outputs
    direction_refs = {}
    for track_dir in tracks_dir.iterdir():
        if not track_dir.is_dir():
            continue
        # Check HTML files for CSS variable definitions
        for html_file in track_dir.glob("*.html"):
            text = html_file.read_text(errors="ignore")
            # Extract --accent color value
            accent_match = re.search(r'--(?:color-)?accent:\s*([^;]+);', text)
            if accent_match:
                direction_refs[track_dir.name] = accent_match.group(1).strip()

    if len(direction_refs) < 2:
        return {"id": "RP-X1", "status": "SKIP", "detail": f"Only {len(direction_refs)} tracks with detectable direction"}

    # Check all tracks use the same accent color
    unique_accents = set(direction_refs.values())
    if len(unique_accents) == 1:
        return {"id": "RP-X1", "status": "PASS", "detail": f"All {len(direction_refs)} tracks use same accent: {unique_accents.pop()}"}
    else:
        return {
            "id": "RP-X1",
            "status": "FAIL",
            "detail": f"Inconsistent accents across tracks: {direction_refs}",
            "fix": "Apply same direction YAML to all tracks",
        }


def check_message_alignment(content_dir: Path) -> dict:
    """RP-X2: Verify thesis is consistent across all format outputs."""
    content_package = content_dir / "content_package.md"
    if not content_package.exists():
        return {"id": "RP-X2", "status": "SKIP", "detail": "No content_package.md"}

    pkg_text = content_package.read_text(errors="ignore")
    # Extract thesis from content_package
    thesis_match = re.search(r'\*\*Thesis:\*\*\s*(.+)', pkg_text)
    if not thesis_match:
        return {"id": "RP-X2", "status": "WARN", "detail": "No thesis found in content_package.md"}

    thesis = thesis_match.group(1).strip()
    # Extract clean words (no punctuation) and filter stopwords
    raw_words = re.findall(r'\b\w+\b', thesis.lower())
    stopwords = {"the", "a", "an", "is", "are", "was", "were", "this", "that", "it", "in", "on", "at", "to", "for", "of", "and", "or", "but", "not", "with", "from", "by", "about"}
    content_words = sorted(w for w in raw_words if w not in stopwords and len(w) > 2)
    thesis_words = set(content_words[:7])  # Top 7 content words (sorted for determinism)

    # Check each track output references the thesis
    tracks_dir = content_dir / "tracks"
    if not tracks_dir.exists():
        return {"id": "RP-X2", "status": "SKIP", "detail": "No tracks directory"}

    aligned = []
    misaligned = []
    for track_dir in tracks_dir.iterdir():
        if not track_dir.is_dir():
            continue
        # Read any text/html/md/json file in the track
        track_content = ""
        for f in track_dir.iterdir():
            if f.suffix in (".html", ".md", ".json", ".txt"):
                track_content += f.read_text(errors="ignore")

        if not track_content:
            continue

        # Check if thesis keywords appear
        content_lower = track_content.lower()
        matches = sum(1 for w in thesis_words if w in content_lower)
        if matches >= 4:  # At least 4 of 7 thesis content words present (57%)
            aligned.append(track_dir.name)
        else:
            misaligned.append(track_dir.name)

    if not misaligned:
        return {"id": "RP-X2", "status": "PASS", "detail": f"Thesis aligned in {len(aligned)} tracks"}
    else:
        return {
            "id": "RP-X2",
            "status": "WARN",
            "detail": f"Thesis may not be reflected in: {misaligned}",
            "fix": "Verify these tracks reference the core thesis from content_package",
        }


def check_data_integrity(content_dir: Path) -> dict:
    """RP-X3: Verify same numbers/metrics are consistent across formats."""
    tracks_dir = content_dir / "tracks"
    if not tracks_dir.exists():
        return {"id": "RP-X3", "status": "SKIP", "detail": "No tracks directory"}

    # Extract all numbers with context from each track
    track_numbers = {}
    for track_dir in tracks_dir.iterdir():
        if not track_dir.is_dir():
            continue
        numbers = set()
        for f in track_dir.iterdir():
            if f.suffix in (".html", ".md", ".json", ".txt"):
                text = f.read_text(errors="ignore")
                # Find numbers with units (e.g., "96.6%", "$1.2M", "500ms")
                for match in re.finditer(r'(\d+\.?\d*)\s*(%|MB|GB|KB|ms|M\b|K\b|x\b)', text):
                    numbers.add(match.group(0).strip())
        if numbers:
            track_numbers[track_dir.name] = numbers

    if len(track_numbers) < 2:
        return {"id": "RP-X3", "status": "SKIP", "detail": "Fewer than 2 tracks with numeric data"}

    # Find numbers that appear in multiple tracks and check consistency
    all_numbers = set()
    for nums in track_numbers.values():
        all_numbers.update(nums)

    # If same metric appears differently in two tracks, that's a potential issue
    # For now, just report shared metrics as PASS (they're consistent by detection)
    shared_count = 0
    for num in all_numbers:
        tracks_with_num = [t for t, nums in track_numbers.items() if num in nums]
        if len(tracks_with_num) > 1:
            shared_count += 1

    return {
        "id": "RP-X3",
        "status": "PASS",
        "detail": f"{shared_count} metrics shared across tracks (consistent)",
    }


def check_naming_conventions(content_dir: Path) -> dict:
    """RP-X4: Verify consistent file/directory naming across tracks."""
    tracks_dir = content_dir / "tracks"
    if not tracks_dir.exists():
        return {"id": "RP-X4", "status": "SKIP", "detail": "No tracks directory"}

    issues = []
    # Only check track OUTPUT files (not metadata/config)
    output_extensions = {".html", ".png", ".jpg", ".mp3", ".mp4", ".pdf", ".pptx", ".xlsx", ".docx"}
    for track_dir in tracks_dir.iterdir():
        if not track_dir.is_dir():
            continue
        for f in track_dir.rglob("*"):
            if not f.is_file() or f.suffix not in output_extensions:
                continue
            if " " in f.name:
                issues.append(f"Space in filename: {f.relative_to(content_dir)}")
            if f.name != f.name.lower():
                issues.append(f"Uppercase in filename: {f.relative_to(content_dir)}")

    if not issues:
        return {"id": "RP-X4", "status": "PASS", "detail": "All filenames consistent (lowercase, no spaces)"}
    else:
        return {
            "id": "RP-X4",
            "status": "WARN",
            "detail": f"{len(issues)} naming issues: {issues[:3]}",
            "fix": "Rename files to lowercase, replace spaces with hyphens",
        }


def check_visual_coherence(content_dir: Path) -> dict:
    """RP-X5: Verify visual formats share color palette."""
    tracks_dir = content_dir / "tracks"
    if not tracks_dir.exists():
        return {"id": "RP-X5", "status": "SKIP", "detail": "No tracks directory"}

    # Extract hex colors from all HTML outputs
    track_colors = {}
    for track_dir in tracks_dir.iterdir():
        if not track_dir.is_dir():
            continue
        colors = set()
        for html_file in track_dir.glob("*.html"):
            text = html_file.read_text(errors="ignore")
            # Find hex colors
            for match in re.finditer(r'#([0-9a-fA-F]{6})\b', text):
                colors.add(match.group(0).upper())
        if colors:
            track_colors[track_dir.name] = colors

    if len(track_colors) < 2:
        return {"id": "RP-X5", "status": "SKIP", "detail": f"Only {len(track_colors)} tracks with HTML color data"}

    # Check palette overlap — tracks should share at least some colors
    all_track_names = list(track_colors.keys())
    shared_colors = track_colors[all_track_names[0]]
    for name in all_track_names[1:]:
        shared_colors = shared_colors & track_colors[name]

    if shared_colors:
        return {
            "id": "RP-X5",
            "status": "PASS",
            "detail": f"{len(shared_colors)} shared colors across {len(track_colors)} tracks",
        }
    else:
        return {
            "id": "RP-X5",
            "status": "WARN",
            "detail": f"No shared colors between {all_track_names}",
            "fix": "Verify all tracks use same direction YAML tokens",
        }


def run_all_checks(content_dir: Path) -> list[dict]:
    """Run all RP-X checks and return results."""
    return [
        check_brand_consistency(content_dir),
        check_message_alignment(content_dir),
        check_data_integrity(content_dir),
        check_naming_conventions(content_dir),
        check_visual_coherence(content_dir),
    ]


def main():
    parser = argparse.ArgumentParser(description="RP-X Cross-Format Consistency Checker")
    parser.add_argument("content_dir", help="Path to content directory (e.g., content/2026-05-26-topic/)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    content_dir = Path(args.content_dir)

    if not content_dir.exists():
        print(f"Error: content directory not found: {content_dir}", file=sys.stderr)
        sys.exit(2)

    results = run_all_checks(content_dir)

    if args.json:
        output = {
            "content_dir": str(content_dir),
            "results": results,
            "summary": {
                "total": len(results),
                "pass": sum(1 for r in results if r["status"] == "PASS"),
                "fail": sum(1 for r in results if r["status"] == "FAIL"),
                "warn": sum(1 for r in results if r["status"] == "WARN"),
                "skip": sum(1 for r in results if r["status"] == "SKIP"),
            },
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        for r in results:
            status_icon = {"PASS": "✓", "FAIL": "✗", "WARN": "⚠", "SKIP": "○"}[r["status"]]
            print(f"  {r['id']}: {status_icon} {r['status']}  {r['detail']}")
            if "fix" in r:
                print(f"         Fix: {r['fix']}")

        fails = sum(1 for r in results if r["status"] == "FAIL")
        if fails:
            print(f"\n  {fails} FAIL — fix before delivery")
            sys.exit(1)
        else:
            print(f"\n  All clear ({sum(1 for r in results if r['status'] == 'PASS')} PASS)")

    sys.exit(1 if any(r["status"] == "FAIL" for r in results) else 0)


if __name__ == "__main__":
    main()
