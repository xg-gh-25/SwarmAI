# Style: anatomy (Mechanical Anatomy)

Units: `u` = frame short side / 1080 (1u = 1px at 1080p); CSS `calc(N * var(--u))`, GSAP `const u = MV.u`. World units are about 1 mm of the real thing; camera `k` = px per unit (3.5-4.5u). `(m)` = from the reference's tokens and demo numbers, `(s)` = seen in its stills, `(i)` = inferred for video.

## Identity

The film explains its subject as one invented physical machine on a bench, a real object whose working is the idea (a sorting bench for a data pipeline, a lock for a cache, drawers for storage tiers), and keeps that machine for the whole film. It is drawn in a fixed axonometric camera (azimuth 45, elevation 30) as solid parts filled in paper white: white tops, four near-identical grey side tones by facing, thin graphite hairline outlines and a soft grey halo under the base. It is dense with small purposeful parts: feet, grooves, corner screws, ruler ticks, a label plate, knurled knobs, bushings, seams. The drawing sits in a framed figure card with four quiet monospace corner labels: `Fig n` top left, the uppercase title top right, the phase bottom left, a live readout in true units bottom right. Exactly one part is lit (a black edge on paper) and that is where the eye goes. Colour appears only when colour is the subject. Motion is the mechanism working, calm and eased: carriages glide and settle, drawers slide, needles swing, assemblies come apart with a staggered spring and close up again. Words stay out of the drawing.

Best for: how-it-works explainers, system architecture told as a mechanism, developer and infrastructure topics, hardware teardown, launches that take the product apart. Not for: people, emotion or story-led films, photo or footage, dashboards with many charts, loud beat-cut promos, brand films that need saturated colour.

## Palette

Light plate is the default and the one to design first (m). Dark plate = class `an-dark` on `#stage` (m).

| Token | Light | Dark | Role |
|---|---|---|---|
| `--an-page` | `#F2F2EF` | `#0B0B0B` | frame outside the card |
| `--an-card` | `#FBFBFA` | `#131313` | card = paper; base fill of every solid |
| `--an-rule` | `#8A8A8F` | `#6A6A6A` | 1px card frame |
| `--an-top` / `--an-face` | `#FFFFFF` | `#1C1C1C` / `#1B1B1B` | top faces / lit fill |
| `--an-shade-0..3` | `#DCDCD9 #E5E5E2 #EDEDEA #F4F4F1` | `#0F0F0F #121212 #151515 #181818` | side faces, 0 = away from the light, ~3% apart |
| `--an-lit-shade` | `#EEEEEB` | `#1D1D1D` | sides of the lit part |
| `--an-hi` | `#26262A` | `#D4D4D4` | subject outline, zero mark, needle, pointer |
| `--an-mid` | `#8A8A8F` | `#6A6A6A` | structure outline, major ticks |
| `--an-lo` | `#B4B4B8` | `#3E3E3E` | base outline, minor ticks, screw rings |
| `--an-faint` | `#D4D4D6` | `#2A2A2A` | grooves, creases, inset outlines, floor lines |
| `--an-lit` | `#000000` | `#FFFFFF` | THE accent: the one part to watch |
| `--an-dot` | `#3A3A3E` | `#E6E6E6` | screws and perforations (opacity .22-.85) |
| `--an-text` / `--an-strong` / `--an-muted` | `#57575A / #18181A / #8C8C90` | `#A3A3A3 / #E6E6E6 / #6E6E6E` | Fig and readout / statement, selected key / title, phase |
| `--an-halo` | `#8A8A80` at .32 | `#050505` at .9 | blurred shadow under base and lifted parts |

Tone hierarchy (m): base and feet `lo`, structure `mid`, the one subject `hi` (repeated items such as queued blocks stay `mid`), exactly one part `lit`; grooves `faint`; minor ticks `lo`, major `mid`, zero mark `hi`; creases `faint` (`lo` on `hi` parts).

Colour only when colour is the subject (m): RGB channels as a set (`#E5484D #30A46C #3E63DD`); heat and fire (`--an-heat-rim #C2410C`, `--an-heat #EA580C`, pale core `#FFD9B0`, smoke body `#AB645F` (s)); an indicator lamp (`#C4C4C4` off, `#D9480F` on). Fluids are tints of the plate's own greys, not colours. Optional metal for a turning part (i): brass `#EFE0BF / #D9BC86 / #B8955A`, line `#7A5C2A`. One colour family per screen; a coloured phenomenon and the lit part may coexist because the lit part is black, not a hue.

## Type

One monospace face for every word on the plate (m): `--an-mono` = `"SF Mono", Menlo, "MV Noto Sans Mono", "PingFang SC", "Hiragino Sans GB", "MV Noto Sans SC", monospace`; tabular figures for numbers.

| Role | Size | Details |
|---|---|---|
| `Fig n` (top left) | 26 | `--an-text`, tracking .04em, sentence case (`Fig 3`) |
| title (top right) | 26 | uppercase, tracking .1em, `--an-muted`; 2-5 words, `·` separates parts |
| phase (bottom left) | 26 | uppercase, tracking .1em, `--an-muted`; optional lamp dot before it |
| readout (bottom right) | 28 | `--an-text`, tabular; true units, lower case, `·` separated: `partition 2 · 12,480 rows` |
| legend key | 24 | `01` number in `--an-lo`, a 26u swatch rule, name in `--an-muted`; selected key `--an-strong` |
| plate statement (optional) | 52 | weight 500, `--an-strong`, second line `--an-muted`, 1-2 short lines (i) |
| engraved face label | 12-18 | at most one per machine, on a face, `--an-mid` |

Corner labels are about 1.45% of card width in the stills (s). `white-space: nowrap` everywhere; numbers get a fixed width in `ch` so the readout never jumps.

## Layout grid

- Card (`.an-card`): left/right 96u, top 54u, bottom = `var(--cap-band)` (54u without captions, the caption band with them). 1px `--an-rule` frame, no radius, no shadow (m). Page colour shows around it.
- Corner labels inset 36u from the card frame (s); each sits on a small paper chip so the drawing may pass under it in a close shot.
- The machine is centred and fills 55-65% of the card (m), with air on every side; never touches a corner label. Shift it right (centre at 58-62% of card width) when a legend or a statement takes the left third.
- Legend: left side of the card, vertically centred, 14u between keys, at most 8 keys. Statement: top left under `Fig n` (top = corner + 64u).
- Per screen: 4 corners + one machine + at most a legend or a statement. No floating labels, leader lines, balloons or arrows in the drawing (m).
- Keep one scale (k) for the whole film so the machine is recognisably the same object scene to scene (m: consistent scale across a set).
- Portrait (i): card inset 54u, corners 30u, machine 80-90% of card width centred at 45% height; legend moves under the machine as a 2-column block; statement stays top left at 48u.

## Camera grammar

The projection never changes within a film: azimuth 45 (35-60 to show a long side), elevation 30 (up to 44 to look into something open) (m). The card frame never moves; the camera moves inside it (`#camera` inside `.an-card`, clipped).

| Shot | Expresses | How |
|---|---|---|
| full figure | the whole machine, "this is the system" | machine 55-65% of card, shot-long push 3-5% (`ease: "none"`) |
| region | one mechanism of it | scale 1.5-2.2 about the part, 1.2-1.6 s `power2.inOut`; the rest steps down one tone |
| detail 4x | "every region rewards a close look" | scale 3-4 on a junction (knurl, ticks, screws); only if that area is fully detailed |
| apart | what it is made of, in what order | exploded view in place (camera still or slow push) |
| turn (optional) | a part that moves in depth | re-project one subject with a changing azimuth (Technique library) |

Glide between regions instead of cutting when the next idea is in the same machine (i). Never perspective, tilt, shake or whip.

## Motion

Principles (m): motion is the mechanism, time-based and eased toward targets; nothing snaps or pops; one-shot events have an attack and a decay; the readout is computed from the same value that moves the parts.

| Action | Duration | Ease / detail |
|---|---|---|
| glide (carriage, slider, reach) | 1.2-1.6 s | critically damped from rest: `ease: x => (1-(1+6*x)*Math.exp(-6*x))/(1-7*Math.exp(-6))` |
| follow (needle, pointer, dial to a new value) | 0.6-1.0 s | exponential follow τ 0.1-0.3 s; `power2.out` (≈ τ 0.2) |
| spring (drawer, plunger, each exploded assembly) | 0.75 s | stiffness 64, damping 14.2: overshoot 0.15%, so `power2.out` 0.75 s fits (m, fitted rms .012); never `back` |
| explode stagger | 35 ms between assemblies (m); 35-80 ms in video (i) | close in reverse order |
| lift / hover-style raise | 0.7 s | cubic-bezier(.32,.72,0,1) (m), or `power3.out` 0.9 s |
| settle of a level (colour, brightness, frequency) | 0.3-1.0 s | critically damped, as glide |
| event (click, flash, spark) | attack 0.04-0.12 s, decay 0.13-0.4 s | one tween of 0.3-0.6 s with a burst ease (Technique library) |
| focus step-down / lit hand-over | 0.6-0.8 / 0.25-0.4 s | `power2.inOut` / `power1.out` crossfade |
| corner text, legend keys, statement | 0.4-0.6 s | opacity + 16-18u rise, `power3.out`, stagger 0.06-0.18 |
| readout counter | with the move it reports | same ease and duration as the part |

Scene rhythm (i): frame 0 already shows the whole machine and all four corner labels (never an empty card or a corner fading in); first mechanism move at 0.4-0.6 s; one mechanism action every 1-2 s; a full open-and-close cycle fits in 3-4 s; every mechanism move settled 0.3 s before the end; the camera push alone runs to the last frame. No beat grid: timing follows the mechanism, not music.

## Materials and light

Measured on the reference stills (light plate): L range 20-38, highlight share (L>80) 0.88-0.98, glow 0, texture 2-8 (fine lines only), chroma 0.1-0.7, no vignette (corners equal the card) (m). With a heat effect: range 48, glow 17, chroma 2.7 (m).
- Light comes from the side, perpendicular to the view in plan: side tone index = floor(((n · L) + 1) / 2 × 4), L = (-sin az, cos az) (m). Tops always white. Curved bodies show the four tones as vertical bands.
- Outline: silhouette only (back top rim, front bottom rim, the two side edges), 1.5u in the part's tone; crease (front top rim) 1u `faint`; details 1u (m: 0.6px at figure size, scaled for video (i)). Lit part: 2.4u `--an-lit` edge and lit fills.
- Halo: the base footprint at its lowest z, `--an-halo` at .32, blur 14u, offset 5u down (m: 9px blur, 3px down at figure size). Also under any lifted part.
- Surfaces are flat paper: no gradient, grain, noise or vignette on page, card or parts. Bevels and seams are lines, not shading.
- Heat, fire, steam, lamp light (only when it is the subject): saturated body, pale core, coloured rim, on paper with normal (not screen) blending; what the light falls on takes a tint; fades only get darker; build it as blurred radial gradients with an `feTurbulence` displacement whose seed is fixed and whose `baseFrequency` or offset is driven by time.

## Technique library

Capabilities, not obligations.

- **Projection and solids (the core).** Paste this once per scene. `solid(ring, z, h, tone, {lit, inner})` extrudes a plan ring: four shaded side buckets, white top, faint crease, outline; `inner` is drawn on the top before the outline (grooves, screws, ticks). Paint back to front, bottom to top: base, then what stands on it, far (small x+y) before near; split a part that wraps another into back and front halves.
```js
const AN = (() => {
  const R = Math.PI / 180, C = { a: 45 * R, e: 30 * R, k: 3.5, ox: 0, oy: 0 };
  const cam = (o) => { if (o.az != null) C.a = o.az * R; if (o.el != null) C.e = o.el * R; Object.assign(C, { k: o.k ?? C.k, ox: o.ox ?? C.ox, oy: o.oy ?? C.oy }); };
  const P = ([x, y, z]) => { const s = Math.sin(C.a), c = Math.cos(C.a);
    return [C.ox + (x * s - y * c) * C.k, C.oy + ((x * c + y * s) * Math.sin(C.e) - z * Math.cos(C.e)) * C.k]; };
  const shift = (v) => { const p = P(v), o = P([0, 0, 0]); return { x: p[0] - o[0], y: p[1] - o[1] }; };  // world move -> px
  const path = (pts, close) => pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join("") + (close ? "Z" : "");
  const rect = (x, y, w, d, r = 0, n = 5) => { r = Math.min(r, w / 2, d / 2); const out = [];       // rounded plan, CCW
    [[x + w - r, y + r, -90], [x + w - r, y + d - r, 0], [x + r, y + d - r, 90], [x + r, y + r, 180]].forEach(([cx, cy, a0]) => {
      for (let i = 0; i <= (r ? n : 0); i++) { const a = (a0 + (r ? 90 * i / n : 45)) * R; out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]); } });
    return out; };
  const circle = (cx, cy, r, n = 48) => Array.from({ length: n }, (_, i) => [cx + r * Math.cos(2 * Math.PI * i / n), cy + r * Math.sin(2 * Math.PI * i / n)]);
  const ln = (cls, d) => d ? `<path class="an-line ${cls}" d="${d}"/>` : "";
  function solid(ring, z, h, tone = "mid", o = {}) {
    const n = ring.length, s = Math.sin(C.a), c = Math.cos(C.a);
    const area = ring.reduce((A, p, i) => A + p[0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * p[1], 0);
    const T = ring.map(([x, y]) => P([x, y, z + h])), B = ring.map(([x, y]) => P([x, y, z]));
    const N = ring.map((p, i) => { const q = ring[(i + 1) % n], dx = q[0] - p[0], dy = q[1] - p[1], L = Math.hypot(dx, dy) || 1;
      return area > 0 ? [dy / L, -dx / L] : [-dy / L, dx / L]; });
    const F = N.map(([nx, ny]) => nx * c + ny * s > 1e-9);
    const sh = ["", "", "", ""]; let out = "", crease = "";
    for (let i = 0; i < n; i++) { const j = (i + 1) % n;
      if (h > 0 && F[i]) { const t = Math.min(3, Math.max(0, Math.floor((N[i][0] * -s + N[i][1] * c + 1) / 2 * 4)));
        sh[t] += path([T[i], T[j], B[j], B[i]], true); crease += path([T[i], T[j]]); out += path([B[i], B[j]]); }
      else out += path([T[i], T[j]]);
      if (h > 0 && F[(i + n - 1) % n] !== F[i]) out += path([T[i], B[i]]); }
    return `<g class="an-solid${o.lit ? " an-lit" : ""}"${o.id ? ` id="${o.id}"` : ""}>` +
      sh.map((d, t) => d ? `<path class="an-s${t}" d="${d}"/>` : "").join("") + `<path class="an-top" d="${path(T, true)}"/>` +
      ln(`fine ${tone === "hi" ? "lo" : "faint"}`, crease) + (o.inner || "") + ln(`edge ${tone}`, out) + "</g>"; }
  const slab = (x, y, w, d, r, z, h, tone, o) => solid(rect(x, y, w, d, r), z, h, tone, o);
  const cyl = (cx, cy, r, z, h, tone, o) => solid(circle(cx, cy, r, o?.n || 40), z, h, tone, o);
  const line = (pts, tone = "lo", fine = true) => ln(`${fine ? "fine " : ""}${tone}`, path(pts.map(P)));
  const loop = (ring, z, tone = "faint") => ln(`fine ${tone}`, path(ring.map(([x, y]) => P([x, y, z])), true));
  const dots = (pts, r = 0.5, tone = "") => `<g class="an-dots ${tone}">` + pts.map((p) => { const [x, y] = P(p);
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${(r * C.k).toFixed(2)}"/>`; }).join("") + "</g>";
  const screw = (x, y, z) => loop(circle(x, y, 2, 16), z, "lo") + dots([[x, y, z]], 0.55, "hi");
  const ticks = (x0, x1, step, every, y, z, len = [2, 3.6]) => {                       // ruler ticks hang from edge y
    let mi = "", ma = ""; for (let i = 0, x = x0; x <= x1 + 1e-6; i++, x += step) { const d = path([P([x, y, z]), P([x, y - len[i % every ? 0 : 1], z])]);
      if (i % every) mi += d; else ma += d; } return ln("fine lo", mi) + ln("fine mid", ma); };
  const halo = (ring, z) => `<path class="an-halo" transform="translate(0 ${(5 * MV.u).toFixed(1)})" d="${path(ring.map(([x, y]) => P([x, y, z])), true)}"/>`;
  const knurl = (cx, cy, r, z0, z1, n = 36) => { let d = ""; const s = Math.sin(C.a), c = Math.cos(C.a);
    for (let i = 0; i < n; i++) { const a = 2 * Math.PI * i / n, v = Math.cos(a) * c + Math.sin(a) * s; if (v < 0.2) continue;
      const x = cx + r * Math.cos(a), y = cy + r * Math.sin(a); d += path([P([x, y, z0]), P([x, y, z1])]); } return ln("fine lo", d); };
  const radial = (cx, cy, r, n, every, z, len = [1.2, 2.6]) => { let mi = "", ma = "";  // dial / turntable ticks
    for (let i = 0; i < n; i++) { const a = 2 * Math.PI * i / n, L = len[i % every ? 0 : 1];
      const d = path([P([cx + r * Math.cos(a), cy + r * Math.sin(a), z]), P([cx + (r - L) * Math.cos(a), cy + (r - L) * Math.sin(a), z])]);
      if (i % every) mi += d; else ma += d; } return ln("fine lo", mi) + ln("fine mid", ma); };
  return { cam, P, shift, path, rect, circle, solid, slab, cyl, line, loop, dots, screw, ticks, halo, knurl, radial, C };
})();
// card size: W - 192u by (MV.captionTop - 54u); AN.cam({ k: 4 * MV.u, ox: cardW / 2, oy: cardH / 2 + 60 * MV.u })
```
- **Card markup.** `#stage.an-stage > .an-card > #camera > svg.an-svg`; the four `.an-corner` labels (`.an-fig .an-title .an-phase .an-readout`) are siblings of `#camera`, so they never move.
- **Parts recipes** (world units). Base plate on feet: halo; 4 feet = `cyl r7 h1.6 lo` + `cyl r5.4 h3.4 mid`, sorted far to near; slab h 10-12 `mid`, r 8; groove `loop` inset 5; corner screws inset 10. Ruler strip: slab d 7 h 0.6 `lo` along a near edge, `ticks` every 4, major every 5th. Label plate: slab h 0.8 + inset loop + three rule lines of different lengths + two screw dots. Rails: slabs d 5 h 3 with end stops (5 x 44 x 7, two dots on top). Carriage: plate h 5 + four corner bushings with a dot + rivet row (0.4 dots) + handle (neck slab + small cylinder). Knob: `cyl r6 h4` + `knurl` + pointer line `hi` on top. Dial gauge face up: puck, knurled bezel band, bezel `loop` `lo`, `radial` ticks (50, major every 5th), needle `hi` 2.4u rebuilt per frame. Drawer: front slab on the case face, pull (two posts + bar), label holder outline with two rivets. Vents: rows of 0.55 `lo` dots or closed slot outlines. Details on a vertical face: `line`/`dots` with the face's x (or y) held constant.
- **Detail density.** A figure has 40-80 parts, a hero 80-150 (m). At 1x it reads as one machine; zoomed 4x every region still has a real part. Every part rests on another; every line ends on an edge, a dot or an outline (no line stops in mid-air; ticks hang from a strip; seams run edge to edge).
- **Moving a part.** Build it once at rest inside its own `<g>`; move with `x/y` from `AN.shift(worldVector)` times progress. A part whose shape changes (drawer body length, spring, needle) is rebuilt in `onUpdate` from a proxy value (no layout reads).
- **Exploded view.** Each assembly has a world move vector along its real disassembly direction (stacked parts along the axis, side units outward). Order: whoever is outside or underneath everything leaves first; close in reverse.
```js
order.forEach((id, i) => { const m = AN.shift(MOVE[id]);
  tl.fromTo("#" + id, { x: 0, y: 0 }, { x: m.x, y: m.y, duration: 0.75, ease: "power2.out" }, tOpen + i * 0.035);
  tl.fromTo("#" + id, { x: m.x, y: m.y }, { x: 0, y: 0, duration: 0.75, ease: "power2.out", immediateRender: false }, tClose + (order.length - 1 - i) * 0.035); });
```
  Draw assemblies in an order that stays right in every frame (bound the moves so no part passes another in depth). Phase word follows the springs: `taking apart` -> `apart · 6 assemblies` -> `closing up`.
- **Lit hand-over.** Build each selectable assembly twice (normal and `lit:true`) and crossfade the lit copy's opacity in 0.25 s; the matching legend key turns `--an-strong`. Only one lit part settled on screen.
- **Focus.** Everything but the selected group steps down one tone: tween its `filter` from `contrast(1) brightness(1)` to `contrast(.55) brightness(1.29)` (dark: `contrast(.6) brightness(.8)`), 0.8 s `power2.inOut`.
- **Readout and counters.** One proxy drives both the part and the text: `onUpdate: () => { el.textContent = v.toFixed(1); setNeedle(v); }`. Phase word and number come from the same instant.
- **Burst ease for events.** `const burst = (r, f) => (q) => q < r ? (q / r) ** 2 * (3 - 2 * q / r) : Math.exp(-(q - r) / f);` on a 0.4 s tween of an opacity or scale (r, f as fractions of the tween).
- **Turn (optional).** Tween a proxy azimuth and rebuild the turning subject with `AN.cam({ az })` in `onUpdate`; keep the rest static. Rebuild every frame (no prebuilt frames).

## Transitions

Between scene files: `cut` (default; match the camera when the next scene shows the same machine) or the contract `fade` (0.4 s, keep key text out of the incoming first 0.4 s). In-scene, style-owned: glide between regions of one machine (preferred over a cut); take apart / close up; push into a 4x detail and pull back. The card and its corners never cut away; only the corner texts change (`Fig n`, phase, readout) with a 0.4 s crossfade.

## Captions

A square plate label in the caption band under the card: paper `#FBFBFA`, 1px `#8A8A8F` frame, no radius, monospace 34u weight 500 `#18181A`, a small graphite dot on the left (like the phase lamp). Dark plate: `#131313` / `#E6E6E6`.
```css
--cap-bg: #FBFBFA; --cap-fg: #18181A; --cap-font: var(--an-mono); --cap-size: calc(34 * var(--u)); --cap-weight: 500;
--cap-radius: 0; --cap-bottom: calc(52 * var(--u)); --cap-border: 1px solid #8A8A8F; --cap-shadow: none;
--cap-mark: dot; --cap-accent: #26262A;      /* portrait: size 32u, bottom 180u */
```

## Image-gen guidance

Rarely needed: the machine is drawn, not generated. If an asset is unavoidable (content for a screen on a face): "technical axonometric illustration, paper-white solid parts, four light grey side tones, white tops, thin graphite outlines, soft grey shadow under the base, monochrome, many small precise parts, no text, no perspective". Never generate the machine, labels or numbers. Post: desaturate, clip to its opening on a face.

## Taboos

- The reference never uses perspective, camera rotation of the projection mid-film, tilt or shake.
- Never wireframes, see-through parts or dashed hidden edges; every solid is filled and hides what is behind it.
- Never leader lines, balloons, dimension arrows, hatching or floating labels in the drawing; words go to the corners.
- Never abstract stacks or slabs standing for ideas; never one big plain slab.
- Never colour for decoration; never two lit parts settled at once.
- Never a line or texture that stops in mid-air; never a pipe through a part.
- Never snaps, pops or bounce/elastic eases; never an empty card on frame 0.
- No gradients, grain or vignette on page, card or parts.

## Known gaps

- Smooth shading of curved bodies (bells, domes, balls) is not in the snippet; cylinders show four facet bands.
- Pipes, cables and harness routing (depth-sorted chunks, braids, clamps) and the proof that nothing intersects are not ported; route few lines by hand.
- Painter's order is manual (far to near, back/front splits); per-frame depth re-sorting for parts that change depth order is not built in.
- The 3D turning mode with metal materials and the shader layer (fire, water, caustics, lamp light) are only approximated with gradients and filters.
- Reference motion is interactive; video timings are mapped from its springs and eases, the scene rhythm is inferred.
- Portrait layout is inferred; the reference stills are landscape and near-square.
