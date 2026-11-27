# Style: exec-dark (Executive Brief, Night Office)

Units: `u` = frame short side / 1080. At 1920x1080, 1u = 1px. In CSS write `calc(N * var(--u))` (see `exec-dark.css`). In GSAP use `const u = MV.u;` and tween `y: 30 * u`. Every size below is in u.

## Identity

A management talk lit by screen light in a night office: near-black, warm white type, one colour, amber. Each content scene lies on a darkened office photo that drifts slowly; content sits on one side on glass cards; a Kicker top-left names chapter and section; a thin amber bar on the bottom edge shows how far the film has come. Statements rise from blur to sharp; numbers count up, dots fill cards, week cards light in turn; a chapter opens dimmed, its centred title shrinking into the Kicker. It must read as a carefully made internal review film, never a slide deck or launch event. Six identity rules, all mandatory: near-black + warm white + exactly one accent; a visible photo under every content scene, darkened by a directional mask, not a flat black layer; content on glass on the dark side; a Kicker in every content scene; the full-film progress bar; blur-to-sharp rise with no overshoot except the light pop.

Best for: leadership talks, internal comms, executive viewpoints, retros, methodology and team mechanisms (weekly reports, OKRs), quarterly reviews, strategy readouts; default for management scripts. Not for: hardware showcases (3D product style), software UI demos (dark-keynote), white-paper look (exec-brief), playful or handmade topics.

## Palette

Red and green are not accents.

| Hex | Role |
|---|---|
| `#0E1116` | stage base; radial centre `#1B212C`, corners `#0A0C10` |
| `#0B0D11` | chapter dim and chapter dip |
| `#F2EFE9` | main text (warm white), captions |
| `#D8D3CA` | subtitles, notes, stat labels |
| `#9AA1AD` | Kicker chapter name, card titles, labels, W1..W8 |
| `rgba(16,19,25,.58)` | glass face; border `1px rgba(255,255,255,.12)`; shadow `rgba(0,0,0,.35)` |
| `rgba(255,255,255,.22)` / `.08` | rules / faint rules, tracks |
| `#F5A524` | the one accent, default amber |
| `#E5484D` | risk: escalation edge, strike line, red annotation dots |
| `#30A46C` | resolved: green ticks, the opening green pill |

Accent alternatives (exactly one per film): gold `#D9A441`, coral `#FF7A59`, cyan `#5FD4FF`, or the brand colour.

Accent only for: Kicker number, big number (accent by default), key phrase + underline, chapter number, the one conclusion card edge, rings, task card, lit timeline, progress bar, opening/closing lines and brand mark. Two accent elements in a frame means one is wrong (number + its card edge count as one). Red only while the problem stands, then only in annotations. Green at most twice per film.

Photos are grey with amber warmth: brightness 0.42-0.62 (default 0.52; a person or clear subject 0.55-0.62; closing line 0.4), saturation 0.82, contrast 1.06. The mask, not a dimmed photo, darkens: about 90% on the content side, 20-25% on the photo side. Text is always brighter than the photo.

## Type

- Serif (title, brand, statements, golden lines, quotes), 600-700: `Georgia, "Songti SC", "MV Noto Serif SC", "Noto Serif CJK SC", "Source Han Serif SC", SimSun, "Times New Roman", serif`.
- Sans (everything else): `-apple-system, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", "MV Noto Sans SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`.

| Role | Size | Details |
|---|---|---|
| film title | 180 | serif 700, tracking 0.11em, per character |
| opening brand mark | 150 | serif 600, accent part 700, tracking 0.15em |
| chapter title | 88 | sans 300, tracking 12u; number 700 accent, margin-right 40u |
| big number | 124-150 | sans 800, tracking -0.02em, tabular; unit 47%, prefix 45% |
| statement / golden line | 56-74 (56) | serif 700, line-height 1.4, tracking 0.02em, max 14 CJK chars/line; "X = **Y**" headers sans 800 58u |
| card big text / body / title | 58-66 / 32-38 (36) / 30-34 (32) | sans 800 / line-height 1.6 / `#9AA1AD` (accent on the conclusion card) |
| note / subtitle | 36-38 (38) / 46 | sans `#D8D3CA`, line-height 1.6 / tracking 6u |
| quote | 40 | serif 600, line-height 1.5, nowrap, shrink past 18 chars; byline 26u `#9AA1AD`, 10u below |
| Kicker / W label / caption | 26 / 24 / 40 | tracking 4u / `#9AA1AD` / 500 |

One loudest level per screen: big-number cards hold one big line + dots; golden-line screens have no cards. CJK: `white-space: nowrap` per line, explicit breaks. Portrait (inferred): title 120u, chapter 64u, big number 110u; Kicker on two lines.

## Layout grid

- 12 columns, margin 96u (Kicker edge), gutter 32u; column c left = 96u + c*(colW + 32u), colW = (W - 544u) / 12. Content from 150u to 190u above the bottom; Kicker top 72u; progress bar 5u on the bottom edge.
- Content on one side only (about 1000u wide); the photo's subject shows on the other. Subject left: mask `right`, content from x = W*0.42. Subject right: mask `left`, content between 96u and W*0.5. Only centred full-frame content (title, chapter card, golden line) uses mask `center` / `warm`. The photo stays legible.
- One argument, 3-6 elements, subject 30-50% of frame height. Typical: big number + two glass cards (700u wide, 56u gap, 300u tall, at H*0.52); three column cards (470 / 560 / 470u, 40u gaps, 440u tall, at H*0.36); two role rows + one card; funnel + one big number.
- Data only as big number, dot matrix (10,000 dots), dot row (23 dots), notched meter (70%), week cards (30 days); keep approximations ("20+").
- Safe area 5%; sides >= 96u, top >= 150u, bottom >= 190u; big numbers and golden lines end >= 200u above the bottom.
- Portrait (inferred): margin 72u; cards stack; week cards 2x2; funnel vertical or 0.8x.

## Camera grammar

One main move per scene; all slow. Two movers keep every frame alive: the photo's Ken Burns (outside the camera) and a micro push of `#camera` (content; origin at the focus; `scale`, `x`, `y`). Layers back to front: stage, plate (+ tone, mask, vignette), `#camera`, Kicker, chapter card, progress bar, runtime captions. Single plane.

| Shot | Expresses | How |
|---|---|---|
| photo slow push | realism, breathing | plate 1.09 -> 1.0, x +1% -> -1% W (alternate sign, 1-2%), `ease:"none"` |
| micro push | frame always moves | `#camera` 1 -> 1.02 (to 1.04), log space, `ease:"none"` |
| blur-to-sharp rise + underline | argument sentence by sentence, stress | lines 0.3 s apart; underline 0.6 s after the phrase |
| count-up, dots | scale, quantity | count 1.6 s; matrix 2.2 s; dots every 0.06 s |
| slide-in list | division of work, checks | every 0.45 s, red / amber / green marks |
| four cards | plan, steps | cards 0.6 s apart, last one accent + glow |
| ring | control point missing -> embedded -> calendar | dashed ring with "?" -> snap 1.9 -> 1 -> a row lights |
| handoff, doors, meter, funnel | dumping, one/two-way door, 70% info, 10x filter | see Technique library |
| chat | opening situation | messages on cues; card greys when nobody answers |
| chapter / title / brand card | new part, opening, sign-off | dimmed title flies into the Kicker; per-character title; amber lines + brand |

No dutch angle, shake or snap zoom. Pan only inside the zoom margin: focus offset = (focus - centre) x (1 - 1/zoom). Constant perceived speed: tween proxy `z` from `Math.log(1)` to `Math.log(1.02)`, `ease:"none"`, `scale = Math.exp(z)` in `onUpdate`.

Scene lengths: opening/closing card 3 s; situation 4-8 s; statement 5-7 s; data / structure 8-15 s; closing line 5-7 s.

## Motion

Calm: fast start, long settle.

| Action | Duration | Ease | Notes |
|---|---|---|---|
| standard reveal | 0.85 (lines, cards, Kicker); 0.9 default; 0.75-0.9; stagger 0.3-0.5 | `power4.out` = cubic-bezier(0.22,1,0.36,1) | rise 30u, opacity 0 -> 1, `blur(8px)` -> `blur(0px)` |
| Kicker | 0.85 at 0.1 s (chapter scenes 1.3 s) | `power4.out` | rise 14u + blur |
| slide-in | 0.9, items 0.45 apart | `power4.out` | x -60u -> 0 + opacity |
| pop | 0.75 | custom (below) | scale 0.8 -> 1, peak about 1.04; opacity linear over 0.375 |
| rule grow / underline, strike | 0.9 / 0.6 | `power4.out` | scaleX from 0 (brand lines from centre) |
| count | 1.6 | `power4.out` | block rises 24u + blur over 0.6; "+" shows at 98% |
| dot matrix / dot | 2.2 / 0.35 each, 0.06 apart | `power4.out` | dots grow from centre |
| week card | first at t0 + 0.2, then 0.6 apart | `power4.out` | inner lines at card + 0.3 + k*0.2, rise 14u |
| ring row | 0.25 apart | pop | row rises 20u / 0.8 at t0 - 0.5 |
| per-character | 1.0 each, step 0.12 (title 0.14, brand 0.06 / 0.12) | `power4.out` | rise 40u, scale 0.9 -> 1, blur 10px -> 0 |
| settle / minimum move | 0.5 / 0.25 | `power4.out` | |

Other eases: `power2.inOut` = cubic-bezier(0.65,0,0.35,1) (chapter flight, dip), `power2.out`, `power2.in`. Never `back`, `elastic`, `bounce`.

```js
const pop = p => 1 - Math.pow(1 - p, 5) + 0.2 * Math.sin(Math.PI * Math.min(1, Math.max(0, (p - .45) / .55)));
tl.fromTo(el, {scale: .8}, {scale: 1, duration: .75, ease: pop}, t0)
  .fromTo(el, {opacity: 0}, {opacity: 1, duration: .375, ease: "none"}, t0);
```

Sync: elements appear about 0.1 s before their keyword; numbers count as spoken. Main subject on screen within 0.3-0.8 s (chapter scenes: content from 1.3 s). Cards enter with their text. Elements never exit; once placed they may rest.

Periodic motion (pulsing "?" opacity .35 + .65*(.5 + .5*sin(2πk/1.4)), dashed ring rotate k*360/14 deg, typing dots, funnel particles; k = t - t0) is computed in the `onUpdate` of one proxy clock `{t: 0} -> {t: DUR}`, `ease:"none"`, never timers.

Music: felt piano + soft synth pulse, 88 BPM, lifting at chapter two; no beat grid. SFX only: whoosh + low thud on a chapter's first scene, a chat ding, a click when the ring snaps; at most 6 per film.

## Materials and light

- Stage: `radial-gradient(ellipse at 50% 40%, #1B212C 0%, #0E1116 60%, #0A0C10 100%)`.
- Plate (a local file in the project's `assets/`): box `inset: -3%`, `<img>` cover, `filter: brightness(b) saturate(.82) contrast(1.06)`. Chapter scenes may open over-exposed: brightness 1.8b falling to b over 1.3 s `power4.out`.
- Tone: `linear-gradient(135deg, rgba(18,42,64,.35), rgba(245,165,36,.10))`, `mix-blend-mode: soft-light`.
- Masks (`.ed-shade-*`): left 92% -> 70% at 45% -> 25%; right 90% -> 62% -> 20% (mirrored); center radial 18% -> 72% at 85%; warm (closing line) radial 62% -> 88% at 80%; all in `rgba(14,17,22,a)`. No extra full-frame black layer.
- Vignette: `inset 0 0 260u rgba(0,0,0,a)`, a = 0.35 (left/right mask), 0.55 (center/warm).
- Glass: face 58%, 1px 12% white, radius 22u, padding 36u, `backdrop-filter: blur(16px)`, shadow `0 30u 80u rgba(0,0,0,.35)`; conclusion card border accent 60%; highlighted week card adds `0 0 60u` accent 18%. Backdrop blur needs the photo below in the same page and no ancestor with `filter`, `opacity < 1`, `mask`, `clip-path` or `mix-blend-mode` (a backdrop root blinds it): animate the card itself. No glass in glass. Fallback (inferred): face 78% + `inset 0 1px 0 rgba(255,255,255,.06)`, via `@supports` or `.ed-glass.solid`.
- Text shadows: statements `0 8u 40u rgba(0,0,0,.6)`, big numbers `0 10u 40u rgba(0,0,0,.5)`. Amber halos: task card `0 10u 30u` 35%, ring core `0 0 (8+4i)u` 60%, snapped ring `0 0 40u` 35%.
- Procedural night office when `assets/` has no photo (inferred): `.ed-night` base `#0B1118 -> #111A24 (55%) -> #0A0D12`; a window band (12-58% H) of 18-28 mulberry32-seeded rectangles, 60-180u x 30-110u, 70% warm (`rgba(245,165,36,.55)` / `rgba(255,214,150,.45)`), 30% cool `rgba(150,190,230,.25)`, one `filter: blur(28u)` (18-40u) on the band; `.ed-night-light`: lamp glow at 72% 64% on the photo side + desk shade from 62% down. Then tone, mask, vignette, Ken Burns as usual; seed the rectangles once, before the timeline.

## Technique library

Capabilities, not obligations. Sizes not given here are in `exec-dark.css`.

- **Photo plate.**
  ```js
  tl.fromTo(".ed-plate img", {scale: 1.09, x: .01*MV.width}, {scale: 1, x: -.01*MV.width, duration: DUR, ease: "none"}, 0);
  ```
- **Statement with underline.** Accent phrase `.ed-em` with child `.ed-underline`.
  ```js
  tl.fromTo(".ed-line", {y: 30*u, opacity: 0, filter: "blur(8px)"}, {y: 0, opacity: 1, filter: "blur(0px)", duration: .85, ease: "power4.out", stagger: .3}, t0)
    .fromTo(".ed-underline", {scaleX: 0}, {scaleX: 1, duration: .6, ease: "power4.out"}, t0 + i*.3 + underlineAt);
  ```
- **Kicker.** Accent number + grey chapter + white section ("01  Root cause  Problem and impact"); not on title, closing-line or brand cards. The section is shown, not spoken.
- **Full-film progress bar.** Fill `linear-gradient(90deg, accent 75%, accent)` scaled from the left; chapter ticks 2u x 15u, 45% white. Not on opening, title or end cards, though their time counts. Continuity across files: in play order, `at` = sum of durations of all earlier scenes, `total` = sum of all durations (fades keep it), `marks` = start of each chapter's first scene. A scene cannot read `storyboard.json` (no fetch): write the three numbers as literals; regenerate them whenever `tts.py` rewrites durations. The fill runs linearly from at/total to (at + DUR)/total, so neighbours meet.
  ```js
  const FILM = {at: 21.3, total: 119.8, marks: [21.3, 65.1]};
  tl.fromTo(".ed-progress-fill", {scaleX: FILM.at/FILM.total}, {scaleX: (FILM.at + DUR)/FILM.total, duration: DUR, ease: "none"}, 0);
  // one .ed-progress-mark per entry in FILM.marks at left = 100*m/total %
  ```
- **Chapter card** (in-scene, a chapter's first scene, 1.6 s). `#0B0D11` at 75% holds 0.8 s, clears over 0.8 s `power4.out`. Centred 88u title sharpens in over 0.352 s (22%), holds to 0.992 s (62%), then over 0.608 s `power2.inOut` shrinks to 26/88 and flies to the Kicker spot; its section text fades in meanwhile. The real Kicker rises at 1.3 s.
  ```js
  const w = el.offsetWidth;   // measured once, before building; el rests at the Kicker spot, origin 0 50%
  tl.fromTo(".ed-chapter-dim", {opacity: .75}, {opacity: 0, duration: .8, ease: "power4.out"}, .8)
    .fromTo(el, {opacity: 0, filter: "blur(8px)"}, {opacity: 1, filter: "blur(0px)", duration: .352, ease: "power4.out"}, 0)
    .fromTo(el, {x: MV.width/2 - 96*u - w/2, y: MV.height/2 - 85*u, scale: 1}, {x: 0, y: 0, scale: 26/88, duration: .608, ease: "power2.inOut"}, .992);
  ```
- **Title card.** Title per character from t0 + 0.05 (step 0.14); question subtitle 28u below at +1.0, rise 30u; 42u sans 500 thesis 70u below at +1.8, underline 0.8 s later; bottom inset 120u; mask `center`.
- **Brand card.** Opening: 760u glow lines above and below grow from centre at 0.05 (0.9 s); brand per character from 0.2 (step 0.06), accent part from 0.55 (step 0.12); tagline 46u, tracking 40u, `#D8D3CA`, at 0.9, rise 20u; gap 46u. End: 96u title at 0.1 (step 0.12), 520u line at 0.5, 64u brand (tracking 0.2em) at 0.8 / 0.9, tagline at 1.0. Plate 0.4, mask `center`.
- **Big number + dots.** One per screen; thousands grouped (not years). Dot matrix: 10,000 dots, 200 columns, one div per row: `radial-gradient(color radius max(0.6px, .3*cellW), transparent max(0.9px, .32*cellW))` tiled `cellW cellH`; full rows opacity 0.35 + 0.4*hash(row+1), `hash(x) = frac(sin(x*127.1 + 311.7)*43758.5453)`, growing row 0.6; shown = floor(n * power4.out(p)) over 2.2 s, as row `clip-path: inset(0 X% 0 0)` in `onUpdate`; never 10,000 divs. Dot row: radius 16u, 50u apart, rows 62u, 12 per row.
- **Cards and annotated list.** Items slide in; marks red / amber `●`, green `✓` at 0.7 x size, annotations only.
- **Week cards.** Accent rule 3u, 40u above, grows 0.9 s; four cards (330u, 30u gaps, padding 34u), "W1" 60u 800 accent + two 36u lines; the highlighted card gets edge + glow and accent later lines.
- **Rings.** Row: 96u rings, 44u gaps, cores (18 + 3i)u, W labels 12u below. Single 190u ring: `dashed` = missing (spin, pulsing "?" 0.44 x size, counter-rotated); `snap` = embedded (1.9 -> 1 over 0.9 s `power4.out`, core 0.34 x size), optional 40u 700 accent label.
- **Handoff.** 200u round nodes (`rgba(16,19,25,.7)`, 2px 22% white, blur 10px, 44u 700) pop at t0 + 1.1 / 1.25 (32u grey sub); 44u grey label at +1.3; 6u shaft grows +1.4 -> 2.3 (grey -> red), red head fades in +2.0 -> 2.6; 96 x 72u amber task card slides 1.8 -> 3.1 (shown from 1.6, fades 3.4 -> 3.8); at 4.5 a 4u red line strikes the label (0.6 s); a 66u red word pops at 4.8.
- **Doors and meter.** Doors pop 0.25 s apart, 40u gap, pulsing "?"; place them over real doors in the photo. Meter: striped fill revealed by `clip-path` (not scaleX, which stretches stripes) from t0 + 0.6 over 1.6 s; accent notch with a 30u 700 accent label 16u below at t0; 30u value label at +1.0.
- **Funnel.** SVG viewBox 1100 x 420: trapezoid `300,40 740,40 590,200 450,200` (fill `rgba(16,19,25,.55)`, stroke 30% white, 3); stem 520,200-520,340 (accent 40%, 3); outlet 520,340-820,340 (accent, 5), head `820,326 850,340 820,354`. 80 particles: start i*0.08 s, life 2.2 s looping, sx = 320 + (i*137 mod 400); falling (u < 0.55, v = u/0.55): x = sx + (520 - sx)v², y = 46 + 154v; every 9th passes down the stem and outlet (red if i mod 18 = 0, else accent, radius 9); others (white 70%, radius 5) fade over u 0.45-0.55. Result label at t0 + 1.2.
- **Role rows.** "Who" 50u 700 right-aligned in 300u, a 120 x 5u rule (40% white, accent if highlighted) grows at +0.2, "what" 58u 800 slides in at +0.4; 40u gaps, 22u row margin, rows 1.6 s apart.
- **Chat (opening).** De-branded chat in glass (padding 30u 44u 36u, rise 20u / 0.8 s): 26u grey channel over a 1px 10% rule; escalation (6u red edge on red 12%, radius 10u, 32u) slides in; messages = 68u avatar (`#9AA1AD`, leader accent) + 24u name + 38u bubble (padding 18u 28u, radius 16u, 8% white; leader accent 18% + 60% border), rising 24u; typing: three 12u dots, period 1.1 s, offsets 0 / 0.18 / 0.36, lift 10u in the first 40%; divider "—— three days later ——" 26u. No answer: `grayscale(g) brightness(1 - .25g)` over 1.2 s. Extras: one green pill (radius 40u, green 16% / 55% border, 32u); a 300u accent "?" at 22%. No real app look.

## Transitions

The contract offers `cut` or `fade` (0.4 s). The original soft crossfade (0.3 s, 9 frames, `power2.inOut`, next scene over a frozen last frame) maps to `fade`.
- Ordinary change: `fade`; no key text in the first 0.4 s (the Kicker, in the same spot, is fine).
- Situation scenes in one chat window: `cut`, same composition.
- A chapter's first scene: `cut` plus an in-scene dip echo: frame 0 fully `#0B0D11`, clearing over 0.15 s (frames 0-4 at 30 fps) `power2.inOut` onto the chapter card already at 75% dim; optionally with the over-exposed plate intro.
  ```js
  tl.fromTo(".ed-dip", {opacity: 1}, {opacity: 0, duration: .15, ease: "power2.inOut"}, 0);
  ```
No black frames, white flash, push, slide or wipe. A scene never fades itself out; its last frame is complete.

## Captions

Translucent black box, bottom centre, warm white, no tick, no border. The box stays while lines change, sized to the longest line; text shrinks, never truncates. First line rises 12u with a 0.25 s fade; the box goes 0.6 s after the last line. Height 1.75 x size, side padding 0.8 x size each, max width 86% W (portrait 90%). The bottom 190u holds only captions and, below them, the progress bar.

```css
--cap-bg: rgba(0,0,0,.55); --cap-fg: #F2EFE9; --cap-font: <sans stack>; --cap-size: calc(40 * var(--u));
--cap-radius: calc(8 * var(--u)); --cap-bottom: calc(58 * var(--u));   /* portrait: 220u */
--cap-weight: 500; --cap-shadow: 0 calc(2*var(--u)) calc(6*var(--u)) rgba(0,0,0,.6);
```

## Image-gen guidance

One plate per content scene, in `assets/` (user photo or generated), never a URL; neighbours may share one.
- Anchors: "cinematic editorial photograph, modern tech company office", "warm amber practical lights against deep teal-blue shadows", "shallow depth of field, 35mm lens, realistic, subtle film grain", "premium corporate documentary look, lots of negative space", "East Asian professionals seen from behind, hands only, or as distant silhouettes, no faces"; end with "no text, no letters, no logos, no watermark". 16:9 (portrait 9:16). Add "subject on the left third, right half empty desk / dark background" (or mirrored).
- Subjects: night office with one desk lamp; over-the-shoulder phone chat; phone face-down by coffee at dawn; glass corridor, one warm meeting room; a hand passing a folder; high-angle open office; Newton's cradle; two doors, one ajar; hands typing, blurred dashboard; two people at a whiteboard from behind; sticky-note wall; sunrise skyline, empty chair.
- Never generate: any layout element (Kicker, numbers, dots, rings, cards, labels, funnel, bar), text or digits; logos, real companies or UIs, faces; white, neon, HUD or illustrated images.
- Post-treatment: only the plate treatment (Materials), mask chosen by subject side.

## Taboos

- No white or light background; no second hue; red and green only in annotations and the opening.
- No charts (bar, line, pie); data only as big number, dots, meter, timeline.
- No black frames, white flash, push, slide or wipe.
- No bounce, elastic, typewriter, scale pop beyond the light pop; no rotation except the dashed ring's slow spin; no dutch angle or shake.
- Photo never brighter than its text, never invisible, no faces or legible text; no small text on the bare photo.
- Max 6 elements; never two big text blocks; no half-empty frame, empty opening or card that fills later.
- Nothing else in the bottom 190u; no grain, scanlines, grid, HUD; no "AI generated" badges.

## Known gaps

- Fonts: system stacks replace Noto Serif / Sans CJK; Latin headlines look more bookish.
- Photos: new images match the original 2560x1440 plates in composition, not look. The procedural night office has mood but no subject, so "the photo must be legible" cannot be met; its values are inferred.
- Glass: `backdrop-filter` is costly and fails silently under a backdrop root; the solid fallback loses the frosted photo.
- Transitions: original crossfade 0.3 s vs contract 0.4 s; the chapter dip's outgoing half (previous scene darkening) is not built.
- Chapter title and Kicker overlap about 0.2 s (as in the original).
- Captions break by sentence or 16 chars, not at phrase pauses.
- Blur radii (8, 10, 16px) are 1080p pixels; scale by u elsewhere.
- Chat avatars use `#9AA1AD`, not the original second-hue blue `#7aa2f7`.
- Voice (calm executive, about 1.1x) not covered; portrait inferred.

---
Design adapted from an Apache-2.0-licensed style set; see NOTICE.md.
