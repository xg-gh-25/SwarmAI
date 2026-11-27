# Style: living-screencast (Living Screencast)

Units: `u` = frame short side / 1080. At 1920x1080, 1u = 1px. In CSS write `calc(N * var(--u))` (see `living-screencast.css`). In GSAP use `const u = MV.u;` and tween `y: 28 * u`. Every size below is in u. The world (wallpaper + window) is 1920 x 1080 u (portrait 1080 x 1920); `W = MV.width / u`, `H = MV.height / u`. Boxes are `{x, y, w, h}` in world u.

## Identity

A real-looking screen recording of the user's product where every window, menu, diff line and button is redrawn as crisp vector UI, so the camera can push anywhere and text stays sharp. The app window floats on a warm wallpaper; the camera moves like screen-recording software in one take: push to the cursor, drift while the voice talks, whip pan with directional blur, push plus spotlight on the one promise. Two actors, no hands: the cursor is the user; an optional pixel mascot is the software, standing on top edges of UI elements, and each of its actions causes a real UI change. Six identity rules: (1) recorded look, everything redrawn: no screenshots or placeholder copy, names, modes and shortcuts match the user's product, demo data stays consistent; (2) two resolutions collide: smooth UI vs hard-edged square pixels (mascot, props, dust, speed lines on one grid); (3) gravity = top edges of UI elements; (4) cursor = user, mascot = software, cause and effect; (5) the window floats on wallpaper, zoom never below 1; (6) only four overlays: chapter pill, keystroke HUD, frosted caption pill, "4x" fast-forward tag; the title appears only inside the UI (empty-state headline, window title, notification). The mascot can be dropped for sober enterprise films: then cursor, keystroke HUD and UI state carry the cause and effect; all other rules stay.

Best for: product tutorials, feature launches, onboarding, release walkthroughs, developer-tool and SaaS demos. Not for: strategy or leadership talks (use exec-brief), hardware (3D product style), data stories without a real product UI.

## Palette

The product's own UI colours win: copy the token set and replace values, always both light and dark. Default = warm ivory light theme and its dark twin.

| Token | Light | Dark | Role |
|---|---|---|---|
| ivory | `#FAF9F5` | `#1E1D1B` | window base, main panel |
| oat | `#F0EEE6` | `#2C2A27` | bubble, segmented control, hover row, icon chips |
| side | `#F4F2EC` | `#232220` | sidebar, title bar, tab strips |
| line / line2 | `#E6E3D9` / `#D9D5C8` | `#353330` / `#45423D` | dividers / control borders |
| panel / field | `#FFFFFF` / `#FFFFFF` | `#1A1917` / `#262523` | right pane / inputs, popovers |
| ink / ink2 | `#1F1E1C` / `#3D3B37` | `#EEEAE2` / `#D3CEC4` | text / secondary |
| mute / faint | `#85827A` / `#B8B3A6` | `#8F8B82` / `#5E5A53` | labels / placeholders |
| clay / clay2 / clayT | `#D97757` / `#BD5D3A` / `#F6E3DA` | `#E08565` / `#EE9A7B` / `#43291F` | accent / accent text / tint, focus halo |
| add / addI | `#E7F3E8` / `#2F7D3F` | `#1C3022` / `#80C98F` | added bg / pass |
| del / delI | `#FBE9E7` / `#B4432F` | `#3A211C` / `#EC907C` | deleted bg / minus |
| plan / planB / planI | `#EEF0FA` / `#C9CDEA` / `#3F4AA0` | `#262A45` / `#3C4270` / `#A9B1F0` | plan-mode badge |
| sel | `#E8E4DA` | `#34322E` | selected row |
| kk / ks / kf | `#9A4E8E` / `#3F7A4A` / `#355F9E` | `#D39AD0` / `#9BCB8E` / `#8EB6EE` | code keyword / string / function |
| shadow | `rgba(60,40,20,.14)` | `rgba(0,0,0,.5)` | window, popover shadow |
| w0; w1 / w2 / w3 | `#E9E1D1`; `#F3D9C6` / `#E7C6AE` / `#EFE6D2` | `#171614`; `#3A2A24` / `#2A2230` / `#2E2922` | wallpaper base; three glows |
| menubar | `rgba(250,249,245,.6)` | `rgba(30,29,27,.6)` | desktop menu bar |
| appBg / appInk / appSoft | `#FFF8F0` / `#2B2320` / `#F4E7D8` | `#1E1A18` / `#F4EDE4` / `#352E2A` | app under test in a preview |

Pixel colours (theme-independent): body `#D97757`, bottom-row shade `#B85F40`, eye `#2A2622`; pencil `#F2C14E`, eraser `#E8A898`, heart `#E0543A`, check `#2F9E4F`, sweat `#7FB6E8`, dust `#CFC7B6`, spark `#F2C14E`, white `#FFFFFF`. Wipe blocks `#D97757` `#CF6E4E` `#E08A6B`; wipe label and runner `#FFF8F0`, runner shade `#F3DCCD`. Fixed: keystroke panel `rgba(31,30,28,.88)`, key cap `#3A3834`, key label `#D8D2C6`, window dots `#EC6A5E` `#F4BF4F` `#61C554`.

The mascot uses one saturated hue no UI element uses, findable in any frame; if the product itself is clay-coloured, recolour body/shade (or use the user's own mascot colour). Overlays are neutral frosted glass with at most one accent. A theme switch may become plot (dark mode takes over the film, then comes back).

## Type

- UI sans: `-apple-system, "SF Pro Text", "Helvetica Neue", "PingFang SC", "Hiragino Sans GB", "MV Noto Sans SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`.
- Serif (headlines, chapter names, big numbers, plan title): `Georgia, "Songti SC", "MV Noto Serif SC", "Noto Serif CJK SC", "Source Han Serif SC", SimSun, "Times New Roman", serif`; display sizes put `Baskerville` first.
- Mono (code, file tree, fast-forward tag, wipe number): `"SF Mono", Menlo, "MV Noto Sans Mono", "PingFang SC", "Hiragino Sans GB", "MV Noto Sans SC", monospace`.

| Role | Size | Weight / details |
|---|---|---|
| body, bubble, reply | 16 | 400, line-height 1.55 |
| prompt input | 17 | line-height 1.5 |
| sidebar row | 14 | 400, selected 500; section 12/600 mute, tracking .02em |
| tabs, menu bar, pills, URL bar | 13.5 | 500 |
| small, sub, kbd hints | 12-12.5 | 400-600 |
| popover item / stat chip | 14.5 | 400 / 600 tabular |
| plan body / plan title | 15.5 / 28 serif | line-height 1.6 / 500 |
| code, diff, tree / tool arg | 14.5 / 13 mono | diff line-height 27 |
| empty-state headline | 96 serif | 340, tracking -.02em, lh 1.2; sub 0.27 x size, 500, clay2 |
| chapter wipe name | 112 serif (84 if W < 1300) | 400, tracking -.02em, lh 1.1; number 30 mono 700, tracking .2em, opacity .8 |
| big number in app under test | 150 serif | 300, tracking -.02em, tabular |
| karaoke slogan | 64 serif | 400, tracking -.01em, word gap 28 |
| chapter pill | 30 serif | 450; number chip 14 sans 700, tracking .04em |
| keystroke HUD / keys | 17 / 18 | 600 |
| fast-forward tag | 22 mono | 700, tracking .02em |
| caption | 33 (portrait 38) | 500, lh 1.3 |
| terminal / toast | 19 mono lh 1.58 / 15 | 400 / 600 |

Every UI row `white-space: nowrap`; UI copy is real product copy in the film's language.

## Layout grid

- Standard window `{x:110, y:74, w:1700, h:950}`; title bar 46; sidebar 262; right pane 520 (900 for a preview); main between. Whole-window camera `[960, 548, 1.02]`. Desktop menu bar 30 high.
- Prompt box `{main.x+40, win.y+win.h-26-118, main.w-80, 118}`; user bubble right-aligned 56 from main right, 560 wide, top `main.y+86`; tool rows at `main.x+56`, every 34.
- HUD margins 56 (40 if W < 1300): chapter pill top-left (56, 48); fast-forward tag top-right (top 48); keystroke HUD bottom-left, top `H-56-58`. Bottom 160 reserved for captions.
- Positions come from boxes, never hand-typed: `topOf(b, fx, dy) = {x: b.x+b.w*fx, y: b.y+dy}` (only place the mascot stands); `pointIn(b, fx, fy)` (cursor targets). Derived: popover item i `{x+7, y+7+52i, w-14, 52}`, popover height `14+52n`; session i `{x+12, y+102+38i, w-24, 34}`; file row i `{x+20+18*depth, y+14+34i, ~8.7u per char, 34}`; diff line i `{x+(files?210:0)+66, y+12+27i, 420, 27}`; list row i `{x, y+62i, w, 52}`; send button `{x+w-52, y+h-46, 34, 34}`; mode pill `{x+58, y+h-44, 96, 30}`; plan item i `{x+66, y+121+40i, 400, 26}`. You may also measure real elements once with `getBoundingClientRect()` before building the timeline (divide by u, undo camera); never inside onUpdate.
- Framing check at every resting camera `[x, y, z]`: visible world is `x +/- W/2/z`, `y +/- H/2/z`; the explained element and the mascot on it (plus 11 x px above) stay inside with >= 5% margin and above the caption zone. Never crop the line being explained.
- Frame 0 has content (existing chat, list, or a prompt being typed); the camera may start pushed in.
- Portrait (inferred): window `{x:40, y:120, w:1000, h:1560}`, no sidebar, main on top, 700-high pane below; HUD margins 40; mascot px 6-7; karaoke at most 2 words per line.

## Camera grammar

One continuous take, `z >= 1`; reposition only under a chapter wipe. Camera = world point at frame centre `(cx, cy)` + zoom `z`, applied to `#camera` with `transformOrigin: "0 0"`: `x = u*(W/2 - z*cx)`, `y = u*(H/2 - z*cy)`, `scale = z`. Zoom blends in log space; built-in drift `cx += sin(0.7t)*1.2`, `cy += sin(0.53t+1)*0.9`. Each move starts from the current value of the previous (overlap allowed).

| Shot | Expresses | How |
|---|---|---|
| push to cursor / caret | "it happens right here" | `power2.inOut` 1.5-2.4 s to z 1.6-1.85 |
| resting drift | the app is alive during narration | drift always on; small smoothstep push/pull over 2-3 s |
| whip pan + blur | cause here, effect there | `power4.inOut` (quint) 0.35-0.45 s; blur from camera speed |
| push + spotlight hold | the one promise | to z ~1.85 in 0.8 s, spotlight in 0.3 s, hold 1.5-2.5 s; 1-2 per film |
| focus shift | attention moves | unread panel blurs to 6u over 0.3 s; read side sharp |
| pull back | whole flow at a glance | z 1.0-1.05, `power2.inOut` or smoothstep 0.8-2.4 s |
| follow the mascot | software processes item by item | smoothstep follow while it hops row by row |
| split reframe | two things at once | window splits, pull back to z ~1.03 |
| reposition under wipe | new chapter | 0.1 s step while the wipe fully covers |

Directional blur, whips only: screen displacement over the last 1/24 s, `v = (cam(t).x - cam(t-1/24).x) * z` per axis; radius `min(36, max(0, |v|-30)*0.3)`; apply when > 0.6 as `feGaussianBlur stdDeviation="bx*u by*u"` (filter region -5% / 110%) on an untransformed outer wrapper, never on the camera layer (it smears the pixels). Pull-backs of 0.8 s or more stay sharp. Portrait: zoom one step less (z 1.3-1.6), whips vertical.

## Motion

30 fps, about 100 BPM (beat 0.6 s); landings and the headline reveal fall on beats. One take per chapter, 6-9 s per segment.

| Action | Duration | Ease / formula |
|---|---|---|
| cursor segment | 0.25-0.45 s | `power2.inOut` with side arc 20-40 (default 40): `p = lerp(a,b,e) + n*arc*sin(PI*e)`, n = (-dy/L, dx/L) |
| cursor show / hide | 0.15 / 0.2 s | linear |
| click | scale 0.82 for 0.1 s; ring 0.45 s | ring radius 0 -> 26u, 3u clay border, opacity (1-k)*0.8 |
| cursor ghosts | speed > 900 u/s | 4 copies at t - 0.012i, opacity 0.16*(5-i)/4 |
| typing | 9-15 chars/s (default 14) | gap `(1/cps)*(0.55 + 0.9r)`, +0.03 on a space, +0.12 after `，。,.`; r = hash(31seed + 7.3i) |
| popover | 0.2 s | opacity min(1, 1.5p), scale 0.92 -> 1, origin 20% 100% |
| sent message flies up | 0.35 s | `power2.out` |
| keystroke HUD | rise 20u in 0.3 s, total 1.5 s, fade out 0.25 s | `back.out(1.7)`, fade in 0.15; key press 0.18 s sinks 2u, turns clay |
| chapter pill | fade 0.3 s, 0.6 s after transition start | linear |
| empty-state headline | ~0.5 s | char k = clamp(1.6p - 0.06i), rise 40 -> 0 `power2.out` |
| karaoke word | times[i] - 0.08 to + 0.12 | `back.out(1.7)`, rise 18 |
| focus blur / spotlight | 0.3 s | linear |
| screenshot flash | 0.3 s | white 0.5 -> 0 |
| opening fade | 0.5 s | ivory overlay 1 -> 0, `power2.out` |
| spinner / skeleton | 360 deg/s / opacity 0.5+0.5 sin(9t) | linear |

Mascot:
- Jump: anticipation 0.14 s before take-off squashes to 0.28 (smoothstep); airborne stretch -0.22 -> 0 over the first half then to -0.12, legs tucked, arms up; quadratic arc with apex `h` above the higher end (hops 20-60, cross-panel 110-230); landing 0.22 s `sq = 0.34*(1-k)*cos(7k)`; dust 0.4 s (10 grains on a heavy beat, else 6).
- Jump time: neighbours 0.26-0.32 s; across regions 0.42-0.55 s. Row hops: jump 0.2-0.26 s, h 16-26, stand >= 0.2 s (0.06 s in fast-forward, a row per 0.26 s).
- Walk: smoothstep position, legs alternate 9 steps/s, faces its direction.
- Breath: standing, first 0.12 s of each beat adds `0.07*sin(PI*bp/0.12)`; `still` disables.
- Blink: ~every 2.9 s (window i offset hash(3.7i + seed)*2 s), 0.1 s.
- Gaze `n l r u d wide happy shut blink` toward what matters (popup, cursor, changing line); arms `down up wave tuck`.
- Stepped feel: x snaps to half a pixel, `round(x/(px/2))*(px/2)`; pose swaps are instant.

Eases: `out` = `power2.out`, `in` = `power2.in`, `inOut` = `power2.inOut` = cubic-bezier(0.65,0,0.35,1), `back` = `back.out(1.7)`, `whip` = `power4.inOut`, `smooth` = `p => p*p*(3-2*p)`, damped `spring` = `p => 1 - Math.exp(-6*p)*Math.cos(9.5*p)*(1-p)`. The UI's own animations follow the real product; never invent effects it lacks.

## Materials and light

- UI: flat fills, 1px lines, soft warm shadows. Window radius 14, `0 0 0 1px rgba(0,0,0,.09), 0 30u 80u shadow, 0 8u 24u shadow`; popover radius 12, `0 12u 36u shadow`; prompt radius 18, `0 2u 10u shadow`, focus = clay border + `0 0 0 3u clayT`. No highlights, textures or grain.
- Wallpaper (procedural, oversized from (-1200, -900) to (W+1200, H+900) so pushes never show an edge): w0 plus three radial glows `color 0, transparent 70%` at world points: w1 ellipse 900 x 620 at (350, 200); w2 1000 x 700 at (1650, 880); w3 700 x 500 at (1340, 100); scale by W/1920, H/1080 in portrait.
- Mascot: flat square pixels; only the bottom body row is shade; standing shadow = flat ellipse 12px x 0.8px at the feet, `rgba(0,0,0,.16)`; none airborne.
- Depth only for attention: focus shift `filter: blur(6u)` on the unread panel; spotlight = full-frame `radial-gradient(ellipse 1.6r r at C, transparent 60%, rgba(25,22,18,.55) 100%)`, C = target centre on screen, `r = max(w,h)*z*0.75 + 40`. Text being read is never blurred.
- Frosted glass (pill, captions): `backdrop-filter: blur(14u)`; light `rgba(250,249,245,.9)` + `0 0 0 1px rgba(0,0,0,.06), 0 8u 28u rgba(60,40,20,.12)`; dark `rgba(30,29,27,.85)` + `0 0 0 1px rgba(255,255,255,.08), 0 8u 28u rgba(0,0,0,.4)`.

## Technique library

Capabilities, not obligations. Scene build order: (1) one constant table of event times (typing, key press, send, whip, each landing, push, hold); (2) constant boxes, every position derived; (3) mascot track and UI state both read that table: landing i lights row i, `lit = t >= LAND[i] && t < LAND[i] + stand + 0.05` (lit after take-off looks late); (4) stand points avoid text: the body covers the row above, so pick an empty x there (lists fx 0.35-0.5, file tree 62% of row width, px 5-6); a big jump's h <= distance to the window top minus 11 x px; (5) camera last, framing checked at every rest; (6) frame 0 has content.

- **Clock render (core pattern).** Camera, mascot, cursor and blur are pure functions of t, called from one linear clock tween; any seek gives the same picture. Layers: blur wrapper > `#camera` > world (wallpaper, window) then sprites (mascot, cursor, props); HUD outside the camera. Noise: `hash(n) = frac(sin(12.9898n + 78.233)*43758.5453)` or seeded mulberry32.
  ```js
  const u = MV.u, W = MV.width / u, H = MV.height / u;
  const E = {inOut: gsap.parseEase("power2.inOut"), whip: gsap.parseEase("power4.inOut"), smooth: p => p*p*(3-2*p)};
  const cam = t => { let [x, y, z] = START, lz = Math.log(Math.max(1, z));
    for (const m of MOVES) { if (t <= m.t0) break; const p = E[m.ease || "inOut"](Math.min(1, (t - m.t0) / m.d));
      x += (m.to[0] - x) * p; y += (m.to[1] - y) * p; lz += (Math.log(Math.max(1, m.to[2])) - lz) * p; }
    return {x: x + Math.sin(t*.7)*1.2, y: y + Math.sin(t*.53 + 1)*.9, z: Math.exp(lz)}; };
  function render(t) { const c = cam(t), b = cam(t - 1/24), f = v => Math.min(36, Math.max(0, Math.abs(v) - 30) * .3);
    gsap.set("#camera", {x: u*(W/2 - c.z*c.x), y: u*(H/2 - c.z*c.y), scale: c.z, transformOrigin: "0 0"});
    const bx = f((c.x - b.x)*c.z), by = f((c.y - b.y)*c.z);
    mb.setAttribute("stdDeviation", `${bx*u} ${by*u}`); wrap.style.filter = bx > .6 || by > .6 ? "url(#ls-mb)" : "none";
    drawMascot(t); drawCursor(t, c); }
  const clock = {t: 0};
  tl.fromTo(clock, {t: 0}, {t: DUR, duration: DUR, ease: "none", onUpdate: () => render(clock.t)}, 0); render(0);
  ```
- **Redrawn app window.** Title bar with dots and segmented control, sidebar sessions, tab strips, bubble, @-mention chip, tool rows (spinner -> green tick), stat chip "n files +a -d", prompt box with mode/model pills and clay send button, popover, file tree, plan card (items grow `20 + 520p` wide), diff with inline comment box, list rows, checks bar, toggle, toast, terminal, preview (URL bar, skeleton, app under test). Constants live in the `ls-` classes of the .css, except: tab strip 46 (tabs padding 6 12, radius 7, active on oat); session row 34 (status square 8u radius 2, running = clay + 3u clayT ring); tool row 26 (14 mute, verb 600 ink2, arg 13 mono, 18u oat icon chip radius 5); stat chip padding 8 14, radius 10, line2 border, 14.5/600 tabular, hot = 3u clayT ring; toast `#1F1E1C`, white, radius 10, padding 9 15, 15/600, `0 8u 20u rgba(0,0,0,.25)`; terminal `#1F1D1A` radius 12, bar 34 `#2A2724` with `#8A857A` 12.5 title, text `#E8E2D6` 19 mono lh 1.58, padding 18 22, ok `#8BC48A`, accent `#D97757`, `0 0 0 1px rgba(0,0,0,.2), 0 30u 80u rgba(60,40,20,.3)`. The original draws a generic menu bar and window dots; keep them generic with the user's product name, never a real logo.
- **Pixel mascot (optional).** 16 x 11 grid, pixel 7u default (5-6 on rows, 6 for clones). Inline SVG `viewBox="0 0 16 11" shape-rendering="crispEdges"`, rects 1.04 wide (no seams); for a CSS-grid or bitmap version add `image-rendering: pixelated`. Body cols 2-13 x rows 1-8 minus the 4 corners, row 8 = shade, antenna (10,0),(11,0). Pose data (`[col,row]`; legs `[col,length]` from row 9 down):
  ```js
  const ARMS = {down: [[0,5],[1,5],[0,6],[1,6],[14,5],[15,5],[14,6],[15,6]],
    up: [[0,1],[1,1],[0,2],[1,2],[0,3],[1,3],[14,1],[15,1],[14,2],[15,2],[14,3],[15,3]],
    wave: [[0,5],[1,5],[0,6],[1,6],[14,1],[15,1],[14,2],[15,2],[14,3],[15,3]], tuck: []};
  const LEGS = {stand: [[4,2],[5,2],[10,2],[11,2]], walkA: [[4,2],[5,2],[10,1],[11,1]],
    walkB: [[4,1],[5,1],[10,2],[11,2]], tuck: [[4,1],[5,1],[10,1],[11,1]], none: []};
  const EYES = {n: [[5,3],[5,4],[10,3],[10,4]], l: [[4,3],[4,4],[9,3],[9,4]], r: [[6,3],[6,4],[11,3],[11,4]],
    u: [[5,2],[5,3],[10,2],[10,3]], d: [[5,4],[5,5],[10,4],[10,5]], blink: [[5,4],[10,4]],
    wide: [[5,2],[5,3],[5,4],[10,2],[10,3],[10,4]], happy: [[4,4],[5,3],[6,4],[9,4],[10,3],[11,4]],
    shut: [[4,4],[5,4],[6,4],[9,4],[10,4],[11,4]]};
  ```
  Build the body once plus one `<g>` per arms/legs/eyes variant; per frame show the active variant (opacity 1, others 0). Wrapper at `(x - 8px, y - 11px)` (feet point), `scaleX = 1 + 0.7sq` (negative when flipped), `scaleY = 1 - sq`, origin `50% 100%`; somersault rotation (one turn over the jump) on an inner group, origin 50% 60%. Track = segments `stand {at} | walk {from,to} | jump {from,to,h,big} | hide`, each with optional px, eyes, arms, still. Jump: `x = lerp(a.x, b.x, p)`, `apex = min(a.y, b.y) - h`, `cy = 2*apex - (a.y+b.y)/2`, `y = (1-p)^2 a.y + 2(1-p)p cy + p^2 b.y`. Head point for props = feet y - 11px(1-sq). A leg cycle can also be a tween on a frame index with `ease: "steps(9)"` per second.
  ```js
  let sq = seg.k === "jump" ? (p < .5 ? -.22*(1 - 2*p) : -.12*(2*p - 1)) : 0;
  for (const j of jumps) { if (t >= j.t0 - .14 && t < j.t0) sq = Math.max(sq, .28 * smooth((t - j.t0 + .14) / .14));
    if (t >= j.t1 && t < j.t1 + .22) { const k = (t - j.t1) / .22; sq = .34 * (1 - k) * Math.cos(7*k); } }
  ```
- **Pixel props and particles** (same grid, centred beside the head; `.` empty). bang `.kkk. kwwwk kwkwk kwkwk kwwwk kwkwk kwwwk .kkk. ..k..` (k eye, w white); question `.kkk. kwwwk kkkwk ..kwk .kwk. .kkk. .kwk. .kkk.`; pencil `......kk .....yyk ....yyy. ...yyy.. ..yyy... .pyy.... ppy..... k.......` (y pencil, p eraser); spark `..y.. ..y.. yy.yy ..y.. ..y..`; heart `.rr.rr. rrrrrrr rrrrrrr .rrrrr. ..rrr.. ...r...`; check `.....g ....gg g..gg. gggg.. .gg...`; sweat `.b. bbb bbb .b.`; thinking dots `kk.kk.kk` x 2 rows, 1-3 dots. Dust grain i: side alternates, j = floor(i/2)+1, `dx = side*(18 + 16j + (7seed + 13i) % 9)*e`, e = 1-(1-k)^2, `dy = -(8 + 5j)*sin(0.9PIk)`, size `max(3, 0.7px)*(1-0.7k)`, opacity 1-k. Speed lines: 4 bars (3 + 3(i%2))px long, px high, 40 + 12i behind, y offset ((23i+11) % 60) - 70, body colour at (1-k)*0.7. Pixel burst (clone/merge 0.45 s): 14 squares of 7u, angle hash(2.3i + seed)*2PI, distance (0.22 + 0.78 hash(7.7i + seed))*90*`power2.out`(k), y*0.7, size*(1-0.6k), colours body, body, spark. Spark ring: 6 sparks px 4, angle 2PIi/6 + 0.3, radius 30 -> 90, y*0.7, opacity 1-k.
- **Avatar origin.** Opening: the mascot grows out of something already in the UI (a block letter, an icon, a spinner) with a pixel burst, leaving a dashed outline.
- **In-story title.** Product name as the empty-state headline; the mascot lands on its letters on a heavy beat.
- **Moving floor.** The mascot rides the caret (one step per typed char), the popover top, the flying bubble, a progress bar; it moves with the element, never slides.
- **Labelled fast-forward.** Long tasks at N x: tag flashes filled/outline arrows every 0.25 s (`floor(4t) % 2`), the other panel blurs, the mascot hops a row per 0.26 s.
- **Spotlight hold.** Push to z ~1.85, vignette around the promise badge, mascot on it with `happy` eyes.
- **Whole-film state change.** A circle from the toggle swallows the app and desktop; everything after is dark; at the end one stomp brings light back from the mascot's feet. World built twice (second copy has class `ls-dark`), top copy clipped by `clip-path: circle(r at x y)` per frame: in `r = 3200k^2.4 + 20` over 1.5 s; back `r = 2600(1-(1-k)^3) + 10` over 1.0 s; then the base theme becomes the new one; HUD flips when r > 0.6 x frame diagonal. Spark ring at the stomp.
- **Cell division.** A new session splits the window; the mascot bursts into two (second px 6), one per column.
- **Cursor meets mascot.** Cursor to the head; mascot squashes, `shut` eyes, heart prop 0.9 s (approving a change).
- **Karaoke slogan.** Words appear on their spoken times; mascot hops word to word, last word big jump, clay2 italic.
- **Screenshot flash.** White flash 0.3 s plus a toast when the software screenshots itself.
- **Cursor.** Arrow path `M2 2 L2 24 L7.5 18.5 L11 27 L15 25.2 L11.6 17 L19 17 Z`, viewBox 22 x 30, black fill, white 1.6 stroke, round joins, `drop-shadow(0 2px 3px rgba(0,0,0,.3))`, 1.6 x system size, constant on screen (scale 1/z inside the camera), hotspot offset 2 x scale.

## Transitions

- Between scene files always `cut`; the original never used a dissolve, so never `fade`.
- **Chapter pixel wipe** (0.85 s = 25.5 frames), split across the cut: the outgoing scene plays p 0 -> 0.5 in its last 0.425 s (13 frames); the incoming scene plays p 0.5 -> 1 in its first 0.425 s, camera already at the new position. Full cover at p 0.44-0.55 hides the cut. 60u blocks sweep bottom-left to top-right, then uncover in the same direction; centred label (mono number over serif name) for p 0.3-0.82, rising 16u as it fades in; a cream mascot (px 6, no shadow, legs 12 steps/s) runs at `y = H/2 + 250`, `x = (300 + 1320p)/1920 * W`. New chapter pill fades in 0.3 s at ~0.18 s into the incoming scene.
  ```js
  const S = 60, cols = Math.ceil(W/S), rows = Math.ceil(H/S);  // blocks (S+0.5)u squares, centre origin
  // per block: d = c/cols*.55 + (rows - r)/rows*.2 + hash(13.1c + 7.7r)*.12; colour = WIPE[floor(hash(3.7c + 11.3r)*3)]
  function wipe(p) { for (const b of blocks) { const a = clamp((p/.42 - b.d)/.18), e = clamp(((p - .55)/.42 - b.d)/.18);
      gsap.set(b.el, {scale: a*(1 - e)}); }
    const lo = clamp(Math.min((p - .3)/.12, (.82 - p)/.1)); gsap.set(label, {opacity: lo, y: (1 - lo)*16*u}); }
  ```
- **Theme reveal circle** (in-scene, above) is the other style-owned change. Film opening: 0.5 s ivory fade-open. No black frames, no screenshot transitions.

## Captions

Frosted glass pill, bottom centre: light = ivory glass 88% with ink text; dark = warm black 82% with `#EEEAE2`. 33u, 500, lh 1.3, padding 12u 26u, radius 16, `backdrop-filter: blur(14u)`, one line up to 73% of frame width (portrait 38u, may wrap, up to 86%). The pill stays all scene, only text swaps; trailing punctuation dropped; `{word}` in clay2 (`#EE9A7B` dark). Keep explained elements and the mascot out of the bottom 160u. Dark scenes: class `ls-dark` on `<body>` switches caption tokens.

```css
--cap-bg: rgba(250,249,245,.88); --cap-fg: #1F1E1C; --cap-font: <sans stack>; --cap-size: calc(33 * var(--u));
--cap-radius: calc(16 * var(--u)); --cap-bottom: calc(52 * var(--u));   /* portrait: 200u, size 38u */
--cap-weight: 500; --cap-accent: #BD5D3A;
--cap-shadow: 0 0 0 1px rgba(0,0,0,.06), 0 calc(8*var(--u)) calc(28*var(--u)) rgba(60,40,20,.14);
/* dark: bg rgba(30,29,27,.82), fg #EEEAE2, accent #EE9A7B, shadow 0 0 0 1px rgba(255,255,255,.08), 0 8u 28u rgba(0,0,0,.4) */
```

## Image-gen guidance

(none) Everything (desktop, windows, icons, mascot, props) is drawn in code; the point is UI that stays sharp at any zoom. User content inside the app under test (an avatar, a product photo) comes from a user-supplied local image cropped into its UI frame; never a generated UI, a screenshot or a logo.

## Taboos

- No crossfade, no screenshot transitions, no black frames.
- No hands, arms or fingers; the cursor is the user.
- No screenshots or live footage; no placeholder copy; no invented features, modes or shortcuts.
- No black tech stage, no invented abstract UI, no overlays beyond the four kinds; no textures, grain, highlights or reflections.
- No zoom below 1, no framing outside the monitor; no blur on text being read; no directional blur on slow pushes.
- Mascot never floats in empty space, covers text being read, enters the caption zone, or renders anti-aliased.
- No real company logos or brand marks.

## Known gaps

- Fonts: the original used Inter, a Newsreader-like serif and JetBrains Mono; system SF, Georgia/Baskerville and SF Mono differ in width and contrast; serif weights 300-450 fall back to 400.
- Positions are estimated from boxes and char widths (mono 8.7u/char, CJK = size, Latin = 0.5 x size); landings can be off a few u; measure once at build or adjust dy.
- Directional blur is a Gaussian approximation and slows rendering; `backdrop-filter` may look weaker in headless capture.
- The pixel wipe must be split across a cut; the theme reveal doubles the world DOM.
- No sound design: the original's dual-resolution score (band + chiptune), 8-bit footsteps and keyboard foley are not part of the style; use synthesized SFX within contract limits.
- The mascot design (rounded body, one antenna, two legs) is original; portrait layout is inferred.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
