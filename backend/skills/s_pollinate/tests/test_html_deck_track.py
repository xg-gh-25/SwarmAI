"""Tests for the html_deck track wiring in s_pollinate (run_f721108a, Plan B v4).

Covers the format_recommend producer + collision fix (D1), the font-asset
manifest integrity (D2 / AC3-AC5a), and the ingested render infra (AC1/AC2).

Methodology: drives the REAL format_recommend.detect_fast_path (no mock) and
the REAL ingested files on disk. The collision test is the load-bearing one:
"html deck" contains the substring "deck", so a naive add double-emits the
PPTX deck track — this asserts html_deck fires WITHOUT the PPTX deck track,
and that a plain "deck"/"ppt" request still returns PPTX-only (no regression).
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
DATA = SKILL / "templates" / "html-deck"

sys.path.insert(0, str(SCRIPTS))
import format_recommend as fr  # noqa: E402


# ---------- D1: html_deck producer + collision fix ----------

def test_html_deck_signal_detected():
    """An explicit html-deck intent must produce the html_deck track."""
    for msg in ["make an html deck", "做一个网页ppt", "web deck for the launch", "html幻灯"]:
        got = fr.detect_fast_path(msg)
        assert got is not None, f"no fast-path for {msg!r}"
        assert "html_deck" in got, f"html_deck not detected in {msg!r} -> {got}"


def test_html_deck_does_not_double_emit_pptx_deck():
    """COLLISION: 'html deck' contains 'deck' — must NOT also fire the PPTX deck track."""
    got = fr.detect_fast_path("make an html deck")
    assert "html_deck" in got
    assert "deck" not in got, f"double-emit: PPTX deck leaked alongside html_deck -> {got}"


def test_plain_deck_still_pptx_only_no_regression():
    """Regression guard: plain deck/ppt intent stays the PPTX deck track, no html_deck."""
    for msg in ["make a deck", "build a ppt", "需要一个演示", "pptx please"]:
        got = fr.detect_fast_path(msg)
        assert got is not None, f"no fast-path for {msg!r}"
        assert "deck" in got, f"PPTX deck lost for {msg!r} -> {got}"
        assert "html_deck" not in got, f"html_deck wrongly fired for plain {msg!r} -> {got}"


def test_html_deck_in_supported_tracks():
    assert "html_deck" in fr.SUPPORTED_TRACKS


def test_main_returns_html_deck_json():
    """End-to-end via CLI: --message with html-deck intent returns html_deck in detected_formats."""
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "format_recommend.py"), "--message", "做个网页ppt", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "html_deck" in r.stdout


# ---------- AC1/AC2: ingested design systems + shared infra ----------

def test_34_design_systems_present():
    systems = DATA / "systems"
    assert systems.is_dir(), "systems dir missing"
    designs = list(systems.glob("*/design.md"))
    assert len(designs) == 34, f"expected 34 design.md, got {len(designs)}"
    for d in designs:
        assert d.stat().st_size > 5000, f"{d} too small ({d.stat().st_size}B)"


def test_shared_infra_present():
    shared = DATA / "shared"
    for f in ["viewport-base.css", "html-template.md", "animation-patterns.md",
              "export-pdf.sh", "deck-stage.js"]:
        p = shared / f
        assert p.is_file() and p.stat().st_size > 100, f"missing/empty infra: {f}"


# ---------- AC3: fonts fetched, valid, static ----------

def _woff2_tables(path: Path) -> set[str]:
    """Parse woff2 header + table directory WITHOUT fonttools (dropped as a dep).

    woff2: 48-byte header, then a table directory of variable-length entries.
    We read the known-length table-tag table via the flag byte of each entry:
    the low 6 bits are a tag index; 0x3f (63) means an arbitrary 4-byte tag
    follows. We only need to know whether 'fvar'/'gvar' (variable-font tables)
    are present, so decode just the tags.
    """
    import struct
    KNOWN = ["cmap","head","hhea","hmtx","maxp","name","OS/2","post","cvt ","fpgm",
             "glyf","loca","prep","CFF ","VORG","EBDT","EBLC","gasp","hdmx","kern",
             "LTSH","PCLT","VDMX","vhea","vmtx","BASE","GDEF","GPOS","GSUB","EBSC",
             "JSTF","MATH","CBDT","CBLC","COLR","CPAL","SVG ","sbix","acnt","avar",
             "bdat","bloc","bsln","cvar","fdsc","feat","fmtx","fvar","gvar","hsty",
             "just","lcar","mort","morx","opbd","prop","trak","Zapf","Silf","Glat",
             "Gloc","Feat","Sill"]
    data = path.read_bytes()
    if data[:4] != b"wOF2":
        return set()
    num_tables = struct.unpack(">H", data[12:14])[0]
    tags, off = set(), 48
    for _ in range(num_tables):
        flag = data[off]; off += 1
        idx = flag & 0x3f
        if idx == 0x3f:
            tags.add(data[off:off+4].decode("latin-1")); off += 4
        else:
            tags.add(KNOWN[idx] if idx < len(KNOWN) else f"?{idx}")
        # skip origLength (+ transformLength for glyf/loca) UIntBase128 — but we
        # only need tags, and tags are all read before any length field of the
        # NEXT entry; to keep parsing simple we bail after collecting enough.
        # Read the UIntBase128 length(s) to advance correctly:
        def _uintbase128(o):
            v = 0
            for _i in range(5):
                b = data[o]; o += 1
                v = (v << 7) | (b & 0x7f)
                if not (b & 0x80):
                    return v, o
            return v, o
        _, off = _uintbase128(off)
        tag = list(tags)[-1] if tags else ""
        if tag in ("glyf", "loca"):
            _, off = _uintbase128(off)
    return tags


def test_fonts_present_and_static():
    fonts = DATA / "fonts"
    assert fonts.is_dir(), "fonts dir missing"
    woff2 = list(fonts.glob("*.woff2"))
    assert len(woff2) >= 80, f"expected >=80 woff2, got {len(woff2)}"
    # spot-check a sample: valid woff2 signature + NOT a variable font (no fvar/gvar).
    # Variable fonts are the merge-crash risk the plan avoided by using fontsource.
    for p in sorted(woff2)[:8]:
        assert p.stat().st_size > 500, f"{p} too small"
        data = p.read_bytes()
        assert data[:4] == b"wOF2", f"{p} not a valid woff2 (bad signature)"
        tables = _woff2_tables(p)
        assert "fvar" not in tables and "gvar" not in tables, \
            f"{p} is a variable font (merge risk) — tables={tables}"


# ---------- AC4/AC5a: transform removed CDN refs + every family has @font-face ----------

def test_no_google_fonts_refs_after_transform():
    systems = DATA / "systems"
    offenders = []
    for d in systems.glob("*/design.md"):
        txt = d.read_text(encoding="utf-8", errors="ignore")
        if "googleapis.com" in txt or "gstatic.com" in txt:
            offenders.append(d.parent.name)
    assert not offenders, f"CDN refs remain in: {offenders}"


def test_every_used_family_has_local_font_face():
    """AC5-a: across ALL 34 systems, every QUOTED CSS font-family names a family that
    has an injected @font-face — EXCEPT deliberate system/generic fonts (Segoe UI,
    MS Sans Serif, ...), which the ingest records in FONT_MANIFEST.json. This proves
    no *intended-local* face silently falls back to a system font at render."""
    import json
    manifest = json.loads((DATA / "FONT_MANIFEST.json").read_text(encoding="utf-8"))
    generics = {g.lower() for g in manifest.get("generic_families", [])}
    system_fb = {s.lower() for s in manifest.get("system_fallback", [])}
    allowed = generics | system_fb
    systems = DATA / "systems"
    problems = {}
    for d in systems.glob("*/design.md"):
        txt = d.read_text(encoding="utf-8", errors="ignore")
        faces = {f.lower() for f in re.findall(r"@font-face\{font-family:'([^']+)'", txt)}
        used = set()
        # QUOTED family names only (matches the ingest's Source-2 extraction)
        for m in re.finditer(r"font-family:\s*([^;}\n]+)", txt):
            for q in re.findall(r"'([^']+)'|\"([^\"]+)\"", m.group(1)):
                fam = (q[0] or q[1]).strip().lower()
                if fam and fam not in allowed:
                    used.add(fam)
        missing = {u for u in used if u not in faces}
        if missing:
            problems[d.parent.name] = sorted(missing)
    assert not problems, f"font-family with no local @font-face (and not a known system font): {problems}"


def test_track_doc_and_wiring_present():
    """Track doc exists + INSTRUCTIONS.md dispatches html_deck to it."""
    skill = SKILL
    track_doc = skill / "tracks" / "track-e2-html-deck.md"
    assert track_doc.is_file() and track_doc.stat().st_size > 500, "track-e2-html-deck.md missing/empty"
    instr = (skill / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "html_deck" in instr, "INSTRUCTIONS.md has no html_deck dispatch"
    assert "track-e2-html-deck.md" in instr, "INSTRUCTIONS.md does not point to the track doc"


@pytest.mark.skipif(
    not _HAS_PLAYWRIGHT,
    reason="playwright not in this interpreter (render is a runtime concern; see AC6 standalone probe)",
)
def test_ac6_render_offline_fonts_and_scale():
    """AC6 (live E2E, gated): assemble a vellum deck, render headless over file://,
    assert stage scales + local font loads offline. Skips if playwright absent."""
    import tempfile
    from playwright.sync_api import sync_playwright  # type: ignore

    vellum = DATA / "systems" / "vellum" / "design.md"
    design = vellum.read_text(encoding="utf-8")
    m = re.search(r"<style>\s*/\* LOCAL FONTS.*?</style>", design, re.S)
    assert m, "no injected LOCAL FONTS block in vellum"
    faces = m.group(0).replace("../../fonts/", f"file://{DATA}/fonts/")
    deck_js = (DATA / "shared" / "deck-stage.js").read_text(encoding="utf-8")
    vp = (DATA / "shared" / "viewport-base.css").read_text(encoding="utf-8")
    html = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>{faces}<style>{vp}\n"
            f"h1.title{{font-family:'Noto Serif SC',serif;font-weight:700;font-size:112px}}</style></head>"
            f"<body><deck-stage width='1920' height='1080'><section class='slide'>"
            f"<h1 class='title' id='probe'>测试 Vellum</h1></section></deck-stage>"
            f"<script>{deck_js}</script></body></html>")
    out = Path(tempfile.gettempdir()) / "ac6_vellum_test.html"
    out.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(f"file://{out}")
        pg.wait_for_timeout(300); pg.evaluate("document.fonts.ready"); pg.wait_for_timeout(300)
        transform = pg.evaluate("""() => {const s=document.querySelector('deck-stage');
            const c=s.shadowRoot&&s.shadowRoot.querySelector('.canvas');
            return c?getComputedStyle(c).transform:'NONE';}""")
        loaded = pg.evaluate("""() => document.fonts.check("700 112px 'Noto Serif SC'")""")
        b.close()
    assert transform not in ("none", "NONE", None), f"deck-stage did not scale: {transform}"
    assert loaded, "local 'Noto Serif SC' failed to load offline (system fallback)"
