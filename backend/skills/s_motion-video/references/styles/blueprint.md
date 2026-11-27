# Style: blueprint (Blueprint)

Units: `u` = frame short side / 1080 (CSS `calc(N * var(--u))`, GSAP `MV.u`). The drawing lives on one sheet in sheet units: 1 sheet unit = 1u at camera zoom 1. Tags: (m) measured in the reference's code or stills, (s) seen, (i) inferred.

## Identity

The whole film happens on one sheet of Prussian-blue cyanotype paper, and the camera reads it like an eye moving over a drawing. Identity rules, all mandatory: (1) blue and white only: an uneven blue sheet (mottled coating, faint horizontal streaks, quarter-fold creases with worn pale specks) and pale blue-white line work that varies in weight, never in hue; one rubber-stamp colour may appear once per film for the verdict. (2) Everything is technical line drawing: outlines over paper-coloured fills (front parts hide what is behind), four line weights, centre, hidden and phantom dash conventions, 45-degree hatching, dimension lines with arrowheads and numbers, numbered balloons on leaders; flat elevation or oblique projection, never rendered 3D. (3) Things appear in drafting order (centre lines, outlines, detail, hatching, dimensions, balloons, labels), drawn along their length at constant pen speed with a small glowing nib at the head, each layer starting on a beat. (4) The sheet carries its furniture: a hand-lettered capital header with a double underline, FIG. labels that are struck through and rewritten, a GENERAL NOTES column, a ruled title block whose STATUS field holds the verdict. (5) The designed object is the character; people appear at most as a hand shadow or a signature. The camera only pans and zooms over the sheet, with hard cuts on downbeats.

Best for: engineering and hardware explainers, mechanisms, product anatomy and exploded views, specs with dimensions, plans, revisions, launches that end in a verdict. Not for: people stories, UI walkthroughs, photo-led content, multi-series charts, colourful brands.

## Palette

| Hex | Role |
|---|---|
| `#1D3C77` | sheet as rendered (measured mean (m)); flat base `#1B3A75` under the grain overlay, also the knock-out fill |
| `#18336B` -> `#2A58A6` | coating range: low-frequency mottle, a slight left-dark to right-light fade (m) |
| `#162C58` | under a lifted object's shadow, wet-stain core (s) |
| `#809ECC` | sun-bleached edges, worn fold specks |
| `#E7F0F9` | ink: every line and letter (renders about `#C8D4E5` on the paper (m)) |
| `rgba(231,240,249,.72)` / `.38` | soft ink (field labels, thin text) / faint ink (construction lines, trails, imprints) |
| `#0D1116` | drafting cloth, full-sheet wides only; sheet shadow (10u, 16u) blur 30u at .55 |
| `#C23B2E` | stamp ink, the one non-blue colour, exactly once per film |
| `rgba(12,28,62,.58)` | note box |

Accent rule: no accent hue; emphasis is weight, a nib, a balloon or a dimension; the stamp lands once. Alternatives (whole film): sun-bleached `#3A6DA8 -> #5B8CC4`; deep `#0F2350 -> #1D3F80`; a green (`#2E8B57`) or black stamp instead of red.

## Type

- Hand lettering (header, labels, notes, dimensions, balloons, captions), always capitals: `"Noteworthy", "Chalkboard SE", "Bradley Hand", "Hannotate SC", "HanziPen SC", "Kaiti SC", cursive`.
- Technical sans (title-block fields, status, sub-lines, view names), 700, capitals, tracked: `"DIN Alternate", "Avenir Next Condensed", "Avenir Next", "PingFang SC", sans-serif`.
- Script, signatures only: `"Snell Roundhand", "Apple Chancery", "Savoye LET", cursive`.

| Role (sheet units at zoom 1) | Size | Details |
|---|---|---|
| sheet header | 96-112 | hand caps, tracking 12; underline outline weight 22u below the baseline + hair 10u below that |
| sub-line, view name | 26 | tech 700, tracking 5 |
| FIG. n | 64 | hand, tracking 4; detail-weight rule 20u below, sub-line 26 tech under it |
| labels, notes column lines | 28-34 | hand caps, tracking 2, line pitch 1.5 x size; numbered "1." |
| dimension numbers | 26 | hand, centred, rotated with the line, 8u off the line |
| balloon numbers | 24-26 | hand, centred in a circle r 22-24 |
| title-block values | 40 (title), 26-28 (fields), status 34 tech 700 | field labels 16 tech, tracking 1.5, soft ink, 18u under the cell's top rule |
| signature | 46 | script |

Minimum 24u except field labels; never auto-wrap.

## Layout grid

- Sheet bigger than the frame (2880 x 1800 for 1920 x 1080), folds on its centre lines; keep the figure 960/zoom from any framed sheet edge.
- Medium shot (the default): figure 55-80% of frame height, left or centre; empty paper ("sky") on one side. Header top-left (x >= 96, baseline about 160); right column x 1290-1820: GENERAL NOTES at the top, FIG. label in the middle, title block (about 520 x 210) bottom-right with its bottom at y <= 885.
- Caption band: the bottom 16% (y >= 907) has no text; a ground line, base or hatching may run through it.
- Per screen: one figure, <= 4 balloons (in empty paper, leaders never cross), <= 2 dimensions, <= 4 note lines, one FIG. label, one title block. Explode sideways and down, not into the header.
- Portrait (i): sheet 1800 x 2880; header top; figure in the middle 45% of the height; FIG. label and the title block full width under it, above the caption band (cap bottom 220u); notes become 2-3 lines under the header.

## Camera grammar

| Shot | Expresses | How |
|---|---|---|
| whole sheet on the table | scale, the document | zoom = frame / sheet (about 0.6); opening or ending only (m) |
| medium on the figure | the design being drawn | zoom 1, slow push 3-6% per 5 s (m: .72 -> .76 over 5.2 s) |
| 2x close-up | the mechanism's heart | a cut on a downbeat to zoom 2 (m) |
| track along a path of force | cause and effect | keyed pan along a chain, pipe, wire or beam at constant zoom |
| pull back | make room for an exploded view | zoom down 5-8% over the explode window |
| lateral pan Fig. 1 -> Fig. 2 | versions, steps, time | one sheet, keyed x |
| slow push, no voice | wonder | the one real thing |

Keys `[t, x, y, zoom]`, cubic in-out, log zoom (source: monotone spline). Never rotate or tilt; impacts get a decaying bump `sin(90 t) * 5u * (1 - t/.35)` (stamp), 4u over 0.25 s (paper slap) (m).

## Motion

Grid 108 BPM (m): beat 0.556 s, eighth 0.278 s, bar 2.222 s. Layers start on beats; items inside a layer stagger.

| Action | Timing (m unless tagged) |
|---|---|
| draw-on of a line or outline | linear (constant pen speed); layer window, items staggered with span .45: item i of n starts at `t0 + i/(n-1) * .55 * dur`, lasts `.45 * dur` |
| layer order, 6 s scene (i) | centre lines beat 0 (0.9 s); outlines beat 1 (2.3 s window); detail beat 3 (1.4 s); hatching bar 2 (13 strokes/s); dimensions beat 5 (extension 0.3, line 0.5, arrowheads at the end, number 0.3); balloons beat 6, one per eighth (leader 0.3, circle 0.3, number 0.25); labels, FIG. beat 8 |
| lettering | left to right, linear: header about 0.08 s per character; a notes line 0.5-0.7 s; title-block values about 1 s; FIG. digit 0.3 s |
| FIG. rewrite | strike 0.25 s (source 0.2), new digit 0.3 s right after, view name 0.6 s |
| exploded view | window 2.2 s (4 beats); part i leaves `.055` of the window after part i-1, cubic in-out; trails as faint centre lines; parts come back one group per eighth with a 0.25 s cubic out (source 0.1 s) |
| projection turn | oblique depth factor 0 -> 0.5 over 1.1 s, cubic in-out; may settle to 0.42 over 1.2 s |
| mechanism step | one tooth per beat, 0.25 s cubic out (source: per eighth in 0.1 s) |
| machine acting | failure = slow, sag, wind down over about 1.5 s; joy = snap upright, needle overshoot `back.out(1.8)` |
| stamp | shadow approaches 0.45 s (offset 90, 120 -> 0, blur 30 -> 6, alpha .15 -> .5), lands with scale 1.08 -> 1 in 0.25 s and the camera bump |
| note box | in 0.2 s, text lettered 0.35 s, out 0.2 s |

Line first, then its label; frame 0 already shows the sheet furniture (header, title-block frame) and the first stroke under way, never bare paper (except a roll-out opening); nothing new in the last 0.3 s; the camera always drifts. Source ran on ones at 24 fps; 30 is fine.

## Materials and light

Reference stills (m): range 49-65 L, highlight 0.9-2.3%, glow 2.8-10 (the high value only around the one real thing), texture 0.8-1.8, vignette 1-2.5, chroma 34-37; k-means sheet `#1D3C77` 45%, `#1A366C` 32%, shadow `#162C58` 7%. Demo renders: range 55-60, highlight 1.5-2.4%, glow 4-5, texture 1.1-1.3, chroma 35.

- Line weights (screen u at zoom 1, measured on stills): outline 3.4, detail 2.1, thin 1.3, hair 1.0 (source ratio 3.2 / 2.0 / 1.25 / 0.9). Screen width = weight x zoom^0.65 (the kit sets `--bpz`). Dashes in sheet units: centre `34 7 6 7`, hidden `11 7`, phantom `30 6 5 6 5 6`, dotted `2 7`.
- Ink bleed: two halos 2.5u at .30 + 7u at .12 (source: 18% of a 3 px + 10% of a 7 px blur; shoulder +8-15 grey levels for 2 px (m)).
- Paper: flat base, then OVER the ink the grain (so paper fills vanish): low-frequency ellipses +-.2-.26, one broad streak band (160u period, 2%), left-to-right fade 14%, inset bleach 60u (`.bp-grain`); fibres (noise .55, alpha `.18R - .07`), coating streaks (.004 x .06, alpha `.2R - .08`; they must stay barely visible, never banding), fold creases 28u (dark edge .16, bright .12) with worn specks `#9EB5DB` (`paper()`).
- Vignette: radial 75%, from 55% to `rgba(3,8,24,.12)` at the corners.
- Shadows only from lifted things, light upper left: offset down-right, blur 14-40u, alpha <= .5, darkening lines too. Wet stain: deeper blue (x .70/.78/.92), pale tide line, softened lines.
- The one real thing (optional, once): soft white glow (blur 40u, alpha .22), one radial gradient across the whole shape from the top-left (alpha .30), 45-degree crescent hatching on the lower right, outline 1.6x on the shadow side, a shadow on the sheet; where it was drawn a pale imprint stays (outline at 26%).
- Stamp: ink `#C23B2E` broken by grain (holes where noise is low), opacity <= .92, rotated -8 degrees, double frame 8u + 2.6u, tech 700 tracking 7.

## Technique library

Capabilities; the content decides which to use.

- **Scene skeleton.** `#stage.bp-stage > #camera > .bp-sheet` (size the sheet with `calc(2880 * var(--u))` etc.) containing ONE `<svg id="ink" class="bp-ink" viewBox="0 0 2880 1800">` with everything drawn; `.bp-vignette` after `#camera`. Classes: weights `bp-out bp-det bp-thin bp-hair`, paper fill `bp-knock`, dashes `bp-center bp-hidden bp-phantom`, `bp-solid` (arrowheads, dots), text `bp-t-header bp-t-fig bp-t-hand bp-t-tech bp-t-field bp-t-dim bp-t-sign` (font-size attribute). Later in the DOM = in front.
- **The drafting kit (copy as is).** One master clock and a pure `draw(t)`; every visual change is a job of `t`. Build inside `document.fonts.ready.then(...)` (text is measured once), call `paper(sheet, W, H)`, add jobs, then:
  ```js
  tl.fromTo(clock, {t: 0}, {t: T, duration: T, ease: "none", onUpdate: () => draw(clock.t)}, 0); draw(0); MV.register(tl);
  ```
  ```js
// ---- blueprint drafting kit: one clock, pure draw(t) ----
const u = MV.u, T = MV.duration, BEAT = 60 / 108, BAR = 4 * BEAT;      // 108 BPM grid
const cl = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x)), seg = (t, a, b) => cl((t - a) / (b - a));
const eio = p => p < .5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2, eo = p => 1 - (1 - p) ** 3, ei = p => p ** 3;
const SV = document.getElementById("ink"), CAM = document.getElementById("camera"), JOBS = [], vis = (e, on) => e.style.visibility = on ? "visible" : "hidden";
SV.insertAdjacentHTML("afterbegin", `<defs><radialGradient id="bp-nibg"><stop offset="0" stop-color="#fff"/>
  <stop offset=".25" stop-color="#fff" stop-opacity=".45"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>`);
const nibIn = g => { g.insertAdjacentHTML("beforeend", '<circle class="bp-nib" r="16"/>'); return g.lastElementChild; };
const nibAt = (n, k, x, y) => { vis(n, k > 0 && k < 1); n.setAttribute("cx", x); n.setAttribute("cy", y); };
// pen: draw a shape along its length, t0..t0+dur, constant speed, nib at the head. Dashed conventions draw through a mask.
function pen(el, t0, dur, o = {}) { let p = el;
  if (o.dash) { const id = "bpm" + JOBS.length; SV.querySelector("defs").insertAdjacentHTML("beforeend", `<mask id="${id}" maskUnits="userSpaceOnUse" ` +
      `x="-9999" y="-9999" width="29999" height="29999"><path d="${el.getAttribute("d")}" fill="none" stroke="#fff" stroke-width="14"/></mask>`);
    el.setAttribute("mask", `url(#${id})`); p = SV.querySelector(`#${id} path`); }
  const L = p.getTotalLength(), N = Math.max(8, Math.ceil(L / 10)), P = [];
  for (let i = 0; i <= N; i++) { const q = p.getPointAtLength(L * i / N); P.push([q.x, q.y]); }
  p.setAttribute("pathLength", 1); p.style.strokeDasharray = "1 2";
  const nib = o.nib === false ? null : nibIn(el.parentNode), ease = o.ease || (x => x);
  JOBS.push(t => { const k = ease(seg(t, t0, t0 + dur)), f = k * N, i = Math.min(N - 1, Math.floor(f)), r = f - i;
    p.style.strokeDashoffset = 1 - k; vis(el, k > 0);
    if (nib) nibAt(nib, k, P[i][0] + (P[i + 1][0] - P[i][0]) * r, P[i][1] + (P[i + 1][1] - P[i][1]) * r); }); }
// pens: a layer; item i of n starts at t0 + i/(n-1) * (1-span) * dur and lasts span * dur
function pens(els, t0, dur, o = {}) { const n = els.length, s = o.span ?? .45;
  els.forEach((el, i) => pen(el, t0 + dur * (n > 1 ? i / (n - 1) : 0) * (1 - s), dur * s, o)); }
// write: letter a <text> on left to right, nib at the edge
function write(el, t0, dur, o = {}) { const b = el.getBBox(), nib = o.nib === false ? null : nibIn(el.parentNode);
  JOBS.push(t => { const k = seg(t, t0, t0 + dur); vis(el, k > 0);
    el.style.clipPath = k < 1 ? `inset(-40% ${(100 - 100 * k).toFixed(2)}% -40% -4%)` : "none";
    if (nib) nibAt(nib, k, b.x + b.width * k, b.y + b.height * .6); }); }
// hatch: the lines of a group appear one by one, `rate` strokes per second; hatchLines: 45-degree lines in a rectangle
function hatch(g, t0, rate = 13) { [...g.children].forEach((l, i) => JOBS.push(t => vis(l, t >= t0 + i / rate))); }
function hatchLines(x, y, w, h, sp = 10) { let s = ""; for (let c = -h; c < w; c += sp) { const a = Math.max(0, c), b = Math.min(w, c + h);
  s += `<line x1="${x + a}" y1="${y + h - a + c}" x2="${x + b}" y2="${y + h - b + c}"/>`; } return s; }
// camera keys [[t, x, y, zoom]] in sheet units, cubic in-out, log zoom; bumps [[t, amp]] = decaying shake for impacts
function camAt(t, K) { let i = 0; while (i < K.length - 2 && t > K[i + 1][0]) i++;
  const a = K[i], b = K[i + 1] || a, k = b === a ? 0 : eio(seg(t, a[0], b[0]));
  return [a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k, a[3] * (b[3] / a[3]) ** k]; }
function camera(K, bumps = []) { JOBS.push(t => { let [x, y, z] = camAt(t, K);
  for (const [tb, amp] of bumps) if (t > tb && t < tb + .35) y += Math.sin((t - tb) * 90) * amp * (1 - (t - tb) / .35) / z;
  CAM.style.transform = `translate(${(MV.width / 2 - x * z * u).toFixed(2)}px, ${(MV.height / 2 - y * z * u).toFixed(2)}px) scale(${z.toFixed(5)})`;
  SV.style.setProperty("--bpz", (z ** -.35).toFixed(4)); }); }
// paper: fibres, coating streaks, fold wear and the stamp-ink filter, laid OVER the ink (call once; sheet w x h)
function paper(sheet, w, h) { const F = (id, f, m) => `<filter id="${id}" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">` +
    `<feTurbulence type="fractalNoise" ${f}/><feColorMatrix values="${m}"/></filter>`, R = (a, f) => `<rect ${a} filter="url(#${f})"/>`;
  sheet.insertAdjacentHTML("beforeend", `<svg class="bp-tex" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs>` +
    F("bp-fibre", 'baseFrequency=".55" numOctaves="2" seed="3"', "0 0 0 0 .80 0 0 0 0 .86 0 0 0 0 .96 .18 0 0 0 -.07") +
    F("bp-streak", 'baseFrequency=".004 .06" numOctaves="3" seed="5"', "0 0 0 0 .04 0 0 0 0 .08 0 0 0 0 .20 .20 0 0 0 -.08") +
    F("bp-wear", 'baseFrequency=".12" numOctaves="2" seed="9"', "0 0 0 0 .62 0 0 0 0 .71 0 0 0 0 .86 5 0 0 0 -3.2") +
    `<filter id="bp-stampink" x="-5%" y="-5%" width="110%" height="110%"><feTurbulence type="fractalNoise" baseFrequency=".05" numOctaves="3" seed="11"/>` +
    `<feColorMatrix values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 4 0 0 0 -1.1"/><feComposite in="SourceGraphic" operator="in"/></filter></defs>` +
    R(`width="${w}" height="${h}"`, "bp-fibre") + R(`width="${w}" height="${h}"`, "bp-streak") +
    R(`x="${w / 2 - 6}" width="12" height="${h}"`, "bp-wear") + R(`y="${h / 2 - 6}" width="${w}" height="12"`, "bp-wear") +
    `</svg><div class="bp-grain"></div><div class="bp-fold-v"></div><div class="bp-fold-h"></div>`); }
// oblique solid: front rectangle, depth d receding up-right at 45 deg, depth factor k (0 = flat elevation, .5 = full oblique)
function box(x, y, w, h, d, k = .45, cls = "bp-out") { const o = d * k * Math.SQRT1_2;
  return `<path class="bp-knock" stroke="none" d="M${x} ${y} l${o} ${-o} h${w} v${h} l${-o} ${o} h${-w} Z"/>` +
    `<path class="bp-det" d="M${x} ${y} l${o} ${-o} h${w} v${h} l${-o} ${o} M${x + w} ${y} l${o} ${-o}"/><path class="${cls} bp-knock" d="M${x} ${y} h${w} v${h} h${-w} Z"/>`; }
// exploded view: part i of n leaves .055 of the window after part i-1, cubic in-out; trail = faint centre line from rest
const exSeq = (t, t0, dur, i, n) => eio(cl(seg(t, t0, t0 + dur) * (1 + n * .055) - i * .055));
function part(g, dx, dy, e, tr) { JOBS.push(t => { const k = e(t); g.setAttribute("transform", `translate(${(dx * k).toFixed(2)} ${(dy * k).toFixed(2)})`);
  if (tr) { tr.el.setAttribute("d", `M${tr.rest[0]} ${tr.rest[1]} l${(dx * k + .01).toFixed(2)} ${(dy * k).toFixed(2)}`); vis(tr.el, k > .02); } }); }
const draw = t => JOBS.forEach(j => j(t));
// ---- end kit ----
  ```
- **Drafting order.** `pens([c1, c2], 0, .9, {dash: true})` for centre lines (dashed elements must be `<path>`), `pens(outlines, BEAT, 2.3)`, `pens(details, 3*BEAT, 1.4)`, `hatch(group, 4*BEAT)`, dimensions, balloons, `write(label, 8*BEAT, .55)`. Put centre lines after the paper fills in the DOM so they read through the parts.
- **Dimension line.** Hair extension lines from 10u off the part to 16u past the dim line; filled arrowheads 16 x 10 when the line completes; number above, rotated with it. Live dimension: a job recomputes geometry and `textContent` from the moving part.
- **Balloon.** Dot r 4 (`bp-solid`) on the part, hair leader, circle r 22-24 as one arc path (`bp-thin bp-knock`), number inside; inside a part's group it flies with the part.
- **Hatching.** `hatchLines(x, y, w, h, 9-14)` inside a group `bp-hair`, revealed by `hatch()`; ground line = outline weight with a 22u hatched strip under it.
- **FIG. rewrite.** Old digit to 50%, struck by a detail stroke, new digit lettered 70u right, then the view name.
- **Oblique solids and projection turn.** `box(x, y, w, h, depth, k)` returns silhouette fill + receding edges + front face. For the turn, rebuild the group's markup in a job each frame with `k = .5 * eio(seg(t, t0, t0 + 1.1))`. Other shapes: knock-fill the swept silhouette first, then the back edges, then the front.
- **Exploded view.** Wrap each part in a `<g>`; `part(g, dx, dy, t => exSeq(t, t0, 4*BEAT, i, n), {el: trailPath, rest: [x, y]})`; pull the camera back 5-8% over the window; balloons after the parts land.
- **Section line sweep.** A phantom line sweeps across a housing (cubic in-out 1.4 s); behind it the hatched cut face shows (hatch lines with x < sweep x).
- **Notes and title block.** Notes numbered, one line per beat; the title block is drawn at the film's start (2 s), later only STATUS changes; end card = its last row lettered in.
- **Stamp (the one colour).** SVG group with `filter="url(#bp-stampink)"` (defined by `paper()`): two `rect`s stroked `#C23B2E` and the word in tech 700; shadow rect `#07112b` blurred; timing in Motion.
- **Hand shadow, mechanisms.** Pen and finger capsules `#000` blurred 5-11u at .45, offset down-right by height, nib glowing at the contact. Gears: trapezoid teeth, one tooth per beat, meshing gears half a tooth apart.

## Transitions

- Between scene files: `cut`, on a downbeat; the reference never dissolves. Keep one sheet: scene B's first camera key equals scene A's last, or B is a 2x close-up of the same sheet.
- In-scene (style-owned): FIG. number rewrite; lateral pan from Fig. 1 to Fig. 2 on the same sheet; push from the whole sheet to the header; pull back to the whole sheet for the verdict; sheet roll-out at the opening (0.55 s cubic out from the left edge, then a 4u slap bump).

## Captions

Drawing notes: translucent navy box, double white rule, hand capitals, white tick. The runtime places it bottom centre.

Variables (in `blueprint.css`): `--cap-bg rgba(12,28,62,.62)`, `--cap-fg #E7F0F9`, hand font 38u 400, radius 0, bottom 56u (portrait 220u), 1px white border plus an inner rule via `--cap-shadow`, `--cap-mark: tick` in ink.
A scene without captions may letter its own note with `.bp-note` + `.bp-note-cell` ("NOTE / n" cell), above the caption band, in empty paper.

## Image-gen guidance

Rarely needed. Anchors: "cyanotype blueprint technical drawing, white line art on Prussian blue paper, orthographic elevation, fine line weights, no shading, no text, no watermark"; subjects: organic silhouettes. Post: threshold to lines, recolour `#E7F0F9`, screen-blend onto the sheet (or trace to paths). Never generate text, dimensions, balloons, title blocks or the sheet.

## Taboos

- The reference never uses rendered 3D, perspective, camera rotation, or shake beyond impact bumps.
- Never colour fills or hues beyond blue and white, except one stamp, once.
- Never glow on ordinary lines (bleed only); glow and soft volume belong to the one real thing.
- Never people beyond a hand shadow or a signature; no photos; no dissolves; captions never on the drawing's lines.

## Known gaps

- Fonts are stand-ins: Noteworthy (rounder than an architect's hand), DIN Alternate (bold only), Snell Roundhand.
- Ink breakup by grain and mipmap softness are approximated (grain overlay, two halos).
- Wet stains, rain, roll-out, hand shadow and shadows darkening lines (needs a multiply layer) are described, not demo-tested.
- Mechanism steps and the FIG. strike are slowed to the 0.25 s minimum; source steps on eighths in 0.1 s.
- Captions are a bottom box; the reference placed each note in the empty paper of the shot, with a NOTE / n cell and a leader tick.
- Oblique occlusion is exact only for boxes and circles; other extrusions need a hand-built silhouette.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
