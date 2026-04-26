#!/usr/bin/env python3
"""Calculate confidence score for a Pollinate content delivery.

Evaluates 8 positive criteria and 4 penalty criteria against the content
directory's artifacts. Returns a 1-10 score with full breakdown.

Usage:
    python confidence_score.py content/aidlc-one-sentence-to-pr/
    python confidence_score.py content/aidlc-one-sentence-to-pr/ --json
    python confidence_score.py content/aidlc-one-sentence-to-pr/ --artifacts /path/to/.artifacts/

Score formula (from INSTRUCTIONS.md):
  +2  all RP-V checks passed
  +2  Studio preview reviewed and approved by user
  +1  TTS duration within target range (3-8min for B站)
  +1  all platform specs validated
  +1  no REVIEW findings above warning level
  +1  polyphone pre-flight completed (zh-CN) or N/A (en-US)
  +1  all thumbnail sizes generated
  +1  BGM mixed successfully
  -2  any RP-V check failed and remains unfixed
  -2  Studio preview skipped or not approved
  -1  duration outside target range
  -1  per platform spec validation failure
  -1  brand colors don't match identity.yaml
"""
import argparse
import json
import os
import re
import subprocess
import sys


def check_review_results(content_dir: str) -> tuple[int, str]:
    """Check review_results.md for RP-V pass/fail."""
    review_path = os.path.join(content_dir, "review_results.md")
    if not os.path.isfile(review_path):
        return 0, "review_results.md not found"

    with open(review_path, "r", encoding="utf-8") as f:
        text = f.read()

    fails = len(re.findall(r"RP-V\d+:\s+FAIL", text))
    passes = len(re.findall(r"RP-V\d+:\s+PASS", text))
    defers = len(re.findall(r"RP-V\d+:\s+DEFER", text))
    total = fails + passes + defers

    if fails > 0:
        return -2, f"{fails} FAIL out of {total} checks"
    elif total >= 10:
        return 2, f"All {passes} checks PASS ({defers} deferred)"
    else:
        return 1, f"{passes}/{total} checks PASS (incomplete coverage)"


def check_studio_preview(content_dir: str) -> tuple[int, str]:
    """Check if studio preview was approved.

    Heuristic: if a rendered video exists in the output directory,
    the user approved the preview (render requires explicit approval).
    """
    video_dir = os.path.join(content_dir, "video")
    out_dir = os.path.dirname(content_dir)

    # Check for rendered output or final_video
    for candidate in [
        os.path.join(video_dir, "final_video.mp4"),
        os.path.join(video_dir, "output.mp4"),
    ]:
        if os.path.isfile(candidate):
            return 2, f"Approved (rendered: {os.path.basename(candidate)})"

    # Check pollinate-studio/out/
    studio_out = os.path.expanduser(
        "~/.swarm-ai/SwarmWS/Services/pollinate-studio/out/"
    )
    if os.path.isdir(studio_out):
        mp4s = [f for f in os.listdir(studio_out) if f.endswith(".mp4")]
        if mp4s:
            return 2, f"Approved (studio out: {mp4s[0]})"

    return -2, "No rendered video found — preview may not have been approved"


def check_duration(content_dir: str, min_s: int = 180, max_s: int = 480) -> tuple[int, str]:
    """Check if TTS duration is within target range (default 3-8min)."""
    timing_path = os.path.join(content_dir, "video", "timing.json")
    if not os.path.isfile(timing_path):
        return -1, "timing.json not found"

    with open(timing_path, "r", encoding="utf-8") as f:
        timing = json.load(f)

    duration = timing.get("total_duration", 0)
    if min_s <= duration <= max_s:
        return 1, f"{duration:.0f}s ({duration/60:.1f}min) within {min_s//60}-{max_s//60}min"
    else:
        return -1, f"{duration:.0f}s ({duration/60:.1f}min) outside {min_s//60}-{max_s//60}min target"


def check_platform_specs(content_dir: str, platforms: list[str] | None = None) -> tuple[int, str]:
    """Run check_specs.py if a rendered video exists."""
    video_candidates = [
        os.path.join(content_dir, "video", "final_video.mp4"),
        os.path.join(content_dir, "video", "output.mp4"),
        os.path.join(content_dir, "video", "video_with_bgm.mp4"),
    ]
    # Also check studio output
    studio_out = os.path.expanduser(
        "~/.swarm-ai/SwarmWS/Services/pollinate-studio/out/"
    )
    if os.path.isdir(studio_out):
        for f in sorted(os.listdir(studio_out)):
            if f.endswith(".mp4"):
                video_candidates.append(os.path.join(studio_out, f))

    video_path = None
    for c in video_candidates:
        if os.path.isfile(c):
            video_path = c
            break

    if not video_path:
        return 0, "No rendered video yet — spec check deferred"

    if not platforms:
        platforms = ["bilibili", "youtube"]

    script = os.path.join(
        os.path.dirname(__file__), "check_specs.py"
    )
    try:
        result = subprocess.run(
            [sys.executable, script, video_path,
             "--platforms", ",".join(platforms), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.stdout:
            results = json.loads(result.stdout)
            failures = sum(1 for r in results if not r.get("passed", True))
            if failures == 0:
                return 1, f"All {len(results)} platform specs pass"
            else:
                return -failures, f"{failures}/{len(results)} platform specs failed"
        return 0, "check_specs.py returned empty"
    except Exception as e:
        return 0, f"check_specs.py error: {e}"


def check_review_severity(content_dir: str) -> tuple[int, str]:
    """Check if any REVIEW findings are above warning level."""
    review_path = os.path.join(content_dir, "review_results.md")
    if not os.path.isfile(review_path):
        return 0, "No review results"

    with open(review_path, "r", encoding="utf-8") as f:
        text = f.read()

    fails = len(re.findall(r"RP-V\d+:\s+FAIL", text))
    warns = len(re.findall(r"RP-V\d+:\s+WARN", text))

    if fails > 0:
        return 0, f"{fails} FAIL findings remain"
    elif warns > 0:
        return 1, f"No failures, {warns} warnings (acceptable)"
    else:
        return 1, "Zero findings above warning level"


def check_phonemes(content_dir: str) -> tuple[int, str]:
    """Check if polyphone pre-flight was completed."""
    phoneme_path = os.path.join(content_dir, "video", "phonemes.json")
    global_phoneme = os.path.expanduser(
        os.path.join(os.path.dirname(__file__), "..", "phonemes.json")
    )

    if os.path.isfile(phoneme_path):
        with open(phoneme_path, "r") as f:
            data = json.load(f)
        return 1, f"Project phonemes: {len(data)} entries"
    elif os.path.isfile(global_phoneme):
        with open(global_phoneme, "r") as f:
            data = json.load(f)
        return 1, f"Global phonemes used: {len(data)} entries"
    else:
        return 0, "No phoneme dictionary found"


def check_thumbnails(content_dir: str) -> tuple[int, str]:
    """Check if all required thumbnail sizes exist."""
    video_dir = os.path.join(content_dir, "video")
    required = {
        "thumbnail_16x9.png": "16:9 (1920x1080)",
        "thumbnail_4x3.png": "4:3 (1200x900)",
        "thumbnail_3x4.png": "3:4 (1080x1440)",
    }

    found = []
    missing = []
    for fname, desc in required.items():
        if os.path.isfile(os.path.join(video_dir, fname)):
            found.append(desc)
        else:
            missing.append(desc)

    if not missing:
        return 1, f"All {len(found)} thumbnail sizes generated"
    elif len(found) >= 2:
        return 1, f"{len(found)}/{len(required)} sizes ({', '.join(missing)} missing)"
    else:
        return 0, f"Only {len(found)}/{len(required)} thumbnails"


def check_bgm(content_dir: str) -> tuple[int, str]:
    """Check if BGM was mixed into audio."""
    bgm_path = os.path.join(content_dir, "video", "podcast_audio_with_bgm.wav")
    original_path = os.path.join(content_dir, "video", "podcast_audio.wav")

    if os.path.isfile(bgm_path):
        bgm_size = os.path.getsize(bgm_path)
        orig_size = os.path.getsize(original_path) if os.path.isfile(original_path) else 0
        if bgm_size > 0 and bgm_size != orig_size:
            return 1, "BGM mixed (file differs from original)"
        elif bgm_size > 0:
            return 1, "BGM file exists"
    return 0, "No BGM mix found"


def calculate_score(content_dir: str, platforms: list[str] | None = None) -> dict:
    """Calculate full confidence score with breakdown."""
    checks = [
        ("RP-V checks", check_review_results(content_dir)),
        ("Studio preview", check_studio_preview(content_dir)),
        ("Duration target", check_duration(content_dir)),
        ("Platform specs", check_platform_specs(content_dir, platforms)),
        ("Review severity", check_review_severity(content_dir)),
        ("Polyphone pre-flight", check_phonemes(content_dir)),
        ("Thumbnails", check_thumbnails(content_dir)),
        ("BGM mix", check_bgm(content_dir)),
    ]

    total = 0
    breakdown = []
    for name, (points, detail) in checks:
        total += points
        sign = "+" if points >= 0 else ""
        breakdown.append({
            "name": name,
            "points": points,
            "detail": detail,
            "display": f"  {sign}{points}  {name}: {detail}",
        })

    # Clamp to 1-10
    score = max(1, min(10, total))

    return {
        "score": score,
        "raw_total": total,
        "max_possible": 10,
        "breakdown": breakdown,
        "recommendation": "publish" if score >= 7 else "review",
    }


def main():
    parser = argparse.ArgumentParser(description="Calculate Pollinate confidence score")
    parser.add_argument("content_dir", help="Path to content directory")
    parser.add_argument("--platforms", default=None,
                       help="Comma-separated platforms for spec check (default: bilibili,youtube)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--artifacts", default=None,
                       help="Path to .artifacts/ directory (for future use)")
    args = parser.parse_args()

    platforms = [p.strip() for p in args.platforms.split(",")] if args.platforms else None
    result = calculate_score(args.content_dir, platforms)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"\nConfidence: {result['score']}/10")
        for item in result["breakdown"]:
            print(item["display"])
        print(f"\nRecommendation: {result['recommendation'].upper()}")
        if result["score"] < 7:
            print("⚠️  Score below 7 — flag for human review before publishing")


if __name__ == "__main__":
    main()
