# Style: whiteboard (Whiteboard Lecture)

Units: `u` = frame short side / 1080. At 1920x1080, 1u = 1px. In CSS write `calc(N * var(--u))` (see `whiteboard.css`). In GSAP use `const u = MV.u;`. Board units: the whole board is one world, default 8000 x 4500 board units, laid out once inside `#wb-world`; at camera zoom 1 one board unit = 1u. Sizes below are board units unless marked screen u.

## Identity

A lecture written live on a real glossy whiteboard. Everything the viewer learns is drawn in front of them by a floating marker in real writing order. The board is a map: each idea owns a region, distance on the board equals distance in the argument, and returning to an earlier region is a free callback. The camera travels between regions, often led by the line the pen is drawing, in one continuous shot. Five identity rules, all mandatory: a real board (no colour blocks, cards, icon stickers or photos); the board is a map; marker, not animated text (ink pooling where the pen lands, dry-brush grain, overshooting unclosed strokes, ghosts of old lessons, a gloss that moves slower than the board; text is always written stroke by stroke, never faded, flown or popped in); no hands (the marker floats, magnets and the eraser move by themselves); one continuous shot (change place with a whip pan, never a fade or wipe).

Best for: science principles, online courses, "how it works", mechanisms, derivations, step-by-step reasoning, explainer promos. Not for: data dashboards, software UI demos, luxury brand films, live-action stories.

## Palette

Neutral board and wall; at most three marker colours, fixed meanings.

| Hex | Role |
|---|---|
| `#FBFBF9` | board, bright top-left end of the gradient |
| `#EEEDE9` | board, darker bottom-right end; one gradient spans the whole board |
| `#D8D3CA` | wall, seen only when the camera pulls past the board edge |
| `#9AA0A8` | ghost marks, grey, 5-10% opacity |
| `#8AA0C8` | ghost marks, light blue (about 1 in 5) |
| `#23262C` | dark pen: objects and structure; caption text |
| `#2A5CB3` | signal pen: signals, measurement, process |
| `#D97757` | accent pen: the one thing that matters most, at most one per region |
| `rgba(250,249,245,.9)` | caption label (cream) |
| `#E9EBEE` / `#B9BEC6` / `#8F949C` | aluminium frame gradient; inner edge `#7D828A`; pen tray `#D7DADF` -> `#8E939B` |

Ink sets (one per film, meanings never change): classic (default) `#23262C` / `#2A5CB3` / `#D97757`; nature `#23262C` / `#2E8B57` / `#C8363A` (deeper `#2E7A4D` / `#C8413A`); night `#1F2A44` / `#1F8A8A` / `#7A4FB3`. Monochrome variant: all dark pen, one accent element as the only colour. Accent on props only: accent pen cap and band, hero magnet, eraser label, caption dash.

## Type

Handwriting only on the board, titles and end lines included.
- Latin, digits, formula symbols (μ ≈ → ← × ÷ − ✓ ² ³ ↑ ↓ ° ± % .): `"Chalkboard SE", "Bradley Hand", "Marker Felt", cursive`.
- CJK: `"Hannotate SC", "HanziPen SC", "Kaiti SC", cursive`, weight 300-400, written with the three-row sweep; CJK punctuation is one sweep.
- Captions only (screen): `-apple-system, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Noto Sans CJK SC", "Hiragino Sans GB", sans-serif`, 500.

Sizes are cap height h in board units. Latin font size = h / capRatio, capRatio measured once with canvas `measureText("H").actualBoundingBoxAscent / fontSize` (about 0.7 for Chalkboard SE). CJK font size = 1.3h, advance 1.04 x CJK size.

| Role | h | Details |
|---|---|---|
| title | 120-200 (11-19% of frame height at zoom 1) | ink width max(5, 0.13h) |
| label | 30-70 (3-6% H) | ink width max(5, 0.13h) |
| caption (screen u) | 44, shrinks to 34, then two lines | sans 500, letter-spacing 1.5u |
| portrait title / label | 90-130 / 36-56 | |

Per-glyph jitter from a seeded PRNG (mulberry32): rotation (r - 0.5) x 0.07 rad (±2°), baseline (r - 0.5) x 0.07h (±3.5%), size x (1 + (r - 0.5) x 0.06). CJK roughly doubles it: rotation ±4°, size jitter x1.5, x jitter ±3% h, baseline x1.7. Optional slant 0.1 ≈ 6° right. Keep CJK labels short; formulas, numbers and units in the Latin hand.

## Layout grid

- One board, default 8000 x 4500 (short film: 4400 x 2300). One screen at zoom 1 = 1920 x 1080 board units. Plan region centres first (inferred: neighbours about one screen apart, a turn 1.5-3 screens away). Related content side by side, contrasts far apart, reused results stay put.
- Bottom 12% of the frame (130u landscape, about 230u portrait) never holds board content. Labels fully in or out of frame. Magnets and markers above ink, never covering text to read; the eraser never crosses a kept label.
- Drawing ink 6-11 (default 9); wobble ±2.6, wavelength 220; lines bow 1.2%, ticks and rulers 0-0.3%.
- Content inside a screen (a map in a phone): ink clipped to one hand-drawn rounded box.
- Optional structures: proof (conclusion on top, steps going down, box each proved step); debate (two pens own half the board each, meet in the middle); timeline (one long line the camera rides left to right).
- Portrait (inferred): regions stacked, rulers under 800, vertical travel, side margins 60, parks outside bottom-right.

## Camera grammar

The camera is a 2D transform on `#wb-world` (origin 0 0): `translate(W/2, H/2) rotate(r) scale(z) translate(-x*u, -y*u)`, (x, y) = board point at frame centre. Three motions only: pan; zoom in log space with position weighted by 1/z (push and pull feel like a dolly); roll ≤ 0.01 rad (inferred). Keys `[t, x, y, z, roll, ease]`, ease on the arriving key.

| Shot | Expresses | How |
|---|---|---|
| pull back from a tiny drawing | scale, setting | z 3-4 -> 1, `io`, 1.5-2 s |
| ride the line being drawn | one idea leads to the next | keys every 0.25 s on the pen tip + (200, 40), constant z, linear; arrive with an `o` key |
| still | read now | hold 2-4 s (formulas, definitions, lists) with a 1-2% log push (never still 2 s) |
| stop, then whip | new thought after a pause, "but" | full stop, then 0.5-0.8 s, `io` or `o` |
| return to an earlier region | callback | the exact earlier key |
| pull out to two regions | two things at once | z 0.5-0.7 |
| slow push on one symbol | this word is the point | z +10-30% over 2-4 s, `io` |
| pan along a long figure | order, time | linear keys along it |
| past the board edge to the wall | the lesson is one map | z 0.3-0.4; frame, tray, wall below z 0.5 |

Eases as GSAP eases: `io` quintic smootherstep `p => p*p*p*(p*(p*6-15)+10)` (default); `i` = `power2.in`; `o` = `power2.out`; `l` = `"none"`; `s` smoothstep `p => p*p*(3-2*p)`; `h` = `"steps(1)"` (hold, then jump at the key; only hidden under a whip or a scene cut). Transit moves 0.5-2 s; the pen may stop one full beat before a whip.

Between keys a, b at eased e: `z = exp(lerp(ln a.z, ln b.z, e))`; weight `w = (1/a.z - 1/z) / (1/a.z - 1/b.z)` if zoom changes, else `w = e`; `x = lerp(a.x, b.x, w)` (same for y); `roll = lerp(a.r, b.r, e)`.

## Motion

Every stroke follows the hand speed curve: slow landing, fast middle, slow lift. `const hand = p => p - Math.sin(2*Math.PI*p) / (2*Math.PI) * 0.8;` used as `ease: hand`.

| Action | Duration | Ease | Notes |
|---|---|---|---|
| label | 0.3-0.8 s | `hand` per stroke/glyph | ends exactly at its word's end; starts 0.3-0.8 s before |
| title | about 3 s (CJK short title 1.5-2.5 s) | `hand` | |
| small diagram (clock, satellite) | 1-1.5 s | `hand` | |
| free speed (no deadline) | 1500 units/s, text x0.8 | | min stroke 0.09 s, short (< 14) 0.05 s, dash 0.02 s |
| hop between strokes | distance / 2600 s, clamp 0.035-0.3 | smoothstep | lift sin(πu) x min(1, d/220 + 0.25); with a deadline, window = max(0.05, by - start), hops ≤ 35% of it, rest split by stroke length |
| park (idle gap > 0.75 s) | out 0.38 s | `power1.in` | lift min(1, 3u); to the park point off-screen bottom-right |
| return | 0.42 s before next stroke | `power2.out` | lift min(1, 2.2(1-u)) |
| busy pen | next task at previous end + 0.05 s | | overloaded pen drifts late: give overlaps to another pen |
| marker idle wobble | continuous | | angle -0.95 rad + 0.02 sin(5.3t + id) - 0.06 lift |
| magnet drop / slide / sink | 0.3 s / by keys / 0.8 s | lift 1->0 / smoothstep / smoothstep | slide adds lift 0.25 sin(πu); sink tilts 0.5 rad, drops 40, fades |
| eraser | constant speed | `"none"` | band 90-230 (150), strength 0.9; flies in/out 0.45 s from bottom-right |
| duet | two pens, own beats | | 0.27 s and 0.27 x 119/128 s (inferred) |
| whip pan | 0.5-0.8 s | `io` or `power2.out` | after a full stop |

Faster than a real teacher, never so fast that stroke order is unreadable. Nothing exits except by eraser or magnet sink. Text always has a deadline: the write ends on the word's end time from `words.json`.

## Materials and light

- Board: SVG `linearGradient` in board units from (0, 0) to (0.3 BW, BH), `#FBFBF9` -> `#EEEDE9`. Bright soft diffuse room light, no vignette.
- Scuff (2-3%), procedural, fixed to the board: a rect at opacity 0.025 with filter `#wb-scuff` = `<feTurbulence type="fractalNoise" baseFrequency="0.0025 0.035" numOctaves="3" seed="11"/>` + `<feColorMatrix values="0 0 0 0 .36  0 0 0 0 .37  0 0 0 0 .40  1.8 0 0 0 -0.75"/>` (grey wipe streaks), plus 20-30 seeded swirl arcs (r 120-400, width 30-60, `#9AA0A8` at 0.015). (Inferred; original: 1024 tile at x1.6.)
- Ghosts: 46 per full board (seed 99), 5-8 per screen: 30% words (h 50-90), 25% circles (r 40-150), 25% arrows (dx ±250, dy ±150), 20% lines (200-700, dy ±40); width 12 at 1.5x, opacity 0.05 + 0.05r, blue with probability 0.2. Words: unrelated leftovers "x = 4", "lab 3", "TUE", "q.2", "f(x)", "pH 7", "due fri", "→ 12", "ok!".
- Ink: opacity 0.94; the ink group is `mix-blend-mode: multiply`, overlaps darken. Pool at each stroke start (length > 30, not hatch): dot r 0.33w at 0.3, 0.5w along. Stroke shape: chisel width (0.8 + 0.2|sin(θ - 2.35)|) x w, landing pressure 0.72 -> 1 over 2.2w, last 2w taper 12%, round ends.
- Dry grain, knocked out of the ink, fixed to the board: ink mask = white rect + rect with filter `#wb-dry` = `<feTurbulence type="fractalNoise" baseFrequency="0.08 0.45" numOctaves="2" seed="7"/>` + `<feColorMatrix values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  9 0 0 0 -5.6"/>` (sparse black specks and dashes; inferred, original tile 512 at x0.9) at opacity `clamp((z - 0.3) x 2, 0, 1) x 0.9`: fades from zoom 0.8 to 0.3, gone below.
- Gloss: one diagonal window-light band in screen space, clipped to the board: `linearGradient` (sx - 700u, 0) -> (sx + 700u, 1080u), white at 0 / .10 / .16 / .10 / 0 at offsets 0 / .45 / .5 / .55 / 1, `sx = (-cam.x x z x 0.35 + 900) x u`: drifts at 0.35x camera speed; sells the gloss; the only light effect.
- Props (the only 3D objects; shadows `rgb(40,40,50)`, magnets `rgb(60,30,20)`; higher lift = shadow further down-right, softer, fainter):
  - Marker, length 430, tip at the pen pose, scale capped at 1.25u per unit. Local shape: felt tip `M-2,-5 Q-8,0 -2,5 L20,9 L20,-9 Z` in ink colour; cone x 20-50, 20 -> 48 tall (`#DFE2E6`, `#FFF` .35, `#8B9099`); white barrel x 48-348, 52 tall, rx 6 (`#E6E8EB`, `#FFF` .3, `#E9EBEE` .62, `#9EA3AB`); band x 150-260 and cap x 338-430 (58 tall, rx 12) in ink colour shaded x0.8 / x1.25 at .3 / x0.55. Lift raises 26z; shadow offset (0.9 up + 6z, 1.2 up + 8z), blur (6 + 10 lift)z, opacity 0.26 - 0.08 lift.
  - Felt eraser: 285 x 150, rx 14, `#4A4F58` -> `#2E3239` -> `#1D2025`, felt strip `#C9C4BA` bottom 30%, highlight rgba(255,255,255,.18), accent label 0.36w x 0.2h at 0.9; rotation -0.08 + 0.02 sin(17t) rad; shadow offset (10z + up, 16z + up), opacity 0.3 - 0.1 lift.
  - Pin magnet (size 150, point at the tip): `M0,0 C-18,-40 -58,-70 -58,-112 A58,58 0 0,1 58,-112 C58,-70 18,-40 0,0 Z`; radial gradient centre (0,-100) r 110 focus (-22,-132): `#F6A98C` .07, colour .45, colour x0.62 at 1; dot r 22 at (0,-112) `#FFF8F2`; highlight ellipse (-24,-140) 16x8 at -34°, white .55. Lift raises 40, scale x(1 + 0.08 lift); shadow offset (8 + 0.8 up, 12 + up), opacity 0.36 - 0.15 lift, blur std (7 + 14 lift)/2. Disc magnet (r 46, inferred): focus (32%, 28%), colour x1.35 -> colour .5 -> x0.62, shadow 0.34 - 0.15 lift.
- Board edge (only z < 0.5): frame 70 wide, rx 30; inner edge 4 `#7D828A`; tray from 0.08 BW, 0.84 BW wide, top BH + 50, 130 tall, rx 18; wall shadow `rgba(60,50,40,.18)` offset (40, 90), size (BW + 60, BH + 140), blur std max(2, 60z)/z/2.

## Technique library

Capabilities, not obligations; the content decides.

- **Marker stroke.** One `<path class="wb-stroke">` per stroke in board units. Points resampled every max(3, 0.6w), Chaikin-smoothed (lines 2 passes, curves 3, text 1), then each point at arc length s moves along the normal by `off = J*2.6*((vn(s/wav + seed*7.31) - .5)*2 + .45*(vn(s/(wav*.23) + seed*3.1) - .5))`, with `hash = n => frac(sin(n*127.1 + 311.7)*43758.5453123)`, `vn` = smoothstep value noise over `hash`, J 1 for shapes, .35 for text (wav 90), .5 dash, .6 hatch, wav 220 default. Draw by dashoffset with `hand`; pool dot appears at t0. Close-up titles: build the tapered ribbon outline per frame in `onUpdate`.
  ```js
  const L = path.getTotalLength();                       // measure once before building
  gsap.set(path, {strokeDasharray: `${L} ${L + 2*w}`});
  tl.fromTo(path, {strokeDashoffset: L + w}, {strokeDashoffset: 0, duration: t1 - t0, ease: hand}, t0);
  ```
- **Shapes** (strokes in writing order). Line: one stroke through a midpoint pushed along the normal by 0.012 x L x (hash - 0.5) x 2. Arc: max(8, ceil(|a1 - a0| x r / 10)) points, wavelength 300. Circle: starts at -2.2 rad (top-left), clockwise, sweeps 2π + 0.22 rad, never closes exactly; optional ry. Rect: four strokes clockwise, each 6 past the corner. Round rect: one stroke, 8 points per corner, ends 14 past its start, wavelength 400. Arrow: shaft (straight or through one control point), then two head strokes of 34 at ±2.6 rad from the direction measured 30 before the end (into the tip, then out). Dashed: 40 / 26, one stroke per dash, sampled every 6. Hatch: one zigzag stroke at -0.8 rad, spacing 26, ends inset 0.2 x spacing, width 6, opacity 0.55. Underline: overshoot 12-14 with a small hook (4 extra points turning up to ±0.25 rad).
- **Handwriting.** Each glyph an inline-block `<span class="wb-g">` on a baseline inside `#wb-world`; jitter set once with `gsap.set`. Reveal: per-glyph left-to-right `clip-path` wipe with `hand`, pen tip on the wipe edge (y on a seeded sine between baseline and cap height). Marker look: `-webkit-text-stroke: .065em` + `paint-order: stroke fill` (blunt ends), opacity 0.94, filter `#wb-wobble` = `<feTurbulence type="fractalNoise" baseFrequency="0.022" numOctaves="2" seed="3"/><feDisplacementMap in="SourceGraphic" scale="4.5" xChannelSelector="R" yChannelSelector="G"/>`. Title variant: per-glyph SVG `<text>`, dasharray 6 x font size drawn in the first 60% of its slot, then fill 0 -> 0.94. CJK three-row sweep: mask of three bands (0.98 x size / 3 each, top at 0.86 x size above baseline, brush 1.45 x band), top to bottom, each left to right.
  ```js
  function write(glyphs, by, lead = 0.6) {               // lead 0.3-0.8 s per word, ~3 s per title
    const adv = glyphs.map(g => g.offsetWidth), sum = adv.reduce((a, b) => a + b, 0), n = glyphs.length;
    const gap = n > 1 ? Math.min(0.035, lead * 0.35 / (n - 1)) : 0, avail = lead - gap * (n - 1);
    let t = by - lead;
    glyphs.forEach((g, i) => { const d = avail * adv[i] / sum;
      tl.fromTo(g, {clipPath: "inset(-20% 100% -20% -10%)"}, {clipPath: "inset(-20% -10% -20% -10%)", duration: d, ease: hand}, t);
      t += d + gap; });
  }
  ```
- **Floating pens and one clock.** At most three markers. Pose = pure function of t from the pen's schedule: on a stroke, tip at the drawn length; gap < 0.75 s, hop; longer, fly to the park point (1920x1080 screen coordinates scaled to the frame; default (2250, 1300); stagger, e.g. (2200, 1320), (2100, 1420), (2350, 1150)) over 0.38 s and back 0.42 s before the next stroke; else hidden. One clock tween drives camera, pens, gloss, dry-mask opacity, frame visibility, magnets and eraser: `const clock = {t: 0}; tl.fromTo(clock, {t: 0}, {t: DUR, duration: DUR, ease: "none", onUpdate: () => render(clock.t)}, 0);`. Never stack camera tweens on one property.
- **Magnets.** The only moving things, for the hero ("you are here", a cart, a cell); accent, one per shot; unique ids per magnet.
- **Eraser rewind.** Erasing a trace backwards = time running back; undo a joke; replace a wrong answer. Ink mask: white rect + eraser path stroked black, width 150, stroke-opacity 0.9, round caps, dashoffset linear. Hits only ink drawn before the erase (later strokes sit outside the mask); 10% remains. Clear a box with a 5-pass zigzag. Prop flies in/out 0.45 s (offset +500 x, +700 y, squared).
- **Duet:** two quantities compared live, each pen on its own beat. **Relevant ghosts:** for an old theory, last year's forecast or a first draft, draw it at 5-10% first, then write over it. **Circle the answer:** two accent ellipses, start angles offset (-2.2 and -1.6 rad). **Full-board view:** zoom 0.3-0.4 past the edge for summary or ending.
- **Openings:** a huge question mark that shrinks into a dot of the first drawing; an erased board still ghosting a wrong answer; a magnet clacking onto an empty board. **Endings:** circle the answer twice and the pen stops; erase all but one line; push into the smallest symbol until it becomes the title.
- **One board across several scene files.** Every scene file repeats the same board: same board-coordinate layout, ghost seed (99) and area; each stroke seeded by a stable id (hash of its name), not call order, so wobble matches across files. A scene renders strokes and erasures finished earlier as complete from frame 0, then schedules its own in local time. Its first camera key equals the previous scene's last key (x, y, z, roll), pens start parked, so the frame at the cut is identical; join with `cut`.
  ```js
  const DONE = ["s01-title", "s01-orbit", "s02-arrow"];   // drawn in earlier scenes: render finished at t = 0
  const CAM = [[0, 3050, 950, .95] /* = last key of S02 */, [1.2, 3050, 950, .95], [2.0, 5200, 1400, 1, 0, "io"]];
  ```

## Transitions

Between scene files: `cut` only, matched composition (incoming first frame = outgoing last frame). Never `fade`: it doubles the board.
- Far region: an in-scene whip (0.5-0.8 s, `io` or `power2.out`, after a full stop).
- Whip across a cut: the outgoing scene's last 0.25-0.4 s accelerates (`power2.in`) onto blank board; the incoming scene starts on the same blank stretch at the same speed and decelerates (`power2.out`) into the new region.
- A different board (rare): pan line, 15 frames = 0.5 s at 30 fps, both boards opaque sliding left, `power2.inOut` = cubic-bezier(0.65,0,0.35,1), in one scene; as a scene opening, board B slides in from x = 100% (entrance echo), no key writing in the first 0.4 s.
  ```js
  tl.fromTo("#boardA", {xPercent: 0}, {xPercent: -100, duration: .5, ease: "power2.inOut"}, tSwap)
    .fromTo("#boardB", {xPercent: 100}, {xPercent: 0, duration: .5, ease: "power2.inOut"}, tSwap);
  ```
No wipes, dissolves or dips. Scene code never fades its whole frame.

## Captions

A persistent cream rounded label, dark text, an accent marker dash at its bottom-left; only the text swaps. Width 72% W (portrait 88%), padding 0 60u, height 1.75 x size + 24u (two lines 2.6 x size + 24u). Too long: shrink to 34u, then two lines; never truncate. Dash: SVG 56u x 14u at left 26u, bottom 16u, path `M3,8 C14,5.5 30,9 53,6.5`, stroke 6, round, accent, 0.92. Each sentence stays at least max(1.8 s, spoken + 0.6 s); the label leaves 0.6 s after the last sentence.

```css
--cap-bg: rgba(250,249,245,.9); --cap-fg: #23262C; --cap-font: <sans stack>; --cap-size: calc(44 * var(--u));
--cap-radius: calc(22 * var(--u)); --cap-bottom: calc(32 * var(--u));   /* 3% H; portrait: 58u */
--cap-weight: 500; --cap-accent: #D97757; --cap-shadow: 0 calc(4*var(--u)) calc(14*var(--u)) rgba(40,40,50,.12);
```

## Image-gen guidance

Only when image generation is on; a generated image never appears on screen as an image.
- Anchors (start every prompt): "hand-drawn dry-erase marker line art on a glossy whiteboard", "single-weight marker strokes, no fills, no shading", "limited marker palette: charcoal #23262C, blue #2A5CB3, one orange #D97757 accent", "warm white board gradient #FBFBF9 to #EEEDE9, soft diffuse window light", "slightly wobbly overshooting lines, open unclosed circles, dry-brush ink grain", "faint ghosted erased marks in the background, no photo, no hands"; end with "no text, no watermark"; add "isolated on plain white background" for anything to trace.
- Subjects: outline sketches of complex objects (people, animals, organs, machine parts) as tracing references only; scuff and faint wall textures; ghost-leftover sketches.
- Never generate: text, formulas, digits, units; charts, axes, rulers, geometry, arrows, dashes; props, frame, tray; colour blocks, cards, icons, photo-like images.
- Post-treatment: trace into single-line paths, apply wobble, overshoot and open ends, recolour to the ink set, let the marker write it. Textures: neutral grey, 2-3%, fixed to the board.

## Taboos

- No hands, arms, cursors; no colour blocks, cards, icon stickers, photos; no glow, lens flare, vignette, depth-of-field blur.
- No fades, wipes, dissolves, in-board hard cuts; no text fading, flying or popping in as a block.
- Never drop the marker cues; ghosts never above 10%.
- More than three pens, changing colour meanings, two accents in one region.
- Anything in the bottom 12%; half-cut labels; a magnet on text to be read; an eraser path across a kept label; an overloaded pen.

## Known gaps

- No stroke-order glyphs: wiped or outline-drawn system handwriting reads as a font up close (the original had this limit for CJK).
- Plain dashoffset strokes have uniform width (taper needs the ribbon build).
- Scuff and dry-speckle textures are procedural approximations with inferred parameters.
- No camera motion blur (the original's legacy 180° shutter above about 14 px per frame); fast whips can look steppy.
- Circle overlap follows the constant 0.22 rad past a full turn; the original's prose says "about 22%".
- Cross-file continuity relies on each file repeating layout and seeds; the cross-scene pan line exists only in-scene.
- No sound in the style. Portrait, disc magnet, roll limit, ghosts per screen, whip duration are inferred.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
