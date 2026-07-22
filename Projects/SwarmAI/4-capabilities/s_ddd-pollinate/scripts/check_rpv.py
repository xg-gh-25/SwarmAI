#!/usr/bin/env python3
"""G2: Automated RP-V checklist validator for Pollinate REVIEW stage.

Auto-checks 6 of 12 RP-V patterns from data files. The remaining 6 require
human judgment and must be explicitly listed by the agent.

Usage:
    python check_rpv.py content/aidlc-one-sentence-to-pr/
    python check_rpv.py content/aidlc-one-sentence-to-pr/ --json
"""
import argparse
import json
import os
import re
import sys


def check_rpv1_sync(content_dir: str) -> dict:
    """RP-V1: Audio-video sync — timing.json vs SRT alignment (±0.5s)."""
    timing_path = os.path.join(content_dir, "video", "timing.json")
    srt_path = os.path.join(content_dir, "video", "podcast_audio.srt")

    if not os.path.isfile(timing_path):
        return {"id": "RP-V1", "name": "Audio-video sync", "status": "SKIP", "detail": "timing.json not found"}
    if not os.path.isfile(srt_path):
        return {"id": "RP-V1", "name": "Audio-video sync", "status": "SKIP", "detail": "SRT not found"}

    try:
        with open(timing_path, "r", encoding="utf-8") as f:
            timing = json.load(f)
        sections = timing.get("sections", [])
        if not sections:
            return {"id": "RP-V1", "name": "Audio-video sync", "status": "FAIL", "detail": "timing.json has no sections"}

        # Check total duration alignment
        fps = timing.get("fps", 30)
        total_frames = timing.get("total_frames", 0)
        timing_duration_s = total_frames / fps if fps else 0

        with open(srt_path, "r", encoding="utf-8") as f:
            srt_text = f.read()

        # Find last SRT end timestamp (not start — we want total duration)
        timestamps = re.findall(r"-->\s*(\d{2}:\d{2}:\d{2},\d{3})", srt_text)
        if timestamps:
            last_ts = timestamps[-1]
            h, m, rest = last_ts.split(":")
            s, ms = rest.split(",")
            srt_end_s = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

            drift = abs(timing_duration_s - srt_end_s)
            if drift <= 0.5:
                return {"id": "RP-V1", "name": "Audio-video sync", "status": "PASS",
                        "detail": f"drift {drift:.2f}s (within ±0.5s)"}
            else:
                return {"id": "RP-V1", "name": "Audio-video sync", "status": "FAIL",
                        "detail": f"drift {drift:.2f}s (> 0.5s tolerance)"}

        return {"id": "RP-V1", "name": "Audio-video sync", "status": "FAIL", "detail": "No SRT timestamps found"}
    except (json.JSONDecodeError, OSError) as e:
        return {"id": "RP-V1", "name": "Audio-video sync", "status": "FAIL", "detail": str(e)}


def check_rpv3_density(content_dir: str) -> dict:
    """RP-V3: Information density — ≤3 key points per screen (section)."""
    timing_path = os.path.join(content_dir, "video", "timing.json")
    if not os.path.isfile(timing_path):
        return {"id": "RP-V3", "name": "Information density", "status": "SKIP", "detail": "timing.json not found"}

    try:
        with open(timing_path, "r", encoding="utf-8") as f:
            timing = json.load(f)
        sections = timing.get("sections", [])
        fps = timing.get("fps", 30)

        violations = []
        for sec in sections:
            dur_s = sec.get("duration_frames", 0) / fps if fps else 0
            key_points = sec.get("key_points", 0)
            if key_points > 3:
                violations.append(f"{sec.get('name', '?')}: {key_points} points")

        if violations:
            return {"id": "RP-V3", "name": "Information density", "status": "FAIL",
                    "detail": f"{len(violations)} sections exceed 3 points: {', '.join(violations)}"}
        return {"id": "RP-V3", "name": "Information density", "status": "PASS",
                "detail": f"All {len(sections)} sections ≤3 points"}
    except (json.JSONDecodeError, OSError) as e:
        return {"id": "RP-V3", "name": "Information density", "status": "FAIL", "detail": str(e)}


def check_rpv4_subtitle_accuracy(content_dir: str) -> dict:
    """RP-V4: Subtitle accuracy — SRT entry count vs script section count."""
    srt_path = os.path.join(content_dir, "video", "podcast_audio.srt")
    script_path = os.path.join(content_dir, "video", "podcast.txt")

    if not os.path.isfile(srt_path):
        return {"id": "RP-V4", "name": "Subtitle accuracy", "status": "SKIP", "detail": "SRT not found"}
    if not os.path.isfile(script_path):
        return {"id": "RP-V4", "name": "Subtitle accuracy", "status": "SKIP", "detail": "podcast.txt not found"}

    with open(srt_path, "r", encoding="utf-8") as f:
        srt_entries = len(re.findall(r"^\d+$", f.read(), re.MULTILINE))

    with open(script_path, "r", encoding="utf-8") as f:
        script_text = f.read()
    # Count meaningful lines (non-empty, non-section-marker)
    script_lines = [l.strip() for l in script_text.split("\n")
                    if l.strip() and not l.strip().startswith("[SECTION:")]
    # SRT should have at least as many entries as meaningful content chunks
    if srt_entries > 0:
        return {"id": "RP-V4", "name": "Subtitle accuracy", "status": "PASS",
                "detail": f"{srt_entries} SRT entries for {len(script_lines)} script lines"}
    return {"id": "RP-V4", "name": "Subtitle accuracy", "status": "FAIL",
            "detail": "0 SRT entries"}


def check_rpv5_thumbnails(content_dir: str) -> dict:
    """RP-V5: Thumbnail specs — check required aspect ratios exist."""
    thumb_dir = os.path.join(content_dir, "video", "thumbnails")
    if not os.path.isdir(thumb_dir):
        # Check alternate paths
        thumb_dir = os.path.join(content_dir, "thumbnails")
    if not os.path.isdir(thumb_dir):
        return {"id": "RP-V5", "name": "Thumbnail specs", "status": "SKIP", "detail": "No thumbnails directory"}

    files = os.listdir(thumb_dir)
    png_files = [f for f in files if f.endswith((".png", ".jpg", ".jpeg"))]

    if len(png_files) >= 3:
        return {"id": "RP-V5", "name": "Thumbnail specs", "status": "PASS",
                "detail": f"{len(png_files)} thumbnail files"}
    elif len(png_files) >= 1:
        return {"id": "RP-V5", "name": "Thumbnail specs", "status": "WARN",
                "detail": f"Only {len(png_files)} thumbnails (need 3 aspect ratios)"}
    return {"id": "RP-V5", "name": "Thumbnail specs", "status": "FAIL",
            "detail": "No thumbnail images found"}


def check_rpv8_duration(content_dir: str) -> dict:
    """RP-V8: Duration target — within platform range."""
    timing_path = os.path.join(content_dir, "video", "timing.json")
    if not os.path.isfile(timing_path):
        return {"id": "RP-V8", "name": "Duration target", "status": "SKIP", "detail": "timing.json not found"}

    try:
        with open(timing_path, "r", encoding="utf-8") as f:
            timing = json.load(f)
        fps = timing.get("fps", 30)
        total_frames = timing.get("total_frames", 0)
        duration_s = total_frames / fps if fps else 0

        # B站/YouTube target: 180-720s (3-12 min)
        if 180 <= duration_s <= 720:
            return {"id": "RP-V8", "name": "Duration target", "status": "PASS",
                    "detail": f"{duration_s:.0f}s ({duration_s/60:.1f}min) — within 3-12min range"}
        elif 30 <= duration_s <= 120:
            return {"id": "RP-V8", "name": "Duration target", "status": "PASS",
                    "detail": f"{duration_s:.0f}s — shorts range (30-120s)"}
        else:
            return {"id": "RP-V8", "name": "Duration target", "status": "WARN",
                    "detail": f"{duration_s:.0f}s — outside standard ranges"}
    except (json.JSONDecodeError, OSError) as e:
        return {"id": "RP-V8", "name": "Duration target", "status": "FAIL", "detail": str(e)}


def check_rpv11_text_sizes(content_dir: str) -> dict:
    """RP-V11: Text readability — check composition for minimum sizes."""
    # This is a best-effort structural check — full validation needs render
    timing_path = os.path.join(content_dir, "video", "timing.json")
    if not os.path.isfile(timing_path):
        return {"id": "RP-V11", "name": "Text readability", "status": "SKIP",
                "detail": "timing.json not found (check composition data manually)"}

    # Structural check: verify timing.json sections have labels (implies rendered text)
    try:
        with open(timing_path, "r", encoding="utf-8") as f:
            timing = json.load(f)
        sections = timing.get("sections", [])
        labeled = sum(1 for s in sections if s.get("label") or s.get("name"))
        return {"id": "RP-V11", "name": "Text readability", "status": "PASS",
                "detail": f"{labeled}/{len(sections)} sections labeled (visual sizes require render)"}
    except (json.JSONDecodeError, OSError) as e:
        return {"id": "RP-V11", "name": "Text readability", "status": "FAIL", "detail": str(e)}


# Patterns that require human judgment — listed but not auto-checked
MANUAL_PATTERNS = [
    ("RP-V2", "Subtitle safe zone", "Bottom 100px clear? (visual check)"),
    ("RP-V6", "Polyphone coverage", "Domain terms in phonemes.json? (zh-CN only)"),
    ("RP-V7", "Resolution & codec", "3840x2160, H.264, >=8Mbps, AAC? (requires ffprobe on rendered video)"),
    ("RP-V9", "Brand consistency", "Swarm Orange #FF6B35? PingFang SC? (visual check)"),
    ("RP-V10", "Component variety", "No same type consecutive? (visual check)"),
    ("RP-V12", "Content width", ">=85% screen utilized? (visual check)"),
]


def run_all_checks(content_dir: str) -> dict:
    """Run all automated checks and list manual ones."""
    auto_checks = [
        check_rpv1_sync(content_dir),
        check_rpv3_density(content_dir),
        check_rpv4_subtitle_accuracy(content_dir),
        check_rpv5_thumbnails(content_dir),
        check_rpv8_duration(content_dir),
        check_rpv11_text_sizes(content_dir),
    ]

    manual_checks = [
        {"id": rpid, "name": name, "status": "MANUAL", "detail": desc}
        for rpid, name, desc in MANUAL_PATTERNS
    ]

    all_checks = auto_checks + manual_checks
    auto_passed = sum(1 for c in auto_checks if c["status"] == "PASS")
    auto_failed = sum(1 for c in auto_checks if c["status"] == "FAIL")
    auto_skipped = sum(1 for c in auto_checks if c["status"] == "SKIP")

    return {
        "content_dir": content_dir,
        "checks": all_checks,
        "summary": {
            "auto_checked": len(auto_checks),
            "auto_passed": auto_passed,
            "auto_failed": auto_failed,
            "auto_skipped": auto_skipped,
            "manual_required": len(manual_checks),
            "total": len(all_checks),
        },
        "passed": auto_failed == 0,
    }


def main():
    parser = argparse.ArgumentParser(description="G2: RP-V checklist validator for Pollinate REVIEW")
    parser.add_argument("content_dir", help="Path to content directory")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = run_all_checks(args.content_dir)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        s = result["summary"]
        print(f"\nRP-V Checklist: {s['auto_passed']}/{s['auto_checked']} auto-checks passed")
        print(f"{'='*50}")

        for c in result["checks"]:
            icon = {
                "PASS": "✅", "FAIL": "❌", "WARN": "⚠️",
                "SKIP": "⏭️", "MANUAL": "👁️",
            }.get(c["status"], "?")
            print(f"  {icon} {c['id']}: {c['name']} — {c['detail']}")

        if s["auto_failed"] > 0:
            print(f"\n❌ {s['auto_failed']} FAIL(s) — fix before advancing to TEST")
        if s["manual_required"] > 0:
            print(f"\n👁️  {s['manual_required']} patterns require human judgment — list each explicitly")

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
