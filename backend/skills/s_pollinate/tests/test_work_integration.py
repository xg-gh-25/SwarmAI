"""work.py cut with the real craft_checks.py (P2) and export_pptx.py (P3) wired in.

The mechanism tests pin the built-in probe (test_work_common.env); these tests use the
real modules, so they prove the hooks of design 13.3 and 13.5 are joined:
- a slide the revision adds that the real fill check calls too empty fails the cut, and
  current does not move (a slide the person's adopted deck already had is carried as a
  warning, see test_work_smoke_fixes);
- the same slide marked data-layout="cover" passes the fill check;
- a craft check that cannot read a measure becomes a failed check, not a crash;
- cut --formats pptx makes out/deck.pptx with one slide per page;
- cut --template keeps the template's head slide and stores the choice for later cuts;
- cut --reference copies the reference measure into the work and judges against it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_work_common import env, meta, need_browser, run, work  # noqa: E402

__all__ = ["env"]

pptx = pytest.importorskip("pptx")
import craft_checks  # noqa: E402  (scripts/ is on sys.path via test_work_common)

FULL = ('<section{attr}>'
        '<h1 style="position:absolute;left:140px;top:120px;margin:0;font:600 96px Georgia">{title}</h1>'
        '<p data-role="body" style="position:absolute;left:140px;top:300px;width:760px;margin:0;font:40px/1.5 Helvetica">'
        'The left column carries the argument in two or three short lines of body text.</p>'
        '<p data-role="body" style="position:absolute;left:1020px;top:300px;width:760px;margin:0;font:40px/1.5 Helvetica">'
        'The right column carries the evidence, and it runs down to the lower part of the frame.</p>'
        '<p data-role="body" style="position:absolute;left:140px;top:820px;width:1640px;margin:0;font:40px/1.5 Helvetica">'
        'A closing line spans the width so the content fills the safe area of the slide.</p>'
        '</section>\n')
EMPTY = '<section{attr}><h1 style="font:600 96px Georgia;margin:120px">{title}</h1></section>\n'


def _deck(d: Path, slides: list[str]) -> Path:
    src = d / "src"
    src.mkdir(parents=True)
    (src / "shared.css").write_text(
        "body{margin:0}section{position:relative;width:1920px;height:1080px;overflow:hidden;"
        "background:#fff}\n", encoding="utf-8")
    (src / "deck.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="shared.css">'
        "</head><body>\n" + "".join(slides) + "</body></html>\n", encoding="utf-8")
    return d


def _adopt_and_cut(d: Path, capsys, *cut_args):
    code, out = run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "1920x1080"], capsys)
    assert code == 0, out
    run(["open", "--dir", str(d), "--scope", ""], capsys)
    return run(["cut", "--dir", str(d), *cut_args], capsys)


@pytest.fixture
def real_craft(env, monkeypatch):
    monkeypatch.setattr(work, "_load_craft", lambda: craft_checks)
    return env


def test_real_fill_check_fails_an_empty_slide_and_keeps_current(real_craft, capsys):
    need_browser()
    d = _deck(real_craft["tmp"] / "w", [FULL.format(attr="", title="Full")])
    code, out = run(["adopt", "--dir", str(d), "--kind", "deck", "--size", "1920x1080"], capsys)
    assert code == 0, out
    run(["open", "--dir", str(d), "--scope", "", "--add", "1"], capsys)
    p = d / "src" / "deck.html"     # the revision adds an empty slide
    p.write_text(p.read_text().replace("</body>", EMPTY.format(attr="", title="Empty") + "</body>"))
    code, out = run(["cut", "--dir", str(d), "--formats", "html,png"], capsys)
    assert code == 1
    assert "fill" in {f["check"] for f in out["failed_checks"]}
    assert meta(d)["current"] == "v001"        # the adopted version, not the failed cut


def test_cover_marked_slide_skips_fill(real_craft, capsys):
    need_browser()
    d = _deck(real_craft["tmp"] / "w", [FULL.format(attr="", title="Full"),
                                        EMPTY.format(attr=' data-layout="cover"', title="Cover")])
    code, out = _adopt_and_cut(d, capsys, "--formats", "html,png")
    assert "fill" not in {f["check"] for f in out["failed_checks"]}, out


def test_unreadable_measure_is_a_failed_check(real_craft, capsys, monkeypatch):
    need_browser()

    def broken(*_a, **_k):
        raise ValueError("measure has no texts list")
    monkeypatch.setattr(craft_checks, "evaluate", broken)
    d = _deck(real_craft["tmp"] / "w", [FULL.format(attr="", title="Full")])
    code, out = _adopt_and_cut(d, capsys, "--formats", "html,png")
    assert code == 1
    assert [f["check"] for f in out["failed_checks"]] == ["craft"]


def test_cut_exports_pptx_one_slide_per_page(env, capsys):
    need_browser()
    d = _deck(env["tmp"] / "w", [FULL.format(attr="", title=t) for t in ("A", "B", "C")])
    code, out = _adopt_and_cut(d, capsys, "--formats", "html,png,pptx")
    assert code == 0, out
    prs = pptx.Presentation(str(d / "out" / "deck.pptx"))
    assert len(prs.slides) == 3


def test_cut_template_keeps_head_and_is_remembered(env, capsys):
    need_browser()
    tpl = env["tmp"] / "brand.pptx"
    prs = pptx.Presentation()
    blank = prs.slide_layouts[6]
    for text in ("COVER", "BASE"):
        s = prs.slides.add_slide(blank)
        s.shapes.add_textbox(0, 0, prs.slide_width, prs.slide_height // 10).text_frame.text = text
    prs.save(str(tpl))
    d = _deck(env["tmp"] / "w", [FULL.format(attr="", title=t) for t in ("A", "B")])
    code, out = _adopt_and_cut(d, capsys, "--formats", "html,png,pptx", "--template", str(tpl),
                               "--keep-head", "1", "--base-index", "2", "--keep-tail", "0")
    assert code == 0, out
    res = pptx.Presentation(str(d / "out" / "deck.pptx"))
    assert len(res.slides) == 3
    assert res.slides[0].shapes[0].text_frame.text == "COVER"
    assert meta(d)["pptx"] == {"template": str(tpl.resolve()), "keep_head": 1, "base_index": 2, "keep_tail": 0}


def test_cut_template_flags_need_template(env, capsys):
    d = _deck(env["tmp"] / "w", [FULL.format(attr="", title="A")])
    code, _ = _adopt_and_cut(d, capsys, "--keep-head", "1")
    assert code == 2


def test_cut_reference_from_another_work(real_craft, capsys):
    need_browser()
    ref = _deck(real_craft["tmp"] / "ref", [FULL.format(attr="", title="Ref")])
    code, out = _adopt_and_cut(ref, capsys, "--formats", "html,png")
    assert code == 0, out
    d = _deck(real_craft["tmp"] / "w", [FULL.format(attr="", title="New")])
    code, out = _adopt_and_cut(d, capsys, "--formats", "html,png", "--reference", str(ref))
    assert meta(d)["reference"] == work.REFERENCE_COPY
    assert (d / work.REFERENCE_COPY).is_file()
    assert code == 0, out                      # same look as the reference: no drift
