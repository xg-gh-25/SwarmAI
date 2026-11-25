# Kind: deck

A deck is always HTML: one file, one `<section>` per slide, on a fixed 1920×1080 stage that
scales to any screen. `cut` turns it into a self-contained `deck.html`, a `deck.pdf`, one PNG
per slide, and on request a `deck.pptx`.

```
"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/work.py" init --dir D --kind deck --title T --style <system id> --size 1920x1080
```

## Pick a design system

`templates/html-deck/systems/` holds 34 design systems; `systems/<id>/design.md` has each
one's font link, theme CSS and layouts. There are too many to list, so recommend three and
show them as pictures:

1. Map the brief to the recommender's values and run it:
   ```
   "$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/recommend_systems.py" --audience <a> --outcome <o> --context <c> --tone <t> --top 3 --offset 0 --json
   ```
   - audience: `leadership`, `customer`, `team`, `developer_community`, `social_followers`
   - outcome: `awareness`, `alignment`, `action`, `data_decision`, `education`
   - context: `meeting`, `email`, `social_media`, `search_learn`, `commute`, `analysis`
   - tone: `serious`, `professional`, `playful`, `bold`, `elegant`, `technical`, `warm`,
     `dramatic`, `quiet`, `creative`
2. For each result, show `![<slug>](<thumbnail_url>)` (use the `thumbnail_url` field as it
   is), its tagline and one line on why it fits.
3. "Show more" → the same command with `--offset 3`, then 6, 9 … (the order is stable, so
   batches never repeat). "See all" → batches of 6, and ask after each batch.
4. When the person picks (by name, or "the orange one"), load only that one `design.md`.

If the person does not want to choose, pick the first result and say so. If the
thumbnails are missing, `render_style_thumbnails.py` makes them once. A reference deck
the person names overrides the gallery: match it (§ Reference deck).

## Write the source: `src/deck.html`

Start from `templates/html-deck/shared/html-template.md`. Then:

1. **Slides.** Each slide is a direct child `<section>` of
   `<deck-stage width="1920" height="1080">`, with `data-slide-id="s-xxxx"` (4–8 lowercase
   hex, unique, never reused) and `data-label="…"`. Do not add your own show/hide CSS for
   slides; the deck stage does that.
2. **Styles.** Put the theme and layout CSS in `src/shared.css` (start from
   `shared/viewport-base.css`, then the design system's CSS, then what you need from
   `shared/animation-patterns.md`). Style one slide inside its own section. Keep the
   `<head>` small: the font link and the link to `shared.css`. A change in `shared.css`
   touches every slide, so in a revise it needs the word `shared` in the open note.
3. **Fonts.** Keep the design system's own font `<link>` (it carries the real italic and
   CJK faces); the renderer caches it locally. Check that every Latin family the CSS names
   is in the link; if one is missing, add `&family=<Name+With+Plus>:wght@<weights>` to it,
   or it silently falls back to a system font. System fonts some systems use on purpose
   (MS Sans Serif, Geneva, Helvetica Neue, Menlo) stay as they are.
4. **Background.** A dark system sets `--stage-bg` and `--slide-bg`. A layered background
   uses `background-color` plus `background-image`, never the `background` shorthand.
5. **Script.** At the end of `<body>`, inline the full `shared/deck-stage.js` in a
   `<script>` (no external `src`).
6. **Brand.** Add the configured signature (INSTRUCTIONS.md § 8): link text and QR on
   the closing slide, the watermark small at the bottom. Use `light_on_dark` QR on dark
   slides and `dark_on_light` on light ones.

## Slide craft

- One idea per slide. The title says the point, not the topic ("Costs fell 30%", not "Costs").
- Big text. Body text on a 1920×1080 slide starts around 28 px; captions are not smaller
  than the craft check allows. When a slide is too full, split it; do not shrink the text.
- Use the space: content should fill the safe area, not float in the middle of it.
- Mark one-line titles and subtitles with `data-nowrap`; give a block `data-role` when
  the guess from its tag would be wrong.
- Line up sibling blocks on the same left and top edges.
- Keep the person's images, portraits and logos. Never drop one to make a layout fit.

## Revise an existing deck

`work.py adopt --dir D --kind deck [--src <existing html>]` registers it in place and adds
missing slide ids without changing anything else. Then follow cards/revise.md.

A .pptx the person wants "turned into a web deck": read its text, images and notes (s_pptx
can read it), and write a new HTML deck with that content in the chosen design system. Keep
the images; drop decoration (page numbers, empty shapes). The .pptx is never the source.

## PPTX export

- **No template:** `work.py cut --dir D --formats html,pdf,png,pptx`. Each slide becomes
  one full-size picture in the PPTX.
- **With the person's template** (cover slides, base slides with a footer): cut first, then
  export with the template, so the template slides stay exactly as they are:
  ```
  "$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/export_pptx.py" --slides D/out/slides --out D/out/deck.pptx --size 1920x1080 --template T.pptx --keep-head N --base-index I --keep-tail M
  ```
  `--keep-head N`: the first N template slides stay as they are (for example the cover).
  `--keep-tail M`: the last M template slides stay. `--base-index I`: the template slide
  (counted from 1) that each content slide is built on; its footer and logo stay, and the
  slide picture goes into its content area. Never replace or restyle a template slide.

PDF pages and PPTX slides must equal the number of source slides; `cut` checks this. After
a template export, count the content slides yourself (head + content + tail).

## Reference deck

When the person says "match the style of <deck>": register that deck as a work too
(`adopt`) and cut it once, so it has a `measure.json`. Then compare:

```
"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/craft_checks.py" --measure D/.versions/<current>/measure.json --reference R/.versions/<current>/measure.json
```

Exit 1 lists where this deck's type scale, margins or colors move too far from the
reference. Fix those, then cut again.

## Delivery

Canvas opens `out/deck.html`. Thumbnails: the cover and the changed slides from
`out/slides/`. In the output lines give slides, pages and file sizes (cards/deliver.md).
