# Style: iso-infographic (Isometric Infographic)

Units: `u` = frame short side / 1080. At 1920x1080, 1u = 1px. In CSS write `calc(N * var(--u))` (see `iso-infographic.css`). In GSAP use `const u = MV.u;` and tween `y: 24 * u`. Every size below is in u; world sizes are in world units (camera `k` = u per unit).

## Identity

A floating isometric sandbox on a beige dot-grid ground. Buildings and machines are flat three-tone blocks (light top, mid left face, dark right face) with no outlines. Thin ink lines belong only to the information layer: callout pins, leader lines, dashed routes, section lines and tracking rings. Quantities are rows of identical small icons; shells slide open to show the inside; the tracked subject stays red while the rest can fade to warm grey. The camera only pans and zooms. Identity rules, all mandatory: true 30-degree isometric, never rotated or in perspective; three flat tones per solid, no outline, gradient or real shadow; ink lines for information only; one colour = one role for the film, red only for the subject; ground and plinth from the first frame; only keywords and numbers on screen.

Best for: how-it-works explainers, processes, supply chains, system architecture, infrastructure, annual-report numbers, "follow one thing through the system". Not for: emotional brand films, people-centred drama, UI demos, photo-led stories, realistic rendering.

## Palette

| Hex | Role |
|---|---|
| `#F2E6D0` | ground, full-frame base under everything (also the colour transitions fade to) |
| `rgba(46,37,34,.10)` | ground dot lattice |
| `#FBF4E6` | paper: cards, label halos, caption box, hatch background |
| `rgba(251,244,230,.94)` | text halo stroke |
| `#2E2522` | ink: every line, text, pin, route, section line |
| `#5A4E48` / `#4A3F3A` | soft ink: plain lines, notes, legends / sub-titles, questions |
| `#F7EEDC` | inset (magnifier) background |

Solids, three tones each as top / left (+y, base) / right (+x), with the default role:

| Name | Tones | Role |
|---|---|---|
| red | `#CF5140 / #B3352B / #8C2620` | subject: the one tracked thing, current focus |
| blue | `#5A9BCB / #3C7FB1 / #2D6390` | water |
| green | `#7BA75F / #5E8C4A / #476F37` | nature |
| soil | `#A77850 / #8A5B3A / #6A432A` | earth, strata |
| mustard | `#E9BE5C / #D9A53A / #B0832A` | energy |
| stone | `#CFCABF / #B8B2A6 / #948E83` | machines, generic buildings |
| cream | `#FBF4E6 / #F2E6D0 / #D9C8A9` | base surfaces, plinth top |
| blue-grey | `#99A4B4 / #7F8FA8 / #6B758D` | data, done stations |
| deep blue-grey | `#7F8794 / #5F6B80 / #52596E` | lower tech strata |
| teal / violet | `#72B5A9 / #4FA59A / #458682`, `#A388CB / #8C6CC4 / #755AA3` | chemistry / light |
| copper / coral | `#D49565 / #C97C45 / #A46640`, `#E9AD87 / #E39A6F / #B97E60` | metal / spare |
| graphite | `#7E797A / #5E5A5F / #514C54` | racks, heavy neutral kit |
| off | `#E2D6C0 / #CDBFA6 / #B9AA90` | unlit cells, future stations |

Tone rule for any new base colour c: top = mix(c, `#FFF7E6`, 0.20); left = c; right = mix(c, `#231A2C`, 0.22) (shadows lean slightly cool). Unlit icons use `#E6DCC8 / #DCD0BA / #CFC2AA`. Small details: LED `#7FD6A4`, skin `#C98B62`, trunk `#8A5B3A`.

Colour is meaning: assign one colour per role before building and keep it all film; red only for the subject; neutrals for what must not compete. No accent alternatives: the role map is the palette.

## Type

Font stacks (system fonts only):
- Text (CJK and Latin words), titles and labels 700, notes 500: `-apple-system, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Noto Sans CJK SC", "Hiragino Sans GB", sans-serif`.
- Numbers, chapter numbers, Latin abbreviations (geometric face): `Futura, "Avenir Next", "PingFang SC", sans-serif`; counters may use `"Avenir Next"` with `font-variant-numeric: tabular-nums` for stable width.

| Role | Size | Weight / details |
|---|---|---|
| screen title / shot title | 70 | text 700, halo 10 |
| big number | 120 (100-130) | number face 400; unit 0.4 x size, text 700, gap 14 |
| term (term card) | 50 | text 700 |
| caption; sub-title, question | 42 | text 500; sub-titles `#4A3F3A` |
| plain line, body | 39 | text 500 `#5A4E48` |
| kicker above a title | 38 | text 700, red right tone `#8C2620` |
| label, pin title | 36 | text 700, tracking 1u |
| pin number | 54 | number face 400; unit 0.5 x size, text 500, gap 10 |
| page header | 32 | "02 / 05" number face 600 tracking 2u, title text 700 |
| small, legend, station name | 30 (minimum on screen) | text 500 `#5A4E48` |

All screen text has a paper halo: SVG `stroke: rgba(251,244,230,.94)`, width max(6u, 0.2 x size), round joins, `paint-order: stroke`. Pin titles 2-6 characters; plain lines <= 15 CJK characters; `white-space: nowrap`.

## Layout grid

- Frame 1920x1080. Safe area 5%: x 96-1824, y 54-1026. Caption zone: bottom 170u (y >= 910) is reserved; no subject, pin or number enters it. Content area x 96-1824, y 54-910.
- World anchor (the screen point the camera centres on): (960, 485) = centre of the area above the caption zone. With a number column on the right, move it to (640, 520).
- One plinth per scene, 50-70% of frame width, its shadow fully visible. Subject at least 1/3 of frame height at the key moment.
- Page header fixed top-left: x 96, baseline y 90.
- Number column (big number, icon row, progress bar): x about 1150-1800, y about 150-800, left-aligned, at least 60u between items.
- Per screen at most: 1 header + 3 pins + 1 term card + 1 number group. Pins pick side and up/down so they never cover the subject.
- Portrait (inferred): anchor (W/2, 0.40 H), plinth 80-90% width, number column moves below the plinth above the caption zone.

## Camera grammar

The camera is {x, y, z, k}: the world point on the anchor, k = u per world unit. With X = x - cam.x, Y = y - cam.y:
`sx = ax + (X - Y) * cos30 * k`, `sy = ay + ((X + Y) * 0.5 - (z - cam.z)) * k`. World x runs lower right, y lower left, z up; nearer = larger x + y + z. Visible faces: top, +y (left), +x (right).

| Shot | Expresses | How |
|---|---|---|
| wide / station / close-up | system / one step / a detail | k 30-45 / 45-80 / >= 100 |
| slow push | keeps the frame alive, going deeper | k up 5-10% per 5 s (e.g. 37 -> 41 over 5.6 s, 44 -> 46), every scene |
| keyed pan + zoom | move to the next station on the same board | keys {t, x, y, z, k}, `power2.inOut` between keys, log-space k, position weighted by 1/k |
| push in / pull out | enter a detail / return to the summary | log zoom, in-scene (see Transitions) |
| focus + context / tracking / inset | "this one" / follow one thing / system and individual together | desaturate the rest; ring follows the subject; magnifier with its own camera |

Never rotate, tilt or add perspective; no dutch angle, no shake.

Exact log zoom between two keys a and b (proxy p tweened 0 -> 1, `power2.inOut`):
```js
const k = Math.exp(lerp(Math.log(a.k), Math.log(b.k), p));
const w = Math.abs(1/a.k - 1/b.k) < 1e-9 ? p : (1/a.k - 1/k) / (1/a.k - 1/b.k);
cam = {x: lerp(a.x, b.x, w), y: lerp(a.y, b.y, w), z: lerp(a.z, b.z, w), k};
```
Apply it either by re-projecting every polygon and label in `onUpdate` (exact), or, for a push on a fixed centre, by building the world once at k0 in `#camera` and setting `scale = k/k0` with `transform-origin` at the anchor (ink lines `vector-effect: non-scaling-stroke`; overlay anchors re-projected). Slow push: tween `{lk}` from `Math.log(k0)` to `Math.log(k1)`, `ease:"none"`, `k = Math.exp(lk)`.

## Motion

Rhythm grid 100 BPM: beat 0.6 s, eighth 0.3 s, sixteenth 0.15 s, bar 2.4 s. Land events on the grid.

Eases: out = `power2.out` (cubic out, entrances); in = `power2.in` (exits); inOut = `power2.inOut` (camera, growth); back = `back.out(2.2)` (pops); backSoft = `back.out(1.4)` (inset opening); soft = cubic-bezier(0.45,0,0.55,1) ~ `power1.inOut` (text wipes); gravity = `power1.in` (falling).

| Action | Duration | Ease / details |
|---|---|---|
| drop (default solid entrance) | 0.45 fall from 6 units up + 0.7 settle | fall `power1.in`; then squash, see settle function |
| rise (towers, tanks, racks) | 0.5 | height scale 0 -> 1, `power2.out`, anchored at the base |
| pop (small items) | 0.35 | uniform scale 0 -> 1, `back.out(2.2)` |
| slide in | 0.5 | from a world offset (default 4 units along -x), `power2.out` |
| exit (sink / lift / pop / slide) | 0.45 | `power2.in`; lift rises 8 units; slide default 6 units up |
| emphasis hop | 0.4 + 0.7 settle | z + sin(pi q) x 0.6 units, then settle amount 0.12 |
| pin / leader / term card | 0.7 / 0.4 / 0.3 + 0.7 | see Technique library |
| count / icon step | 1.2 (to 1.6) / 0.15 (fast 0.08) | `power2.out` / `back.out(2.2)` per icon |
| pipe grow / route draw | 0.9 / 1.2-1.4 | `power2.inOut` |
| section line, shell peel / inset | 0.3 + 0.35 / 0.8 | `power2.inOut`, `power2.out` / `back.out(1.4)` |
| plinth rise (optional) | 0.9 from 4 units below | `power2.out`, visible on frame 0 |
| overlay exit / desaturate / fade to ground | 0.45 / 0.6 / 0.6 | `power2.in` / `power2.inOut` |

Settle after landing (q = 0..1 over 0.7 s): `s = A * Math.exp(-4.2*q) * Math.cos(q*Math.PI*3.2)` with A = 0.18 (drop) or 0.12 (hop); height x (1 - s), width and depth x (1 + 0.5 s). As a GSAP ease on a 0 -> 1 proxy: `ease: q => q` and compute s in `onUpdate`.

Typical 5 s scene: 0 ground and plinth; 0.3 main block drops; 0.5 tank rises; 0.9 pipe grows; 1.2-1.5 flow and first pin; 2.6 term card; all in place by 3 s, then slow push.

Rules: the line goes first (route before the mover, leader before the inset, section line before the peel). Solids enter by movement, never opacity. Accumulation accelerates: beat, then eighth, then sixteenth. A new event every 2-4 s on the keyword; one reveal in the second half; nothing new in the last 0.5 s. Count and icon row finish together.

## Materials and light

- Ground: flat `#F2E6D0` with an isometric dot lattice: tile 78.57u x 45.36u (spacing 4.2% of short side; tile = 2 x spacing x cos30 by spacing), dots radius 1.73u at tile corners and centre, `rgba(46,37,34,.10)`. The lattice may drift with a camera pan. No vignette (0%), no gradient, no grain.
- Light from the upper left, shown only by the tones. Each polygon is also stroked in its own fill, 0.8 px, round joins (hides seams; not an outline).
- Non-axis faces (cylinders, cones), unit normal n: colour = (top max(nz,0) + left max(ny,0) + right max(nx,0)) / sum of weights; cull when nx + ny + nz <= 0.
- Sphere: left-tone disc; clipped inside, a right-tone ellipse at (+.62R, +.28R) radii 1.02R x 1.1R, then a top-tone cap at (-.28R, -.55R) radii .9R x .72R.
- Plinth strata, top down: tech = cream 0.45 / blue-grey 0.8 / deep blue-grey 0.5; cream = cream 0.5 / soil 0.9 / `#6A432A` 0.6; grass = green 0.45 / soil 0.9 / `#6A432A` 0.6; paper = cream 0.5 / `#D9C8A9` 0.7. Shadow: footprint offset +1.2 units in x and y, 2 units below the bottom, `rgba(46,37,34,.13)`, blur stdDeviation clamp(0.8 k, 6, 40).
- Section hatch: SVG pattern 7 x 7 user units, `patternTransform="rotate(-45)"`, paper `#FBF4E6` fill, one ink line `rgba(46,37,34,.55)` 1.4 wide; hatched faces get a 1.5u ink border.
- Ink lines 2u (pins, leaders, routes, borders); section lines 2.5u dashed 10 7; routes 3u dashed 12 9 over a paper under-stroke 7u `rgba(251,244,230,.9)`.
- Cards: paper `#FBF4E6`, radius 11u (legend card 14u), 2u ink border, hard offset shadow (5u, 7u) `rgba(46,37,34,.10)` with no blur.
- Block details = flat quads on the front faces: windows (floor(h/0.9) rows x floor(w/0.8), mix(right tone, `#2E3A4C`, .45)); rack slots (floor(h/0.32), mix(left tone, `#1C1820`, .35), LED `#7FD6A4` lit while sin(9t + 2.3 slot + 1.7 x) > -0.2); vents; red waist stripe at 62-72% height.
- Desaturate (focus + context), d = 0..1: l = 0.3r + 0.55g + 0.15b; grey = mix([l,l,l], `#E9DFCB`, 0.45); colour = mix(c, grey, d). Overlays are never desaturated.

## Technique library

Capabilities, not obligations; the content decides which to use.

- **Isometric box from three polygons (the core).** Box at corner (x, y, z), size w x d x h, X = x+w, Y = y+d, Z = z+h: top `[x,y,Z][X,y,Z][X,Y,Z][x,Y,Z]`, left `[x,Y,z][X,Y,z][X,Y,Z][x,Y,Z]`, right `[X,y,z][X,Y,z][X,Y,Z][X,y,Z]`, each in its tone. Painter's order: by layer first (-1 shadows, 0 plinth and ground decals, 1 solids, 2 routes, 3 section strokes); inside a layer, for each pair whose screen bounds overlap, A is behind B if A.x1 <= B.x0, else A.y1 <= B.y0, else A.z1 <= B.z0 (and symmetrically); sort topologically, always emitting the ready item with the smallest centre depth x+y+z; break cycles by smallest depth. Never let boxes interpenetrate; split pipes into pieces <= 1.4 units.
  ```js
  const C = Math.cos(Math.PI/6);
  const P = (x,y,z) => [ax + ((x-cam.x)-(y-cam.y))*C*cam.k, ay + (((x-cam.x)+(y-cam.y))*.5 - (z-cam.z))*cam.k];
  const poly = (pts, f) => `<polygon points="${pts.map(p=>P(...p).map(v=>v.toFixed(1)).join(",")).join(" ")}" fill="${f}" stroke="${f}" stroke-width="0.8" stroke-linejoin="round"/>`;
  function box(x,y,z,w,d,h,[top,left,right]){ const X=x+w,Y=y+d,Z=z+h;
    return poly([[x,y,Z],[X,y,Z],[X,Y,Z],[x,Y,Z]],top)+poly([[x,Y,z],[X,Y,z],[X,Y,Z],[x,Y,Z]],left)+poly([[X,y,z],[X,Y,z],[X,Y,Z],[X,y,Z]],right); }
  ```
  Drop and rise re-run `box()` in `onUpdate` (z + 6(1-p); h x p). Pop and slide are exact CSS transforms: scale about the projected base centre; world offset (dx, dy, dz) = screen ((dx-dy) C k, ((dx+dy)/2 - dz) k).
- **Callout pin.** Over 0.7 s: dot 0-36% (r 7u, paper fill, 2u ink stroke, `back.out(2.2)`; inner dot 0.42 r in ink, `#B3352B` for the subject); vertical leader 20-60% (120u up, negative = down); horizontal 45-80% (56u); text 60-100% wiped by a clip rect. Text starts 12u past the leader; title baseline 12u above the elbow, number below it.
  ```js
  tl.fromTo(dot, {scale:0}, {scale:1, duration:.25, ease:"back.out(2.2)", svgOrigin:`${px} ${py}`}, t0)
    .fromTo(vLine, {attr:{y2:py}}, {attr:{y2:py-120*u}, duration:.28, ease:"power2.out"}, t0+.14)
    .fromTo(hLine, {attr:{x2:px}}, {attr:{x2:px+56*u}, duration:.25, ease:"power2.out"}, t0+.32)
    .fromTo(clipRect, {attr:{width:0}}, {attr:{width:900*u}, duration:.28, ease:"power1.inOut"}, t0+.42);
  ```
- **Term card.** On a term's first use. Leader from the world point to the nearest card edge (0.35 s); card 0.3 s later: border draws (first 35% of 0.7 s), text wipes (25-90%). Legend cube 13u in the term's role colour at 30u, term 50u at 54u, plain line 39u at 26u. Width max(term + 86u, plain + 52u), height 150u (92u without plain line).
- **Big number.** Label 36u 700 with a 2.5u underline (<= 70u); count 1.2 s `power2.out`, rise 24u in 0.5 s, then a 0.35 s pulse to 1.06; optional 10u role-colour bar; note 32u.
- **Icon row (quantities).** Identical icons, up to 10 per row, cell 40-46u, icon 0.86 x cell; lit ones pop one per step, the rest unlit; legend 30u ("1 = 1,000 servers"). Flat three-tone icons: cube, chip, person, server, document, coin, drop, bolt, sun.
- **Progress bar.** 26u, fills 1.2 s `power2.inOut`; pill (`#E2D6C0` track, 2u ink outline) or cells with 6u gaps; role-coloured segments summing to the total.
- **Pipe with flow.** Axis-aligned, r 0.22, stone, sphere joints 1.2 r; grows, then 4 role-colour balls (0.9 r) move at 3 units/s.
- **Cut-away.** Dashed section line 2.5u (10 7) runs along the shell edge in 0.3 s with a 5u ink dot at its head; the opened walls (top, +x, +y) slide 6 units along their outward normal in 0.35 s, fading out between 10% and 75% of the slide; cut edges then fade in hatched. Wall 0.2; shell `#E3DACA / #DCD3C3 / #B3AAA2`; inside parts rise right after, stagger 0.08.
- **Layer stack and steps.** Slabs (h 0.7, gap 0.25-0.3) drop one per eighth with side tags (70u leader, 34u label); explode spreads them over 0.8 s. Bar steps rise one per eighth along -y (taller ones behind); last one red.
- **Route and tracked subject.** Route draws, dashes crawl 30u/s (`strokeDashoffset` linear); the red subject sphere follows 0.4 s later (`power2.inOut`) inside a dashed ring (r 44-56u, 2.6u, dash 0.35r 0.22r, crawling 0.6r/s, grows in 0.45 s) with a 32u tag.
- **Roadmap.** Iso cubes (13u, current 20u) on a dashed line (10 8) in a paper panel (radius 13u, stroke `rgba(46,37,34,.35)`): done blue-grey, current red in a crawling ring r 32u, future off; names 30u; line 0.8 s, cubes pop at +0.15 + 0.06 i.
- **Inset.** Leader, then a circle (r 200u) opens: shadow (6u, 9u) `rgba(46,37,34,.14)`, bg `#F7EEDC`, 3u ink ring + dotted 1u ring at r + 7; inside, a second world with its own camera or an image.
- **Chapter card.** 4-6 s, camera k 48 pushed 2% over 6 s. Small plinth (8 x 5, tech) rises 4 units in 0.8 s; red extruded number "03" (5.6 units tall, 0.8 deep, x-z plane) pops at 0.3 s over 0.6 s; at (0.53 W, 0.36 H) the 70u title wipes in at 0.55 s under a red 38u kicker, then an 86u ink rule and the question (42u, at 1.6 s + 0.25 s per line, 12u rise); roadmap at the bottom lit to this chapter.
- **Extruded iso text.** SVG text with a plane matrix (x-z `matrix(C*s,.5*s,0,s,sx,sy)`, x-y `matrix(C*s,-.5*s,C*s,.5*s,sx,sy)`) repeated along the depth in 1.5u steps: back half right tone, front half left tone, last copy top tone.
- **Header, legend card, props.** Header "02 / 05 — Title" on every non-chapter scene. Closing legend card (560u): title 52u, total 84u with a cell bar, colour legend, note "Illustrative, not to scale" 24u. Props: round trees, 9-sided pines, faceless people 1.6 units (12 fps arm steps), seeded scatter, min spacing 0.8.

## Transitions

The project contract offers `cut` or `fade` (0.4 s crossfade). This style never crossfades scenes (ghosting), so use `cut` everywhere and build style transitions in-scene. Source length 18 frames (0.6 s at 30 fps): an outgoing half and an incoming half that barely overlap.
- **Same stage, continuous camera (preferred):** `cut` with matched composition: the last camera key of scene A equals the first of scene B.
- **Cream fade (change of stage or topic, the default):** outgoing last 10 frames (0.33 s): a full-frame `#F2E6D0` layer opacity 0 -> 1, `power2.inOut`; `cut`; incoming first 10 frames: the content layer above the ground opacity 0 -> 1, y 24u -> 0, `power2.out`. The ground never moves; only here may frame 0 show bare ground.
- **Iso push / pull:** outgoing last 0.33 s: `#camera` scale 1 -> e^0.3 = 1.35 (push) or 1 -> e^-0.3 = 0.74 (pull) with `power2.inOut` while the cream layer rises; incoming first 0.33 s: scale e^-0.18 = 0.835 -> 1 (push) or e^0.18 = 1.197 -> 1 (pull), opacity 0 -> 1, `power2.out`.
  ```js
  const T = SCENE_DUR - .33;
  tl.fromTo("#camera", {scale: 1}, {scale: Math.exp(.3), duration: .33, ease: "power2.inOut"}, T)
    .fromTo(".iso-cream", {opacity: 0}, {opacity: 1, duration: .33, ease: "power2.inOut"}, T);
  ```
- **Chapter end:** the last 0.6 s fades the frame to ground (`power2.inOut`), then `cut` to the chapter card.
No white flash, rotation, blur, slide or crossfade transitions.

## Captions

Captions are map labels: paper card, 2u ink border, red isometric legend cube (12u) on the left, ink text 42u 500, bottom centre 64u up. Height 1.82 x size, padding 30u, 46u cube slot; width fixed to the longest line (max 90% W). The border draws once (0.25 s, `power2.out`); then only the text changes, each line wiping in left to right in 0.35 s (`power1.inOut`) while the previous fades in 0.2 s. Commas and full stops are hidden. The box leaves 0.3 s after the last line (8u drop, `power2.in`).

```css
--cap-bg: rgba(251,244,230,.96); --cap-fg: #2E2522; --cap-font: <text stack>; --cap-size: calc(42 * var(--u));
--cap-radius: calc(10 * var(--u)); --cap-bottom: calc(64 * var(--u));   /* portrait: 200u */
--cap-weight: 500; --cap-accent: #B3352B; --cap-border: calc(2 * var(--u)) solid #2E2522; --cap-shadow: none;
```

## Image-gen guidance

- Anchors: "isometric infographic illustration, true 30-degree isometric projection", "flat three-tone shading, light top face, mid left face, dark right face, no outlines", "warm beige background #F2E6D0 with faint isometric dot grid", "floating diorama plinth with layered soil strata", "muted palette of stone, graphite, blue-grey with a single red accent", "clean vector style, no gradients, no realistic shadows, no perspective"; end with "no text, no watermark" (cut-outs: "isolated on plain white background").
- Subjects: organic things (crops, food, animals), small people and crowds, vehicles and machines boxes cannot build, trees and rocks, inset detail pictures.
- Never generate: charts, numbers, bars, icon rows, any text, plinths, blocks, pipes, routes, pins, leaders, section lines, rings.
- Post-treatment: remove background, quantise to the palette, repaint as three tones without gradients or shadows, place at true isometric angle and block scale (or in a paper inset), tie back with ink leaders.

## Taboos

- No camera rotation, tilt, perspective, shake; no realistic 3D rendering.
- No outlines, gradients, real shadows or highlights on solids; no non-information ink lines.
- Solids never fade with opacity (cut-away shell excepted).
- No narration sentence on screen; nothing but captions in the bottom 170u.
- One hue never means two things; red never on a supporting object.
- No empty opening frame; no pin covering the subject; no more than the per-screen limit.
- No interpenetrating geometry (pipe through tank).
- No brand logos (name as text); no white flash, blur, slide or crossfade; no vignette or grain.

## Known gaps

- Numbers used a bundled geometric face (Jost); Futura / Avenir Next are close, but Futura digits are narrower and not tabular: fix counter widths to the final value.
- Occlusion is a painter's sort; interpenetrating parts still sort wrong.
- The engine (projection, shading, sort) is inline script per scene; text widths are measured once (`getBBox`) before building the timeline.
- Style transitions are split across a `cut` (cream fade, push) instead of one overlapping 18-frame transition.
- Sound (music ducking) is not part of the picture; portrait layout is inferred.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
