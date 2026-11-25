"""The renderer on the committed fixture deck (design section 11 item 1, without PPTX).

The fixture has 5 slides; slide 2 uses a linked web font (served by the test's
fake download) and slide 4 an image. A cut must give a self-contained
deck.html whose every slide shows with JavaScript off, a 5-page PDF, 5 PNGs at
the canvas size, a contact sheet, and measure.json in the 13.3 shape.
"""
from __future__ import annotations

import json
import re
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import (  # noqa: E402
    FONT_CSS_URL, FONT_FILE_URL, copy_fixture, env, meta, need_browser, run, work, work_render,
)

__all__ = ["env"]


def _cut_fixture(env, capsys):
    need_browser()
    d = copy_fixture(env["tmp"] / "deck")
    code, out = run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "960x540"], capsys)
    assert code == 0
    run(["open", "--dir", str(d), "--scope", ""], capsys)
    code, out = run(["cut", "--dir", str(d), "--formats", "html,pdf,png"], capsys)
    assert code == 0, out
    return d, meta(d)["slides"]


def test_fixture_deck_renders_all_outputs(env, capsys):
    d, ids = _cut_fixture(env, capsys)
    out = d / "out"
    html = (out / "deck.html").read_text(encoding="utf-8")
    # self-contained: no link to a stylesheet, image or font outside the file
    assert not re.search(r"""(?<![\w-])(?:src|href)\s*=\s*["'](?!data:|#)""", html)
    assert "url(http" not in html
    without_notes = re.sub(r'data-pollinate-from="[^"]*"', "", html)
    assert "fonts.example.test" not in without_notes and "shared.css" not in without_notes
    assert html.count("data:font/ttf;base64,") == 1 and html.count("data:image/png;base64,") == 1
    # every slide visible with JavaScript off
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 960, "height": 540}, java_script_enabled=False)
        ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(("file:", "data:")) else r.abort())
        page = ctx.new_page()
        page.goto((out / "deck.html").as_uri())
        sections = page.locator("section[data-slide-id]")
        assert sections.count() == 5
        for i in range(5):
            img = Image.open(BytesIO(sections.nth(i).screenshot())).convert("L")
            lo, hi = img.getextrema()
            assert hi - lo > 40, f"slide {i + 1} is blank with JavaScript off"
        browser.close()
    # PDF: one page per slide
    from pypdf import PdfReader
    assert len(PdfReader(str(out / "deck.pdf")).pages) == 5
    # 5 PNGs at the canvas size, in order, named NN-<id>
    pngs = sorted((out / "slides").glob("*.png"))
    assert [p.name for p in pngs] == [f"{n:02d}-{i}.png" for n, i in enumerate(ids, 1)]
    assert all(Image.open(p).size == (960, 540) for p in pngs)
    assert (d / "review" / "sheet.jpg").is_file()
    assert Image.open(d / "review" / "sheet.jpg").format == "JPEG"
    # font cache: CSS and font downloaded once, rewritten to local files
    cache = env["tmp"] / "font-cache"
    styles = list(cache.glob("*/style.css"))
    assert len(styles) == 1
    local_css = styles[0].read_text()
    assert "https://" not in local_css and re.search(r'url\("000-[0-9a-f]{12}\.ttf"\)', local_css)
    assert env["http"].calls == [FONT_CSS_URL, FONT_FILE_URL]
    # checks and measure
    snap = d / ".versions" / "v002"
    checks = json.loads((snap / "checks.json").read_text())
    assert checks["ok"] is True
    measure = json.loads((snap / "measure.json").read_text())
    assert measure["canvas"] == {"w": 960, "h": 540}
    assert [m["slide"] for m in measure["slides"]] == ids
    first = measure["slides"][0]
    assert first["w"] == 960 and first["h"] == 540
    t = first["texts"][0]
    assert set(t) >= {"text", "role", "font_px", "lines", "nowrap", "box", "container", "chars"}
    assert t["role"] == "title" and t["text"] == "Fixture deck"
    assert measure["slides"][3]["images"][0]["loaded"] is True


def test_web_font_slide_uses_the_font(env, capsys):
    """Slide 2 drawn with the cached font differs from the same slide with the font missing."""
    d, ids = _cut_fixture(env, capsys)
    with_font = (d / "out" / "slides" / f"02-{ids[1]}.png").read_bytes()
    css = next((env["tmp"] / "font-cache").glob("*/style.css"))
    for f in css.parent.glob("*.ttf"):
        f.write_bytes(b"not a font")
    out2 = env["tmp"] / "again"
    code, res = run(["render", "--dir", str(d), "--version", "v002", "--out", str(out2)], capsys)
    assert code == 1  # the broken font is reported
    assert any(f["check"] == "font_load" for f in res["failed_checks"])
    assert (out2 / "slides" / f"02-{ids[1]}.png").read_bytes() != with_font


def test_second_render_reads_the_cache(env, capsys):
    d, ids = _cut_fixture(env, capsys)
    env["http"].calls.clear()
    code, _ = run(["render", "--dir", str(d)], capsys)
    assert code == 0 and env["http"].calls == []


def test_remote_request_blocked(env, capsys):
    """An image from the web is not loaded during capture; the cut fails and says why."""
    need_browser()
    d = env["tmp"] / "remote"
    (d / "src").mkdir(parents=True)
    (d / "src" / "deck.html").write_text(
        '<html><head><style>section{width:320px;height:180px}</style></head><body>'
        '<section><h2>Hi</h2><img src="https://images.example.test/x.png"></section></body></html>')
    code, out = run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "320x180"], capsys)
    run(["open", "--dir", str(d), "--scope", ""], capsys)
    code, out = run(["cut", "--dir", str(d), "--formats", "png"], capsys)
    assert code == 1 and out["guard"]["ok"] is True
    blocked = [f for f in out["failed_checks"] if f["check"] == "blocked_request"]
    assert [f["where"] for f in blocked] == ["https://images.example.test/x.png"]
    assert env["http"].calls == []


def test_builtin_probe_reports_overflow(env, capsys, monkeypatch):
    """Without craft_checks.py the built-in probe still fails text that leaves the slide."""
    need_browser()
    monkeypatch.setattr(work, "_load_craft", lambda: None)
    d = env["tmp"] / "over"
    (d / "src").mkdir(parents=True)
    (d / "src" / "deck.html").write_text(
        '<html><head><style>section{width:320px;height:180px;position:relative}'
        'p{position:absolute;left:300px;top:10px;white-space:nowrap;margin:0}</style></head><body>'
        '<section><p>This line runs off the right edge</p></section></body></html>')
    run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "320x180"], capsys)
    run(["open", "--dir", str(d), "--scope", ""], capsys)
    code, out = run(["cut", "--dir", str(d), "--formats", "png"], capsys)
    assert json.loads((d / ".versions" / "v002" / "checks.json").read_text())["probe"] == "builtin"
    assert code == 1
    assert [f["check"] for f in out["failed_checks"]] == ["overflow"]


def test_poster_renders_png_and_sizes(env, capsys):
    need_browser()
    d = env["tmp"] / "poster"
    run(["init", "--dir", str(d), "--kind", "poster", "--title", "P", "--size", "300x400"], capsys)
    (d / "src" / "index.html").write_text(
        "<html><body style='margin:0;background:#123;color:#fff'><h1>Poster</h1></body></html>")
    m = meta(d)
    m["sizes"] = [{"w": 200, "h": 200}]
    (d / "work.json").write_text(json.dumps(m))
    code, out = run(["cut", "--dir", str(d)], capsys)
    assert code == 0, out
    assert Image.open(d / "out" / "poster.png").size == (300, 400)
    assert Image.open(d / "out" / "poster@200x200.png").size == (200, 200)
    assert not list((d / "out").glob(".*"))


def test_cli_reports_missing_playwright(monkeypatch, tmp_path):
    """Without Playwright the renderer raises an environment error (exit 2), not a crash."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    src = tmp_path / "d.html"
    src.write_text("<section>x</section>")
    assert work_render.main(["--src", str(src), "--out", str(tmp_path / "o")]) == 2


def test_slides_hidden_until_script_fail_js_off_check(env, capsys):
    """A deck that shows its slides only after a script runs is blank with JavaScript off."""
    need_browser()
    d = env["tmp"] / "jsdeck"
    (d / "src").mkdir(parents=True)
    (d / "src" / "deck.html").write_text(
        '<html><head><style>section{width:320px;height:180px;background:#fff}'
        'section h2{visibility:hidden}.ready section h2{visibility:visible}</style></head>'
        '<body><section><h2>Hidden</h2></section>'
        '<script>document.body.classList.add("ready")</script></body></html>')
    run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "320x180"], capsys)
    run(["open", "--dir", str(d), "--scope", ""], capsys)
    code, out = run(["cut", "--dir", str(d), "--formats", "html,png"], capsys)
    assert code == 1
    assert [f["check"] for f in out["failed_checks"]] == ["js_off_blank"]


def test_script_cannot_load_local_files(env, capsys, tmp_path):
    """A script that adds an iframe to a file outside the work gets nothing into the slide."""
    need_browser()
    secret = tmp_path / "secret.html"
    secret.write_text('<body style="margin:0;background:#ff0000">SECRET</body>')
    d = env["tmp"] / "leak"
    (d / "src").mkdir(parents=True)
    (d / "src" / "deck.html").write_text(
        '<html><head><style>section{width:320px;height:180px;background:#fff}'
        'iframe{width:320px;height:180px;border:0}</style></head><body><section></section>'
        f'<script>const f=document.createElement("iframe");f.src={json.dumps(secret.as_uri())};'
        'document.querySelector("section").appendChild(f);</script></body></html>')
    run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "320x180"], capsys)
    run(["open", "--dir", str(d), "--scope", ""], capsys)
    code, out = run(["cut", "--dir", str(d), "--formats", "png"], capsys)
    assert code == 1
    assert [f["where"] for f in out["failed_checks"] if f["check"] == "blocked_request"] == [secret.as_uri()]
    png = next((d / "review").glob("failed-*/slides/*.png"))
    assert Image.open(png).convert("RGB").getpixel((160, 90)) != (255, 0, 0)
