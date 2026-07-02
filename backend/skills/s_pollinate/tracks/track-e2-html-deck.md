# Track L: HTML Deck

> A browser-viewable HTML slide deck drawn from 34 bundled bold design systems.
> Fixed 1920×1080 stage, auto-scaled to any viewport, CSS-only animation. Fonts load
> from Google Fonts / the upstream CDN at render time (real italic serifs + real CJK
> faces). "Open it in any browser and present."

## When This Track Runs

Executes during BUILD when `"html_deck"` is in `confirmed_tracks` (discovery.json).
Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

**Distinct from Track E (PPTX).** Track E produces a `.pptx` via PptxGenJS. Track L
produces a single `.html` file (+ optional `.pdf`). Pick Track L when the audience will
view in a browser / on the web (swarm-content), or when you want the frontend-slides
bold aesthetic. Both can co-exist in one run.

## Fonts: CDN, by design

This track uses the upstream design systems **verbatim** — each `design.md` keeps its
own Google-Fonts (or Fontshare) `<link>`. That is deliberate: the CDN link carries the
**true italic serif axis** (e.g. vellum's italic Cormorant) and the **individual CJK
faces** (LXGW WenKai, ZCOOL, Yozai) that a local upright-only woff2 bundle cannot. SwarmAI
is online by default; a rendered deck is an interactive act in a connected environment.
Trade-off: a genuinely offline render falls back to system fonts (aesthetic degrades but
the deck still renders). If true offline fidelity is ever required, that is a separate
opt-in font-bundling feature — do NOT hand-roll local @font-face here.

## Bundled Assets (`templates/html-deck/`)

```
templates/html-deck/
├── systems/<id>/design.md   # 34 design systems — upstream verbatim (own CDN font <link> + full CSS)
└── shared/
    ├── viewport-base.css     # the 1920×1080 stage base styles
    ├── html-template.md      # the reference HTML skeleton
    ├── animation-patterns.md # reveal/transition CSS snippets
    ├── deck-stage.js         # <deck-stage> web component (scaling + keyboard nav) — SELF-CONTAINED
    └── export-pdf.sh         # Playwright HTML→PDF helper
```

Source: `zarazhangrui/frontend-slides` (MIT) — attribution in `s_frontend-design/data/ATTRIBUTION.md`.

## Step 1 — Pick ONE design system

Consult `s_frontend-design/data/slide_bold_previews/<id>.md` (the selection cards) OR
`systems/<id>/design.md` directly. Use the Direction Gate result / user pick. The 34 ids
match the preview cards exactly. Only load the ONE picked system's `design.md`.

## Step 2 — Assemble a single self-contained HTML file

The picked `design.md` contains the font `<link>`, the theme CSS, and the layout. To assemble:

1. Start from `shared/html-template.md`'s Base HTML Structure.
2. Keep the design system's own font `<link>` (Google Fonts / Fontshare) in `<head>` — it
   carries the correct italic + CJK faces. Do NOT strip or localize it.
3. Inline into one `<style>`: the contents of `shared/viewport-base.css`, the design
   system's CSS, and any needed snippets from `shared/animation-patterns.md`.
4. Wrap slides in `<deck-stage width="1920" height="1080">…<section class="slide">…</section>…</deck-stage>`.
5. Append `<script>` with the full contents of `shared/deck-stage.js` (inline it — no external src).

Result: ONE `.html` that renders the full aesthetic (fonts fetched from CDN on open).

## Step 3 — (optional) Export PDF

Use `shared/export-pdf.sh` (Playwright chromium → `page.pdf()` at 1920×1080). It loads
the page with `waitUntil: 'networkidle'`, so CDN fonts are fully loaded before the PDF
snapshot. Playwright runs at export time only.

## Step 4 — Verify (the litmus)

Before declaring done, render the assembled `.html` headless and confirm:
- `<deck-stage>`'s internal `.canvas` (in shadow DOM) has a `transform: scale(...)` — the
  stage scaled to the viewport. This is the structural guarantee `deck-stage.js` owns.
- With network available, `document.fonts.check("<weight> <size> '<display family>'")`
  returns **true** (the CDN face loaded, not a system fallback).
- The design system's font `<link>` is present in `<head>` (fonts wired).

(See `backend/skills/s_pollinate/tests/test_html_deck_track.py::test_ac6_render_scale` for
the exact Playwright probe — it asserts the stage scales.)

## Font notes

- Each design system loads its own upstream font `<link>`; nothing to manage locally.
- `Noto Sans Mono CJK SC` is an invalid upstream family name (used only as a mono fallback
  in a few templates); browsers ignore the invalid entry and fall through the stack — no action needed.
- If true **offline** rendering is ever required, open a separate feature to bundle+subset
  the fonts (fontsource complete woff2 + rewrite @font-face). Do NOT bolt local fonts onto
  this track piecemeal — it was deliberately reverted to CDN for font fidelity (run_68176c82).
