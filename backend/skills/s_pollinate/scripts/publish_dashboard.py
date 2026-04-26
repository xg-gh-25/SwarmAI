#!/usr/bin/env python3
"""Generate a publish dashboard for a Pollinate content run.

Scans the deliver/ directory and produces a markdown table of all
publish-ready assets with file paths, platforms, formats, and status.

Usage:
    python publish_dashboard.py content/pollinate-v2-launch/
    python publish_dashboard.py content/pollinate-v2-launch/ --open
    python publish_dashboard.py content/pollinate-v2-launch/ --html
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import webbrowser


CHANNEL_LABELS = {
    "xiaohongshu": "📕 小红书",
    "bilibili": "📺 B站",
    "youtube": "▶️ YouTube",
    "douyin": "🎵 抖音",
    "weixin_video": "💬 视频号",
    "gongzhonghao": "📝 公众号",
    "github": "🐙 GitHub",
    "zhihu": "📘 知乎",
}

FORMAT_LABELS = {
    ".png": "图片",
    ".jpg": "图片",
    ".mp4": "视频",
    ".wav": "音频",
    ".md": "文档",
    ".txt": "文案",
    ".html": "网页",
    ".json": "数据",
}


def scan_deliver(content_dir: str) -> list:
    """Scan deliver/ directory for publish-ready assets."""
    deliver_dir = os.path.join(content_dir, "deliver")
    if not os.path.isdir(deliver_dir):
        # Fallback: check tracks/ for assets
        deliver_dir = os.path.join(content_dir, "tracks")
        if not os.path.isdir(deliver_dir):
            return []

    assets = []
    for channel in sorted(os.listdir(deliver_dir)):
        channel_dir = os.path.join(deliver_dir, channel)
        if not os.path.isdir(channel_dir):
            continue

        channel_label = CHANNEL_LABELS.get(channel, channel)

        for fname in sorted(os.listdir(channel_dir)):
            fpath = os.path.join(channel_dir, fname)
            if not os.path.isfile(fpath):
                continue

            ext = os.path.splitext(fname)[1].lower()
            fmt = FORMAT_LABELS.get(ext, ext)
            size = os.path.getsize(fpath)
            size_str = f"{size / 1024:.0f}KB" if size < 1024 * 1024 else f"{size / (1024*1024):.1f}MB"

            # Determine content description from filename
            desc = fname
            if "poster" in fname:
                desc = "海报 " + ("3:4" if "3x4" in fname else "16:9" if "16x9" in fname else "")
            elif "caption" in fname:
                desc = "发布文案"
            elif "dynamic" in fname:
                desc = "动态文案"
            elif "narrative" in fname and "full" in fname:
                desc = "长文 (完整版)"
            elif "narrative" in fname:
                desc = "长文摘要"
            elif "readme" in fname:
                desc = "README section"
            elif "qr" in fname:
                desc = "QR码 " + ("小红书" if "xhs" in fname else "GitHub" if "github" in fname else "")
            elif fname.endswith(".mp4"):
                desc = "视频"
            elif fname.endswith(".srt"):
                desc = "字幕"

            assets.append({
                "channel": channel,
                "channel_label": channel_label,
                "file": fname,
                "path": fpath,
                "format": fmt,
                "size": size_str,
                "description": desc,
            })

    return assets


def load_strategy(content_dir: str) -> dict:
    """Load strategy.json for context."""
    strategy_path = os.path.join(content_dir, "strategy.json")
    if os.path.isfile(strategy_path):
        with open(strategy_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def generate_markdown(content_dir: str, assets: list, strategy: dict) -> str:
    """Generate markdown publish dashboard."""
    lines = []
    topic = strategy.get("message", os.path.basename(content_dir))

    lines.append(f"# 📋 Publish Dashboard")
    lines.append(f"")
    lines.append(f"**Topic:** {topic}")
    lines.append(f"**Date:** {strategy.get('created_at', 'N/A')[:10] if strategy.get('created_at') else 'today'}")
    lines.append(f"**Outcome:** {strategy.get('desired_outcome', 'N/A')}")
    lines.append(f"")

    # Group by channel
    channels = {}
    for a in assets:
        ch = a["channel"]
        if ch not in channels:
            channels[ch] = []
        channels[ch].append(a)

    # Table
    lines.append(f"| 渠道 | 内容 | 格式 | 大小 | 文件 |")
    lines.append(f"|------|------|------|------|------|")

    for ch in channels:
        for i, a in enumerate(channels[ch]):
            ch_label = a["channel_label"] if i == 0 else ""
            lines.append(f"| {ch_label} | {a['description']} | {a['format']} | {a['size']} | `{a['file']}` |")

    # Action items
    lines.append(f"")
    lines.append(f"## 📌 发布操作")
    lines.append(f"")

    for ch_key, ch_assets in channels.items():
        label = CHANNEL_LABELS.get(ch_key, ch_key)
        lines.append(f"### {label}")

        # Find the main content (poster/video first, not QR) + caption
        main_asset = None
        caption = None
        for a in ch_assets:
            if a["format"] in ("图片", "视频") and "QR" not in a["description"]:
                main_asset = a
            if "文案" in a["description"]:
                caption = a

        if ch_key == "xiaohongshu":
            lines.append(f"1. AirDrop `{main_asset['file']}` 到手机" if main_asset else "1. (无主图)")
            lines.append(f"2. 复制文案 → 粘贴到小红书")
            lines.append(f"3. 发布")
        elif ch_key == "bilibili":
            lines.append(f"1. 上传 `{main_asset['file']}` 到 B站动态" if main_asset else "1. (无主图)")
            lines.append(f"2. 粘贴动态文案")
            lines.append(f"3. 发布")
        elif ch_key == "gongzhonghao":
            lines.append(f"1. 打开公众号后台 → 新建图文")
            lines.append(f"2. 导入 `{ch_assets[0]['file']}`")
            lines.append(f"3. 发布")
        elif ch_key == "github":
            lines.append(f"1. 复制 `{ch_assets[0]['file']}` 内容到 README.md")
            lines.append(f"2. Commit + push")
        else:
            lines.append(f"1. 上传内容到 {label}")

        lines.append(f"")

    # File locations
    lines.append(f"## 📁 文件位置")
    lines.append(f"```")
    lines.append(f"{os.path.join(content_dir, 'deliver')}/")
    for ch in channels:
        lines.append(f"  {ch}/")
        for a in channels[ch]:
            lines.append(f"    {a['file']} ({a['size']})")
    lines.append(f"```")

    return "\n".join(lines)


def generate_html(content_dir: str, assets: list, strategy: dict) -> str:
    """Generate HTML publish dashboard for browser viewing."""
    topic = strategy.get("message", os.path.basename(content_dir))

    channels = {}
    for a in assets:
        ch = a["channel"]
        if ch not in channels:
            channels[ch] = []
        channels[ch].append(a)

    rows = ""
    for ch in channels:
        for i, a in enumerate(channels[ch]):
            ch_label = a["channel_label"] if i == 0 else ""
            rowspan = f' rowspan="{len(channels[ch])}"' if i == 0 else ""
            ch_cell = f'<td{rowspan} style="font-size:16px;font-weight:600;vertical-align:top;">{ch_label}</td>' if i == 0 else ""
            rows += f'<tr>{ch_cell}<td>{a["description"]}</td><td>{a["format"]}</td><td>{a["size"]}</td><td><code>{a["file"]}</code></td></tr>\n'

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Publish Dashboard</title>
<style>
body {{ font-family: -apple-system, 'PingFang SC', sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #fafafa; }}
h1 {{ font-size: 24px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
th {{ background: #1a1a2e; color: #fff; padding: 12px 16px; text-align: left; font-size: 13px; }}
td {{ padding: 10px 16px; border-bottom: 1px solid #eee; font-size: 14px; }}
code {{ background: #f0f0f0; padding: 2px 6px; border-radius: 4px; font-size: 12px; }}
.meta {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
</style></head><body>
<h1>📋 Publish Dashboard</h1>
<div class="meta"><strong>Topic:</strong> {topic}<br><strong>Outcome:</strong> {strategy.get('desired_outcome', 'N/A')}</div>
<table>
<tr><th>渠道</th><th>内容</th><th>格式</th><th>大小</th><th>文件</th></tr>
{rows}
</table>
</body></html>"""


def main():
    parser = argparse.ArgumentParser(description="Pollinate publish dashboard")
    parser.add_argument("content_dir", help="Content directory path")
    parser.add_argument("--open", action="store_true", help="Open deliver/ folder in Finder")
    parser.add_argument("--html", action="store_true", help="Generate HTML dashboard and open in browser")
    parser.add_argument("--output", help="Save dashboard to file (default: print to stdout)")
    args = parser.parse_args()

    content_dir = args.content_dir.rstrip("/")
    if not os.path.isdir(content_dir):
        print(f"Error: {content_dir} not found", file=sys.stderr)
        sys.exit(1)

    assets = scan_deliver(content_dir)
    strategy = load_strategy(content_dir)

    if not assets:
        print(f"No publish-ready assets found in {content_dir}/deliver/")
        sys.exit(1)

    if args.html:
        html = generate_html(content_dir, assets, strategy)
        html_path = os.path.join(content_dir, "publish_dashboard.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Dashboard: {html_path}")
        webbrowser.open(f"file://{os.path.abspath(html_path)}")
    else:
        md = generate_markdown(content_dir, assets, strategy)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"Dashboard saved: {args.output}")
        else:
            print(md)

    if args.open:
        deliver_dir = os.path.join(content_dir, "deliver")
        if os.path.isdir(deliver_dir):
            if platform.system() == "Darwin":
                subprocess.run(["open", deliver_dir])
            elif platform.system() == "Windows":
                subprocess.run(["explorer", deliver_dir])
            else:
                subprocess.run(["xdg-open", deliver_dir])


if __name__ == "__main__":
    main()
