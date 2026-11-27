"""On a machine without the macOS families, CJK and Latin text in a style renders with the bundled faces.

Needs SwarmAI's pinned browser runtime, driven the way render.py drives it (conftest.py: skipped without it unless
MV_REQUIRE_BROWSER=1; always skipped on macOS, whose own families come first by design).
Each page is served like render.py serves scenes (/skill/references/styles/<style>.css), and Chromium's own report
(CSS.getPlatformFontsForNode) says which font drew the glyphs.
Run: python -m pytest skills/s_motion-video/tests/test_fonts_render.py -q   (from backend/)"""
import asyncio
import http.server
import os
import sys
import threading

import pytest

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import render  # noqa: E402
pytestmark = pytest.mark.skipif(sys.platform == "darwin", reason="macOS renders its own families first by design")

# style -> [(css font value, text, family the glyphs must come from)]
CASES = {
    "exec-brief": [("600 60px var(--eb-serif)", "云成本下降四成", "Noto Serif SC"),
                   ("400 40px var(--eb-sans)", "规格调整与竞价实例", "Noto Sans SC")],
    "ink-wash": [("700 80px var(--iw-brush)", "山水之间", "LXGW WenKai"),
                 ("400 40px var(--iw-kai)", "一笔一画", "LXGW WenKai"),
                 ("900 40px var(--iw-song)", "印章", "Noto Serif SC")],
    "blueprint": [("400 50px var(--bp-hand)", "VECTOR INDEX", "Patrick Hand"),
                  ("400 50px var(--bp-hand)", "向量索引", "LXGW WenKai"),
                  ("700 40px var(--bp-tech)", "FIG 2", "Roboto Condensed")],
    "game-show": [("900 70px var(--gs-black)", "第一轮答对了", "Noto Sans SC"),
                  ("700 70px var(--gs-round)", "ROUND 1", "Nunito")],
}


class _Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/page/"):
            style = self.path.split("/")[2]
            spans = "".join(f'<div id="c{i}" style="font:{font}">{text}</div>' for i, (font, text, _) in enumerate(CASES[style]))
            body = (f'<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/skill/references/styles/{style}.css">'
                    f"<body>{spans}</body>").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def translate_path(self, path):
        rel = path.split("?")[0][len("/skill/"):]
        return os.path.join(SKILL, *rel.split("/"))

    def log_message(self, *a):
        pass


async def _fonts():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/"
    out = {}

    async def run(browser):
        async def call(op, **f):
            return (await browser.request(op, **f))[0].get("value")

        for style in CASES:
            page = (await call("open", w=1280, h=720, base=base, url=f"{base}page/{style}", init="", timeout=20000))["page"]
            await call("eval", page=page, expr="document.fonts.ready.then(() => true)")
            await call("cdp", page=page, method="DOM.enable")
            await call("cdp", page=page, method="CSS.enable")
            root = (await call("cdp", page=page, method="DOM.getDocument", params={"depth": -1}))["root"]["nodeId"]
            for i in range(len(CASES[style])):
                node = (await call("cdp", page=page, method="DOM.querySelector", params={"nodeId": root, "selector": f"#c{i}"}))["nodeId"]
                fonts = (await call("cdp", page=page, method="CSS.getPlatformFontsForNode", params={"nodeId": node}))["fonts"]
                out[(style, i)] = [f["familyName"] for f in fonts]
            await call("close", page=page)

    try:
        await render.with_browser(run)
    finally:
        srv.shutdown()
    return out


def test_cjk_renders_in_bundled_family(pinned_browser):
    used = asyncio.run(_fonts())
    bad = []
    for style, cases in CASES.items():
        for i, (_, text, family) in enumerate(cases):
            fams = used[(style, i)]
            if not fams or not all(f.startswith(family) for f in fams):
                bad.append(f"{style} {text!r}: drawn with {fams}, expected {family}")
    assert not bad, "\n".join(bad)
