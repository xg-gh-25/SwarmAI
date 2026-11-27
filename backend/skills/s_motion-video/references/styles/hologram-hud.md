# Style: hologram-hud (Hologram HUD)

Units: `u` = frame short side / 1080 (1u = 1px at 1920x1080). CSS `calc(N * var(--u))` (see `hologram-hud.css`); script `const u = MV.u`. World sizes are in metres; the camera turns them into pixels. Provenance: `(m)` read from the reference's code, `(s)` seen in its stills, `(i)` inferred.

## Identity

An object is scanned into a glowing line wireframe hovering over a round projector pad on a near-black stage, and every fact about it is computed on screen. Identity rules, all mandatory: (1) the subject is always a wireframe made of light (thin additive cyan lines, brighter near, fainter far, soft double glow), never a rendered or flat-filled object; it may turn into a translucent solid hologram once per film, lines still on top. (2) One cold hue carries everything (lines, frame furniture, labels); one warm accent is spent only on values at the moment they lock and on the final call to action; near-white means heat (the scan front, the part being opened, a lock flash). (3) A fixed HUD frame on every shot: corner brackets, side rulers that drift with the camera, subject // mode top left, a live status top right, telemetry bottom left and right in small mono caps. (4) Information arrives as scanned data in a fixed grammar: scan, target lock, local explode, leader, spec card whose number rolls from scrambled digits to the true value, then the value docks into a side rail. (5) Text never sits on the wireframe: every text block has its own dark plate with hairline border and corner ticks, and the camera keeps the subject clear of it.

Best for: product specs, teardowns, launch and crowdfunding films, expo loop screens, "what is inside" explainers for any object with parts (devices, machines, vehicles, servers, appliances). Not for: people or emotion, abstract concepts without a physical object, warm or handmade brands, long reading text, data charts (use dataviz), UI walkthroughs.

## Palette

| Hex / value | Role |
|---|---|
| `#02080A` | stage, full frame (hsl(hue+12, 70%, 2.4%)) (m) |
| `#06181D` | radial lift behind the subject, radius 0.62 W, centre on the subject (m) |
| `#70ECFF` = hsl(188,100%,72%) | the hue: wire lines, HUD, rules, labels; used at alpha levels .9 / .75 / .55 / .28 (m) |
| `#2FC3DA` | dim hue: index numbers, telemetry, rulers, dot grid (.16) (m) |
| `#E2F5F8` | heat / near-white: names, card labels, hot edges, lock flash, scan dots (m) |
| `#FFBC47` = hsl(38,100%,64%) | accent: a locked value, its unit and bar; docked chip values; the call-to-action outline (m) |
| `rgba(1,8,12,.72)` / `.62` | dark plate under text / caption plate; border hue .28, corner ticks hue .7 (m) |
| wire levels | 7 alpha levels: stroke hsl(hue,100%,50+30a%) alpha .06+.84a, a = (i+1)/7 (m) |

Accent rule (this style's one-accent rule): one accent hue with one meaning, "this number is now true". A value is hue-coloured while it rolls and turns accent on the frame it locks, with a one-frame near-white flash. Nothing decorative is ever accent. Several locked values may share a final spec sheet (s), but only one value locks at a time.

Alternatives (pick per film from the subject, keep the pair): cyan/amber 188/38 (default), blue/orange 204/18 (`#70C6FF` / `#FF7E47`, stage `#02050A`) (m); violet/lime or ice-blue/red are allowed (i). Recompute every token from hue and accent with the same saturation and lightness. One object may keep its own brand colour (a logo mark), never for decoration.

## Type

- UI face (names, values, labels, details): `"Avenir Next Condensed", "DIN Alternate", "Helvetica Neue", "PingFang SC", "Noto Sans CJK SC", "Hiragino Sans GB", sans-serif` (stand-in for a squared condensed technical face). Mono (frame text, indices, codes, telemetry): `"SF Mono", Menlo, "PingFang SC", monospace`.
- All caps for names, labels, codes, HUD text; sentence case only for details and taglines.

| Role | Size u | Weight / details |
|---|---|---|
| subject name | 176 | 200, tracking 26u, heat colour (m) |
| spec card value | 136 (loupe 116) | 200, tabular digits; unit 0.3 x value, 500 (m) |
| sheet value (price, weight) | 92 | 200, unit 0.3x (m) |
| chip value | 42 | 400; unit 18, 500 (m) |
| tagline | 36 | 500, heat at .85 (m) |
| card label | 30 | 600 caps, tracking 3u, heat (m) |
| detail line | 25 | 500, hue .9 (m) |
| codes, category line | 22 mono | tracking 2u (m) |
| chip label | 21 | 600 caps, tracking 2u (m) |
| HUD frame text, card index | 20 mono | hue .75 top / dim .7 bottom (m) |
| target tag | 19 | 600, dark text on a filled hue tab (m) |

Numbers never jitter: tabular digits, scrambled digits only from 0-9. The decimal point is drawn as a small square (0.13 em), because a thin glowing full stop reads as a comma (m). `white-space: nowrap` everywhere.

## Layout grid

- 1920x1080. Frame: corner brackets 46u long at 44u from each edge; HUD text inset to the safe area (x 96, y 58); side rulers x 52-66 and 1854-1868 from y 160 to H-160 (m, text inset i).
- Subject: on the pad, at least 1/3 of frame height at every key moment, centre x 0.50-0.60 W when the left rail or title is in use (s). World scale: build the model 0.6-1.2 m tall (largest side about 1-1.8 m), pad radius unit R = 0.6-1.0 so the 1.05R bright ring is a bit wider than the footprint; wide shot dist 2.7-3.3, part push dist 1.6-2.2 (m, i). With the card at x 1240 the wire stays left of x 1180 and its leader is at least 150u long; push to a point on the part, not its far edge; orbit from the side where the body recedes away from the card; a flat subject (under 0.3 m tall) gets pitch 0.3-0.5 instead of a stand (i).
- Spec card column: x >= 1240, top y 318, width 470 (m). Chip rail: x 110-470, first chip y 470, step min(112, 410/(n-1)) so 5 chips fit (m). Title block top left: name baseline y 262 at x 110, rule 360u, code line, tagline (m). Final sheet plate right: x 1490-1860, y 540-900 (s).
- Per screen at most: HUD frame + title + 1 spec card + 5 chips + 1 target box + 1 leader (s). Cards on one side, rail on the other, never colliding; shorten explode vectors rather than fly a part into text.
- Captions off: bottom telemetry sits in the bottom corners at the safe inset (`hh-hud bl/br`). Captions on: the bottom telemetry moves up to just above `--cap-band` (or is dropped); the caption plate owns bottom centre.
- Portrait (i): brackets and HUD text as landscape (text inset 60u); subject centre (0.5 W, 0.40 H), height 0.30-0.40 H; title top left; spec card full width (80u margins) under the subject at about 0.60 H; chips become a row under the title; values 120u.

## Camera grammar

An orbit camera: target point, yaw, pitch, distance, fov 0.6 rad, plus the screen position (cx, cy) where the target lands, so the subject can sit beside text (m). Perspective is true; nothing is isometric.

| Move | Expresses | How |
|---|---|---|
| high 3/4 orbit craning down | examination; the scan plane reads as a rising ellipse | yaw 1.0 -> 0.42, pitch 0.24 -> 0.12, dist 2.7 -> 2.85, about 2.6 s cubic out (m) |
| push-in, then drift | this part matters | target = part centre, dist x0.55-0.65, 1.0-1.5 s `power2.inOut`, then 3-8% drift (m) |
| whip following the target box | attention jumps to the next part | 0.5 s `power2.inOut`, starting 0.45 s before the next lock, 28 speed streaks, motion blur (m) |
| orbit about a part's own axis | axial structure (rotors, stacks, lenses) | yaw sweep about 57 deg over the part's 2 bars, `sine.inOut` (m) |
| pull back + loupe | where (wide) and what (close) | wide shot plus a 225u circular inset with its own camera (m) |
| crane to eye level / turntable 360 | status change; every side | pitch to 0, then a full spin of the model over 4 s (m) |
| nearly static slow dolly | reading time | dist 3.5 -> 3.4 over 6 s (m) |

Mix camera keys with log-space distance. Background dot grid centred on the target's screen point + 40u down, offset (-yaw x 260u, pitch x 300u), rulers offset -yaw x 200u x 1.4 (faster, foreground): three depth layers always present (m). No roll, no handheld shake, no cuts inside a scene.

## Motion

Grid 120 BPM: beat 0.5 s, bar 2 s = one information beat; locks and value locks land on beats (m). Moves are on ones; only scrambled glyphs step every frame.

| Action | Duration | Ease / details |
|---|---|---|
| scan-in | 2.6 s (promo 1.5) | plane height 0.2 -> top, `sine.inOut`; frame 0 already shows the plane at the base (m) |
| target lock | 0.25 (ref 0.22) | from 1.9x the part box, rotated 45 deg, alpha .3 -> snug, `power2.out`; 3-frame white flash on the beat (m) |
| focus | 0.3 | rest of the model dims to 0.13-0.15 brightness (m) |
| local explode open / close | 1 beat (first part 2) / 0.4-0.55 | `power2.out` / `power2.in`; shell slides on its own axis, contents stay and turn hot (m) |
| leader | 0.5 | linear draw: anchor ring -> diagonal -> horizontal to the card, elbow at 55% (m) |
| card reveal | 0.4 | plate wipes left to right, top rule grows, label scrambles in (m) |
| value roll | 0.5 (first 1.0) | characters lock right to left; accent + 1-frame flash at the end; bar fills with it (m) |
| detail roll | 0.6 | starts with the value (m) |
| leader pulse after lock | 0.5 | ring 9 -> 35u, fading (m) |
| card -> chip dock | 0.35 + 0.35 | card fades `power2.in`; chip slides 30u into the rail `power2.out`, stays to the end (m) |
| title | name roll 0.5, rule 0.4, code 0.4, tagline 0.4 | rolls, rule `power2.out` (m) |
| ignition (solid) | 0.35 | faces in `power2.out`, brightness 1.5x decaying e^-3t, lines 1.5x -> 1.12x, light wave up the model 1.3 s (m) |
| glitch | 3 frames (0.1 s) | once per film, on the biggest change (m) |
| scan-erase | 1.25 s | plane top -> base `power2.inOut` (m) |

Typical 6 s part beat: 0 lock + push; 0.5 explode; 0.9 leader; 1.15 card; 1.4-2.2 roll, lock on a beat; 2.9 close + dock; drift. A 30-40 s spec film: title >= 5 s, each value on the card >= 2.5 s, then docked; final sheet >= 3 s (m).

## Materials and light

Measured on the reference stills (Lab L): range 31-90, highlight share 0.6-3.5%, glow 9-29, texture 3.8-7.1, vignette 12-42, chroma 4.6-7.1 (m). Dark frames sit at range ~30; the lit solid moment reaches ~90.
- Stage: `#02080A` + radial lift `#06181D` to 0.62 W; square dot grid every 48u, dot 0.4-2u growing toward the centre, alpha .16, fading to nothing at 0.6 W (m). Vignette over everything: clear inside 0.35 H, black .5 at 0.72 W (m).
- Wire: additive strokes 1.3-1.35u (x0.7-1.2 by level); depth fog = clamp(1 - (z - (d-1.1)) / 2.2 x 0.75, 0.22, 1) quantised to 7 levels (m). Glow: two blurred copies of the line layer, 1/4 res blur 3px at .85 and 1/8 res blur 4px at .7, added under the sharp lines (m).
- Hot edges: hsl(hue,70%,92%), width 1.3x: the 6-7 cm under the scan front, opened internals, a light wave band, a flashing part (m). Hot is scarce: at most about 15% of the visible lines at once; when the moving shell is large (a row of sleds, a cover), keep it at normal hue and make only what is revealed inside (or the single pulled item) hot (s, i). Scan cut points get 3u near-white dots (m).
- Pad on the floor in perspective: rings at 0.7 (.18), 1.05 bright (.55, 1.4u), 1.12 dashed (6/9.6u, .3), 1.45 (.14), 2.1 (.07); 120 ticks (every 10th long), 24 faint rays 1.2-2.4; rotating 0.12 rad/s (m).
- Scan plane: ellipse veil alpha .07, rim 1.6u at .75, 72 ticks; a full-width light band (120u soft + 1.5u core) at its screen height (m).
- Solid hologram: faces with screen-space Fresnel 0.3 + 0.7(1 - facing)^1.6, 7 levels, times an interlace (2px full / 2px at .38) scrolling 1px per frame (m). Beam: cone from the pad ring, gradient .10 -> .04 -> 0, 26 faint rays, 70 rising dust motes (m).
- Plates: `rgba(1,8,12,.72)`, 1px border hue .28, 10u corner ticks hue .7 (m). Card top rule 1.6u with a 34x6u tab; chip rule 1u with a 14x4u tab (m).
- No grain (m). No real shadows, no reflections, no lens flare (s).

## Technique library

Build the picture as one full-frame 2D drawing surface repainted every frame from the timeline time (background, pad, wire + glow, scan plane, target box, leader, rulers, glitch), with the text blocks as normal page elements on top (`hh-card`, `hh-chip`, `hh-title`, `hh-hud`, `hh-plate`, `hh-vignette` last). Draw from the timeline's own `onUpdate` so every state tween has already been applied; every state is a proxy tweened with `fromTo` at an absolute time; call the draw once before `MV.register(tl)`.
```js
const S = {scan:0, lock:0, push:0, ex:0, lead:0, roll:0};          // state proxies
const tl = gsap.timeline({paused:true, onUpdate: draw});
tl.fromTo(S, {clock:0}, {clock:1, duration:MV.duration, ease:"none"}, 0);  // timeline length
tl.fromTo(S, {lock:0}, {lock:1, duration:.25, ease:"power2.out"}, tLock);
```
- **Camera projection.** Orbit camera to screen:
  ```js
  function camera(c){ const cp=Math.cos(c.pitch), sp=Math.sin(c.pitch), cy=Math.cos(c.yaw), sy=Math.sin(c.yaw);
    const e=[c.tx+c.dist*sy*cp, c.ty+c.dist*sp, c.tz+c.dist*cy*cp]; let f=[c.tx-e[0],c.ty-e[1],c.tz-e[2]];
    const fl=Math.hypot(...f); f=f.map(v=>v/fl); let r=[-f[2],0,f[0]]; const rl=Math.hypot(...r); r=r.map(v=>v/rl);
    const up=[r[1]*f[2]-r[2]*f[1], r[2]*f[0]-r[0]*f[2], r[0]*f[1]-r[1]*f[0]], foc=MV.height/2/Math.tan(.3);
    return {dist:c.dist, P:(x,y,z)=>{const d=[x-e[0],y-e[1],z-e[2]], zc=d[0]*f[0]+d[1]*f[1]+d[2]*f[2], k=foc/Math.max(zc,.02);
      return [c.cx+(d[0]*r[0]+d[1]*r[1]+d[2]*r[2])*k, c.cy-(d[0]*up[0]+d[1]*up[1]+d[2]*up[2])*k, zc];}}; }
  ```
- **Wireframe model (CAD topology).** A piece = `{part, v:[[x,y,z]], e:[[i,j]], q:[[a,b,c,d]], ex:[dx,dy,dz], hot, internal}`; floor y = 0. Boxes = 8 vertices, 12 edges, 6 quads. Tubes = rings of 12-24 points along an axis plus longitudinal lines every 2-3 segments (radius may taper: cones, hoppers); wheels and burrs add spokes to the centre. Give each part its characteristic silhouette; every part physically touches another; internals (`internal`) are visible only while exploded (alpha = clamp(3k)).
- **Wire draw.** Per edge: add the explode offset `ex*k`, project, clip at the scan height `sy` (drop edges fully above it; cut the crossing one and put a 3u dot at the cut), fog level from the mid depth, multiplied by focus dimming; hot test = within 0.06 of the scan front, or `hot && k>0.02`, or inside the light wave, or part flashing. Collect one path per level for normal and hot, stroke all with `globalCompositeOperation = "lighter"`, normal `hsla(hue,100%,50+30a%,.06+.84a)` width 1.35u(0.7+0.5a), hot `hsla(hue,70%,92%,.35+.65a)` width 1.3x. Draw lines on an offscreen layer, then composite glow: layer -> 1/4 size with `filter="blur(3px)"` drawn back scaled at alpha .85, 1/8 size `blur(4px)` at .7, then the sharp layer, all additive. Return per part the screen box of the rest pose (scan-visible, non-internal vertices) for the target box, and the projected anchor for the leader.
- **Scan-in / scan-erase.** Tween the scan height from just above the floor to above the top (`sine.inOut`); draw the scan plane ellipse at that height (R slightly larger than the pad bright ring) and fade it over the last 15% of the scan. Erase = clip from above instead, plane moving down.
- **Target lock.** Corner-bracket box around the part box + 22u pad; scale lerp(1.9, 1, k), rotation lerp(45 deg, 0, k), alpha lerp(.3, 1, k); bracket arm min(hw, hh) x .38 + 8u, 2u (flash 3u, near-white); centre cross 10u and mid-edge ticks at .5 alpha; tag "TGT 01 · LOCK" (19u 600, dark text on a filled hue tab 26u tall) above the top-left corner once k > .8. Flash = near-white for 3 frames after the lock time, computed from time, not tweened.
- **Explode along the assembly axis.** Shell piece slides along its own axis (battery tray out of the body, hopper and lid up a vertical axis, caliper halves apart); contents stay and glow hot; rotors counter-rotate about their axis (1.6 and -0.9 rad/s x k) (m). Open over a beat, close in 0.4-0.55 s.
- **Leader.** From the part anchor: ring r 9u (grows with the first 20%), 3u near-white core, 1.6u line at .85; elbow at (lerp(ax, cardX-30u, .55), cardY), end at cardX-12u; length drawn with progress 0-1 (linear, 0.5 s). Pulse ring after the value locks.
- **Rolling value.** Lock order right to left: character i locks at k >= 0.25 + 0.7 (n-1-i)/n; spaces and punctuation never scramble; unlocked digits draw a seeded random digit, letters a random capital, re-seeded every frame:
  ```js
  const hash=n=>{n=Math.sin(n*127.1+311.7)*43758.5453; return n-Math.floor(n);};
  function roll(s,k,frame,seed){ if(k>=1) return s; let o=""; for(let i=0;i<s.length;i++){ const ch=s[i];
    if(k>=.25+.7*(s.length-1-i)/s.length || " .,·:-".includes(ch)){o+=ch;continue;} if(k<=0){o+=" ";continue;}
    const set=/\d/.test(ch)?"0123456789":"ABCDEFGHJKLMNPRSTUVWXYZ#%/<>="; o+=set[Math.floor(hash(frame*13.7+i*7.1+seed)*set.length)];} return o; }
  ```
  Write it into the value element from the draw (frame = round(t x fps)); replace "." with a `hh-dp` square; toggle `is-locked` (accent) when k >= 1 and `is-flash` for one frame. Labels and details roll the same way (letters scramble).
- **Spec card / chip / sheet.** `hh-card hh-plate` (rule, head = `hh-idx` + `hh-label`, `hh-value` + `hh-unit`, `hh-bar` > i, `hh-detail`); reveal with `clipPath: inset(0 100% 0 0) -> inset(0)` 0.4 s; bar fill `scaleX` with the roll. Chip `hh-chip hh-plate` in the rail. Final sheet: plate with `hh-sheet-label`, `hh-value hh-sheet-val`, `hh-cta` (accent outline).
- **HUD frame.** Brackets and rulers drawn each frame (rulers offset by -yaw x 200 x 1.4); `hh-hud tl/tr/bl/br` text written from the draw: top left `SUBJECT CODE // MODE`; top right status (`SCANNING 042%` -> `SCAN DONE · 3 SYSTEMS` -> `LOCK 2/3 · PART` -> ... -> `SHEET · READY`); bottom left `FRM 0123 · Y 0.57 M` (frame, scan height); bottom right `yaw° · pitch° · dist M`.
- **Hot-spot markers (final sheet).** Ring 17u with four cross ticks, 4u dot, numbered tag 30u tall on a dark plate beside it; pop one per half beat with a pulse ring (m).
- **Loupe.** Circular inset r 225u that opens 0.45 s `power2.out`: dark fill `rgba(2,10,14,.92)`, its own camera at 4-5x zoom with a slow counter-orbit, 2u rim + 90 outer ticks, tangents from a 46u ring on the part, mono "ZOOM x4.3" (m).
- **Solid hologram + beam.** Once: Fresnel faces x interlace on a separate layer drawn under the lines; beam cone and dust; light wave band (+-0.06) climbs the model; optional turntable spin of the whole model and pad (m).
- **Glitch.** 9 horizontal slices of the finished frame shifted up to +-60u and an 8u additive ghost at .35, amount 1 -> 0 over 3 frames, seeded by frame (m).
- **Whip.** Camera keys mixed `power2.inOut` over 0.5 s; 28 speed streaks 200-800u long, 1.4u, alpha .22 x sin(pi p) (m).

## Transitions

Between scene files: `cut` (default; the HUD frame matches across the cut) or `fade` 0.4 s. In-scene, style-owned: scan-in at the start of a subject, scan-erase top-down at its end (then cut), whip between parts, loupe iris, a 3-frame glitch on the biggest change (m). The HUD frame never fades between scenes of one subject (i). With `fade`, no key value in the incoming first 0.4 s.

## Captions

HUD transcript: dark plate `rgba(1,8,12,.62)`, near-white 38u 500 UI face, bottom centre, wrap at 1180u, hue tick mark on the left, hairline border (m, mark i). In 0.1 s before the voice; hold >= max(1.8 s, speech + 0.6 s) (m).
```css
--cap-bg: rgba(1,8,12,.62); --cap-fg: #E2F5F8; --cap-font: var(--hh-ui); --cap-size: calc(38 * var(--u)); --cap-weight: 500;
--cap-radius: 0; --cap-bottom: calc(100 * var(--u)); --cap-mark: tick; --cap-accent: #70ECFF; --cap-border: 1px solid rgba(112,236,255,.28);
/* portrait: --cap-bottom 220u, --cap-size 40u */
```

## Image-gen guidance

- The subject should be built as a wireframe model, not generated. Use images only as a modelling reference or a faint backplate.
- Anchors (backplate only): "pure black background with a faint cyan haze, empty dark studio, no objects"; "macro photo of an object's real part for modelling reference, plain background".
- Never generate: the wireframe, pad, HUD, numbers, text, logos, any coloured product render.
- Post-treatment for a backplate: desaturate, tint to the hue, darken to L < 8, put it under the dot grid at <= .3 alpha.

## Taboos

- The reference never shows the subject as a shaded, textured or photographic render, and never uses more than one cold hue plus one accent.
- The reference never puts text directly over the wireframe without a plate, and never uses the accent for decoration, headings or lines.
- The reference never cuts or crossfades inside a scene, never uses a title card, never rolls the camera.
- The reference never uses proportional digits for rolling values, serif or handwritten type, rounded friendly UI, grain, or real shadows.

## Known gaps

- The bundled condensed technical face and the micro mono face are replaced by Avenir Next Condensed and SF Mono / Menlo; Avenir is rounder and slightly wider.
- Target lock is 0.25 s (the reference snaps in 0.22 s) to respect the 0.25 s move minimum; the 3-frame flash is time-computed, not a tween.
- Whip motion blur (5 sub-frames at a 180 deg shutter) needs a sub-frame accumulation pass per frame; without it, rely on the streaks.
- The glitch shifts only the drawn layer; page-level cards and HUD text do not tear.
- The caption box cannot show the reference's moving voiceprint bars or its two diagonal brackets; the runtime gives a tick mark and border.
- Sound (synth score on a 120 BPM grid, lock thumps, roll ticks, true-silence beat before ignition) is not part of the picture.
- Portrait layout is inferred; the reference is landscape only.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
