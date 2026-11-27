# Style: ink-wash (Chinese ink painting, shuimo)

Units: `u` = frame short side / 1080. At 1920x1080, 1u = 1px. In CSS write `calc(N * var(--u))` (see `ink-wash.css`). In GSAP use `const u = MV.u`. Inside the ink layer (one inline `<svg class="iw-ink" viewBox="0 0 1920 1080">`, portrait `0 0 1080 1920`) every number is already in u. Provenance: `(m)` measured from the reference stills or its timing numbers, `(s)` seen, `(i)` inferred.

## Identity

A Chinese ink painting (shuimo) painted before the viewer on warm rice paper. Identity rules, all mandatory: (1) ink only, in five tones from burnt black to clear grey, laid translucently so the paper always shows through; (2) exactly one colour besides ink, vermilion, kept for the seal plus, at most, one small story mark that rhymes with it; never red type, never a red wash; (3) 60-80 % of every frame is empty paper, which stands for sky, water, mist and silence, and the subject sits off-centre with a dark foreground allowed along the bottom; (4) forms are brushstrokes, never outlines with flat fills: loaded strokes that make their own dark rim serve as the outline, wet washes that bleed and pile pigment at their rim, quick dry strokes in which bare paper breaks through the hair marks (flying white); far is pale and soft, near is dark and textured; (5) every form appears by being painted, stroke after stroke, and text is calligraphy written into the empty paper in vertical columns read right to left, closed by the seal. The pace is calm: stillness, then one decisive stroke.

Best for: cultural and heritage brands, tea, food and craft, poetry, festival greetings, tourism and landscape, values and philosophy films, any story told as a metaphor (a process as a river, growth as a branch, a history as a handscroll). Not for: dense data, UI demos, specs, diagrams with many labels, beat-cut energetic promos, colourful brands.

## Palette

| Hex | Role |
|---|---|
| `#F2ECDE` | rice paper base (m); reads on screen as `#F4E9D6` centre, `#DBD2C0`-`#E3DAC8` corners (m). CSS paints `--iw-paper-raw #FCF3E1` under the noise so the average lands on `--iw-paper #F4EAD7` |
| `#13110F` @ .96 | jiao, burnt: calligraphy, crispest lines, moss dots (m) -> on paper `#12100D` |
| `#070604` @ .82 | nong, thick: contours, the dark side of a loaded stroke (m) -> `#322F2A` |
| `#1F1F1C` @ .60 | zhong, heavy: secondary lines, folds, streams (m) -> `#747067` |
| `#525450` @ .34 | dan, light: washes for mountains, clothing and bodies (m) -> `#BDB7A9` |
| `#70736D` @ .16 | qing, clear: distant hills, faces, mist (m) -> `#DFD7C6` |
| `#B02E22` | zhu, vermilion: seal (m); reads `#B23C30` on paper (m) |
| `#2F2B27` / `#D9CDB6` | dark table and silk mount around a scroll seen whole (end card only) (m)/(i) |

Ink runs from cool grey when thin to warm black when dense (m); the tone colours above already carry that. Overlapping strokes darken because each is translucent. Values carry depth: far = qing/dan and soft, near = nong/jiao and textured. When a frame feels crowded, take ink away instead of adding a tone. Accent rule: the vermilion seal is the only red on screen; one tiny story mark (an object the subject carries) may rhyme with it in the same frame, nothing else. No alternative accents: a brand colour never replaces vermilion; it appears only inside a photo or logo the brief requires.

## Type

Font stacks (macOS system fonts):
- Brush (calligraphy titles, a hero character): `"Xingkai SC", "Kaiti SC", "STKaiti", "Kai", "BiauKai", "Songti SC", serif`, weight 700 (Xingkai SC Bold).
- Kai (inscriptions, colophons, labels, captions): `"Kaiti SC", "STKaiti", "Kai", "BiauKai", "Songti SC", "Noto Serif CJK SC", serif`, weight 400.
- Song (seal glyphs, numerals in an inscription): `"Songti SC", "STSong", "Noto Serif CJK SC", serif`, seal weight 900.
- Latin (rare; English lines in an inscription): `"Hoefler Text", "Baskerville", "Palatino", Georgia, serif`, italic 400 for lines, upright capitals with tracking .18em for titles (m: 50 px caps, 9 px tracking).

| Role | Size (u) | Details |
|---|---|---|
| hero character | 360 (240-420) | brush 700, one character, written in the empty paper (s) |
| title column | 120 (96-150) | brush 700, vertical, line-height 1.12, tracking .06em, <= 8 characters |
| number in an inscription | 110 | song 400, tabular |
| inscription line / colophon | 46 (m) | kai 400, nong, tracking .08em horizontal, .12em in columns, <= 2 lines or <= 12 characters a column |
| label beside a painted thing | 38 | kai 400, zhong, 2-4 characters |
| Latin caps title | 50 (m) | upright caps, tracking .18em |
| small, minimum | 30 | kai 400 |

All text is ink on paper: no box, halo, outline or shadow. Every headline and column uses `white-space: nowrap` and one span per character, so line breaks never move between frames.

## Layout grid

- Frame 1920x1080; safe area 5 %: x 96-1824, y 54-1026. Inscription blocks inset >= 170u from the frame edge.
- Empty paper 60-80 % of the frame (m: calm frames measure 80-88 % paper, the climax 56 %). Subject on a third, never centred; a horizon low or high, never on the middle line. Mountain ranges and masses fade out sideways, never end with a hard edge.
- The inscription goes into the largest empty area: title column first (rightmost), the small colophon column 70-100u to its left, the seal under the end of the last column (seal edge about 0.6 x title size, 60-150u). In a landscape-wide frame, one horizontal inscription line of <= 16 characters may sit in the sky instead (s).
- Value: give most frames one near dark (a loaded nong/jiao stroke, rock or foreground reed) besides the text; dark ink covers 3-20 % of the frame (m: 3 % calm, 11-20 % dense).
- Per screen at most: one subject group (a mountain range, a branch, a figure, one great character), one title column, one colophon, one seal, three short labels.
- Subject size: a figure in a wide shot is tiny, 150-180u tall in blank paper (m); a single motif (leaf, branch, character) 40-60 % of frame height.
- Portrait (i): subject in the lower 55 %, title column top-right, colophon and seal to its left; or a hanging-scroll tilt from sky to ground. Captions sit at 210u from the bottom.

## Camera grammar

| Shot | Expresses | How |
|---|---|---|
| fixed frame on the sheet | the sheet is the stage; forms arrive by being painted | `#camera` slow push 3 % over the shot, `ease: "none"` (i) |
| handscroll pan, right to left | a span of time or road unrolled as a single picture | a world wider than the frame (2200-5600u); translate it +x, `power2.inOut`, 5 s per ~1560u (m); paper travels with it |
| hanging-scroll tilt | height; stages one above another | world taller than the frame, translate y, `power2.inOut` (i) |
| lateral tracking with lead room | travel, a journey | subject at ~60 % of frame width, far ranges at 0.2-0.4 x the near speed (m)/(i) |
| high wide, tiny subject | smallness, solitude | subject 150-180u in blank paper (m) |
| low angle under a looming dark mass | weight, threat | horizon low, slow push with a seeded shake of 1-3u growing over the hold (i) |
| hard cut into a very tight detail | rupture: a choice, a jolt | the one hard cut used inside a sequence (m) |
| push into spreading ink or mist | crossing from one state to another | push 5-10 % while the bloom covers the frame (i) |
| pull out until the small mark becomes part of the whole | the detail belonged to a larger picture | scale from 2.5 to 1 over 3-4 s, `power2.inOut`; may end on the mounted scroll (i) |
| repeat an earlier framing | the echo reveals the change | reuse the exact camera of an earlier shot (s) |

Before a decisive stroke: hold, with at most a 5 % push (m, `sine.inOut`), then the stroke crosses the frame in 0.25 s; what it causes begins once it has landed (m). No handheld wobble, no rotation, no whip pans.

## Motion

Pace: 60 BPM for landscape (beat 1.0 s), 90 BPM for travel (beat 0.667 s, steps on beats) (m). Strokes land on the beat; leave silences.

| Action | Duration | Ease / details |
|---|---|---|
| paint a stroke (reveal along its path) | 0.35-0.6 (m .35-.4 per stage) | `power2.out`; big side-brush strokes 0.5-0.6 |
| strokes in sequence | one every 0.6 s (m) | each finishes before the next starts |
| calligraphy character | 0.4 per character, one every 0.45 s (m: strokes .4-.55 with .2 gaps) | `power2.inOut`; top-down reveal + wet blur 7u -> 0 |
| inscription, label: wet -> dry | 0.35 (m) | `power2.out`; blur 5-6u -> 0, opacity 0 -> 1; colophon characters 0.06-0.12 apart |
| title in / out | 0.8 in, 0.6 out (m) | `none`; out = evaporates (opacity, blur 0 -> 6u) |
| seal pressed on | 0.25 (m .12, floor .25) | `power2.out`; scale 1.25 -> 1, opacity 0 -> .95 |
| flying-white stroke | 0.25 (m: ~0.17 s, contract floor) | `power2.out`, after a hold |
| ink bloom reveal (a range from a drop) | 2-4.3 (m: R 0 -> 3000 over 4.3 s; 1100 over 2.4 s) | `power2.out` on the mask radius |
| ink bloom between states | 0.8 (m) | `sine.inOut` on the radius, 20 -> 1.2 x frame diagonal |
| drop falls | 0.4 (m) | `power1.in`, lands, then blots spread 0.6 s `power2.out` |
| ripple ring | 2.4 fade (m) | radius ~ age^0.6: `ease: x => Math.pow(x, .6)` |
| split a mass after the cut | 2.3 (m) | halves move only after the stroke ends |
| handscroll pan | 5 per ~1560u (m) | `power2.inOut` |
| figures, animals | step at 12 fps (m) | position and camera stay smooth; `ease: "steps(N)"` on pose props, N = 12 x duration |

Signature rhythm (i, from the reference beats): blank paper -> first mark at 0.3-0.5 s -> forms bloom and are painted over 2-3 s -> title written 2.5-4.5 s -> seal last -> 0.3 s settle. No element moves like a vector graphic: no slide-ins, pops or bounces; forms get painted, spread as ink, or melt into mist. The frame is never frozen for 2 s: keep a push, a pan, a ripple or a drying inscription going.

## Materials and light

- Paper (m: source numbers): base `#F2ECDE`, cloud mottling +-7 %, three directions of long fibres (+1.8 % light), pulp flecks, fine grain +-3.5 %, vignette ~0.22 (corners ~11 % darker). `.iw-stage` / `.iw-paper` render it as procedural noise; `.iw-vignette` adds the vignette. Measured on the stills (m): texture 1.0-2.9, vignette ~10 L, chroma 8.6-9.9, glow 0, paper share (L > 85) 0.56-0.93. The demo measures texture 1.2-1.9, chroma 9.8, vignette 5-10.
- Ink is translucent pigment over paper, never an opaque cover; each tone = colour + opacity (Palette).
- Wet layer (m): bleed ~5u noisy blur, pigment piles up at the rim (edge = small blur minus large blur), granulation. Filter `#iw-wet`: displacement 10u at 0.013 frequency, blur 1.6u and 7u combined as 1.6a - 0.6b, granulation noise 0.35.
- Dry layer (m): fibre jitter ~1.6u (filter displacement 2.6u) and paper tooth (small holes where the bristles skip). Filter `#iw-dry`.
- Stroke body opacity = tone x (1 - dry)^1.6, fading along the stroke by dry x 0.55 (m); dry 0-.2 = wet, about .3 = textured, .6 and up = flying white (m).
- Masses: soft polygons with a vertical gradient, full tone at the top, 55 % at 0.45-0.55 of the depth, nothing at the bottom (they dissolve into mist bands); ends fade sideways over 18-25 % of the width (m).
- Far ranges qing/dan .12-.16, untextured; near ranges .4-.5 with texture strokes and moss dots (m).
- Bloom front: ragged circle (noise displacement 190u at 0.004) softened 10u; an optional tide line darkens the paper by up to .38 at the front (m).
- No light sources: no glow, highlights, cast shadows or lens effects (m: glow 0-1.9).

## Technique library

Capabilities, not obligations; the content decides. The ink filters and the brush kit are in the SCENE KIT comment at the end of `ink-wash.css`: copy the DEFS block as the first child of the ink svg and the KIT block to the top of the scene script. Scene skeleton:

```html
<div id="stage" class="iw-stage"><div id="camera">
  <svg class="iw-ink" id="ink" viewBox="0 0 1920 1080"> <!-- DEFS block here -->
    <g id="far" mask="url(#bloomA)"><g class="iw-wet" filter="url(#iw-wet)" id="farw"></g></g>
    <g class="iw-wet" filter="url(#iw-wet)" id="wetL"></g><g class="iw-dry" filter="url(#iw-dry)" id="dryL"></g>
  </svg>
  <div class="iw-col" id="title"><span class="iw-ch">一</span><span class="iw-ch">叶</span></div>
  <div class="iw-seal" id="seal"><span>春</span></div>
</div><div class="iw-vignette"></div></div>
<script> /* KIT block */ IW.init(document.getElementById("ink"), "#wetL", "#dryL"); </script>
```

- **Brushstroke (the core).** `IW.stroke(pts, o)` builds one stroke from a centreline `[[x, y, pressure], ...]` (pressure 0.2-1.2): body polygon in the wet layer, 4-40 bristle tracks in the dry layer, both revealed along the path. Options: `w` width, `tone` (bristles) and `body` (body tone), `dry`, `prof` (`brush` pressed entry and tapering exit, `even`, `tip` both ends pointed, `lens` leaf or petal, `nail` heavy entry sharp exit), `side` (+1/-1), `nb`, `seed`, `wetG`/`dryG` (target layers). It returns the reveal path; `IW.paint(tl, path, t0, dur, ease)` paints it.
  ```js
  const leaf = IW.stroke([[520,735,.5],[700,640,1],[900,520,1],[1090,420,.3]], {w:150, tone:"nong", body:"dan", dry:.28, prof:"lens", side:1, nb:70, seed:"leaf"});
  IW.paint(tl, leaf, 0.3, .6);
  ```
- **Loaded stroke.** `side: 1` loads one side of the brush darker, so the stroke draws its own edge: use it for bodies, rocks, leaves, waves. A pale `body` (dan, qing) with dark `tone` (nong) is the classic loaded brush (m).
- **Flying white.** One fast dry stroke: `dry: .6-.7`, `tone: "jiao"`, `prof: "even"`, 0.25 s, strongest right after a hold. Breaks come from long-scale noise shared by neighbouring bristles, so they read as streaks, not dashes (m).
- **Mass, mountains.** `IW.ridge(x0, x1, baseY, [[x, height, halfWidth], ...], seed)` gives sharp peaks that fall off exponentially, roughened by three noise octaves (m); `IW.mass([...ridge, [x1, bottom], [x0, bottom]], {tone, top, mist, fadeX, parent})` fills it, dissolving downward and fading at both ends. Near ranges: a loaded rim stroke along the ridge (`w` 10-14, `dry` .35-.4, `side: 1`), 8-14 short texture strokes down the slope (`w` 5, `zhong`, `dry` .55, `tip`), moss dots `IW.blot(x, y, 3-7, {tone:"jiao"})` on the ridge only.
- **Rock, foreground.** One wide loaded stroke (`w` 60-120, `prof:"even"`, `body:"zhong"`, `dry` .2-.3, `nb` 50-60) + one smaller dark stroke + 4-6 moss dots. Avoid many parallel pale strokes (they read as organ pipes) (m).
- **Water is blank paper.** Two or three faint dry streaks (`dan`, `dry` .7, `tip`) and open rings `IW.ring(x, y, r, {tone:"zhong", w:2.6, rot})` that grow and fade (Motion). Heavy water: rows of wave lines packed tighter towards the shore, or one pale wash crossed by a few very large loaded strokes (m).
- **Ink bloom reveal.** `const c = IW.bloom("bloomA", cx, cy)` makes a mask; put the group to reveal under `mask="url(#bloomA)"` and tween `attr: {r}` from 0 to past the far corner, `power2.out`. Use it for "a drop of ink becomes a landscape" or one season giving way to the next.
- **Calligraphy title.** `.iw-col` vertical column, one `.iw-ch` span per character, `filter: url(#iw-edge)` on the column for a ragged ink edge. Write each character:
  ```js
  gsap.utils.toArray("#title .iw-ch").forEach((ch, k) => tl.fromTo(ch,
    {opacity: 0, filter: "blur(7px)", clipPath: "inset(0% 0% 100% 0%)"},
    {opacity: 1, filter: "blur(0px)", clipPath: "inset(0% 0% 0% 0%)", duration: .4, ease: "power2.inOut"}, t0 + k * .45));
  ```
  Colophon `.iw-col.small` and labels: the same without the clip, 0.35 s `power2.out`.
- **One great character.** `.iw-hero-ch` (brush 700, 240-420u) written the same way, slower (0.6-0.8 s per character); it may be the event of the film (s).
- **Seal.** `.iw-seal` (one character, song 900, paper knocked out of vermilion, irregular edge, ink-paste speckle) or `.iw-seal.q` with four characters; `.kai` for a softer face; size with `--s`. Press it on last (Motion). Never more than one seal per frame.
- **Horizontal inscription.** `.iw-ins` with a `.iw-mark` square before the line (0.32 x size, vermilion) (m); `.iw-ins.latin` for English, italic.
- **Handscroll pan.** A `#world` div wider than the frame holding a `.iw-paper` sized to it, the ink svg (viewBox = world size) and the text; tween `#world` x from `-(worldW - 1920) * u` to 0 (`power2.inOut`). Paint strokes as they come into view.
- **Splitting a mass.** Copy the mass into two groups, each clipped to one side of the cut line (`clipPath` polygons); after the cutting stroke lands, one half rises and rotates 3-6 deg while fading, the other slumps (y +20u, scaleY .9), over 2.3 s `power2.out`; splatter blots travel along the cut and fall (m)/(i).
- **Mist.** A paper-coloured band (`.iw-paper` strip with a vertical gradient mask) crossing a range at 0.3-0.6 opacity; drifting 20-40u over the shot.
- **Figures (xieyi).** A single pale body wash (`dan`) + two loaded strokes (`nong`, `side`) + limbs in burnt-ink lines; a face, if shown at all, gets 3-5 strokes; pose changes on 12 fps steps (m).
- **Endings (i).** close on one small mark as the surrounding ink dries; evaporation (strokes withdraw last-first: tween dashoffset 0 -> 1, `immediateRender:false`); the mounted scroll seen whole on the dark table (`.iw-table`, `.iw-mount`).

Cautions: give `svgOrigin` / `transformOrigin` in both the from and the to vars (otherwise GSAP leaves an offset). Filters cost render time (about 2-4 frames/s at 1080p): keep one wet and one dry layer per scene rather than a filter per stroke.

## Transitions

Between scene files: `cut` (default). Avoid `fade`: a plain crossfade is the generic dissolve this style never uses. Style transitions are built in-scene:
- **Mist out / paint in (default change of scene):** last 0.6 s of the outgoing scene: a full-frame `.iw-paper` layer opacity 0 -> 1, `sine.inOut`; `cut`; the incoming scene starts on blank paper and paints or blooms in from 0.3 s.
- **Ink bloom:** the incoming scene's first 0.8 s: its picture appears inside a widening ink bloom mask (`IW.bloom`, `sine.inOut`) centred where the eye was at the end of the previous scene (match the point across the cut).
- **Scroll pan:** both states on one handscroll world in one scene file.
- **Hard cut into a tight detail:** the only hard cut that is a statement (Camera grammar).
- **Evaporation:** the old picture is taken back stroke by stroke, last stroke first over its last 0.8-1.2 s.

## Captions

Captions are inscriptions: ink-coloured kai text, no box, small vermilion mark at the start of the line. Keep the caption band (bottom 16 %) free of other text; dark foreground ink may pass below it only when captions are off.

```css
--cap-bg: transparent; --cap-fg: #12100D; --cap-font: var(--iw-kai); --cap-size: calc(46 * var(--u));
--cap-weight: 400; --cap-radius: 0; --cap-bottom: calc(70 * var(--u));   /* portrait: 210u, 48u */
--cap-border: none; --cap-shadow: none; --cap-accent: #B02E22; --cap-mark: dot;
```

## Image-gen guidance

- Anchors: "Chinese xieyi ink wash painting on warm rice paper #F2ECDE", "black ink only in five tones, translucent washes, wet bleed with darker rims, dry brush with flying white", "70 % empty paper, asymmetric composition", "far mountains pale and soft dissolving into mist, near forms dark and textured", "monochrome, no colour", "no text, no seal, no signature, no watermark".
- Subjects: landscapes, mist, plants and branches, birds, animals, boats, architecture silhouettes, small figures seen from afar, textures of rock.
- Never generate: text or calligraphy, seals, any red, frames or mounts, UI, charts, people's faces in close-up detail.
- Post-treatment: desaturate fully, multiply onto the paper (or key out the paper), lift blacks to the jiao tone, add the wet edge filter, place in the empty-paper composition; reveal it with an ink bloom, never a slide.

## Taboos

- The reference never uses a hue besides ink and the single vermilion: no colour washes, no red type, no red wash, no second seal colour.
- No outlines with flat fills, no hard-edged vector shapes, no icons, cards, boxes, charts or UI.
- Nothing slides, pops, bounces or scales in like a vector graphic; no elastic or back eases.
- No generic crossfade or wipe between scenes; no fast montage (the only hard cut is to an extreme close-up).
- The reference never uses glow, flares, light bands, cast shadows or photographic depth of field.
- No sans-serif type, no horizontal headline blocks across the middle of the frame, no centred symmetrical composition.

## Known gaps

- Calligraphy is a system brush font revealed per character (top-down wipe plus wet blur), not traced in stroke order with hidden-tip entry and tapered exits; a single character written stroke by stroke must be hand-authored as `IW.stroke` centrelines.
- The brush kit is simplified: at most ~40-70 bristles, no width boil on twos, no per-pixel cached mountain texture (texture strokes stand in), the wet bleed is one filter per layer.
- Captions are the runtime's bottom-centre box with a round mark; the reference places each inscription in the shot's empty paper with a small square mark and an italic serif (use `.iw-ins` for on-screen lines instead).
- No figure rig: xieyi figures are built per film from masses and loaded strokes.
- Splitting a mass, the drop-to-landscape bloom and the tide line are approximations; the bloom transition between scene files is split across a `cut`.
- The paper is fixed to the frame unless a world paper is added; zooming the camera re-rasterises it (slower renders).
- Portrait layout, camera numbers marked (i) and the sound palette (plucked zither, literal silences) are not measured here.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
