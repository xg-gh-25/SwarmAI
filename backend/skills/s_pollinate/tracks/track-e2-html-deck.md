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
3. **VERIFY FONT COMPLETENESS (do not skip — silent-fallback trap).** The design.md's
   `<link>` sometimes lists ONLY the CJK families, while its CSS `fontFamily:` declarations
   name Latin display fonts (e.g. `fontFamily: "Barlow, Noto Sans SC, sans-serif"`). If a
   named Latin family is NOT in the `<link>`, it silently falls back to a system font at
   render and the whole aesthetic breaks. So:
     a. Extract every family from the design.md's `fontFamily:"..."` / `font-family:'...'`
        declarations (handle nested quotes like `"'Tektur', cursive"`; skip `{token}`
        placeholders).
     b. Drop CJK (Noto Sans/Serif SC, LXGW WenKai TC), generics (sans-serif/serif/mono),
        and intentional SYSTEM fonts (MS Sans Serif, Geneva, Helvetica Neue, Menlo,
        -apple-system) — those are NOT CDN fonts by design, leave them.
     c. For every remaining Latin family, confirm it appears in the `<link>`. If missing,
        add `&family=<Name+With+Plus>:wght@<weights>` to the googleapis css2 URL.
   (The 34 bundled design.md were backfilled by `scripts` — but a NEW/edited system, or a
   font you add for taste, still needs this check.)
4. **Set the stage background.** viewport-base.css paints the field from `--stage-bg` /
   `--slide-bg` (default #fff). A dark system MUST set these vars, and any multi-layer
   background (gradient + grid) MUST use explicit `background-color` + `background-image`
   (NOT the `background` shorthand — it gets clobbered by the base `--slide-bg` rule).
5. Inline into one `<style>`: the contents of `shared/viewport-base.css`, the design
   system's CSS, and any needed snippets from `shared/animation-patterns.md`.
6. Slides are the direct `<section>` children of `<deck-stage width="1920" height="1080">`,
   each with a `data-label="…"`. deck-stage slots them into shadow DOM and toggles
   visibility via `[data-deck-active]` — do NOT add your own `.slide.active` show/hide CSS
   (it fights the component). Style the `<section>` (or `deck-stage > section`) directly.
7. Append `<script>` with the full contents of `shared/deck-stage.js` (inline it — no external src).

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
  returns **true** for the DISPLAY font (not just body) — a system fallback here means the
  Latin family was missing from the `<link>` (Step 2.3). Check the actual headline face.
- The design system's font `<link>` is present in `<head>` AND includes every Latin family
  its CSS names (fonts wired, not just CJK).
- The field/background painted (not white) — confirms `--stage-bg`/`--slide-bg` were set.

(See `backend/skills/s_pollinate/tests/test_html_deck_track.py::test_ac6_render_scale` for
the exact Playwright probe — it asserts the stage scales.)

## Font notes

- Each design system loads its own upstream font `<link>`. The 34 bundled design.md were
  backfilled (run_c1dd1173) so their `<link>` now includes BOTH the CJK families AND every
  Latin family their CSS names — no hand-adding needed for the shipped systems. (Backfill
  tool: the font-extractor script; re-run it if you add/edit a system.)
- Upstream design.md frequently link ONLY the CJK families while naming Latin display fonts
  (Barlow / Tektur / Source Serif 4 / …) in CSS — that was a real silent-fallback bug (3
  systems shipped fallback fonts before the backfill). Step 2.3 is the guard.
- SYSTEM fonts (MS Sans Serif, Geneva, Helvetica Neue, Menlo, -apple-system) are
  intentional non-CDN fonts — retro-windows' whole aesthetic IS the OS system font. Leave them.
- `Noto Sans Mono CJK SC` is an invalid upstream family name (used only as a mono fallback
  in a few templates); browsers ignore the invalid entry and fall through the stack — no action needed.
- If true **offline** rendering is ever required, open a separate feature to bundle+subset
  the fonts (fontsource complete woff2 + rewrite @font-face). Do NOT bolt local fonts onto
  this track piecemeal — it was deliberately reverted to CDN for font fidelity (run_68176c82).
