# Style: dataviz (Data Story)

Units: `u` = frame short side / 1080. At 1920x1080, 1u = 1px. In CSS write `calc(N * var(--u))` (see `dataviz.css`). In GSAP use `const u = MV.u;`. This style also uses **world units** (w), the coordinates of the one paper sheet: 1w = 1080p px at zoom 1, on screen 1w = zoom x u px, visible world width = W / (zoom x u). Chart geometry is in w; screen-layer items (frozen labels, pencil, captions) in u.

## Identity

The chart is the film. One cream sheet, one continuous camera, one red/blue pencil that draws real data point by point into a story. Every mark is a real number; every action is a chart operation (drop a dot, grow an axis, rescale, morph, pin a note). Handwritten notes give human scale; each dot lands on a beat. One thing at a time: not a dashboard, not a slide. Identity rules, all mandatory: cream paper with the 24w dot grid always on (it is how the eye reads camera motion); colour encodes value only, through a diverging ramp centred on a meaningful reference mean with a symmetric half-span; one sheet and one continuous 2D camera, no fades or dissolves; notes pinned in data coordinates and written by hand, never flat print type; series lines are clean ink, everything annotated is pencil.

Best for: data journalism, report and KPI readouts, cost/usage/latency trends, benchmarks, science data. Not for: launches (use dark-keynote), arguments without data (use exec-brief or whiteboard), many-chart dashboards.

## Palette

Paper and warm ink; colour only means value. Lead colour means role: blue = memory, human notes; red = record, warning, unknown, empty cell. Switching lead (the pencil flips to red) is the turning point.

| Hex | Role |
|---|---|
| `#F6F3EC` | paper, whole film and backdrop |
| `#E4DDCF` | dot grid |
| `#2B2723` | ink: axes, series line, title, captions, dot rings |
| `#6E665C` | soft ink: ticks, sublines, values, dashed drop line |
| `#DDD6C9` | grid lines, frozen-strip rule |
| `#BDB4A5` | zero line |
| `#2F5D8A` / `#A8283A` | blue lead / red lead |
| `#3A3733` | graphite (default pencil stroke) |
| `#E3C79C` / `#C9A774` | sharpened wood / darker wood |
| `#274E75` / `#8E2232` | blue / red lead tip |
| `rgb(70,50,30)` | pencil shadow |
| `#D97757` | the one accent, non-data marks only (sparkle, logo, highlight) |

Diverging ramp (RdBu family), `u = (v - center) / half`, linear RGB between stops, clamped: `-1 #2166AC`, `-0.72 #4393C3`, `-0.45 #92C5DE`, `-0.2 #D1E5F0`, `0 #F2EEE8` (reference mean), `0.2 #FDDBC7`, `0.45 #F4A582`, `0.72 #D6604D`, `1 #B2182B`, `1.4 #7A0F1E` (beyond the half-span keeps darkening). Sequential ramp (no natural centre), u = (v - lo)/(hi - lo): `0 #F2EEE8`, `0.25 #FDDBC7`, `0.5 #F4A582`, `0.75 #D6604D`, `1 #B2182B`. Reference example: centre 0.22 (a 1971-2000 mean), half 0.85.

Honesty over drama: half-span = largest distance of any value from the centre, both sides, so middle values stay pale; never shrink it. Accent alternatives: none (brand colour allowed, non-data only).

## Type

- Sans (title 700, subline and captions 400): `-apple-system, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", "MV Noto Sans SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`.
- Mono (ticks, values, kicker), 400, tabular numerals: `"SF Mono", Menlo, "MV Noto Sans Mono", "PingFang SC", "Hiragino Sans GB", "MV Noto Sans SC", monospace`.
- Handwriting (notes), 500: `"Chalkboard SE", "Bradley Hand", "Marker Felt", "MV Patrick Hand", "Hannotate SC", "HanziPen SC", "Kaiti SC", "MV LXGW WenKai", cursive`, always with glyph jitter and grain.

| Role | Size | Details |
|---|---|---|
| chart title (on paper, above plot) | 66w (6.1% H) | sans 700, typeset glyph by glyph, each rising 10w; fades when the camera pushes in, returns on the wide |
| subline | 17w | sans 400 soft ink: unit, basis, or "one dot per X" |
| note | 30w (2.8% H) | glyph rotation +-1.7 deg, baseline +-2.5%, skewX(-3 deg), advance 0.96 x glyph width, line height 1.08 |
| ticks, kicker | 17-20w; y ticks 18u on screen | mono soft ink; first/last year in ink |
| value beside a dot | 15w x 1.25/sqrt(zoom) | mono soft ink, offset 11 + 9 x 1.25/sqrt(zoom) right |
| zero-line note ("0 = 1951-80 mean") | 15u | mono soft ink on a paper chip |
| caption | 42u (3.9% H), min 34u | sans 400 ink, max 2 lines, line height 1.22 |

Never auto-wrap: one `<text>` per glyph, positions from widths measured once after `document.fonts.ready`.

## Layout grid

- Chart picker by relation: compare categories = bars, ranking race; two quantities = scatter, bubbles (a dot can be a character); place = map, dot map; part of whole, head counts = waffle, unit chart; before/after = slope, dumbbell; distribution = histogram, beeswarm; where things go = flow; over time = dot-line series, stripes, area.
- World reference (landscape): plot box 1500 x 520w (top -260, bottom 260), 15w per year; dot r 6 + 1.5 ink ring; series 2.8. Sparse series (a dozen points >= 80w apart): r 8-10, line 3, ring 2.
- Key moments: subject >= 1/3 frame height; chart in the top 80%, bottom 15% is the caption zone. Memory zone: early notes go where later data will not land.
- Notes pinned in data coordinates: anchor = data point + offset, from the same `dot(i, t)` that places dots, line and pencil, so a rescale moves everything together. Right-aligned notes still write left to right; the pen hops to the first glyph.
- Frozen pane: when following scrolls the y axis off screen, y ticks stick to a paper strip at the left edge.
- Portrait (inferred): plot ~850 x 660w, 8.5w/year, dot r 4.5, line 2; box at 25-75% H, title above; ticks, subline, kicker x1.3; notes on two lines; pencil angle ~1.0.

## Camera grammar

A vocabulary, not a route. The camera is one 2D pose `{x, y, zoom, roll, sx, sy}`: x/y = world point at frame centre, roll in radians (negative = left low, right high), sx/sy shake in u. `#camera` is an SVG `<g>` holding the world; every frame set `transform="translate(W/2+sx*u, H/2+sy*u) rotate(rollDeg) scale(zoom*u) translate(-x,-y)"`. Keys ease `power2.inOut`, zoom in log space: `zoom = Math.exp(lerp(Math.log(z0), Math.log(z1), e))`. Key frames 1.5-2.5, whole series ~1.0, extreme close ~4. Each move 2-5 s.

| Shot | Expresses | How / when |
|---|---|---|
| extreme close on one dot (~4), tilt up the leader | one fact, one person | single case, named person |
| wide, then dive into one dot | pattern, then case | familiar trend, myth to correct |
| pull out, axis grows from the dot | unit first, frame after | naming the measure, widening the claim |
| follow the pen | past left, future right | x = penX - 190/zoom (newest dot at ~60% W); y = mean of last 7 dots + 45-90w; penX averaged over the last 0.7 s (8 samples) |
| rise with data, roll 0 -> -1.5 deg (smoothstep) | escalation, unease | runaway growth, debt |
| 2-frame push + decaying shake | surprise, frame broken | outlier, crash |
| fast pull to 1.0, roll -3 deg, constant 2u shake | sprint | boom, chain reaction, panic |
| locked | the change is the motion | morph, slow reading |
| hold on a dot, pull out | part of a whole | conclusion |
| pull out and pan as data fills in | scale switch | longer record |
| pan across memory-zone notes | remembering | biography, timeline |

Shake: `sx = (vnoise(22t + 3) - 0.5)*2*amp`, `sy = (vnoise(22t + 43) - 0.5)*2*amp`. Depth of field only on the pencil; one foreground defocus per film at most (blur 11u -> 0 while focusing on the freshly landed tip, ~0.5 s). Never 3D.

## Motion

The rhythm of the data is the rhythm of the film. Pick a BPM whose finest note lands on whole frames: 90 BPM at 30 fps, beat B = 0.667 s = 20 frames, 8th 10, 16th 5, 32nd 2.5 -> "one dot every 2 frames". Accelerate by subdividing, never by changing tempo: quarter -> 8th -> 16th -> every 2 frames (reference cadence 7 / 24 / 40 / 27 dots). A real ~1 s stillness before key data.

| Action | Timing | Ease / formula |
|---|---|---|
| dot pop | 0.25 s | scale = 1 + 0.55 sin(pi p)(1 - 0.4p), p = fresh/0.25 |
| dot ripple | 0.6 s, linear | ring r(1.2 + 2.4k x m), opacity 0.55(1-k), 1.2w, dot colour; m = 1 (3.6r), key first/last dots 2.2; none if dots < 0.1 s apart |
| pencil line dot to dot | min(0.45 s, gap x 0.8) | linear; lift 0.03, from 70% `0.22 sin(((u-0.7)/0.3) pi)` then tap |
| note (+ doodle) | 1.3-2 s (+ 0.3-0.5 s) | ring 18% -> leader 12% -> writing 70%; right-aligned: hop to first glyph = first 16% of writing (arc 12w) |
| axis growth | dashed drop 0.6 beat -> x axis 1 beat -> grid L to R 1.6 beat -> y ticks fade in | `power2.out` |
| tense rescale (ratchet) | 4 steps x 0.09 beat (0.06 s) | each `power2.out`, steps evenly spaced (reference window 0.33 s) |
| calm rescale | 1.5 beat | `power2.inOut` |
| frame break | zoom 1.22 -> 1.42 in 2 frames, then -> 1.3 over 0.8 s | `power2.out`, `power2.inOut`; shake 12e^(-6 dt) u; 4u shake (decay 9) at the rescale; hold broken >= 1/2 beat (>= 10 frames) |
| sprint shake | while dots arrive every 2 frames | constant 1.2u |
| morph | sweep L to R over 0.85 beat, 0.3 s per mark | fall to band centre in first 45% (smoothstep); from 30% stretch to full band (`power2.out`), ring fades, radius r -> 0 |
| scale switch | older stripes right to left, 0.12 s each | `power2.out` |
| title glyphs | k = clamp(p(n+4) - i, 0, 4)/4 | opacity k, rise 10w(1-k), linear |
| pencil flip / startle | 0.5 s end over end / recoil 0.12 s, freeze 1/2 beat | `power2.inOut` / `power2.out` |

GSAP: easeInOutCubic = `power2.inOut` = cubic-bezier(0.65,0,0.35,1); easeOutCubic = `power2.out` = cubic-bezier(0.33,1,0.68,1); smoothstep = `p => p*p*(3-2*p)`; dot pop on `scale` 1 -> 2 with `ease: p => 0.55*Math.sin(Math.PI*p)*(1-0.4*p)`; ratchet `v = sum_i easeOut((t - t0 - i(t1-t0)/4)/0.06)/4`.

Pencil acting: hover before landing; shadow meets tip on contact, small rebound; denser data = hastier pen, steeper body (0.62 -> ~1.0 rad); seismograph tremble at a peak (tip += `2.5 sin(97t), 3.5 sin(131t)` w); exit the frame to hand the story over. Between acts: gap <= 1.6 s = hop on an arc (`power2.inOut`, lift = max(lerp(a,b,u), sin(pi u) min(0.5, 0.9 gap))); longer = leave to screen (W+230u, H+240u) in 0.9 s at lift 0.8, return in the last 0.9 s. Writing lift 0.04 + 0.05|sin(31t)|.

One master clock and a pure `draw(t)` per scene (tween onUpdate order is not guaranteed under seek):
```js
const tl = gsap.timeline({paused: true}), clock = {t: 0}, E = gsap.parseEase("power2.inOut");
const camAt = t => keyed(t, CAM_KEYS, E);      // {x, y, zoom, roll, sx, sy}, zoom via log
tl.fromTo(clock, {t: 0}, {t: DUR, duration: DUR, ease: "none", onUpdate: () => draw(clock.t)}, 0);
window.__timelines["S03"] = tl;                // register last
```

## Materials and light

- Paper `#F6F3EC`, bright, flat, no vignette; faint clouds (+-2 levels) and fibres fixed to the sheet: one rect over the view inside `#camera` (world-unit turbulence moves with it), filter `feTurbulence fractalNoise baseFrequency 0.006 octaves 3 seed 4` -> `feColorMatrix` RGB `rgb(70,50,30)`, alpha `0.05R - 0.015` (0-2%); plus `feTurbulence fractalNoise baseFrequency "0.9 0.035" octaves 2 seed 9` -> RGB `rgb(120,100,70)`, alpha `0.3R - 0.18` (fibres up to ~6%); `feMerge` both (port values, tune by eye).
- Dot grid: 24w step, square dots `#E4DDCF` at 0.9, centred on multiples of 24w, side 3.3/sqrt(zoom) w (reference text: ~2.9/sqrt(zoom)). `<pattern patternUnits="userSpaceOnUse" x="-12" y="-12" width="24" height="24">` inside `#camera`; resize its rect each frame. Never off.
- Ink (w): grid 1 `#DDD6C9` (opacity clamp((y - top + 40)/40) near the top); zero 1.3 `#BDB4A5`; axis 1.5 ink (1.4-1.6); dashed drop 1, `3 5`, soft ink 0.8; series 2.8 ink, round caps. X tick 7 long 1.2, label 30 below. Ceiling 1.4; a break leaves a +-11w gap with barbs (gx+-11, y) -> (gx+-7, y-6) and (gx+-14, y-5), width 1; the scar stays.
- Colour blocks (bars, areas, map tiles): flat value colour (opacity clamp(1.5p)), 45 deg graphite hatch every 7w, `rgb(40,30,20)` at 0.12 x hatch x (0.5 + 0.5 hash), width 1; pencil outline 1.6, two passes, wobble 0.35.
- Graphite grain filter `#dv-grain` (every pencil stroke and note; region x -10% y -20% w 120% h 140%, sRGB), inside `#camera`, per frame `GF = 0.9 zoom u`: `feTurbulence fractalNoise GF, 1 octave, seed 11` = n -> `feColorMatrix` alpha `7R - 2.55` = m (knocks out ~16% of pixels); `feTurbulence fractalNoise 0.1 GF, 2 octaves, seed 5` -> alpha `2.2R - 0.35` = m2 (uneven pressure); `feComposite arithmetic k1=1` (m x m2) = mm; `feDisplacementMap` SourceGraphic by n, scale `1.6/zoom`, channels R/G; `feComposite operator="in"` mm.
- Light: one soft light upper left, seen only in the pencil shadow, lower right: `sh = (3 + 70 lift s) u`, offset (0.8 sh, 1.1 sh); blur `sb = (2 + 16 lift + 0.5 blur) u` (stdDeviation sb/2); opacity 0.2 - 0.08 lift; `rgb(70,50,30)`.
- Pencil (screen layer, local units, tip at origin, body along +x): lead `M0 0 L16 -3.6 L16 3.6` (`#274E75`/`#8E2232`); wood cone (14.4, +-3.2) -> (74, +-12), scalloped back edge (x 74/79 alternating, 7 points), `#E3C79C` + lower shade `rgba(150,110,60,.25)`; hexagonal body from x 72, 24 wide, 1400 long, half lead colour, half the other; highlight `rgba(255,255,255,.18)` 5 tall at y -9, dark edge `rgba(0,0,0,.16)` 6 tall at y 6. Place: `translate(tip) rotate(ang) scale(s*u*(1 + 0.35 lift)) translate(180(1-as), 0)`, body `scale(as, 1)`; `s = 0.72 zoom^0.6`, lift 0-1.2, ang 0.62 rad. Flip: `a = cos(flip pi)`, `as = max(0.04, |a|)`, red end when a < 0. Depth of field: `b = (blur + 2.5 lift) u`; if b > 0.3 or focus < 1, draw the body blurred (stdDeviation (b + 4u)/2) plus a sharp copy masked by a gradient from the tip (opacity focus) to 260 s u along the body (0).

## Technique library

Inline helpers: `hash(n) = fract(sin(127.1n + 311.7) * 43758.5453)`; `vnoise(x)` = smoothstep blend of hash(floor x), hash(floor x + 1); mulberry32 for jitter.

- **Graphite stroke** (notes, rings, leaders, doodles, outlines). Resample every 2.5w; offset along the normal by `(vnoise(0.02 len + 13.1 seed) - 0.5)*2*0.6*width`; chunks of 5 segments, chunk opacity `0.92(1 - 0.3 hash(1.7c + 31 seed + 7 pass))`, width `width(0.75 + 0.25 tp)`, taper `tp = min(1, 8f, 8(1-f) + 0.35)`; pass 2 offset `(hash(seed+1) - 0.5) 0.5 width` in x and y, width x0.6, opacity x0.45; round caps; grain filter. Write by revealing chunks; the tip is the wobbled point at `end`:
  ```js
  chunks.forEach(c => { const v = clamp(end - c.l0, 0, c.L);
    c.el.style.strokeDasharray = c.L; c.el.style.strokeDashoffset = c.L - v; c.el.style.visibility = v > 0 ? "visible" : "hidden"; });
  ```
- **Handwritten note.** Per glyph `transform="translate(x, base+dy) rotate(rot) skewX(-3)"`, rot = 3.4(R()-0.5), dy = 0.05 size (R()-0.5), R = mulberry32(97 len + size + seed); revealed by a clip rect x `left - 0.3 size`, y `base - 1.25 size`, h `1.7 size`, w `0.3 size + tw p`. Tip: `[left + f, base - size(0.28 + 0.22(0.5 sin 0.55f + 0.3 sin(1.31f + 1)))]`, f = p tw (bounces on the x-height).
- **Pinned note.** Unclosed ring: 48 segments, 1.12 turns from -2.2 rad, `rr = r(1 + 0.08 sin(5t + seed) + 0.06t)`, x x1.08, r = 2.3 x dot r, width 1.6, one pass. Leader: quadratic, bend 0.22, from `dot + (1.1r cos a, r sin a)` to the text corner (right-aligned `[at.x+8, at.y-0.32 size]`; text above `[left-2, lastBase+0.12 size]`; below `[left-4, at.y-0.78 size]`), width 1.4, wobble 0.4. Colour arc: blue memories early, red records late. Doodles (width 1.7): three-roll wave 66w wide, 14w after the text; hollow heart 26w after, scale 0.55; under-bracket h 14.
- **Data dot / axis.** Value fill + 1.5w ink ring (pale values stay visible); non-data emphasis takes the accent. The axis grows when the unit is first spoken.
- **Frozen labels.** Screen layer; when the axis screen x < 150u, fade in (over 60u) a paper strip 118u wide at 0.96, a 57u paper-to-clear gradient, a 1u `#DDD6C9` rule at 124.5u. Tick right edge = max(112u, axisX - 10u), 18u, on a 66 x 22u paper chip; hidden above 20u or below H - 200u.
- **Break the frame.** Torn ceiling at the outlier, 2-frame push, shake, startle, flip to red, hold >= 1/2 beat, ratchet rescale with 4u shake. Viral peaks, bubbles, record floods.
  ```js
  const amp = (t < T_B ? 0 : 12*Math.exp(-6*(t - T_B))) + (t < T_R ? 0 : 4*Math.exp(-9*(t - T_R)));
  ```
- **Encoding morph.** Bars to waffle, map to ranking, line to stripes; camera locked. Series line fades as the dot to its left morphs; notes fade during the sweep, return as small marks above the band. Stripes: one band per value (+0.6w overlap), clipped to any shape, pencil outline 1.4 one pass.
- **Scale switch.** One value -> a series -> the longer real record. Never extrapolate.
- **Empty next cell.** Unknown next value: red dashed cell (dash 9, gap 7, width 1.6, opacity 0.85(1 - 0.4 hash)) drawn along its perimeter + red handwritten question; can become the end card by diving into it.
- **Any shape.** Colour blocks, dot-line series along any path, stripe fill, comet tail (r 1.5 -> 6, alpha 0.25 -> 1, ring 1.2, no ripple), accent four-point sparkle (astroid, k 0.14).
- **Openings / endings.** Open on the finished chart, the measured object (a ticket becomes the first dot), or a question (empty chart, unit, pen hovering). End on one dot and its note, the object, or the pencil striking a title word and writing the true one.
- **One sheet across scene files.** Copy one WORLD block verbatim into every scene: data, `X(i)`, `Y(v, vmax)`, box, ramp centre/half, note specs, and a sheet map (each chart's world origin, e.g. chart A x 0-1500, chart B x 2400+), mirrored in design.md. Each scene declares `CAM_IN` and `CAM_OUT`; S(n+1) `CAM_IN` equals S(n) `CAM_OUT` exactly (x, y, zoom, roll; shake 0 at both edges). Rebuild inherited state at t = 0: landed dots settled (no pop), axes and grid complete, finished notes written, current vmax, pencil at the same tip/lift/flip or off stage. S(n) ends at rest (last 0.3 s settled); S(n+1) starts moving; `cut` makes the join invisible.
- **Lighter port (no pencil).** Keep paper, dots, ink, ramp, camera, chart operations, frozen labels, frame break; drop the pencil body, shadow, flip and choreography. Each stroke becomes one wobbled path revealed by `strokeDashoffset` (grain optional); notes reveal by clip rect; dots pop on the beat. All timings hold.

## Transitions

Generic transitions are not part of this style. Between scene files: `cut` with matched composition (one sheet, above); never `fade`. One non-matching hard cut per film at most, at its most meaningful point. Real transitions are chart operations in-scene: grow an axis, pull out, rescale, morph, a cell expands into the next frame. If the next chart lies elsewhere on the sheet, the incoming scene opens on the previous `CAM_OUT` and pans there in 15 frames (0.5 s), `power2.inOut`, the sheet moving as one opaque surface:
```js
const CAM_KEYS = [[0, CAM_IN], [0.5, CAM_CHART_B], /* ... */];   // CAM_IN = previous scene's CAM_OUT
```
No dissolves, page turns, black frames or self-fades.

## Captions

The caption is the chart's legend: ink sans on a paper band, left-aligned at 9.4% W (portrait 7%), baseline 91.2% H (portrait 86%), max width min(1380u, W - 2x), two balanced lines max, shrinking (min 34u), never truncating. Band: clear at 78.7% H to paper over 75u, then paper 0.94. Above the text a 1u ink rule at 0.9 and an 18u mono soft-ink kicker with the range drawn so far ("1926-1958"); these persist, only text changes. Glyphs appear on their timestamps (0.125 s, 6u rise); a sentence holds >= max(1.8 s, spoken + 0.6 s); all gone 0.6 s after the last. Frame breaks and morphs happen in silence, caption empty. If the runtime draws only text, the scene draws band, rule and kicker (`.dv-capband`, `.dv-capkicker`).

```css
--cap-bg: rgba(246,243,236,.94); --cap-fg: #2B2723; --cap-font: <sans stack>; --cap-size: calc(42 * var(--u));
--cap-radius: 0; --cap-bottom: calc(86 * var(--u));   /* portrait: 260u */
--cap-weight: 400; --cap-accent: #2B2723; --cap-shadow: none; --cap-border: 1px solid rgba(43,39,35,.9);  /* top edge */
```

## Image-gen guidance

Only when image generation is on.
- Anchors: "hand-drawn graphite pencil sketch on warm cream paper (#F6F3EC) with faint fibers", "monochrome warm dark ink (#2B2723) linework with blue (#2F5D8A) and red (#A8283A) colored-pencil accents", "flat 2D editorial data-journalism illustration, top-down view", "tapered wobbly pencil strokes, broken graphite grain, light diagonal hatching", "soft single light from upper left, bright flat paper, no vignette, no glow", "minimal, sparse, lots of empty paper"; end with "no text, no watermark" (+ "isolated on plain white background" for cut-outs).
- Subjects: the measured object for the opening (ticket, shoe, pill bottle); small portraits beside a note; landmarks for a map dot; topic props in the memory zone (wheat field, glacier, shopfront).
- Never generate: chart marks, text or digits, axes, grids, dashed cells, the pencil, any value colour.
- Post-treatment: cut out, monochrome graphite or blue-pencil line art, ink plus at most one lead colour, grain; `mix-blend-mode: multiply` on the paper; no frame, shadow or glow; <= 1/3 frame height, beside a data point, circled by the pencil.

## Taboos

- No fades, dissolves, page turns, black frames; no glow, lens flare, light bands, vignette; no 3D or WebGL.
- No side-by-side dashboard charts, no decoration; dot grid never off; one foreground defocus at most; pencil never covers the newest dot.
- No colour except value; accent non-data only; never shrink the half-span, change numbers or extrapolate; check every spoken number.
- Series lines never pencil; notes never clean ink or print type; no rescale within 1/2 beat of a break.

## Known gaps

- Fonts: the reference used a dedicated handwriting face for notes and a serif for titles/captions. System handwriting fonts plus jitter and grain approximate it; cursive joins and pressure are lost. A serif 500 title is a valid closer-to-source option.
- Paper: the reference used a painted 512 px tile; the turbulence paper is an approximation tuned by eye.
- Sonification: the reference gave each dot a pitch (higher value, higher note). The SFX budget (2 per scene, 8 per film) rules it out. Optional: a soft tick sfx on the frame break and the flip to red, or a pitch line baked into the music bed.
- Per-frame SVG filters render slowly in dense scenes (the lighter port avoids most).
- Portrait layout and CJK note sizes are inferred.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
