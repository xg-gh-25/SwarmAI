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

## Step 0 — (if importing an existing PPT) extract content first

If the user gave a `.pptx` (upload or path) to "turn into a web deck":
```bash
python "$SKILL_DIR/scripts/pptx_to_deck.py" "<path/to.pptx>" --out "<content_dir>/import"
```
This extracts per-slide {title, bullets, images KEPT, notes} → `deck_content.json`
(+ `images/`). We do NOT reproduce the original styling — the value is the
professional RESTYLE. Images are preserved (professionally made); decorative chrome
(page numbers, footers, empty shapes) is dropped. Feed this content into Step 2 with
the user-selected system. Then continue to style selection below.

## Step 1 — Pick ONE design system (chat-inline gallery)

The 34 systems are too many to list blind. Recommend a few, show them as INLINE
thumbnails, let the user pick — all as ordinary chat markdown (no new UI).

1. **Recommend top-3** from the DISCOVER answers (audience/outcome/context/tone):
   ```bash
   python "$SKILL_DIR/scripts/recommend_systems.py" \
     --audience {audience} --outcome {outcome} --context {context} --tone {tone} \
     --top 3 --offset 0 --json
   ```
   Each result carries `thumbnail` = a workspace-relative PNG path.
2. **Present them inline** — for each recommendation, emit a standard markdown image
   + the tagline + one-line "why".
   ⚠️ **MUST use an ABSOLUTE image URL, not the workspace-relative path.** Chat
   assistant messages render via `ContentBlockRenderer` → `MarkdownRenderer` WITHOUT a
   `basePath`, so a relative `![](Knowledge/...)` does NOT resolve and shows a broken
   image. `MarkdownRenderer.resolveImageSrc` passes `http(s)://` URLs through
   unchanged, and the backend serves any workspace file at
   `/api/workspace/file/raw?path=<workspace-relative>`. **Do not hand-build this URL —
   use the recommender's `thumbnail_url` field verbatim** (it already prefixes the raw
   endpoint with the correct daemon port from `$SWARMAI_PORT`, default 18321). The src
   looks like:
   `http://localhost:18321/api/workspace/file/raw?path=Knowledge/assets/deck-styles/<slug>.png`
   (verified this renders in chat). Example:
   ```
   Based on your answers, three styles fit:

   ![vellum](http://localhost:18321/api/workspace/file/raw?path=Knowledge/assets/deck-styles/vellum.png)
   **Vellum** — scholarly, quiet navy + warm-yellow serif. Fits a considered leadership brief.

   ![blue-professional](http://localhost:18321/api/workspace/file/raw?path=Knowledge/assets/deck-styles/blue-professional.png)
   **Blue-Professional** — consulting-grade cream + cobalt. Clean and authoritative.

   ![cartesian](http://localhost:18321/api/workspace/file/raw?path=Knowledge/assets/deck-styles/cartesian.png)
   **Cartesian** — museum-catalog Playfair serif. Calm and editorial.

   Pick one, or say **"show more"** for the next set, or **"see all"** for the full gallery.
   ```
   (The recommender's `thumbnail` field is the workspace-relative path — wrap it in the
   `http://localhost:18321/api/workspace/file/raw?path=` prefix when emitting.)
3. **"show more" / "next batch"** → re-run with `--offset 3` (then 6, 9 …). Ordering
   is deterministic, so batches are disjoint (no repeats).
4. **"see all"** → do NOT dump 34 thumbnails at once (floods the chat). Present in
   batches of **6** (`--top 6`, then `--offset 6`, `12`, …), and after each batch ask
   "keep going, or pick one?" — wait for the user before the next batch. Same
   deterministic ordering, so batches stay disjoint.
5. **User picks** by name or by pointing ("the orange one", "the retro Windows one").
   Only then load that ONE system's `systems/<id>/design.md`.

Thumbnails are pre-generated (idempotent) by:
`python "$SKILL_DIR/scripts/render_style_thumbnails.py"` — one comparable sample slide
per system. Re-run only if a system's palette/fonts change (`--force` to rebuild all).

## Step 2 — Assemble a single self-contained HTML file

**Content source:** either the DISCOVER-generated content, OR — if Step 0 ran — the
`deck_content.json` from the imported PPT (reflow each extracted slide's title +
bullets into the chosen system's layout; place its KEPT `images/*` where they fit —
data/diagram images earn a slide region, purely decorative ones may be dropped by
judgment). The design system is the user's pick; the content is theirs.

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

Use `shared/export-pdf.sh`. It does NOT use `page.pdf()`. Instead, per slide it navigates
via the `<deck-stage>` component (`_go(i)`), forces `.reveal` entrance animations to their
finished state, screenshots at 1920×1080, then assembles the PDF with **pdf-lib** — one
image page per slide **plus clickable `/Link` annotations** overlaid from the captured
`<a href>` rects (so links stay clickable, not flattened to pixels). Capture uses the real
loaded webfonts. It self-validates page-count vs slide-count and link-annotation count
(hard-fails on mismatch), and prints an **ADVISORY text-overlap warning** (see Step 4).
Playwright runs at export time only.

> **Note:** the DDD-portable copy at
> `backend/templates/ddd-skills/s_ddd-pollinate/templates/html-deck/shared/export-pdf.sh`
> is an OLDER generation (base64 `<img>` + `page.pdf()`, which flattens links). It is
> intentionally NOT kept byte-identical to this copy — a separate full-generation
> catch-up would be needed to port the modern screenshot+pdf-lib architecture there.

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
- **Overlap advisory:** if `export-pdf.sh` prints a `⚠ ADVISORY: text-overlap` line,
  review those slides — text blocks visually collide (footer clash, stacked captions).
  It is NOT a hard gate (this check is false-positive-prone). If an overlap is
  intentional (decorative accent, ribbon), add `data-om-validate="false"` to that slide
  to opt it out of the check.

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
