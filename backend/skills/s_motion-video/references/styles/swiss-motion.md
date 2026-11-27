# Style: swiss-motion (Swiss Motion Typography)

Units: `u` = frame short side / 1080. At 1920x1080, 1u = 1px. In CSS write `calc(N * var(--u))` (see `swiss-motion.css`). In GSAP use `const u = MV.u;` and tween `y: 28 * u`. Every size below is in u, except "paper px": sizes inside the artifact layer (a 706 x 1008 sheet whose camera scale already contains u).

## Identity

International Typographic Style set in motion: a flat page and a white sheet, black type and bars, a few light greys, one saturated signal colour. Everything sits on a modular grid and lands on the beat; nothing bounces. Inside that strictness there is one exception: the element that disobeys the system becomes the character, the only thing in the signal colour and the only thing allowed to be imprecise. Five identity rules, all mandatory: flat print graphics on a grid (page, paper, ink, light greys, one signal colour); every arrival snaps to the grid and to a note value, never a bounce; one exception per film, and only it carries the signal colour; extreme scale contrast (a numeral 65% of frame height beside 19u technical notes); a two-page spread, left page = the "program" (numeral, rule sentence, notes), right page = the "artifact" (one sheet of paper), cause on the left, effect on the right.

Best for: brand identity and design systems, event promos, product spec sheets, record numbers, years and prices, design talks, "one rule at a time" explainers, launch teasers with one hero claim. Not for: warm human stories, photo-led films, dense dashboards, playful hand-made topics, anything needing gradients, depth or realism.

## Palette

Flat, high-key, low-contrast page with pure ink; no light, no shading.

| Hex | Role |
|---|---|
| `#EAE9E5` | page, full-frame base |
| `#FFFFFF` | paper (the artifact sheet), wall pieces |
| `#111111` | ink: all text, black bars, rules, caption rule, knife line |
| `#E30613` | the one signal colour (default red) |
| `#CFCEC9` | page grid, grid extension, "second voice" grey blocks |
| `#C9C8C2` | paper grid while it is being built |
| `#E2E1DC` | paper grid once built; frame column bands |
| `#77766F` | grey lead, small grey text, readout labels |
| `#DFDED9` | wall board behind the artifact wall |

Signal alternatives (exactly one per film): orange `#FF5A00` (page `#F2F0EA`, ink `#141414`), ultramarine `#1F3FFF` (page `#E9ECEF`, ink `#0B0B0B`), green `#00A651` (red set).

Signal goes only to: the exception, its trail, ring and pulse, the "off grid" word, the opening and wall-cut red lines, the playhead and the bars it is sweeping, the main wall label, the one highlighted 3D part. While a playhead or red line shows, all but the exception stays greyscale. Overprint allowed: ink type on a signal block.

## Type

One typeface, weights 400-900 (Helvetica Neue tops out at 700): `"Helvetica Neue", -apple-system, "SF Pro Text", "PingFang SC", "Noto Sans CJK SC", "Hiragino Sans GB", sans-serif`. Flush left, ragged right; every block starts on a column line; white space is part of the layout.

Scale (u unless noted):

| Role | Size | Weight / details |
|---|---|---|
| film title | 120 | 700, two lines flush left, line step 0.9 x size, baseline 132u above the opening red line |
| statement (rule sentence) | 76 | 700 (other sentences 400), line step 1.1 x size; one line (two hit the numeral) |
| grey lead ("Rule one") | 30 | 500, `#77766F`; baseline 168u, statement baseline 84u below it |
| caption-row and technical notes | 19 | 500, 1px ink rule above, first baseline 30u below the rule, line step 26u |
| labels, readouts | 22-26 (default 26); bar counter 18; wall labels 22 | labels 500 `#77766F`, values 700 ink, tabular; counter "bar 02.3" 600; main wall label 700 signal |
| captions | 40 | 500 ink |
| giant numeral | digit height 700 (65% of 1080) | 700, tracking -0.04em; font-size = height / digit ratio (about 0.714 for Helvetica Neue, 0.667 for Noto CJK; verify in a test frame); shifted left 0.05 em (optical flush-left); baseline 1044u = sheet bottom |
| artifact poster title | 170 paper px | 800, tracking -0.03em |
| artifact sub / spec lines | 32 / 20 paper px | 700 / 500, spec line step 1.3 |

Tracking: >= 90u gets -0.022em (large CJK -0.01em, inferred). Line box 1.3 x size; top = baseline - 1.03 x size. CJK: `nowrap` and explicit breaks. Tokens: one CJK character (punctuation glued to the previous one) or one Latin/digit run; word gap 0.28em.

Portrait (inferred): numeral height 20% of H with its baseline on the bottom of the upper page; lead baseline at 8% H; Notes at column 3, y 30% H.

## Layout grid

- Landscape: 12 columns, margin 5% W (96u at 1920), gutter 24u, column 122u; column c left = margin + c x (colW + 24u); span n = n x colW + (n-1) x 24u; baseline 24u.
- Spread: program page = columns 0-5, y 54u, height H - 108u; column 6 is the fold; artifact page = columns 7-11, y 36u to 1044u. The sheet (706 x 1008, Swiss world format 0.70) is scaled by k = min(pageW / 706, pageH / 1008), centred there.
- Paper module grid (from the content, Gerstner style): origin (34.5, 35), 7 columns x 91, 16 rows x 40 paper px; a cell block insets 4 left/right, 3 top/bottom (width 83). Fewer, wider modules: 7 x 91 beats 12 x 43 (thin columns read as a chart).
- Fixed positions: caption row rule y 36u over 6 columns, items at columns 0 / 2 / 4 (film / "Rule 01 / 03" / keyword); statement and numeral at column 0; notes at column 4, rule y 962u ("col 7 · row 16", "module 91 x 40"); bar counter column 6, bottom 1044u; opening red line y 300u (portrait 20% H).
- With captions on, keep the bottom-left sixth free: Notes go to the top of the program page or right; lift the numeral above the caption rule (inferred: baseline 930u, height 600u).
- Portrait (inferred): 6 columns, margin 72u, gutter 24u; program = upper page (y 54u, height 42% H), artifact = lower page (y 46% H, height 50% H); caption row on top, captions bottom-left.

## Camera grammar

A flat 2D matrix on the artifact layer only: centre (x, y), scale s, rotation r (deg); SNAP ease, arriving on the beat. Wrapper `#artifact` (706 x 1008, at 0,0, `transform-origin:50% 50%`); GSAP sets `x = camX - 353`, `y = camY - 504`, `scale`, `rotation`. The program page is a separate frame-space layer. No 2.5D depth, no slow push.

| Shot | Expresses | How |
|---|---|---|
| fixed spread | cause (left) -> effect (right) | camera at artifact-page centre, s = k; one rule acting once, before/after |
| hard push by a page or column | the system's next step | x by one page or column width, 0.5 s SNAP |
| rotate 90 and hold | same object, another reading (poster -> score) | r -90 over 0.75 s (1.5 beats), scale 1.34u (portrait 0.86 W / 1008), hold; then grid, labels, playhead |
| pull back to the wall | one program, many outputs | 1.0 s SNAP to s = 0.42u (portrait 0.3u), centre y 452u (portrait 45% H) |
| push into one module | detail, the unit of the system | scale so one 91 x 40 cell fills the frame; for big ratios tween a proxy `z = Math.log(s)` and set `scale = Math.exp(z)` |
| grid extends past the paper | the system is bigger than the work | extension lines grow out in one beat, retract in 0.5 s |
| equal frame cells | parallel cases (options, variants) | one case per cell of the frame grid |
| scroll down a column | reading, lists, spec tables | y one row block per beat |
| left-page slide | the program steps aside | program x -1200u in one beat, artifact to x 860u (portrait: program y -50% H) |

Upright labels after a rotation sit in frame space:
```js
const toScreen = (cam, px, py) => { const a = cam.r * Math.PI / 180, dx = (px - 353) * cam.s, dy = (py - 504) * cam.s;
  return { x: cam.x + dx * Math.cos(a) - dy * Math.sin(a), y: cam.y + dx * Math.sin(a) + dy * Math.cos(a) }; };
```
Use the camera's rest state; never label while rotating.

## Motion

Only two curves. SNAP = cubic-bezier(.6,0,.25,1) for every arrival; `ease:"none"` (linear) for drawing lines, the playhead and the exception. No overshoot, no spring, no back, no fade.

```js
const bez = (x1, y1, x2, y2) => { const B = (s, a, b) => 3*(1-s)*(1-s)*s*a + 3*(1-s)*s*s*b + s*s*s;
  return p => { if (p <= 0) return 0; if (p >= 1) return 1; let lo = 0, hi = 1, s = p;
    for (let i = 0; i < 30; i++) { const x = B(s, x1, x2); if (Math.abs(x - p) < 1e-6) break; x < p ? lo = s : hi = s; s = (lo + hi) / 2; }
    return B(s, y1, y2); }; };
const SNAP = bez(.6, 0, .25, 1);                       // ease: SNAP
const BEAT = .5, E8 = .25, E16 = .125, BAR = 2, MIN = .25;   // 120 BPM; tempo(bpm): BEAT = 60/bpm
// land at t1: start one note early, arrive exactly on the beat; never shorter than MIN
const land = (tl, el, from, to, t1, d = E8) => { const D = Math.max(d, MIN);
  return tl.fromTo(el, from, { ...to, duration: D, ease: SNAP, immediateRender: false }, t1 - D); };
```

Durations are note values (120 BPM). Every scene time is a multiple of 0.125 s and is an arrival time: the move starts one note earlier and lands on the beat. 0.125 s is spacing between elements, never a move length (3.75 frames reads as a jump); moves overlap.

| Action | Duration | Ease | Notes |
|---|---|---|---|
| any landing | 0.25 min (E8) | SNAP | starts at t1 - max(d, 0.25) |
| text token rise | 0.25 | SNAP | from yPercent 100 inside a clip box 1.3 x size tall; token k lands at start + k x stagger + 0.125 |
| token stagger | Latin 0.25; CJK 0.125; statement Latin 0.375 | | lead tokens first, statement continues after them |
| token exit | 0.25 | SNAP | to yPercent -100, lands at `end` |
| caption row / notes | rule 0.25 | SNAP | row: rule and items land at `start`; notes line i lands at start + (i+1) x 0.125 |
| rule draw | max(d, 0.25), d default 0.25 | `none` | end time fixed; retract 0.5 SNAP from the start end |
| opening red line | 3u, 0.5 across the full width | `none` | retracts at about 2.5 s |
| column bands | each 0.25, column c lands at start + (c+1) x 0.125 | SNAP | grow up and down from the red line; retract 0.5 each, right column first, 0.03125 apart |
| film title | stagger 0.25 | SNAP | exits at about 2.75 s |
| paper grid | verticals linear 0.25 each, one every 0.25 (fast 0.125, faster 0.0625); horizontals one every 0.125 (fast 0.0625, faster 0.03125) | `none` | horizontals start at start + 8 x colStep - 0.25; whole grid retracts 0.5 linear |
| paper wipe | 0.5 (one beat) top to bottom | SNAP | faster flashes instead of wiping |
| block grow | 0.25 down (or right), blocks 0.125 apart | SNAP | |
| block move | 0.25 | SNAP | to the new rect |
| giant numeral rise | 0.75 (1.5 beats), 4 levels | staircase ease (below) | one beat is the minimum; sink 0.5 SNAP at the next rule |
| knife cut | 2px line 0.125 linear, then squash to the midline 0.25 | `none` / SNAP | squash starts as the line lands |
| reflow ripple | each 0.25; pairs land every 0.125, nearest first | SNAP | starts right after the cause lands |
| exception | linear, pause 0.25 at 40%, d 2 -> 4 columns; enter 0.5 SNAP | `none` | the only off-beat move |
| camera key / grid extension | 0.5; rotate 0.75; wall 1.0 | SNAP | |
| playhead | draws across 0.25 (half beat), sweeps, retracts 0.25 | `none` | 4px, paper width + 24 each side |
| colour change, minimum move | 0.25 | SNAP | never a one-frame flip |

Staircase ease for the numeral (continuous, slows once per level, no dead frames). GSAP's own `steps(4)` jumps a level in one frame and breaks the 0.25 s floor; use it only for values that are discrete by nature (bar-counter digits).
```js
const stairs = (n = 4, dur = .75) => { const s = 1 / n, span = dur / n, ov = (Math.max(1.5 * span, MIN) - span) / 2 / dur;
  return p => { let v = 0; for (let i = 0; i < n; i++) { const a = Math.max(0, i*s - ov), b = Math.min(1, (i+1)*s + ov);
    v += SNAP(Math.min(1, Math.max(0, (p - a) / (b - a)))); } return v / n; }; };
tl.fromTo(".sw-numeral", { yPercent: 100 }, { yPercent: 0, duration: .75, ease: stairs(4, .75) }, t0);
```

Rhythm: one rule = 2 bars (4 s); one nearly still bar before a reveal (never over 2 s fully still); voice pauses on beats. Every scene keeps one continuous move (playhead, bar counter, readout, turntable). Elements already in place at scene start are set in their end state in HTML.

## Materials and light

- Flat everywhere: no light, shading, highlight, glow, grain, vignette, gradient, shadow or particles.
- Blocks are flat rectangles, no radius or stroke. Merged cells overdraw 1 px up and down at each seam (seams show after rotation). Bleed: the sheet clips its content, so draw to y -2 or x 708.
- Depth only from the "second voice": full-width `#CFCEC9` column blocks under the black bars, a shadow-like diagonal.
- Grid lines are 1px. Exception trail and ring 1.5px; pulse ring radius d/2 + 70 x SNAP(p), stroke 6 x (1 - p) paper px.
- Wall: board `#DFDED9`, 110 paper px margin; white pieces 80 apart, two each side of the main sheet, one signal element each.

## Technique library

Capabilities, not obligations. Blocks and the exception are 1x1 px unit divs (`transform-origin:0 0`); GSAP sets `x, y, scaleX = w, scaleY = h`, never width/height (SVG `<rect>` + `attr` also works).

- **Grid with meaning.** Columns and rows become quantities (notes, weekdays, hours, floors, years): the climax "this poster is a score". One inline `<svg>` of 1px lines, grown linearly (`scaleX/scaleY` or `strokeDashoffset`).
- **Landing is the beat.** Every arrival is a hit: `land()` for every entrance.
- **Rule program (the film's spine).** Each rule acts on the same sheet; at the end the same program makes many sheets (wall). Per rule (4 s): caption row "Rule 0n / 0N", numeral, lead + statement, notes, then the sheet changes.
- **Extreme scale contrast.** Numeral 700u beside 19u notes, baseline on the sheet bottom.
  ```html
  <div class="sw-numeral-mask" style="top:calc(302*var(--u))"><div class="sw-numeral">1</div></div>
  ```
  Mask top = base - 1.06 x height (302u), height 1.06 x height; the numeral travels the full mask height.
- **Opening red line.** A 3u signal line crosses the frame in 0.5 s linear; the 12 frame column bands grow up and down from it, 0.125 apart; the title rises sitting on the line.
  ```js
  tl.fromTo(".sw-redline", {scaleX: 0}, {scaleX: 1, duration: .5, ease: "none"}, 0);
  bands.forEach((b, c) => land(tl, b, {scaleY: 0}, {scaleY: 1}, .25 + (c + 1) * .125));   // transform-origin at the line
  ```
- **Clip-reveal text.** Every word (CJK: every character) rises from a clip box under its baseline; leaves upward. Never fades.
  ```js
  toks.forEach((el, k) => land(tl, el, {yPercent: 100}, {yPercent: 0}, start + k * stag + .125));
  toks.forEach(el => land(tl, el, {yPercent: 0}, {yPercent: -100}, end));
  ```
- **The exception.** The one signal element breaks the grid: linear, unsnapped, pauses an eighth note at 40% of the path ("looks back"), grows 2 -> 4 columns as it moves. Its readout (`col 5.00 / row 0.00 / on grid`) turns to signal `off grid` with fractional or negative values (U+2212 minus). The origin keeps a 1.5px empty ring and a trail line until the grid retracts (both collapse in 0.25 s). The voice may pause while it flies.
  ```js
  const P = {a: 0}, D = 2.5, L = 4.0, p0 = 3.0, p1 = 3.25;       // depart, land, pause window
  const draw = () => { const cx = lerp(f.cx, to.cx, P.a), cy = lerp(f.cy, to.cy, P.a), d = lerp(f.d, to.d, P.a);
    gsap.set(dot, {x: cx - d/2, y: cy - d/2, scaleX: d, scaleY: d});
    colEl.textContent = fmt((cx - d/2 - 34.5) / 91); rowEl.textContent = fmt((cy - d/2 - 35) / 40); };
  tl.fromTo(P, {a: 0}, {a: .4, duration: p0 - D, ease: "none", onUpdate: draw}, D)
    .fromTo(P, {a: .4}, {a: 1, duration: L - p1, ease: "none", onUpdate: draw, immediateRender: false}, p1);
  ```
  Optional enter: 0.5 s SNAP drop down its column from above the sheet. Hesitate: shift 0.125 x d sideways over 0.25 s, back from +0.125 s over 0.25 s (two snaps, not a bounce).
- **Knife cut.** Delete: a 2px ink line crosses the sheet (10 px beyond each edge) in 0.125 s linear, then the element squashes `scaleY` to its midline in 0.25 s.
- **Reflow by distance.** After the cause lands the others move nearest-first: rank by `Math.hypot` to the cause, land at t0 + floor(rank / 2) x 0.125. The final layout must be clearly bolder: one main diagonal, long-short rhythm, the signal element at 2x, bars bleeding off the edge, ink overprinting signal; a slightly tilted correct layout is not enough.
- **Grid extension.** Grid lines extend 2600 paper px (1600 in tight shots) past the sheet in `#CFCEC9`, drawn behind the sheet; out one beat, back 0.5 s.
- **Rotation reveal.** Fixed order: rotate -90 (0.75 s) -> hold -> grid draws fast -> upright labels (26u 700 column names 0.0625 apart, 22u 600 row times) -> playhead. Bars are signal while the playhead is in their rows (row = (t - start) / (end - start) x 16); at the exception a pulse ring spreads. Reference: arrive 1.25, labels 1.75, sweep 2.25 -> 4.25.
- **Artifact wall.** Pull back to 0.42; the same grid generates the other sheets (seeded mulberry32 variants), each with one signal element; labels rise under each 0.125 apart. Close: a 3u red line crosses at wall centre in 0.5 s linear; each piece squashes `scaleY` in 0.25 s once the line passes its screen x (t = cut + 0.5 x x / W); the board `scaleX` shrinks from the right over 0.5 s. Show the wall only during its shots.
- **Bar counter.** "♩ = 120" over "bar 02.3": bar = floor(t / 2) + 1, beat = floor((t mod 2) / 0.5) + 1, written in `onUpdate` of a linear scene-long proxy tween.
- **3D asset on paper (optional).** An orthographic line drawing on the sheet: white flat faces, 1.5px ink edges, no light, no perspective; CSS 3D boxes (`preserve-3d`, no `perspective`) or an SVG wireframe. Yaw -35, pitch 22-24; turntable linear 5-8 deg/s, snapping to -35 one beat before the first step. Explode one step per beat (reference 2.5 / 3.5 / 4.5 s), close over 2 beats from 5.75. Only the part being removed turns signal (0.25 s SNAP in and out). A 3px ink progress line (y 968, x 34 -> 672) fills linearly. Part names and step go into Notes. One model per scene.

## Transitions

The style never dissolves: between scene files always `cut`, never `fade`. The incoming scene opens on the same page with the grid and carried elements already in place (end state in HTML); first motion lands within 0.25-0.5 s. Style-owned transitions live in one scene file:
- **Paper wipe** (0.5 s = 15 frames, SNAP): new sheet clip-path `inset(0 0 100% 0)` -> `inset(0 0 0% 0)`.
- **Knife cut** (0.125 s line + 0.25 s squash = 11 frames).
- **Left-page slide** (0.5 s = 15 frames, SNAP): program layer x -1200u, artifact to x 860u.
- **Camera move** (0.5 s = 15 frames; rotate 22-23 frames; wall 30 frames; SNAP), then reflow.
- **Red-line cut** (0.5 s = 15 frames, linear) to clear a wall or close a chapter. The film may end back on the opening spread.
```js
tl.fromTo("#program", {x: 0}, {x: -1200 * u, duration: .5, ease: SNAP}, tSlide - .5)
  .fromTo("#artifact", {x: homeX - 353}, {x: 860 * u - 353, duration: .5, ease: SNAP}, tSlide - .5);
```
No black frames, no dip to black, no whole-frame fade.

## Captions

A museum label, not a bar: a persistent 1px ink rule bottom-left from column 0 over 8 columns (portrait 6), one line of 40u 500 ink under it, flush left, no box. Only the text changes: the new sentence rises token by token from a clip box (0.25 s SNAP each, min(0.25, max(0.03, 0.5 x sentence duration / tokens)) apart); the old one leaves upward in 0.25 s. Trailing commas and full stops dropped. Rule y = H - 54u - 1.6 x size; text top 18u below it.

```css
--cap-bg: transparent; --cap-fg: #111111; --cap-font: <sans stack>; --cap-size: calc(40 * var(--u));
--cap-radius: 0; --cap-bottom: calc(48 * var(--u)); --cap-weight: 500; --cap-accent: none; --cap-shadow: none;
--cap-border: 1px solid #111111;   /* top rule only; label is left-aligned at column 0, width = 8 columns (portrait 6) */
```

## Image-gen guidance

(none) by default: every graphic is a flat shape, line or type drawn in HTML/SVG; a photo breaks the flat print surface. If a product image is mandatory, only inside the sheet, cut out on white.
- Anchors: "flat product cutout on pure white, orthographic front view, no shadow", "technical line drawing, black 1.5px outlines on white, no shading".
- Never generate: grids, numerals, type, bars, the exception, textures, gradients, glows, scenes with light or depth, faces, logos.
- Post-treatment: desaturate to greyscale; place within whole grid modules; no filters, no shadow.

## Taboos

- No gradients, shadows, glow, particles, grain, texture, vignette, light, rounded or stroked blocks.
- No fade or dissolve of anything, not even a small label (clip-rise instead).
- No bounce, overshoot, spring, back or elastic; no moves shorter than 0.25 s; no off-beat moves except the exception.
- No second signal colour; no second signal-coloured thing beside the exception (playhead and red lines excepted).
- No serif, script or blackletter faces (one typeface only); no centred or justified text.
- No labels during a rotation; no thin-column grids that read as charts; no lit or realistic 3D; no still frame over 2 s.

## Known gaps

- Font: the original used a narrower neo-grotesque (Archivo). Helvetica Neue is close in spirit but wider, lacks 800/900 Latin, and its numeral ratio 0.714 is an estimate. CJK reads heavier than Latin.
- "Picture is the score": the original drove picture and music from one data set (columns = pitch, rows = duration); here music cannot be matched to the blocks.
- 3D: no real meshes, hidden-line removal or arbitrary exploded models; only CSS 3D boxes or hand-built SVG wireframes.
- Cross-scene style transitions are limited to `cut`; wipes, slides and camera moves live inside one scene file.
- The museum-label caption needs the runtime to honour `--cap-border` and left alignment; otherwise it falls back to a centred boxless line.
- Portrait layout, CJK tracking (-1%) and the lifted numeral when captions are on are inferred, not from a reference.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
