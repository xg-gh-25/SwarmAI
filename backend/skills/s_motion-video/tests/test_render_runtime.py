"""render.py on SwarmAI's pinned browser runtime, run by SwarmAI's own Python, with no Python Playwright.

The browser tests need the pinned runtime and ffmpeg (conftest.py: they skip without them, MV_REQUIRE_BROWSER=1 makes
that a failure). The end-to-end test runs the scripts the way a person does ("$SWARM_BACKEND_BIN" py, here the backend
interpreter running desktop_main.py py) with a `playwright` package on the path that cannot be imported, so it proves
the render does not depend on Python Playwright even on a machine that has it.
Run: python -m pytest skills/s_motion-video/tests/test_render_runtime.py -q   (from backend/)"""
import ast
import asyncio
import json
import os
import subprocess
import sys

import pytest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(SKILL, "scripts")
BACKEND = os.path.dirname(os.path.dirname(SKILL))
sys.path.insert(0, SCRIPTS)

import render as R  # noqa: E402

FPS = 30
W, H = 480, 270
SCENES = [("S01", 3.5, "fade", "#18324a", "#f2a541"), ("S02", 2.0, "cut", "#2b1d3a", "#5fd38d"),
          ("S03", 2.0, "cut", "#3a2216", "#59a5f5")]

SCENE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{sid}</title>
<script src="/skill/runtime/gsap.min.js"></script>
<script src="/skill/runtime/scene-runtime.js"></script>
<style>
  body {{ margin: 0; background: radial-gradient(circle at 30% 30%, #ffffff33, {bg}); overflow: hidden; }}
  .title {{ position: absolute; left: 8%; top: 12%; color: #fff; font: 700 calc(72 * var(--u)) sans-serif; margin: 0; }}
  .card {{ position: absolute; left: 8%; top: 42%; width: 34%; height: 24%; border-radius: calc(24 * var(--u)); background: {fg}; }}
  .bar {{ position: absolute; left: 8%; top: 74%; height: calc(14 * var(--u)); width: 80%; background: #fff; transform-origin: 0 50%; }}
</style></head>
<body>
<h1 class="title" id="title">Bill -40%</h1>
<div class="card" id="card"></div>
<div class="bar" id="bar"></div>
<script>
  if (!window.__MV_DATA || window.__MV_DATA.id !== "{sid}") throw new Error("__MV_DATA missing before the page script");
  const tl = gsap.timeline({{ paused: true, defaults: {{ ease: "power3.out", duration: 0.6 }} }});
  tl.fromTo("#title", {{ opacity: 0, y: 30 }}, {{ opacity: 1, y: 0 }}, 0.1);
  tl.fromTo("#card", {{ opacity: 0, rotation: -8 }}, {{ opacity: 1, rotation: 0, duration: 0.8 }}, 0.3);
  tl.fromTo("#bar", {{ scaleX: 0.02 }}, {{ scaleX: 1, duration: MV.duration, ease: "none" }}, 0);
  MV.register(tl);
</script></body></html>
"""


def make_project(root, scenes=SCENES, html=SCENE_HTML):
    os.makedirs(os.path.join(root, "scenes"))
    os.makedirs(os.path.join(root, "audio", "voice"))
    sb = {"title": "Render runtime test", "lang": "en", "width": W, "height": H, "fps": FPS, "style": "exec-brief",
          "method": "principle", "mode": "explainer", "voice": {"name": "Matthew"}, "bgm": None, "captions": True,
          "slug": "runtime-test",
          "scenes": [{"id": sid, "title": f"Scene {sid}", "narration": "", "visual": "title, card, bar",
                      "shot_type": "statement", "duration": d, "transition": tr} for sid, d, tr, _, _ in scenes]}
    with open(os.path.join(root, "storyboard.json"), "w", encoding="utf-8") as f:
        json.dump(sb, f)
    for sid, d, _, bg, fg in scenes:
        with open(os.path.join(root, "scenes", f"{sid}.html"), "w", encoding="utf-8") as f:
            f.write(html.format(sid=sid, bg=bg, fg=fg))
        with open(os.path.join(root, "audio", "voice", f"{sid}.captions.json"), "w", encoding="utf-8") as f:
            json.dump([{"text": f"First line {sid}", "start": 0.0, "end": d / 2}, {"text": f"Second line {sid}", "start": d / 2, "end": None}], f)
    return sb


def bundled_py(script, *args, env=None):
    """run a skill script the product's way: the backend interpreter's `py` (desktop_main.py py)"""
    return subprocess.run([sys.executable, os.path.join(BACKEND, "desktop_main.py"), "py", os.path.join(SCRIPTS, script), *args],
                          capture_output=True, text=True, timeout=55, env=env, cwd=BACKEND)


def no_playwright_env(tmp_path):
    """the environment with a `playwright` package first on the path that refuses to import"""
    block = tmp_path / "block" / "playwright"
    block.mkdir(parents=True)
    (block / "__init__.py").write_text("raise ImportError('Python Playwright is blocked in this test')\n", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path / "block") + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def ffprobe_json(path, entries):
    r = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries", entries,
                        "-of", "json", path], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


# ---------------------------------------------------------------- no Python Playwright anywhere

def test_no_script_imports_python_playwright():
    bad = []
    for name in sorted(os.listdir(SCRIPTS)):
        if name.endswith(".py"):
            tree = ast.parse(open(os.path.join(SCRIPTS, name), encoding="utf-8").read())
            for node in ast.walk(tree):
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                        [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                bad += [f"{name}:{node.lineno}: {m}" for m in mods if m.split(".")[0] == "playwright"]
    assert not bad, bad
    assert os.path.isfile(R.DRIVER) and R.DRIVER.endswith("render_driver.mjs")


def test_blocker_really_blocks_playwright(tmp_path):
    r = subprocess.run([sys.executable, os.path.join(BACKEND, "desktop_main.py"), "py", "-c", "import playwright"],
                       capture_output=True, text=True, timeout=55, env=no_playwright_env(tmp_path), cwd=BACKEND)
    assert r.returncode != 0 and "blocked" in r.stderr


# ---------------------------------------------------------------- end to end under the bundled interpreter

def test_bundled_python_preview_join_check_on_three_scene_fade_project(pinned_browser_ffmpeg, tmp_path):
    root = str(tmp_path / "film")
    sb = make_project(root)
    env = no_playwright_env(tmp_path)

    r = bundled_py("render.py", "--project", root, "--scene", "S01", "--preview", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    from PIL import Image
    for tag in "abc":
        p = os.path.join(root, "review", f"S01-{tag}.jpg")
        with Image.open(p) as im:
            assert im.format == "JPEG" and im.size == (W, H)

    r = bundled_py("render.py", "--project", root, "--join", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "per-fade encode (stream copy, 12 fade frames encoded)" in r.stdout, r.stdout
    total = sum(int(round(s["duration"] * FPS)) for s in sb["scenes"])
    info = ffprobe_json(os.path.join(root, "build", "picture.mp4"), "stream=nb_read_packets:format=duration")
    assert int(info["streams"][0]["nb_read_packets"]) == total
    assert float(info["format"]["duration"]) == pytest.approx(total / FPS, abs=1e-6)
    assert R.probe_frames(os.path.join(root, "build", "S01.tail.mp4")) == R.fade_frames(FPS)

    r = bundled_py("render.py", "--project", root, "--join", env=env)   # fingerprints: nothing changed, nothing redone
    assert r.returncode == 0 and r.stdout.count("up to date (fingerprint match)") == 3, r.stdout
    assert "picture.mp4: up to date" in r.stdout

    r = bundled_py("check.py", "--project", root, "--scene", "S01", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.load(open(os.path.join(root, "review", "check-S01.json"), encoding="utf-8"))
    assert report["ok"] and [st["tag"] for st in report["previews"]["S01"]] == ["a", "b", "c"]

    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{total / FPS:.6f}",
                    os.path.join(root, "build", "mix.wav")], check=True)
    r = bundled_py("render.py", "--project", root, "--final", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert os.path.isfile(os.path.join(root, "out", "runtime-test.mp4"))


# ---------------------------------------------------------------- page isolation and reporting

ISOLATION_HTML = """<!doctype html><html><head><meta charset="utf-8">
<script src="/skill/runtime/gsap.min.js"></script>
<script src="/skill/runtime/scene-runtime.js"></script>
<link rel="stylesheet" href="/project/scenes/nope.css">
</head><body><img src="https://example.invalid/pixel.png">
<script>
  if (!window.__MV_DATA || window.__MV_DATA.id !== "{sid}") throw new Error("__MV_DATA missing before the page script");
  try {{ new WebSocket("ws://example.invalid/socket"); }} catch (e) {{}}
  console.error("boom-console {sid}");
  if ("{sid}" === "S02") throw new Error("scene-broke");
  const tl = gsap.timeline({{ paused: true }});
  tl.fromTo("body", {{ opacity: 0.5 }}, {{ opacity: 1, duration: 0.5 }}, 0);
  MV.register(tl);
</script></body></html>
"""


def test_page_isolation_blocks_network_and_reports_errors(pinned_browser, tmp_path):
    root = str(tmp_path / "film")
    make_project(root, [("S01", 2.0, "cut", "#000", "#fff"), ("S02", 2.0, "cut", "#000", "#fff")], ISOLATION_HTML)
    res = R.preview_scenes(root, ["S01", "S02"], 2, probe=True, log=lambda m: None)

    ok = res["S01"]
    assert ok["errors"] == [], ok["errors"]
    assert "https://example.invalid/pixel.png" in ok["blocked"]
    assert "ws://example.invalid/socket" in ok["blocked"]
    assert not any(b.startswith("http://127.0.0.1") for b in ok["blocked"])
    assert "404 /project/scenes/nope.css" in ok["missing"]
    assert "error: boom-console S01" in ok["console"]
    assert len(ok["stills"]) == 3 and all(os.path.getsize(st["path"]) > 0 for st in ok["stills"])
    assert all({"band", "outside", "captionTop"} <= set(st) for st in ok["stills"])
    assert isinstance(ok["tweens"], list) and ok["tweens"]

    broke = res["S02"]
    assert broke["stills"] == [] and len(broke["errors"]) == 1
    assert "S02: page script error" in broke["errors"][0] and "scene-broke" in broke["errors"][0]


# ---------------------------------------------------------------- environment errors: exit 2, one line

def test_runtime_unavailable_exits_2_with_reason(tmp_path):
    root = str(tmp_path / "film")
    make_project(root)
    env = dict(no_playwright_env(tmp_path), SWARM_FEATURE_MAP_RUNTIME=str(tmp_path / "no-runtime"))
    r = bundled_py("render.py", "--project", root, "--scene", "S01", "--preview", env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    line = r.stderr.strip()
    assert "\n" not in line, line
    assert line.startswith("error: the pinned browser runtime is not available: ")
    assert f"SWARM_FEATURE_MAP_RUNTIME={tmp_path / 'no-runtime'} is not a runtime folder" in line


@pytest.mark.skipif(sys.platform.startswith("win"), reason="the fake node is a shell script")
def test_chromium_start_failure_exits_2(tmp_path):
    rt = tmp_path / "rt"
    (rt / "node" / "bin").mkdir(parents=True)
    pkg = rt / "pw" / "node_modules" / "@playwright" / "test"
    pkg.mkdir(parents=True)
    (pkg / "cli.js").write_text("", encoding="utf-8")
    (pkg / "index.mjs").write_text("", encoding="utf-8")
    node = rt / "node" / "bin" / "node"
    node.write_text('#!/bin/sh\necho \'{"ready": false, "error": "fake launch failure"}\'\nexit 3\n', encoding="utf-8")
    node.chmod(0o755)
    root = str(tmp_path / "film")
    make_project(root)
    env = dict(no_playwright_env(tmp_path), SWARM_FEATURE_MAP_RUNTIME=str(rt))
    r = bundled_py("render.py", "--project", root, "--scene", "S01", "--preview", env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert r.stderr.strip() == "error: cannot launch the pinned Chromium: fake launch failure"


def test_missing_ffmpeg_hint_per_os(monkeypatch):
    assert "brew install ffmpeg" in R.ffmpeg_hint("darwin")
    assert "winget install Gyan.FFmpeg" in R.ffmpeg_hint("win32")
    assert "apt install ffmpeg" in R.ffmpeg_hint("linux") and "brew" not in R.ffmpeg_hint("linux")
    monkeypatch.setattr(R.shutil, "which", lambda name: None)
    monkeypatch.setattr(R.os.path, "exists", lambda p: False)
    for plat, hint in (("darwin", "brew install ffmpeg"), ("win32", "winget"), ("linux", "apt install ffmpeg")):
        monkeypatch.setattr(R.sys, "platform", plat)
        with pytest.raises(R.UsageError) as e:
            R.tool("ffprobe")
        assert str(e.value).startswith("ffprobe not found on PATH (") and hint in str(e.value)


# ---------------------------------------------------------------- the driver failing: a RenderError, never a hang

def _open_page(root, body):
    """run body(scene page) against S01 of the project in a fresh browser; returns what body returns"""
    sb = R.load_storyboard(root)
    data = R.scene_data(root, sb, 0, warn=lambda m: None)

    async def run(browser):
        sp = R.ScenePage(browser, server, data)
        await sp.open()
        try:
            return await asyncio.wait_for(body(browser, sp), 60)
        finally:
            await sp.close()

    with R.Server(root) as server:
        return asyncio.run(R.with_browser(run))


def test_driver_death_raises_render_error(pinned_browser, tmp_path):
    root = str(tmp_path / "film")
    make_project(root)

    async def body(browser, sp):
        assert (await sp.frame(0.0))[:2] == b"\xff\xd8"           # a JPEG while the driver lives
        browser.proc.kill()
        with pytest.raises(R.RenderError) as e:
            await sp.frame(0.5)
        return str(e.value)

    msg = _open_page(root, body)
    assert "the browser driver stopped" in msg and "'frame'" in msg


def test_driver_hang_raises_render_error(pinned_browser, tmp_path, monkeypatch):
    root = str(tmp_path / "film")
    make_project(root)
    monkeypatch.setattr(R, "REQUEST_HANG_S", 2)

    async def body(browser, sp):
        with pytest.raises(R.RenderError) as e:
            await sp.evaluate("new Promise(() => {})")
        assert browser.proc.returncode is not None              # killed, not left running
        with pytest.raises(R.RenderError):
            await sp.frame(0.0)                                  # and every later request fails at once
        return str(e.value)

    msg = _open_page(root, body)
    assert "did not answer 'eval' in 2s" in msg


def test_error_reply_keeps_page_events(pinned_browser, tmp_path):
    """what the page reported up to a failed request still reaches the scene's lists and the error text"""
    root = str(tmp_path / "film")
    make_project(root)

    async def body(browser, sp):
        with pytest.raises(R.RenderError) as e:
            await sp.evaluate("(() => { console.error('said before failing'); throw new Error('eval-broke'); })()")
        return str(e.value), list(sp.console)

    msg, console = _open_page(root, body)
    assert "S01: eval: " in msg and "eval-broke" in msg
    assert "error: said before failing" in console


# ---------------------------------------------------------------- the driver's environment and its own failures

class _StubRuntime:
    def __init__(self, folder):
        from pathlib import Path
        self.playwright_pkg = Path(folder) / "pw" / "node_modules" / "@playwright" / "test"

    def env(self):
        return {"PLAYWRIGHT_BROWSERS_PATH": "/rt/browsers", "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1"}


DUMMY = "dummy-credential-value"   # stands in for a credential; never a real one


def test_driver_env_is_an_allowlist(monkeypatch, tmp_path):
    """no credential and no Node/Playwright behaviour variable reaches the process that runs scene pages; what Node
    and Chromium need does, whatever case the OS stores it in (Windows: SYSTEMROOT, WINDIR)"""
    monkeypatch.setattr(R.os, "environ", {
        "PATH": "/usr/bin", "HOME": "/home/u", "LANG": "en_US.UTF-8", "FONTCONFIG_FILE": "/etc/fonts.conf",
        "SYSTEMROOT": r"C:\Windows", "WINDIR": r"C:\Windows",
        "AWS_SECRET_ACCESS_KEY": DUMMY, "AWS_SESSION_TOKEN": DUMMY, "ANTHROPIC_API_KEY": DUMMY,
        "NODE_OPTIONS": "--require /tmp/evil.js", "NODE_PATH": "/tmp", "PLAYWRIGHT_BROWSERS_PATH": "/elsewhere",
        "SWARM_FEATURE_MAP_RUNTIME": "/rt"})
    env = R.driver_env(_StubRuntime(tmp_path))
    assert {"PATH", "HOME", "LANG", "FONTCONFIG_FILE", "SYSTEMROOT", "WINDIR"} <= set(env)
    for k in ("AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "ANTHROPIC_API_KEY", "NODE_OPTIONS", "NODE_PATH",
              "SWARM_FEATURE_MAP_RUNTIME"):
        assert k not in env, k
    assert env["PLAYWRIGHT_BROWSERS_PATH"] == "/rt/browsers" and env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] == "1"
    assert env["MV_PLAYWRIGHT"].startswith("file:") and env["MV_PLAYWRIGHT"].endswith("/index.mjs")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="links the pinned node into a fake runtime folder")
def test_driver_load_failure_reason_reaches_the_user(pinned_browser, tmp_path):
    """the real driver on the real pinned Node, with a Playwright that cannot load: its own reason, one line, exit 2"""
    node = R.browser_runtime().node
    rt = tmp_path / "rt"
    (rt / "node" / "bin").mkdir(parents=True)
    os.symlink(node, rt / "node" / "bin" / "node")
    pkg = rt / "pw" / "node_modules" / "@playwright" / "test"
    pkg.mkdir(parents=True)
    (pkg / "cli.js").write_text("", encoding="utf-8")
    (pkg / "index.mjs").write_text("throw new Error('broken playwright package');\n", encoding="utf-8")
    root = str(tmp_path / "film")
    make_project(root)
    env = dict(no_playwright_env(tmp_path), SWARM_FEATURE_MAP_RUNTIME=str(rt))
    r = bundled_py("render.py", "--project", root, "--scene", "S01", "--preview", env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert r.stderr.strip() == ("error: cannot launch the pinned Chromium: the pinned Playwright could not be loaded: "
                                "broken playwright package")


FAKE_DRIVER = """import readline from 'node:readline';
process.stdout.write(JSON.stringify({ ready: true, version: 'fake' }) + '\\n');
for await (const line of readline.createInterface({ input: process.stdin })) process.stdout.write('[1]\\n');
"""


def test_broken_reply_fails_waiting_requests_at_once(pinned_browser, tmp_path, monkeypatch):
    """a reply that is JSON but not an object ends the reader as a dead driver: the request fails now, not after
    REQUEST_HANG_S"""
    fake = tmp_path / "fake_driver.mjs"
    fake.write_text(FAKE_DRIVER, encoding="utf-8")
    monkeypatch.setattr(R, "DRIVER", str(fake))
    monkeypatch.setattr(R, "REQUEST_HANG_S", 30)

    async def run(browser):
        assert browser.version == "fake"
        with pytest.raises(R.RenderError) as e:
            await asyncio.wait_for(browser.request("eval", page=1, expr="1"), 10)
        return str(e.value)

    msg = asyncio.run(R.with_browser(run))
    assert "the browser driver sent a broken reply" in msg and "'eval'" in msg


def test_browser_gate_never_installs_the_runtime(browser_gate, monkeypatch, tmp_path):
    """the tests' gate asks for the runtime with install=False (a first-use download would run into the per-test
    timeout); the install hint comes only when the runtime is simply not installed"""
    runtime = R.runtime_module()
    calls = []

    def fake_ensure(**kw):
        calls.append(kw)
        raise runtime.RuntimeUnavailable("the pinned runtime is not installed")

    monkeypatch.setattr(runtime, "ensure", fake_ensure)
    monkeypatch.setattr(runtime, "runtime_dir", lambda: tmp_path / "rt")
    monkeypatch.delenv("SWARM_FEATURE_MAP_RUNTIME", raising=False)
    monkeypatch.delitem(browser_gate._STATE, "why", raising=False)
    why = browser_gate.browser_unavailable()
    assert calls == [{"install": False}]
    assert why.startswith("the pinned browser runtime is not available here: the pinned runtime is not installed")
    assert "install it once with: cd backend && python -m core.feature_map.runtime" in why

    monkeypatch.setenv("SWARM_FEATURE_MAP_RUNTIME", str(tmp_path / "elsewhere"))   # installing would not help here
    monkeypatch.delitem(browser_gate._STATE, "why", raising=False)
    why = browser_gate.browser_unavailable()
    assert calls == [{"install": False}] * 2 and "install it once" not in why
