"""Tests for the html_deck track in s_pollinate.

History: Plan B v4 (run_f721108a) originally bundled fonts LOCALLY (zero-network
render). run_68176c82 REVERSED that to upstream CDN fonts — SwarmAI is online by
default, and the CDN carries the upstream font fidelity that local bundling
degraded (true italic serif axis + individual CJK faces, not Noto substitutes).

This suite now asserts the CDN-based contract:
  - D1: the format_recommend html_deck producer + PPTX-collision fix (unchanged).
  - AC1/AC2: 34 upstream design.md + shared render infra present (unchanged).
  - AC3-CDN: every design.md carries an upstream font <link> (googleapis/fontshare/
    jsdelivr), NOT a local ../../fonts/ ref — i.e. the reversal is complete.
  - AC4-italic: the italic-serif templates (vellum, grove, ...) preserve their
    real italic axis via the CDN link (the fidelity local bundling lost).
  - AC6 (gated): a vellum deck renders headless and the stage scales. Font load
    is now a network concern (CDN), so the offline-local-font assertion is gone;
    we assert the structural render (scale) which is what deck-stage.js owns.

Methodology: drives the REAL format_recommend.detect_fast_path (no mock) and the
REAL restored files on disk.
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

_CDN_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com", "fontshare",
              "cdn.jsdelivr", "chinese-fonts-cdn")


# ---------- D1: html_deck producer + collision fix (unchanged by the reversal) ----------

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


# ---------- AC1/AC2: upstream design systems + shared infra ----------

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


# ---------- reversal invariants: CDN fonts, no local machinery ----------

def test_local_font_machinery_removed():
    """The reversal deleted the bundled fonts + manifest. Their ABSENCE is the invariant."""
    assert not (DATA / "fonts").exists(), "local fonts/ dir should be deleted (reverted to CDN)"
    assert not (DATA / "FONT_MANIFEST.json").exists(), "FONT_MANIFEST.json should be deleted"


def test_no_residual_local_font_refs():
    """No design.md may point at the deleted local ../../fonts/ path (would 404 at render)."""
    offenders = [d.parent.name for d in (DATA / "systems").glob("*/design.md")
                 if "../../fonts/" in d.read_text(encoding="utf-8", errors="ignore")]
    assert not offenders, f"residual local font refs in: {offenders}"


def test_every_design_has_cdn_font_link():
    """AC3-CDN: every design.md loads its fonts from an upstream CDN <link>.
    (A pure-system-font template with no web font is theoretically allowed; if one
    ever appears, list it here explicitly rather than weakening the assertion.)"""
    missing = []
    for d in (DATA / "systems").glob("*/design.md"):
        txt = d.read_text(encoding="utf-8", errors="ignore")
        if not any(h in txt for h in _CDN_HOSTS):
            missing.append(d.parent.name)
    assert not missing, f"design.md with no CDN font link (font fidelity lost): {missing}"


def test_italic_serif_templates_preserve_italic_axis():
    """AC4-italic: templates whose personality IS the italic serif must carry a real
    italic axis in their CDN link — the fidelity local upright-only bundling destroyed.
    vellum's headline font is italic Cormorant Garamond ('ital,wght@...;1,400')."""
    vellum = (DATA / "systems" / "vellum" / "design.md").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" in vellum, "vellum lost its Google Fonts link"
    # the ital axis appears as ':ital,wght@' with at least one '1,<weight>' (italic) pair
    assert re.search(r"ital,wght@[^\"'&]*1,\d", vellum), \
        "vellum's Cormorant link has no italic (1,<wght>) axis — italic fidelity lost"


def test_track_doc_and_wiring_present():
    """Track doc exists + INSTRUCTIONS.md dispatches html_deck to it."""
    track_doc = SKILL / "tracks" / "track-e2-html-deck.md"
    assert track_doc.is_file() and track_doc.stat().st_size > 500, "track-e2-html-deck.md missing/empty"
    instr = (SKILL / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "html_deck" in instr, "INSTRUCTIONS.md has no html_deck dispatch"
    assert "track-e2-html-deck.md" in instr, "INSTRUCTIONS.md does not point to the track doc"


def test_track_doc_is_cdn_based_not_local():
    """The reversal must have rewritten the recipe: no 'LOCAL FONTS'/'../../fonts/'
    zero-network language should survive in the track doc (would mislead the agent)."""
    doc = (SKILL / "tracks" / "track-e2-html-deck.md").read_text(encoding="utf-8")
    assert "../../fonts/" not in doc, "track doc still references deleted local fonts/"


@pytest.mark.skipif(
    not _HAS_PLAYWRIGHT,
    reason="playwright not in this interpreter (render is a runtime concern; see AC6 standalone probe)",
)
def test_ac6_render_scale():
    """AC6 (live E2E, gated): assemble a minimal deck using the upstream design.md's
    own font <link> + deck-stage.js, render headless over file://, assert the stage
    SCALES (the structural guarantee deck-stage.js owns). Font loading is now a CDN
    (network) concern, so this does not assert offline-local-font — it asserts the
    scale engine, which is what this track's infra actually owns."""
    import tempfile
    from playwright.sync_api import sync_playwright  # type: ignore

    deck_js = (DATA / "shared" / "deck-stage.js").read_text(encoding="utf-8")
    vp = (DATA / "shared" / "viewport-base.css").read_text(encoding="utf-8")
    html = (f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{vp}\n"
            f"h1.title{{font-size:112px}}</style></head>"
            f"<body><deck-stage width='1920' height='1080'><section class='slide'>"
            f"<h1 class='title' id='probe'>Vellum 测试</h1></section></deck-stage>"
            f"<script>{deck_js}</script></body></html>")
    out = Path(tempfile.gettempdir()) / "ac6_scale_test.html"
    out.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(f"file://{out}")
        pg.wait_for_timeout(300)
        transform = pg.evaluate("""() => {const s=document.querySelector('deck-stage');
            const c=s.shadowRoot&&s.shadowRoot.querySelector('.canvas');
            return c?getComputedStyle(c).transform:'NONE';}""")
        b.close()
    assert transform not in ("none", "NONE", None), f"deck-stage did not scale: {transform}"


# ---------- export-pdf.sh: deck-stage support + clickable links (run_8546727e) ----------

def _export_script_text() -> str:
    return (DATA / "shared" / "export-pdf.sh").read_text(encoding="utf-8")


def test_export_pdf_supports_deck_stage_nav():
    """Regression: export-pdf.sh MUST navigate <deck-stage> decks via the component's
    own API (_go / _slides / public length getter), not ONLY the legacy .slide class.
    The old script did `querySelectorAll('.slide').length` → 0 on a deck-stage deck →
    hard exit (unusable). Both branches must exist (deck-stage primary, .slide fallback)."""
    s = _export_script_text()
    assert "deck-stage" in s, "export-pdf.sh has no <deck-stage> detection — 0-slide exit bug"
    assert "_go(" in s or "goTo(" in s, "no deck-stage navigation call (_go/goTo)"
    assert ".slide" in s, "legacy .slide fallback removed — would regress old decks"


def test_export_pdf_preserves_clickable_links():
    """Regression: the PDF MUST carry clickable link annotations, not raster-only.
    The old script screenshot→base64 img→page.pdf() flattened all <a href> to pixels
    ('PDF links all dead'). The fix captures visible http links + overlays /Link annots
    via pdf-lib."""
    s = _export_script_text()
    assert "pdf-lib" in s, "pdf-lib not used — links cannot be overlaid (raster-only PDF)"
    assert "href" in s and "getBoundingClientRect" in s, \
        "no per-slide link-rect capture (href + getBoundingClientRect)"
    assert "pdf-lib" in s and "npm install" in s and re.search(r"npm install[^\n]*pdf-lib", s), \
        "pdf-lib not added to the temp npm install line"


def test_export_pdf_self_validates():
    """Regression: script MUST self-validate after export (page count vs slide count,
    link-annotation count) and exit nonzero on mismatch — so a silent 'first-page-only'
    or 'links dropped' regression fails loudly instead of shipping a broken PDF."""
    s = _export_script_text()
    assert "process.exit(1)" in s, "no nonzero-exit self-validation guard"
    assert "<details>" in s or "details" in s, "no <details> collapse-region warning"
