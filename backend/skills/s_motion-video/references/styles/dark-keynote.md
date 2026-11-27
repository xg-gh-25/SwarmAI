# Style: dark-keynote (Dark Keynote Launch)

Units: `u` = frame short side / 1080. At 1920x1080, 1u = 1px. In CSS write `calc(N * var(--u))` (see `dark-keynote.css`). In GSAP use `const u = MV.u;` and tween `y: 34 * u`. Every size below is in u.

## Identity

A launch-grade software film where the interface itself is the hero: a near-black gradient stage, a hairline grid, soft cool light, and original abstract UI parts (a caret, windows, notification cards, files, thumbnails, tiles, one big number) that land exactly on the grid and on the beat. One brand accent only; the product is unveiled by a sweep of light. No physical product, no glass. Five identity rules, all mandatory: near-black cool stage + hairline grid in every scene; exactly one accent and it belongs to the product (red belongs to the problem); everything lands on a 120 BPM grid with a fast-out, small-overshoot, dead-stop ease; the camera hugs the UI (close shots, windows bleeding off the edge); disappearance is designed (retract part by part, collapse like an old TV), never a fade.

Best for: software and SaaS launches, feature explainers, keynote openers, AI-product demos, before/after stories. Not for: strategy talks (use exec-brief), hardware (use a 3D product style), real charts (use a dataviz style), warm or handmade topics.

## Palette

Cool neutral greys with a slight blue cast, never warm brown.

| Hex | Role |
|---|---|
| `#0A0B0F` | stage base, transition backdrop |
| `#151822` | radial lift, upper middle |
| `#3B4CCA` / `#6F82C8` | cool pool top-right 8% / glow behind the window 10% |
| `#12151C` / `#1A1E28` / `#222734` | panel tiers 1 / 2 / 3 (base, raised one, raised two) |
| `#10131A` | product window body, status bar (body gradient `#12151D -> #0D0F15`, title bar `#151923`) |
| `#E8EAF0` / `#F1F3F7` | primary text / big number |
| `#8A90A0` / `#5C6272` | secondary text, units / labels |
| `rgba(255,255,255,.08)` / `.07` | 1px panel stroke / top inner highlight |
| `#B7F34A` | the one accent, default lime |
| `#FF5A5F` | problem: red badges, warnings, full storage bar |
| `#C9D3E6` `#F2A65A` `#7E95FF` | three flow colours, only on motion trails |

Accent alternatives (exactly one per film): violet `#8B7CFF`, orange `#FF7A1A`, or the product's brand colour. Text on accent is `#0A0B0F`.

Accent = the product: hero caret, action pill, check, the key value or ruler, a lit tile edge, status dot, rim light, caption dot; each with a same-colour glow of about 35%. Two accent elements on screen means one is wrong. Red = noise: only while the problem exists, never after the turn. Flow colours only while moving. Content colours (icon gradients, thumbnail skies, extension tags) stay small: many in the chaos, tidy after the snap; never a frame of one grey.

## Type

Font stacks (system fonts only):
- Sans (UI, titles, typed lines, big number with `tabular-nums`, captions): `-apple-system, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Noto Sans CJK SC", "Hiragino Sans GB", sans-serif`.
- Mono (labels, file names, status text, ruler labels): `"SF Mono", Menlo, "PingFang SC", monospace`.

| Role | Size (u) | Weight / details |
|---|---|---|
| one-line title | 48-150 | 500-600, tracking -0.01 to -0.03em, one per screen |
| typed line | 48 typical | 500, tracking -0.01em; caret 1.27 x size tall |
| big number | 360 (about 33% H) | 600 (never heavier), tracking -0.045em, tabular, `#F1F3F7`, line-height 1, never accent |
| unit | 110-120 | 500 `#8A90A0`, baseline right of the number, gap 40 |
| key line under number | 84 | 500; key value accent, `text-shadow: 0 0 34u` accent 55% |
| labels | 13-22 (above number 22, tracking 0.25em) | mono 600 UPPERCASE, tracking 0.15-0.3em, tabular, `#5C6272` |
| file names, status | 9-17 (status 17) | mono 500, tracking 0.02em, keep case |
| window text | name 22/600; side label 13 mono 0.16em; row 20/500; count 16 mono; title 14/500 `#8A90A0`; tabs 16/500 | notif card (h = height): title 0.2h 600, body 0.18h, time 0.15h |
| caption | 40 | 500 |

CJK: `nowrap`, explicit breaks. Wordmark = name 600, -0.03em, plus an accent caret 0.86 x size.

## Layout grid

- One focus per screen, much empty space, one title line per screen; the big number owns its screen.
- The camera hugs the UI: close shots; windows and grids bleed off right or bottom; subject left or low; product window 0.8-1.3 x frame width. A whole centred window only briefly in a snap pull; never a small window in a sea of black.
- Hairline grid in every scene, moving only with the camera. The snap grid appears only on its beat; slots = items, no holes.
- Product window, built at 1920 x 1080 window coordinates and scaled as one piece: title bar 72 (brand mark 36 at x 44, name at x 76, tabs at w/2 - 190, accent pill 138 x 38 at right 32), sidebar 360 (rows 320 x 52, radius 12, active `rgba(255,255,255,.05)`), status bar 56 (dot at x 40, text from x 58, right text at right 36), radius 26. Reveal scale `s0 = 0.8 x min(W/1920, H/1080)`.
- Big-number screen, centred: label 20% H, number 27%, key line 66%, ruler 84%; no caption. Safe area 5%; caption zone bottom 160u; lift content 25u under captions.
- Portrait (inferred): subject at 30-70% height; big number about 0.3 W, unit below; dive x2-3 rather than whole windows; snap grid of fewer columns (e.g. 7).

## Camera grammar

A vocabulary, not a route. 2.5D: one layer per depth plane in `#camera`; depth z (+ = toward lens) scales `s = 1 / max(1/zoom - z, 0.02)`; transform `translate(W/2 + shakeX, H/2 + shakeY) rotate(rot) scale(s) translate(-camX, -camY)`, origin `0 0`. Near layers blur `max(0, (s/zoom - 1.15) * 6) / s` px (when > 0.3). Log-space zoom: tween proxy `lz` between `Math.log(z0)` and `Math.log(z1)`, `zoom = Math.exp(lz)`; one `power2.inOut` tween per key.

| Shot | Expresses | How |
|---|---|---|
| locked extreme close | intimacy, the only live thing | zoom about 4.6, drift within x1.04 |
| log-space pull, a step per beat | accumulation, scale from one point | e.g. hold 4.6-4.9 to 2.8 s, then 3.4, 2.5, 1.9, 1.55, 1.28, 1.12 about every 0.5 s |
| push through a freeze | time stops, focus narrows | all holds about 1 s, camera pushes `power2.inOut` |
| one-beat snap pull | cause and effect together | 0.5 s `power2.out` to x0.78 |
| dive + match cut | entering detail | log zoom `power2.in` to x16 (1 -> 4.4 -> 4.7 -> 16), cut to the same thing full frame |
| half-second inserts | overload | 0.5 s each, grown from the item's place |
| near-locked product turn + sweep | the product is the hero | from -24 deg (0.42 rad) to front over 2-4.5 s, with the sweep |
| lateral track over a UI plane | reading order | timelines, pipelines |
| top view over the grid | system, structure | architecture, permissions, workflows |
| return to an earlier framing | rhyme: what changed | before/after, open/close |

Turn <= 24 deg, perspective visible. Dutch 1-2 deg and shake only for chaos; shake (k = 24t): `x = amp*(0.6 sin 1.7k + 0.4 sin(3.1k+1))`, `y = amp*(0.6 sin(2.3k+2) + 0.4 sin 0.9k + (rand(floor k) - 0.5)*0.1)`. Scene lengths: inserts about 0.5 s, regular 2-5 s, product reveal 4-5 s.

## Motion

Fast out, small overshoot, dead stop; no sine drift; continuous every frame. Freeze = about 1 s, only the camera moves.

| Action | Duration (s) | Ease | Notes |
|---|---|---|---|
| beat grid | 0.5 beat, 2 bar, 0.125 16th | | 120 BPM; start early, land on the beat; entrances on 16ths |
| landing | 0.3 | `back.out(1.3)` | GSAP back.out is the same curve |
| entrances (opacity in first 40%) | 0.3 | file `drop`, photo `power2.out`, notif `back.out(1.3)`, window `back.out(1.6)` | file 34u fall + 6u bounce; photo from (-700u, -120u) x dir, rot -0.6 dir -> seeded +-0.12; notif x +90u; window 0.94 -> 1 |
| product window parts appear | 0.45 | `power2.out` | bar from y -72, sidebar from x -360, status from y +56 |
| tremble before a freeze | 0.5-1 | per frame | +-2.4u: `x = a sin(61t+1.3k) sin(17t+k)`, `y = a sin(53t+2.1k) cos(23t+k)` |
| snap flight | 0.26-0.40 | arc + back 1.35 | `dur = 0.26 + 0.14*min(1, dist/1200u)` |
| caret blink | 0.5 on / 0.5 off | 0.1 soft edges | solid while typing or stunned |
| typing | 0.085/char x (0.7 + 0.6 rand), space x1.4 | `smooth` | each char fades 0.2, rises 4%, width grows so the caret slides |
| caret wind-up, press, spring | 0.5 (>= 8 frames), 2/24, 0.6 | `power2.in`, hold, `springEase(8,.3)` | sy 1.35 sx 0.8 -> sy 0.3 sx 2.4 -> 1 |
| caret shove | 0.25 | decay | `k = e^(-14d) sin(38d)`: x += 8u k, sx *= 1-0.1k, sy *= 1+0.1k |
| light sweep | 1.3 | `none` | starts at -0.35 so frame 0 is partly lit |
| background band | 1.9 | `power2.inOut` | -0.45 to 1.45 |
| window turn | 2.2 | `p => 1 - Math.pow(1-p, 2.4)` | rotateY 0.42 rad -> 0, scale 0.95 -> 1 |
| scramble | lock every 0.125 | damped | glyphs refresh 24/s; unit drops 18u in 0.22 `power2.out`, 0.1 after the last lock |
| key line, ruler | 0.3, 0.6 | `power2.out` | key line +0.35 after unit, rise 18u; ruler +0.1 |
| retract | 0.3 each, one per beat | `power2.in` | sidebar -> status bar -> title bar -> content |
| CRT-off | 0.22 | cubic in | see Techniques |
| check pop | 0.25 | `back.out(2.2)` | |
| minimum move | 0.25 | | the beat lives in landing times |

Cubic out/in/in-out = `power2.out` / `power2.in` (exits) / `power2.inOut` = cubic-bezier(0.65,0,0.35,1) (camera). Custom:
```js
const smooth = p => p*p*(3 - 2*p);
const springEase = (w = 9, z = 0.42) => p => { if (p <= 0) return 0; const wd = w*Math.sqrt(1 - z*z);
  return 1 - Math.exp(-z*w*p) * (Math.cos(wd*p) + z*w/wd*Math.sin(wd*p)); };
const drop = p => p < .7 ? -34*(1 - Math.pow(p/.7, 3)) : -6*Math.sin(Math.PI*(p - .7)/.3);  // y in u; tween proxy p "none"
```
Pile-up times: `t0 + (t1 - t0)*(1 - (1 - (i+0.5)/n)^(1/2.2))`, snapped to 16ths.

## Materials and light

- Stage: `#0A0B0F`; lift `radial-gradient(circle R at 50% 34%, rgba(21,24,34,.95), rgba(10,11,15,0))`, R = 0.75 x long side; cool pool at (78%, 12%), radius 900u, `radial-gradient(circle closest-side, rgba(59,76,202,.08), rgba(59,76,202,.028) 45%, transparent)`. Darken overlay `rgba(4,5,7,dark)` for freeze or ending. The light source is invisible, soft, cool.
- Vignette 50-55%: `radial-gradient(circle (1.05 x short side) at centre, transparent 33.3%, rgba(0,0,0,.55))`, above content, below captions.
- Grid: 48u, 1px white 3.2%, every 4th line 6% (as stacked gradients the major layer is 2.9%), a major line through the centre; inserts x1.4 at 0.8. For 1px lines at any zoom keep it outside `#camera` and set `background-size` (48u s, 192u s) and `background-position` from the camera `onUpdate`; hide below a 4px module.
- Panels: radius about 14u; `linear-gradient(180deg, top 0, fill min(h,60)u, fill)` with top = fill mixed 25% toward `#2A3040` (`#181C25` / `#1E232E` / `#242937`); 1px 8% white stroke; `inset 0 1px 0` 7% white; shadow `0 min(40, 6 + 0.06h)u min(90, 18 + 0.18h)u rgba(0,0,0,.55)`. Cheap shadow (crowds, inside the sweep): `2u (0.06h + 4)u 0 rgba(0,0,0,.35)`.
- Product window: stroke 7%, shadow `0 40u 120u rgba(0,0,0,.6)`; sidebar `rgba(255,255,255,.018)`, 8% border. Tile: radius 20%, `linear-gradient(180deg, #1F2430, #1A1E28)`, border 9% white, inset top 6%; lit: border accent at 0.25 + 0.6 lit, glow `0 0 10 lit u` accent 35% x lit.
- Lit SVG shape: `#262B38 -> #1A1E28 35% -> #12151C`, shadow dy 14 blur 20 50%, stroke 12%; accent: solid, glow 14 at 75%, top white 28% -> 0, stroke 35%.
- Light reveals: unlit 7%, lit stays full; glint <= 20% (hard white looks like a sticker); the product is opaque.
- Accent glow: caret `0 0 0.22h` at 90% + radial pool 0.9h at 22%; pill `0 0 0.6h` 35%; status dot r 5, `0 0 8u` 80%; check `drop-shadow(0 0 6u)` 70%. Trails additive (`mix-blend-mode: plus-lighter`). No grain, no scanlines.
- UI parts (original, HTML/SVG): app icons, radius 0.26 s, colour +15% white -> +25% black, white glyph (`#5B7CFA` `#8C6CF0` `#2FB6A6` `#E0A33A` `#4C9BE8` `#D0668A` `#6E7890`); red pill badge 20s tall; notification card (tier 2, radius 0.24h, icon 0.46h); file icon 44 x 56, fold 12, paper `#E9ECF3 -> #B9BFCC`, coloured extension tag; seeded landscape thumbnail; windows with a 40u bar (note, inbox, browser, sheet, chat, viewer, code); storage bar (grey to red 60-85%); check; pill; tabs. Brand mark = tier-3 square (radius 0.28 s) + accent caret 0.52 s.

## Technique library

Capabilities, not obligations; the content decides.

- **Light-sweep reveal (signature).** Three stacked full-frame copies of the opaque product (build once, clone twice): base at opacity 0.07; lit copy masked by a gradient edge at the sweep front; glint copy flattened to near-white (`filter: brightness(0) invert(.94)`), opacity 0.2, plus-lighter, masked to a band at the front. Angle 0.62 rad = CSS `125.52deg`; travel `L = W cos a + H sin a` (2190u at 16:9); soft edge 700u; band 360u. Inside: classes not ids (tweens hit all copies), cheap shadows. Add the background band (600 x 2800u, rotated 0.62 rad, `rgba(200,215,255,.045)` peak, plus-lighter, x -600u -> W + 600u) and the `#6F82C8` glow (radius 1100 s0 at window centre - 40u, opacity 0.1 min(1, 1.5p)).
  ```js
  const a = .62, soft = 700*u, band = 360*u, L = MV.width*Math.cos(a) + MV.height*Math.sin(a), sw = {p: 0};
  const mask = (el, g) =>  // lit, glint = the two masked copies
    el.style.maskImage = el.style.webkitMaskImage = g;
  const paint = () => { const f = -soft + sw.p*(L + 2*soft), a0 = f - 1.6*band, a1 = f + .4*band, done = sw.p >= 1;
    mask(lit, done ? "none" : `linear-gradient(125.52deg, #000 ${f - soft}px, transparent ${f}px)`); glint.style.opacity = done ? 0 : .2;
    mask(glint, `linear-gradient(125.52deg, transparent ${a0}px, #000 ${a0 + .7*(a1 - a0)}px, transparent ${a1}px)`); };
  tl.fromTo(sw, {p: .35/1.3}, {p: 1, duration: .95, ease: "none", onUpdate: paint}, 0);   // 27% lit at frame 0
  ```
- **Window turn.** Full-frame parent with `perspective: 2.2 x windowW x s`, origin at the window centre; window `rotateY(0.42rad)` (right edge far; "from -24 deg") -> 0, scale 0.95 -> 1, 2.2 s: `tl.fromTo(".dk-turn", {rotationY: 24, scale: .95*s0}, {rotationY: 0, scale: s0, duration: 2.2, ease: p => 1 - Math.pow(1 - p, 2.4)}, 0)`. Pitfall: perspective-rotated semi-transparent layers show vertical seams. Keep the rotated window opaque (no opacity on it or its children); do dimming and light in screen space by wrapping the reveal copies around the perspective parent.
- **Scramble number (signature).** Digit cells 0.6em (`.` `,` 0.3em), margin-right -0.045em. The k-th digit from the right locks at `start + k*0.125`; separators appear with the digit to their right. Unlocked digits show a glyph from `0123456789#%/*+=<>?$&@ABCDEFXZ`, seeded on (floor(24t), index), in three ghosts (0.85 / 0.3 / 0.14, raised 0 / 0.09 / 0.18em). On lock: `y = -0.12em e^(-14d) cos(40d)` for 0.3 s, opacity `min(1, 30d + 0.4)`.
  ```js
  const G = "0123456789#%/*+=<>?$&@ABCDEFXZ", st = {t: 0};
  tl.fromTo(st, {t: 0}, {t: dur, duration: dur, ease: "none", onUpdate() { const t = st.t, f = Math.floor(t*24);
    cells.forEach((c, i) => { const d = t - locks[i], scr = d < 0;   // c = {el, ghosts, sep}; t0 = start
      c.el.classList.toggle("scr", scr); c.el.style.opacity = t < t0 || scr && c.sep ? 0 : scr ? 1 : Math.min(1, 30*d + .4);
      if (scr) c.ghosts.forEach(n => n.textContent = G[Math.floor(mulberry32(f*977 + i*131)()*G.length)]);
      c.el.style.transform = `translateY(${!scr && d < .3 ? -.12*px*Math.exp(-14*d)*Math.cos(40*d) : 0}px)`; }); }}, 0);
  ```
  Optional 0-1 s ruler: 720u, 2px base 12%, 11 ticks (every 5th 12u at 30%, else 6u at 12%), 4px accent fill (`0 0 14u` glow 80%) to the target, mono 18u end labels. Screen pull 1.035 -> 1 in 2 s `power2.out`; grid 0.7; glow `#8CA0FF` 5% r 760u at (50%, 44%); vignette 0.5.
- **Generative pile-up -> snap to grid (signature).** Dozens to hundreds of seeded parts (never hand-placed), one entrance per kind, accelerating 16th-snapped times; trim or pad to the slot count. At the promise beat a ripple (2600u/s) leaves the press point; items take off as it reaches them. Assign: items by y then x, slots by row then column, each category into its own rows (nearest-slot flickers). Flight: `q = gsap.parseEase("back.out(1.35)")(p)`, position = lerp(from, to, q) + normal x `sin(pi min(q,1)) 0.18 dist` (same sign per category, arcs never cross); scale `power2.out`; rotation to 0 by `springEase(9,.42)(min(1,1.2p))`; morph into the tile by `smooth` over p 0.15-0.7. Trail: 9 SVG segments at `p - 0.35(1 - k/9)`, width 6u (0.25 + 0.75k/9), alpha 0.9 (k/9)^2, `drop-shadow(0 0 6u)`, plus-lighter, flow colour. Tile edges flash accent behind the ripple.
- **Caret performance.** Accent bar, width max(2px, h/14), radius min(w/2, 4 + 0.01h); metronome and brand. Blink, solid, shove, wind-up, press, spring back (Motion) via `scaleX/scaleY` at its centre. Typed text is its own caption. **Freeze**: all stops about 1 s after a tremble; only the camera pushes.
- **Retract -> rim light -> CRT-off (signature ending).** One part per beat (sidebar out left 360, status down 56, bar up 72, then content); the window never dims as a whole. Rim: border 1.5u accent 90% a, `0 0 24a u` 35% a, `inset 0 0 16a u` 15% a. CRT-off squashes the real picture (never a flat block), origin at the caret: q = p^3 over 0.22 s; vertical squash to a line first, then horizontal to a point, brightening only in the last 45%; leaves the caret.
  ```js
  const c = {p: 0}, cl = x => Math.min(1, Math.max(0, x));
  tl.fromTo(c, {p: 0}, {p: 1, duration: .22, ease: "none", onUpdate() { const q = c.p ** 3, k = cl((q - .55)/.45);
    gsap.set(".dk-crt", {scaleY: Math.max(.004, 1 - .996*cl(q/.6)), scaleX: 1 - .995*k*k*(3 - 2*k),
      filter: q > .55 ? `brightness(${1 + 3*k})` : "none", opacity: q >= 1 ? 0 : 1}); }}, tOff);
  ```
- **Type flow; inserts.** Flow-coloured arcs per category row (inbox sorting, log routing). Inserts (0.5 s): tabs splitting, a red badge at x6, a storage bar turning red.

## Transitions

No fades, no black frames; shots change by UI expanding or collapsing.
- Between scene files: `cut` only, never `fade`; a scene expands from where the previous one collapsed.
- **Iris expand** (default; in-scene; 15 frames = 0.5 s = one beat; `power2.out`): the incoming shot grows from a centred rounded rect 0.14W x 0.14H (radius 16u -> 0) to full frame over the still outgoing picture. Start one frame early so the beat frame is about 80% open. Build it over a rebuilt still of the previous composition, or keep both states in one file. No key text in the first 0.4 s.
  ```js
  const W = MV.width, H = MV.height, ir = {q: 0}, nxt = document.querySelector("#shotB");
  tl.fromTo(ir, {q: 0}, {q: 1, duration: .5, ease: "power2.out", onUpdate() { const k = 1 - ir.q;
    nxt.style.clipPath = `inset(${.43*H*k}px ${.43*W*k}px round ${16*u*k}px)`; }}, tBeat - 1/30);
  ```
- **Collapse out** (endings, closing a section; 15 frames): the outgoing picture runs the CRT-off centred in the first 70% (0.35 s) with the next shot visible around it (collapsing layer on top). In-scene only.
- Gaps show `#0A0B0F`. Scene code never fades its whole frame.

## Captions

A UI part: dark translucent pill bottom centre, 1px 9% white stroke, accent dot on the left. Box stays, text swaps; width from the longest line; text shrinks, never truncates; trailing commas and full stops dropped. First line: 12u rise, 0.25 s fade; last line holds >= max(1.8 s, spoken + 0.6 s); box leaves 0.6 s after the last line. Height 1.9 x size, fully round; text inset 1.5 x size left, 0.9 x size right; dot 0.28 x size at 0.95 x size, glow `0 0 10u` 80%. Background .72 (.88 in chaos). Max width 86% W (portrait 90%). Captions in every scene except the big-number screen; typed lines are not repeated.

```css
--cap-bg: rgba(12,14,19,.72); --cap-fg: #E8EAF0; --cap-font: <sans stack>; --cap-size: calc(40 * var(--u));
--cap-radius: calc(38 * var(--u)); --cap-bottom: calc(72 * var(--u));   /* portrait: 220u */
--cap-weight: 500; --cap-accent: var(--dk-accent); --cap-border: 1px solid rgba(255,255,255,.09);
--cap-shadow: 0 calc(10*var(--u)) calc(40*var(--u)) rgba(0,0,0,.5);
```

## Image-gen guidance

Only when image generation is on; images are content inside UI parts, never the stage.
- Anchors (start every prompt): "dark keynote software launch aesthetic, near-black blue-gray stage", "cool soft ambient light, faint cobalt blue glow from top right", "clean crisp digital image, no grain, no noise, no scanlines", "muted cool neutral palette, slightly blue shadows, low saturation", "minimal composition, single subject, generous negative space", "small rounded thumbnail image, studio-clean, precise and calm"; end with "no text, no watermark"; cut-outs add "isolated on plain white background".
- Subjects: landscapes for thumbnails and viewers (sky, mountains, sea, distant city); abstract or blurred avatars (no real identity); ordinary previews (food, objects, interiors); a low-contrast cool bokeh far behind the window.
- Never generate: UI parts, text, numbers, grids, light effects, brand marks, real OS chrome or app icons, charts.
- Post-treatment: small rounded thumbnails in photo tiles, viewers or avatars, 1px 8% stroke and soft shadow; desaturated, cool blue-grey, highlights down, no grain; lit by the sweep like everything else.

## Taboos

- No physical product, no glass; no real OS chrome (red/yellow/green buttons, menu bar, dock) or real app screens and icons.
- No scanlines, HUD, grain or noise; no charts (one number, at most one ruler).
- No fades, no black frames; "close" squashes the real picture, never a flat block.
- Never two accents on screen; no red after the turn; no flow colours on still UI.
- Big number never fully accent, never above weight 600, never under a caption.
- No sine drift; no move under 0.25 s; dutch angle and shake only for chaos.
- No hard white highlight; sweep <= 1.3 s, window fully lit after it; no perspective on semi-transparent layers.

## Known gaps

- Fonts: original Inter, Inter Tight 600 (number), JetBrains Mono (labels); here SF Pro and SF Mono, CJK PingFang SC; CJK sizes inferred.
- No whole-object motion blur on the snap flight; only trails.
- The runtime draws captions: a caption-free number screen needs a scene without narration (duration by hand); the 25u lift is manual.
- Iris and collapse cannot span scene files; in-scene they need the outgoing picture rebuilt.
- The three-copy sweep and depth blur are render-heavy. Portrait is inferred. No sound design.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
