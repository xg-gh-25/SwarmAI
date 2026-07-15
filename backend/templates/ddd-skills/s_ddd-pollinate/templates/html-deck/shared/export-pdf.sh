#!/usr/bin/env bash
# export-pdf.sh — Export an HTML presentation to PDF
#
# Usage:
#   bash scripts/export-pdf.sh <path-to-html> [output.pdf]
#
# Examples:
#   bash scripts/export-pdf.sh ./my-deck/index.html
#   bash scripts/export-pdf.sh ./presentation.html ./presentation.pdf
#
# What this does:
#   1. Starts a local server to serve the HTML (fonts and assets need HTTP)
#   2. Uses Playwright to screenshot each slide at 1920x1080
#   3. Combines all screenshots into a single PDF
#   4. Cleans up the server and temp files
#
# The PDF preserves colors, fonts, and layout — but not animations.
# Perfect for email attachments, printing, or embedding in documents.
set -euo pipefail

# ─── Colors ────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}ℹ${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
err()   { echo -e "${RED}✗${NC} $*" >&2; }

# ─── Parse flags ──────────────────────────────────────────

# Default resolution: 1920x1080 (full HD, ~1-2MB per slide)
# Compact resolution: 1280x720 (HD, ~50-70% smaller files)
VIEWPORT_W=1920
VIEWPORT_H=1080
COMPACT=false

POSITIONAL=()
for arg in "$@"; do
    case $arg in
        --compact)
            COMPACT=true
            VIEWPORT_W=1280
            VIEWPORT_H=720
            ;;
        *)
            POSITIONAL+=("$arg")
            ;;
    esac
done
set -- "${POSITIONAL[@]}"

# ─── Input validation ─────────────────────────────────────

if [[ $# -lt 1 ]]; then
    err "Usage: bash scripts/export-pdf.sh <path-to-html> [output.pdf] [--compact]"
    err ""
    err "Examples:"
    err "  bash scripts/export-pdf.sh ./my-deck/index.html"
    err "  bash scripts/export-pdf.sh ./presentation.html ./slides.pdf"
    err "  bash scripts/export-pdf.sh ./presentation.html --compact   # smaller file size"
    exit 1
fi

INPUT_HTML="$1"
if [[ ! -f "$INPUT_HTML" ]]; then
    err "File not found: $INPUT_HTML"
    exit 1
fi

# Resolve to absolute path
INPUT_HTML=$(cd "$(dirname "$INPUT_HTML")" && pwd)/$(basename "$INPUT_HTML")

# Output PDF path: use second argument or derive from input name
if [[ $# -ge 2 ]]; then
    OUTPUT_PDF="$2"
else
    OUTPUT_PDF="$(dirname "$INPUT_HTML")/$(basename "$INPUT_HTML" .html).pdf"
fi

# Resolve output to absolute path
OUTPUT_DIR=$(dirname "$OUTPUT_PDF")
mkdir -p "$OUTPUT_DIR"
OUTPUT_PDF="$OUTPUT_DIR/$(basename "$OUTPUT_PDF")"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║       Export Slides to PDF            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════╝${NC}"
echo ""

# ─── Step 1: Check dependencies ───────────────────────────

info "Checking dependencies..."

if ! command -v npx &>/dev/null; then
    err "Node.js is required but not installed."
    err ""
    err "Install Node.js:"
    err "  macOS:   brew install node"
    err "  or visit https://nodejs.org and download the installer"
    exit 1
fi

ok "Node.js found"

# ─── Step 2: Create the export script ─────────────────────

# We use a temporary Node.js script with Playwright to:
# 1. Start a local server (so fonts load correctly)
# 2. Navigate to each slide
# 3. Screenshot each slide at 1920x1080 (16:9 landscape)
# 4. Combine into a single PDF

TEMP_DIR=$(mktemp -d)
TEMP_SCRIPT="$TEMP_DIR/export-slides.mjs"

# Figure out which directory to serve (the folder containing the HTML)
SERVE_DIR=$(dirname "$INPUT_HTML")
HTML_FILENAME=$(basename "$INPUT_HTML")

cat > "$TEMP_SCRIPT" << 'EXPORT_SCRIPT'
// export-slides.mjs — Playwright script to export HTML slides to PDF
//
// Handles TWO deck architectures:
//   • <deck-stage> web-component decks (slides are slotted <section>s; navigation
//     via the component's own _go(i) / public `length` getter). This is the current
//     Pollinate html-deck output. The legacy `.slide` path does NOT work on these —
//     querySelectorAll('.slide') returns 0 → the old script hard-exited (unusable).
//   • Legacy `.slide` decks (older generated presentations) — kept as a fallback.
//
// Pipeline:
//   1. Local HTTP server (fonts/assets need HTTP).
//   2. Navigate to each slide (deck-stage._go, or .slide show/hide).
//   3. Screenshot each slide at design size (pixel-perfect fidelity).
//   4. BEFORE each screenshot, capture the visible http <a> link rects on that slide.
//   5. Assemble the PDF with pdf-lib: one image page per slide + a clickable /Link
//      URI annotation overlaid at each captured rect (screenshots are raster and
//      would otherwise flatten every hyperlink → "PDF links all dead").
//   6. Self-validate (page count == slide count, link-annot count == captured) and
//      exit nonzero on any mismatch, so a silent regression fails loudly.
//
// NOTE (verified empirically run_8546727e): the "native" `page.pdf()` print path
// does NOT work for <deck-stage> — its slides stack at inset:0 inside a scaled
// canvas and the shadow-DOM @media print rules do not flatten them into the print
// flow, yielding a PDF with only the first slide painted. Screenshot+overlay is the
// reliable path and is what preserves BOTH content and clickable links.

import { chromium } from 'playwright';
import { createServer } from 'http';
import { readFileSync, mkdirSync, unlinkSync } from 'fs';
import { join, extname } from 'path';
import { PDFDocument, PDFName, PDFString, PDFArray, PDFNumber } from 'pdf-lib';

const SERVE_DIR = process.argv[2];
const HTML_FILE = process.argv[3];
const OUTPUT_PDF = process.argv[4];
const SCREENSHOT_DIR = process.argv[5];
const VP_WIDTH = parseInt(process.argv[6]) || 1920;
const VP_HEIGHT = parseInt(process.argv[7]) || 1080;

// ─── Simple static file server ────────────────────────────
// (We need HTTP so that Google Fonts and relative assets load correctly)

const MIME_TYPES = {
  '.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript',
  '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.svg': 'image/svg+xml',
  '.webp': 'image/webp', '.woff': 'font/woff', '.woff2': 'font/woff2',
  '.ttf': 'font/ttf', '.eot': 'application/vnd.ms-fontobject',
};

const server = createServer((req, res) => {
  const decodedUrl = decodeURIComponent(req.url);
  const filePath = join(SERVE_DIR, decodedUrl === '/' ? HTML_FILE : decodedUrl);
  try {
    const content = readFileSync(filePath);
    const ext = extname(filePath).toLowerCase();
    res.writeHead(200, { 'Content-Type': MIME_TYPES[ext] || 'application/octet-stream' });
    res.end(content);
  } catch {
    res.writeHead(404);
    res.end('Not found');
  }
});

const port = await new Promise((resolve) => {
  server.listen(0, () => resolve(server.address().port));
});
console.log(`  Local server on port ${port}`);

// A landscape screenshot is captured at design size; the PDF page is built at the
// SAME pixel dimensions (1 pt == 1 screenshot px), so link rects need only a Y-flip,
// no scale reconciliation. We therefore force deck-stage into `noscale` mode below
// (scale factor exactly 1.0) so getBoundingClientRect() coords == screenshot coords.

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: VP_WIDTH, height: VP_HEIGHT } });
await page.goto(`http://localhost:${port}/`, { waitUntil: 'networkidle' });
await page.evaluate(() => document.fonts.ready);

// ─── Detect deck architecture (deck-stage primary, .slide fallback) ───
// Readiness gate: <deck-stage> populates _slides on slotchange (async). Wait on the
// PUBLIC `length` getter, never an immediate _slides read — else we'd re-hit the
// 0-slide bug in a new disguise. If no deck-stage, fall back to .slide count.
const isDeckStage = await page.evaluate(() => !!document.querySelector('deck-stage'));

if (isDeckStage) {
  // Force noscale so DOM geometry == screenshot geometry (scale = 1.0), esp. --compact.
  await page.evaluate(() => {
    const ds = document.querySelector('deck-stage');
    ds.setAttribute('noscale', '');
  });
  await page.waitForFunction(
    () => { const d = document.querySelector('deck-stage'); return d && d.length > 0; },
    { timeout: 10000 },
  ).catch(() => {});
}

const slideCount = await page.evaluate(() => {
  const ds = document.querySelector('deck-stage');
  if (ds) return ds.length || 0;                 // public getter (reflects _slides.length)
  return document.querySelectorAll('.slide').length;
});
console.log(`  Found ${slideCount} slides (${isDeckStage ? 'deck-stage' : 'legacy .slide'})`);

if (slideCount === 0) {
  console.error('  ERROR: No slides found (<deck-stage> with slotted <section>s, or .slide elements).');
  await browser.close(); server.close();
  process.exit(1);
}

// Warn on collapse regions — the PDF captures the COLLAPSED state only.
const detailsCount = await page.evaluate(() => document.querySelectorAll('details').length);
if (detailsCount > 0) {
  console.warn(`  ⚠ ${detailsCount} <details> collapse region(s) detected — the PDF captures`);
  console.warn('    only the COLLAPSED state; expandable content + its links live in the HTML.');
}

// ─── Screenshot each slide + capture its visible http link rects ───
mkdirSync(SCREENSHOT_DIR, { recursive: true });
const screenshotPaths = [];
const slideLinks = [];   // per slide: [{href,x,y,w,h}, ...] in screenshot px
const slideOverlaps = []; // per slide: {slide, skipped, overlaps:[{a,b,px}]} — ADVISORY only

for (let i = 0; i < slideCount; i++) {
  // Navigate. deck-stage: _go(i) + hide overlay chrome. legacy: show/hide .slide.
  await page.evaluate((index) => {
    const ds = document.querySelector('deck-stage');
    if (ds) {
      ds._go(index, 'pdf');
      const root = ds.shadowRoot;
      if (root) root.querySelectorAll('.overlay, .tapzones').forEach(e => { e.style.display = 'none'; });
      // Force the current slide's .reveal entrance animations to their FINISHED
      // state. Reveals fade opacity 0→1 over ~0.6s (staggered); a screenshot taken
      // mid-transition captures a dimmed/washed slide. Setting the end-state (and
      // killing the transition) makes the capture deterministic regardless of timing.
      const cur = ds._slides && ds._slides[index];
      if (cur) cur.querySelectorAll('.reveal').forEach(el => {
        el.style.transition = 'none';
        el.style.opacity = '1';
        el.style.transform = 'none';
      });
    } else {
      const slides = document.querySelectorAll('.slide');
      slides.forEach((slide, idx) => {
        if (idx === index) {
          slide.style.display = ''; slide.style.opacity = '1';
          slide.style.visibility = 'visible'; slide.style.position = 'relative';
          slide.style.transform = 'none'; slide.classList.add('active');
        } else { slide.style.display = 'none'; slide.classList.remove('active'); }
      });
      if (window.presentation && typeof window.presentation.goToSlide === 'function') {
        window.presentation.goToSlide(index);
      }
      slides[index]?.querySelectorAll('.reveal').forEach(el => {
        el.style.opacity = '1'; el.style.transform = 'none'; el.style.visibility = 'visible';
      });
    }
  }, i);

  // Wait for a COMMITTED paint (double-rAF) rather than a fixed guess.
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  await page.waitForTimeout(150);

  // Capture visible http links scoped to the CURRENT slide element (skip collapsed
  // <details>, off-canvas, and zero-size). deck-stage slides are light-DOM slotted.
  const links = await page.evaluate((index) => {
    const ds = document.querySelector('deck-stage');
    const slide = ds ? ds._slides[index] : document.querySelectorAll('.slide')[index];
    if (!slide) return [];
    const out = [];
    slide.querySelectorAll('a[href^="http"]').forEach((a) => {
      const details = a.closest('details');
      if (details && !details.open) return;      // collapsed → not visible in the shot
      const r = a.getBoundingClientRect();
      if (r.width > 1 && r.height > 1 && r.bottom > 0 && r.top < window.innerHeight &&
          r.right > 0 && r.left < window.innerWidth) {
        out.push({ href: a.href, x: r.left, y: r.top, w: r.width, h: r.height });
      }
    });
    return out;
  }, i);
  slideLinks.push(links);

  // ── ADVISORY text-overlap probe (READ side of the data-om-validate contract) ──
  // deck-stage.js WRITES data-om-validate="no_overflowing_text,no_overlapping_text,
  // slide_sized_text" on every slide but nothing ever read it. Here we honor the
  // no_overlapping_text token: detect two LEAF text blocks whose boxes intersect,
  // using the real loaded fonts (reveals already forced above). This is ADVISORY
  // only — we warn, never fail (INSTRUCTIONS.md:801: this check class is
  // false-positive-prone and must not hard-block a genuine delivery). A slide may
  // opt out with data-om-validate="false".
  const overlap = await page.evaluate((index) => {
    const ds = document.querySelector('deck-stage');
    const slide = ds ? ds._slides[index] : document.querySelectorAll('.slide')[index];
    if (!slide) return { slide: index + 1, skipped: true, overlaps: [] };
    const attr = (slide.getAttribute('data-om-validate') || '').trim();
    // Opt-out: explicit "false", or a token list that does not request the check.
    if (attr === 'false' || (attr && !attr.split(',').map(t => t.trim()).includes('no_overlapping_text'))) {
      return { slide: index + 1, skipped: true, overlaps: [] };
    }
    // Collect LEAF text blocks: element has non-empty text, all children are
    // text/inline (not a wrapping container → avoids counting a parent that
    // merely contains its own children), is visible, not rotated (matrix/rotate
    // measurement artifact), and not zero-size.
    const INLINE = new Set(['A','SPAN','EM','STRONG','B','I','U','CODE','SUP','SUB','SMALL','MARK','BR','WBR','ABBR','TIME','LABEL']);
    const isLeafText = (el) => {
      const txt = (el.textContent || '').trim();
      if (!txt) return false;
      for (const c of el.children) { if (!INLINE.has(c.tagName)) return false; }
      const cs = getComputedStyle(el);
      if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) return false;
      const tf = cs.transform;
      if (tf && tf !== 'none' && /matrix|rotate/.test(tf)) return false; // rotated → skip
      const r = el.getBoundingClientRect();
      return r.width > 2 && r.height > 2;
    };
    const blocks = [];
    slide.querySelectorAll('*').forEach((el) => {
      if (INLINE.has(el.tagName)) return;            // inline spans handled by their block parent
      if (!isLeafText(el)) return;
      const r = el.getBoundingClientRect();
      blocks.push({ el, r, text: (el.textContent || '').trim().slice(0, 40) });
    });
    const overlaps = [];
    for (let a = 0; a < blocks.length; a++) {
      for (let b = a + 1; b < blocks.length; b++) {
        const ra = blocks[a].r, rb = blocks[b].r;
        // one is an ancestor of the other → not a real overlap, skip
        if (blocks[a].el.contains(blocks[b].el) || blocks[b].el.contains(blocks[a].el)) continue;
        const ix = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
        const iy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
        if (ix > 4 && iy > 4) {                       // >4px on BOTH axes (not a touching border)
          overlaps.push({ a: blocks[a].text, b: blocks[b].text, px: Math.round(Math.min(ix, iy)) });
        }
      }
    }
    return { slide: index + 1, skipped: false, overlaps };
  }, i);
  slideOverlaps.push(overlap);

  const screenshotPath = join(SCREENSHOT_DIR, `slide-${String(i + 1).padStart(3, '0')}.png`);
  await page.screenshot({ path: screenshotPath, fullPage: false });
  screenshotPaths.push(screenshotPath);
  console.log(`  Captured slide ${i + 1}/${slideCount} (${links.length} link${links.length === 1 ? '' : 's'})`);
}

await browser.close();
server.close();

// ─── Assemble PDF with pdf-lib: image page per slide + clickable link annotations ───
console.log('  Assembling PDF (with clickable links)...');

const pdfDoc = await PDFDocument.create();
let totalLinks = 0;

for (let i = 0; i < screenshotPaths.length; i++) {
  const pngBytes = readFileSync(screenshotPaths[i]);
  const png = await pdfDoc.embedPng(pngBytes);
  // Page in points == screenshot pixels (1:1), so a link rect only needs a Y-flip.
  const page = pdfDoc.addPage([png.width, png.height]);
  page.drawImage(png, { x: 0, y: 0, width: png.width, height: png.height });

  const scaleX = png.width / VP_WIDTH;    // 1.0 for a full-size shot; robust if compact
  const scaleY = png.height / VP_HEIGHT;
  const annots = [];
  for (const l of slideLinks[i]) {
    const x0 = l.x * scaleX;
    const x1 = (l.x + l.w) * scaleX;
    const y1 = png.height - l.y * scaleY;               // PDF origin is bottom-left
    const y0 = png.height - (l.y + l.h) * scaleY;
    const annot = pdfDoc.context.obj({
      Type: 'Annot', Subtype: 'Link',
      Rect: [x0, y0, x1, y1],
      Border: [0, 0, 0],
      A: { Type: 'Action', S: 'URI', URI: PDFString.of(l.href) },
    });
    annots.push(pdfDoc.context.register(annot));
    totalLinks++;
  }
  if (annots.length) page.node.set(PDFName.of('Annots'), pdfDoc.context.obj(annots));
}

const pdfBytes = await pdfDoc.save();
const { writeFileSync } = await import('fs');
writeFileSync(OUTPUT_PDF, pdfBytes);
screenshotPaths.forEach((p) => unlinkSync(p));

// ─── Self-validate: fail loudly instead of shipping a broken PDF ───
const captured = slideLinks.reduce((n, ls) => n + ls.length, 0);
const producedPages = pdfDoc.getPageCount();
let ok = true;
if (producedPages !== slideCount) {
  console.error(`  ✗ VALIDATION: ${producedPages} PDF pages != ${slideCount} slides`);
  ok = false;
}
if (captured > 0 && totalLinks !== captured) {
  console.error(`  ✗ VALIDATION: overlaid ${totalLinks} link annots != ${captured} captured`);
  ok = false;
}
if (!ok) process.exit(1);

// ─── ADVISORY: report text-overlap warnings (data-om-validate contract). ───
// Non-blocking BY DESIGN (INSTRUCTIONS.md:801) — this warns and NEVER changes the
// exit code. The exit-code gate above (page/link counts) is the ONLY hard gate;
// keep this block strictly after it so overlaps can never affect exit status.
const overlapSlides = slideOverlaps.filter((s) => !s.skipped && s.overlaps.length);
if (overlapSlides.length) {
  console.warn(`  ⚠ ADVISORY: text-overlap detected on ${overlapSlides.length} slide(s) (review; not a hard failure):`);
  for (const s of overlapSlides) {
    for (const o of s.overlaps) {
      console.warn(`      slide ${s.slide}: "${o.a}" ⇄ "${o.b}" overlap ~${o.px}px`);
    }
  }
  console.warn(`      (intentional? add data-om-validate="false" to that slide to opt out.)`);
}

console.log(`  ✓ PDF saved: ${OUTPUT_PDF} (${producedPages} pages, ${totalLinks} clickable links)`);
EXPORT_SCRIPT

# ─── Step 3: Install Playwright in temp directory ──────────
# We install Playwright locally in the temp dir so the Node script can import it.
# This avoids polluting global packages and ensures the script is self-contained.

info "Setting up Playwright (headless browser for screenshots)..."
info "This may take a moment on first run..."
echo ""

cd "$TEMP_DIR"

# Create a minimal package.json so npm install works
cat > "$TEMP_DIR/package.json" << 'PKG'
{ "name": "slide-export", "private": true, "type": "module" }
PKG

# Install Playwright + pdf-lib into the temp directory.
# pdf-lib (pure-JS) assembles the image-per-slide PDF AND overlays the clickable
# /Link annotations — no python/pypdf dependency, keeps this script self-contained.
npm install playwright pdf-lib &>/dev/null || {
    err "Failed to install Playwright + pdf-lib."
    err "Try running: npm install playwright pdf-lib"
    rm -rf "$TEMP_DIR"
    exit 1
}

# Ensure Chromium browser binary is downloaded
npx playwright install chromium 2>/dev/null || {
    err "Failed to install Chromium browser for Playwright."
    err "Try running manually: npx playwright install chromium"
    rm -rf "$TEMP_DIR"
    exit 1
}
ok "Playwright ready"
echo ""

# ─── Step 4: Run the export ───────────────────────────────

SCREENSHOT_DIR="$TEMP_DIR/screenshots"

info "Exporting slides to PDF..."
echo ""

# Run from the temp dir so Node can find the locally-installed playwright
if [[ "$COMPACT" == "true" ]]; then
    info "Using compact mode (1280×720) for smaller file size"
fi

node "$TEMP_SCRIPT" "$SERVE_DIR" "$HTML_FILENAME" "$OUTPUT_PDF" "$SCREENSHOT_DIR" "$VIEWPORT_W" "$VIEWPORT_H" || {
    err "PDF export failed."
    rm -rf "$TEMP_DIR"
    exit 1
}

# ─── Step 5: Cleanup and success ──────────────────────────

rm -rf "$TEMP_DIR"

echo ""
echo -e "${BOLD}════════════════════════════════════════${NC}"
ok "PDF exported successfully!"
echo ""
echo -e "  ${BOLD}File:${NC}  $OUTPUT_PDF"
echo ""
FILE_SIZE=$(du -h "$OUTPUT_PDF" | cut -f1 | xargs)
echo "  Size: $FILE_SIZE"
echo ""
echo "  This PDF works everywhere — email, Slack, Notion, print."
echo "  Note: Animations are not preserved (it's a static export)."
echo -e "${BOLD}════════════════════════════════════════${NC}"
echo ""

# Open the PDF automatically
if command -v open &>/dev/null; then
    open "$OUTPUT_PDF"
elif command -v xdg-open &>/dev/null; then
    xdg-open "$OUTPUT_PDF"
fi
