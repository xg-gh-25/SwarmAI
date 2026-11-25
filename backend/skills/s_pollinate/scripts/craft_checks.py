#!/usr/bin/env python3
"""craft_checks.py — measurable craft checks for Pollinate works.

A rendered page is measured in the browser, then the measurement is judged
in Python. The two halves are separate on purpose: the work script stores the
measurement of every version (``measure.json``), and a reference deck can be
measured once and compared with later versions.

What this module gives (Pollinate v2 design, section 13.3):

* ``PROBE_JS`` — a JavaScript function expression. Run it in a rendered page
  with a slide id, for example ``page.evaluate(PROBE_JS, "s-7f3a")`` in
  Playwright. It returns one measurement dict for that slide (shape below).
* ``evaluate(measure, canvas, config=None, reference=None)`` — returns a list
  of findings. ``measure`` is one slide measurement, a list of them, or
  ``{"slides": [...]}``.
* A command line: ``craft_checks.py --measure measure.json
  [--reference ref-measure.json] [--config c.json] [--canvas 1920x1080]``.
  It prints ``{"ok", "fails", "warns", "findings"}`` as JSON. Exit 0 = no fail
  finding, 1 = at least one fail finding, 2 = usage or input error.
* ``check_signature(measure, signature, config=None)`` — the ``brand`` check:
  the person's brand signature (``brand_config.signature()``) is on the work.
  It judges the work as a whole (all its slides at once), so ``work.py cut``
  calls it once per work, next to ``evaluate``; the command line does not run
  it.

Measurement shape (one slide; all boxes are ``[x, y, w, h]`` in canvas px,
relative to the slide's top-left corner, with any CSS scale of the slide
removed)::

    {"slide": "s-7f3a", "w": 1920, "h": 1080,
     "texts":  [{"text", "role", "font_px", "lines", "nowrap",
                 "box", "container", "chars", "color"}],
     "blocks": [{"box", "group"}],
     "images": [{"src", "box", "loaded", "role"?, "fp"?}],
     "colors": [{"color", "use": "text|fill", "weight"}],
     "layout": "cover" | null}

``color`` (per text), ``colors`` and ``layout`` (per slide), ``role`` (per
image, only when the author wrote ``data-role="background"``) and ``fp`` (per
image whose source is a base64 ``data:`` URL, which is every local image
after the renderer inlined it: ``fingerprint`` of the image's bytes) are
additions to the 13.3 shape. They are optional: the palette part of the reference check
needs colors and is skipped when a measurement has none. ``layout`` is the
slide's ``data-layout`` attribute: a slide declared with one of the
``sparse_layouts`` names (``cover``, ``chapter``, ``statement``, ``quote`` ...)
is meant to be sparse, and the fill check skips it.

How the probe reads the page:

* A *text* is the nearest block-level box (or an element with ``data-role``)
  that owns visible text. ``box`` is the extent of its rendered lines,
  ``lines`` the number of distinct line boxes, ``chars`` the number of
  non-space characters. ``container`` is the nearest box around it that a
  reader sees as a container (it has a background, a border, a shadow, or
  clips its content); without one it is the slide itself.
* ``role``: ``data-role`` on the element or an ancestor inside the slide wins.
  Otherwise h1/h2 or a ``title``/``headline`` class is a title; a
  ``subtitle``/``subhead`` class is a subtitle; figcaption or a ``caption``
  class is a caption; label or a ``label``/``tag``/``badge``/``eyebrow``/
  ``kicker``/``chip`` class is a label. The first text right after a title
  that is smaller and at most 90 characters long is the subtitle. Everything
  else is body.
* ``nowrap`` is true when ``data-nowrap`` is on the element or an ancestor
  inside the slide (``data-nowrap="false"`` turns it off), or when the
  computed ``white-space`` / ``text-wrap-mode`` is ``nowrap``.
* *blocks* are the visible block-level children of every element that has at
  least two of them; ``group`` names that parent.
* *images* are img, svg, canvas and video elements and elements with a CSS
  background image.

The checks (design section 6) and their thresholds live in ``DEFAULTS``;
see the comments there for each value and why it was chosen. Besides the
nine checks of section 13.3 there are two more: ``image``, an image that did
not load (the probe measures ``loaded``) is a fail; and ``brand``, the
configured brand signature (watermark, link text, QR image) must be on the
work (``check_signature``; design section 9 keeps 品牌/水印/二维码).
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import re
import statistics
import sys
import unicodedata
from pathlib import Path
from typing import Any

CHECKS = ("font_floor", "fill", "align", "nowrap", "density",
          "overflow", "overlap", "safe_area", "image", "reference", "brand")

# The canvas the density cap is written for; other canvases scale by area.
_BASE_AREA = 1920 * 1080

DEFAULTS: dict[str, Any] = {
    # Safe area: the canvas minus this share of its width (left and right)
    # and of its height (top and bottom). 5 % is the common print and screen
    # safe margin.
    "safe_margin": 0.05,

    # font_floor. Two floors, the larger one wins. Both come from the canvas,
    # never from a fixed px value.
    # 1) A share of the canvas short side. 0.022 gives about 24 px for
    #    titles, subtitles and body text and 0.0167 about 18 px for labels and
    #    captions on a 1920x1080 slide: the sizes that still read on a
    #    projector or a phone. The user said 「字太小」 more than five times.
    "font_canvas_ratio": {"title": 0.022, "subtitle": 0.022, "body": 0.022,
                          "label": 0.0167, "caption": 0.0167},
    # 2) A share of the largest title on the same slide. Body text or a
    #    subtitle under a fifth of the title (labels under 15 %) looks tiny
    #    next to it. The title is the anchor itself (0). A slide without a
    #    title uses only the canvas floor, so a huge number on a stat slide
    #    does not push the floor up.
    "font_anchor_ratio": {"title": 0.0, "subtitle": 0.2, "body": 0.2,
                          "label": 0.15, "caption": 0.15},

    # fill: the content bounding box (texts and images that are not a
    # backdrop) must cover at least this share of the safe area. Below it the
    # slide looks 「太空」 or 「拘谨」. A slide the author declared sparse
    # (data-layout on the slide, one of sparse_layouts) is not measured; the
    # layout is never guessed, because a guess would also hide a content
    # slide that really is too empty. A slide with no content always fails.
    "fill_min": 0.35,
    # The names cover the sparse slide types of the shipped design systems
    # (cover, chapter, statement, quote, end ...); compared without case.
    "sparse_layouts": ["cover", "section", "chapter", "statement", "quote", "stat",
                       "hero", "manifesto", "closing", "end", "colophon"],

    # align: two sibling blocks of one group that are stacked (or side by
    # side) are a near miss when their closest edge is off by more than
    # align_tol and at most align_window. Both are shares of the canvas width
    # (6 px and 77 px on 1920). Offsets above the window on every edge are
    # read as a deliberate layout, not a slip.
    "align_tol": 0.003,
    "align_tol_min_px": 2.0,
    "align_window": 0.04,

    # nowrap: texts declared nowrap (data-nowrap or white-space: nowrap) are
    # always checked. Texts of these roles must also render on one line, on
    # every slide, declared or not: the user asked that subtitles never wrap
    # and that every slide be checked for the same problem
    # (「副标题…不能换行 你看看其他slides 有没有同样的问题都修了」). Set this
    # to [] to check only the texts declared nowrap.
    "nowrap_roles": ["title", "subtitle"],

    # density: weighted characters per slide. A wide character (CJK, full
    # width) takes about two Latin widths, so it counts as wide_char_weight.
    # 400 units on 1920x1080 is about 200 Chinese characters or 70 English
    # words; other canvases scale the cap by their area
    # (「字太多」「不要再加文字了」).
    "density_cap": 400,
    "wide_char_weight": 2,

    # overflow / overlap / safe_area. These px values are rounding
    # tolerances for sub-pixel layout, not design sizes.
    "overflow_tol_px": 2.0,
    "safe_tol_px": 2.0,
    # Two items overlap when the shared area is at least this share of the
    # smaller item and at least overlap_min_px in both directions.
    "overlap_min_ratio": 0.1,
    "overlap_min_px": 4.0,
    # An image covering this share of the canvas is a backdrop: fill,
    # overlap and safe_area ignore it. data-role="background" does the same.
    "background_area_ratio": 0.8,

    # reference: drift from a reference measure. Sparse slides (declared in
    # sparse_layouts, or with no body text and no content image) are compared
    # with the reference's sparse slides, all other slides with its content
    # slides; with no counterpart in the reference, type scale and margins
    # are not compared. The palette is compared with the whole reference deck.
    # Type scale: the median font of a role (title, subtitle, body), as a
    # share of the canvas short side, may differ from the reference by this
    # ratio (15 % is about one step of a type scale).
    "reference_font_tol": 0.15,
    "reference_roles": ["title", "subtitle", "body"],
    # Margins: the content box's side margin (the smaller of left and right)
    # and top margin, as a share of the canvas, may differ by this much
    # (2 % is about 38 px on 1920).
    "reference_margin_tol": 0.02,
    # Palette: a color that makes up at least palette_min_share of the
    # slide's text or fill must be within palette_delta_e (CIE76) of a color
    # of the reference palette. A delta E of 20 is a clearly different color.
    "palette_delta_e": 20.0,
    "palette_min_share": 0.05,

    # The level of each check's findings: "fail" stops a version, "warn"
    # only reports.
    "levels": {c: "fail" for c in CHECKS},
}

PROBE_JS = r"""
(slideId) => {
  const SUBTITLE_MAX_CHARS = 90;
  const ROLES = ['title', 'subtitle', 'body', 'label', 'caption'];
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'HEAD', 'TITLE', 'META', 'LINK']);

  let root = null;
  if (slideId && slideId !== 'main') {
    root = document.querySelector('[data-slide-id="' + CSS.escape(slideId) + '"]');
  } else {
    root = document.querySelector('[data-slide-id="main"]') || document.body;
  }
  if (!root) { throw new Error('craft probe: no slide with id ' + slideId); }

  const isBody = (root === document.body);
  const rr = root.getBoundingClientRect();
  let ox, oy, sx = 1, sy = 1, W, H;
  if (isBody) {
    ox = -window.scrollX; oy = -window.scrollY;
    W = window.innerWidth;
    H = Math.max(window.innerHeight, document.documentElement.scrollHeight);
  } else {
    ox = rr.left; oy = rr.top;
    W = root.offsetWidth; H = root.offsetHeight;
    if (W > 0 && rr.width > 0) { sx = rr.width / W; }
    if (H > 0 && rr.height > 0) { sy = rr.height / H; }
  }
  if (!(W > 0 && H > 0)) { throw new Error('craft probe: slide ' + slideId + ' is not rendered (zero size)'); }

  const r1 = (v) => Math.round(v * 10) / 10;
  const rel = (r) => [r1((r.left - ox) / sx), r1((r.top - oy) / sy), r1(r.width / sx), r1(r.height / sy)];
  const style = (el) => getComputedStyle(el);

  const visible = (el) => {
    if (el.checkVisibility) {
      return el.checkVisibility({opacityProperty: true, visibilityProperty: true});
    }
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = style(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) { return false; }
      if (e === root) { break; }
    }
    return true;
  };
  const inSvg = (el) => {
    for (let e = el.parentElement; e && e !== root.parentElement; e = e.parentElement) {
      if (e.namespaceURI === SVG_NS) { return true; }
    }
    return false;
  };
  const closestIn = (el, sel) => {
    const c = el.closest(sel);
    return (c && (c === root || root.contains(c))) ? c : null;
  };
  const isInline = (cs) => cs.display === 'inline' || cs.display === 'contents';

  const parseColor = (s) => {
    const m = s && s.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,\/]\s*([\d.]+%?))?/);
    if (!m) { return null; }
    let a = 1;
    if (m[4] !== undefined) { a = m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4]); }
    if (a <= 0.05) { return null; }
    const hx = (v) => Math.max(0, Math.min(255, Math.round(parseFloat(v)))).toString(16).padStart(2, '0');
    return '#' + hx(m[1]) + hx(m[2]) + hx(m[3]);
  };
  const hasVisibleBox = (cs) => {
    if (parseColor(cs.backgroundColor)) { return true; }
    if (cs.backgroundImage && cs.backgroundImage !== 'none') { return true; }
    if (cs.boxShadow && cs.boxShadow !== 'none') { return true; }
    for (const side of ['Top', 'Right', 'Bottom', 'Left']) {
      if (parseFloat(cs['border' + side + 'Width']) > 0 && cs['border' + side + 'Style'] !== 'none') { return true; }
    }
    return false;
  };
  const clips = (cs) => cs.overflowX !== 'visible' || cs.overflowY !== 'visible';
  // FNV-1a (32 bit) of the bytes of a base64 data: URL, so a known file (the brand QR)
  // is found again after the renderer inlined it. Same as craft_checks.fingerprint.
  const fingerprint = (src) => {
    const i = src.indexOf(';base64,');
    if (!src.startsWith('data:') || i < 0) { return null; }
    let bin;
    try { bin = atob(src.slice(i + 8)); } catch (e) { return null; }
    let h = 0x811c9dc5;
    for (let k = 0; k < bin.length; k++) { h ^= bin.charCodeAt(k); h = Math.imul(h, 0x01000193); }
    return (h >>> 0).toString(16).padStart(8, '0');
  };

  const classParts = (el) => {
    const cn = (typeof el.className === 'string') ? el.className : (el.getAttribute('class') || '');
    const parts = new Set();
    for (const tok of cn.toLowerCase().split(/\s+/)) {
      if (!tok) { continue; }
      parts.add(tok);
      for (const p of tok.split(/[-_]/)) { if (p) { parts.add(p); } }
    }
    return parts;
  };
  const inferRole = (el) => {
    const decl = closestIn(el, '[data-role]');
    if (decl) {
      const v = (decl.getAttribute('data-role') || '').trim().toLowerCase();
      if (ROLES.includes(v)) { return v; }
    }
    const tag = el.tagName;
    const parts = classParts(el);
    if (parts.has('subtitle') || parts.has('subhead') || (parts.has('sub') && parts.has('title'))) {
      return 'subtitle';
    }
    if (tag === 'H1' || tag === 'H2' || parts.has('title') || parts.has('headline')) {
      return 'title';
    }
    if (tag === 'FIGCAPTION' || parts.has('caption')) { return 'caption'; }
    if (tag === 'LABEL' || ['label', 'tag', 'badge', 'eyebrow', 'kicker', 'chip'].some((p) => parts.has(p))) {
      return 'label';
    }
    return null;
  };
  const nowrapOf = (el, cs) => {
    const decl = closestIn(el, '[data-nowrap]');
    if (decl) { return (decl.getAttribute('data-nowrap') || '').trim().toLowerCase() !== 'false'; }
    return cs.whiteSpace === 'nowrap' || cs.textWrapMode === 'nowrap';
  };

  // ---- texts: group visible text nodes by their owning box ----
  const owners = [];
  const owned = new Map();
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!/\S/.test(n.data)) { continue; }
    const parent = n.parentElement;
    if (!parent || SKIP_TAGS.has(parent.tagName) || parent.namespaceURI === SVG_NS || inSvg(parent)) { continue; }
    if (!visible(parent)) { continue; }
    let owner = parent;
    while (owner !== root && !owner.hasAttribute('data-role') && isInline(style(owner))) {
      owner = owner.parentElement;
    }
    if (!owned.has(owner)) { owned.set(owner, []); owners.push(owner); }
    owned.get(owner).push(n);
  }

  const range = document.createRange();
  const colorWeight = new Map();
  const addColor = (color, use, weight) => {
    if (!color || !(weight > 0)) { return; }
    const k = color + '|' + use;
    colorWeight.set(k, (colorWeight.get(k) || 0) + weight);
  };

  const texts = [];
  for (const owner of owners) {
    const nodes = owned.get(owner);
    const rects = [];
    const sizeChars = new Map();
    let raw = '';
    for (const n of nodes) {
      raw += n.data;
      range.selectNodeContents(n);
      for (const r of range.getClientRects()) {
        if (r.width > 0.5 && r.height > 0.5) { rects.push(r); }
      }
      const fs = parseFloat(style(n.parentElement).fontSize) || 0;
      sizeChars.set(fs, (sizeChars.get(fs) || 0) + n.data.replace(/\s/g, '').length);
    }
    if (!rects.length) { continue; }
    const text = raw.replace(/\s+/g, ' ').trim();
    const chars = text.replace(/\s/g, '').length;
    if (!chars) { continue; }

    // distinct line boxes: rects whose vertical middles share a line
    rects.sort((a, b) => a.top - b.top);
    const lineBoxes = [];
    for (const r of rects) {
      const mid = (r.top + r.bottom) / 2;
      const hit = lineBoxes.find((l) => mid >= l.top && mid <= l.bottom);
      if (hit) { hit.top = Math.min(hit.top, r.top); hit.bottom = Math.max(hit.bottom, r.bottom); }
      else { lineBoxes.push({top: r.top, bottom: r.bottom}); }
    }
    let L = Infinity, T = Infinity, R = -Infinity, B = -Infinity;
    for (const r of rects) {
      L = Math.min(L, r.left); T = Math.min(T, r.top); R = Math.max(R, r.right); B = Math.max(B, r.bottom);
    }
    let fontPx = 0, best = -1;
    for (const [fs, c] of sizeChars) { if (c > best) { best = c; fontPx = fs; } }

    let cont = root;
    for (let e = owner; e && e !== root; e = e.parentElement) {
      const cs = style(e);
      if (clips(cs) || hasVisibleBox(cs)) { cont = e; break; }
    }
    const cs = style(owner);
    const color = parseColor(cs.color);
    addColor(color, 'text', chars);
    texts.push({
      text: text, role: inferRole(owner),
      font_px: r1(fontPx), lines: lineBoxes.length, nowrap: nowrapOf(owner, cs),
      box: rel({left: L, top: T, width: R - L, height: B - T}),
      container: cont === root ? [0, 0, r1(W), r1(H)] : rel(cont.getBoundingClientRect()),
      chars: chars, color: color,
    });
  }
  // the smaller, short text right after a title is its subtitle; the rest is body
  for (let i = 0; i < texts.length; i++) {
    const t = texts[i];
    if (t.role !== null) { continue; }
    const prev = i > 0 ? texts[i - 1] : null;
    if (prev && prev.role === 'title' && t.font_px < prev.font_px && t.chars <= SUBTITLE_MAX_CHARS) {
      t.role = 'subtitle';
    } else {
      t.role = 'body';
    }
  }

  // ---- blocks: visible block-level siblings, grouped by parent ----
  const pathOf = (el) => {
    if (el === root) { return 'root'; }
    if (el.id) { return '#' + el.id; }
    const parts = [];
    for (let e = el; e && e !== root; e = e.parentElement) {
      if (e.id) { parts.unshift('#' + e.id); break; }
      const idx = Array.prototype.indexOf.call(e.parentElement.children, e) + 1;
      parts.unshift(e.tagName.toLowerCase() + ':nth-child(' + idx + ')');
    }
    return parts.join('>');
  };
  const blocks = [];
  const all = [root].concat(Array.from(root.querySelectorAll('*')));
  for (const parent of all) {
    if (SKIP_TAGS.has(parent.tagName) || parent.namespaceURI === SVG_NS) { continue; }
    const kids = [];
    for (const k of parent.children) {
      if (SKIP_TAGS.has(k.tagName)) { continue; }
      const cs = style(k);
      if (isInline(cs) || cs.display === 'none' || !visible(k)) { continue; }
      const r = k.getBoundingClientRect();
      if (r.width <= 0.5 || r.height <= 0.5) { continue; }
      kids.push(r);
    }
    if (kids.length < 2) { continue; }
    const g = pathOf(parent);
    for (const r of kids) { blocks.push({box: rel(r), group: g}); }
  }

  // ---- images and fills ----
  const images = [];
  for (const el of all) {
    if (SKIP_TAGS.has(el.tagName) || inSvg(el)) { continue; }
    const tag = el.tagName.toLowerCase();
    const cs = style(el);
    if (!visible(el)) { continue; }
    const r = el.getBoundingClientRect();
    if (r.width <= 0.5 || r.height <= 0.5) { continue; }
    const area = (r.width / sx) * (r.height / sy);
    const bg = parseColor(cs.backgroundColor);
    if (bg) { addColor(bg, 'fill', area); }
    let src = null, loaded = true;
    if (tag === 'img') {
      src = el.currentSrc || el.getAttribute('src') || '';
      loaded = el.complete && el.naturalWidth > 0;
    } else if (tag === 'svg' || tag === 'canvas' || tag === 'video') {
      src = tag;
      if (tag === 'video') { loaded = el.readyState >= 2; }
    } else if (cs.backgroundImage && cs.backgroundImage.includes('url(')) {
      const m = cs.backgroundImage.match(/url\(["']?([^"')]*)["']?\)/);
      src = m ? m[1] : 'background-image';
    }
    if (src === null) { continue; }
    const img = {src: src.length > 200 ? src.slice(0, 200) : src, box: rel(r), loaded: loaded};
    const fp = fingerprint(src);
    if (fp) { img.fp = fp; }
    if ((el.getAttribute('data-role') || '').trim().toLowerCase() === 'background') { img.role = 'background'; }
    images.push(img);
  }

  const colors = [];
  for (const [k, w] of colorWeight) {
    const [color, use] = k.split('|');
    colors.push({color: color, use: use, weight: r1(w)});
  }
  colors.sort((a, b) => b.weight - a.weight);

  const layout = root.getAttribute('data-layout');
  return {slide: slideId || 'main', w: r1(W), h: r1(H), texts: texts, blocks: blocks, images: images, colors: colors,
          layout: layout ? layout.trim().toLowerCase() : null};
}
"""


# --------------------------------------------------------------------------
# config and input
# --------------------------------------------------------------------------

def merge_config(config: dict | None) -> dict:
    """Return DEFAULTS with ``config`` laid over it; unknown keys raise ValueError."""
    cfg = copy.deepcopy(DEFAULTS)
    if not config:
        return cfg
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    for key, value in config.items():
        if key not in cfg:
            raise ValueError(f"unknown config key: {key}")
        if isinstance(cfg[key], dict):
            if not isinstance(value, dict):
                raise ValueError(f"config key {key} must be an object")
            for sub, sub_value in value.items():
                if sub not in cfg[key]:
                    raise ValueError(f"unknown config key: {key}.{sub}")
                cfg[key][sub] = sub_value
        else:
            cfg[key] = value
    for key, default in DEFAULTS.items():
        value = cfg[key]
        if key == "levels":
            for check, level in value.items():
                if level not in ("fail", "warn"):
                    raise ValueError(f"levels.{check} must be 'fail' or 'warn', not {level!r}")
        elif isinstance(default, dict):
            for sub, sub_value in value.items():
                if not _is_number(sub_value):
                    raise ValueError(f"config key {key}.{sub} must be a number")
        elif isinstance(default, list):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"config key {key} must be a list of names")
            if key == "sparse_layouts":  # layouts are compared without case
                cfg[key] = [v.strip().lower() for v in value]
        elif not _is_number(value):
            raise ValueError(f"config key {key} must be a number")
    return cfg


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _slides(measure: Any) -> list[dict]:
    """Accept one slide measure, a list of them, or {"slides": [...]}."""
    if isinstance(measure, dict) and "slides" in measure and "slide" not in measure:
        measure = measure["slides"]
    if isinstance(measure, dict):
        measure = [measure]
    if not isinstance(measure, list) or not all(isinstance(m, dict) for m in measure):
        raise ValueError("measure must be a slide object, a list of them, or {\"slides\": [...]}")
    return measure


def _box(value: Any, what: str) -> tuple[float, float, float, float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or not all(isinstance(v, (int, float)) for v in value)):
        raise ValueError(f"{what} must be [x, y, w, h], got {value!r}")
    x, y, w, h = (float(v) for v in value)
    return x, y, max(w, 0.0), max(h, 0.0)


def _canvas(slide: dict, canvas: dict | None) -> tuple[float, float]:
    src = canvas if canvas and canvas.get("w") and canvas.get("h") else slide
    try:
        w, h = float(src["w"]), float(src["h"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("canvas size missing: give canvas {w, h} or w/h in the measure")
    if w <= 0 or h <= 0:
        raise ValueError(f"canvas size must be positive, got {w}x{h}")
    return w, h


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def _area(b) -> float:
    return b[2] * b[3]


def _inter(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return max(0.0, x2 - x1), max(0.0, y2 - y1)


def _union(boxes):
    x1 = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    x2 = max(b[0] + b[2] for b in boxes)
    y2 = max(b[1] + b[3] for b in boxes)
    return x1, y1, x2 - x1, y2 - y1


def _span_overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _fmt_box(b) -> str:
    return "[" + ", ".join(f"{v:g}" for v in (round(b[0]), round(b[1]), round(b[2]), round(b[3]))) + "]"


def _short(text: str, n: int = 60) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _is_wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _units(text: str, wide_weight: float) -> float:
    return sum(wide_weight if _is_wide(c) else 1 for c in str(text) if not c.isspace())


# --------------------------------------------------------------------------
# the parts of one slide that the checks share
# --------------------------------------------------------------------------

class _Slide:
    def __init__(self, raw: dict, W: float, H: float, cfg: dict):
        self.id = str(raw.get("slide", "?"))
        self.W, self.H, self.cfg = W, H, cfg
        m = cfg["safe_margin"]
        self.safe = (W * m, H * m, W * (1 - 2 * m), H * (1 - 2 * m))
        self.texts = []
        for i, t in enumerate(raw.get("texts") or []):
            if not isinstance(t, dict):
                raise ValueError(f"slide {self.id}: texts[{i}] must be an object")
            box = _box(t.get("box"), f"slide {self.id} texts[{i}].box")
            cont = t.get("container")
            cont = _box(cont, f"slide {self.id} texts[{i}].container") if cont is not None else None
            text = str(t.get("text", ""))
            chars = t.get("chars")
            if not isinstance(chars, (int, float)):
                chars = len("".join(text.split()))
            if chars <= 0 or box[2] <= 0 or box[3] <= 0:
                continue
            self.texts.append({
                "text": text, "role": str(t.get("role") or "body"),
                "font_px": float(t.get("font_px") or 0), "lines": int(t.get("lines") or 1),
                "nowrap": bool(t.get("nowrap")), "box": box, "container": cont,
                "chars": chars, "color": t.get("color"),
            })
        self.blocks = []
        for i, b in enumerate(raw.get("blocks") or []):
            if not isinstance(b, dict):
                raise ValueError(f"slide {self.id}: blocks[{i}] must be an object")
            self.blocks.append({"box": _box(b.get("box"), f"slide {self.id} blocks[{i}].box"),
                                "group": str(b.get("group", ""))})
        self.images = []
        bg_ratio = cfg["background_area_ratio"]
        for i, im in enumerate(raw.get("images") or []):
            if not isinstance(im, dict):
                raise ValueError(f"slide {self.id}: images[{i}] must be an object")
            box = _box(im.get("box"), f"slide {self.id} images[{i}].box")
            if box[2] <= 0 or box[3] <= 0:
                continue
            ix, iy = _inter(box, (0, 0, W, H))
            backdrop = im.get("role") == "background" or ix * iy >= bg_ratio * W * H
            self.images.append({"src": str(im.get("src", "")), "box": box, "backdrop": backdrop,
                                "loaded": im.get("loaded", True) is not False,
                                "fp": im.get("fp") if isinstance(im.get("fp"), str) else None})
        self.colors = [c for c in (raw.get("colors") or [])
                       if isinstance(c, dict) and isinstance(c.get("color"), str)]
        layout = raw.get("layout")
        self.layout = layout.strip().lower() if isinstance(layout, str) else None
        # declared sparse by the author: the only thing that turns fill off
        self.sparse = self.layout in cfg["sparse_layouts"]

    @property
    def kind(self) -> str:
        """The reference group: 'sparse' (declared, or no body text and no
        content image) or 'content'. Only the reference check uses it."""
        if self.sparse:
            return "sparse"
        has_body = any(t["role"] == "body" for t in self.texts)
        has_image = any(not im["backdrop"] for im in self.images)
        return "content" if has_body or has_image else "sparse"

    def content_boxes(self):
        return ([t["box"] for t in self.texts]
                + [im["box"] for im in self.images if not im["backdrop"]])

    def items(self):
        """Texts and content images, each as (label, box, kind)."""
        out = [(f"text \"{_short(t['text'], 40)}\"", t["box"], "text") for t in self.texts]
        out += [(f"image {_short(im['src'], 40)}", im["box"], "image")
                for im in self.images if not im["backdrop"]]
        return out


def _finding(sl: _Slide, check: str, msg: str, where: str) -> dict:
    return {"slide": sl.id, "check": check, "level": sl.cfg["levels"][check],
            "msg": msg, "where": where}


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------

def _check_font_floor(sl: _Slide) -> list[dict]:
    cfg = sl.cfg
    short = min(sl.W, sl.H)
    titles = [t["font_px"] for t in sl.texts if t["role"] == "title"]
    anchor = max(titles) if titles else None
    out = []
    for t in sl.texts:
        role = t["role"]
        if role not in cfg["font_canvas_ratio"]:
            continue
        floor_canvas = cfg["font_canvas_ratio"][role] * short
        floor_anchor = cfg["font_anchor_ratio"].get(role, 0) * anchor if anchor else 0.0
        floor = max(floor_canvas, floor_anchor)
        if t["font_px"] + 0.05 < floor:
            if floor_anchor > floor_canvas:
                why = (f"{cfg['font_anchor_ratio'][role]:g} of the largest title "
                       f"({anchor:g}px)")
            else:
                why = (f"{cfg['font_canvas_ratio'][role]:g} of the canvas short side "
                       f"({short:g}px)")
            out.append(_finding(sl, "font_floor",
                                f"{role} text is {t['font_px']:g}px; the floor here is "
                                f"{floor:.1f}px ({why}). Make it larger (字太小).",
                                _short(t["text"])))
    return out


def _check_fill(sl: _Slide) -> list[dict]:
    boxes = sl.content_boxes()
    safe = sl.safe
    if not boxes:
        return [_finding(sl, "fill", "The slide has no content (太空).", _fmt_box(safe))]
    if sl.sparse:
        return []  # the author declared a cover, divider, quote or stat slide
    u = _union(boxes)
    ix, iy = _inter(u, safe)
    ratio = ix * iy / _area(safe)
    if ratio < sl.cfg["fill_min"]:
        return [_finding(sl, "fill",
                         f"Content covers {ratio:.0%} of the safe area; at least "
                         f"{sl.cfg['fill_min']:.0%} is needed. The slide looks too empty "
                         f"(太空): make the content larger or spread it. If it is a cover "
                         f"or divider, mark the slide data-layout=\"cover\".",
                         _fmt_box(u))]
    return []


def _check_align(sl: _Slide) -> list[dict]:
    cfg = sl.cfg
    tol = max(cfg["align_tol"] * sl.W, cfg["align_tol_min_px"])
    window = cfg["align_window"] * sl.W
    groups: dict[str, list] = {}
    for b in sl.blocks:
        groups.setdefault(b["group"], []).append(b["box"])
    out = []
    for group, boxes in groups.items():
        worst = None
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ox = _span_overlap(a[0], a[0] + a[2], b[0], b[0] + b[2])
                oy = _span_overlap(a[1], a[1] + a[3], b[1], b[1] + b[3])
                if ox > 0 and oy > 0:
                    continue  # they overlap; the overlap check speaks for that
                if oy > 0:    # side by side: compare top, middle, bottom
                    diffs = {"top": abs(a[1] - b[1]),
                             "middle": abs((a[1] + a[3] / 2) - (b[1] + b[3] / 2)),
                             "bottom": abs((a[1] + a[3]) - (b[1] + b[3]))}
                elif ox > 0:  # stacked: compare left, center, right
                    diffs = {"left": abs(a[0] - b[0]),
                             "center": abs((a[0] + a[2] / 2) - (b[0] + b[2] / 2)),
                             "right": abs((a[0] + a[2]) - (b[0] + b[2]))}
                else:
                    continue  # diagonal: not meant to line up
                edge = min(diffs, key=diffs.get)
                d = diffs[edge]
                if tol < d <= window and (worst is None or d > worst[0]):
                    worst = (d, edge, a, b)
        if worst:
            d, edge, a, b = worst
            out.append(_finding(sl, "align",
                                f"Two blocks in group {group} are {d:.0f}px off on their {edge} "
                                f"edge (allowed {tol:.0f}px). Line them up (对齐).",
                                f"{_fmt_box(a)} vs {_fmt_box(b)}"))
    return out


def _check_nowrap(sl: _Slide) -> list[dict]:
    roles = set(sl.cfg["nowrap_roles"])
    out = []
    for t in sl.texts:
        if t["lines"] > 1 and (t["nowrap"] or t["role"] in roles):
            why = "is declared nowrap" if t["nowrap"] else f"is a {t['role']}"
            out.append(_finding(sl, "nowrap",
                                f"This text {why} but renders on {t['lines']} lines; it must "
                                f"stay on one line. Shorten it or give it more width.",
                                _short(t["text"])))
    return out


def _check_density(sl: _Slide) -> list[dict]:
    cfg = sl.cfg
    units = sum(_units(t["text"], cfg["wide_char_weight"]) for t in sl.texts)
    cap = cfg["density_cap"] * (sl.W * sl.H) / _BASE_AREA
    if units > cap:
        return [_finding(sl, "density",
                         f"The slide holds {units:.0f} character units; the cap for this canvas "
                         f"is {cap:.0f} (wide characters count {cfg['wide_char_weight']:g}). "
                         f"Cut text or split the slide (字太多).",
                         f"{len(sl.texts)} texts")]
    return []


def _check_overflow(sl: _Slide) -> list[dict]:
    tol = sl.cfg["overflow_tol_px"]
    out = []
    for t in sl.texts:
        c = t["container"] or (0.0, 0.0, sl.W, sl.H)
        b = t["box"]
        spill = {"left": c[0] - b[0], "top": c[1] - b[1],
                 "right": (b[0] + b[2]) - (c[0] + c[2]),
                 "bottom": (b[1] + b[3]) - (c[1] + c[3])}
        bad = {k: v for k, v in spill.items() if v > tol}
        if bad:
            sides = ", ".join(f"{k} by {v:.0f}px" for k, v in bad.items())
            out.append(_finding(sl, "overflow",
                                f"Text leaves its container ({sides}).",
                                f"\"{_short(t['text'], 40)}\" {_fmt_box(b)} in {_fmt_box(c)}"))
    return out


def _check_overlap(sl: _Slide) -> list[dict]:
    cfg = sl.cfg
    items = sl.items()
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            la, a, _ = items[i]
            lb, b, _ = items[j]
            ix, iy = _inter(a, b)
            if ix < cfg["overlap_min_px"] or iy < cfg["overlap_min_px"]:
                continue
            smaller = min(_area(a), _area(b))
            if smaller > 0 and ix * iy >= cfg["overlap_min_ratio"] * smaller:
                out.append(_finding(sl, "overlap",
                                    f"{la} and {lb} overlap by {ix:.0f}x{iy:.0f}px.",
                                    f"{_fmt_box(a)} vs {_fmt_box(b)}"))
    return out


def _check_safe_area(sl: _Slide) -> list[dict]:
    tol = sl.cfg["safe_tol_px"]
    s = sl.safe
    out = []
    for label, b, _ in sl.items():
        spill = {"left": s[0] - b[0], "top": s[1] - b[1],
                 "right": (b[0] + b[2]) - (s[0] + s[2]),
                 "bottom": (b[1] + b[3]) - (s[1] + s[3])}
        bad = {k: v for k, v in spill.items() if v > tol}
        if bad:
            sides = ", ".join(f"{k} by {v:.0f}px" for k, v in bad.items())
            out.append(_finding(sl, "safe_area",
                                f"{label} is outside the safe area ({sides}).",
                                f"{_fmt_box(b)} vs safe {_fmt_box(s)}"))
    return out


def _check_image(sl: _Slide) -> list[dict]:
    return [_finding(sl, "image",
                     f"Image did not load: {_short(im['src'], 80)}. Fix the path or embed "
                     f"the file.", _fmt_box(im["box"]))
            for im in sl.images if not im["loaded"]]


# --------------------------------------------------------------------------
# reference
# --------------------------------------------------------------------------

def _hex_to_lab(hx: str):
    hx = hx.lstrip("#")
    if len(hx) == 3:
        hx = "".join(c * 2 for c in hx)
    r, g, b = (int(hx[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = lin(r), lin(g), lin(b)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _delta_e(a: str, b: str) -> float:
    la, lb = _hex_to_lab(a), _hex_to_lab(b)
    return math.sqrt(sum((p - q) ** 2 for p, q in zip(la, lb)))


_HEX = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


def _valid_hex(c: Any) -> bool:
    return isinstance(c, str) and _HEX.fullmatch(c) is not None


def _palette(slides: list[_Slide], min_share: float) -> list[tuple[str, float]]:
    """Colors with their share of text weight or of fill weight."""
    totals = {"text": 0.0, "fill": 0.0}
    weights: dict[tuple[str, str], float] = {}
    for sl in slides:
        for c in sl.colors:
            use = c.get("use") if c.get("use") in totals else "text"
            w = float(c.get("weight") or 0)
            if not _valid_hex(c["color"]) or w <= 0:
                continue
            key = (c["color"].lower(), use)
            weights[key] = weights.get(key, 0.0) + w
            totals[use] += w
    out = []
    for (color, use), w in weights.items():
        share = w / totals[use] if totals[use] else 0.0
        if share >= min_share:
            out.append((color, share))
    return out


def _margins(sl: _Slide):
    boxes = sl.content_boxes()
    if not boxes:
        return None
    u = _union(boxes)
    side = min(u[0], sl.W - (u[0] + u[2])) / sl.W
    top = u[1] / sl.H
    return side, top


def _role_fonts(sl: _Slide, role: str) -> list[float]:
    short = min(sl.W, sl.H)
    return [t["font_px"] / short for t in sl.texts if t["role"] == role and t["font_px"] > 0]


class _Reference:
    def __init__(self, reference: Any, cfg: dict):
        slides = []
        for raw in _slides(reference):
            W, H = _canvas(raw, None)
            slides.append(_Slide(raw, W, H, cfg))
        if not slides:
            raise ValueError("reference has no slides")
        # per slide kind: {"fonts": {role: share}, "side": share, "top": share}
        self.kinds: dict[str, dict] = {}
        for kind in ("content", "sparse"):
            group = [sl for sl in slides if sl.kind == kind]
            if not group:
                continue
            fonts = {}
            for role in cfg["reference_roles"]:
                vals = [v for sl in group for v in _role_fonts(sl, role)]
                if vals:
                    fonts[role] = statistics.median(vals)
            ms = [m for m in (_margins(sl) for sl in group) if m]
            self.kinds[kind] = {
                "fonts": fonts,
                "side": statistics.median(m[0] for m in ms) if ms else None,
                "top": statistics.median(m[1] for m in ms) if ms else None,
            }
        self.palette = [c for c, _ in _palette(slides, 0.0)]


def _check_reference(sl: _Slide, ref: _Reference) -> list[dict]:
    cfg = sl.cfg
    short = min(sl.W, sl.H)
    out = []
    prof = ref.kinds.get(sl.kind, {"fonts": {}, "side": None, "top": None})
    for role, ref_v in prof["fonts"].items():
        vals = _role_fonts(sl, role)
        if not vals or ref_v <= 0:
            continue
        cur = statistics.median(vals)
        if abs(cur / ref_v - 1) > cfg["reference_font_tol"]:
            out.append(_finding(sl, "reference",
                                f"Type scale drift: {role} text is {cur * short:.0f}px; the "
                                f"reference uses {ref_v * short:.0f}px on this canvas "
                                f"(allowed ±{cfg['reference_font_tol']:.0%}).",
                                role))
    m = _margins(sl)
    tol = cfg["reference_margin_tol"]
    if m and prof["side"] is not None:
        if abs(m[0] - prof["side"]) > tol:
            out.append(_finding(sl, "reference",
                                f"Margin drift: the side margin is {m[0] * sl.W:.0f}px; the "
                                f"reference uses {prof['side'] * sl.W:.0f}px "
                                f"(allowed ±{tol * sl.W:.0f}px).", "side margin"))
        if abs(m[1] - prof["top"]) > tol:
            out.append(_finding(sl, "reference",
                                f"Margin drift: the top margin is {m[1] * sl.H:.0f}px; the "
                                f"reference uses {prof['top'] * sl.H:.0f}px "
                                f"(allowed ±{tol * sl.H:.0f}px).", "top margin"))
    if ref.palette:
        for color, share in _palette([sl], cfg["palette_min_share"]):
            nearest = min(ref.palette, key=lambda c: _delta_e(color, c))
            d = _delta_e(color, nearest)
            if d > cfg["palette_delta_e"]:
                out.append(_finding(sl, "reference",
                                    f"Palette drift: {color} ({share:.0%} of its use) is not in "
                                    f"the reference palette; the nearest is {nearest} "
                                    f"(delta E {d:.0f}, allowed {cfg['palette_delta_e']:g}).",
                                    color))
    return out


# --------------------------------------------------------------------------
# public entry points
# --------------------------------------------------------------------------

_SLIDE_CHECKS = (_check_font_floor, _check_fill, _check_align, _check_nowrap,
                 _check_density, _check_overflow, _check_overlap, _check_safe_area,
                 _check_image)


def evaluate(measure: dict, canvas: dict | None = None, config: dict | None = None,
             reference: dict | None = None) -> list[dict]:
    """Judge a measurement and return the findings.

    ``measure``: one slide measurement from PROBE_JS, a list of them, or
    ``{"slides": [...]}``. ``canvas``: ``{"w", "h"}``; when empty, each
    slide's own w/h is used. ``config``: overrides for DEFAULTS (unknown keys
    raise ValueError). ``reference``: a measurement of a reference deck; when
    given, type scale, margins and palette drift are reported.

    Each finding is ``{"slide", "check", "level", "msg", "where"}``.
    Raises ValueError when the measure, the canvas or the config is invalid.
    """
    cfg = merge_config(config)
    ref = _Reference(reference, cfg) if reference is not None else None
    slides = _slides(measure)
    if not slides:
        raise ValueError("measure has no slides")
    findings: list[dict] = []
    for raw in slides:
        W, H = _canvas(raw, canvas)
        sl = _Slide(raw, W, H, cfg)
        for check in _SLIDE_CHECKS:
            findings.extend(check(sl))
        if ref is not None:
            findings.extend(_check_reference(sl, ref))
    return findings


def fingerprint(data: bytes) -> str:
    """FNV-1a (32 bit) of a file's bytes, as 8 hex digits: the probe's ``fp``."""
    h = 0x811C9DC5
    for b in data:
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def _norm(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def check_signature(measure: Any, signature: dict | None, config: dict | None = None) -> list[dict]:
    """The ``brand`` check: each configured signature element is on the work.

    ``signature`` is ``brand_config.signature()``: an unset element is not
    required, so with no brand configured (or ``signature`` None) there is no
    finding. ``measure`` is every slide of the work (one for a poster or a page);
    an element counts when it is on any slide, inside that slide's measured area:
    * watermark and link text: in the visible text (spaces and case ignored);
    * QR: a loaded image whose bytes are one of the configured QR files
      (``fp``; either variant counts).
    A missing element is reported on the last slide, which is the closing slide
    of a deck, so work.py can carry it over like any slide finding. A QR file
    that cannot be read is a finding with no slide (fix the brand file).
    Not measured: the link's href (a PNG has none), the footer lines
    (mark, name, tagline), the QR size and which variant suits the background.
    """
    if not signature:
        return []
    cfg = merge_config(config)
    slides = []
    for raw in _slides(measure):
        W, H = _canvas(raw, None)
        slides.append(_Slide(raw, W, H, cfg))
    if not slides:
        return []
    last = slides[-1]
    level = cfg["levels"]["brand"]

    def on_page(sl, box):
        ix, iy = _inter(box, (0, 0, sl.W, sl.H))
        return ix > 0 and iy > 0

    seen = _norm(" ".join(t["text"] for sl in slides for t in sl.texts if on_page(sl, t["box"])))
    fps = {im["fp"] for sl in slides for im in sl.images
           if im["fp"] and im["loaded"] and on_page(sl, im["box"])}
    out = []

    def missing(where: str, msg: str, slide: str | None = None) -> None:
        out.append({"slide": last.id if slide is None else slide, "check": "brand",
                    "level": level, "msg": msg, "where": where})

    watermark = signature.get("watermark")
    if watermark and _norm(watermark) not in seen:
        missing("watermark", f"The brand watermark \"{watermark}\" is not on the work. Add it, "
                             f"small, at the bottom (缺水印).")
    link_text = signature.get("link_text")
    if link_text and _norm(link_text) not in seen:
        missing("link", f"The brand link \"{link_text}\" is not on the work. Show it as text in "
                        f"the footer or on the closing slide (缺链接).")
    qr_paths = [p for p in (signature.get("qr") or {}).values() if p]
    wanted = {}
    for p in qr_paths:
        try:
            wanted[fingerprint(Path(p).read_bytes())] = Path(p).name
        except OSError:
            missing("qr", f"The brand QR file {p} cannot be read, so the QR is not checked. "
                          f"Fix the brand file (brand_config.py --signature).", slide="")
    if wanted and not fps & set(wanted):
        names = " or ".join(sorted(set(wanted.values())))
        missing("qr", f"The brand QR image ({names}) is not on the work. Copy it into "
                      f"src/assets/ and show it in the footer or on the closing slide (缺二维码).")
    return out


def _read_json(path: str, what: str):
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"{what} file not found: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{what} file is not valid JSON: {path} ({exc})")


def _parse_size(text: str) -> dict:
    try:
        w, h = text.lower().split("x")
        size = {"w": float(w), "h": float(h)}
    except ValueError:
        raise ValueError(f"--canvas must look like 1920x1080, got {text!r}")
    if size["w"] <= 0 or size["h"] <= 0:
        raise ValueError(f"--canvas must be positive, got {text!r}")
    return size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the Pollinate craft checks on a measurement made by PROBE_JS. "
                    "Exit 0 = no fail finding, 1 = a check failed, 2 = usage or input error.")
    parser.add_argument("--measure", required=True,
                        help="measure.json: one slide, a list of slides, or {\"slides\": [...]}")
    parser.add_argument("--reference", help="a measurement of the reference deck")
    parser.add_argument("--config", help="JSON object that overrides DEFAULTS")
    parser.add_argument("--canvas", help="canvas size WxH; default: each slide's w and h")
    args = parser.parse_args(argv)
    try:
        measure = _read_json(args.measure, "measure")
        reference = _read_json(args.reference, "reference") if args.reference else None
        config = _read_json(args.config, "config") if args.config else None
        canvas = _parse_size(args.canvas) if args.canvas else None
        findings = evaluate(measure, canvas, config, reference)
    except (ValueError, TypeError) as exc:
        # TypeError: a measure value of the wrong type (e.g. texts is a number)
        print(f"craft_checks: invalid input: {exc}", file=sys.stderr)
        return 2
    fails = sum(1 for f in findings if f["level"] == "fail")
    warns = len(findings) - fails
    print(json.dumps({"ok": fails == 0, "fails": fails, "warns": warns, "findings": findings},
                     ensure_ascii=False, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
