#!/usr/bin/env python3
"""Export a rendered deck (one PNG per slide) to a PPTX file, with or without a template.

Input: a folder of slide pictures named ``NN-<id>.png`` (NN starts at 01, in page
order), as ``work.py`` writes them to ``out/slides/``.

Without a template, every page becomes one slide that is filled by its picture. The
slide size follows the picture's aspect ratio (or ``--size``).

With a template (``--template T.pptx``):
  * the first N template slides (``--keep-head``) and the last M (``--keep-tail``)
    are kept exactly as they are: their shapes and text are never changed;
  * for each page, template slide I (``--base-index``, 1-based) is copied as the
    base, so its footer, logo and other shapes stay; the picture goes into the base's
    content area (its largest empty content placeholder, or else the slide area
    without the header and footer shapes), keeping the picture's aspect ratio;
  * empty placeholders on the new content slides are removed, so no
    "Click to add text" shows;
  * the other template slides (between head and tail, the base included) are not
    in the result; PowerPoint sections and custom shows are updated to match, and
    the template file itself is never written.
  * charts, media, embedded objects and comments on the base slide, and links from
    it to slides that leave the deck, are not copied (each slide must own its own;
    the result notes it). Kept template slides are never changed, even when they
    link to a slide that leaves the deck (the result notes that too).

After writing, the script opens the result again and checks it: slide count, kept
slides unchanged, one picture per content page, no empty placeholder left, sections
in deck order.

Usage:
  export_pptx.py --slides out/slides --out out/deck.pptx [--size 1920x1080]
  export_pptx.py --slides out/slides --out out/deck.pptx \
      --template brand.pptx --keep-head 2 --base-index 3 --keep-tail 2

Exit codes: 0 = written and checked, 1 = the result failed its check,
2 = usage or environment error (bad arguments, missing files, no python-pptx).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT
    from pptx.util import Emu
except ImportError as exc:  # pragma: no cover - environment problem
    print(f"export_pptx: python-pptx and Pillow are needed ({exc}). "
          "Install them with: python3 -m pip install --user python-pptx", file=sys.stderr)
    sys.exit(2)

SLIDE_NAME_RE = re.compile(r"^(\d+)-(.+)\.png$", re.IGNORECASE)
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"  # slide sections

# The long side of a slide without a template: 13.333 in, the standard 16:9 width.
LONG_SIDE_EMU = 12192000
MIN_SIDE_EMU = 914400        # 1 in, the smallest slide side PowerPoint allows
MAX_SIDE_EMU = 51206400      # 56 in, the largest

# Placeholder types that can hold the page picture.
CONTENT_PH = {
    PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT, PP_PLACEHOLDER.PICTURE,
    PP_PLACEHOLDER.CHART, PP_PLACEHOLDER.TABLE, PP_PLACEHOLDER.ORG_CHART,
    PP_PLACEHOLDER.MEDIA_CLIP, PP_PLACEHOLDER.BITMAP, PP_PLACEHOLDER.VERTICAL_BODY,
    PP_PLACEHOLDER.VERTICAL_OBJECT,
}
# Relationships of a slide that belong to that slide alone and are not copied.
SKIP_RELS = {RT.SLIDE_LAYOUT, RT.NOTES_SLIDE}
# Relationships a copy of the base slide shares with it. Anything else (a chart,
# media, an embedded object, comments) belongs to one slide and is not copied.
SHARED_OK_RELS = SKIP_RELS | {RT.IMAGE, RT.HYPERLINK, RT.SLIDE, RT.TAGS}


class UsageError(Exception):
    """Bad arguments or input files (exit 2)."""


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def contains(self, other: "Box", slack: int = 1) -> bool:
        return (other.x >= self.x - slack and other.y >= self.y - slack
                and other.x + other.w <= self.x + self.w + slack
                and other.bottom <= self.bottom + slack)


# ---------------------------------------------------------------- input


def find_slides(folder: Path) -> list[Path]:
    """Return the ``NN-<id>.png`` files of ``folder`` in page order."""
    if not folder.is_dir():
        raise UsageError(f"slides folder not found: {folder}")
    found = []
    for p in folder.iterdir():
        m = SLIDE_NAME_RE.match(p.name)
        if m and p.is_file():
            found.append((int(m.group(1)), p.name, p))
    if not found:
        raise UsageError(f"no slide pictures named NN-<id>.png in {folder}")
    found.sort()
    return [p for _, _, p in found]


def image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception as exc:  # noqa: BLE001 - any unreadable file is a usage error
        raise UsageError(f"cannot read picture {path}: {exc}") from exc
    if w <= 0 or h <= 0:
        raise UsageError(f"picture has no size: {path}")
    return w, h


def parse_size(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", text or "")
    if not m or int(m.group(1)) == 0 or int(m.group(2)) == 0:
        raise UsageError(f"--size must look like 1920x1080, got {text!r}")
    return int(m.group(1)), int(m.group(2))


def slide_emu(w: int, h: int) -> tuple[int, int]:
    """Slide width and height in EMU for a w:h canvas (long side = 13.333 in)."""
    if w >= h:
        sw, sh = LONG_SIDE_EMU, round(LONG_SIDE_EMU * h / w)
    else:
        sw, sh = round(LONG_SIDE_EMU * w / h), LONG_SIDE_EMU
    if min(sw, sh) < MIN_SIDE_EMU:
        raise UsageError(f"aspect ratio {w}x{h} is too extreme for a slide "
                         "(the short side would be under 1 inch)")
    return sw, sh


def fit(img_w: int, img_h: int, area: Box) -> Box:
    """The largest box with the picture's aspect ratio, centred in ``area``."""
    scale = min(area.w / img_w, area.h / img_h)
    w, h = int(img_w * scale), int(img_h * scale)
    return Box(area.x + (area.w - w) // 2, area.y + (area.h - h) // 2, w, h)


# ---------------------------------------------------------------- no template


def build_plain(pictures: list[Path], size: tuple[int, int] | None, notes: list[str]):
    canvas = size or image_size(pictures[0])
    sw, sh = slide_emu(*canvas)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(sw), Emu(sh)
    layout = min(prs.slide_layouts, key=lambda lay: len(lay.placeholders))
    full = Box(0, 0, sw, sh)
    for pic in pictures:
        iw, ih = image_size(pic)
        if abs(iw / ih - canvas[0] / canvas[1]) > 0.01 * canvas[0] / canvas[1]:
            notes.append(f"{pic.name}: aspect {iw}x{ih} differs from the slide "
                         f"{canvas[0]}x{canvas[1]}; it is fitted inside, not stretched")
        slide = prs.slides.add_slide(layout)
        for ph in list(slide.placeholders):
            ph._element.getparent().remove(ph._element)
        b = fit(iw, ih, full)
        slide.shapes.add_picture(str(pic), Emu(b.x), Emu(b.y), Emu(b.w), Emu(b.h))
    return prs


# ---------------------------------------------------------------- template


def _box(shape) -> Box | None:
    try:
        x, y, w, h = shape.left, shape.top, shape.width, shape.height
    except Exception:  # noqa: BLE001 - a placeholder with nothing to inherit from
        return None
    if None in (x, y, w, h):
        return None
    return Box(int(x), int(y), int(w), int(h))


def _ph_type(shape):
    try:
        return shape.placeholder_format.type
    except Exception:  # noqa: BLE001
        return None


def _is_empty_placeholder(shape) -> bool:
    """A text placeholder with no text and no field: it shows only a prompt."""
    if not shape.is_placeholder:
        return False
    el = shape._element
    if el.tag != f"{{{P_NS}}}sp":
        return False  # a picture, table or chart placeholder has content
    if el.findall(f".//{{{A_NS}}}fld"):
        return False  # slide number, date: a field that shows a value
    text = shape.text_frame.text if shape.has_text_frame else ""
    return not text.strip()


def _shows_master_shapes(*cslds_owners) -> bool:
    return all(o._element.get("showMasterSp") not in ("0", "false") for o in cslds_owners)


def content_area(base, slide_w: int, slide_h: int, notes: list[str]) -> tuple[Box, object | None]:
    """Where the picture goes on a copy of ``base``, and the placeholder it replaces.

    1. The largest content placeholder (body, object, picture, ...) on the base
       slide; sample content in it is replaced by the picture.
    2. Else the slide without the header and footer shapes: every visible shape of
       the slide, its layout and (when shown) its master counts, except very large
       ones (backgrounds and side bars). A shape whose centre is in the upper half
       is header, in the lower half footer.
    """
    candidates = []
    for shape in base.shapes:
        if shape.is_placeholder and _ph_type(shape) in CONTENT_PH:
            b = _box(shape)
            if b and b.w > 0 and b.h > 0:
                candidates.append((b.w * b.h, b, shape))
    if candidates:
        _, b, shape = max(candidates, key=lambda c: c[0])
        if not _is_empty_placeholder(shape):
            notes.append(f"the content placeholder '{shape.name}' on the base slide has "
                         "sample content; the picture replaces it on every page")
        return b, shape

    chrome = [s for s in base.shapes if not _is_empty_placeholder(s)]
    layout = base.slide_layout
    chrome += [s for s in layout.shapes if not s.is_placeholder]
    if _shows_master_shapes(base, layout):
        chrome += [s for s in layout.slide_master.shapes if not s.is_placeholder]
    top, bottom = 0, slide_h
    for s in chrome:
        b = _box(s)
        if b is None or b.h > 0.5 * slide_h or b.w * b.h > 0.5 * slide_w * slide_h:
            continue
        if b.y + b.h / 2 < slide_h / 2:
            top = max(top, b.bottom)
        else:
            bottom = min(bottom, b.y)
    pad = int(0.04 * slide_h)
    area = Box(pad, top + pad, slide_w - 2 * pad, bottom - top - 2 * pad)
    if area.h < 0.25 * slide_h or area.w <= 0:
        notes.append("the base slide leaves little room between header and footer; "
                     "the picture uses the whole slide minus a margin")
        area = Box(pad, pad, slide_w - 2 * pad, slide_h - 2 * pad)
    return area, None


def _remap_rids(element, mapping: dict[str, str]) -> None:
    for el in element.iter():
        for key, value in el.attrib.items():
            if key.startswith(f"{{{R_NS}}}") and value in mapping:
                el.set(key, mapping[value])


def _drop_skipped(csld, skipped: set[str]) -> None:
    """Remove what refers to a relationship the copy does not get.

    A hyperlink element is removed on its own (the shape stays); any other element
    (a chart frame, a video, an embedded object) takes its whole top-level shape.
    """
    hlinks = {f"{{{A_NS}}}hlinkClick", f"{{{A_NS}}}hlinkHover"}
    hits = [el for el in csld.iter()
            if any(k.startswith(f"{{{R_NS}}}") and v in skipped for k, v in el.attrib.items())]
    tree, bg = csld.spTree, csld.find(f"{{{P_NS}}}bg")
    for el in hits:
        if el.tag in hlinks:
            target = el
        else:
            target = el
            while target.getparent() is not None and target.getparent() is not tree \
                    and target is not bg:
                target = target.getparent()
        parent = target.getparent()
        if parent is not None:
            parent.remove(target)


def copy_slide(prs, base, dropped_parts: set, notes: set[str]):
    """Append a new slide that looks like ``base``: same layout, background, shapes.

    Images, hyperlinks and links to kept slides are shared with the base. Charts,
    media, embedded objects and comments belong to one slide, and links to slides
    that leave the deck would keep those slides in the file, so copies do not get
    them (``notes`` says what was left out).
    """
    new = prs.slides.add_slide(base.slide_layout)
    mapping, skipped = {}, set()
    for rid, rel in base.part.rels.items():
        if rel.reltype in SKIP_RELS:
            continue
        if rel.reltype == RT.SLIDE and not rel.is_external \
                and rel.target_part in dropped_parts:
            skipped.add(rid)
            notes.add("a link on the base slide points to a slide that is not in the "
                      "result; content slides do not get that link")
            continue
        if rel.reltype not in SHARED_OK_RELS:
            skipped.add(rid)
            kind = rel.reltype.rsplit("/", 1)[-1]
            notes.add(f"the base slide holds a {kind} part; content slides do not get it "
                      "(each slide must own its own)")
            continue
        if rel.is_external:
            mapping[rid] = new.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
        else:
            mapping[rid] = new.part.relate_to(rel.target_part, rel.reltype)

    src_csld = base._element.cSld
    dst_csld = new._element.cSld
    if src_csld.get("name") is not None:
        dst_csld.set("name", src_csld.get("name"))
    # Keep the new slide's own spTree element: python-pptx caches ``slide.shapes`` on
    # it, so replacing the element would send later shapes to a detached tree.
    dst_tree = dst_csld.spTree
    for child in list(dst_csld):
        if child is not dst_tree:
            dst_csld.remove(child)
    for child in list(dst_tree):
        dst_tree.remove(child)
    before_tree = True
    for child in src_csld:
        if child.tag == dst_tree.tag:
            before_tree = False
            for k, v in child.attrib.items():
                dst_tree.set(k, v)
            for sub in child:
                dup = copy.deepcopy(sub)
                _remap_rids(dup, mapping)
                dst_tree.append(dup)
            continue
        dup = copy.deepcopy(child)
        _remap_rids(dup, mapping)
        if before_tree:
            dst_tree.addprevious(dup)
        else:
            dst_csld.append(dup)
    if skipped:
        _drop_skipped(dst_csld, skipped)

    for tag in ("clrMapOvr",):
        src = base._element.find(f"{{{P_NS}}}{tag}")
        dst = new._element.find(f"{{{P_NS}}}{tag}")
        if src is not None and dst is not None:
            dst.getparent().replace(dst, copy.deepcopy(src))
    show = base._element.get("showMasterSp")
    if show is not None:
        new._element.set("showMasterSp", show)
    return new


def build_templated(pictures: list[Path], template: Path, head: int, base_index: int | None,
                    tail: int, notes: list[str]):
    try:
        prs = Presentation(str(template))
    except Exception as exc:  # noqa: BLE001
        raise UsageError(f"cannot open template {template}: {exc} "
                         "(it must be a .pptx file; save a .potx as .pptx first)") from exc
    total = len(prs.slides)
    if total == 0:
        raise UsageError("the template has no slides")
    if head < 0 or tail < 0:
        raise UsageError("--keep-head and --keep-tail cannot be negative")
    if head + tail > total:
        raise UsageError(f"--keep-head {head} + --keep-tail {tail} is more than the "
                         f"template's {total} slides")
    if base_index is None:
        if head >= total:
            raise UsageError("every template slide is kept as head, so give "
                             "--base-index to say which slide to copy for the pages")
        base_index = head + 1
    if not 1 <= base_index <= total:
        raise UsageError(f"--base-index {base_index} is outside the template's "
                         f"slides 1..{total}")
    sw, sh = int(prs.slide_width), int(prs.slide_height)
    base = prs.slides[base_index - 1]

    area, used_ph = content_area(base, sw, sh, notes)
    # the copy has the base's shape ids, so the placeholder is found by its id
    used_id = used_ph.shape_id if used_ph is not None else None
    template_slides = list(prs.slides)
    kept_idx = list(range(head)) + list(range(total - tail, total))
    dropped_parts = {template_slides[i].part for i in range(total) if i not in kept_idx}

    copy_notes: set[str] = set()
    for pic in pictures:
        iw, ih = image_size(pic)
        slide = copy_slide(prs, base, dropped_parts, copy_notes)
        for shape in list(slide.shapes):
            if shape.shape_id == used_id or _is_empty_placeholder(shape):
                shape._element.getparent().remove(shape._element)
        b = fit(iw, ih, area)
        slide.shapes.add_picture(str(pic), Emu(b.x), Emu(b.y), Emu(b.w), Emu(b.h))

    notes.extend(sorted(copy_notes))
    position = {sl.part: i + 1 for i, sl in enumerate(template_slides)}
    for i in kept_idx:
        for rel in template_slides[i].part.rels.values():
            if rel.reltype == RT.SLIDE and not rel.is_external \
                    and rel.target_part in dropped_parts:
                notes.append(f"kept template slide {i + 1} links to template slide "
                             f"{position[rel.target_part]}, which is not in the result; "
                             "the kept slide is not changed, so that link and its "
                             "target stay in the file")

    id_list = prs.slides._sldIdLst
    entries = list(id_list)
    original, added = entries[:total], entries[total:]
    keep_head = original[:head]
    keep_tail = original[total - tail:] if tail else []
    dropped = original[head:total - tail]
    for el in entries:
        id_list.remove(el)
    for el in keep_head + added + keep_tail:
        id_list.append(el)
    _fix_sections(prs, [e.id for e in keep_head], [e.id for e in added],
                  [e.id for e in keep_tail])
    dropped_rids = {el.rId for el in dropped}
    for sld in prs.part._element.findall(f".//{{{P_NS}}}custShow//{{{P_NS}}}sld"):
        if sld.get(f"{{{R_NS}}}id") in dropped_rids:
            sld.getparent().remove(sld)
    for el in dropped:
        prs.part.drop_rel(el.rId)
    return prs, area


def _section_lists(prs):
    return [sec.find(f"{{{P14_NS}}}sldIdLst")
            for sec in prs.part._element.iter(f"{{{P14_NS}}}section")]


def _fix_sections(prs, head_ids, new_ids, tail_ids) -> None:
    """Keep PowerPoint sections in step with the slide list.

    Slides that are no longer in the deck leave their section; the new content
    slides go right after the last kept head slide (or before the first kept tail
    slide), so the sections still list every slide once, in deck order.
    """
    from lxml import etree

    lists = [lst for lst in _section_lists(prs) if lst is not None]
    if not lists:
        return
    kept = {str(i) for i in head_ids + tail_ids}
    for lst in lists:
        for e in list(lst):
            if e.get("id") not in kept:
                lst.remove(e)

    def find(sid):
        for lst in lists:
            for e in lst:
                if e.get("id") == str(sid):
                    return e
        return None

    made = [etree.Element(f"{{{P14_NS}}}sldId", {"id": str(i)}, nsmap={"p14": P14_NS})
            for i in new_ids]
    anchor = find(head_ids[-1]) if head_ids else None
    if anchor is not None:
        for e in reversed(made):
            anchor.addnext(e)
        return
    anchor = find(tail_ids[0]) if tail_ids else None
    if anchor is not None:
        for e in made:
            anchor.addprevious(e)
        return
    for e in made:
        lists[0].append(e)


# ---------------------------------------------------------------- check


def _slide_signature(slide) -> bytes:
    """The slide's own XML; equal signatures mean equal shapes and text."""
    from lxml import etree
    return etree.tostring(slide._element, method="c14n")


def verify(out: Path, pictures: list[Path], template: Path | None, head: int, tail: int,
           area: Box | None) -> list[str]:
    """Open the written file again and list every way it is wrong."""
    problems = []
    prs = Presentation(str(out))
    slides = list(prs.slides)
    want = len(pictures) + head + tail
    if len(slides) != want:
        problems.append(f"the file has {len(slides)} slides, expected {want}")
        return problems
    if template is not None:
        tpl = list(Presentation(str(template)).slides)
        kept = [(i, i) for i in range(head)]
        kept += [(len(slides) - tail + k, len(tpl) - tail + k) for k in range(tail)]
        for got_i, tpl_i in kept:
            if _slide_signature(slides[got_i]) != _slide_signature(tpl[tpl_i]):
                problems.append(f"slide {got_i + 1} differs from template slide {tpl_i + 1}")
    sections = [lst for lst in _section_lists(prs) if lst is not None]
    if sections:
        listed = [e.get("id") for lst in sections for e in lst]
        in_deck = [str(e.id) for e in prs.slides._sldIdLst]
        if listed != in_deck:
            problems.append("the PowerPoint sections do not list the slides in deck order")
    sw, sh = int(prs.slide_width), int(prs.slide_height)
    box = area or Box(0, 0, sw, sh)
    for n, slide in enumerate(slides[head:head + len(pictures)], start=head + 1):
        inside = [s for s in slide.shapes
                  if s.shape_type == MSO_SHAPE_TYPE.PICTURE and _box(s)
                  and box.contains(_box(s))]
        if len(inside) != 1:
            problems.append(f"slide {n} has {len(inside)} pictures in the content area, "
                            "expected 1")
        if any(_is_empty_placeholder(s) for s in slide.shapes):
            problems.append(f"slide {n} still has an empty placeholder")
    return problems


# ---------------------------------------------------------------- main


def save_atomic(prs, out: Path) -> None:
    """Write to a temporary file next to ``out``, then move it into place, so a
    failed save never leaves a half-written deck."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    try:
        prs.save(str(tmp))
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Export slide pictures (NN-<id>.png) to a PPTX, optionally on a template.")
    ap.add_argument("--slides", required=True, type=Path,
                    help="folder with the slide pictures NN-<id>.png")
    ap.add_argument("--out", required=True, type=Path, help="the .pptx file to write")
    ap.add_argument("--size", help="canvas size WxH, e.g. 1920x1080; without a template "
                    "it sets the slide shape (default: the first picture's size)")
    ap.add_argument("--template", type=Path, help="a .pptx whose slides are kept and reused")
    ap.add_argument("--keep-head", type=int, help="template slides to keep at the start")
    ap.add_argument("--base-index", type=int,
                    help="1-based template slide to copy for each page (default: the "
                         "first slide after the kept head)")
    ap.add_argument("--keep-tail", type=int, help="template slides to keep at the end")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args(argv)

    notes: list[str] = []
    try:
        pictures = find_slides(args.slides)
        size = parse_size(args.size) if args.size else None
        template_opts = (args.keep_head, args.base_index, args.keep_tail)
        if args.template is None:
            if any(v is not None for v in template_opts):
                raise UsageError("--keep-head, --base-index and --keep-tail need --template")
            prs, area = build_plain(pictures, size, notes), None
            head = tail = 0
        else:
            if not args.template.is_file():
                raise UsageError(f"template not found: {args.template}")
            if args.out.resolve() == args.template.resolve():
                raise UsageError("--out must not be the template file; the template is "
                                 "never changed")
            if size is not None:
                notes.append("--size is not used with a template; the template's slide "
                             "size is kept")
            head = args.keep_head or 0
            tail = args.keep_tail or 0
            prs, area = build_templated(pictures, args.template, head, args.base_index,
                                        tail, notes)
        save_atomic(prs, args.out)
    except UsageError as exc:
        print(f"export_pptx: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"export_pptx: cannot write {args.out}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - 1 means "the result failed its check"
        print(f"export_pptx: unexpected error ({type(exc).__name__}: {exc})",
              file=sys.stderr)
        return 2

    try:
        problems = verify(args.out, pictures, args.template, head, tail, area)
    except Exception as exc:  # noqa: BLE001 - an unreadable result is a failed check
        problems = [f"cannot read the written file back: {type(exc).__name__}: {exc}"]
    result = {"ok": not problems, "out": str(args.out), "pages": len(pictures),
              "slides": len(pictures) + head + tail, "template": str(args.template)
              if args.template else None, "notes": notes, "problems": problems}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for n in notes:
            print(f"note: {n}")
        for p in problems:
            print(f"FAIL: {p}")
        if not problems:
            print(f"ok: wrote {args.out} ({result['slides']} slides, "
                  f"{len(pictures)} pages)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
