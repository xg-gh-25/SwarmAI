#!/usr/bin/env python3
"""WS1: generate 34 comparable style thumbnails for the chat gallery.

Robust across all 34 idiosyncratic palettes by classifying color ROLES from color
PROPERTIES (luminance/saturation), not from the systems' inconsistent role NAMES:
  - bg     = per scheme: dark→darkest color, light→lightest color
  - text   = the remaining color with highest contrast vs bg
  - accent = the most-saturated remaining color (the "signature" hue)
One NEUTRAL placeholder copy across all 34 → a fair style-only comparison.
Fonts: the display+body family from the card's Typography line, loaded from Google
Fonts CDN (skip {token} + CJK + generic). Headless screenshot → Knowledge/assets/deck-styles/<slug>.png.
Idempotent: re-run overwrites the same 34 files deterministically.
"""
import re, glob, os, sys, colorsys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling _ddd_paths
from _ddd_paths import pollinate_dir as _pollinate_dir

# Bundled into this skill's own data/ (decoupled from the SwarmAI-sibling s_frontend-design).
CARDS = Path(__file__).resolve().parent.parent / "data" / "slide_bold_previews"
# DDD-local output: <workspace>/.artifacts/pollinate/assets/deck-styles (was hardcoded SwarmWS).
OUT = _pollinate_dir() / "assets" / "deck-styles"
GF_KNOWN = {  # families we can load from Google Fonts (Latin display/body)
    "Barlow","IBM Plex Mono","Cormorant Garamond","DM Sans","Courier Prime","Inter",
    "Space Grotesk","Playfair Display","Bricolage Grotesque","Fraunces","Jost","DM Mono",
    "Bodoni Moda","Manrope","Source Serif 4","Source Serif Pro","Bebas Neue","Caveat",
    "Albert Sans","Big Shoulders Display","Zilla Slab","Instrument Serif","Archivo",
    "JetBrains Mono","Chakra Petch","Space Mono","Tektur","Press Start 2P","VT323",
    "Libre Baskerville","Shrikhand","Fredoka One","Fredoka","Quicksand","Lora","Work Sans",
    "Hanken Grotesk","Manrope","Syne","Alfa Slab One","Bowlby One","Stardos Stencil",
    "Noto Serif SC","Noto Sans SC",
}
CJK = {"noto sans sc","noto serif sc","lxgw wenkai tc","noto sans mono cjk sc","noto sans jp"}
GENERIC = {"sans-serif","serif","monospace","cursive","system-ui"}

def lum(hx):
    r,g,b = (int(hx[i:i+2],16)/255 for i in (1,3,5))
    return 0.2126*r+0.7152*g+0.0722*b
def sat(hx):
    r,g,b = (int(hx[i:i+2],16)/255 for i in (1,3,5))
    return colorsys.rgb_to_hsv(r,g,b)[1]
def contrast(a,b):
    la,lb = lum(a)+0.05, lum(b)+0.05
    return max(la,lb)/min(la,lb)

# Per-system role overrides for the handful whose preview-card Palette can't be
# auto-derived (playful multi-bright systems have no single neutral field; their
# real bg/text/accent live in design.md defaults). Verified from each design.md.
ROLE_OVERRIDES = {
    "8-bit-orbit":  ("#0A0E27", "#E2D5F2", "#5EDCF4"),  # deep space / lavender / neon cyan
    "scatterbrain": ("#F7F5F0", "#2D2A26", "#FFD43B"),  # cream paper / ink / sticky-yellow
    "daisy-days":   ("#FFFFFF", "#2D2D2D", "#7ECDC0"),  # white / charcoal / turquoise
    "monochrome":   ("#FAFADF", "#1A1A16", "#1A1A16"),  # cream paper / ink (mono → accent=ink)
}

def roles(colors, scheme, slug=None):
    if slug in ROLE_OVERRIDES:
        return ROLE_OVERRIDES[slug]
    cs = list(dict.fromkeys(colors))  # dedup, keep order
    if len(cs) < 2:
        return ("#111111","#ffffff","#E85D26") if scheme!="light" else ("#ffffff","#111111","#E85D26")
    # BG: the FIELD color. Must be a low-saturation neutral so bright hues stay
    # accents (not the field). A palette almost always lists its paper/ink neutrals
    # first; picking "lightest" alone made mint the bg on scatterbrain. So:
    #   dark  → darkest LOW-SAT color (fall back to darkest)
    #   light → lightest LOW-SAT color (fall back to lightest)
    #   mixed → most-neutral extreme
    NEUTRAL = 0.28  # sat below this = a neutral (paper/ink/gray)
    neutrals = [c for c in cs if sat(c) <= NEUTRAL]
    if scheme == "dark":
        # A dark FIELD may be a saturated-but-dark hue (deep navy #0A0E27, sat .74)
        # — do NOT restrict to neutrals here (that wrongly forced white bg). Just
        # take the darkest color overall.
        bg = min(cs, key=lum)
    elif scheme == "light":
        # A light field should be a neutral PAPER, not a bright — restrict to
        # low-sat neutrals so a bright (mint/pink) can't become the field.
        pool = neutrals or cs
        bg = max(pool, key=lum)
    else:  # mixed — most-extreme neutral (paper or ink), bright stays accent
        pool = neutrals or cs
        bg = max(pool, key=lambda c: abs(lum(c)-0.5))
    rest = [c for c in cs if c != bg]
    # TEXT: highest contrast vs bg AND readable (contrast >= 3). Prefer a neutral
    # ink over a bright so body copy stays legible.
    readable = [c for c in rest if contrast(c,bg) >= 3.0]
    text_pool = readable or rest
    # among readable, prefer the most neutral (true ink) then highest contrast
    text = max(text_pool, key=lambda c: (contrast(c,bg), 1-sat(c)))
    # ACCENT: the most-saturated remaining hue (the signature color)
    accent_pool = [c for c in rest if c != text] or rest
    accent = max(accent_pool, key=sat)
    if sat(accent) < 0.12:      # monochrome system — accent = text
        accent = text
    return bg, text, accent

def fonts(card_txt):
    m = re.search(r'- Typography:\s*(.+)', card_txt)
    fams = []
    if m:
        for part in m.group(1).split(';'):
            f = part.strip()
            if not f or "{" in f or f.lower() in CJK or f.lower() in GENERIC: continue
            if f.lower().startswith("see full"): continue
            if f in GF_KNOWN: fams.append(f)
    # display = first, body = second (or reuse display)
    disp = fams[0] if fams else "Inter"
    body = fams[1] if len(fams) > 1 else disp
    return disp, body

GF_SPEC = {  # weight specs for the sample (superset-safe)
    "Barlow":"Barlow:wght@400;900","Cormorant Garamond":"Cormorant+Garamond:ital,wght@0,400;1,400",
    "Playfair Display":"Playfair+Display:wght@400;900","Bebas Neue":"Bebas+Neue","Shrikhand":"Shrikhand",
    "Tektur":"Tektur:wght@700;900","Press Start 2P":"Press+Start+2P","VT323":"VT323",
    "Fraunces":"Fraunces:ital,opsz,wght@0,9..144,400;1,9..144,400","Bodoni Moda":"Bodoni+Moda:wght@400;700",
    "Bricolage Grotesque":"Bricolage+Grotesque:wght@400;800","Alfa Slab One":"Alfa+Slab+One",
    "Big Shoulders Display":"Big+Shoulders+Display:wght@400;900","Fredoka One":"Fredoka",
    "Fredoka":"Fredoka:wght@400;600","Bowlby One":"Bowlby+One","Syne":"Syne:wght@700;800",
    "Stardos Stencil":"Stardos+Stencil:wght@400;700","Libre Baskerville":"Libre+Baskerville:wght@400;700",
    "Instrument Serif":"Instrument+Serif","Zilla Slab":"Zilla+Slab:wght@400;700",
}
def spec(fam):
    if fam in GF_SPEC: return GF_SPEC[fam]
    # default: a family with common weights
    return fam.replace(" ","+")+":wght@400;700"

def build_html(slug, disp, body, bg, text, accent, deck_js, vp):
    dspec, bspec = spec(disp), spec(body)
    link = (f'<link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link href="https://fonts.googleapis.com/css2?family={dspec}&family={bspec}&display=swap" rel="stylesheet">')
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">{link}<style>{vp}
:root{{--stage-bg:{bg};--slide-bg:{bg};}}
deck-stage>section{{background:{bg};color:{text};width:1920px;height:1080px;
  display:flex;flex-direction:column;justify-content:center;padding:120px 140px;box-sizing:border-box;
  font-family:'{body}',sans-serif;}}
.kick{{font-size:26px;letter-spacing:.22em;text-transform:uppercase;color:{accent};margin-bottom:34px;font-weight:700;}}
.title{{font-family:'{disp}',serif;font-size:150px;line-height:.95;font-weight:900;margin:0;color:{text};}}
.sub{{font-size:40px;margin-top:30px;color:{text};opacity:.72;max-width:60ch;}}
.rule{{width:200px;height:8px;background:{accent};margin:44px 0;}}
.stat{{font-family:'{disp}',serif;font-size:120px;font-weight:900;color:{accent};line-height:1;}}
.statlbl{{font-size:28px;color:{text};opacity:.6;letter-spacing:.1em;text-transform:uppercase;}}
.row{{display:flex;align-items:baseline;gap:40px;margin-top:20px;}}
</style></head><body>
<deck-stage width="1920" height="1080"><section data-label="{slug}">
<div class="kick">◆ {slug}</div>
<div class="title">Your message,<br>their attention.</div>
<div class="rule"></div>
<div class="row"><span class="stat">1</span><span class="statlbl">builder<br>+ AI = a team</span></div>
<div class="sub">The right format for the right audience — this is the {slug} design system.</div>
</section></deck-stage><script>{deck_js}</script></body></html>"""

def main():
    import hashlib, json as _json
    from playwright.sync_api import sync_playwright
    force = "--force" in sys.argv
    base = Path(__file__).resolve().parent.parent / "templates" / "html-deck"
    vp = (base/"shared"/"viewport-base.css").read_text()
    deck_js = (base/"shared"/"deck-stage.js").read_text()
    OUT.mkdir(parents=True, exist_ok=True)
    cards = sorted(glob.glob(str(CARDS/"*.md")))

    # Idempotence guard: headless font rendering is NOT byte-deterministic, so we
    # don't compare pixels — we skip a thumbnail whose INPUTS (derived roles+fonts+
    # template) are unchanged since last run. Re-run with no input change = no-op
    # (no PNG rewrites → no git noise). --force overrides.
    tmpl_sig = hashlib.md5((vp + deck_js).encode(), usedforsecurity=False).hexdigest()[:8]
    sidecar = OUT / ".thumb-inputs.json"
    prev = {}
    if sidecar.exists() and not force:
        try: prev = _json.loads(sidecar.read_text())
        except Exception: prev = {}

    manifest, cur_sig, rendered, skipped = [], {}, 0, 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        for cf in cards:
            slug = Path(cf).stem
            txt = open(cf).read()
            scheme = (re.search(r'- Scheme:\s*(\w+)', txt) or [None,'light'])[1]
            pal = re.search(r'- Palette:\s*(.+)', txt)
            colors = re.findall(r'#[0-9A-Fa-f]{6}', pal.group(1)) if pal else []
            bg, text, accent = roles(colors, scheme, slug)
            disp, body = fonts(txt)
            sig = hashlib.md5(f"{bg}{text}{accent}{disp}{body}{tmpl_sig}".encode(), usedforsecurity=False).hexdigest()[:12]
            cur_sig[slug] = sig
            manifest.append((slug, scheme, disp, bg, text, accent))
            png = OUT/f"{slug}.png"
            if not force and png.exists() and prev.get(slug) == sig:
                skipped += 1
                continue  # inputs unchanged → keep existing PNG (idempotent no-op)
            html = build_html(slug, disp, body, bg, text, accent, deck_js, vp)
            hp = OUT/f"_{slug}.html"; hp.write_text(html)
            pg = b.new_page(viewport={"width":1280,"height":720})
            pg.goto(f"file://{hp}", wait_until="networkidle")
            pg.wait_for_timeout(400); pg.evaluate("document.fonts.ready"); pg.wait_for_timeout(300)
            pg.screenshot(path=str(png))
            pg.close()
            hp.unlink()  # keep only PNGs
            rendered += 1
        b.close()
    sidecar.write_text(_json.dumps(cur_sig, indent=2, sort_keys=True))
    print(f"thumbnails: {rendered} rendered, {skipped} unchanged-skipped (idempotent)")
    print(f"generated {len(manifest)} thumbnails → {OUT}")
    for slug,sch,disp,bg,tx,ac in manifest:
        print(f"  {slug:20} {sch:6} {disp:22} bg={bg} tx={tx} ac={ac}")

if __name__ == "__main__":
    main()
