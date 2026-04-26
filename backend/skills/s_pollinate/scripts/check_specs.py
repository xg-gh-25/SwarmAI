#!/usr/bin/env python3
"""Platform spec validation for rendered video files.

Uses ffprobe to check resolution, codec, bitrate, duration against platform requirements.

Usage:
    python check_specs.py final_video.mp4 --platforms bilibili,youtube
    python check_specs.py final_video.mp4 --platforms xiaohongshu --json
"""
import argparse
import json
import os
import subprocess
import sys


# Platform specifications
PLATFORM_SPECS = {
    "bilibili": {
        "name": "Bilibili (B站)",
        "orientation": "horizontal",
        "width": 3840, "height": 2160,
        "codec": "h264",
        "min_bitrate_kbps": 8000,
        "min_duration_s": 180, "max_duration_s": 720,  # 3-12 min
        "audio_codec": "aac",
        "min_audio_bitrate_kbps": 192,
    },
    "youtube": {
        "name": "YouTube",
        "orientation": "horizontal",
        "width": 3840, "height": 2160,
        "codec": "h264",
        "min_bitrate_kbps": 8000,
        "min_duration_s": 180, "max_duration_s": 720,
        "audio_codec": "aac",
        "min_audio_bitrate_kbps": 192,
    },
    "xiaohongshu": {
        "name": "Xiaohongshu (小红书)",
        "orientation": "vertical",
        "width": 2160, "height": 3840,
        "codec": "h264",
        "min_bitrate_kbps": 6000,
        "min_duration_s": 30, "max_duration_s": 120,
        "audio_codec": "aac",
        "min_audio_bitrate_kbps": 192,
    },
    "douyin": {
        "name": "Douyin (抖音)",
        "orientation": "vertical",
        "width": 2160, "height": 3840,
        "codec": "h264",
        "min_bitrate_kbps": 6000,
        "min_duration_s": 30, "max_duration_s": 120,
        "audio_codec": "aac",
        "min_audio_bitrate_kbps": 192,
    },
    "weixin_video": {
        "name": "WeChat Channels (视频号)",
        "orientation": "vertical",
        "width": 2160, "height": 3840,
        "codec": "h264",
        "min_bitrate_kbps": 6000,
        "min_duration_s": 30, "max_duration_s": 120,
        "audio_codec": "aac",
        "min_audio_bitrate_kbps": 192,
    },
}


def probe_video(filepath: str) -> dict:
    """Run ffprobe and return stream info."""
    if not os.path.isfile(filepath):
        return {"error": f"File not found: {filepath}"}

    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        filepath
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout) if result.stdout else {"error": "ffprobe returned empty"}
    except FileNotFoundError:
        return {"error": "ffprobe not found — install ffmpeg"}
    except subprocess.TimeoutExpired:
        return {"error": "ffprobe timed out"}
    except json.JSONDecodeError:
        return {"error": "ffprobe output not valid JSON"}


def extract_info(probe_data: dict) -> dict:
    """Extract relevant info from ffprobe output."""
    if "error" in probe_data:
        return probe_data

    info = {"video": None, "audio": None, "duration": 0}

    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video" and info["video"] is None:
            info["video"] = {
                "width": int(stream.get("width", 0)),
                "height": int(stream.get("height", 0)),
                "codec": stream.get("codec_name", "unknown"),
                "bitrate_kbps": int(stream.get("bit_rate", 0)) // 1000,
            }
        elif stream.get("codec_type") == "audio" and info["audio"] is None:
            info["audio"] = {
                "codec": stream.get("codec_name", "unknown"),
                "bitrate_kbps": int(stream.get("bit_rate", 0)) // 1000,
                "sample_rate": int(stream.get("sample_rate", 0)),
            }

    fmt = probe_data.get("format", {})
    info["duration"] = float(fmt.get("duration", 0))
    info["size_mb"] = int(fmt.get("size", 0)) / (1024 * 1024)

    # Fallback: get bitrate from format if not in stream
    if info["video"] and info["video"]["bitrate_kbps"] == 0:
        info["video"]["bitrate_kbps"] = int(fmt.get("bit_rate", 0)) // 1000

    return info


def check_platform(info: dict, platform: str) -> dict:
    """Check video info against platform specs."""
    spec = PLATFORM_SPECS.get(platform)
    if not spec:
        return {"platform": platform, "error": f"Unknown platform: {platform}"}

    results = {"platform": spec["name"], "checks": [], "passed": True}

    if "error" in info:
        results["checks"].append({"name": "probe", "status": "FAIL", "detail": info["error"]})
        results["passed"] = False
        return results

    v = info.get("video")
    a = info.get("audio")

    # Resolution check
    if v:
        if v["width"] == spec["width"] and v["height"] == spec["height"]:
            results["checks"].append({
                "name": "resolution",
                "status": "PASS",
                "detail": f"{v['width']}x{v['height']}"
            })
        else:
            results["checks"].append({
                "name": "resolution",
                "status": "FAIL",
                "detail": f"{v['width']}x{v['height']} (expected {spec['width']}x{spec['height']})"
            })
            results["passed"] = False

        # Codec check
        if v["codec"] == spec["codec"]:
            results["checks"].append({"name": "codec", "status": "PASS", "detail": v["codec"]})
        else:
            results["checks"].append({
                "name": "codec", "status": "FAIL",
                "detail": f"{v['codec']} (expected {spec['codec']})"
            })
            results["passed"] = False

        # Bitrate check
        if v["bitrate_kbps"] >= spec["min_bitrate_kbps"]:
            results["checks"].append({
                "name": "video_bitrate",
                "status": "PASS",
                "detail": f"{v['bitrate_kbps']}kbps (>= {spec['min_bitrate_kbps']})"
            })
        else:
            results["checks"].append({
                "name": "video_bitrate",
                "status": "WARN",
                "detail": f"{v['bitrate_kbps']}kbps (< {spec['min_bitrate_kbps']})"
            })
    else:
        results["checks"].append({"name": "video_stream", "status": "FAIL", "detail": "No video stream"})
        results["passed"] = False

    # Duration check
    dur = info.get("duration", 0)
    if spec["min_duration_s"] <= dur <= spec["max_duration_s"]:
        results["checks"].append({
            "name": "duration",
            "status": "PASS",
            "detail": f"{dur:.1f}s ({dur/60:.1f}min)"
        })
    else:
        results["checks"].append({
            "name": "duration",
            "status": "WARN",
            "detail": f"{dur:.1f}s (target: {spec['min_duration_s']}-{spec['max_duration_s']}s)"
        })

    # Audio check
    if a:
        if a["codec"] == spec["audio_codec"]:
            results["checks"].append({"name": "audio_codec", "status": "PASS", "detail": a["codec"]})
        else:
            results["checks"].append({
                "name": "audio_codec", "status": "WARN",
                "detail": f"{a['codec']} (expected {spec['audio_codec']})"
            })

        if a["bitrate_kbps"] >= spec["min_audio_bitrate_kbps"]:
            results["checks"].append({
                "name": "audio_bitrate",
                "status": "PASS",
                "detail": f"{a['bitrate_kbps']}kbps"
            })
        else:
            results["checks"].append({
                "name": "audio_bitrate",
                "status": "WARN",
                "detail": f"{a['bitrate_kbps']}kbps (< {spec['min_audio_bitrate_kbps']})"
            })

    return results


def main():
    parser = argparse.ArgumentParser(description="Validate video against platform specs")
    parser.add_argument("video", help="Path to video file")
    parser.add_argument("--platforms", default="bilibili,youtube",
                       help="Comma-separated platform list")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    probe_data = probe_video(args.video)
    info = extract_info(probe_data)

    platforms = [p.strip() for p in args.platforms.split(",")]
    all_results = []

    for platform in platforms:
        result = check_platform(info, platform)
        all_results.append(result)

    if args.json:
        print(json.dumps(all_results, indent=2, ensure_ascii=False))
    else:
        all_passed = True
        for r in all_results:
            print(f"\n{'='*50}")
            print(f"Platform: {r['platform']}")
            print(f"{'='*50}")
            for c in r.get("checks", []):
                icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}.get(c["status"], "?")
                print(f"  {icon} {c['name']}: {c['detail']}")
            if not r["passed"]:
                all_passed = False
                print(f"  ❌ OVERALL: FAIL")
            else:
                print(f"  ✅ OVERALL: PASS")

        if info.get("duration"):
            print(f"\nFile: {args.video}")
            print(f"Duration: {info['duration']:.1f}s ({info['duration']/60:.1f}min)")
            print(f"Size: {info.get('size_mb', 0):.1f}MB")

        sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
