# Style: exec-brief (Executive Brief)

Units: `u` = frame short side / 1080. At 1920x1080, 1u = 1px. In CSS write `calc(N * var(--u))` (see `exec-brief.css`). In GSAP use `const u = MV.u;` and tween `y: 28 * u`. Every size below is in u.

## Identity

A premium business brief turning under the camera: ivory paper, ink serif headlines, one accent colour, hairline editorial layout. Arguments rise one line at a time, the key phrase is swept by an accent underline, numbers count up, two columns compare, a flow connects node by node, a timeline lights cell by cell, and the whole frame always pushes in slowly. It must read as a carefully typeset executive brief, never as a slide deck. Five identity rules, all mandatory: ivory paper + ink + exactly one accent; serif for statements, sans for everything else; 12-column editorial grid with a Kicker above every statement; unhurried rise-and-settle motion with no overshoot; a slow push in every scene.

Best for: leadership and management talks, strategy reviews, quarterly or annual reviews, methodology, internal comms, industry viewpoints, white-paper readouts, any explainer with a thesis, evidence, a little data and an action plan. Not for: hardware showcases (use a 3D product style), software UI demos (use dark-keynote or living-screencast), playful or handmade topics.

## Palette

Warm paper, cool ink, one accent reserved for the conclusion.

| Hex | Role |
|---|---|
| `#F7F3EA` | paper, full-frame base; fades to `#EEE8DA` at the bottom |
| `#EEE8DA` | paper lower part |
| `#FBF9F4` | card face (slightly brighter than paper) |
| `#16181E` | ink: statements, titles, chapter rule, caption pill base |
| `#3A3E47` | body text |
| `#5C616B` | secondary text, notes, flow connectors |
| `#8E939C` | labels, folio, unlit timeline ticks |
| `#CDC6B6` | hairline rules, card borders |
| `#E2DCCD` | fainter rules, page-margin lines |
| `rgba(30,26,18,.16)` | card soft shadow |
| `#C8371E` | the one accent, default vermilion |

Accent alternatives (pick exactly one per film): cobalt `#1F3DFF`, forest `#1E6B4B`, gold `#B8892B`, or the user's brand colour.

Accent goes only to: the key phrase inside a statement, its underline, the Kicker square and serial, the chapter number, the edge of the "conclusion" flow node, the lit part of a timeline and its lit ticks, the tick on the caption pill, and the one conclusion number of the film. Two accent-coloured elements in one frame means one of them is wrong. Big numbers are ink by default. No gradient text, no second hue, no coloured icons. Photos are slightly desaturated so they never outshine the paper.

## Type

Font stacks (macOS families first, then the bundled faces, see INDEX.md § Fonts):
- Serif (statements, titles, chapter numbers, quotes, flow node titles, compare column titles), weight 600-700: `Georgia, "Songti SC", "MV Noto Serif SC", "Noto Serif CJK SC", "Source Han Serif SC", SimSun, "Times New Roman", serif`.
- Sans (body, notes, labels, numbers): `-apple-system, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", "MV Noto Sans SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`.

Scale (u):

| Role | Size | Weight / details |
|---|---|---|
| statement | 72-96 (default 88) | serif 600, line-height 1.32, tracking +0.01em; 2-4 lines per screen, at most 14 CJK chars per line |
| chapter number | 240 | serif 700, line-height 1, tracking -0.04em, accent |
| chapter title | 72 | serif 600, line-height 1.2 |
| chapter subline | 30 | sans, line-height 1.5, `#5C616B`, max width 760 |
| column / node title | 34-56 (compare 56, flow 28-36 by card width) | serif 600, line-height 1.2 |
| quote | 56 | serif 600, line-height 1.45 |
| body | 28-34 (default 32) | sans 400, line-height 1.6, `#3A3E47` |
| label / Kicker / folio | 16-20 (Kicker 20, folio 18) | sans 600, UPPERCASE, tracking 0.14-0.2em, tabular numerals |
| stat number | 160-220 (default 200) | sans 600, tracking -0.03em, tabular numerals, line-height 1 |
| stat unit | 28% of stat size | sans 500, `#5C616B`, margin-left 0.08 x stat |
| stat prefix (>=, +) | 45% of stat size | sans 500 |
| stat label | 24-30 (max(24, 0.15 x stat)) | sans, `#5C616B` |
| caption | 40 | sans 500 |

One loudest level per screen: a statement screen carries no second big text block; a big-number screen carries one line of explanation. CJK text: `white-space: nowrap` per line and explicit line breaks; never rely on auto-wrap.

Portrait (inferred): statements 56-72u, at most 9 CJK chars per line.

## Layout grid

- 12 columns, page margin 120u, gutter 32u, content top 110u. Column width = (W - 2*120u - 11*32u) / 12. Column c left edge = 120u + c*(colW + 32u); span n = n*colW + (n-1)*32u.
- Editorial, left-aligned. Two faint vertical page-margin lines (`#E2DCCD`, 1px, full height) at x = margin - 40u and W - margin + 40u.
- Folio top-right at top 64u, right = margin: one small label line such as "ROOT CAUSE · 02 / 05".
- Kicker top-left above every statement: accent square (0.55 x label size, radius 0.1 x size) + optional accent serial "01" + label. Then the content.
- One argument per screen, 3-6 elements, filling the middle of the safe area (never half the frame empty). Subject occupies 30-50% of frame height.
- Typical compositions: statement + one big number or one figure; flow or timeline across all 12 columns in the lower half with Kicker + serif statement in the upper half; compare = two columns of 5 each with a vertical rule between.
- Hierarchy by size and colour, not by boxes. Cards only for several peer items; other text sits directly on the paper.
- Data only as big number, timeline, or compare. No bar, line or pie charts; several numbers go on several screens, one per screen.
- Safe area 5% all round (>= 120u left/right at 1080 height, >= 100u top/bottom); caption zone (bottom 160u) is extra. Big numbers and statements stay >= 180u above the bottom.
- Portrait (inferred): margin 72u, 6 columns; flow becomes vertical (nodes stacked); compare becomes top and bottom blocks.

## Camera grammar

A vocabulary, not a route. One main move per scene; everything slow. Wrap the scene in a camera layer (`#camera`, `transform-origin` at the focus) and move only `scale`, `x`, `y`.

| Shot | Expresses | How |
|---|---|---|
| whole-scene slow push | focus, going deeper | scale 1 -> 1.04-1.06 over the full scene, `ease:"none"` on log scale (see Motion); present in every scene |
| line-by-line rise | the argument is spoken sentence by sentence | statement lines staggered 0.18-0.36 s, aligned to narration sentences |
| underline sweep | puts the stress | 0.6 s after the key phrase turns accent, sweep the underline |
| count-up | scale, facts | 1.4 s ease-out count, unit follows, label rises 0.5 s later |
| node-by-node link | division of work, cause, process | flow nodes rise in turn every 0.55-0.7 s, connector grows right behind |
| cell-by-cell lighting | plan, steps, time | timeline lit bar advances, lighting each tick and its label |
| left vs right | two approaches, two kinds of decision | two columns, middle rule grows, items rise one by one |
| chapter card | a new part begins | huge number rises, rule grows, title settles; hold >= 1.5 s |
| quote | sets the tone, closes | huge faint accent quote mark top-left, serif text rises |
| figure push | scene, evidence | image inside its frame scales 1 -> 1.06, one caption line |

No dutch angle, no shake, no snap zooms. Slow push: pan only inside the zoom margin so the paper edge never enters the frame: focus offset applied = (focus - centre) x (1 - 1/zoom).

Scene lengths: chapter card 3-4 s, statement 5-9 s, data / flow / timeline 7-12 s, closing quote 5-7 s.

## Motion

Unhurried: fast start, long settle, no overshoot.

| Action | Duration | Ease | Notes |
|---|---|---|---|
| element reveal (line, card, block) | 0.7 (range 0.5-0.8) | `power4.out` (quintic out) = cubic-bezier(0.22,1,0.36,1) | rise 28u + opacity 0 -> 1 |
| Kicker reveal | 0.7 | `power4.out` | rise 14u |
| stagger between lines / items | 0.14 base; statements 0.18; lists 0.22; up to 0.36 | | |
| rule draw | 0.9 | `power4.out` | scaleX from 0, origin left |
| accent underline sweep | 0.6 | `power4.out` | starts after the phrase is visible |
| number count | 1.4 | `power4.out` | stat block rises 24u over 0.6 s; label rises 14u at +0.5 s |
| small element settle | 0.5 | `power4.out` | |
| timeline step | 0.7 per step (0.7-0.9) | linear fill between ticks | base rule draws 0.9 s first; fill starts at +0.4 s |
| flow node | 0.55 per node (0.55-0.7) | `power4.out` | connector 0.5 s starting +0.3 s after its node, end dot appears in last 40% |
| chapter card | number 0.8 s rise 36u; rule at +0.35 s (0.9 s, 520u max, 2u ink); title at +0.5 s rise 28u; subline at +0.75 s rise 20u | `power4.out` | |
| quote | mark fades 0.9 s; text at +0.2 s rise 24u; byline at +0.9 s rise 12u | `power4.out` | |
| figure | rise 30u over 0.8 s; inner scale 1 -> 1.06 over 8 s; caption at +0.5 s rise 12u | `power4.out` / `none` | |
| minimum for any move | 0.25 | | |

Other eases in the vocabulary: cubic out `power2.out`, cubic in `power2.in`, cubic in-out `power2.inOut` = cubic-bezier(0.65,0,0.35,1) (transitions, multi-key camera). Never `back`, `elastic`, `bounce`, rotation or scale pop.

Sync: an element appears about 0.1 s before its sentence or keyword; a number starts counting as it is spoken. The main subject (not just the Kicker) is on screen within 0.3-0.8 s of scene start; never an empty paper waiting for the voice. Cards and flow nodes enter together with their text, never empty first. Elements do not exit: when a screen is done, the scene ends.

Slow push with constant perceived speed: tween a proxy `z` from `Math.log(1)` to `Math.log(1.05)` with `ease:"none"` over the scene duration and set `scale = Math.exp(z)` in `onUpdate`.

## Materials and light

- Paper: `linear-gradient(180deg, #F7F3EA 0%, #F7F3EA 55%, #EEE8DA 100%)`. A soft warm light from the top: an ellipse at left 15% W, top -55% H, size 70% W x 100% H, `radial-gradient(circle closest-side, rgba(255,255,255,.6), rgba(255,255,255,0))`. Vignette 8%: `radial-gradient(circle at centre, radius 1.1 x shortSide, rgba(30,26,18,0) 55%, rgba(30,26,18,.08) 100%)`. Clean digital paper: no grain, fibre or creases.
- Darker paper for heavy or closing beats: overlay `rgba(30,26,18, (1 - light) * 0.8)` with light 0.9.
- Cards: face `#FBF9F4`, 1px `#CDC6B6` border, radius 12u, padding 36u, shadow `0 14u 40u rgba(30,26,18,.16)`; optional 6u accent edge on the left.
- Rules 1-2u; chapter rule 2u ink. Accent underline 6u (or 0.055 x font size, min 3px) at opacity 0.85-0.9; as a highlighter behind text use opacity 0.35.
- Figure frame: 1px rule border, same soft shadow `0 16u 44u`, radius 10u, image `saturate(0.85)`.

## Technique library

Capabilities, not obligations; the content decides which to use.

- **Chapter card.** Opens the film and each part. Numbers "01 / 02 / 03" (or CJK numerals), consistent across the film. Huge accent serif number, 2u ink rule that grows, serif title, optional sans subline.
  ```js
  tl.fromTo(".eb-chapter-no", {y: 36*u, opacity: 0}, {y: 0, opacity: 1, duration: .8, ease: "power4.out"}, t0)
    .fromTo(".eb-chapter-rule", {scaleX: 0}, {scaleX: 1, duration: .9, ease: "power4.out"}, t0 + .35)
    .fromTo(".eb-chapter-title", {y: 28*u, opacity: 0}, {y: 0, opacity: 1, duration: .7, ease: "power4.out"}, t0 + .5);
  ```
- **Statement with accent phrase and underline.** Each line is a block; the key phrase is a `<span class="eb-em">` in accent colour with an absolutely positioned underline child at bottom -0.04em, height 0.055em, scaled from 0.
  ```html
  <div class="eb-statement"><div class="eb-line">Delegation still means</div>
  <div class="eb-line"><span class="eb-em">owning the result<i class="eb-underline"></i></span></div></div>
  ```
  ```js
  tl.fromTo(".eb-line", {y: 28*u, opacity: 0}, {y: 0, opacity: 1, duration: .7, ease: "power4.out", stagger: .18}, t0)
    .fromTo(".eb-underline", {scaleX: 0}, {scaleX: 1, duration: .6, ease: "power4.out"}, t0 + .18 + .7);
  ```
- **Big number.** One per screen, beside a serif statement. Tween a plain object; write only `textContent`; group thousands with commas; tabular numerals keep width stable.
  ```js
  const o = {v: 0}, el = document.querySelector(".eb-stat-num");
  tl.fromTo(o, {v: 0}, {v: 23000, duration: 1.4, ease: "power4.out",
    onUpdate: () => el.textContent = Math.round(o.v).toLocaleString("en-US")}, t0);
  ```
- **Compare.** Two columns, each 5 columns wide, a 1.5u vertical rule in the gap that grows over 0.9 s from t0 + 0.1; left title at t0, right title at t0 + 0.25; items rise 16u, starting title + 0.35 s, stagger 0.14. The recommended side has an accent title and accent bullets (square 0.32 x item size, radius 0.06 x size); the other side ink title, `#5C616B` bullets.
- **Flow.** 3-5 node cards across 12 columns, connector 48u between cards. Card height 150u, radius 10u, 1.5px border (`#CDC6B6`, accent for the conclusion node), padding 20u 22u; inside: serial "01" label 16u, serif title (28-36u by card width: clamp(24u, cardW/7.2, 36u)), sub 18-24u. Connector: 1.5u `#5C616B` line scaled from the left + a 10u dot at its end.
  ```js
  nodes.forEach((n, i) => {
    tl.fromTo(n, {y: 26*u, opacity: 0}, {y: 0, opacity: 1, duration: .7, ease: "power4.out"}, t0 + i*.55);
    if (links[i]) tl.fromTo(links[i], {scaleX: 0}, {scaleX: 1, duration: .5, ease: "power4.out"}, t0 + i*.55 + .3);
  });
  ```
- **Timeline.** 4-6 steps across 12 columns in the lower half. Base rule 2u `#CDC6B6` draws 0.9 s; an accent bar 4u tall (radius 2u) fills linearly from t0 + 0.4 at one step per 0.7 s. Ticks are 18u circles with 2px border (`#CDC6B6` on paper fill, switching to accent fill and border when the bar reaches them; use a 0.25 s colour tween). Under each tick: label 18u 700 tracking 0.14em (accent when lit), text 24u; first label left-aligned, last right-aligned, others centred, label box 320u wide.
- **Quote.** Opening quote mark at 3.2 x the quote size, accent at 18% opacity, offset left -0.35em, top -1.1em; serif text; byline label "— Name" 18u tracking 0.2em.
- **List and cards.** 3-5 parallel points rising one by one (stagger 0.22, rise 18u), each with an accent square (0.3 x size) or a "01" accent serial; on paper or inside a card.
- **Figure.** User image, screenshot or generated image; at most one per screen, at most 5 columns wide, one caption line 16u under it. Inner image scales 1 -> 1.06 over 8 s (`ease:"none"`).
- **Folio and margin lines.** Present on every content screen; they make it editorial, not a slide.

## Transitions

The project contract offers `cut` or `fade` (0.4 s crossfade) between scene files.
- Ordinary change inside a chapter: `fade`. The incoming scene must not put key text in its first 0.4 s.
- Into a chapter card or a new argument: `cut`, with the incoming scene's elements rising from frame 0 (they are on screen by 0.3-0.8 s).
- Style-owned, in-scene only (both pages live in one scene file as two full-frame `.eb-page` layers):
  - **Paper slide** (the style's default, 0.4 s = 12 frames at 30 fps, `power2.inOut`): the new page slides in from x = 100% W to 0 with a left-edge shadow `-(40*(1-p)+4)u 0 (80*(1-p)+10)u rgba(30,26,18,0.35*(1-p)+0.05)`; the old page moves to x = -12% W and darkens with an overlay rising to `rgba(30,26,18,.18)`.
  - **Rule wipe** (for chapter or argument changes, 0.4 s, `power4.out`): a 2u ink line at opacity 0.9 sweeps left to right; the new page is revealed behind it with `clip-path: inset(0 calc(100% - x) 0 0)`; the old page does not move.
  ```js
  tl.fromTo("#pageB", {xPercent: 100}, {xPercent: 0, duration: .4, ease: "power2.inOut"}, tSwap)
    .fromTo("#pageA", {xPercent: 0}, {xPercent: -12, duration: .4, ease: "power2.inOut"}, tSwap)
    .fromTo("#pageA .eb-dim", {opacity: 0}, {opacity: 1, duration: .4, ease: "power2.inOut"}, tSwap);
  ```
No black frames, no dip to black. Scene code never fades its own whole frame.

## Captions

Ink pill, bottom centre, ivory text, accent tick on the left. The box stays while lines change; only the text swaps; width is sized to the scene's longest line (no width jumping) and the text shrinks rather than truncates. First line enters with a 12u rise and 0.25 s fade. Max width 86% W (portrait 90%), height 1.85 x font size, side padding about 1.15 x size, shadow `0 10u 30u rgba(30,26,18,.25)`. Accent tick: left 0.8 x size, width 0.1 x size, height 0.64 x size. Keep the caption zone (bottom 160u) free of other text.

```css
--cap-bg: rgba(22,24,30,.9); --cap-fg: #F7F3EA; --cap-font: <sans stack>; --cap-size: calc(40 * var(--u));
--cap-radius: calc(8 * var(--u)); --cap-bottom: calc(72 * var(--u));   /* portrait: 220u */
--cap-weight: 500; --cap-accent: var(--eb-accent); --cap-mark: tick; --cap-shadow: 0 calc(10*var(--u)) calc(30*var(--u)) rgba(30,26,18,.25);
```

## Image-gen guidance

Only when image generation is on; images are supporting figures and must sit on this paper.
- Anchors (start every prompt with these): "editorial corporate photography, warm ivory paper tones, soft natural daylight", "minimal composition, generous negative space, one clear subject", "muted palette, low saturation, slightly warm shadows, clean digital image", "shallow depth of field, calm, premium business magazine aesthetic"; end with "no grain, no noise, no lens flare, no text, no watermark".
- Subjects: work scenes for a figure frame (meeting room, open office, whiteboard discussion, window over a city) with people out of focus or from behind, no identifiable real people; order vs chaos props (neat files, scattered notes, a row of lamps, a clean desk); low-contrast material swatches (paper fibre, linen, matte metal, dark wood) only as card or page-corner accents.
- Never generate: rules, titles, big numbers, charts, timelines, flows, quote marks (all drawn in HTML); any text, labels, digits or units; logos, real companies, real product UIs, real faces; dark, neon or HUD images.
- Post-treatment: crop 3:2 or 16:9, `saturate(0.85)`, no other filters, one caption line under the frame; at most one image per screen, at most 5 columns wide; text beside the image, never over it; no full-frame background photos.

## Taboos

- No dark background, neon, glow, HUD, grid lines, scanlines, grain, paper texture.
- No chart components (bar, line, pie); data only as big number, timeline, compare.
- No coloured icons, emoji, illustrated characters; no second hue.
- No bounce, overshoot, rotation, scale pop, typewriter; no dutch angle, no shake.
- At most 6 elements per screen, never two big text blocks; no half-empty frame; no empty opening (main subject within 0.8 s); no empty card that fills later.
- No fade to black; scene code does not fade its whole frame.
- Nothing else in the caption zone; no fully accent-coloured big number except the one conclusion number of the film.
- No centred-everything slide layout; no boxes or fills behind text (caption pill excepted).

## Known gaps

- Latin and digits come from system sans (SF / Helvetica Neue); a dedicated editorial Latin face would be sharper. The "engineered" label look relies on tabular numerals and wide tracking.
- The paper has no real fibre or slight print misregistration; it is clean digital paper.
- True two-scene paper slide and rule wipe need both pages in one scene file; across scene files the contract only allows cut or fade.
- Portrait layout is inferred, not taken from a reference.

---
Design adapted from an Apache-2.0-licensed style set; see NOTICE.md.
