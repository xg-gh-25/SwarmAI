# Scene contract (how to write scenes/SNN.html)

One scene = one HTML page with **one paused GSAP timeline**. `scripts/render.py` opens the page once,
then for every frame `i` seeks the timeline to `i / fps` and takes a screenshot. The picture must be a
pure function of time: seeking to 1.20 s gives the same frame whether you came from 0 s or from 3 s.
Anything driven by real time (timers, CSS animation, `Date`, randomness) breaks this and is rejected by
`check.py`. Numbers below come from `project-contract.md`; that file wins if they ever differ.

Start from `templates/scene.html` (a correct, minimal scene: three elements cued to keywords, one
progressive reveal, a slow camera push).

## URL scheme (what a page may load)

The renderer serves two roots from `http://127.0.0.1:<port>/`; every other request is blocked.

| URL | Serves |
|---|---|
| `/skill/runtime/gsap.min.js` | GSAP 3 (vendored, no plugins) |
| `/skill/runtime/scene-runtime.js` | the scene runtime (`window.MV`) |
| `/skill/references/styles/<style>.css` | the film's style tokens; `<style>` = storyboard `"style"` |
| `/project/scenes/shared.css` | the project's shared styles (optional; may be linked even if absent) |
| `/project/assets/...` | images, logos, local fonts, muted video clips |

The page itself is `/project/scenes/SNN.html`, so relative paths also work (`../assets/logo.png`).
Only `runtime/`, `references/`, `templates/`, `assets/` of the skill are served. No `http(s)://`, no `//host`,
no web fonts: system fonts or `@font-face` with `url(/project/assets/fonts/...)`.

## Required structure

```html
<head>
  <script src="/skill/runtime/gsap.min.js"></script>          <!-- 1. GSAP -->
  <script src="/skill/runtime/scene-runtime.js"></script>     <!-- 2. runtime (after GSAP) -->
  <link rel="stylesheet" href="/skill/references/styles/exec-brief.css">
  <link rel="stylesheet" href="/project/scenes/shared.css">
  <style>/* this scene's layout */</style>
</head>
<body>
<div id="stage" class="eb-stage">          <!-- the frame; style's stage class -->
  <div id="camera"> ... scene content ... </div>
</div>
<script>
  const tl = gsap.timeline({ paused: true, defaults: { ease: "power3.out", duration: 0.6 } });
  tl.fromTo("#title", { opacity: 0, y: 40 * MV.u }, { opacity: 1, y: 0 }, MV.cue("keyword"));
  // ... every tween ...
  MV.register(tl);                         // last line, after every tween is added
</script>
</body>
```

The runtime sizes `html`, `body` and `#stage` to the storyboard frame and appends the caption box.
Do not draw captions yourself.

## The runtime: `window.MV`

| Member | Meaning |
|---|---|
| `MV.width`, `MV.height`, `MV.fps`, `MV.duration`, `MV.frames` | from the storyboard (duration = rounded to whole frames) |
| `MV.u` | number: frame short side / 1080 (use in JS: `40 * MV.u` px) |
| `--u` (CSS) | the same as a length: write sizes `calc(N * var(--u))` (N px at a 1080 short side) |
| `MV.cue(keyword, lead = 0.1)` | seconds: start of the first spoken word/phrase containing `keyword`, minus `lead`. Case and punctuation are ignored; phrases may span words (`MV.cue("账单下降")`). **Throws** if the voice never says it. Before `tts.py` has run it estimates from the narration text (`MV.estimated = true`) |
| `MV.end(settle = 0.3)` | `duration - settle`: the time by which the last move must have landed |
| `MV.rand(seed)` | seeded PRNG (mulberry32): `const r = MV.rand("dots"); r()` -> [0, 1), same every run |
| `MV.register(tl)` | registers the paused timeline (exactly once) |
| `MV.words`, `MV.captions` | word timings and caption lines `[{text, start, end}]`, seconds from scene start |
| `MV.captionTop`, `MV.safe.x`, `MV.safe.y` | layout limits used by `check.py` (also CSS `--cap-band`, `--safe-x`, `--safe-y`) |

Renderer hooks (do not call them): `window.__MV_DATA` (injected before page scripts),
`window.__mvReady` (fonts loaded, images decoded), `window.__mvSeek(t)`.

## Timeline rules

- **One** `gsap.timeline({ paused: true })`, built synchronously (or inside `document.fonts.ready.then`),
  registered last. Never `tl.play()`, never a second timeline, never `gsap.ticker`.
- **`fromTo` or `set` only**, each at an **absolute time** (third argument): a number or `MV.cue(...)`.
  `gsap.to`, `gsap.from`, `tl.to`, `tl.from` and relative positions (`"+=0.2"`, `"<"`, `">"`) are not allowed.
  Sub-sequences: build a function that adds fromTo tweens at `t0 + k`, not nested relative timelines.
- When a second `fromTo` animates the same property of the same element (e.g. fade in, later fade out),
  give the later one `immediateRender: false`, otherwise it overwrites the initial state.
- The same holds for **shared state redrawn in `onUpdate`** (a proxy object whose values rebuild SVG, a text that
  several swaps write): every `fromTo` renders its start at creation, so the LAST one created wins frame 0 and
  everything before its start shows its start state. Give every tween after the first on that state
  `immediateRender: false`, or render text from one clock tween that computes it from the time.
- A static start state goes in `gsap.set(target, props)` before the timeline, not `tl.set(target, props, 0)`:
  a zero-length set at time 0 may not render on the first seek, so frame 0 differs from frame 1.
- Animate `x`, `y`, `scale`, `rotation`, `opacity` (and `clipPath: inset(...)` for wipes, `scaleX/Y` +
  `transformOrigin` for growing bars, `strokeDashoffset` for line draws). Not `left/top/width/height/margin`.
- Counters: `tl.fromTo(obj, {v: 0}, {v: 90, onUpdate: () => el.textContent = Math.round(obj.v)}, t)`;
  `onUpdate` only writes text, never measures layout.
- Finite repeats only: `repeat: Math.max(0, Math.floor(secs / period) - 1)`.
- Ease entrances `power3.out` / `expo.out`, exits `power2.in`, camera `power2.inOut` or `none` for a
  shot-long push; no bounce/elastic for routine entrances.
- Muted `<video muted playsinline>` only; set its `currentTime` from an `onUpdate`. No `<audio>`.

## Forbidden in scene files and shared.css (check.py fails on these)

`http(s)://` / `//host` URLs, `fetch`, `XMLHttpRequest`, `WebSocket`, `import(`, iframes, navigation,
`Math.random`, `Date.now`, `new Date`, `performance.now`, `setTimeout`, `setInterval`,
`requestAnimationFrame`, CSS `@keyframes`, `animation:`, `transition:`, `repeat: -1`, `<audio>`,
unmuted `<video>`, `.to(` / `.from(` calls, a missing `MV.register(`, missing runtime scripts.

## Timing (from project-contract.md)

- Elements appear about 0.1 s before their keyword: `MV.cue(keyword)` already subtracts it.
- The last motion settles 0.3 s before the scene end: clamp late cues with
  `Math.min(MV.cue(kw), MV.end() - <move length>)`.
- Every move lasts at least 0.25 s (8 frames at 30 fps), per item in a stagger too.
- The whole frame is never fully still for more than 2 s (except an end-card hold): keep one shot-long
  slow push on `#camera` (about 5 % over 5 s) or another slow drift. `defects.py` flags freezes from 2.5 s.
- `transition: "fade"` on a scene cross-fades its frozen last frame into the first 0.4 s of the NEXT scene.
  So: never fade a scene in or out yourself, and keep key text out of a scene's first 0.4 s when the
  previous scene fades into it.

## Layout

- Sizes: `calc(N * var(--u))`; style tokens (`--eb-margin`, `--eb-fs-statement`, ...) already use it.
- Safe area: 5 % of each side (`--safe-x`, `--safe-y`); the style's margins are inside it.
- **Caption band**: the bottom 16 % of the frame (higher if the style raises the caption box, e.g.
  portrait) belongs to captions. No author text in it (`check.py` fails). `--cap-band` is its height.
- Captions are drawn by the runtime: one persistent box, only the text swaps; a line lasts until the next
  starts; hidden when storyboard `captions` is `false`. Look comes from the style's `--cap-bg, --cap-fg,
  --cap-font, --cap-size, --cap-radius, --cap-bottom` (+ `--cap-weight, --cap-shadow, --cap-border`, and an accent
  mark on the left: `--cap-mark: tick | dot` in colour `--cap-accent`). Any other caption furniture a style wants
  (a top rule, a kicker, a marker dash) is drawn by the scene above the caption band.
- CJK and headline text: `white-space: nowrap` with explicit `<br>`, so line breaks never change between frames.

## Test one scene quickly

```bash
python3 "$SKILL_DIR/scripts/check.py"  --project "$P" --scene S03            # static + 3 stills + storyboard
python3 "$SKILL_DIR/scripts/render.py" --project "$P" --scene S03 --preview  # review/S03-{a,b,c}.jpg
```
Read the three stills (start, middle, end). `check.py` writes `review/check-S03.json`; it fails on page
errors (including an unknown `MV.cue` keyword), missing files, blocked requests, a blank middle/end
frame (pixel std < 4) and text in the caption band; it warns on text outside the safe area.
Then `render.py --project "$P" --scene S03` makes `build/S03.mp4` (skipped when nothing changed).

## Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Blank frames / "did not call MV.register" | tweens added after registration, or an exception before it | register on the last line; read the page error that check.py prints |
| "keyword ... is not in the narration" | cue word not spoken (or spelled differently) | cue a word from `audio/voice/SNN.words.json` |
| Element visible before its entrance, or missing later | two fromTo on one property without `immediateRender: false` | add it to the later tween |
| One-frame pop (check: tween shorter than 0.25 s; defects: jumps) | duration < 0.25 s, `set` used as an entrance, CSS state change | fromTo with duration >= 0.25 s and easing |
| Frozen stretch (defects: still) | everything landed, nothing drifts | shot-long push on `#camera`, slow drift, progress line |
| Flash on one frame | element hidden for one frame (overlapping tweens, display toggles) | one owner per property; tween opacity, not display |
| Text jumps lines between frames | auto-wrapped CJK | `white-space: nowrap` + `<br>` |
| Caption covers a label | text placed in the bottom 16 % | move it above `var(--cap-band)` |
| Picture greyer than the design | (renderer handles full->limited range and BT.709) | nothing to do in the scene |
