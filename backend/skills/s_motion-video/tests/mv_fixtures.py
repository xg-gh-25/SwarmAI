"""Small synthetic motion-video projects for the film-layer and derive tests (no voice service, no network)."""
import json
import os
import sys

import pytest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.dont_write_bytecode = True

DOT_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
           '<circle cx="50" cy="50" r="40" fill="#c43c22"/></svg>\n')

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{sid}</title>
<script src="/skill/runtime/gsap.min.js"></script>
<script src="/skill/runtime/scene-runtime.js"></script>
<script src="/skill/runtime/film-layer.js"></script>
<script src="/project/scenes/film-data.js"></script>
<link rel="stylesheet" href="/skill/references/styles/exec-brief.css">
<link rel="stylesheet" href="/project/scenes/shared.css">
<style>.t {{ position: absolute; left: calc(240 * var(--u)); top: calc(320 * var(--u)); margin: 0; white-space: nowrap; }}
.dot {{ position: absolute; left: calc(1300 * var(--u)); top: calc(300 * var(--u)); width: calc(240 * var(--u)); }}</style>
</head><body>
<div id="stage" class="eb-stage"><div id="camera">
<img class="dot" src="../assets/dot.svg" alt="">
<h1 class="eb-statement t" id="t">{text}</h1>
</div></div>
<script>
  const tl = gsap.timeline({{ paused: true, defaults: {{ ease: "power3.out" }} }});
  FL.setup(tl, {{}});
  tl.fromTo("#t", {{ opacity: 0, y: 30 * MV.u }}, {{ opacity: 1, y: 0, duration: 0.5 }}, FL.at("{kw}", 0.5));
  MV.register(tl);
</script>
</body></html>
"""

WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]


def storyboard(scenes, fps=10, width=640, height=360, style="exec-brief"):
    return {"title": "Synthetic", "lang": "en-US", "width": width, "height": height, "fps": fps, "style": style,
            "method": "principle", "mode": "explainer", "voice": {"name": "en-US-GuyNeural", "rate": "+0%"},
            "bgm": None, "captions": True, "scenes": scenes}


def scene(sid, title, narration, duration, shot="statement", transition="cut"):
    return {"id": sid, "title": title, "narration": narration, "visual": f"{title} on screen", "shot_type": shot,
            "duration": duration, "transition": transition}


def words_for(narration, start=0.15, step=0.3):
    out, t = [], start
    for w in narration.split():
        out.append({"text": w, "start": round(t, 3), "end": round(t + step * 0.8, 3)})
        t += step
    return out


def write_voice(project, sid, narration, seed=""):
    v = os.path.join(project, "audio", "voice")
    os.makedirs(v, exist_ok=True)
    words = words_for(narration)
    json.dump(words, open(os.path.join(v, f"{sid}.words.json"), "w"))
    json.dump([{"text": narration, "start": words[0]["start"], "end": None}], open(os.path.join(v, f"{sid}.captions.json"), "w"))
    open(os.path.join(v, f"{sid}.mp3"), "wb").write(("fake mp3 " + narration + seed).encode())
    json.dump({"scene": sid, "hash": "h-" + narration, "text": narration}, open(os.path.join(v, f"{sid}.tts.json"), "w"))


def write_page(project, sid, text, kw):
    os.makedirs(os.path.join(project, "scenes"), exist_ok=True)
    open(os.path.join(project, "scenes", f"{sid}.html"), "w", encoding="utf-8").write(PAGE.format(sid=sid, text=text, kw=kw))


def make_project(root, n=4, film=None, duration=2.0):
    """a finished-looking project: n scenes with pages, voice files, assets, shared.css, film.json, film data"""
    import film as F
    os.makedirs(os.path.join(root, "assets"), exist_ok=True)
    os.makedirs(os.path.join(root, "scenes"), exist_ok=True)
    open(os.path.join(root, "assets", "dot.svg"), "w").write(DOT_SVG)
    open(os.path.join(root, "assets", "unused.svg"), "w").write(DOT_SVG)
    open(os.path.join(root, "scenes", "shared.css"), "w").write(".shared-note { opacity: .9; }\n")
    open(os.path.join(root, "design.md"), "w").write("# Design\n")
    scenes = []
    for k in range(n):
        sid = f"S{k + 1:02d}"
        narration = f"{WORDS[k]} line number {k + 1}"
        scenes.append(scene(sid, WORDS[k].title(), narration, duration))
        write_page(root, sid, WORDS[k].upper(), WORDS[k])
        write_voice(root, sid, narration)
    json.dump(storyboard(scenes), open(os.path.join(root, "storyboard.json"), "w"), indent=1)
    json.dump(film if film is not None else {"progress": False}, open(os.path.join(root, "film.json"), "w"))
    F.write(root)
    return root


def load_sb(project):
    return json.load(open(os.path.join(project, "storyboard.json"), encoding="utf-8"))


def save_sb(project, sb):
    json.dump(sb, open(os.path.join(project, "storyboard.json"), "w"), indent=1)


def need_browser():
    """skip (or with MV_REQUIRE_BROWSER=1 fail) unless SwarmAI's pinned browser runtime and ffmpeg are available:
    the same gate as every other browser test of the skill (conftest.require_browser)"""
    from conftest import require_browser
    require_browser(ffmpeg=True)
