#!/usr/bin/env python3
"""Render a Pollinate work with Playwright Chromium.

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

Use it from ``work.py``. Run directly, it renders one HTML file for a quick
look: ``work_render.py --src deck.html --out DIR [--kind deck] [--size 1920x1080]``.
Exit codes: 0 rendered without failed checks, 1 a check failed, 2 usage or
environment error (for example Playwright or Chromium missing).
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
import socket
import sys
import tempfile
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
    """The machine cannot render (Playwright or Chromium missing, launch failed)."""


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


def font_cache_dir() -> Path:
    env = os.environ.get("POLLINATE_FONT_CACHE")
    return Path(env) if env else Path.home() / ".swarm-ai" / "cache" / "fonts"


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
    "{display:none!important}")


def _insert_head(text: str, snippet: str) -> str:
    m = re.search(r"</head\s*>", text, re.I)
    if m:
        return text[:m.start()] + snippet + "\n" + text[m.start():]
    return snippet + "\n" + text


class _Browser:
    """One Chromium for a render; every context allows only local and data: URLs."""

    def __init__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RenderEnvError(
                "Playwright for Python is not installed. Install it in user space: "
                "python3 -m pip install --user playwright && python3 -m playwright install chromium"
            ) from None
        self._pw_cm = sync_playwright()
        try:
            self._pw = self._pw_cm.start()
            self.browser = self._pw.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - any launch failure is an environment error
            try:
                self._pw_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            raise RenderEnvError(
                f"Chromium did not start ({str(exc).splitlines()[0][:300]}). "
                "Install it with: python3 -m playwright install chromium") from None
        self.blocked: list = []

    def context(self, w: int, h: int, allowed: set, js: bool = True):
        ctx = self.browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1,
                                       java_script_enabled=js)

        def route(r):
            url = r.request.url
            if url in allowed or url.startswith(("data:", "blob:", "about:")):
                r.continue_()
            else:
                if url not in self.blocked:
                    self.blocked.append(url)
                r.abort()

        ctx.route("**/*", route)
        ctx.set_default_timeout(NAV_TIMEOUT_MS)
        return ctx

    def close(self):
        try:
            self.browser.close()
        finally:
            self._pw_cm.__exit__(None, None, None)


def _shot(target, **kw) -> bytes:
    return target.screenshot(animations="disabled", caret="hide", type="png", **kw)


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
        ctx = browser.context(2100, 800, {tmp.resolve().as_uri()})
        try:
            page = ctx.new_page()
            page.goto(tmp.resolve().as_uri(), wait_until="load")
            path.write_bytes(page.screenshot(full_page=True, type="jpeg", quality=82, animations="disabled"))
        finally:
            ctx.close()
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
    Raises RenderEnvError when the browser cannot run.
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
        text = _insert_head(text, _print_css(w, h))
        doc = out_dir / "deck.html"
    elif kind == "page":
        doc = out_dir / "page.html"
    else:
        doc = out_dir / ".poster-capture.html"
    doc.write_bytes(text.encode("utf-8", "surrogateescape"))
    doc_url = doc.resolve().as_uri()
    probe = probe_js or BUILTIN_PROBE_JS

    browser = _Browser()
    try:
        ctx = browser.context(w, h, {doc_url})
        try:
            page = ctx.new_page()
            page.goto(doc_url, wait_until="load")
            for fam in page.evaluate(_SETTLE_JS):
                findings.append(_finding("font_load", "fail", f"font '{fam}' failed to load", where=fam))
            if kind == "deck":
                _capture_deck(page, w, h, out_dir, doc, formats, findings, outputs, measures, probe)
            else:
                page.evaluate("() => document.body.setAttribute('data-slide-id', 'main')")
                if kind == "poster":
                    png = _shot(page)
                    (out_dir / "poster.png").write_bytes(png)
                    outputs["poster"] = sha256_bytes(png)
                else:
                    png = _shot(page, full_page=True)
                    (out_dir / "page.png").write_bytes(png)
                    outputs["page"] = sha256_bytes(png)
                    outputs["html"] = sha256_file(doc)
                measures.append(page.evaluate(probe, "main"))
        finally:
            ctx.close()
        if kind == "deck" and "html" in formats:
            _check_js_off(browser, w, h, doc, doc_url, findings)
        if kind == "poster":
            for extra in extra_sizes or []:
                ew, eh = int(extra["w"]), int(extra["h"])
                ectx = browser.context(ew, eh, {doc_url})
                try:
                    epage = ectx.new_page()
                    epage.goto(doc_url, wait_until="load")
                    epage.evaluate(_SETTLE_JS)
                    png = _shot(epage)
                finally:
                    ectx.close()
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
    finally:
        blocked = list(browser.blocked)
        browser.close()
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
    page.add_style_tag(content=_CAPTURE_CSS)
    slides_dir = out_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)
    shas = {}
    page.evaluate("() => document.body.setAttribute('data-pollinate-capture', '')")
    for i, sid in enumerate(ids):
        page.evaluate(
            "(i) => document.querySelectorAll('[data-pollinate-index]').forEach("
            "s => s.toggleAttribute('data-pollinate-current', s.getAttribute('data-pollinate-index') === String(i)))",
            i)
        page.evaluate(_SETTLE_JS)
        el = page.locator(f'[data-pollinate-index="{i}"]')
        box = el.bounding_box()
        if not box or box["width"] < 1 or box["height"] < 1:
            findings.append(_finding("slide_size", "fail", "slide is not visible when rendered", sid))
            continue
        png = _shot(el)
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
        page.emulate_media(media="print")
        page.pdf(path=str(pdf), print_background=True, prefer_css_page_size=True)
        page.emulate_media(media="screen")
        pages = _pdf_pages(pdf)
        if pages != len(ids):
            findings.append(_finding("pdf_pages", "fail", f"deck.pdf has {pages} pages, the deck has {len(ids)} slides"))
        outputs["pdf"] = sha256_file(pdf)
    if "html" in formats:
        outputs["html"] = sha256_file(doc)


def _check_js_off(browser, w, h, doc, doc_url, findings):
    """Open deck.html with JavaScript disabled; every slide must show something."""
    ctx = browser.context(w, h, {doc_url}, js=False)
    try:
        page = ctx.new_page()
        page.goto(doc_url, wait_until="load")
        slides = page.locator("section:not(section section)")
        for i in range(slides.count()):
            el = slides.nth(i)
            sid = el.get_attribute("data-slide-id") or f"page {i + 1}"
            box = el.bounding_box()
            if not box or box["width"] < 1 or box["height"] < 1 or _is_blank(_shot(el)):
                findings.append(_finding(
                    "js_off_blank", "fail",
                    "this slide is blank in deck.html when JavaScript is off; "
                    "do not hide slides until a script runs", sid))
    finally:
        ctx.close()


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
    except (RenderEnvError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for m in res["measure"]:
        res["findings"].extend(builtin_evaluate(m, size))
    print(json.dumps({"outputs": res["outputs"], "findings": res["findings"]}, indent=1, ensure_ascii=False))
    return 1 if any(f["level"] == "fail" for f in res["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
