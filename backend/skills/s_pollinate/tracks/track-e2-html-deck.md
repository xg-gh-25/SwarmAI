# Track L: HTML Deck (self-contained, zero-network)

> A browser-viewable HTML slide deck drawn from 34 bundled bold design systems.
> Fixed 1920×1080 stage, auto-scaled to any viewport, CSS-only animation, fonts
> bundled locally — renders identically offline. "Open it in any browser and present."

## When This Track Runs

Executes during BUILD when `"html_deck"` is in `confirmed_tracks` (discovery.json).
Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

**Distinct from Track E (PPTX).** Track E produces a `.pptx` via PptxGenJS. Track L
produces a single `.html` file (+ optional `.pdf`). Pick Track L when the audience will
view in a browser / on the web (swarm-content), or when you want the frontend-slides
bold aesthetic. Both can co-exist in one run.

## Bundled Assets (all local — `templates/html-deck/`)

```
templates/html-deck/
├── systems/<id>/design.md   # 34 design systems (full CSS + injected LOCAL @font-face)
├── shared/
│   ├── viewport-base.css    # the 1920×1080 stage base styles
│   ├── html-template.md     # the reference HTML skeleton
│   ├── animation-patterns.md# reveal/transition CSS snippets
│   ├── deck-stage.js         # <deck-stage> web component (scaling + keyboard nav) — SELF-CONTAINED
│   └── export-pdf.sh         # Playwright HTML→PDF helper
├── fonts/<slug>-<weight>.woff2  # 87 complete static woff2 (fontsource, zero-network)
└── FONT_MANIFEST.json        # family → weights → resolved slug/subset (+ alias table)
```

Source: `zarazhangrui/frontend-slides` (MIT) — attribution in `s_frontend-design/data/ATTRIBUTION.md`.

## Step 1 — Pick ONE design system

Consult `s_frontend-design/data/slide_bold_previews/<id>.md` (the selection cards) OR
`systems/<id>/design.md` directly. Use the Direction Gate result / user pick. The 34 ids
match the preview cards exactly. Only load the ONE picked system's `design.md`.

## Step 2 — Assemble a single self-contained HTML file

The picked `design.md` already contains a `<style>/* LOCAL FONTS */…@font-face…</style>`
block at its top (injected at ingest, pointing at `../../fonts/*.woff2`). To assemble:

1. Start from `shared/html-template.md`'s Base HTML Structure.
2. **Inline** (not `<link>`): the LOCAL FONTS `@font-face` block from `design.md`, the
   contents of `shared/viewport-base.css`, the design system's CSS, and any needed
   snippets from `shared/animation-patterns.md` — all into one `<style>`.
3. Rewrite the `@font-face` `src:` url from the relative `../../fonts/x.woff2` to a path
   valid from the OUTPUT file's location (or a `file://` absolute path for local render).
   **Never re-add a googleapis/gstatic `<link>`** — that reintroduces the network dep.
4. Wrap slides in `<deck-stage width="1920" height="1080">…<section class="slide">…</section>…</deck-stage>`.
5. Append `<script>` with the full contents of `shared/deck-stage.js` (inline it — no external src).

Result: ONE `.html` that renders the full aesthetic with zero network access.

## Step 3 — (optional) Export PDF

Use `shared/export-pdf.sh` (Playwright chromium → `page.pdf()` at 1920×1080). Playwright
runs at BUILD/export time only — it is a build tool, not a render-time dependency; the
`.html` itself needs no browser tooling to be viewed.

## Step 4 — Verify (the litmus)

Before declaring done, render the assembled `.html` headless and confirm:
- `<deck-stage>`'s internal `.canvas` (in shadow DOM) has a `transform: scale(...)` (stage scaled).
- `document.fonts.check("<weight> <size> '<display family>'")` returns **true** (local font loaded,
  NOT a system fallback) — this is the offline-fidelity guarantee.
- `grep -cE 'https?://' ` on the output `.html` == **0** — the strongest litmus: catches
  googleapis/gstatic AND fontshare/jsdelivr/any-CDN. A single external URL breaks zero-network.
  (A local `xmlns="http://www.w3.org/..."` on an inline `<svg>` is the only allowed exception —
  it is a namespace identifier, not a fetched resource; if present, exclude it: `grep -cE 'https?://' out.html | grep -v w3.org`.)

(See `backend/skills/s_pollinate/tests/test_html_deck_track.py::test_ac6_*` for the exact
Playwright probe — it asserts scale + local-font-load + zero CDN.)

## Font notes

- All 34 systems' fonts are pre-bundled. `Noto Sans Mono CJK SC` (an invalid upstream
  family used only as a monospace fallback in 5 templates) is aliased to `noto-sans-mono`.
- If a design system ever references a family with no local woff2, add it to
  `FONT_MANIFEST.json` and re-run the ingest (fetches from fontsource, fail-loud). Do NOT
  fall back to a CDN `<link>`.
