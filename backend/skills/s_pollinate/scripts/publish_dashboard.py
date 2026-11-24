#!/usr/bin/env python3
"""Generate a publish dashboard for a Pollinate content run.

Scans the deliver/ directory and produces a markdown table of all
publish-ready assets with file paths, platforms, formats, and status.

Usage:
    python publish_dashboard.py content/<topic>/
    python publish_dashboard.py content/<topic>/ --open
    python publish_dashboard.py content/<topic>/ --html
    python publish_dashboard.py content/<topic>/ --lang zh   # Chinese dashboard

The dashboard text is English by default; ``--lang zh`` renders it in Chinese.
Platform publish kits are parsed in either language.
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import webbrowser


LANGS = ("en", "zh")

CHANNEL_LABELS = {
    "en": {
        "xiaohongshu": "📕 Xiaohongshu",
        "bilibili": "📺 Bilibili",
        "youtube": "▶️ YouTube",
        "douyin": "🎵 Douyin",
        "weixin_video": "💬 WeChat Channels",
        "gongzhonghao": "📝 WeChat Official Account",
        "github": "🐙 GitHub",
        "zhihu": "📘 Zhihu",
    },
    "zh": {
        "xiaohongshu": "📕 小红书",
        "bilibili": "📺 B站",
        "youtube": "▶️ YouTube",
        "douyin": "🎵 抖音",
        "weixin_video": "💬 视频号",
        "gongzhonghao": "📝 公众号",
        "github": "🐙 GitHub",
        "zhihu": "📘 知乎",
    },
}

# File extension -> format kind (language-neutral; the label comes from STRINGS).
FORMAT_KINDS = {
    ".png": "image",
    ".jpg": "image",
    ".mp4": "video",
    ".wav": "audio",
    ".md": "doc",
    ".txt": "copy",
    ".html": "web",
    ".json": "data",
}

STRINGS = {
    "en": {
        "fmt.image": "Image", "fmt.video": "Video", "fmt.audio": "Audio", "fmt.doc": "Document",
        "fmt.copy": "Copy", "fmt.web": "Web page", "fmt.data": "Data",
        "role.poster": "Poster", "role.caption": "Post caption", "role.dynamic": "Feed post copy",
        "role.narrative_full": "Article (full)", "role.narrative": "Article summary",
        "role.readme": "README section", "role.qr": "QR code", "role.video": "Video",
        "role.subtitles": "Subtitles",
        "md.table": "| Channel | Content | Format | Size | File |",
        "md.actions": "## 📌 How to publish",
        "md.files": "## 📁 File locations",
        "md.no_main": "1. (no main image)",
        "xhs.1": "1. AirDrop `{file}` to your phone", "xhs.2": "2. Copy the caption → paste it into Xiaohongshu",
        "bili.1": "1. Upload `{file}` to a Bilibili feed post", "bili.2": "2. Paste the feed post copy",
        "gzh.1": "1. Open the Official Account console → new article", "gzh.2": "2. Import `{file}`",
        "gh.1": "1. Copy the content of `{file}` into README.md", "gh.2": "2. Commit + push",
        "publish": "3. Publish", "other": "1. Upload the content to {label}",
        "html.lang": "en", "html.no_cover": "No cover image", "html.video": "🎬 Video preview",
        "html.cover": "🖼️ Cover", "html.title": "📝 Title", "html.body": "📝 Body", "html.tags": "🏷️ Tags",
        "html.publish_xhs": "🚀 Publish to Xiaohongshu",
    },
    "zh": {
        "fmt.image": "图片", "fmt.video": "视频", "fmt.audio": "音频", "fmt.doc": "文档",
        "fmt.copy": "文案", "fmt.web": "网页", "fmt.data": "数据",
        "role.poster": "海报", "role.caption": "发布文案", "role.dynamic": "动态文案",
        "role.narrative_full": "长文 (完整版)", "role.narrative": "长文摘要",
        "role.readme": "README section", "role.qr": "QR码", "role.video": "视频",
        "role.subtitles": "字幕",
        "md.table": "| 渠道 | 内容 | 格式 | 大小 | 文件 |",
        "md.actions": "## 📌 发布操作",
        "md.files": "## 📁 文件位置",
        "md.no_main": "1. (无主图)",
        "xhs.1": "1. AirDrop `{file}` 到手机", "xhs.2": "2. 复制文案 → 粘贴到小红书",
        "bili.1": "1. 上传 `{file}` 到 B站动态", "bili.2": "2. 粘贴动态文案",
        "gzh.1": "1. 打开公众号后台 → 新建图文", "gzh.2": "2. 导入 `{file}`",
        "gh.1": "1. 复制 `{file}` 内容到 README.md", "gh.2": "2. Commit + push",
        "publish": "3. 发布", "other": "1. 上传内容到 {label}",
        "html.lang": "zh-CN", "html.no_cover": "无封面图", "html.video": "🎬 视频预览",
        "html.cover": "🖼️ 封面", "html.title": "📝 标题", "html.body": "📝 正文", "html.tags": "🏷️ 标签",
        "html.publish_xhs": "🚀 发布到小红书",
    },
}


def _t(lang: str) -> dict:
    """The dashboard strings for ``lang`` (English for an unknown language)."""
    return STRINGS.get(lang, STRINGS["en"])


def _role(fname: str):
    """What a deliver/ file is, from its name (language-neutral)."""
    if "poster" in fname:
        return "poster"
    if "caption" in fname:
        return "caption"
    if "dynamic" in fname:
        return "dynamic"
    if "narrative" in fname and "full" in fname:
        return "narrative_full"
    if "narrative" in fname:
        return "narrative"
    if "readme" in fname:
        return "readme"
    if "qr" in fname:
        return "qr"
    if fname.endswith(".mp4"):
        return "video"
    if fname.endswith(".srt"):
        return "subtitles"
    return None


def scan_deliver(content_dir: str, lang: str = "en") -> list:
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

        t = _t(lang)
        channel_label = CHANNEL_LABELS.get(lang, CHANNEL_LABELS["en"]).get(channel, channel)

        for fname in sorted(os.listdir(channel_dir)):
            fpath = os.path.join(channel_dir, fname)
            if not os.path.isfile(fpath):
                continue

            ext = os.path.splitext(fname)[1].lower()
            kind = FORMAT_KINDS.get(ext)
            fmt = t[f"fmt.{kind}"] if kind else ext
            size = os.path.getsize(fpath)
            size_str = f"{size / 1024:.0f}KB" if size < 1024 * 1024 else f"{size / (1024*1024):.1f}MB"

            # Determine content description from filename
            role = _role(fname)
            desc = t[f"role.{role}"] if role else fname
            if role == "poster":
                desc += " " + ("3:4" if "3x4" in fname else "16:9" if "16x9" in fname else "")
            elif role == "qr":
                target = ("xiaohongshu" if "xhs" in fname else "github" if "github" in fname else "")
                if target:
                    desc += " " + CHANNEL_LABELS.get(lang, CHANNEL_LABELS["en"])[target].split(" ", 1)[1]

            assets.append({
                "channel": channel,
                "channel_label": channel_label,
                "file": fname,
                "path": fpath,
                "format": fmt,
                "kind": kind,
                "role": role,
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


def generate_markdown(content_dir: str, assets: list, strategy: dict, lang: str = "en") -> str:
    """Generate markdown publish dashboard."""
    t = _t(lang)
    labels = CHANNEL_LABELS.get(lang, CHANNEL_LABELS["en"])
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
    lines.append(t["md.table"])
    lines.append(f"|------|------|------|------|------|")

    for ch in channels:
        for i, a in enumerate(channels[ch]):
            ch_label = a["channel_label"] if i == 0 else ""
            lines.append(f"| {ch_label} | {a['description']} | {a['format']} | {a['size']} | `{a['file']}` |")

    # Action items
    lines.append(f"")
    lines.append(t["md.actions"])
    lines.append(f"")

    for ch_key, ch_assets in channels.items():
        label = labels.get(ch_key, ch_key)
        lines.append(f"### {label}")

        # Find the main content (poster/video first, not QR) + caption
        main_asset = None
        caption = None
        for a in ch_assets:
            if a.get("kind") in ("image", "video") and a.get("role") != "qr":
                main_asset = a
            if a.get("role") in ("caption", "dynamic"):
                caption = a

        if ch_key == "xiaohongshu":
            lines.append(t["xhs.1"].format(file=main_asset["file"]) if main_asset else t["md.no_main"])
            lines.append(t["xhs.2"])
            lines.append(t["publish"])
        elif ch_key == "bilibili":
            lines.append(t["bili.1"].format(file=main_asset["file"]) if main_asset else t["md.no_main"])
            lines.append(t["bili.2"])
            lines.append(t["publish"])
        elif ch_key == "gongzhonghao":
            lines.append(t["gzh.1"])
            lines.append(t["gzh.2"].format(file=ch_assets[0]["file"]))
            lines.append(t["publish"])
        elif ch_key == "github":
            lines.append(t["gh.1"].format(file=ch_assets[0]["file"]))
            lines.append(t["gh.2"])
        else:
            lines.append(t["other"].format(label=label))

        lines.append(f"")

    # File locations
    lines.append(t["md.files"])
    lines.append(f"```")
    lines.append(f"{os.path.join(content_dir, 'deliver')}/")
    for ch in channels:
        lines.append(f"  {ch}/")
        for a in channels[ch]:
            lines.append(f"    {a['file']} ({a['size']})")
    lines.append(f"```")

    return "\n".join(lines)


def generate_html(content_dir: str, assets: list, strategy: dict, lang: str = "en") -> str:
    """Generate Pollinate Studio HTML — interactive review + publish page."""
    import base64
    t = _t(lang)

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
            body_m = (re.search(r"##+ (?:正文|Body)\n(.+?)(?=\n##+ (?:标签|Tags)|\Z)", kit_text, re.DOTALL)
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
    poster_html = f'<div class="placeholder">{t["html.no_cover"]}</div>'
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
              <h2>{t["html.video"]}</h2>
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
<html lang="{t['html.lang']}"><head><meta charset="UTF-8">
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
      <h2>{t['html.cover']}</h2>
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
      <h2>{t['html.title']}</h2>
      <textarea id="title" rows="2">{publish_kit['title']}</textarea>
      <button class="btn" onclick="copyField('title')">📋 Copy Title</button>
      <span class="copy-ok" id="title-ok">✅ Copied</span>
    </div>

    <div class="section">
      <h2>{t['html.body']}</h2>
      <textarea id="body" rows="12">{body_plain}</textarea>
      <button class="btn" onclick="copyField('body')">📋 Copy Body</button>
      <span class="copy-ok" id="body-ok">✅ Copied</span>
    </div>

    <div class="section">
      <h2>{t['html.tags']}</h2>
      <div class="tags" id="tag-display"></div>
      <button class="btn" onclick="copyTags()" style="margin-top:12px;">📋 Copy All Tags</button>
      <span class="copy-ok" id="tags-ok">✅ Copied</span>
    </div>

    <div class="section">
      <h2>{t['html.publish_xhs']}</h2>
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
    parser.add_argument("--lang", choices=LANGS, default="en",
                        help="Dashboard language (default: en)")
    args = parser.parse_args()

    content_dir = args.content_dir.rstrip("/")
    if not os.path.isdir(content_dir):
        print(f"Error: {content_dir} not found", file=sys.stderr)
        sys.exit(1)

    assets = scan_deliver(content_dir, args.lang)
    strategy = load_strategy(content_dir)

    if not assets:
        print(f"No publish-ready assets found in {content_dir}/deliver/")
        sys.exit(1)

    if args.html:
        html = generate_html(content_dir, assets, strategy, args.lang)
        html_path = os.path.join(content_dir, "publish_dashboard.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Dashboard: {html_path}")
        webbrowser.open(f"file://{os.path.abspath(html_path)}")
    else:
        md = generate_markdown(content_dir, assets, strategy, args.lang)
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
