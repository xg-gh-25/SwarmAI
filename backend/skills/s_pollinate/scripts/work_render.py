#!/usr/bin/env python3
"""Render a Pollinate work with the pinned Chromium of SwarmAI's browser runtime.

This module is the renderer behind ``work.py render`` and ``work.py cut``. It
turns a work's source (one HTML file plus its local files) into the fixed
outputs of design section 13.2:

* ``deck.html``: one self-contained file. Every stylesheet, image, script and
  font is inlined, so it opens offline and every slide shows with JavaScript
  off. A print style gives one page per slide.
* ``deck.pdf``: printed from ``deck.html``, one page per slide.
* ``slides/NN-<id>.png``: one PNG per slide at the canvas size.
* ``poster.png`` (and ``poster@<w>x<h>.png``) for a poster, ``page.png`` and
  ``page.html`` for a page, ``copy.md`` for copy.
* ``sheet.jpg``: a contact sheet of all slides with page number and id.

Fonts: the first time a remote stylesheet (a web-font CSS) is linked, the CSS
and its font files are downloaded into the font cache
(``~/.swarm-ai/cache/fonts/``, or ``$POLLINATE_FONT_CACHE``) and rewritten to
local files. Later renders read the cache. Only https URLs on public addresses
are fetched. During capture the browser may load nothing but the rendered file
and ``data:`` URLs; every other request is blocked and reported.

The renderer also measures each slide with a probe (``PROBE_JS`` from
``craft_checks.py`` when the caller passes it, else the small built-in probe
here, which only looks for text overflow) and checks that each slide of
``deck.html`` is not blank with JavaScript disabled.

The browser is the product's pinned runtime (``core/feature_map/runtime.py``:
Node, @playwright/test and the Chromium headless shell, installed on first use
into the app data folder). ``work_render_driver.mjs`` next to this file runs on
that Node and drives Chromium; this module starts it and sends it JSON-line
requests (``_Browser``). There is no other renderer and no Python Playwright.

Use it from ``work.py``. Run directly, it renders one HTML file for a quick
look: ``work_render.py --src deck.html --out DIR [--kind deck] [--size 1920x1080]``.
Exit codes: 0 rendered without failed checks, 1 a check failed, 2 usage or
environment error (for example the runtime cannot be installed or Chromium does
not start), with the reason in one line.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import html.parser
import ipaddress
import json
import mimetypes
import os
import re
import shutil
import queue
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

MAX_DOWNLOAD = 20 * 1024 * 1024
DOWNLOAD_TIMEOUT = 20
NAV_TIMEOUT_MS = 60_000
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_EXTRA_TYPES = {
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
    ".otf": "font/otf", ".svg": "image/svg+xml", ".webp": "image/webp",
    ".avif": "image/avif", ".css": "text/css", ".js": "text/javascript",
}
_CSS_URL_RE = re.compile(r"""url\(\s*(?P<q>['"]?)(?P<u>[^'")]*?)(?P=q)\s*\)""", re.I)
_CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\(\s*(?P<q1>['"]?)(?P<u1>[^'")]*?)(?P=q1)\s*\)|(?P<q2>['"])(?P<u2>[^'"]*)(?P=q2))"""
    r"""(?P<rest>[^;]*);""", re.I)
_URL_ATTRS = {"src", "poster", "href", "xlink:href", "data"}
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class RenderEnvError(Exception):
    """The machine cannot render (the pinned runtime missing, Node or Chromium does not start)."""


class FetchError(Exception):
    """A remote stylesheet or font could not be fetched or is not allowed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path: Path) -> str:
    """Read a file as text without losing any byte (bad UTF-8 is kept as is)."""
    return path.read_bytes().decode("utf-8", "surrogateescape")


def _finding(check: str, level: str, msg: str, slide: str = "", where: str = "") -> dict:
    return {"slide": slide, "check": check, "level": level, "msg": msg, "where": where}


def _mime(path_or_url: str) -> str:
    ext = os.path.splitext(urllib.parse.urlsplit(path_or_url).path)[1].lower()
    if ext in _EXTRA_TYPES:
        return _EXTRA_TYPES[ext]
    return mimetypes.guess_type("x" + ext)[0] or "application/octet-stream"


def data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


# ── outbound fetch (font cache) ────────────────────────────────────────────

def _ip_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    ip = mapped or ip
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        return True
    return ip.version == 4 and (ip in _CGNAT or ip in ipaddress.ip_network("0.0.0.0/8"))


def _resolve(host: str) -> list:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def validate_url(url: str) -> None:
    """Refuse a URL that is not https or whose host has a non-public address.

    Every address the host resolves to is checked. A DNS answer that changes
    between this check and the connection is not covered (a known limit).
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise FetchError(f"only https URLs are fetched, not {parts.scheme or 'no scheme'}: {url}")
    host = parts.hostname
    if not host or parts.username or parts.password:
        raise FetchError(f"URL has no host or carries a user name: {url}")
    try:
        ips = _resolve(host)
    except OSError as exc:
        raise FetchError(f"cannot resolve {host}: {exc}") from None
    if not ips or any(_ip_blocked(ip) for ip in ips):
        raise FetchError(f"{host} resolves to a private or local address; not fetched")


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url: str) -> bytes:
    """GET an https URL after the address check, with size and time limits."""
    validate_url(url)
    opener = urllib.request.build_opener(_CheckedRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        # The URL is https and its host was checked above (and on every redirect).
        with opener.open(req, timeout=DOWNLOAD_TIMEOUT) as resp:  # nosec B310
            data = resp.read(MAX_DOWNLOAD + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FetchError(f"download failed for {url}: {exc}") from None
    if len(data) > MAX_DOWNLOAD:
        raise FetchError(f"{url} is larger than {MAX_DOWNLOAD // (1024 * 1024)} MB")
    return data


def _app_data_dir() -> Path:
    """The app data dir, resolved exactly as backend config.get_app_data_dir (a skill
    script cannot import backend config): $SWARM_DATA_DIR, then ~/.swarm-ai."""
    return Path(os.environ.get("SWARM_DATA_DIR") or Path.home() / ".swarm-ai")


def font_cache_dir() -> Path:
    env = os.environ.get("POLLINATE_FONT_CACHE")
    return Path(env) if env else _app_data_dir() / "cache" / "fonts"


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def cached_remote_css(url: str) -> Path:
    """Return the cached, local copy of a remote stylesheet; download it the first time.

    The cache entry is ``<cache>/<key>/style.css`` with every ``url()`` rewritten
    to a file next to it, plus ``source.json``. An entry is written to a temporary
    folder first and renamed when complete, so a failed download leaves nothing.
    """
    root = font_cache_dir()
    entry = root / _cache_key(url)
    css_path = entry / "style.css"
    if css_path.is_file():
        return css_path
    root.mkdir(parents=True, exist_ok=True)
    css = http_get(url).decode("utf-8", "replace")
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=root))
    try:
        files = {}

        def repl(m: re.Match) -> str:
            ref = m.group("u").strip()
            if not ref or ref.startswith(("data:", "#")):
                return m.group(0)
            absolute = urllib.parse.urljoin(url, ref)
            if absolute not in files:
                ext = os.path.splitext(urllib.parse.urlsplit(absolute).path)[1].lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,6}", ext or ""):
                    ext = ".bin"
                name = f"{len(files):03d}-{_cache_key(absolute)[:12]}{ext}"
                (tmp / name).write_bytes(http_get(absolute))
                files[absolute] = name
            return f'url("{files[absolute]}")'

        local_css = _CSS_URL_RE.sub(repl, css)
        (tmp / "style.css").write_text(local_css, encoding="utf-8")
        (tmp / "source.json").write_text(
            json.dumps({"url": url, "fetched": _now(), "files": files}, indent=1), encoding="utf-8")
        try:
            os.replace(tmp, entry)
        except OSError:
            if not css_path.is_file():  # another render did not win the race either
                raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return css_path


# ── inlining ───────────────────────────────────────────────────────────────

def _is_remote(ref: str) -> bool:
    low = ref.lower()
    return low.startswith(("http://", "https://", "//"))


def _skip_ref(ref: str) -> bool:
    low = ref.strip().lower()
    return (not low or low.startswith(("data:", "#", "mailto:", "tel:", "javascript:", "blob:", "about:")))


def resolve_local(ref: str, base_dir: Path, root: Path):
    """Resolve a relative reference to a file inside ``root``; None when outside."""
    path = urllib.parse.unquote(urllib.parse.urlsplit(ref).path)
    if not path or ref.lower().startswith("file:") or os.path.isabs(path):
        return None
    target = (base_dir / path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


class Inliner:
    """Turn a source HTML file and its local files into one self-contained HTML.

    Local files are read only inside ``root``. With ``fetch=False`` nothing is
    downloaded: remote references are skipped (used to list the local files a
    source depends on).
    """

    def __init__(self, root: Path, findings: list, fetch: bool = True):
        self.root = root.resolve()
        self.findings = findings
        self.fetch = fetch
        self.used_files: set = set()

    def _local_data(self, ref: str, base_dir: Path, root: Path) -> str:
        """A local file as a data URI, or an empty value with a finding."""
        target = resolve_local(ref, base_dir, root)
        if target is None:
            self.findings.append(_finding(
                "local_file", "fail",
                f"'{ref}' points outside the work folder; copy the file into the work and link it there",
                where=ref))
            return "data:,"
        if not target.is_file():
            self.findings.append(_finding("local_file", "fail", f"file not found: {ref}", where=ref))
            return "data:,"
        if root == self.root:
            self.used_files.add(target)
        return data_uri(target.read_bytes(), _mime(target.name))

    def _remote_file_data(self, url: str) -> str:
        """A remote file named in a local stylesheet (usually a font): cache, inline."""
        if not self.fetch:
            return url
        try:
            cached = cached_remote_file(url)
        except FetchError as exc:
            self.findings.append(_finding("font_fetch", "fail", str(exc), where=url))
            return "data:,"
        return data_uri(cached.read_bytes(), _mime(url))

    def css(self, text: str, base_dir: Path, root=None, depth: int = 0) -> str:
        """Inline @import and url() of a stylesheet; files resolve inside ``root``."""
        root = root or self.root
        if depth > 6:
            self.findings.append(_finding("local_file", "fail", "@import nested too deep"))
            return text

        def imp(m: re.Match) -> str:
            ref = (m.group("u1") or m.group("u2") or "").strip()
            media = (m.group("rest") or "").strip()
            body = self.stylesheet(ref, base_dir, root, depth + 1)
            if body is None:
                return "" if self.fetch or not _is_remote(ref) else m.group(0)
            return f"@media {media} {{\n{body}\n}}" if media else body

        text = _CSS_IMPORT_RE.sub(imp, text)

        def url(m: re.Match) -> str:
            ref = m.group("u").strip()
            if _skip_ref(ref):
                return m.group(0)
            if _is_remote(ref):
                return f'url("{self._remote_file_data(_abs_url(ref))}")'
            return f'url("{self._local_data(ref, base_dir, root)}")'

        return _CSS_URL_RE.sub(url, text)

    def stylesheet(self, ref: str, base_dir: Path, root, depth: int = 0):
        """The inlined text of a linked stylesheet, or None when it cannot be read."""
        if _is_remote(ref):
            if not self.fetch:
                return None
            url = _abs_url(ref)
            try:
                css_path = cached_remote_css(url)
            except FetchError as exc:
                self.findings.append(_finding("font_fetch", "fail", str(exc), where=url))
                return None
            entry = css_path.parent.resolve()
            return self.css(read_text(css_path), entry, entry, depth)
        target = resolve_local(ref, base_dir, root)
        if target is None or not target.is_file():
            self.findings.append(_finding(
                "local_file", "fail", f"stylesheet not found inside the work: {ref}", where=ref))
            return None
        if root == self.root:
            self.used_files.add(target)
        return self.css(read_text(target), target.parent, root, depth)

    def html(self, text: str, base_dir: Path) -> str:
        scan = _TagScanner(text)
        root = self.root
        edits = []  # (start, end, replacement)
        for tag in scan.tags:
            name, attrs = tag["tag"], tag["attrs"]
            amap = {k: v for k, v in attrs}
            rel = (amap.get("rel") or "").lower().split()
            href = (amap.get("href") or "").strip()
            if name == "link" and "stylesheet" in rel and href:
                body = self.stylesheet(href, base_dir, root)
                if body is None and not self.fetch and _is_remote(href):
                    continue
                open_tag = f'<style data-pollinate-from="{html.escape(href, quote=True)}"'
                if amap.get("media"):
                    open_tag += f' media="{html.escape(amap["media"], quote=True)}"'
                edits.append((tag["start"], tag["end"], f"{open_tag}>\n{_style_safe(body or '')}\n</style>"))
                continue
            if name == "link" and _is_remote(href):
                edits.append((tag["start"], tag["end"], ""))  # preconnect, preload or icon from the web
                continue
            if name == "script" and amap.get("src"):
                src = amap["src"].strip()
                if _is_remote(src):
                    self.findings.append(_finding(
                        "remote_script", "fail",
                        f"remote script is not loaded offline: {src}; save it into the work", where=src))
                    edits.append((tag["start"], tag["element_end"], ""))
                    continue
                target = resolve_local(src, base_dir, root)
                if target is None or not target.is_file():
                    self.findings.append(_finding(
                        "local_file", "fail", f"script not found inside the work: {src}", where=src))
                    edits.append((tag["start"], tag["element_end"], ""))
                    continue
                self.used_files.add(target)
                code = read_text(target).replace("</script", "<\\/script")
                kept = [(k, v) for k, v in attrs if k != "src"]
                edits.append((tag["start"], tag["element_end"], f"{_build_tag('script', kept)}{code}</script>"))
                continue
            changed = False
            new_attrs = []
            for k, v in attrs:
                if v is not None and k in _URL_ATTRS and name not in ("a", "area", "base", "form"):
                    ref = v.strip()
                    if not (name == "link" and not rel) and not _skip_ref(ref) and not _is_remote(ref):
                        v = self._local_data(ref, base_dir, root)
                        changed = True
                elif v is not None and k == "srcset":
                    v = self._srcset(v, base_dir)
                    changed = True
                elif v is not None and k == "style" and "url(" in v.lower():
                    v = self.css(v, base_dir)
                    changed = True
                new_attrs.append((k, v))
            if changed:
                edits.append((tag["start"], tag["end"], _build_tag(name, new_attrs, tag["selfclose"])))
        for start, end in scan.style_ranges:
            edits.append((start, end, _style_safe(self.css(text[start:end], base_dir))))
        out = text
        for start, end, rep in sorted(edits, key=lambda e: e[0], reverse=True):
            out = out[:start] + rep + out[end:]
        return out

    def _srcset(self, value: str, base_dir: Path) -> str:
        parts = []
        for item in value.split(","):
            bits = item.strip().split(None, 1)
            if not bits:
                continue
            ref = bits[0]
            if not _skip_ref(ref) and not _is_remote(ref):
                ref = self._local_data(ref, base_dir, self.root)
            parts.append(" ".join([ref] + bits[1:]))
        return ", ".join(parts)


def _style_safe(css: str) -> str:
    """Text that cannot close the <style> element it is pasted into."""
    return re.sub(r"</(style)", r"<\\/\1", css, flags=re.I)


def local_files_of(src_root: Path, source: Path) -> set:
    """The local files a source uses (through CSS too), without any download."""
    inl = Inliner(src_root, [], fetch=False)
    inl.html(read_text(source), source.parent)
    return inl.used_files


def _abs_url(ref: str) -> str:
    return "https:" + ref if ref.startswith("//") else ref


def cached_remote_file(url: str) -> Path:
    """Cache one remote file named directly in a local stylesheet (a font file)."""
    root = font_cache_dir() / "files"
    root.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,6}", ext or ""):
        ext = ".bin"
    path = root / f"{_cache_key(url)}{ext}"
    if not path.is_file():
        data = http_get(url)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=root)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    return path


def _build_tag(name: str, attrs, selfclose: bool = False) -> str:
    parts = [name]
    for k, v in attrs:
        parts.append(k if v is None else f'{k}="{html.escape(v, quote=True)}"')
    return "<" + " ".join(parts) + (" />" if selfclose else ">")


class _TagScanner(html.parser.HTMLParser):
    """Record start tags and <style> contents with their offsets in the text."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self._line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.tags: list = []
        self.style_ranges: list = []
        self._style_start = None
        self._script = None
        self.feed(text)
        self.close()

    def _pos(self) -> int:
        line, col = self.getpos()
        return self._line_starts[line - 1] + col

    def _start(self, tag, attrs, selfclose):
        start = self._pos()
        raw = self.get_starttag_text() or ""
        rec = {"tag": tag, "attrs": attrs, "start": start, "end": start + len(raw),
               "element_end": start + len(raw), "selfclose": selfclose}
        self.tags.append(rec)
        if tag == "style" and not selfclose:
            self._style_start = rec["end"]
        if tag == "script" and not selfclose:
            self._script = rec

    def handle_starttag(self, tag, attrs):
        self._start(tag, attrs, False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, attrs, True)

    def handle_endtag(self, tag):
        pos = self._pos()
        if tag == "style" and self._style_start is not None:
            self.style_ranges.append((self._style_start, pos))
            self._style_start = None
        if tag == "script" and self._script is not None:
            close = self.text.find(">", pos)
            self._script["element_end"] = close + 1 if close >= 0 else len(self.text)
            self._script = None


# ── capture ────────────────────────────────────────────────────────────────

BUILTIN_PROBE_JS = r"""
(slideId) => {
  const root = document.querySelector('[data-slide-id="' + CSS.escape(slideId) + '"]') || document.body;
  const r0 = root.getBoundingClientRect();
  const rel = (r) => [Math.round(r.left - r0.left), Math.round(r.top - r0.top),
                      Math.round(r.width), Math.round(r.height)];
  const roleOf = (el) => {
    if (el.dataset && el.dataset.role) return el.dataset.role;
    const tag = el.tagName.toLowerCase();
    const cls = (el.className && el.className.toString()) || '';
    if (tag === 'h1' || tag === 'h2' || /\btitle\b/.test(cls)) return 'title';
    if (/^h[3-6]$/.test(tag) || /\bsubtitle\b/.test(cls)) return 'subtitle';
    if (tag === 'figcaption' || /\bcaption\b/.test(cls)) return 'caption';
    if (tag === 'label' || /\blabel\b/.test(cls)) return 'label';
    return 'body';
  };
  const texts = [];
  for (const el of root.querySelectorAll('*')) {
    const own = Array.from(el.childNodes).filter(n => n.nodeType === 3 && n.textContent.trim());
    if (!own.length) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = Array.from(range.getClientRects()).filter(r => r.width > 0 && r.height > 0);
    if (!rects.length) continue;
    const tops = new Set(rects.map(r => Math.round(r.top)));
    const tb = range.getBoundingClientRect();
    const text = own.map(n => n.textContent).join(' ').replace(/\s+/g, ' ').trim();
    texts.push({
      text: text.slice(0, 200), role: roleOf(el), font_px: parseFloat(cs.fontSize),
      lines: tops.size, nowrap: cs.whiteSpace.indexOf('nowrap') >= 0 || el.hasAttribute('data-nowrap'),
      box: rel(tb), container: rel(el.getBoundingClientRect()), chars: text.length,
    });
  }
  const images = Array.from(root.querySelectorAll('img')).map(img => ({
    src: (img.getAttribute('src') || '').slice(0, 80), box: rel(img.getBoundingClientRect()),
    loaded: img.complete && img.naturalWidth > 0,
  }));
  return {slide: slideId, w: Math.round(r0.width), h: Math.round(r0.height),
          texts: texts, blocks: [], images: images};
}
"""

# deck.html opened with JavaScript off (a shared file in a mail or chat preview): a deck
# runtime's entrance effects keep content at opacity 0 until a script marks the slide
# active (the shipped <deck-stage> designs key ``.reveal`` on ``data-deck-active``), so the
# slide shows empty. Only without JavaScript, everything a slide holds is made visible;
# with JavaScript on this changes nothing, so the presented deck keeps its effects.
_NOSCRIPT_CSS = ("<noscript><style>section, section *"
                 "{opacity:1!important;visibility:visible!important}</style></noscript>")

_MARK_SLIDES_JS = r"""
() => {
  const all = Array.from(document.querySelectorAll('section')).filter(
    s => !(s.parentElement && s.parentElement.closest('section')));
  let n = 0;
  return all.map((s, i) => {
    s.setAttribute('data-pollinate-index', String(i));
    if (!s.getAttribute('data-slide-id')) s.setAttribute('data-slide-id', 'new-' + (++n));
    return s.getAttribute('data-slide-id');
  });
}
"""

_SETTLE_JS = r"""
async () => {
  await Promise.all(Array.from(document.fonts).map(f => f.load().catch(() => null)));
  await document.fonts.ready;
  await Promise.all(Array.from(document.images).map(i => i.decode ? i.decode().catch(() => null) : null));
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  return Array.from(document.fonts).filter(f => f.status === 'error').map(f => f.family);
}
"""


def builtin_evaluate(measure: dict, canvas: dict) -> list:
    """The fallback check when craft_checks.py is not installed: text overflow only."""
    out = []
    w, h = canvas.get("w") or measure.get("w"), canvas.get("h") or measure.get("h")
    for t in measure.get("texts", []):
        x, y, bw, bh = t["box"]
        cx, cy, cw, ch = t["container"]
        if x < -1 or y < -1 or x + bw > w + 1 or y + bh > h + 1:
            out.append(_finding("overflow", "fail", "text goes outside the slide", measure["slide"], t["text"][:60]))
        elif x < cx - 1 or y < cy - 1 or x + bw > cx + cw + 1 or y + bh > cy + ch + 1:
            out.append(_finding("overflow", "fail", "text overflows its box", measure["slide"], t["text"][:60]))
    return out


def _is_blank(png: bytes) -> bool:
    from PIL import Image  # Pillow ships with the SwarmAI backend
    img = Image.open(BytesIO(png)).convert("L")
    lo, hi = img.getextrema()
    return hi - lo < 8


def _pdf_pages(path: Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(path)).pages)
    except ImportError:
        return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", path.read_bytes()))


def _print_css(w: int, h: int) -> str:
    top = "section:not(section section)"
    return (
        '<style data-pollinate="print">'
        f"@page{{size:{w}px {h}px;margin:0}}"
        "@media print{html,body{margin:0!important;padding:0!important}"
        f"body>*:not(section):not(:has({top})){{display:none!important}}"
        f"{top}{{width:{w}px!important;height:{h}px!important;overflow:hidden!important;"
        "break-after:page;break-inside:avoid;margin:0!important;box-sizing:border-box}"
        f"{top}:last-of-type{{break-after:auto}}}}"
        "</style>")


_CAPTURE_CSS = (
    "body[data-pollinate-capture] section:not(section section):not([data-pollinate-current])"
    "{display:none!important}"
    # A deck runtime may hide inactive slides with visibility/opacity instead of display
    # (the shipped <deck-stage> does: visibility:hidden + opacity:0). The slide being
    # captured is shown whatever its runtime thinks the active slide is.
    "body[data-pollinate-capture] [data-pollinate-current]"
    "{visibility:visible!important;opacity:1!important}")

# Mark the slide being captured. Besides our own attribute it gets ``data-deck-active``, the
# active-slide attribute of the shipped <deck-stage> runtime, which design-system CSS keys
# entrance effects on (``section[data-deck-active] .reveal``); and every <deck-stage> gets
# ``noscale``, its documented switch to render at the authored size for capture.
_MARK_CURRENT_JS = (
    "(i) => { document.querySelectorAll('deck-stage').forEach(d => d.setAttribute('noscale', ''));"
    " document.querySelectorAll('[data-pollinate-index]').forEach(s => {"
    " const on = s.getAttribute('data-pollinate-index') === String(i);"
    " s.toggleAttribute('data-pollinate-current', on); s.toggleAttribute('data-deck-active', on); }); }")


def _insert_head(text: str, snippet: str) -> str:
    m = re.search(r"</head\s*>", text, re.I)
    if m:
        return text[:m.start()] + snippet + "\n" + text[m.start():]
    return snippet + "\n" + text


DRIVER = Path(__file__).resolve().parent / "work_render_driver.mjs"
START_HANG_S = 180      # the driver loads Playwright and launches Chromium (its own cap: 60 s)
REQUEST_HANG_S = 300    # one request; Playwright's own cap is 60 s, but evaluate has none
_NO_ARG = object()


class RenderError(Exception):
    """A browser request failed during a render (the page's own error message)."""


def _runtime_module():
    """``core.feature_map.runtime``: as the backend interpreter has it, else from the
    backend folder this skill lives in (``<backend>/skills/s_pollinate/scripts``)."""
    try:
        from core.feature_map import runtime
        return runtime
    except ImportError:
        pass
    for parent in Path(__file__).resolve().parents:
        if (parent / "core" / "feature_map" / "runtime.py").is_file():
            sys.path.insert(0, str(parent))
            try:
                from core.feature_map import runtime
                return runtime
            except ImportError:
                break
    raise RenderEnvError(
        "the renderer needs SwarmAI's pinned browser runtime (core.feature_map.runtime), which "
        'cannot be imported here; run the script with "$SWARM_BACKEND_BIN" py')


def browser_runtime():
    """The pinned Node + Playwright + Chromium runtime, installed on first use.

    Raises RenderEnvError with the runtime's reason when it cannot be had.
    """
    runtime = _runtime_module()
    try:
        if (not os.environ.get("SWARM_FEATURE_MAP_RUNTIME")
                and not (runtime.runtime_dir() / "ready.json").is_file()):
            print("work_render: installing the pinned browser runtime "
                  "(a one-time download on first use)", file=sys.stderr)
        return runtime.ensure()
    except runtime.RuntimeUnavailable as exc:
        raise RenderEnvError(f"the pinned browser runtime is not available: {_one_line(exc.reason)}") from None
    except (ImportError, OSError) as exc:
        raise RenderEnvError(f"the pinned browser runtime is not available: {_one_line(str(exc))}") from None


# What the driver's Node and Chromium get from the caller's environment: what they need to
# run and lay out text (paths, temp, locale, time zone, fonts, libraries); nothing else, so
# no credential and no variable that changes how Node or Playwright behave reaches them.
_DRIVER_ENV = (
    "PATH", "HOME", "USERPROFILE", "TMPDIR", "TEMP", "TMP", "SystemRoot", "windir",
    "LOCALAPPDATA", "APPDATA", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TZ",
    "FONTCONFIG_FILE", "FONTCONFIG_PATH", "LD_LIBRARY_PATH", "XDG_RUNTIME_DIR",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_DATA_DIRS",
)


def driver_env(rt) -> dict:
    """The driver's environment: the allowed caller variables, the runtime's own, and
    the pinned @playwright/test entry point."""
    env = {k: v for k, v in os.environ.items() if k in _DRIVER_ENV}
    env.update(rt.env())
    env["POLLINATE_PLAYWRIGHT"] = (Path(rt.playwright_pkg) / "index.mjs").resolve().as_uri()
    return env


def _one_line(text: str, limit: int = 600) -> str:
    return " | ".join(ln.strip() for ln in str(text).splitlines() if ln.strip())[:limit]


class _Browser:
    """One Chromium for a render, driven by ``work_render_driver.mjs`` on the pinned runtime.

    Every page the driver opens allows only the URLs it is given and ``data:``, ``blob:``
    and ``about:`` URLs; every other request, and every WebSocket, is blocked, and
    ``close`` returns them.
    """

    def __init__(self):
        rt = browser_runtime()
        env = driver_env(rt)
        self._err = tempfile.TemporaryFile()
        self._next = 0
        try:
            self._proc = subprocess.Popen(
                [str(rt.node), str(DRIVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._err, env=env, cwd=str(DRIVER.parent),
                text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            self._err.close()
            raise RenderEnvError(f"the pinned Node could not start: {exc}") from None
        self._replies: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        hello = self._reply(START_HANG_S)
        if not hello or not hello.get("ready"):
            reason = (hello or {}).get("error") or self._stderr_tail() or "the driver printed nothing"
            self._stop(kill=True)
            raise RenderEnvError(f"Chromium did not start ({_one_line(reason, 300)})")
        self.version = hello.get("version")

    def _read(self) -> None:
        stdout = self._proc.stdout
        try:
            for line in stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue            # not a reply (a stray print); replies are JSON objects
                if isinstance(msg, dict):
                    self._replies.put(msg)
        except (OSError, ValueError):
            pass                        # the stream was closed by _stop
        self._replies.put(None)         # the driver's stdout closed

    def _reply(self, wait_s: float):
        try:
            return self._replies.get(timeout=wait_s)
        except queue.Empty:
            return None

    def _stderr_tail(self) -> str:
        try:
            self._err.seek(0)
            text = self._err.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            return ""
        return _one_line("\n".join(text.strip().splitlines()[-4:]))

    def request(self, op: str, **fields):
        """Send one request and return its value; RenderError when it failed."""
        if self._proc is None:
            raise RenderError(f"the browser driver is closed ('{op}')")
        self._next += 1
        rid = self._next
        try:
            self._proc.stdin.write(json.dumps({"id": rid, "op": op, **fields}) + "\n")
            self._proc.stdin.flush()
        except OSError:
            pass                        # the driver is gone: the reply below says so
        try:
            msg = self._replies.get(timeout=REQUEST_HANG_S)
        except queue.Empty:
            self._stop(kill=True)
            raise RenderError(f"the browser driver did not answer '{op}' in {REQUEST_HANG_S}s") from None
        if msg is None:
            tail = self._stderr_tail()
            self._stop(kill=True)
            raise RenderError(f"the browser driver stopped during '{op}'" + (f": {tail}" if tail else ""))
        if msg.get("id") != rid:
            self._stop(kill=True)
            raise RenderError(f"the browser driver answered request {msg.get('id')} for request {rid}")
        if not msg.get("ok"):
            raise RenderError(msg.get("error") or f"'{op}' failed")
        return msg.get("value")

    def open(self, w: int, h: int, url: str, allowed: set, js: bool = True) -> "_Page":
        """Open ``url`` in a new w x h context and wait for load."""
        value = self.request("open", w=w, h=h, url=url, allowed=sorted(allowed), js=js)
        return _Page(self, value["page"])

    def close(self) -> list:
        """Stop the driver and Chromium; return the URLs that were blocked."""
        if self._proc is None:
            return []
        blocked = self.request("blocked") or []
        self._stop()
        return blocked

    def _stop(self, kill: bool = False) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if kill:
                proc.kill()
            else:
                proc.stdin.close()
            proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait()
        finally:
            for stream in (proc.stdin, proc.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            self._err.close()


class _Page:
    """A page the driver opened (its own browser context)."""

    def __init__(self, browser: _Browser, page_id: int):
        self._b = browser
        self.id = page_id

    def evaluate(self, js: str, arg=_NO_ARG):
        fields = {"page": self.id, "js": js}
        if arg is not _NO_ARG:
            fields["arg"] = arg
        return self._b.request("eval", **fields)

    def bounding_box(self, selector: str, nth=None):
        return self._b.request("box", page=self.id, selector=selector, nth=nth)

    def count(self, selector: str) -> int:
        return int(self._b.request("count", page=self.id, selector=selector))

    def get_attribute(self, selector: str, nth: int, name: str):
        return self._b.request("attr", page=self.id, selector=selector, nth=nth, name=name)

    def add_style_tag(self, css: str) -> None:
        self._b.request("style", page=self.id, css=css)

    def emulate_media(self, media: str) -> None:
        self._b.request("media", page=self.id, media=media)

    def screenshot(self, selector=None, nth=None, full_page: bool = False, jpeg_quality=None) -> bytes:
        """PNG (or JPEG with ``jpeg_quality``) of the element or the page, animations off."""
        fields = {"page": self.id, "selector": selector, "nth": nth, "full_page": full_page,
                  "type": "jpeg" if jpeg_quality is not None else "png"}
        if jpeg_quality is not None:
            fields["quality"] = jpeg_quality
        return base64.b64decode(self._b.request("shot", **fields))

    def pdf(self, path: Path) -> None:
        self._b.request("pdf", page=self.id, path=str(Path(path).resolve()))

    def close(self) -> None:
        if self._b._proc is not None:
            self._b.request("close", page=self.id)


def _write_sheet(browser: _Browser, items: list, path: Path) -> None:
    """A contact sheet: every image in a grid, labelled 'page · id'."""
    cells = "".join(
        f'<figure><img src="{data_uri(png, "image/png")}"><figcaption>{html.escape(label)}</figcaption></figure>'
        for label, png in items)
    doc = ("<!doctype html><meta charset='utf-8'><style>"
           "body{margin:0;padding:24px;background:#e9e9ec;font:600 20px sans-serif;color:#222}"
           ".g{display:grid;grid-template-columns:repeat(4,480px);gap:20px}"
           "figure{margin:0;background:#fff;padding:8px;box-shadow:0 1px 3px #0003}"
           "img{display:block;width:100%;height:auto}figcaption{padding-top:6px}"
           f"</style><div class='g'>{cells}</div>")
    tmp = path.with_suffix(".html")
    tmp.write_text(doc, encoding="utf-8")
    try:
        url = tmp.resolve().as_uri()
        page = browser.open(2100, 800, url, {url})
        try:
            path.write_bytes(page.screenshot(full_page=True, jpeg_quality=82))
        finally:
            page.close()
    finally:
        tmp.unlink(missing_ok=True)


def _clear_outputs(out_dir: Path) -> None:
    """Remove only the files this renderer writes; leave anything else alone."""
    shutil.rmtree(out_dir / "slides", ignore_errors=True)
    for pattern in ("deck.html", "deck.pdf", "deck.pptx", "poster.png", "poster@*.png",
                    "page.png", "page.html", "copy.md", "sheet.jpg"):
        for p in out_dir.glob(pattern):
            if p.is_file():
                p.unlink()


def render(src_root: Path, source_rel: str, kind: str, size: dict, out_dir: Path,
           review_dir, formats: list, probe_js=None, extra_sizes=None) -> dict:
    """Render one work source. Returns outputs (sha256), measure and findings.

    ``src_root`` is the folder the source and its local files live in; nothing
    outside it is read. ``review_dir`` gets ``sheet.jpg`` (None: no sheet).
    Raises RenderEnvError when the browser cannot run, RenderError when a browser
    request fails during the render.
    """
    src_root = Path(src_root).resolve()
    source = (src_root / source_rel).resolve()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_outputs(out_dir)
    if review_dir is not None:
        Path(review_dir).mkdir(parents=True, exist_ok=True)
        _clear_outputs(Path(review_dir))
    w, h = int(size["w"]), int(size["h"])
    findings: list = []
    outputs: dict = {}
    measures: list = []
    result = {"outputs": outputs, "measure": measures, "findings": findings,
              "probe": "craft_checks" if probe_js else "builtin"}

    if kind == "copy":
        dest = out_dir / "copy.md"
        shutil.copyfile(source, dest)
        outputs["md"] = sha256_file(dest)
        return result

    text = Inliner(src_root, findings).html(read_text(source), source.parent)
    if kind == "deck":
        text = _insert_head(text, _print_css(w, h) + _NOSCRIPT_CSS)
        doc = out_dir / "deck.html"
    elif kind == "page":
        doc = out_dir / "page.html"
    else:
        doc = out_dir / ".poster-capture.html"
    doc.write_bytes(text.encode("utf-8", "surrogateescape"))
    doc_url = doc.resolve().as_uri()
    probe = probe_js or BUILTIN_PROBE_JS

    browser = None
    blocked: list = []
    try:
        browser = _Browser()
        page = browser.open(w, h, doc_url, {doc_url})
        try:
            for fam in page.evaluate(_SETTLE_JS):
                findings.append(_finding("font_load", "fail", f"font '{fam}' failed to load", where=fam))
            if kind == "deck":
                _capture_deck(page, w, h, out_dir, doc, formats, findings, outputs, measures, probe)
            else:
                page.evaluate("() => document.body.setAttribute('data-slide-id', 'main')")
                if kind == "poster":
                    png = page.screenshot()
                    (out_dir / "poster.png").write_bytes(png)
                    outputs["poster"] = sha256_bytes(png)
                else:
                    png = page.screenshot(full_page=True)
                    (out_dir / "page.png").write_bytes(png)
                    outputs["page"] = sha256_bytes(png)
                    outputs["html"] = sha256_file(doc)
                measures.append(page.evaluate(probe, "main"))
        finally:
            page.close()
        if kind == "deck" and "html" in formats:
            _check_js_off(browser, w, h, doc, doc_url, findings)
        if kind == "poster":
            for extra in extra_sizes or []:
                ew, eh = int(extra["w"]), int(extra["h"])
                epage = browser.open(ew, eh, doc_url, {doc_url})
                try:
                    epage.evaluate(_SETTLE_JS)
                    png = epage.screenshot()
                finally:
                    epage.close()
                (out_dir / f"poster@{ew}x{eh}.png").write_bytes(png)
                outputs[f"poster@{ew}x{eh}"] = sha256_bytes(png)
        if review_dir is not None:
            items = []
            if kind == "deck":
                for i, p in enumerate(sorted((out_dir / "slides").glob("*.png")), 1):
                    items.append((f"{i} · {p.stem.split('-', 1)[1]}", p.read_bytes()))
            else:
                items = [(p.name, p.read_bytes()) for p in sorted(out_dir.glob("*.png"))]
            if items:
                _write_sheet(browser, items, Path(review_dir) / "sheet.jpg")
        blocked = browser.close()
    finally:
        if browser is not None:
            browser._stop(kill=True)    # a no-op after close(); stops the driver on an error
        if kind == "poster":
            doc.unlink(missing_ok=True)
    for url in blocked:
        findings.append(_finding(
            "blocked_request", "fail",
            f"the page asked for a file that is not part of the work, which is blocked: {url}. "
            "Save the file into the work and link it there.", where=url))
    if kind == "deck" and "html" not in formats:
        doc.unlink(missing_ok=True)
    return result


def _capture_deck(page, w, h, out_dir, doc, formats, findings, outputs, measures, probe):
    ids = page.evaluate(_MARK_SLIDES_JS)
    if not ids:
        findings.append(_finding("slides", "fail", "the deck has no <section> slides"))
        return
    page.add_style_tag(_CAPTURE_CSS)
    slides_dir = out_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    shas = {}
    page.evaluate("() => document.body.setAttribute('data-pollinate-capture', '')")
    for i, sid in enumerate(ids):
        page.evaluate(_MARK_CURRENT_JS, i)
        page.evaluate(_SETTLE_JS)
        sel = f'[data-pollinate-index="{i}"]'
        box = page.bounding_box(sel)
        if not box or box["width"] < 1 or box["height"] < 1:
            findings.append(_finding("slide_size", "fail", "slide is not visible when rendered", sid))
            continue
        png = page.screenshot(sel)
        if round(box["width"]) != w or round(box["height"]) != h:
            findings.append(_finding(
                "slide_size", "fail",
                f"slide renders at {round(box['width'])}x{round(box['height'])}, the canvas is {w}x{h}", sid))
        if "png" in formats or "pptx" in formats:
            (slides_dir / f"{i + 1:02d}-{sid}.png").write_bytes(png)
        shas[sid] = sha256_bytes(png)
        measures.append(page.evaluate(probe, sid))
    page.evaluate("() => document.body.removeAttribute('data-pollinate-capture')")
    outputs["slides"] = shas
    if "pdf" in formats:
        pdf = out_dir / "deck.pdf"
        page.emulate_media("print")
        page.pdf(pdf)
        page.emulate_media("screen")
        pages = _pdf_pages(pdf)
        if pages != len(ids):
            findings.append(_finding("pdf_pages", "fail", f"deck.pdf has {pages} pages, the deck has {len(ids)} slides"))
        outputs["pdf"] = sha256_file(pdf)
    if "html" in formats:
        outputs["html"] = sha256_file(doc)


_BLANK_LOOKS = 4          # screenshots of a slide before it counts as blank
_BLANK_PAUSE_S = 0.5      # between them: a frame scrolled into view paints a moment later


def _stays_blank(page, sel: str, i: int) -> bool:
    """True when the slide is blank in every look.

    The first screenshot after scrolling a slide into view can be taken before an embedded
    frame on it (a ``data:`` iframe, say) has painted: measured on a real deck, a slide was
    blank in the first look and complete one second later. A slide that is really blank
    stays blank in every look.
    """
    for look in range(_BLANK_LOOKS):
        if look:
            time.sleep(_BLANK_PAUSE_S)
        if not _is_blank(page.screenshot(sel, i)):
            return False
    return True


def _check_js_off(browser, w, h, doc, doc_url, findings):
    """Open deck.html with JavaScript disabled; every slide must show something."""
    page = browser.open(w, h, doc_url, {doc_url}, js=False)
    try:
        sel = "section:not(section section)"
        for i in range(page.count(sel)):
            sid = page.get_attribute(sel, i, "data-slide-id") or f"page {i + 1}"
            box = page.bounding_box(sel, i)
            if not box or box["width"] < 1 or box["height"] < 1 or _stays_blank(page, sel, i):
                findings.append(_finding(
                    "js_off_blank", "fail",
                    "this slide is blank in deck.html when JavaScript is off; "
                    "do not hide slides until a script runs", sid))
    finally:
        page.close()


def parse_size(value: str) -> dict:
    m = re.fullmatch(r"\s*(\d{2,5})\s*[xX]\s*(\d{2,5})\s*", value or "")
    if not m:
        raise ValueError(f"size must look like 1920x1080, not '{value}'")
    return {"w": int(m.group(1)), "h": int(m.group(2))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render one HTML source for a quick look (no work, no version).")
    ap.add_argument("--src", required=True, help="the HTML file to render")
    ap.add_argument("--out", required=True, help="the folder to write outputs into")
    ap.add_argument("--kind", default="deck", choices=["deck", "poster", "page"])
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--formats", default="html,pdf,png")
    args = ap.parse_args(argv)
    src = Path(args.src)
    if not src.is_file():
        print(f"error: {src} is not a file", file=sys.stderr)
        return 2
    try:
        size = parse_size(args.size)
        res = render(src.parent, src.name, args.kind, size, Path(args.out), Path(args.out),
                     [f.strip() for f in args.formats.split(",") if f.strip()])
    except (RenderEnvError, RenderError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for m in res["measure"]:
        res["findings"].extend(builtin_evaluate(m, size))
    print(json.dumps({"outputs": res["outputs"], "findings": res["findings"]}, indent=1, ensure_ascii=False))
    return 1 if any(f["level"] == "fail" for f in res["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
