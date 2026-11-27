#!/usr/bin/env python3
"""Render scene pages (HTML + paused GSAP timeline) frame-exact to MP4.

  render.py --project P                 render every scene whose fingerprint changed -> build/<id>.mp4
  render.py --project P --scene S03     one scene
  render.py --project P --preview       3 stills per scene (start/middle/end) -> review/<id>-{a,b,c}.jpg, no video
  render.py --project P --join          render, then join -> build/picture.mp4 (cut = concat, fade = 0.4 s xfade)
  render.py --project P --final         render, join, mux build/mix.wav -> out/<slug>.mp4
Options: --jobs N (scenes in parallel, default 3), --force (ignore fingerprints).

How a frame is made: a local HTTP server on 127.0.0.1 serves /skill/ (runtime, references) and /project/
(the project dir). Headless Chromium opens /project/scenes/<id>.html with window.__MV_DATA injected, waits for
window.__mvReady, then for frame i awaits window.__mvSeek(i/fps) and captures a JPEG (q92) that is piped to ffmpeg.
Every request that is not to the local server is aborted (pages cannot reach the network).

Fade transitions keep the film length = sum of scene durations: the outgoing scene gets a 0.4 s tail
(build/<id>.tail.mp4) = its last frame frozen, with the NEXT scene's caption already in the caption box, and the
join cross-fades that tail into the first 0.4 s of the next scene (xfade offset = end of the outgoing scene).
So the next scene starts exactly at its audio time, and the caption box never shows two lines blended.

Exit codes: 0 ok, 1 render/verification failed, 2 usage or environment error.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_FILES = [os.path.join(SKILL_DIR, "runtime", f) for f in ("gsap.min.js", "scene-runtime.js")]
SKILL_SERVED = ("runtime", "references", "templates", "assets")   # sub-dirs of the skill a page may load
RENDER_VERSION = "mv-render-1"
OPTIONAL_FILES = ("/project/scenes/shared.css",)   # may be linked by every scene but need not exist
FADE_S = 0.4
JPEG_Q = 92
READY_TIMEOUT_MS = 20000


class UsageError(Exception):
    """bad arguments, missing project files or missing tools -> exit 2"""


class RenderError(Exception):
    """a scene failed to render or verify -> exit 1"""


def tool(name):
    p = shutil.which(name) or (f"/opt/homebrew/bin/{name}" if os.path.exists(f"/opt/homebrew/bin/{name}") else None)
    if not p:
        raise UsageError(f"{name} not found (brew install ffmpeg)")
    return p


# ---------------------------------------------------------------- storyboard and scene data

def load_storyboard(project):
    p = os.path.join(project, "storyboard.json")
    if not os.path.isfile(p):
        raise UsageError(f"no storyboard.json in {project}")
    try:
        sb = json.load(open(p, encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise UsageError(f"storyboard.json is not valid JSON: {e}")
    for k in ("width", "height", "fps"):
        if not isinstance(sb.get(k), (int, float)) or sb[k] <= 0:
            raise UsageError(f"storyboard.json: '{k}' must be a positive number")
    if not isinstance(sb.get("scenes"), list) or not sb["scenes"]:
        raise UsageError("storyboard.json: 'scenes' must be a non-empty list")
    for s in sb["scenes"]:
        if not isinstance(s, dict) or not re.fullmatch(r"S\d{2,}", str(s.get("id", ""))):
            raise UsageError(f"storyboard.json: bad scene id {s.get('id') if isinstance(s, dict) else s!r}")
    return sb


def scene_frames(sb, sc):
    d = sc.get("duration")
    if not isinstance(d, (int, float)) or d <= 0:
        raise UsageError(f"{sc['id']}: duration is not set (run tts.py, or set it by hand for scenes without narration)")
    return max(1, int(round(float(d) * sb["fps"])))


def read_json_list(path):
    if not os.path.isfile(path):
        return None
    try:
        v = json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise UsageError(f"{path}: invalid JSON: {e}")
    if isinstance(v, dict):   # tolerate {"words": [...]} / {"captions": [...]}
        v = v.get("words") or v.get("captions") or []
    return [x for x in v if isinstance(x, dict) and "text" in x and "start" in x]


PUNCT = set("，。！？；：、,.!?;:…")


def captions_from_words(words, limit):
    """fallback when captions.json is missing: break at punctuation or `limit` chars; a line lasts until the next starts"""
    lines, cur, start = [], "", None
    for w in words:
        t = str(w["text"])
        if start is None:
            start = float(w["start"])
        joiner = "" if (not cur or re.match(r"[　-鿿]", t[:1]) or re.search(r"[　-鿿]$", cur)) else " "
        if cur and len((cur + joiner + t).replace(" ", "")) > limit:
            lines.append({"text": cur.strip(), "start": start})
            cur, start, joiner = "", float(w["start"]), ""
        cur += joiner + t
        if t and t[-1] in PUNCT:
            lines.append({"text": cur.strip(), "start": start})
            cur, start = "", None
    if cur.strip():
        lines.append({"text": cur.strip(), "start": start})
    for i, ln in enumerate(lines):
        ln["end"] = lines[i + 1]["start"] if i + 1 < len(lines) else None
    return lines


def scene_data(project, sb, idx, warn=print):
    sc = sb["scenes"][idx]
    sid = sc["id"]
    n = scene_frames(sb, sc)
    vdir = os.path.join(project, "audio", "voice")
    words = read_json_list(os.path.join(vdir, f"{sid}.words.json")) or []
    caps = read_json_list(os.path.join(vdir, f"{sid}.captions.json"))
    show = sb.get("captions", True) is not False
    if caps is None:
        caps = []
        if words and show:
            portrait = sb["height"] > sb["width"]
            caps = captions_from_words(words, 12 if portrait else 16)
            warn(f"  {sid}: no audio/voice/{sid}.captions.json; caption lines derived from words.json")
    style = sb.get("caption_style") if isinstance(sb.get("caption_style"), dict) else {}
    return {
        "id": sid, "index": idx, "count": len(sb["scenes"]),
        "width": int(sb["width"]), "height": int(sb["height"]), "fps": sb["fps"],
        "duration": n / sb["fps"], "frames": n,
        "words": words, "captions": caps, "showCaptions": show,
        "narration": sc.get("narration") or "", "lang": sb.get("lang") or "", "style": sb.get("style") or "",
        "captionStyle": {k: style.get(k) for k in ("bg", "fg", "font", "size", "radius", "bottom") if style.get(k) is not None},
    }


def caption_at(caps, t):
    cur = None
    for c in caps:
        if float(c["start"]) <= t + 1e-6:
            cur = c
    return cur["text"] if cur else ""


def tail_spec(sb, idx, data_next):
    """(frames, [caption text per tail frame]) when this scene fades into the next one, else None"""
    sc = sb["scenes"][idx]
    if sc.get("transition") != "fade" or idx + 1 >= len(sb["scenes"]):
        return None
    k = max(1, int(round(FADE_S * sb["fps"])))
    if data_next and data_next["showCaptions"]:
        caps = [caption_at(data_next["captions"], j / sb["fps"]) for j in range(k)]
    else:
        caps = None
    return k, caps


# ---------------------------------------------------------------- local server (/skill/ and /project/ only)

def make_handler(roots):
    class Handler(BaseHTTPRequestHandler):
        TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".woff2": "font/woff2", ".woff": "font/woff",
                 ".ttf": "font/ttf", ".otf": "font/otf", ".mp4": "video/mp4", ".webm": "video/webm"}

        def log_message(self, *a):
            pass

        def _send(self, body_ok):
            full, code = resolve_url(roots, self.path)
            if code != 200:
                self.send_error(code)
                return
            data = open(full, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", self.TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body_ok:
                self.wfile.write(data)

        def do_GET(self):
            self._send(True)

        def do_HEAD(self):
            self._send(False)

        def do_POST(self):
            self.send_error(405)

    return Handler


def resolve_url(roots, url_path):
    """URL path -> (file, 200) or (None, 403/404). Roots: {"skill": (dir, allowed subdirs or None), "project": (dir, None)}"""
    p = unquote(urlsplit(url_path).path)
    parts = p.lstrip("/").split("/", 1)
    if len(parts) < 2 or parts[0] not in roots or "\x00" in p:
        return None, 404
    root, allowed = roots[parts[0]]
    rel = parts[1]
    segs = rel.replace("\\", "/").split("/")
    if any(s in ("..", ".") for s in segs) or rel.startswith("/") or any(s.startswith(".") for s in segs if s):
        return None, 403
    if allowed is not None and segs[0] not in allowed:
        return None, 403
    root_real = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root_real, *segs))
    if os.path.commonpath([full, root_real]) != root_real:
        return None, 403
    if not os.path.isfile(full):
        return None, 404
    return full, 200


class Server:
    def __init__(self, project):
        self.roots = {"skill": (SKILL_DIR, SKILL_SERVED), "project": (os.path.abspath(project), None)}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.roots))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------- fingerprint

REF_RX = re.compile(r"""(?:href|src)\s*=\s*["']([^"'#?]+)|url\(\s*["']?([^"')#?]+)|@import\s+["']([^"']+)""")


def referenced_files(roots, text, base_url):
    out = []
    for m in REF_RX.finditer(text):
        ref = next(g for g in m.groups() if g)
        if ref.startswith(("data:", "#")):
            continue
        if ref.startswith("/"):
            url = ref
        else:   # relative to the page or css URL
            url = os.path.normpath(os.path.join(os.path.dirname(base_url), ref)).replace("\\", "/")
        full, code = resolve_url(roots, url)
        out.append((url, full))
    return out


def fingerprint(project, roots, data, tail):
    h = hashlib.sha256()
    h.update(RENDER_VERSION.encode())
    html_url = f"/project/scenes/{data['id']}.html"
    html_path = os.path.join(project, "scenes", f"{data['id']}.html")
    html = open(html_path, "rb").read()
    h.update(html)
    seen = set()
    files = [(u, f) for u, f in referenced_files(roots, html.decode("utf-8", "replace"), html_url)]
    files += [("/skill/runtime/" + os.path.basename(f), f) for f in RUNTIME_FILES]
    files.append(("/project/scenes/shared.css", os.path.join(project, "scenes", "shared.css")))
    i = 0
    while i < len(files):
        url, f = files[i]
        i += 1
        if url in seen:
            continue
        seen.add(url)
        h.update(url.encode())
        if f and os.path.isfile(f):
            b = open(f, "rb").read()
            h.update(hashlib.sha256(b).digest())
            if f.endswith(".css") and len(seen) < 200:
                files += referenced_files(roots, b.decode("utf-8", "replace"), url)
        else:
            h.update(b"<missing>")
    # scene position (index/count) is excluded so inserting a scene does not re-render the others
    h.update(json.dumps({k: v for k, v in data.items() if k not in ("index", "count")}, sort_keys=True, ensure_ascii=False).encode())
    h.update(json.dumps(tail, ensure_ascii=False).encode())
    return h.hexdigest()


# ---------------------------------------------------------------- browser

LAUNCH_ARGS = ["--force-color-profile=srgb", "--hide-scrollbars", "--mute-audio", "--disable-background-timer-throttling",
               "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
               "--proxy-server=http://127.0.0.1:9", "--no-first-run", "--disable-extensions"]

# Text rectangles of visible author text that intrude into the caption band / leave the safe box (used by check.py).
PROBE_JS = r"""() => {
  const W = innerWidth, H = innerHeight, top = MV.captionTop, sx = MV.safe.x, sy = MV.safe.y;
  const band = [], outside = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    const txt = n.textContent.trim();
    if (!txt) continue;
    const el = n.parentElement;
    if (!el || el.closest('#mv-caption') || el.closest('script,style')) continue;
    let op = 1, e = el, hidden = false;
    while (e) { const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden') { hidden = true; break; }
      op *= parseFloat(cs.opacity); e = e.parentElement; }
    if (hidden || op < 0.05) continue;
    const r = document.createRange(); r.selectNodeContents(n);
    for (const b of r.getClientRects()) {
      if (b.width < 1 || b.height < 1 || b.right <= 0 || b.left >= W || b.bottom <= 0 || b.top >= H) continue;
      const item = {text: txt.slice(0, 40), left: Math.round(b.left), top: Math.round(b.top), right: Math.round(b.right), bottom: Math.round(b.bottom)};
      if (MV.showCaptions && b.bottom > top + 1) band.push(item);
      else if (b.left < sx - 1 || b.right > W - sx + 1 || b.top < sy - 1 || b.bottom > H - sy + 1) outside.push(item);
      break;
    }
  }
  return {band, outside, captionTop: top};
}"""


class ScenePage:
    """one browser context + page for one scene; collects page errors, failed and blocked requests"""

    def __init__(self, browser, server, data):
        self.browser, self.server, self.data = browser, server, data
        self.errors, self.missing, self.blocked, self.console = [], [], [], []

    async def open(self):
        d = self.data
        self.ctx = await self.browser.new_context(viewport={"width": d["width"], "height": d["height"]}, device_scale_factor=1,
                                                  java_script_enabled=True, bypass_csp=False)
        await self.ctx.add_init_script(script="window.__MV_DATA = " + json.dumps(d, ensure_ascii=False) + ";")
        base = self.server.base + "/"

        async def gate(route):
            if route.request.url.startswith(base):
                await route.continue_()
            else:
                self.blocked.append(route.request.url[:200])
                await route.abort()

        await self.ctx.route("**/*", gate)
        self.page = await self.ctx.new_page()
        self.page.on("pageerror", lambda e: self.errors.append(str(e).splitlines()[0][:300]))
        self.page.on("response", lambda r: self.missing.append(f"{r.status} {r.url.replace(self.server.base, '')}") if r.status >= 400 else None)
        self.page.on("console", lambda m: self.console.append(f"{m.type}: {m.text[:300]}")
                     if m.type in ("warning", "error") and not m.text.startswith("Failed to load resource") else None)
        url = f"{self.server.base}/project/scenes/{d['id']}.html"
        await self.page.goto(url, wait_until="load", timeout=READY_TIMEOUT_MS)
        ready_js = "window.__mvReady === true && !!(window.MV && window.MV.registered)"
        t_end = time.time() + READY_TIMEOUT_MS / 1000
        while not self.errors:
            if await self.page.evaluate(ready_js):
                break
            if time.time() > t_end:
                state = await self.page.evaluate("({rt: !!window.MV, reg: !!(window.MV && window.MV.registered)})")
                why = ("the runtime did not load (is /skill/runtime/scene-runtime.js linked?)" if not state["rt"]
                       else "scene did not call MV.register(tl)" if not state["reg"] else "fonts/images never finished loading")
                raise RenderError(f"{d['id']}: page not ready after {READY_TIMEOUT_MS // 1000}s: {why}" + self.problems())
            await asyncio.sleep(0.05)
        if self.errors:
            raise RenderError(f"{d['id']}: page script error" + self.problems())
        self.cdp = await self.ctx.new_cdp_session(self.page)

    def problems(self):
        s = ""
        if self.errors:
            s += "\n    errors: " + " | ".join(self.errors[:5])
        missing = [m for m in self.missing if not m.endswith(OPTIONAL_FILES)]
        if missing:
            s += "\n    missing files: " + ", ".join(missing[:8])
        if self.blocked:
            s += "\n    blocked network requests: " + ", ".join(self.blocked[:5])
        return s

    async def seek(self, t, caption=None):
        if caption is None:
            await self.page.evaluate("t => window.__mvSeek(t)", t)
        else:
            await self.page.evaluate("([t, c]) => window.__mvSeek(t, c)", [t, caption])

    async def jpeg(self):
        r = await self.cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": JPEG_Q, "optimizeForSpeed": True,
                                                            "captureBeyondViewport": False, "fromSurface": True})
        return base64.b64decode(r["data"])

    async def close(self):
        try:
            await self.ctx.close()
        except Exception:
            pass


SETPARAMS = "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"


def encode_args(fps, out):
    """JPEG (full range, BT.601 matrix) -> H.264 yuv420p limited range, BT.709 matrix and tags"""
    return [tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
            "-vf", "scale=in_range=full:out_range=tv:in_color_matrix=bt601:out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int,"
                   "format=yuv420p," + SETPARAMS,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-g", str(int(round(fps * 2))),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-r", str(fps), "-an", "-movflags", "+faststart", out]


async def encode_frames(sp, times, captions, out):
    tmp = out + ".part.mp4"
    proc = await asyncio.create_subprocess_exec(*encode_args(sp.data["fps"], tmp), stdin=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        for i, t in enumerate(times):
            await sp.seek(t, None if captions is None else captions[i])
            proc.stdin.write(await sp.jpeg())
            await proc.stdin.drain()
            if sp.errors:
                raise RenderError(f"{sp.data['id']}: page error at t={t:.3f}s" + sp.problems())
        proc.stdin.close()
        err = (await proc.stderr.read()).decode("utf-8", "replace")
        if await proc.wait() != 0:
            raise RenderError(f"{sp.data['id']}: ffmpeg failed: {err[-400:]}")
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    os.replace(tmp, out)


async def render_scene_video(browser, server, project, data, tail):
    sid, fps, n = data["id"], data["fps"], data["frames"]
    build = os.path.join(project, "build")
    sp = ScenePage(browser, server, data)
    t0 = time.time()
    try:
        await sp.open()
        t1 = time.time()
        await encode_frames(sp, [i / fps for i in range(n)], None, os.path.join(build, f"{sid}.mp4"))
        frames = n
        tail_path = os.path.join(build, f"{sid}.tail.mp4")
        if tail:
            k, caps = tail
            await encode_frames(sp, [(n - 1) / fps] * k, caps, tail_path)
            frames += k
        elif os.path.exists(tail_path):
            os.remove(tail_path)
    finally:
        await sp.close()
    dt = time.time() - t1
    warn = sp.problems()
    return {"id": sid, "frames": frames, "load_s": round(t1 - t0, 2), "capture_s": round(dt, 2),
            "fps": round(frames / dt, 1) if dt > 0 else None, "warnings": warn}


async def preview_scene(browser, server, project, data, probe=True):
    sid, fps, n = data["id"], data["fps"], data["frames"]
    review = os.path.join(project, "review")
    os.makedirs(review, exist_ok=True)
    sp = ScenePage(browser, server, data)
    res = {"id": sid, "stills": [], "errors": [], "missing": [], "blocked": [], "console": [], "tweens": []}
    try:
        await sp.open()
        if probe:
            res["tweens"] = await sp.page.evaluate("window.__mvTweens ? window.__mvTweens() : []")
        for tag, f in zip("abc", sorted({0, n // 2, n - 1}) if n >= 3 else [0] * 3):
            t = f / fps
            await sp.seek(t)
            p = os.path.join(review, f"{sid}-{tag}.jpg")
            open(p, "wb").write(await sp.jpeg())
            item = {"tag": tag, "frame": f, "t": round(t, 3), "path": p}
            if probe:
                item.update(await sp.page.evaluate(PROBE_JS))
            res["stills"].append(item)
    except RenderError as e:
        res["errors"].append(str(e))
    except Exception as e:   # playwright errors (timeouts, crashed page)
        res["errors"].append(f"{sid}: {type(e).__name__}: {str(e).splitlines()[0][:300]}" + sp.problems())
    finally:
        await sp.close()
    if sp.errors and not res["errors"]:
        res["errors"].append(f"{sid}: page script error" + sp.problems())
    res["missing"], res["blocked"], res["console"] = sp.missing, sp.blocked, sp.console
    return res


async def with_browser(fn):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise UsageError("python playwright is not installed (python3 -m pip install playwright)")
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
        except Exception as e:
            raise UsageError(f"cannot launch Chromium: {str(e).splitlines()[0]} (try: python3 -m playwright install chromium)")
        try:
            return await fn(browser)
        finally:
            await browser.close()


def select_scenes(sb, scene):
    ids = [s["id"] for s in sb["scenes"]]
    if scene is None:
        return list(range(len(ids)))
    if scene not in ids:
        raise UsageError(f"scene {scene} is not in storyboard.json (have: {', '.join(ids)})")
    return [ids.index(scene)]


def check_scene_files(project, sb, idxs):
    for i in idxs:
        sid = sb["scenes"][i]["id"]
        if not os.path.isfile(os.path.join(project, "scenes", f"{sid}.html")):
            raise UsageError(f"missing scenes/{sid}.html")


def preview_scenes(project, scene=None, jobs=3, probe=True, log=print):
    """3 stills per scene (scene = None for all, an id, or a list of ids); returns {id: result}. Used by --preview and check.py"""
    project = os.path.abspath(project)
    sb = load_storyboard(project)
    if isinstance(scene, (list, tuple)):
        idxs = [i for x in scene for i in select_scenes(sb, x)]
    else:
        idxs = select_scenes(sb, scene)
    check_scene_files(project, sb, idxs)
    datas = {i: scene_data(project, sb, i, warn=log) for i in idxs}

    async def run(browser):
        sem = asyncio.Semaphore(max(1, jobs))

        async def one(i):
            async with sem:
                return await preview_scene(browser, server, project, datas[i], probe)
        return await asyncio.gather(*(one(i) for i in idxs))

    with Server(project) as server:
        results = asyncio.run(with_browser(run))
    return {r["id"]: r for r in results}


def render_scenes(project, scene=None, jobs=3, force=False, log=print):
    project = os.path.abspath(project)
    sb = load_storyboard(project)
    idxs = select_scenes(sb, scene)
    check_scene_files(project, sb, idxs)
    build = os.path.join(project, "build")
    os.makedirs(build, exist_ok=True)
    n_sc = len(sb["scenes"])
    all_data = {}

    def data_of(i):
        if i not in all_data:
            all_data[i] = scene_data(project, sb, i, warn=log)
        return all_data[i]

    todo = []
    with Server(project) as server:
        for i in idxs:
            d = data_of(i)
            tail = tail_spec(sb, i, data_of(i + 1) if i + 1 < n_sc else None)
            if sb["scenes"][i].get("transition") == "fade" and i + 1 >= n_sc:
                log(f"  {d['id']}: transition 'fade' on the last scene has nothing to fade into; treated as cut")
            fp = fingerprint(project, server.roots, d, tail)
            fp_path = os.path.join(build, f"{d['id']}.fp")
            outs = [os.path.join(build, f"{d['id']}.mp4")] + ([os.path.join(build, f"{d['id']}.tail.mp4")] if tail else [])
            if not force and all(os.path.isfile(o) for o in outs) and os.path.isfile(fp_path) and open(fp_path).read().strip() == fp:
                log(f"{d['id']}: up to date (fingerprint match), skipped")
                continue
            todo.append((i, d, tail, fp, fp_path))
        if not todo:
            return []

        async def run(browser):
            sem = asyncio.Semaphore(max(1, jobs))

            async def one(item):
                i, d, tail, fp, fp_path = item
                async with sem:
                    if os.path.exists(fp_path):
                        os.remove(fp_path)
                    r = await render_scene_video(browser, server, project, d, tail)
                    open(fp_path, "w").write(fp + "\n")
                    log(f"{r['id']}: {r['frames']} frames, load {r['load_s']}s, capture+encode {r['capture_s']}s ({r['fps']} frames/s)"
                        + (f"\n  warnings:{r['warnings']}" if r["warnings"] else ""))
                    return r
            return await asyncio.gather(*(one(x) for x in todo))

        t0 = time.time()
        res = asyncio.run(with_browser(run))
        total = sum(r["frames"] for r in res)
        dt = time.time() - t0
        log(f"rendered {len(res)} scene(s), {total} frames in {dt:.1f}s ({total / dt:.1f} frames/s overall, jobs={jobs})")
        return res


# ---------------------------------------------------------------- join and final mux

def probe_frames(path):
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return int(r.stdout.strip().split(",")[0])
    except ValueError:
        raise RenderError(f"cannot read frame count of {path}: {r.stderr[-200:]}")


def probe_duration(path):
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise RenderError(f"cannot read duration of {path}: {r.stderr[-200:]}")


def join(project, log=print):
    project = os.path.abspath(project)
    sb = load_storyboard(project)
    fps = sb["fps"]
    build = os.path.join(project, "build")
    scenes = sb["scenes"]
    frames = [scene_frames(sb, s) for s in scenes]
    clips = []
    for i, s in enumerate(scenes):
        p = os.path.join(build, f"{s['id']}.mp4")
        if not os.path.isfile(p):
            raise RenderError(f"missing build/{s['id']}.mp4 (render it first)")
        got = probe_frames(p)
        if got != frames[i]:
            raise RenderError(f"build/{s['id']}.mp4 has {got} frames, storyboard says {frames[i]} (re-render with --force)")
        fade = s.get("transition") == "fade" and i + 1 < len(scenes)
        tail = os.path.join(build, f"{s['id']}.tail.mp4") if fade else None
        if fade and not os.path.isfile(tail):
            raise RenderError(f"missing build/{s['id']}.tail.mp4 for the fade into {scenes[i + 1]['id']} (re-render {s['id']})")
        clips.append((p, tail))
    out = os.path.join(build, "picture.mp4")
    jfp = hashlib.sha256(json.dumps([RENDER_VERSION, fps, [s.get("transition") for s in scenes],
                                     [open(os.path.join(build, f"{s['id']}.fp")).read() if os.path.isfile(os.path.join(build, f"{s['id']}.fp")) else "" for s in scenes],
                                     ]).encode()).hexdigest()
    jfp_path = os.path.join(build, "picture.fp")
    total = sum(frames)
    if os.path.isfile(out) and os.path.isfile(jfp_path) and open(jfp_path).read().strip() == jfp:
        log("picture.mp4: up to date, skipped")
        return out
    t0 = time.time()
    tmp = out + ".part.mp4"
    if not any(t for _, t in clips):
        lst = os.path.join(build, "concat.txt")
        open(lst, "w").write("".join(f"file '{p}'\n" for p, _ in clips))
        cmd = [tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
               "-an", "-c:v", "copy", "-movflags", "+faststart", tmp]
        r = subprocess.run(cmd, capture_output=True, text=True)
        os.remove(lst)
        how = "concat (stream copy)"
    else:
        inputs, graph, k_in = [], [], 0
        labels = []
        for p, t in clips:
            inputs += ["-i", p]
            labels.append([f"v{k_in}"])
            graph.append(f"[{k_in}:v]setpts=PTS-STARTPTS,format=yuv420p,settb=AVTB[v{k_in}]")
            k_in += 1
            if t:
                inputs += ["-i", t]
                labels[-1].append(f"v{k_in}")
                graph.append(f"[{k_in}:v]setpts=PTS-STARTPTS,format=yuv420p,settb=AVTB[v{k_in}]")
                k_in += 1
        acc, acc_frames, step = labels[0][0], frames[0], 0
        k_fade = max(1, int(round(FADE_S * fps)))
        for i in range(1, len(clips)):
            nxt = labels[i][0]
            prev_tail = labels[i - 1][1] if len(labels[i - 1]) > 1 else None
            step += 1
            if prev_tail:
                graph.append(f"[{acc}][{prev_tail}]concat=n=2:v=1:a=0[t{step}]")
                graph.append(f"[t{step}][{nxt}]xfade=transition=fade:duration={k_fade / fps:.6f}:offset={acc_frames / fps:.6f}[j{step}]")
            else:
                graph.append(f"[{acc}][{nxt}]concat=n=2:v=1:a=0[j{step}]")
            acc, acc_frames = f"j{step}", acc_frames + frames[i]
        graph.append(f"[{acc}]{SETPARAMS}[out]")
        acc = "out"
        cmd = [tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error"] + inputs + [
            "-filter_complex", ";".join(graph), "-map", f"[{acc}]", "-frames:v", str(total), "-r", str(fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-g", str(int(round(fps * 2))),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
            "-an", "-movflags", "+faststart", tmp]
        r = subprocess.run(cmd, capture_output=True, text=True)
        how = "concat + xfade (re-encode)"
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RenderError(f"join failed: {r.stderr[-600:]}")
    got = probe_frames(tmp)
    if got != total:
        os.remove(tmp)
        raise RenderError(f"joined picture has {got} frames, storyboard sum is {total}")
    os.replace(tmp, out)
    open(jfp_path, "w").write(jfp + "\n")
    log(f"picture.mp4: {total} frames ({total / fps:.2f}s) via {how} in {time.time() - t0:.1f}s")
    return out


def slug_of(project, sb):
    s = sb.get("slug") or re.sub(r"^\d{4}-\d{2}-\d{2}-", "", os.path.basename(os.path.abspath(project).rstrip("/")))
    s = re.sub(r"[^\w\-]+", "-", s).strip("-")
    return s or "film"


def final(project, log=print):
    project = os.path.abspath(project)
    sb = load_storyboard(project)
    fps = sb["fps"]
    pic = os.path.join(project, "build", "picture.mp4")
    mix = os.path.join(project, "build", "mix.wav")
    for p in (pic, mix):
        if not os.path.isfile(p):
            raise RenderError(f"missing {os.path.relpath(p, project)}" + (" (run mix.py)" if p == mix else " (run --join)"))
    v = probe_frames(pic) / fps
    a = probe_duration(mix)
    if abs(v - a) > 1.0 / fps + 1e-6:
        raise RenderError(f"audio and picture lengths differ: picture {v:.3f}s, mix.wav {a:.3f}s (more than 1 frame); "
                          "re-run mix.py after changing durations")
    out_dir = os.path.join(project, "out")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, slug_of(project, sb) + ".mp4")
    tmp = out + ".part.mp4"
    r = subprocess.run([tool("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-i", pic, "-i", mix, "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", tmp],
                       capture_output=True, text=True)
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RenderError(f"final mux failed: {r.stderr[-400:]}")
    os.replace(tmp, out)
    log(f"final: {out} (picture {v:.3f}s, audio {a:.3f}s)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render scene pages to MP4 (frame-exact seek render).")
    ap.add_argument("--project", required=True, help="project directory")
    ap.add_argument("--scene", help="render one scene, e.g. S03")
    ap.add_argument("--preview", action="store_true", help="only 3 stills per scene into review/<id>-{a,b,c}.jpg")
    ap.add_argument("--jobs", type=int, default=3, help="scenes rendered in parallel (default 3)")
    ap.add_argument("--join", action="store_true", help="join all scenes into build/picture.mp4")
    ap.add_argument("--final", action="store_true", help="join, then mux build/mix.wav into out/<slug>.mp4")
    ap.add_argument("--force", action="store_true", help="re-render even if the fingerprint matches")
    a = ap.parse_args(argv)
    try:
        if not os.path.isdir(a.project):
            raise UsageError(f"project directory not found: {a.project}")
        if a.jobs < 1:
            raise UsageError("--jobs must be >= 1")
        if a.preview:
            if a.join or a.final:
                raise UsageError("--preview cannot be combined with --join/--final")
            res = preview_scenes(a.project, a.scene, a.jobs, probe=False)
            bad = 0
            for sid, r in res.items():
                if r["errors"]:
                    bad += 1
                    print(f"{sid}: FAILED\n  " + "\n  ".join(r["errors"]))
                else:
                    print(f"{sid}: " + ", ".join(os.path.relpath(s["path"], a.project) for s in r["stills"]))
                for m in r["missing"]:
                    if not m.endswith(OPTIONAL_FILES):
                        print(f"  warning: {m}")
            return 1 if bad else 0
        if (a.join or a.final) and a.scene:
            raise UsageError("--join/--final work on the whole film; drop --scene")
        render_scenes(a.project, a.scene, a.jobs, a.force)
        if a.join or a.final:
            join(a.project)
        if a.final:
            final(a.project)
        return 0
    except UsageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except RenderError as e:
        print(f"render failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
