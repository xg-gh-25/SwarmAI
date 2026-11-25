"""Tests for scripts/export_pptx.py (slide pictures to PPTX, optional template).

What must hold:
  * without a template: one slide per picture, filled by it, and the slide has the
    picture's aspect ratio;
  * with a template (N=2, I=3, M=2): 2 + pages + 2 slides; the kept cover and tail
    slides are byte-for-byte the template's (same XML, same text, same images); each
    content slide keeps the base slide's footer and logo and holds exactly one picture
    inside the content area, with the page's aspect ratio; no empty placeholder is
    left; the template file is never changed.

Everything is built in the test: PNGs with Pillow, the template with python-pptx.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

pptx = pytest.importorskip("pptx")
from lxml import etree  # noqa: E402
from PIL import Image  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.enum.shapes import MSO_SHAPE_TYPE  # noqa: E402
from pptx.util import Emu, Inches, Pt  # noqa: E402

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "scripts" / "export_pptx.py"
sys.path.insert(0, str(SKILL / "scripts"))

import export_pptx as ex  # noqa: E402

W169, H169 = 12192000, 6858000
COLORS = [(200, 40, 40), (40, 160, 60), (40, 70, 200), (220, 180, 30), (120, 30, 150)]


def make_pngs(folder: Path, n: int, size=(1920, 1080), ids=None) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        sid = ids[i] if ids else f"s-{i + 1:04x}"
        p = folder / f"{i + 1:02d}-{sid}.png"
        Image.new("RGB", size, COLORS[i % len(COLORS)]).save(p)
        paths.append(p)
    return paths


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pictures(slide):
    return [s for s in slide.shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]


def texts(slide):
    return [s.text_frame.text for s in slide.shapes if s.has_text_frame]


def signature(slide) -> bytes:
    return etree.tostring(slide._element, method="c14n")


def image_blobs(slide):
    return [hashlib.sha256(p.image.blob).hexdigest() for p in pictures(slide)]


def box(shape):
    return int(shape.left), int(shape.top), int(shape.width), int(shape.height)


def inside(inner, outer) -> bool:
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    return x >= ox - 1 and y >= oy - 1 and x + w <= ox + ow + 1 and y + h <= oy + oh + 1


def overlaps(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def build_template(path: Path, tmp: Path, base_layout: int = 6) -> dict:
    """2 covers, 1 base slide (footer text box + logo), 2 tail slides; 16:9."""
    logo = tmp / "logo.png"
    Image.new("RGB", (200, 100), (10, 10, 10)).save(logo)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(W169), Emu(H169)
    lay = prs.slide_layouts

    c1 = prs.slides.add_slide(lay[0])
    c1.shapes.title.text = "Standard Cover"
    c1.placeholders[1].text = "Company template, page 1"
    c1.shapes.add_picture(str(logo), Inches(11), Inches(0.3), Inches(2), Inches(1))
    c2 = prs.slides.add_slide(lay[5])
    c2.shapes.title.text = "Agenda"
    tb = c2.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(3))
    tb.text_frame.text = "Intro / Body / Close"

    base = prs.slides.add_slide(lay[base_layout])
    # notes first, so the logo's relationship id on the base (rId3) differs from the
    # one it gets on a copy (notes are not copied): the copy must remap the id
    base.notes_slide.notes_text_frame.text = "base slide notes"
    footer =base.shapes.add_textbox(Inches(0.5), Emu(H169 - Inches(0.6)), Inches(8),
                                     Inches(0.4))
    footer.text_frame.text = "ACME Confidential | footer"
    footer.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
    footer.name = "Footer Box"
    blogo = base.shapes.add_picture(str(logo), Inches(11.8), Inches(0.2), Inches(1.2),
                                    Inches(0.6))
    blogo.name = "Base Logo"

    t1 = prs.slides.add_slide(lay[5])
    t1.shapes.title.text = "Thank you"
    t2 = prs.slides.add_slide(lay[1])
    t2.shapes.title.text = "Appendix"
    t2.placeholders[1].text = "Original slide kept at the end"
    prs.save(str(path))
    return {"footer": box(footer), "logo": box(blogo), "footer_text": footer.text_frame.text}


# ---------------------------------------------------------------- no template


def test_no_template_one_full_slide_per_picture(tmp_path):
    pngs = make_pngs(tmp_path / "slides", 4)
    out = tmp_path / "out" / "deck.pptx"
    assert ex.main(["--slides", str(tmp_path / "slides"), "--out", str(out)]) == 0

    prs = Presentation(str(out))
    slides = list(prs.slides)
    assert len(slides) == 4
    sw, sh = int(prs.slide_width), int(prs.slide_height)
    assert abs(sw / sh - 1920 / 1080) < 0.001
    for slide, png in zip(slides, pngs):
        pics = pictures(slide)
        assert len(pics) == 1
        assert box(pics[0]) == pytest.approx((0, 0, sw, sh), abs=2)
        assert image_blobs(slide) == [hashlib.sha256(png.read_bytes()).hexdigest()]
        assert not list(slide.placeholders)


@pytest.mark.parametrize("size", [(1080, 1350), (1600, 1200)])
def test_no_template_slide_follows_png_aspect(tmp_path, size):
    make_pngs(tmp_path / "slides", 4, size=size)
    out = tmp_path / "deck.pptx"
    assert ex.main(["--slides", str(tmp_path / "slides"), "--out", str(out)]) == 0
    prs = Presentation(str(out))
    assert len(prs.slides) == 4
    assert abs(int(prs.slide_width) / int(prs.slide_height) - size[0] / size[1]) < 0.001


def test_no_template_page_order_follows_number_not_name(tmp_path):
    folder = tmp_path / "slides"
    pngs = make_pngs(folder, 3, ids=["s-ffff", "s-0001", "s-aaaa"])
    (folder / "notes.txt").write_text("not a slide")
    out = tmp_path / "deck.pptx"
    assert ex.main(["--slides", str(folder), "--out", str(out)]) == 0
    got = [image_blobs(s)[0] for s in Presentation(str(out)).slides]
    assert got == [hashlib.sha256(p.read_bytes()).hexdigest() for p in pngs]


# ---------------------------------------------------------------- template


def test_template_keeps_head_and_tail_and_fills_base(tmp_path):
    tpl = tmp_path / "brand.pptx"
    info = build_template(tpl, tmp_path)
    tpl_hash = sha(tpl)
    pngs = make_pngs(tmp_path / "slides", 3)
    out = tmp_path / "deck.pptx"

    code = ex.main(["--slides", str(tmp_path / "slides"), "--out", str(out),
                    "--template", str(tpl), "--keep-head", "2", "--base-index", "3",
                    "--keep-tail", "2"])
    assert code == 0
    assert sha(tpl) == tpl_hash, "the template file must never change"

    t = list(Presentation(str(tpl)).slides)
    prs = Presentation(str(out))
    o = list(prs.slides)
    assert len(o) == 2 + 3 + 2

    # cover and tail slides: identical shapes, text and images
    for got, want in [(o[0], t[0]), (o[1], t[1]), (o[5], t[3]), (o[6], t[4])]:
        assert signature(got) == signature(want)
        assert [s.name for s in got.shapes] == [s.name for s in want.shapes]
        assert texts(got) == texts(want)
        assert image_blobs(got) == image_blobs(want)
        assert got.slide_layout.name == want.slide_layout.name
    assert texts(o[6])[-1] == "Original slide kept at the end"

    # content slides: footer + logo kept, one picture in the content area
    sw, sh = int(prs.slide_width), int(prs.slide_height)
    fx, fy, fw, fh = info["footer"]
    lx, ly, lw, lh = info["logo"]
    content = (0, ly + lh, sw, fy - (ly + lh))
    logo_blob = image_blobs(t[2])[0]
    for slide, png in zip(o[2:5], pngs):
        names = {s.name: s for s in slide.shapes}
        assert "Footer Box" in names and "Base Logo" in names
        assert box(names["Footer Box"]) == info["footer"]
        assert names["Footer Box"].text_frame.text == info["footer_text"]
        assert hashlib.sha256(names["Base Logo"].image.blob).hexdigest() == logo_blob

        page = [p for p in pictures(slide) if p.name != "Base Logo"]
        assert len(page) == 1
        in_area = [p for p in pictures(slide) if inside(box(p), content)]
        assert in_area == page, "exactly one picture inside the content area"
        pb = box(page[0])
        assert not overlaps(pb, info["footer"]) and not overlaps(pb, info["logo"])
        assert abs(pb[2] / pb[3] - 1920 / 1080) < 0.01
        assert hashlib.sha256(page[0].image.blob).hexdigest() == sha(png)
        assert not list(slide.placeholders)


def test_template_base_with_placeholders_uses_largest_and_drops_empty(tmp_path):
    """A base slide made from 'Title and Content' has an empty title and body: the
    picture goes into the body's area and no 'Click to add text' is left."""
    tpl = tmp_path / "brand.pptx"
    build_template(tpl, tmp_path, base_layout=1)
    base = Presentation(str(tpl)).slides[2]
    body = [p for p in base.placeholders if p.placeholder_format.idx == 1][0]
    body_box = box(body)
    make_pngs(tmp_path / "slides", 2)
    out = tmp_path / "deck.pptx"
    assert ex.main(["--slides", str(tmp_path / "slides"), "--out", str(out),
                    "--template", str(tpl), "--keep-head", "2", "--base-index", "3",
                    "--keep-tail", "2"]) == 0
    o = list(Presentation(str(out)).slides)
    assert len(o) == 6
    for slide in o[2:4]:
        assert not list(slide.placeholders)
        assert "Footer Box" in {s.name for s in slide.shapes}
        page = [p for p in pictures(slide) if p.name != "Base Logo"]
        assert len(page) == 1 and inside(box(page[0]), body_box)


def test_template_without_tail_and_default_base(tmp_path):
    tpl = tmp_path / "brand.pptx"
    build_template(tpl, tmp_path)
    make_pngs(tmp_path / "slides", 2)
    out = tmp_path / "deck.pptx"
    # base defaults to the slide after the head (3); slides 3-5 are not kept
    assert ex.main(["--slides", str(tmp_path / "slides"), "--out", str(out),
                    "--template", str(tpl), "--keep-head", "2"]) == 0
    o = list(Presentation(str(out)).slides)
    assert len(o) == 4
    assert all("Footer Box" in {s.name for s in sl.shapes} for sl in o[2:])


# ---------------------------------------------------------------- usage errors


def test_usage_errors_exit_2(tmp_path, capsys):
    tpl = tmp_path / "brand.pptx"
    build_template(tpl, tmp_path)
    make_pngs(tmp_path / "slides", 1)
    s = str(tmp_path / "slides")
    out = str(tmp_path / "o.pptx")
    (tmp_path / "empty").mkdir()
    cases = [
        ["--slides", str(tmp_path / "empty"), "--out", out],
        ["--slides", s, "--out", out, "--keep-head", "1"],
        ["--slides", s, "--out", out, "--size", "wide"],
        ["--slides", s, "--out", out, "--template", str(tmp_path / "missing.pptx")],
        ["--slides", s, "--out", str(tpl), "--template", str(tpl)],
        ["--slides", s, "--out", out, "--template", str(tpl), "--base-index", "9"],
        ["--slides", s, "--out", out, "--template", str(tpl), "--keep-head", "4",
         "--keep-tail", "2"],
    ]
    before = sha(tpl)
    for argv in cases:
        assert ex.main(argv) == 2, argv
    assert sha(tpl) == before


def test_cli_json(tmp_path):
    make_pngs(tmp_path / "slides", 2)
    out = tmp_path / "deck.pptx"
    r = subprocess.run([sys.executable, str(SCRIPT), "--slides", str(tmp_path / "slides"),
                        "--out", str(out), "--json"], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["ok"] is True and data["slides"] == 2 and data["problems"] == []


SECTION_EXT = (
    '<p:ext xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'uri="{{521415D9-36F7-43E2-AB2F-B90AF26B5E84}}">'
    '<p14:sectionLst xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main">'
    '{sections}</p14:sectionLst></p:ext>')


def add_sections(path: Path, groups: list[tuple[str, list[int]]]) -> None:
    """Give the template PowerPoint sections over its slides (1-based positions)."""
    prs = Presentation(str(path))
    ids = [str(e.id) for e in prs.slides._sldIdLst]
    secs = "".join(
        f'<p14:section name="{name}" id="{{00000000-0000-0000-0000-00000000000{k}}}">'
        "<p14:sldIdLst>" + "".join(f'<p14:sldId id="{ids[i - 1]}"/>' for i in pos)
        + "</p14:sldIdLst></p14:section>" for k, (name, pos) in enumerate(groups))
    root = prs.part._element
    ns = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
    ext_lst = root.find(f"{ns}extLst")
    if ext_lst is None:
        ext_lst = etree.SubElement(root, f"{ns}extLst")
    ext_lst.append(etree.fromstring(SECTION_EXT.format(sections=secs)))
    prs.save(str(path))


def section_ids(prs) -> list[list[str]]:
    p14 = "{http://schemas.microsoft.com/office/powerpoint/2010/main}"
    return [[e.get("id") for e in sec.iter(f"{p14}sldId")]
            for sec in prs.part._element.iter(f"{p14}section")]


THREE = [("Cover", [1, 2]), ("Body", [3]), ("End", [4, 5])]
SPLIT = [("Title", [1]), ("Rest", [2, 3]), ("End", [4, 5])]


@pytest.mark.parametrize("head,tail,groups", [(2, 2, THREE), (0, 2, THREE), (2, 0, THREE),
                                              (2, 0, SPLIT)])
def test_template_sections_follow_the_new_slide_list(tmp_path, head, tail, groups):
    tpl = tmp_path / "brand.pptx"
    build_template(tpl, tmp_path)
    add_sections(tpl, groups)
    make_pngs(tmp_path / "slides", 3)
    out = tmp_path / "deck.pptx"
    assert ex.main(["--slides", str(tmp_path / "slides"), "--out", str(out),
                    "--template", str(tpl), "--keep-head", str(head), "--base-index", "3",
                    "--keep-tail", str(tail)]) == 0
    prs = Presentation(str(out))
    deck = [str(e.id) for e in prs.slides._sldIdLst]
    secs = section_ids(prs)
    assert len(deck) == head + 3 + tail
    assert [i for sec in secs for i in sec] == deck
    assert len(secs) == 3  # no section is removed, the body one may be empty


def test_template_custom_show_loses_only_dropped_slides(tmp_path):
    """A custom slide show that names a dropped template slide must not point at a
    relationship that no longer exists."""
    tpl = tmp_path / "brand.pptx"
    build_template(tpl, tmp_path)
    prs = Presentation(str(tpl))
    rids = [e.rId for e in prs.slides._sldIdLst]
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    show = etree.fromstring(
        f'<p:custShowLst xmlns:p="{p}" xmlns:r="{r}"><p:custShow name="Short" id="0">'
        f'<p:sldLst><p:sld r:id="{rids[0]}"/><p:sld r:id="{rids[2]}"/></p:sldLst>'
        "</p:custShow></p:custShowLst>")
    prs.part._element.find(f"{{{p}}}defaultTextStyle").addprevious(show)
    prs.save(str(tpl))
    make_pngs(tmp_path / "slides", 1)
    out = tmp_path / "deck.pptx"
    assert ex.main(["--slides", str(tmp_path / "slides"), "--out", str(out),
                    "--template", str(tpl), "--keep-head", "2", "--base-index", "3",
                    "--keep-tail", "2"]) == 0
    got = Presentation(str(out))
    refs = [e.get(f"{{{r}}}id") for e in got.part._element.iter(f"{{{p}}}sld")]
    assert len(refs) == 1
    assert got.part.related_part(refs[0]) is got.slides[0].part


# ---------------------------------------------------------------- review fixes


def assert_links_resolve(prs) -> None:
    """Every relationship id a slide's XML names exists in that slide's relationships."""
    r = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    for n, slide in enumerate(prs.slides, start=1):
        rels = slide.part.rels
        for el in slide._element.iter():
            for k, v in el.attrib.items():
                if k.startswith(r):
                    assert v in rels, f"slide {n}: {k} -> {v} has no relationship"


def slide_parts_in_package(prs) -> set:
    return {p for p in prs.part.package.iter_parts()
            if str(p.partname).startswith("/ppt/slides/slide")}


def run_template(tmp_path, tpl, n_pages=2, extra=()):
    make_pngs(tmp_path / "slides", n_pages)
    out = tmp_path / "deck.pptx"
    code = ex.main(["--slides", str(tmp_path / "slides"), "--out", str(out),
                    "--template", str(tpl), "--keep-head", "2", "--base-index", "3",
                    "--keep-tail", "2", *extra])
    return code, out


def test_base_sample_text_in_body_placeholder_is_replaced(tmp_path, capsys):
    tpl = tmp_path / "brand.pptx"
    build_template(tpl, tmp_path, base_layout=1)
    prs = Presentation(str(tpl))
    body = [p for p in prs.slides[2].placeholders if p.placeholder_format.idx == 1][0]
    body.text = "Sample body text"
    body_box = box(body)
    prs.save(str(tpl))
    code, out = run_template(tmp_path, tpl)
    assert code == 0
    assert "sample content" in capsys.readouterr().out
    for slide in list(Presentation(str(out)).slides)[2:4]:
        assert "Sample body text" not in texts(slide)
        assert not list(slide.placeholders)
        page = [p for p in pictures(slide) if p.name != "Base Logo"]
        assert len(page) == 1 and inside(box(page[0]), body_box)


def test_links_to_dropped_slides(tmp_path, capsys):
    """A kept slide that links to a dropped one stays unchanged (with a note); the
    base's link to a dropped slide is not copied; its link to a kept slide is."""
    tpl = tmp_path / "brand.pptx"
    build_template(tpl, tmp_path)
    prs = Presentation(str(tpl))
    s = list(prs.slides)
    s[0].shapes[0].click_action.target_slide = s[2]          # cover -> base (dropped)
    to_self = s[2].shapes.add_textbox(Inches(1), Inches(1), Inches(1), Inches(0.3))
    to_self.click_action.target_slide = s[2]                  # base -> itself (dropped)
    to_end = s[2].shapes.add_textbox(Inches(3), Inches(1), Inches(1), Inches(0.3))
    to_end.name = "To End"
    to_end.click_action.target_slide = s[4]                   # base -> tail (kept)
    prs.save(str(tpl))

    code, out = run_template(tmp_path, tpl)
    assert code == 0
    printed = capsys.readouterr().out
    assert "kept template slide 1 links to template slide 3" in printed
    got = Presentation(str(out))
    o = list(got.slides)
    t = list(Presentation(str(tpl)).slides)
    assert signature(o[0]) == signature(t[0])
    assert_links_resolve(got)
    for slide in o[2:4]:
        targets = [r.target_part for r in slide.part.rels.values() if r.reltype.endswith("/slide")]
        assert targets == [o[-1].part]
        link = {sh.name: sh for sh in slide.shapes}["To End"].click_action
        assert link.target_slide.part is o[-1].part


def test_chart_on_base_is_not_shared(tmp_path, capsys):
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE

    tpl = tmp_path / "brand.pptx"
    build_template(tpl, tmp_path)
    prs = Presentation(str(tpl))
    data = CategoryChartData()
    data.categories = ["a", "b"]
    data.add_series("s", (1, 2))
    prs.slides[2].shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1),
                                   Inches(3), Inches(2), data)
    prs.save(str(tpl))
    code, out = run_template(tmp_path, tpl)
    assert code == 0
    assert "chart part" in capsys.readouterr().out
    got = Presentation(str(out))
    assert_links_resolve(got)
    for slide in list(got.slides)[2:4]:
        assert not any(sh.has_chart for sh in slide.shapes)
        assert not [r for r in slide.part.rels.values() if r.reltype.endswith("/chart")]
        assert "Footer Box" in {sh.name for sh in slide.shapes}


def test_main_template_result_has_whole_links_and_no_dropped_slide(tmp_path):
    tpl = tmp_path / "brand.pptx"
    build_template(tpl, tmp_path)
    code, out = run_template(tmp_path, tpl, n_pages=3)
    assert code == 0
    got = Presentation(str(out))
    assert_links_resolve(got)
    assert slide_parts_in_package(got) == {sl.part for sl in got.slides}


def test_environment_errors_exit_2_and_leave_no_temp_file(tmp_path, capsys):
    tpl = tmp_path / "brand.pptx"
    build_template(tpl, tmp_path)
    make_pngs(tmp_path / "slides", 1)
    folder = tmp_path / "is_a_folder.pptx"
    folder.mkdir()
    assert ex.main(["--slides", str(tmp_path / "slides"), "--out", str(folder)]) == 2
    # every slide kept as head and no --base-index: say what is missing
    assert ex.main(["--slides", str(tmp_path / "slides"), "--out", str(tmp_path / "o.pptx"),
                    "--template", str(tpl), "--keep-head", "5"]) == 2
    assert "give --base-index" in capsys.readouterr().err
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
