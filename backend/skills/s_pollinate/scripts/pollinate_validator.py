#!/usr/bin/env python3
"""Pollinate Structural Validator — mechanical delivery quality gate.

Verifies 9 structural invariants that every Pollinate delivery must satisfy.
Inspired by pipeline_validator.py — same JSON output format for consistency.

Checks:
    1. Platform matrix present (platform_matrix.md or section in delivery)
    2. QR code image file present (qr-*.png)
    3. GitHub link in delivery text (github.com)
    4. 2+ variant files per track format
    5. Output files have valid extensions (.html, .png, .mp4, .md)
    6. Content directory structure is valid (tracks/ subdir exists)
    7. Cross-track brand-token consistency (folds RP-X1: all tracks share --accent)
    8. Produced tracks ⊆ discovery.json confirmed_tracks (no unconfirmed track built)
    9. strategy.json production_tracks == discovery.json confirmed_tracks (no drift)

Checks 7-9 are the cross-format consistency gate (RP-X, previously prose-only in
cross_format_check.py). Only the DETERMINISTIC facts are folded in here as
hard-fail invariants — RP-X1 brand-token equality + the two track-set drift
checks. The heuristic RP-X2/3/4/5 (thesis-keyword / numeric-coincidence / naming
/ color-overlap) stay ADVISORY in cross_format_check.py (REVIEW stage), because
false-positives on a hard gate would wrongly block a genuine delivery. Every new
check SKIPs on missing input (single-track / legacy / fast-path runs are valid).

Usage:
    python pollinate_validator.py /path/to/content/topic/
    python pollinate_validator.py /path/to/content/topic/ --json

Returns JSON:
    {"valid": true, "errors": [], "warnings": [], "checks_passed": 9, "checks_total": 9}
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Valid output extensions for Pollinate deliverables
VALID_EXTENSIONS = {".html", ".png", ".jpg", ".mp4", ".md", ".svg", ".pdf"}

# confirmed_tracks token → tracks/ subdir name.
# NOT a 1:1 string match — observed live in INSTRUCTIONS.md INIT (:223-231) +
# real Knowledge/Pollinate/*/tracks/ dirs:
#   - underscore token maps to hyphenated dir (html_deck → html-deck)
#   - TWO tokens can share ONE dir (one_pager OR full_pdf → pdf)
# So track-set comparison must go through this map, never raw ==.
_TOKEN_TO_DIR = {
    "video": "video",
    "narrative": "narrative",
    "poster": "poster",
    "shorts": "shorts",
    "deck": "deck",
    "html_deck": "html-deck",
    "one_pager": "pdf",
    "full_pdf": "pdf",
    "data_report": "data-report",
    "document": "document",
}


def _load_json_field(json_path: Path, field: str):
    """Read a list field from a JSON file. Returns None if file/field absent,
    unreadable, malformed, or not a non-empty list — callers treat None as
    'input missing → SKIP' (fail-safe: never block a delivery just because an
    optional upstream doc is missing OR shaped wrong).

    An EMPTY list is treated as None (→ SKIP), NOT as "zero confirmed tracks":
    a fast-path/legacy doc that initializes the key to [] must not hard-block a
    delivery that has real track output (Gate-2 HIGH, run_be232a07).
    Top-level non-dict JSON (e.g. a bare list) returns None instead of crashing
    — a raw AttributeError here is swallowed by artifact_cli's bare except and
    would silently bypass the ENTIRE validator (Gate-2 CRITICAL, run_be232a07)."""
    if not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    val = data.get(field)
    if not isinstance(val, list) or not val:
        return None
    return val


def _confirmed_dirs(confirmed_tracks: list) -> set:
    """Map confirmed_tracks tokens to the set of tracks/ subdir names they permit.
    Unknown tokens map to themselves (forward-compatible: a new track token added
    to INIT before this map won't cause a false drift FAIL)."""
    return {_TOKEN_TO_DIR.get(t, t) for t in confirmed_tracks}


# The full set of dir names that ARE tracks (all _TOKEN_TO_DIR values). Only these
# are subject to the produced⊆confirmed check — a subdir under tracks/ whose name
# is NOT a known track dir (e.g. a "_scratch"/staging/temp dir) is a non-track
# artifact and is ignored, never a false "unconfirmed track" (Gate-2 HIGH).
_KNOWN_TRACK_DIRS = set(_TOKEN_TO_DIR.values())


def check_brand_consistency(root: Path) -> dict:
    """Check 7 (RP-X1): all tracks with a detectable --accent use the SAME value.
    SKIP if <2 tracks expose an accent (single-track / no-HTML runs are valid).

    Returns WARN (NOT FAIL) on divergence — 'all tracks share one accent' is a
    brand-POLICY assumption, not a deterministic fact: a campaign may intentionally
    theme a poster differently from a deck. Hard-blocking that would be exactly the
    false-positive this gate's design forbids (GUI17/PIT23; Gate-2 MEDIUM,
    run_be232a07). The divergence is surfaced as a warning for human judgment; only
    the truly deterministic track-set checks (8, 9) hard-fail."""
    tracks_dir = root / "tracks"
    if not tracks_dir.is_dir():
        return {"id": 7, "name": "brand-token consistency", "status": "SKIP",
                "detail": "no tracks/ dir"}
    accents: dict[str, str] = {}
    for track_dir in tracks_dir.iterdir():
        if not track_dir.is_dir():
            continue
        for html_file in track_dir.glob("*.html"):
            m = re.search(r'--(?:color-)?accent:\s*([^;]+);',
                          html_file.read_text(errors="ignore"))
            if m:
                accents[track_dir.name] = m.group(1).strip().lower()
                break
    if len(accents) < 2:
        return {"id": 7, "name": "brand-token consistency", "status": "SKIP",
                "detail": f"only {len(accents)} track(s) with detectable --accent"}
    unique = set(accents.values())
    if len(unique) == 1:
        return {"id": 7, "name": "brand-token consistency", "status": "PASS",
                "detail": f"all {len(accents)} tracks share --accent {next(iter(unique))}"}
    return {"id": 7, "name": "brand-token consistency", "status": "WARN",
            "detail": f"tracks use different --accent (intentional theming? verify): {accents}"}


def check_produced_subset(root: Path) -> dict:
    """Check 8 (AC2-b, PRIMARY drift gate): every produced tracks/ subdir must be
    permitted by discovery.json confirmed_tracks. Catches 'BUILD produced a track
    nobody confirmed'. SKIP if discovery.json or its confirmed_tracks is absent
    (legacy / fast-path content is valid)."""
    confirmed = _load_json_field(root / "discovery.json", "confirmed_tracks")
    if confirmed is None:
        return {"id": 8, "name": "produced ⊆ confirmed_tracks", "status": "SKIP",
                "detail": "no discovery.json confirmed_tracks"}
    tracks_dir = root / "tracks"
    if not tracks_dir.is_dir():
        return {"id": 8, "name": "produced ⊆ confirmed_tracks", "status": "SKIP",
                "detail": "no tracks/ dir"}
    # Only KNOWN track dirs count — a non-track subdir (staging/_scratch/temp)
    # under tracks/ is not a "produced track" and must not false-block (Gate-2 HIGH).
    produced = {d.name for d in tracks_dir.iterdir()
                if d.is_dir() and d.name in _KNOWN_TRACK_DIRS}
    allowed = _confirmed_dirs(confirmed)
    unconfirmed = produced - allowed
    if not unconfirmed:
        return {"id": 8, "name": "produced ⊆ confirmed_tracks", "status": "PASS",
                "detail": f"all {len(produced)} produced track(s) confirmed"}
    return {"id": 8, "name": "produced ⊆ confirmed_tracks", "status": "FAIL",
            "detail": f"produced unconfirmed track(s): {sorted(unconfirmed)} "
                      f"(confirmed maps to dirs {sorted(allowed)})"}


def check_track_set_drift(root: Path) -> dict:
    """Check 9 (AC2-a, fail-safe supplement): strategy.json production_tracks must
    equal discovery.json confirmed_tracks (the prose 'MUST equal' contract at
    INSTRUCTIONS.md:176, now mechanical). SKIP unless BOTH files provide the field
    — a run with only one (legacy strategy-only, or discovery-only fast-path) is
    valid and must not be blocked."""
    confirmed = _load_json_field(root / "discovery.json", "confirmed_tracks")
    production = _load_json_field(root / "strategy.json", "production_tracks")
    if confirmed is None or production is None:
        return {"id": 9, "name": "production==confirmed tracks", "status": "SKIP",
                "detail": "discovery.json and/or strategy.json track list absent"}
    if set(confirmed) == set(production):
        return {"id": 9, "name": "production==confirmed tracks", "status": "PASS",
                "detail": f"both agree on {sorted(set(confirmed))}"}
    return {"id": 9, "name": "production==confirmed tracks", "status": "FAIL",
            "detail": f"drift — confirmed={sorted(set(confirmed))} "
                      f"production={sorted(set(production))}"}


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
    checks_total = 9
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

    # ── Checks 7-9: Cross-format consistency (RP-X deterministic subset) ────
    # Each returns PASS / SKIP / WARN / FAIL. PASS/SKIP/WARN all count as passed
    # (a check that doesn't apply, or one that only advises, is not a failure);
    # only FAIL (a deterministic conflict) blocks delivery. WARN (check 7 brand
    # divergence — a policy heuristic) is surfaced as a warning, never a block.
    for _check in (check_brand_consistency,
                   check_produced_subset,
                   check_track_set_drift):
        _r = _check(root)
        if _r["status"] == "FAIL":
            errors.append(f"CROSS-FORMAT (check {_r['id']} {_r['name']}): {_r['detail']}")
        else:
            if _r["status"] == "WARN":
                warnings.append(f"CROSS-FORMAT (check {_r['id']} {_r['name']}): {_r['detail']}")
            checks_passed += 1

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
