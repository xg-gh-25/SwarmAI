#!/usr/bin/env python3
"""Generate platform-specific publish metadata templates for content.

Reads content_package.md + timing.json and produces publish_info.md
with per-platform sections containing title constraints, description ranges,
tag formats, chapter timestamps, and CTAs. Title/description/tag fields are
left as agent-fillable placeholders — the agent completes them with
platform-optimized copy in a subsequent step.

Usage:
    python publish_meta.py content/memory-is-the-moat/ --platforms bilibili,youtube,xiaohongshu
"""
import argparse
import json
import os
import re
from datetime import datetime

try:
    import yaml
except ImportError:
    yaml = None


ACCOUNTS_PATH = os.path.expanduser("~/.swarm-ai/pollinate-accounts.yaml")


def load_accounts() -> dict:
    """Load channel account identities from private config."""
    if not os.path.isfile(ACCOUNTS_PATH):
        return {}
    if yaml is None:
        # Fallback: basic YAML parsing for simple key-value config
        accounts = {}
        with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
            current_platform = None
            for line in f:
                line = line.rstrip()
                if not line or line.startswith("#"):
                    continue
                if not line.startswith(" ") and line.endswith(":"):
                    current_platform = line[:-1].strip()
                    accounts[current_platform] = {}
                elif current_platform and ":" in line:
                    key, val = line.strip().split(":", 1)
                    accounts[current_platform][key.strip()] = val.strip().strip('"\'')
        return accounts
    with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


PLATFORM_CONFIGS = {
    "bilibili": {
        "name": "Bilibili (B站)",
        "title_max": 80,
        "title_formula": "number + topic + hook",
        "desc_min": 100, "desc_max": 200,
        "tag_count": 10,
        "tag_format": "plain",  # just words
        "chapters": True,
        "chapter_format": "MM:SS",
        "cta": "一键三连 (点赞、投币、收藏)",
        "language": "zh-CN",
    },
    "youtube": {
        "name": "YouTube",
        "title_max": 70,
        "title_formula": "SEO-optimized",
        "desc_min": 100, "desc_max": 500,
        "tag_count": 15,
        "tag_format": "plain+hashtags",
        "chapters": True,
        "chapter_format": "0:00",  # must start at 0:00
        "cta": "Like, Subscribe & Share",
        "language": "en-US",
    },
    "xiaohongshu": {
        "name": "Xiaohongshu (小红书)",
        "title_max": 20,
        "title_formula": "punchy + emoji",
        "desc_min": 200, "desc_max": 500,
        "tag_count": 8,
        "tag_format": "#tag#",  # double hash
        "chapters": False,
        "cta": "点赞收藏加关注",
        "language": "zh-CN",
    },
    "douyin": {
        "name": "Douyin (抖音)",
        "title_max": 55,
        "title_formula": "short + casual",
        "desc_min": 100, "desc_max": 200,
        "tag_count": 6,
        "tag_format": "#tag",  # single hash
        "chapters": False,
        "cta": "点赞关注",
        "language": "zh-CN",
    },
    "weixin_video": {
        "name": "WeChat Channels (视频号)",
        "title_max": 55,
        "title_formula": "knowledge-sharing + forwarding-friendly",
        "desc_min": 100, "desc_max": 300,
        "tag_count": 6,
        "tag_format": "#tag",  # single hash
        "chapters": False,
        "cta": "点赞关注，转发给朋友",
        "language": "zh-CN",
    },
}


def load_timing(content_dir: str) -> dict:
    """Load timing.json for chapter generation."""
    timing_path = os.path.join(content_dir, "video", "timing.json")
    if os.path.isfile(timing_path):
        with open(timing_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def format_timestamp(seconds: float, fmt: str = "MM:SS") -> str:
    """Format seconds to timestamp string."""
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if fmt == "0:00":
        return f"{mins}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def generate_chapters(timing: dict, fmt: str = "MM:SS") -> list:
    """Generate chapter timestamps from timing.json."""
    chapters = []
    for section in timing.get("sections", []):
        if section.get("is_silent"):
            continue
        label = section.get("label", section.get("name", "Section"))
        ts = format_timestamp(section.get("start_time", 0), fmt)
        chapters.append(f"{ts} {label}")
    return chapters


def generate_publish_info(content_dir: str, platforms: list) -> str:
    """Generate publish_info.md content."""
    timing = load_timing(content_dir)
    total_duration = timing.get("total_duration", 0)
    accounts = load_accounts()

    # Read content_package.md for title/thesis
    cp_path = os.path.join(content_dir, "content_package.md")
    title = "Untitled"
    thesis = ""
    if os.path.isfile(cp_path):
        with open(cp_path, "r", encoding="utf-8") as f:
            cp_text = f.read()
            # Extract title from first # heading
            m = re.search(r'^#\s+(.+)$', cp_text, re.MULTILINE)
            if m:
                title = m.group(1).strip()
            # Extract thesis
            m = re.search(r'##\s+Core Thesis\n(.+?)(?:\n##|\Z)', cp_text, re.DOTALL)
            if m:
                thesis = m.group(1).strip()

    lines = [
        f"# Publish Info: {title}",
        f"",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Duration: {total_duration:.0f}s ({total_duration/60:.1f}min)",
        f"",
    ]

    for platform_key in platforms:
        config = PLATFORM_CONFIGS.get(platform_key)
        if not config:
            continue

        lines.append(f"---")
        lines.append(f"## {config['name']}")
        lines.append(f"")

        # Account identity
        acct = accounts.get(platform_key, {})
        if acct.get("enabled", True) and acct.get("name"):
            acct_name = acct["name"]
            acct_url = acct.get("url", "")
            acct_id = acct.get("uid") or acct.get("channel_id") or ""
            lines.append(f"**Account:** {acct_name}" + (f" ({acct_id})" if acct_id else ""))
            if acct_url:
                lines.append(f"**URL:** {acct_url}")
            lines.append(f"")
        elif acct.get("enabled") is False:
            lines.append(f"**Status:** DISABLED — skipping this platform")
            lines.append(f"")
            continue

        # Title placeholder (agent fills with platform-optimized version)
        lines.append(f"### Title (max {config['title_max']} chars)")
        lines.append(f"Formula: {config['title_formula']}")
        lines.append(f"**[AGENT: Generate platform-optimized title here]**")
        lines.append(f"")

        # Description
        lines.append(f"### Description ({config['desc_min']}-{config['desc_max']} chars)")
        if thesis:
            lines.append(f"Core thesis: {thesis}")
        lines.append(f"**[AGENT: Generate platform-optimized description here]**")
        lines.append(f"")

        # Tags
        lines.append(f"### Tags ({config['tag_count']} tags, format: `{config['tag_format']}`)")
        lines.append(f"**[AGENT: Generate {config['tag_count']} relevant tags]**")
        lines.append(f"")

        # Chapters (if supported)
        if config.get("chapters") and timing:
            chapters = generate_chapters(timing, config.get("chapter_format", "MM:SS"))
            if chapters:
                lines.append(f"### Chapters")
                for ch in chapters:
                    lines.append(f"- {ch}")
                lines.append(f"")

        # CTA
        lines.append(f"### CTA")
        lines.append(f"{config['cta']}")
        lines.append(f"")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate platform publish metadata")
    parser.add_argument("content_dir", help="Path to content directory")
    parser.add_argument("--platforms", default="bilibili,youtube,xiaohongshu,douyin,weixin_video",
                       help="Comma-separated platform list")
    parser.add_argument("--output", help="Output file (default: content_dir/video/publish_info.md)")
    args = parser.parse_args()

    platforms = [p.strip() for p in args.platforms.split(",")]
    publish_info = generate_publish_info(args.content_dir, platforms)

    output_path = args.output or os.path.join(args.content_dir, "video", "publish_info.md")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(publish_info)

    print(f"Publish info saved to: {output_path}")
    print(f"Platforms: {', '.join(platforms)}")


if __name__ == "__main__":
    main()
