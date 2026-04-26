#!/usr/bin/env python3
"""Generate vertical 9:16 shorts from horizontal Pollinate videos.

Reads timing.json, selects section combos by strategy, cuts audio/subtitles,
generates per-short asset directories ready for Remotion rendering.

Usage:
    # Auto combo (merge sections to 60-90s shorts)
    python generate_shorts.py content/aidlc-one-sentence-to-pr/ --strategy combo

    # Manual section selection
    python generate_shorts.py content/aidlc-one-sentence-to-pr/ --sections crash_story,evolution

    # Highlight mode (hero, climax, outro)
    python generate_shorts.py content/aidlc-one-sentence-to-pr/ --strategy highlight

    # With BGM mixing
    python generate_shorts.py content/aidlc-one-sentence-to-pr/ --sections crash_story,evolution \
        --bgm bgm_peppy.mp3 --bgm-volume 0.03

    # Generate + render
    python generate_shorts.py content/aidlc-one-sentence-to-pr/ --strategy combo --render

Outputs per short (in content/{name}/shorts/{short_id}/):
    short_audio.wav     — cut audio segment
    short_audio.srt     — rebased subtitles
    timing_short.json   — rebased section timing for Remotion
    short_config.json   — metadata for ShortVideo.tsx composition
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import timedelta


# ---------------------------------------------------------------------------
# SRT parsing / writing
# ---------------------------------------------------------------------------

_SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def _parse_srt_time(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _format_srt_time(seconds):
    if seconds < 0:
        seconds = 0
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(srt_path):
    """Parse SRT file into list of {index, start, end, text}."""
    entries = []
    if not os.path.isfile(srt_path):
        return entries

    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        m = _SRT_TIME_RE.search(lines[1])
        if not m:
            continue
        start = _parse_srt_time(*m.groups()[:4])
        end = _parse_srt_time(*m.groups()[4:])
        text = "\n".join(lines[2:])
        entries.append({"index": int(lines[0]), "start": start, "end": end, "text": text})

    return entries


def write_srt(entries, output_path):
    """Write SRT entries to file."""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, e in enumerate(entries, 1):
            f.write(f"{i}\n")
            f.write(f"{_format_srt_time(e['start'])} --> {_format_srt_time(e['end'])}\n")
            f.write(f"{e['text']}\n\n")


def filter_and_rebase_srt(entries, start_s, end_s):
    """Filter SRT entries within time range and rebase to start at 0."""
    filtered = []
    for e in entries:
        # Include if any overlap with the range
        if e["end"] <= start_s or e["start"] >= end_s:
            continue
        filtered.append({
            "start": max(0, e["start"] - start_s),
            "end": min(end_s - start_s, e["end"] - start_s),
            "text": e["text"],
        })
    return filtered


# ---------------------------------------------------------------------------
# Timing / section selection
# ---------------------------------------------------------------------------

def load_timing(content_dir):
    """Load timing.json from content video directory."""
    timing_path = os.path.join(content_dir, "video", "timing.json")
    if not os.path.isfile(timing_path):
        print(f"Error: timing.json not found at {timing_path}", file=sys.stderr)
        sys.exit(1)

    with open(timing_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_sections(timing, names=None):
    """Get sections, optionally filtered by name list."""
    sections = [s for s in timing.get("sections", []) if not s.get("is_silent")]
    if names:
        name_list = [n.strip() for n in names]
        sections = [s for s in sections if s["name"] in name_list]
        # Preserve requested order
        order = {n: i for i, n in enumerate(name_list)}
        sections.sort(key=lambda s: order.get(s["name"], 999))
    return sections


def strategy_single(sections, min_s=30, max_s=120):
    """Each section becomes a short. Skip sections outside duration range."""
    shorts = []
    for s in sections:
        dur = s["duration"]
        if dur < min_s:
            print(f"  ⏭ {s['name']} ({dur:.0f}s) — skipped, below {min_s}s minimum")
            continue
        if dur > max_s:
            print(f"  ⚠ {s['name']} ({dur:.0f}s) — exceeds {max_s}s, including anyway")
        shorts.append({
            "id": f"short_{s['name']}",
            "sections": [s],
            "start_time": s["start_time"],
            "end_time": s["end_time"],
            "duration": dur,
        })
    return shorts


def strategy_combo(sections, min_s=60, max_s=90, hard_max=120):
    """Merge adjacent sections into 60-90s combos."""
    shorts = []
    current = {"sections": [], "start_time": 0, "end_time": 0, "duration": 0}

    for s in sections:
        if not current["sections"]:
            current = {
                "sections": [s],
                "start_time": s["start_time"],
                "end_time": s["end_time"],
                "duration": s["duration"],
            }
        elif current["duration"] + s["duration"] <= hard_max:
            current["sections"].append(s)
            current["end_time"] = s["end_time"]
            current["duration"] += s["duration"]
        else:
            # Flush current combo
            shorts.append(current)
            current = {
                "sections": [s],
                "start_time": s["start_time"],
                "end_time": s["end_time"],
                "duration": s["duration"],
            }

        # If reached target range, flush
        if current["duration"] >= min_s:
            shorts.append(current)
            current = {"sections": [], "start_time": 0, "end_time": 0, "duration": 0}

    # Flush remainder
    if current["sections"]:
        shorts.append(current)

    # Label each combo
    for i, short in enumerate(shorts):
        names = [s["name"] for s in short["sections"]]
        short["id"] = f"short_{i+1:02d}_{'_'.join(names[:2])}"

    # Report
    for short in shorts:
        names = [s["name"] for s in short["sections"]]
        status = "✅" if min_s <= short["duration"] <= hard_max else "⚠"
        print(f"  {status} {short['id']} ({short['duration']:.0f}s): {', '.join(names)}")

    return shorts


def strategy_highlight(sections):
    """Pick high-energy sections: hero, climax-like, outro."""
    # Heuristic: longest non-intro/outro sections are likely climax
    candidates = sorted(
        [s for s in sections if s["name"] not in ("hero", "outro")],
        key=lambda s: s["duration"],
        reverse=True,
    )

    # Take hero + top 1-2 longest sections + outro (if it exists)
    hero = [s for s in sections if s["name"] == "hero"]
    outro = [s for s in sections if s["name"] == "outro"]
    highlights = hero + candidates[:2] + outro

    # Sort by original time order
    highlights.sort(key=lambda s: s["start_time"])

    # Build short plans — each highlight gets its own short
    shorts = []
    # Combo 1: hero + top climax
    if hero and candidates:
        combo = hero + [candidates[0]]
        dur = sum(s["duration"] for s in combo)
        shorts.append({
            "id": f"short_highlight_hook",
            "sections": combo,
            "start_time": combo[0]["start_time"],
            "end_time": combo[-1]["end_time"],
            "duration": dur,
        })

    # Combo 2: top 2 climax sections
    if len(candidates) >= 2:
        # Check if they're adjacent for cleaner audio
        c1, c2 = candidates[0], candidates[1]
        combo = sorted([c1, c2], key=lambda s: s["start_time"])
        dur = combo[-1]["end_time"] - combo[0]["start_time"]
        shorts.append({
            "id": f"short_highlight_climax",
            "sections": combo,
            "start_time": combo[0]["start_time"],
            "end_time": combo[-1]["end_time"],
            "duration": dur,
        })

    for short in shorts:
        names = [s["name"] for s in short["sections"]]
        print(f"  ✨ {short['id']} ({short['duration']:.0f}s): {', '.join(names)}")

    return shorts


def strategy_manual(sections, names):
    """Manual section selection — combine specified sections into one short."""
    selected = get_sections({"sections": sections}, names)
    if not selected:
        print(f"Error: no sections matched {names}", file=sys.stderr)
        sys.exit(1)

    # Sort by time order
    selected.sort(key=lambda s: s["start_time"])
    start = selected[0]["start_time"]
    end = selected[-1]["end_time"]
    dur = end - start

    short = {
        "id": f"short_{'_'.join(n.strip() for n in names[:3])}",
        "sections": selected,
        "start_time": start,
        "end_time": end,
        "duration": dur,
    }
    print(f"  🎯 {short['id']} ({dur:.0f}s): {', '.join(s['name'] for s in selected)}")
    return [short]


# ---------------------------------------------------------------------------
# Asset generation
# ---------------------------------------------------------------------------

def cut_audio(source_wav, start_s, duration_s, output_wav):
    """Cut audio segment using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_s),
        "-t", str(duration_s),
        "-i", source_wav,
        "-c", "copy",
        output_wav,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Retry without -c copy (handles edge cases with WAV seeking)
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_s),
            "-t", str(duration_s),
            "-i", source_wav,
            "-ar", "48000", "-ac", "1",
            output_wav,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ✗ Audio cut failed: {result.stderr[:200]}", file=sys.stderr)
            return False
    return True


def mix_bgm(audio_wav, bgm_path, output_wav, volume=0.03, fade_in=2, fade_out=3):
    """Mix BGM into short audio."""
    if not bgm_path or not os.path.isfile(bgm_path):
        return False

    duration_result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_wav],
        capture_output=True, text=True,
    )
    if not duration_result.stdout.strip():
        return False
    dur = float(duration_result.stdout.strip())
    fade_out_start = max(0, dur - fade_out)

    cmd = [
        "ffmpeg", "-y",
        "-i", audio_wav,
        "-stream_loop", "-1", "-i", bgm_path,
        "-filter_complex",
        f"[1:a]volume={volume},afade=t=in:st=0:d={fade_in},"
        f"afade=t=out:st={fade_out_start}:d={fade_out}[bgm];"
        f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[out]",
        "-map", "[out]", "-ar", "48000", "-ac", "1",
        output_wav,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0


def generate_short_assets(short, content_dir, bgm_path=None, bgm_volume=0.03):
    """Generate all assets for a single short."""
    video_dir = os.path.join(content_dir, "video")
    shorts_dir = os.path.join(content_dir, "shorts", short["id"])
    os.makedirs(shorts_dir, exist_ok=True)

    source_wav = os.path.join(video_dir, "podcast_audio.wav")
    source_srt = os.path.join(video_dir, "podcast_audio.srt")

    start = short["start_time"]
    end = short["end_time"]
    duration = end - start

    # 1. Cut audio
    short_audio = os.path.join(shorts_dir, "short_audio.wav")
    if not cut_audio(source_wav, start, duration, short_audio):
        return False

    # 2. Mix BGM if provided
    if bgm_path:
        bgm_audio = os.path.join(shorts_dir, "short_audio_bgm.wav")
        if mix_bgm(short_audio, bgm_path, bgm_audio, bgm_volume):
            # Replace original with BGM version
            os.rename(bgm_audio, short_audio)
            print(f"    BGM mixed ({bgm_volume*100:.0f}%)")

    # 3. Cut and rebase subtitles
    srt_entries = parse_srt(source_srt)
    short_srt = filter_and_rebase_srt(srt_entries, start, end)
    write_srt(short_srt, os.path.join(shorts_dir, "short_audio.srt"))

    # 4. Generate rebased timing
    rebased_sections = []
    for s in short["sections"]:
        rebased_sections.append({
            "name": s["name"],
            "label": s.get("label", s["name"]),
            "start_time": s["start_time"] - start,
            "end_time": s["end_time"] - start,
            "duration": s["duration"],
            "start_frame": int((s["start_time"] - start) * 30),
            "duration_frames": int(s["duration"] * 30),
            "is_silent": False,
        })

    timing_short = {
        "total_duration": duration,
        "fps": 30,
        "total_frames": int(duration * 30),
        "sections": rebased_sections,
        "source": {
            "content_dir": os.path.basename(content_dir),
            "original_start": start,
            "original_end": end,
        },
    }
    with open(os.path.join(shorts_dir, "timing.json"), "w", encoding="utf-8") as f:
        json.dump(timing_short, f, indent=2, ensure_ascii=False)

    # 5. Generate short config for Remotion composition
    section_names = [s["name"] for s in short["sections"]]
    intro_frames = 90  # 3s at 30fps
    cta_frames = 90
    content_frames = int(duration * 30)
    total_frames = intro_frames + content_frames + cta_frames

    short_config = {
        "id": short["id"],
        "sections": section_names,
        "sectionTitle": short["sections"][0].get("label", section_names[0]),
        "contentFrames": content_frames,
        "introFrames": intro_frames,
        "ctaFrames": cta_frames,
        "transitionFrames": 10,
        "totalFrames": total_frames,
        "totalDuration": duration + 6,  # +6s for intro+CTA
        "width": 2160,
        "height": 3840,
        "fps": 30,
    }
    with open(os.path.join(shorts_dir, "short_config.json"), "w", encoding="utf-8") as f:
        json.dump(short_config, f, indent=2, ensure_ascii=False)

    # Verify audio duration
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", short_audio],
        capture_output=True, text=True,
    )
    actual_dur = float(probe.stdout.strip()) if probe.stdout.strip() else 0

    print(f"    Audio: {actual_dur:.1f}s, SRT: {len(short_srt)} entries, "
          f"Sections: {len(rebased_sections)}")
    print(f"    Output: {shorts_dir}/")

    return True


# ---------------------------------------------------------------------------
# Render (optional)
# ---------------------------------------------------------------------------

def render_short(short_dir, studio_dir):
    """Render a short using Remotion (placeholder — requires composition registration)."""
    config_path = os.path.join(short_dir, "short_config.json")
    if not os.path.isfile(config_path):
        print(f"  ✗ No short_config.json in {short_dir}", file=sys.stderr)
        return False

    with open(config_path, "r") as f:
        config = json.load(f)

    print(f"\n  🎬 Render instructions for {config['id']}:")
    print(f"     1. Copy {short_dir}/ contents to {studio_dir}/public/")
    print(f"     2. Register ShortVideo composition in Root.tsx with:")
    print(f"        - id: \"{config['id']}\"")
    print(f"        - width: {config['width']}, height: {config['height']}")
    print(f"        - durationInFrames: {config['totalFrames']}")
    print(f"     3. npx remotion render src/remotion/index.ts {config['id']} \\")
    print(f"          {short_dir}/output.mp4 --codec h264")
    print(f"     Total: {config['totalDuration']:.0f}s "
          f"(intro {config['introFrames']//30}s + content {config['contentFrames']//30}s + CTA {config['ctaFrames']//30}s)")

    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="Generate vertical 9:16 shorts from horizontal Pollinate videos",
        epilog="Strategies: combo (default, 60-90s merges), single (per-section), "
               "highlight (auto-pick climax). Use --sections for manual selection.",
    )
    parser.add_argument("content_dir", help="Content directory (e.g. content/aidlc-one-sentence-to-pr/)")
    parser.add_argument("--strategy", "-s", default="combo",
                       choices=["combo", "single", "highlight"],
                       help="Section selection strategy (default: combo)")
    parser.add_argument("--sections", default=None,
                       help="Manual comma-separated section names (overrides strategy)")
    parser.add_argument("--min-duration", type=int, default=30,
                       help="Minimum short duration in seconds (default: 30)")
    parser.add_argument("--max-duration", type=int, default=120,
                       help="Maximum short duration in seconds (default: 120)")
    parser.add_argument("--bgm", default=None,
                       help="BGM file path for mixing")
    parser.add_argument("--bgm-volume", type=float, default=0.03,
                       help="BGM volume level (default: 0.03)")
    parser.add_argument("--render", action="store_true",
                       help="Print render instructions after generating assets")
    parser.add_argument("--json", action="store_true",
                       help="Output short plan as JSON (no asset generation)")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show plan without generating assets")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    content_dir = args.content_dir.rstrip("/")
    if not os.path.isdir(content_dir):
        print(f"Error: Content directory not found: {content_dir}", file=sys.stderr)
        sys.exit(1)

    # Load timing
    timing = load_timing(content_dir)
    all_sections = [s for s in timing.get("sections", []) if not s.get("is_silent")]
    print(f"Loaded {len(all_sections)} sections, total {timing['total_duration']:.0f}s")

    # Select shorts
    if args.sections:
        section_names = [n.strip() for n in args.sections.split(",")]
        shorts = strategy_manual(all_sections, section_names)
    elif args.strategy == "single":
        shorts = strategy_single(all_sections, args.min_duration, args.max_duration)
    elif args.strategy == "highlight":
        shorts = strategy_highlight(all_sections)
    else:
        shorts = strategy_combo(all_sections, min_s=60, max_s=90, hard_max=args.max_duration)

    if not shorts:
        print("No shorts generated — check section durations and strategy.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{len(shorts)} short(s) planned:")
    for short in shorts:
        names = [s["name"] for s in short["sections"]]
        print(f"  {short['id']}: {short['duration']:.0f}s ({', '.join(names)})")

    # JSON output mode
    if args.json:
        output = [{
            "id": s["id"],
            "sections": [sec["name"] for sec in s["sections"]],
            "start_time": s["start_time"],
            "end_time": s["end_time"],
            "duration": s["duration"],
        } for s in shorts]
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    # Dry run
    if args.dry_run:
        total_short_time = sum(s["duration"] for s in shorts)
        print(f"\nDry run: {len(shorts)} shorts, {total_short_time:.0f}s total content")
        print(f"  +6s each for intro/CTA = {total_short_time + len(shorts)*6:.0f}s rendered")
        return

    # Resolve BGM path
    bgm_path = None
    if args.bgm:
        # Try absolute, then relative to video dir, then relative to brand assets
        candidates = [
            args.bgm,
            os.path.join(content_dir, "video", args.bgm),
            os.path.join(os.path.dirname(__file__), "..", "brand", "assets", "bgm", args.bgm),
        ]
        for c in candidates:
            if os.path.isfile(c):
                bgm_path = c
                break
        if not bgm_path:
            print(f"Warning: BGM file not found: {args.bgm}", file=sys.stderr)

    # Generate assets
    print(f"\nGenerating short assets...")
    studio_dir = os.path.expanduser("~/.swarm-ai/SwarmWS/Services/pollinate-studio")

    for short in shorts:
        names = [s["name"] for s in short["sections"]]
        print(f"\n  📹 {short['id']} ({short['duration']:.0f}s): {', '.join(names)}")
        success = generate_short_assets(short, content_dir, bgm_path, args.bgm_volume)
        if not success:
            print(f"  ✗ Failed to generate assets for {short['id']}")
            continue

        if args.render:
            render_short(
                os.path.join(content_dir, "shorts", short["id"]),
                studio_dir,
            )

    # Summary
    shorts_dir = os.path.join(content_dir, "shorts")
    short_dirs = [d for d in os.listdir(shorts_dir) if os.path.isdir(os.path.join(shorts_dir, d))] if os.path.isdir(shorts_dir) else []
    print(f"\n{'='*50}")
    print(f"Done: {len(short_dirs)} short(s) in {shorts_dir}/")
    for d in sorted(short_dirs):
        config_path = os.path.join(shorts_dir, d, "short_config.json")
        if os.path.isfile(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
            print(f"  {d}/  ({cfg['totalDuration']:.0f}s, {cfg['width']}x{cfg['height']})")


if __name__ == "__main__":
    main()
