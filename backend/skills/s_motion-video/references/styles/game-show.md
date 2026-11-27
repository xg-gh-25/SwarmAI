# Style: game-show (Game Show Flat)

Units: `u` = frame short side / 1080. At 1920x1080, 1u = 1px. HTML sizes are `calc(N * var(--u))` (see `game-show.css`); in GSAP `const u = MV.u`. Most of this style is drawn in one stage SVG with `viewBox="0 0 1920 1080"` (portrait `0 0 1080 1920`), where one user unit is one u, so SVG numbers below are plain u. Provenance: `(m)` read from the reference's own numbers, `(s)` seen in reference stills, `(i)` inferred.

## Identity

A rhythm-game variety show drawn as flat vector toys. Identity rules, all mandatory: (1) **everything lands on one fast tempo grid** (150 BPM: beat 0.4 s, bar 1.6 s); every drop, pop, stamp, punch and glyph sits on a beat or a subdivision, and a round's verb repeats on the beat and speeds up (quarters, then eighths) as the plot; (2) **one drawing kit**: flat candy fills, one near-black ink outline about 8u with round joins, depth only as a hard offset ink shadow; no gradient, blur, glow or texture anywhere; (3) **one set = one saturated hue**: ground, a same-hue pattern one step lighter (diagonal stripes, offset polka dots or a sunburst) and a darker floor band topped by an ink line; neighbouring scenes never share a hue; (4) **toy contestants**: one bean-shaped body construction for the whole cast, told apart by body colour, belly patch, one head mark and a name-tag pill; they hop on every beat, squash on hits, throw both arms up on a shout; machines are the same kit with a boxier silhouette; (5) **words are telops**: heavy rounded type, white or yellow fill over a thick ink outline, short; each round ends on one judgement word that pops at an angle on the last hit with a star burst and a frame punch.

Best for: year-in-review, rankings and top-N lists, step lists, quizzes, team or product line-ups, playful launches, "how many / how fast" promos, social shorts. Not for: sombre or high-trust topics (incidents, finance results for a board), dense data that must be read exactly, photo or UI-led stories, slow contemplative pieces.

## Palette

| Hex | Role |
|---|---|
| `#1B1B1B` | ink: every outline, hard shadow, name-tag pill, ink text (m) |
| `#FFFFFF` | telop fill, mitten hands, catch-lights, burst ring (m) |
| `#FFF6E5` | cream: round cards, plates, report cards, paper props (m) |
| `#FFD23F` `#FF8C42` `#FF5A5F` `#FF8FB1` | candy yellow, orange, red, pink (m) |
| `#5BD68A` `#C6F16D` `#8ED1FC` `#4D7CFE` | candy green, lime, sky, blue (m) |
| `#6B4FBB`/`#46318F`, `#3FB8AF`/`#2B8F88`, `#26264A` | deep sets with darker partners: purple, teal, navy (finale, night) (m) |
| `#C98A52`/`#8E5530`, `#C3CAD9`, `#FF9FB8`, `#E8394A` | wood, steel grey, blush, open mouth (m, blush/mouth i) |

Set triples (ground / pattern / floor), presets `.gs-set-*` in the css: red `#FF5A5F / #FF7276 / #D14D51` (s, sampled), tangerine `#FFB870 / #FFA85A / #E08A4A` (m), purple `#6B4FBB / #8169C5 / #46318F` (m/s), plus yellow, green, sky, blue, pink, teal, navy (i). Rule for a new hue c: dots pattern = mix(c, white, 0.15); stripe pattern = c moved 4-8 L either way; floor = mix(c, ink, 0.20) (m from the red set).

Colour logic: a contestant's body colour is its identity for the whole film and never equals the set hue it stands on (s). Round card and set share the round's hue; the corner sticker takes a contrasting candy hue (s: red sticker on a purple set). Judgement words are yellow or pink (m); big titles white with one yellow word allowed. There is no single brand accent: the one-accent rule becomes **one judgement word per screen** (two judgement words on one screen means one is wrong). A brand colour can replace one candy hue as a set or a hero contestant, never the ink.

## Type

- Display (Latin, numbers, judgement words, round numbers, name tags): `"Arial Rounded MT Bold", "SF Pro Rounded", "MV Nunito", "Hiragino Sans GB", "PingFang SC", "MV Noto Sans SC", sans-serif`, weight 700 (`--gs-round`). Headless Chromium resolves Arial Rounded MT Bold; SF Pro Rounded and `ui-rounded` do not resolve there (m, probed).
- CJK and plain heavy text (round-card names, plates, stickers' second line): `"Hiragino Sans GB", "PingFang SC", "MV Noto Sans SC", "Microsoft YaHei", "Noto Sans CJK SC", "Helvetica Neue", sans-serif`, weight 900 (renders W6) (`--gs-black`).
- Hand-lettered props only (a note, a chalkboard): `--gs-hand`.

| Role | Size (u) | Outline stroke (under the fill) |
|---|---|---|
| film title, glyph pop | 170-200 (m 200) | 0.13-0.2 x size (m 26 at 200) |
| judgement word | 120-150 (m 120) | 18-26 (m 18 at 120) |
| round-card name | fit to 720u wide, max 150 (m) | none, ink 900 on cream |
| scoreboard number | 130-150 | none on cream; 0.14 x size on a set |
| sticker year / name | 64 / 54 (m) | 12 |
| sticker subtitle, plate text | 34-46 | none, ink |
| name tag | 34 (m) | none, white on ink pill |

Telop outline = an ink stroke of about 0.14 x font size painted **under** the fill with round joins (`.gs-t`, `paint-order: stroke`) (m), so the visible outline is about 7 % of the size. Outlined text is always stage-SVG text: HTML text strokes draw mitre spikes on A, V and W. Tracking 0. Lengths: a sticker line <= 20 CJK / about 32 Latin characters, a plate <= 12 CJK / about 20 Latin (m); Latin runs about 1.6x wider than CJK (m). Every word holds >= max(1.8 s, speech + 0.6 s); a judgement word may be shorter (m).

## Layout grid

- Frontal, symmetrical proscenium (m). Floor top line at y 790 (`--gs-floor-y`): characters stand on it, name tags sit right below it (806-856) so they stay above the caption band (i; the reference floor is at y 860-880 (s) with tags in the caption zone, which this port cannot use).
- Sticker banner top-left at (128, 84) (reference (56, 44) (m), moved inside the 5 % safe area plus a 3 % push). Judgement words centre-top, centre at y 250-380, never in the bottom 40 %. Scoreboards and counter plates top-right or mid-right.
- Contestants: hero 160 x 180u at scale 1 (m), at 25-40 % of frame height with its mark; crowd members at scale 0.6-0.75, spaced >= 200u. Props stand on the floor line too.
- Per screen at most: 1 sticker + 1 title or judgement word + 1 plate/scoreboard + 1 hero + 6 crowd. Keep the middle band (y 380-560) clear for bursts and the judgement drift.
- Caption band (bottom 16 %) holds only the floor colour, feet and the audience row; no text.
- Portrait 9:16 (i): viewBox 1080 x 1920, floor line y 1380, title 150u at y 360, judgement centre y 560, sticker at (60, 110) full width minus margins, hero centred, crowd in one row of up to 4 at scale 0.6, scoreboard above the hero.

## Camera grammar

The stage never travels: the frame itself moves (m). Vocabulary, not a route.

| Shot | Expresses | How |
|---|---|---|
| locked frontal wide | the whole stage as a game board | every scene; plus a shot-long push 1.00 -> 1.03 on `#camera` (i, the contract's still-frame rule) |
| beat punch | the hit felt in the frame | `#punch` scale +0.8 % (ticks) / +2 % (hits) / +3-5 % (slams) around centre, attack 12 %, decay over 0.3 s (m 0.16 s decay, stretched to the 0.25 s minimum) |
| several punches in a bar | the frame breathing with the music | one per beat, amounts summed < 0.06 so the edge never shows (m) |
| hard cut on a bar line + white flash | a new round, a new set | scene `cut`; incoming scene opens with a white layer 0.5 -> 0 in 0.25 s, `expo.out` (m 0.08 s at 0.5) |
| split stage | two sides answering | two sets side by side, each its own hue, ink divider 8u |
| shrink to thumbnail | memory, accumulation | a finished bit scales to x0.27 into a slot along the bottom (m) |
| vertical scroll of the set | rising stakes, a leaderboard | `#camera` y over one or two bars, `power2.inOut` |
| snap zoom on a prop | this detail is the joke | inside one scene, a 2x framing set on a bar line, back on the next bar line (cut-like, no tween) |

No dutch angles, no orbit, no perspective, no shake beyond punches.

## Motion

Grid: 150 BPM (m): beat 0.4 s, eighth 0.2 s, bar 1.6 s; `at(bar, beat) = (bar * 4 + beat) * 0.4`. A 6.4 s scene = 4 bars. Faster subdivisions are allowed only as stagger offsets between tweens that each last >= 0.25 s.

Eases: pop = `back.out(1.9)` (m, every card, sticker, stamp, word, glyph); exit = `power2.in` (m cubic in); drop = `power2.in` (m cubic in, lands on the beat); drift = `power2.out`; hop = custom parabola `p => 4p(1-p)`; squash = damped wobble `p => exp(-4.9p) cos(10.5p)` (m: 14/s decay, 30 rad/s, 0.35 s); punch = attack-decay shape. Nothing fades in slowly: things are on or popping (m).

| Action | Duration | Details |
|---|---|---|
| idle hop | 1 beat, repeated | height 8-30u (m); hero 16, crowd 14, props 10; neighbours offset half a beat so a crowd ripples (m) |
| drop entrance | 0.28 s ending ON the beat | fall 500-1100u, hidden until it starts (m 0.28 s, 500-900u) |
| landing / hit squash | 0.35 s | amount 0.16-0.24; scaleX = 1 + 0.6 x amount |
| shout | arms 0.25 up + 0.25 down; mouth open 0.3 s | arms 20 -> 150 deg (m 18 -> 158); about half a beat (m) |
| pop (stamp, word, plate) | 0.25-0.35 s | scale 0 -> 1 (stamp 1.8 -> 1), rotation lands at -6 deg (word) / -8 (stamp) (m) |
| glyph-by-glyph title | 0.38 s each, 0.06 s apart | rise 60u, `back.out(1.9)` (m 0.05-0.06 apart, 0.38, 60) |
| sticker in / out | 0.35 in / 0.25 out | in from x -1000 with rotation -6 -> -1.5 (m); out lifts 260u up, `power2.in` |
| round card in / out | 0.25 in / 0.25 out | from x +1640 with rotation 8 -> -2, `back.out(1.9)`; card pulses 1.03 on each beat (m) |
| judgement word | pop 0.3, drift 20u up over 1.2 s | with a burst at the same point and a 3-5 % punch (m) |
| burst | 0.3 s | scale 0.55 -> 1.25, rotation 0 -> 30, opacity on then 1 - p^2 (m) |
| counter step | 0.25 per beat | number steps on the beat, plate bounces 1.2 -> 1 |
| set pattern | whole scene, linear | stripes scroll 40-60u/s on sets, 300u/s on round cards (m); sunburst turns about 6 deg per bar (i) |

Round shape (m): a 1-bar round card, then play: set up the pattern in the first half, answer or escalate in the second, and land the slam + judgement on the last strong beat. Call-and-response: the call on one beat, the answer on the next; the second half repeats it twice as often. Crowds drop one per beat or half-beat (m). The final 0.3 s carries only hops and the push.

## Materials and light

- Measured on the reference stills (light helper): glow 0.0, texture 0.11-0.5 (flat), vignette 2.2-6.9 (comes only from the darker floor band, not a real vignette), chroma 55-64 (very saturated), highlight share 3.5-5.7 % (white telops and hands) (m).
- Flat fills only, one outline colour. Outline 8u on characters and props, 5-7u on small parts, 10u on round cards, 4u white ring on name tags (m). Round joins and caps everywhere.
- Hard offset shadow: the same shape in ink, drawn first, shifted (10, 10) for stickers, plates and tags, (14, 14) for cards and big props (m). No soft shadow.
- Patterns: stripes 70u wide at -25 deg (m `.gs-stripes`); polka dots step 90u, r 12u, rows offset half a step (s `.gs-dots`); sunburst 24 rays (`.gs-rays`). The floor band is darker; on dot sets it may drop the ink line (s).
- Characters: no gradients; belly patch a lighter tint of the body; blush flat pink; eyes ink ovals 10 x 15u with a 4u white catch-light up-right (m/s).
- Optional stage furniture (s): a scalloped valance across the top in a contrasting candy hue with an ink edge; seeded confetti (small rotated rectangles in candy hues, `MV.rand`); a foreground audience row of dark same-hue circles r 55-60u along the bottom edge.

## Technique library

Capabilities, not obligations. Kit to paste into a scene (after `const tl = gsap.timeline({paused:true})`, with `<svg class="gs-svg" id="svg" viewBox="0 0 1920 1080">` inside `#punch` inside `#camera`):

```js
const B = .4, at = (bar, beat = 0) => (bar * 4 + beat) * B, INK = "#1B1B1B", svg = document.getElementById("svg"), NS = svg.namespaceURI;
const OL = { stroke: INK, "stroke-width": 8, "stroke-linejoin": "round", "stroke-linecap": "round" };
function el(t, a, p) { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); (p || svg).appendChild(e); return e; }
// the outer <g> holds the place; GSAP only moves the returned inner <g>
function place(x, y, p, s = 1) { return el("g", {}, el("g", { transform: `translate(${x} ${y}) scale(${s})` }, p)); }
function telop(p, str, x, y, size, cls = "", sw = size * .14) { const t = el("text", { x, y, class: "gs-t " + cls, "font-size": size, "stroke-width": sw }, p); t.textContent = str; return t; }
function glyphs(p, str, x, y, size, cls = "", sw = size * .14) {          // one <g> per glyph, measured once
  const m = telop(p, str, 0, 0, size, cls, sw), W = m.getComputedTextLength(), out = [];
  for (let i = 0; i < str.length; i++) { const a = i ? m.getSubStringLength(0, i) : 0, w = m.getSubStringLength(i, 1);
    const g = place(x - W / 2 + a + w / 2, y, p); telop(g, str[i], 0, 0, size, cls, sw); out.push(g); }
  m.remove(); return out;
}
const hopEase = p => 4 * p * (1 - p), pulse = p => (p <= 0 || p >= 1) ? 0 : 1 - p * p;
const wob = p => Math.exp(-4.9 * p) * Math.cos(10.5 * p), punchEase = p => p < .12 ? p / .12 : Math.pow(1 - (p - .12) / .88, 2);
const on = (g, t) => tl.fromTo(g, { opacity: 0 }, { opacity: 1, duration: .25, ease: p => p > 0 ? 1 : 0 }, t);   // hidden before t
const punch = (t, a) => tl.fromTo("#punch", { scale: 1 }, { scale: 1 + a, duration: .3, ease: punchEase, immediateRender: false }, t);
const squash = (s, t, a = .2) => tl.fromTo(s, { scaleY: 1, scaleX: 1, svgOrigin: "0 0" }, { scaleY: 1 - a, scaleX: 1 + a * .6, duration: .35, ease: wob, immediateRender: false }, t);
const drop = (g, t, h = 700) => { on(g, t - .28); tl.fromTo(g, { y: -h }, { y: 0, duration: .28, ease: "power2.in" }, t - .28); };
const pop = (g, t, r = -6) => tl.fromTo(g, { scale: 0, rotation: r + 8, svgOrigin: "0 0" }, { scale: 1, rotation: r, duration: .3, ease: "back.out(1.9)" }, t);
function hops(h, t0, t1, amp) { const n = Math.floor((t1 - t0) / B); if (n > 0) tl.fromTo(h, { y: 0 }, { y: -amp, duration: B, ease: hopEase, repeat: n - 1 }, t0); }
```

- **Grid discipline.** Write every time as `at(bar, beat)`; half-beats as `at(b, n) + B / 2`. Clamp the last moves to `MV.end()`.
- **Visibility windows.** A tween whose from-state is visible (a stamp at 1.8x, a falling body) must be hidden before its time: `on(g, t)`; things that appear and vanish inside one tween use the `pulse` ease (back to 0 at both ends). Mouth swaps are `tl.set` at the beat.
- **Contestant (shape rules, draw your own).** Origin = feet centre. Body: a gumdrop / bean 160 wide x 180 tall, domed top, slightly flared flat base (ratio 0.85-0.95); two foot ovals 56 x 28 peeking under it; capsule arms 26 x 62 hinged at the shoulders (+-70, -96), hanging at +-20 deg, white mitten circles r 16; belly patch ellipse 84 x 60 at 72 % of the height, a light tint of the body; ink oval eyes 20 x 30 at (+-26, -112) with a white catch-light; flat blush ovals under the eyes; a small smile arc that swaps to an open D-shaped mouth on shouts. Exactly one head mark from simple geometry (antenna ball, leaf, star, bow, flat cap, headset). Groups: holder (`place`) > hop > squash > parts, arms in their own groups. Machines: rounded box body about 226 x 196, a dark screen face with two eyes, wheels or feet, an antenna ball; same outline, shadow and hop rules.
- **Shout.** Both arms to +-150 deg with `back.out(1.9)` in 0.25 s and back with `power2.in`; mouth open 0.3 s; optionally a speech-burst telop ("Hey!") 64u above the head.
- **Burst.** At a hit point: white ring r 80 stroke 8, an 8-point star (r 70 / 28) in yellow or pink with a 6u outline, 8 ink speed lines from r 90 to 130 (7u); one group, `pulse` opacity plus scale 0.55 -> 1.25 and rotation 0 -> 30 over 0.3 s (m).
- **Judgement word.** `telop(place(960, 300), "Nice!", 0, 50, 140, "yellow", 24)`, `pop(g, t)` at -6 deg, then a 1.2 s drift 20u up; burst + 5 % punch on the same beat; the contestants shout and props squash.
- **Corner sticker.** In SVG at (128, 84): ink rect shifted (10, 10), then a rect in a candy hue contrasting with the set (rx 24, 7u outline), height 158; line 1 = white telop 64u (year / step number) + 54u name, line 2 = ink 34u subtitle at baseline 128; width = longest line + 68. Slides in from x -1000 on the first downbeat.
- **Round card (1 bar, its own scene or the first bar of one).** Full-bleed set hue with stripes scrolling 300u/s; cream `.gs-card`-style panel 1240 x 460u (rx 50, 10u outline, 14u shadow) centred; a pill tag "ROUND 3" in the set hue on its top edge; the name in ink 900 fit to 720u; a tag line (year, step) 56u in the set hue with a 5u ink stroke; an icon hopping on the left.
- **Stamp.** A rounded red or green plate with a white word ("OFF", "DONE"), `on` + scale 1.8 -> 1 at -8 deg in 0.25 s, the prop squashes, a small burst.
- **Stats as game UI.** Scoreboard plates (`.gs-plate`) whose number steps once per beat, bar charts whose bars grow one per beat (`scaleY`, `transformOrigin: 50% 100%`, `back.out(1.9)`), podiums of three heights, price tags hit and replaced.
- **Crowd drop.** Up to 8 contestants, one per beat or half-beat, alternating left/right, each landing with a squash; then all hop with the off-beat ripple.
- **Remix board.** One set with a ribbon of months or items along the top; each item gets 1-2 bars centre stage then shrinks to x0.27 into a slot along the bottom; at the end all slots hop together (m).
- **Report card.** A cream card typed line by line (one character per eighth via a counter that writes `textContent.slice(0, n)`), then a grade stamp pops; good as a closing scene.
- **Name tag.** Ink pill height 50, rx 25, 4u white ring, white 34u rounded name, under the feet; it moves with the drop, not the hop.

## Transitions

- Between scene files: `cut` only, on a bar line. The incoming scene's first frames carry the white flash (0.5 -> 0, 0.25 s); that is this style's whole transition (m: hard cuts + 0.08 s flash; a fade is saved for the very end of the film).
- `fade` (0.4 s) only into the final card of a film, if at all.
- In-scene: the sticker lifts out and the judgement word replaces the title; a round card slides off left in 0.25 s (`power2.in`) on the bar line where play starts; thumbnails shrink into slots.

## Captions

The reference has no captions; its telops are the subtitles (m). When narration needs captions, they are a telop plate: cream box, 6u ink border, hard 8u ink shadow, ink 900 text 46u, yellow dot mark, bottom centre 48u up (i). Keep the caption band free of other text; plates and stickers carry the key words above it.

```css
--cap-bg: #FFF6E5; --cap-fg: #1B1B1B; --cap-font: var(--gs-black); --cap-size: calc(46 * var(--u)); --cap-weight: 900;
--cap-radius: calc(26 * var(--u)); --cap-bottom: calc(48 * var(--u));   /* portrait: 220u, size 44u */
--cap-border: calc(6 * var(--u)) solid #1B1B1B; --cap-shadow: calc(8 * var(--u)) calc(8 * var(--u)) 0 #1B1B1B;
--cap-accent: #FFD23F; --cap-mark: dot;
```

## Image-gen guidance

- Mostly not needed: everything is built from the kit. If a prop is too complex to draw (a specific dish, a vehicle), generate it.
- Anchors: "flat vector sticker illustration, thick uniform near-black outline, round line joins, flat saturated candy colours, no gradients, no shading, no texture, simple toy-like shapes, hard offset black shadow", "isolated on plain white background", end with "no text, no watermark".
- Never generate: contestants (draw them from the shape rules), telops, numbers, scoreboards, sets and patterns, judgement words, bursts.
- Post-treatment: remove the background, quantise to the palette, add an 8u ink outline and a (10, 10) hard shadow, stand it on the floor line and give it the beat hop.

## Taboos

- The reference never uses blur, glow, grain, painted surfaces or smooth colour ramps on shapes; depth is never soft.
- The reference never uses a camera that travels, rotates or tilts; no slow dissolves between rounds.
- The reference never lets anything move off the grid or fade in slowly.
- The reference never gives two characters the same body colour, nor a character the colour of its set.
- No real logos, product UI or real people as more than generic stand-ins; names only on tags.
- No thin generic subtitle bar; no outlined text drawn with HTML text strokes.

## Known gaps

- Sound is the other half of this style (synthesized score generated from the picture's own events, toy foley, short shouts placed 20-30 ms before the beat, a jingle per round). The skill's audio tools give one music bed and at most 2 SFX per scene, so the picture-to-sound sync is not reproduced; keep `bgm` upbeat and cue SFX (`pop`, `ding`, `thud`) on judgement beats.
- Punch decay (0.16 s) and cut flash (0.08 s) are stretched to the 0.25 s minimum move; they read softer than the reference.
- The reference's rounded display face is not a system font; Arial Rounded MT Bold is close for Latin, but CJK telops use Hiragino Sans GB W6, which is less heavy and not rounded.
- Floor line raised from y 860-880 to y 790 to keep name tags out of the caption band; stages feel slightly lower.
- Hits are deliberate one-frame accents (squash at full amount, stamp on, punch attack); `defects.py` lists them as one-frame jumps on the beats. Expected for this style; anything off the beat is a real defect.
- Portrait layout is inferred; the reference is landscape only.
- Sunburst speed, set triples other than red, tangerine and purple, caption plate and the image-gen recipe are inferred.

---
Design adapted from an MIT-licensed style set; see NOTICE.md.
