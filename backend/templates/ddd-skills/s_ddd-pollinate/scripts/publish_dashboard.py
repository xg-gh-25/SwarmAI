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
    """Generate Pollinate Studio HTML — interactive review + publish page."""
    import base64

    topic = strategy.get("message", os.path.basename(content_dir))
    abs_dir = os.path.abspath(content_dir)

    # Load publish kit if exists
    publish_kit = {"title": "", "body": "", "tags": ""}
    for kit_file in [
        "deliver/xiaohongshu/publish-kit.md",
        "deliver/xiaohongshu-publish-kit.md",
        "xiaohongshu-publish-kit.md",
    ]:
        kit_path = os.path.join(content_dir, kit_file)
        if os.path.isfile(kit_path):
            with open(kit_path, "r", encoding="utf-8") as f:
                kit_text = f.read()
            # Parse sections — handle multiple publish-kit formats
            import re
            # Format A: "## 标题\n...", "## 正文\n...", "## 标签\n..."
            # Format B: "### Title\n...", "### 正文\n...", "### 标签\n..."
            # Format C: "### 标题\n...", "### 正文\n...", "## Tags\n..."
            title_m = (re.search(r"##+ (?:标题|Title)[^\n]*\n(.+?)(?=\n##|\Z)", kit_text, re.DOTALL)
                       or re.search(r"(?:标题|Title)\n(.+?)(?=\n##|\n###|\Z)", kit_text, re.DOTALL))
            body_m = (re.search(r"##+ 正文\n(.+?)(?=\n##+ (?:标签|Tags)|\Z)", kit_text, re.DOTALL)
                      or re.search(r"正文\n(.+?)(?=\n##+ (?:标签|Tags|发布)|\Z)", kit_text, re.DOTALL))
            tags_m = (re.search(r"##+ (?:标签|Tags)\n(.+?)(?=\n##|\Z)", kit_text, re.DOTALL)
                      or re.search(r"(#\w+(?:\s+#\w+)+)", kit_text))
            if title_m:
                publish_kit["title"] = title_m.group(1).strip()
            if body_m:
                publish_kit["body"] = body_m.group(1).strip()
            if tags_m:
                publish_kit["tags"] = tags_m.group(1).strip()
            break

    # Find poster image — search broadly across common paths and names
    poster_html = '<div class="placeholder">无封面图</div>'
    poster_path = ""
    # Also search deliver/{channel}/ for copied posters
    poster_candidates = [
        "tracks/poster/cover_3x4.png", "tracks/poster/poster_3x4.png",
        "tracks/poster/cover.png", "poster_3x4.png",
        "tracks/poster/poster-xiaohongshu-3x4.png",
        "tracks/poster/poster-cover.png", "tracks/poster/poster-matrix.png",
        "deliver/xiaohongshu/cover_3x4.png", "deliver/xiaohongshu/poster-cover.png",
        "deliver/xiaohongshu/poster-matrix.png",
    ]
    # Also glob for any PNG in poster/ and deliver/xiaohongshu/
    import glob
    for pattern in ["tracks/poster/*.png", "deliver/xiaohongshu/*.png"]:
        for p in glob.glob(os.path.join(content_dir, pattern)):
            rel = os.path.relpath(p, content_dir)
            if rel not in poster_candidates:
                poster_candidates.append(rel)
    for candidate in poster_candidates:
        p = os.path.join(content_dir, candidate)
        if os.path.isfile(p):
            poster_path = p
            try:
                with open(p, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                poster_html = f'<img src="data:image/png;base64,{b64}" class="poster-img" alt="Cover">'
            except Exception:
                poster_html = f'<img src="file://{os.path.abspath(p)}" class="poster-img" alt="Cover">'
            break

    # Find video
    video_html = ""
    video_path = ""
    for candidate in [
        "tracks/video/final_video.mp4", "video/final_video.mp4",
    ]:
        p = os.path.join(content_dir, candidate)
        if os.path.isfile(p):
            video_path = os.path.abspath(p)
            video_html = f'''
            <div class="section">
              <h2>🎬 视频预览</h2>
              <video controls width="320" style="border-radius:12px; max-height:568px;">
                <source src="file://{video_path}" type="video/mp4">
              </video>
              <div style="margin-top:8px;">
                <button class="btn" onclick="openInFinder('{video_path}')">📂 Open in Finder</button>
              </div>
            </div>'''
            break

    # Escape for JS strings
    def js_escape(s):
        return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "")

    title_js = js_escape(publish_kit["title"])
    body_js = js_escape(publish_kit["body"])
    tags_js = js_escape(publish_kit["tags"])
    # Plain text body (strip markdown bold)
    body_plain = publish_kit["body"].replace("**", "")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<title>🐝 Pollinate Studio — {topic}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, 'PingFang SC', 'Noto Sans SC', sans-serif; background: #0f0f1a; color: #e0e0e0; min-height: 100vh; }}
.header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding: 24px 32px; border-bottom: 2px solid #FF6B35; }}
.header h1 {{ font-size: 22px; color: #FF6B35; }} .header .meta {{ color: #888; font-size: 13px; margin-top: 4px; }}
.container {{ max-width: 1100px; margin: 0 auto; padding: 24px; display: grid; grid-template-columns: 320px 1fr; gap: 24px; }}
.section {{ background: #1a1a2e; border-radius: 16px; padding: 24px; margin-bottom: 16px; }}
.section h2 {{ font-size: 16px; color: #FF6B35; margin-bottom: 12px; }}
.poster-img {{ width: 100%; border-radius: 12px; }}
.placeholder {{ width: 100%; height: 400px; background: #252540; border-radius: 12px; display: flex; align-items: center; justify-content: center; color: #555; }}
textarea {{ width: 100%; background: #12122a; color: #e0e0e0; border: 1px solid #333; border-radius: 8px; padding: 12px; font-size: 14px; font-family: inherit; resize: vertical; }}
.btn {{ background: #FF6B35; color: #fff; border: none; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; margin-right: 8px; margin-top: 8px; }}
.btn:hover {{ background: #e55a2b; }}
.btn-outline {{ background: transparent; border: 1px solid #FF6B35; color: #FF6B35; }}
.btn-outline:hover {{ background: rgba(255,107,53,0.15); }}
.btn-green {{ background: #00B894; }} .btn-green:hover {{ background: #00a383; }}
.copy-ok {{ color: #00B894; font-size: 12px; margin-left: 8px; opacity: 0; transition: opacity 0.3s; }}
.copy-ok.show {{ opacity: 1; }}
.tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }}
.tag {{ background: #252540; color: #FF6B35; padding: 4px 10px; border-radius: 12px; font-size: 12px; }}
.publish-actions {{ margin-top: 16px; padding-top: 16px; border-top: 1px solid #333; }}
.status {{ display: inline-block; padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; }}
.status-draft {{ background: #333; color: #888; }}
.status-ready {{ background: rgba(255,107,53,0.2); color: #FF6B35; }}
@media (max-width: 768px) {{ .container {{ grid-template-columns: 1fr; }} }}
</style></head><body>

<div class="header">
  <h1>🐝 Pollinate Studio</h1>
  <div class="meta">{topic} · {strategy.get('desired_outcome', '')}</div>
</div>

<div class="container">
  <!-- Left: Poster Preview -->
  <div>
    <div class="section">
      <h2>🖼️ 封面</h2>
      {poster_html}
      <div style="margin-top:8px;">
        <button class="btn btn-outline" onclick="openInFinder('{os.path.abspath(poster_path) if poster_path else ''}')">📂 Open File</button>
      </div>
    </div>
    {video_html}
  </div>

  <!-- Right: Text + Publish -->
  <div>
    <div class="section">
      <h2>📝 标题</h2>
      <textarea id="title" rows="2">{publish_kit['title']}</textarea>
      <button class="btn" onclick="copyField('title')">📋 Copy Title</button>
      <span class="copy-ok" id="title-ok">✅ Copied</span>
    </div>

    <div class="section">
      <h2>📝 正文</h2>
      <textarea id="body" rows="12">{body_plain}</textarea>
      <button class="btn" onclick="copyField('body')">📋 Copy Body</button>
      <span class="copy-ok" id="body-ok">✅ Copied</span>
    </div>

    <div class="section">
      <h2>🏷️ 标签</h2>
      <div class="tags" id="tag-display"></div>
      <button class="btn" onclick="copyTags()" style="margin-top:12px;">📋 Copy All Tags</button>
      <span class="copy-ok" id="tags-ok">✅ Copied</span>
    </div>

    <div class="section">
      <h2>🚀 发布到小红书</h2>
      <div>
        <span class="status status-ready">Ready to Publish</span>
      </div>
      <div class="publish-actions">
        <button class="btn" onclick="copyAll()">📋 Copy All (Title + Body + Tags)</button>
        <span class="copy-ok" id="all-ok">✅ Copied</span>
        <br>
        <button class="btn btn-green" onclick="window.open('https://creator.xiaohongshu.com/publish/publish','_blank')">
          🔗 Open XHS Creator
        </button>
        <button class="btn btn-outline" onclick="openInFinder('{abs_dir}/deliver')">📂 Open Deliver Folder</button>
      </div>
    </div>
  </div>
</div>

<script>
const TAGS = '{tags_js}'.split(/\\s+/).filter(t => t.startsWith('#'));
const tagDisplay = document.getElementById('tag-display');
TAGS.forEach(t => {{
  const el = document.createElement('span');
  el.className = 'tag';
  el.textContent = t;
  tagDisplay.appendChild(el);
}});

function copyField(id) {{
  const el = document.getElementById(id);
  navigator.clipboard.writeText(el.value);
  flash(id + '-ok');
}}
function copyTags() {{
  navigator.clipboard.writeText(TAGS.join(' '));
  flash('tags-ok');
}}
function copyAll() {{
  const title = document.getElementById('title').value;
  const body = document.getElementById('body').value;
  const tags = TAGS.join(' ');
  navigator.clipboard.writeText(title + '\\n\\n' + body + '\\n\\n' + tags);
  flash('all-ok');
}}
function flash(id) {{
  const el = document.getElementById(id);
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2000);
}}
function openInFinder(path) {{
  // file:// URL for local files — works when opened from file://
  if (path) window.open('file://' + path);
}}
</script>
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
