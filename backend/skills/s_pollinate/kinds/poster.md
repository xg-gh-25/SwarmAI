# Kind: poster

A poster is one HTML page rendered to PNG, for a feed or a phone screen. The source is
`src/index.html`; the whole poster is one unit with id `main`. `cut` gives `out/poster.png`
(and `poster@<w>x<h>.png` for other sizes).

## Size

| Use | Size |
|---|---|
| 小红书 card (default for Chinese) | 1080×1440 (3:4) |
| 小红书 tall, story | 1080×1920 (9:16) |
| 朋友圈 square | 1080×1080 |
| X / LinkedIn / link preview | 1200×630 |
| Long poster (长图) | 1080 wide, height follows the content, at most 8000 px; see kinds/page.md |

Details per platform (sections, text size, padding): `references/platform-adaptive.md`.

## Two variants, two directions

The first time, make **two variants** in two design directions, so the person can choose.
Each variant is its own work, side by side: `<dir>-a/` and `<dir>-b/`.

```
"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/work.py" init --dir <dir>-a --kind poster --title T --style d2-paper --size 1080x1440
```

Directions live in `brand/directions/` (D1 Obsidian, D2 Paper, D3 Ink, D4 Neon, D5 Morandi).
Pick by the person's wish first, then by each file's `content_triggers` (strong 3, moderate
2, weak 1; the top two win); D1 if nothing fits. After the person picks, revise only that
work.

## Write the source

Read `brand/poster_design_system.md` first; it is the full system. The rules that matter
most:

- **Tokens only.** Put the direction's `css_snippet` in `:root`; every color in the body is
  `var(--token)`, never a raw hex value. Add `<!-- Direction: D{N} {name} -->` as the first
  comment. Never mix two directions.
- **Center everything.** Every container sets `text-align: center` itself (do not rely on
  inheritance). Only code blocks and bullet lists may be left-aligned inside a centered
  box. A left-aligned poster leaves dead space on a phone.
- **Tight rhythm.** Internal spacing in multiples of 24 px. The visible gap between two
  content blocks is at most 72 px; no empty divider space. A big gap on a phone looks like
  the end of the poster.
- **Type scale (1080 wide).** Hero headline 52–56 px, section headline 40–44 px, body 24 px,
  eyebrow 12–13 px. Chinese body line height at least 2.0. Text at most 700 px wide.
- **Vary the cards.** Never the same card style twice in a row (the direction's `card_styles`).
- **Ban list.** No purple gradients, no rainbow text, no pure black background, at most 2
  hues, at most 3 font families, no stock photos, no generic people illustrations, at most
  6 sections. The full list is in `brand/poster_design_system.md` § Anti-Slop Quality Gate.
- **Brand.** Every configured signature element is on the poster: footer (mark, name,
  tagline, link text, QR) and the watermark at the bottom right. QR at most 120 px:
  `dark_on_light` on D2, D3, D5 and `light_on_dark` on D1, D4. Copy the QR image into
  `src/assets/` and use its file name. Unset elements are left out
  (`brand_config.py --signature`).

## Check before cut

`cut` runs the craft checks (text size, fill, alignment, overflow, overlap, safe area,
images, and the brand: the configured watermark, link text and QR image are on the
poster). Also check by eye, once, on the PNG in `review/`:

- the direction comment is there and no raw hex is in the body;
- every container is centered;
- no gap between blocks is larger than 72 px;
- the footer lines (mark, name, tagline) are there and the QR is at most 120 px (not measured);
- the PNG is the planned width and under 2 MB.

## Restyle without losing content

A restyle ("make it D5", "换个风格") puts the whole poster in scope, so the guard cannot see
what you drop. Count the sections before and after:

```
grep -Ec '<section|class="card' <old index.html> <new index.html>
```

Keep at least 90% of the sections; change only tokens, colors and type. Restyles used to
cut 40–60% of the content without anyone asking.

## Content

Apply `brand/content_principles.md` to the text, and run the hero-framing scan on it:

```
"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/p2_scan.py" D/src/index.html
```

Exit 1 lists lines like "我造了…", "我们是最…"; rewrite them as what is true for the reader.

## 小红书 set

A 小红书 note can carry up to 18 images: posters first (1 to 3: cover, then a comparison
or the core insight), then content cards (4 to 12, one section each, same design). Make the
cards as a deck with `--size 1080x1440`: each card is one slide with its own id, and
`out/slides/` holds one PNG per card. The text goes in the delivery block (kinds/copy.md).
