"""Shared helpers for the work.py tests (no tests in this file).

The tests never use the network: ``fake_http`` stands in for the font
download, serving the committed test font for ``https://fonts.example.test/``.
Tests that need Chromium skip when Playwright or Chromium cannot start.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "work"
DECK5 = FIXTURES / "deck5"
FONT = (FIXTURES / "fonts" / "test-blocks.ttf").read_bytes()
FONT_CSS_URL = "https://fonts.example.test/css2?family=Pollinate+Test+Blocks"
FONT_FILE_URL = "https://fonts.example.test/blocks.ttf"

sys.path.insert(0, str(SCRIPTS))
import work  # noqa: E402
import work_render  # noqa: E402


class FakeHttp:
    """Serves the test font CSS and font file; counts every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url == FONT_CSS_URL:
            return (b"@font-face{font-family:'Pollinate Test Blocks';"
                    b"src:url(/blocks.ttf) format('truetype');}")
        if url == FONT_FILE_URL:
            return FONT
        raise work_render.FetchError(f"test server has no {url}")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A private workspace and font cache, and no network."""
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("POLLINATE_FONT_CACHE", str(tmp_path / "font-cache"))
    fake = FakeHttp()
    monkeypatch.setattr(work_render, "http_get", fake)
    return {"tmp": tmp_path, "http": fake}


@functools.lru_cache(maxsize=1)
def browser_problem():
    try:
        b = work_render._Browser()
    except work_render.RenderEnvError as exc:
        return str(exc)
    b.close()
    return None


def need_browser():
    problem = browser_problem()
    if problem:
        pytest.skip(f"Chromium cannot run here: {problem}")


def run(argv, capsys):
    """Run work.py in process; return (exit code, parsed stdout JSON or None)."""
    capsys.readouterr()
    code = work.main(argv)
    out = capsys.readouterr().out
    try:
        return code, json.loads(out)
    except json.JSONDecodeError:
        return code, None


def copy_fixture(dest: Path) -> Path:
    shutil.copytree(DECK5, dest)
    return dest


SMALL_SLIDES = ["Alpha", "Bravo", "Charlie", "Delta"]


def make_small_deck(dest: Path) -> Path:
    """A 4-slide deck at 320x180 without ids (quick to render)."""
    src = dest / "src"
    src.mkdir(parents=True)
    (src / "shared.css").write_text(
        "body{margin:0}section{width:320px;height:180px;box-sizing:border-box;padding:16px;"
        "background:#eef;font:12px sans-serif;overflow:hidden}h2{margin:0 0 8px;font-size:18px}\n", encoding="utf-8")
    body = "".join(f'<section class="s{i}">\n  <h2>{t}</h2>\n  <p>Slide {t} text</p>\n</section>\n'
                   for i, t in enumerate(SMALL_SLIDES))
    (src / "deck.html").write_text(
        '<!doctype html>\n<html><head><meta charset="utf-8"><title>Small</title>\n'
        f'<link rel="stylesheet" href="shared.css"></head>\n<body>\n{body}</body></html>\n',
        encoding="utf-8")
    return dest


def adopt_small(dest: Path, capsys) -> list:
    make_small_deck(dest)
    code, out = run(["adopt", "--dir", str(dest), "--kind", "deck", "--size", "320x180"], capsys)
    assert code == 0, out
    return out["slides"]


def deck_text(d: Path) -> str:
    return (d / "src" / "deck.html").read_text(encoding="utf-8")


def set_deck_text(d: Path, text: str) -> None:
    (d / "src" / "deck.html").write_text(text, encoding="utf-8")


def section_of(text: str, sid: str) -> str:
    m = re.search(r'<section data-slide-id="%s".*?</section>\n' % re.escape(sid), text, re.S)
    assert m, sid
    return m.group(0)


def replace_in_slide(d: Path, sid: str, old: str, new: str) -> None:
    text = deck_text(d)
    sec = section_of(text, sid)
    assert old in sec
    set_deck_text(d, text.replace(sec, sec.replace(old, new)))


def meta(d: Path) -> dict:
    return json.loads((d / "work.json").read_text(encoding="utf-8"))


def version(d: Path, vid: str) -> dict:
    return next(v for v in meta(d)["versions"] if v["id"] == vid)


def tree_hashes(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}
