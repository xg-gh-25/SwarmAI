# Scene contract (how to write scenes/SNN.html)

One scene = one HTML page with **one paused GSAP timeline**. `scripts/render.py` opens the page once,
then for every frame `i` seeks the timeline to `i / fps` and takes a screenshot. The picture must be a
pure function of time: seeking to 1.20 s gives the same frame whether you came from 0 s or from 3 s.
Anything driven by real time (timers, CSS animation, `Date`, randomness) breaks this and is rejected by
`check.py`. Numbers below come from `project-contract.md`; that file wins if they ever differ.

Start from `templates/scene.html` (a correct, minimal scene: three elements cued to keywords, one
progressive reveal, the film layer with its slow camera push).

## URL scheme (what a page may load)

The renderer serves two roots from `http://127.0.0.1:<port>/`; every other request is blocked.

| URL | Serves |
|---|---|
| `/skill/runtime/gsap.min.js` | GSAP 3 (vendored, no plugins) |
| `/skill/runtime/scene-runtime.js` | the scene runtime (`window.MV`) |
| `/skill/runtime/film-layer.js` | the film layer (`window.FL`, see below) |
| `/skill/references/styles/<style>.css` | the film's style tokens; `<style>` = storyboard `"style"` |
| `/project/scenes/shared.css` | the project's shared styles (optional; may be linked even if absent) |
| `/project/scenes/film-data.js` | the film data `scripts/film.py` writes (must exist when linked) |
| `/project/assets/...` | images, logos, local fonts, muted video clips |

The page itself is `/project/scenes/SNN.html`, so relative paths also work (`../assets/logo.png`).
Only `runtime/`, `references/`, `templates/`, `assets/` of the skill are served. No `http(s)://`, no `//host`,
no web fonts: system fonts or `@font-face` with `url(/project/assets/fonts/...)`.

## Required structure

```html
<head>
  <script src="/skill/runtime/gsap.min.js"></script>          <!-- 1. GSAP -->
  <script src="/skill/runtime/scene-runtime.js"></script>     <!-- 2. runtime (after GSAP) -->
  <script src="/skill/runtime/film-layer.js"></script>        <!-- 3. film layer + this film's data -->
  <script src="/project/scenes/film-data.js"></script>
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
  FL.setup(tl, { mask: "left" });          // right after the timeline: the film's shared parts
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

## The film layer: `window.FL`

The parts every scene of a film shares come from one place, so no film rebuilds them: a background
**plate** behind `#camera` (an image from the project's `assets/` and/or a style class, drifting slowly,
direction alternating scene by scene), a **mask** (directional shade: `left`, `right`, `center`, `warm`),
a **vignette**, the chapter **kicker** (chapter number, chapter name, section title; static, top left),
the film-wide **progress** bar on the bottom edge with chapter marks (each scene's fill runs from its
start to its end, so neighbours meet exactly) and the shot-long camera **push** of `#camera` (log space,
constant perceived speed). Every part is optional.

- **One place per project:** `film.json` in the project (copy `templates/film.json`, keep only what you
  change). It switches the parts on for the film and names the plate image(s) (`"images": {"S03": ...}`
  for a scene with its own photo), the chapter shot types (default `chapter-card`) and the shot types
  without kicker or bar. Turn on what the style file asks for (exec-dark: plate, mask, vignette, kicker,
  progress); without `film.json` only the push is on.
- **Film data:** `python3 "$SKILL_DIR/scripts/film.py" --project "$P"` writes `scenes/film-data.js`
  (film length, each scene's start, chapter marks, each scene's kicker and the next section's title, the
  settings). **Re-run it after every `tts.py`** (durations change) and after editing `film.json`, a plate
  image, or the storyboard's order, titles or shot types. `film.py --check` exits 1 when the data is stale.
  A scene whose duration differs from the data stops with a page error naming `film.py`, so `check.py`
  and `render.py` fail instead of drawing a wrong progress bar. `render.py` fingerprints the whole file,
  so every scene that loads it re-renders when it changes (with the progress bar on, every scene's
  picture does change when any duration does).
- **In the scene:** `FL.setup(tl, opts)` right after creating the timeline, before the scene's own tweens.
  Per-scene `opts`: `mask` (another side or `false`), `vignette` (`false`, `true` or extra class names),
  `plate` (`false` or `{class, drift}`), `push` (`false` or the end scale, default from `film.json`, 1.03),
  `origin` (the push's transform origin, put it on the focus), `kicker: false`, `progress: false`
  (these two are switched on only in `film.json`). An unknown option is a page error.
  It returns the elements it made (`plate`, `mask`, `vignette`, `kicker`, `progress`, `camera`).
- `FL.scene` is this scene's entry (`title`, `chapter {no, name}`, `section`, `next`, `kicker`,
  `progress`): use it for a chapter card that names the next section. Never read film totals or other
  scenes; a scene's picture depends only on its own entry. (`derive.py` treats a page that reads
  `FL.scene`, or loads a project script, as depending on its whole entry: a new cut re-renders it when a
  neighbour's title changes.) `FL.at(keyword, move)` is a cue clamped so a
  move of `move` seconds still settles by `MV.end()`.
- When the push is on, the layer owns `#camera`'s `scale`: move the camera with `x`/`y`, or animate an
  inner wrapper for a scene's own zoom.
- **The look is the style's.** Each part gets a neutral `fl-*` class whose colours and font are the
  style's own tokens (`runtime/film-styles.json`: accent, ink, muted, rule, font) plus the style's own
  class where it has one (exec-dark `ed-kicker`, `ed-progress`, ...); the neutral rules have zero
  specificity, so the style always wins. `film.json` `classes`/`tokens` adapt a style that is not mapped
  yet; the layer never brings a colour or font of its own.

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
  slow push on `#camera` (the film layer's push from `FL.setup`, about 3–5 % over the scene) or another
  slow drift. `defects.py` flags freezes from 2.5 s.
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

## Writing rules (every scene, one writer or many)

These hold for every film, written by the director alone or by parallel sub-agents
(`templates/scene-writer-brief.md` points here instead of repeating them).

- **Source wording exact.** Terms, numbers and labels on screen are the ones in the storyboard `visual` and
  the person's material (`85`, `5×`, `≥3×`, `7×24` stay exactly so). Never invent a fact, number or label;
  an approximate number stays approximate. No author or presenter name unless the person asked for it.
- **Cue only spoken words.** `MV.cue(word)` only with a word in `audio/voice/SNN.words.json`. It returns the
  first match; for a later repeat take the start from `MV.words` (e.g.
  `MV.words.find(w => w.start > 3 && w.text.includes("cost")).start - 0.1`).
- **Shown when spoken.** Each on-screen element appears when its word is spoken (`MV.cue` leads by 0.1 s).
- **Opening.** No key text in the first 0.4 s when the previous scene fades in (§ Timing); something
  moves by 0.3 s (the layer's push or plate drift, or a ghost structure).
- **Text hold.** Every key text block stays readable at least (CJK characters / 4.5 + other characters / 15
  + 1.5) s before the scene ends where the voice allows; no new block lands in the last 1.2 s: cue it to an
  earlier word.
- **One accent** per frame, on the focus; the style's alarm colour (often red) only for problems.
- **Layout.** Content inside the safe area and above the caption band; the main graphic about 30–50 % of
  the frame height unless the style's layout grid says otherwise. Text sizes from the style's type
  scale; where the style names no size for the role, labels at least 30u and card or body text at least
  36u. CJK and headlines `white-space: nowrap`.
- **Ghost, then ink.** A multi-part structure first shows whole as a ghost (about 20 % opacity), then each
  part inks on its cue; explained parts may step back to about 0.5. Elements do not leave mid-scene unless
  the narration removes them (a strike-out stays legible).
- **Neighbours differ.** Read the storyboard entries before and after yours (`shot_type`, `visual`) and do not
  repeat their layout (centred, split, stacked, band, triptych, grid).
- **Own files only.** In a film written by several writers each edits only its own `scenes/SNN.html`.
  `shared.css`, `film.json`, `scenes/film-data.js`, `design.md`, `storyboard.json` and shared scripts
  belong to the director; a writer who needs a shared change reports it.
- **Loop.** Per scene: `check.py --scene`, `render.py --scene --preview`, look at the three stills; at most
  3 rounds, then report what is left.

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
| Frozen stretch (defects: still) | everything landed, nothing drifts | the layer's push (`FL.setup`), plate drift, progress line |
| "FL: scenes/film-data.js is stale" / "no film data" | durations changed (tts.py) or film.py never ran | `python3 "$SKILL_DIR/scripts/film.py" --project "$P"` |
| Flash on one frame | element hidden for one frame (overlapping tweens, display toggles) | one owner per property; tween opacity, not display |
| Text jumps lines between frames | auto-wrapped CJK | `white-space: nowrap` + `<br>` |
| Caption covers a label | text placed in the bottom 16 % | move it above `var(--cap-band)` |
| Picture greyer than the design | (renderer handles full->limited range and BT.709) | nothing to do in the scene |
