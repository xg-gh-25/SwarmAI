# Production modes

The production mode (`storyboard.mode`) answers "how is this film made": where the pictures
come from (user material, user images and footage, code-drawn animation, optional local AI
images), how it is cut, how layers are combined, how scenes join, and its pacing. It is
independent of the writing method and the style.

On conflict: structure, scene order and narration follow the method; look follows the style;
asset source, editing, compositing and pacing follow the mode. The user's explicit wishes
and the contract's fixed numbers override the mode.

Default: `explainer`. Use another mode only when the material calls for it.

| id | Name | Typical method | One-line idea |
|---|---|---|---|
| `explainer` | Code-animation explainer (science and technology) | `principle`, `science`, `tutorial`, `data` | everything that explains is drawn in code; sources before imagination |
| `product-intro` | Product introduction | `product`, `launch`, `feature` | the user's product images are the hero; nothing invented |
| `footage-edit` | Footage edit | any | only the user's videos and images, cut into a highlight or recap |
| `feed-ad` | Feed ad | `product`, `promo` | one selling point, first 3 s, repeated, clear CTA |
| `marketing` | Marketing showcase | `product`, `promo`, `brand` | one product shown clean, in scenes, in detail; product never changes |
| `game-promo` | Game promo | `launch`, `product` | strongest visual in 3 s; the core loop in 10 s |
| `game-drama` | Game ad short drama | `game-drama` | one hero, photoreal plates, a sticker for every named word |
| `warmup` | Event warm-up loop | `brand` | mood not content; slow; loops; music-led |
| `meeting-share` | Meeting share | `meeting-share` | source structure on screen, source timing, source facts |

---

## Rules shared by all modes

### Transitions: `cut` and `fade` only between scenes

The contract allows two scene transitions: `cut`, and `fade` (a 0.4 s crossfade into the
next scene; total length unchanged). Any other joining effect is built **inside** the
scenes, as motion of the incoming (or outgoing) scene:

| Effect a mode asks for | How to build it |
|---|---|
| push along the flow ("slide") | `cut`; the incoming scene's content enters from the side the flow comes from |
| wipe (contrast, "but") | `cut`; the incoming scene opens with a wipe reveal of its layer |
| zoom into a detail | `cut`; the outgoing scene ends pushing in, the incoming scene starts at that scale with the part in the same screen position |
| flash / dip to white (impact) | `cut`; the incoming scene starts with a short white flash that fades over at least 0.1 s (never a single-frame flash; it is detected as a defect) |
| dip to dark (chapter) | `cut` or `fade`; the incoming scene rises from near-black (e.g. #0B0D11), never pure black frames |
| match cut (cause, "then") | `cut`; the last element of one scene becomes the first element of the next, same place |

- The first 0.4 s of a scene holds no key text (it may be under a fade).
- Transitions serve logic, never decoration. No page turns, spins or glitches unless the
  style owns them.
- At most two kinds of joining effect per film unless the mode says otherwise.

### Optional AI-generated images (local assets only)

This skill does not generate images or video. If the user supplies AI-generated images, or
asks for them and an image generator is available outside this skill, they can enrich a
film as local files in `assets/` (no URLs). Where a mode below says "AI images: optional",
this is what it means.
- Roles: `hero` (big image for the opening or climax), `plate` (background for a chapter,
  with code-drawn shapes and text on top), `subject` (the thing being talked about: object,
  creature, place), `character` (a character or mascot), `detail` (a close-up for a
  magnifier), `texture` (paper, chalkboard, metal).
- Amount when the user wants them: a 20 s film at least 2, a 60 s film at least 3, up to 6.
  One image reused across scenes counts once.
- Prompt shape: 4-7 style-anchor words from the style (palette, stroke, era), then subject,
  composition, background, light, and end with "no text, no watermark". For cut-outs
  "isolated on plain white background". No real people's names, no brand logos.
- Consistency: make a character sheet first (front, side, two or three expressions, plain
  background) and use it as the reference for every later image of that character or
  product; give one subject's images a shared name prefix (`plane-hero`, `plane-climb`).
- Text, numbers, charts, flowcharts and UI are always drawn in code; image models cannot
  draw exact text or numbers.
- Blend images into the style (cut out, recolour, push the palette) so they look like part
  of the style, not pasted photos.
- Never redraw what the user already gave an image of.

### Fallbacks

- No voice (`narration` empty): captions and title cards carry the information; on-screen
  text per scene may grow to 20 Chinese chars (10 words); every text block uses the hold
  formula in `director.md` section 6; durations are set by hand.
- No images available: draw everything in code (gradients, geometric characters, CSS or
  canvas textures, the style's stage).
- Where a mode would want generated footage: camera moves on stills (push, pan, slight
  scale, parallax), code-drawn particles and light, or the user's footage.
- No web access: only the user's material and certain knowledge; uncertain facts are left
  out and the summary says so.

---

## `explainer` - Code-animation explainer

**What it is**: makes a technology, a principle or a system's working process clear to people
who want to understand it. Success has one test: afterwards the viewer can retell in their
own words "how it works".
- Three moves: **visualise** (abstract things become visible objects and actions),
  **take apart** (whole -> parts -> how parts work together), **compare** (with what the
  viewer knows, and with the common misunderstanding).
- **Sources first**: the user's material wins for terms, numbers and steps. What it does not
  cover may be checked on the web and cited; if you cannot verify it, do not say it. Never
  draw an "it's roughly like this" structure to make the picture look good.
- **One main line per film**: 60 s = one mechanism; 3 min = three or four parts of one
  system. What doesn't fit is named in one closing line ("what we didn't cover").

**Sources (priority order)**

| Source | When | How |
|---|---|---|
| Code animation (the body) | structures, flowcharts, data flows, sequences, state machines, comparison tables, formulas, axes, anything "boxes and lines" | each part is its own element so it can be highlighted, moved or taken apart alone |
| User images | architecture diagrams, screenshots, photos, logos | never redraw; push into the part being explained; overlay code-drawn highlight boxes and leader lines, never alter the image |
| User footage | demo recordings, lab footage | only the seconds being talked about, picture-in-picture or full screen, with labels |
| AI images: optional | organic things code draws badly (tissue, material feel, scene backgrounds, personified characters) and style textures | 1-3 for a 60 s film, as background or character only; never for structure or data (they invent details) |
| Web check | facts the material lacks but which must be exact (specs, years, standard names) | cite it; if not found, leave it out |

**Editing**
- **One concept per scene**: each scene builds one new concept; as the voice names a part,
  that part appears. Parts already explained fade but stay in place, keeping the spatial
  sense.
- **Whole -> part -> whole**: the first scene shows the whole system (all parts, still, no
  labels); each middle scene pushes into one part; the last scene pulls back to the whole,
  now with every label, and runs the data flow once.
- **Motion matches the verbs**: "forwards the request to" = a packet slides from A to B;
  "compresses" = the volume shrinks; "in parallel" = several lines light at once. The picture
  never moves two seconds before or after the voice.
- **Numbers grow**: values roll from 0 to the target, bars grow from the base, curves draw
  left to right; a large change is stressed by rescaling the axis.
- **Rhythm**: explaining scenes 5-10 s; after each part is explained, hold 0.8-1.2 s so the
  viewer can see it; the fastest scenes of the film are the one or two "result reveal"
  moments.
- **On-screen text**: when a term first appears, a `term-card` (term plus one explanation);
  after that only the term. At most 12 Chinese chars (6 words) on screen at once.

**Compositing**
- **Layered stage**: bottom = background (style texture or an optional image); middle =
  the structure diagram; top = labels and leader lines. Build the three layers separately so
  one can move alone.
- **Exploded view**: the whole appears complete, then the parts slide apart along their own
  directions, hold, get labelled one by one, then come back together.
- **Two-column compare**: left "without it", right "with it", the same input running on
  both at once; or "common misunderstanding / what really happens". Both columns at the same
  scale.
- **Magnifier**: a round or rounded-rectangle mask enlarges a part of the whole, with a
  leader line back to its place. At most 3x, or the viewer loses position.
- **Analogy inset**: when an everyday analogy (queue, water pipe, mailbox) explains the
  mechanism, draw the analogy in a corner and the real structure on the other side; the
  same colour links each analogy role to its real part; the analogy appears at most twice.
- **Picture-in-picture**: user footage in a corner as evidence, the main picture still code
  animation; give the inset a frame and one caption line.

**Transitions** (logic, not decoration)

| Relation | Join |
|---|---|
| cause, sequence ("then", "so") | push along the data-flow direction, or a match cut (the last part of one scene becomes the first part of the next) |
| zoom into a detail | zoom join; the pushed-in part stays at the same screen position |
| take apart | exploded view inside the scene -> hold -> zoom join into one piece |
| contrast, turn ("but", "on the contrary") | wipe join |
| back to the whole, summary | a pull-back move inside the scene, or `fade` |
| new topic | `cut` or `fade` plus a `chapter-card` |

At most one noticeable transition every 10 s; between them, let the parts' own motion carry
the join.

| Symptom | Fix |
|---|---|
| All boxes and lines appear at once; the viewer doesn't know where to look | reveal with the voice; explained parts fade |
| Motion doesn't match the voice | re-time to the word timings from TTS |
| A generated architecture image with invented details | redraw every structure in code; images only as background |
| Terms used without explanation | `term-card` at first use; every term in the brief gets one |
| The analogy changes halfway | one main analogy per film, all the way |
| Too many, too fancy transitions | at most one noticeable transition per 10 s |
| A number not in the material is on screen | delete it, or verify and cite it first |

---

## `product-intro` - Product introduction

**What it is**: introduces one concrete product to prospects: what it is, what it looks
like, how to use it, what problem it solves, its measurable selling points. Afterwards the
viewer knows "what this is and whether I want to know more".
- **The input is the truth; no hallucination**: features, specs, prices, use cases and steps
  come only from the user's material, images, footage and request. Not in the material:
  don't say it. Unsure: don't say it. Never "a product like this would surely have ...".
- **The product image is the hero**: wherever the product appears, it is the user's image or
  footage, never redrawn, never generated.
- **Vivid is not exaggerated**: life comes from camera, rhythm and contrast (before/after,
  with/without the feature), not from piled-up adjectives.

**Sources**

| Source | When | How |
|---|---|---|
| User product images | appearance, close-ups, selling-point labels | white-background images cut out to transparent PNG and placed on the style's stage; real-scene photos keep their background and get camera moves; crop a part and enlarge it for details |
| User screenshots, recordings | software, app and web product demos | push into the operation area, overlay cursor and ripple; recordings trimmed to 3-8 s, picture-in-picture or full screen |
| User footage | unboxing, use, working state | trim; mute or lower the original sound; overlay captions and labels |
| Code animation | spec tables, comparison tables, step numbers, prices, flows, dimension labels, leader lines, end card | always code; numbers copied from the material |
| Code-drawn parametric model or diagram | **no product image**, or explaining inner structure, size, part relations | a parametric shape (bottle, body, package, phone) or a cut-away diagram, set from the material's description of shape, colour and material; invent no details; label it "illustration" (示意). Never turn the film into charts and numbers only |
| AI images: optional | backgrounds of use scenes (desk, kitchen, outdoors), mood textures | background and support only; **never the product itself**; prompt "no product, no text" |
| Web check | public specs the user explicitly asks for | cite; if not found, leave out |

**Never**: generate or redraw "this product" (models invent details); add colours,
accessories or features the material lacks.

**Editing**
- **Three parts**: reveal (the whole product, 1-2 scenes) -> use and selling points (one
  selling point or one step per scene, 3-6 scenes) -> end card (name, logo, action, 1
  scene). Lengths flex with the method.
- **Close-up per selling point**: push into the matching part (port, screen, material), a
  leader line points to it, a small card beside it gives the point and its number. One
  selling point per scene, never three in one.
- **Operations visible**: software products one step per scene; push into the operation
  area; cursor moves -> click ripple -> UI changes, in that order, about 1 s per step.
- **Camera instead of action**: product images are static; slow push, pan, slight 3D tilt
  and a gloss sweep give the feeling of looking at a real object. One camera move per scene.
- **Contrast**: when the material has before/after or with/without, split screen in sync,
  same scale on both sides.
- **Numbers**: specs and prices roll in, units as in the material; at most 3 numbers per
  screen.

**Compositing**
- **Stage + product + labels**: bottom = the style's stage or an optional scene background;
  middle = the cut-out product with a shadow; top = leader lines, selling-point cards, step
  numbers. Three separate layers.
- **Card carousel**: for several selling points, cards slide in from one side, the previous
  one shrinks back; the product stays still on the other side.
- **Detail magnifier**: circle a part of the whole and enlarge it beside, with a leader line
  back; 2-3x.
- **Device frame**: recordings inside a phone or monitor frame (use the style's device frame
  if it has one), explanation text outside.
- **Scene compositing**: a scene background + the user's cut-out product placed sensibly,
  perspective and light direction matched. Nothing in the background covers the product.
- **End card**: the user's logo (shape and colour unchanged); product name, one-line selling
  point, call to action in three lines; holds at least 2 s.

**Transitions** (clean, never steal the product's show)

| Moment | Join |
|---|---|
| whole -> detail | zoom join; the product stays at the same screen position |
| between selling points | cards slide in and out inside the scene while the product stays, or the product turns an angle on the stage; `cut` between scenes |
| between operation steps | the UI change itself is the join (`cut`); push join when the page changes |
| change of scene | `fade`, or the style's own wipe or light sweep |
| into the end card | elements gather to the centre inside the scene, then a dark join; the logo appears |

No spins, page turns, white flashes or glitches; at most two kinds of join per film.

| Symptom | Fix |
|---|---|
| A feature or spec not in the material | delete it |
| The product was generated or redrawn | use the user's image; with no image, a parametric model or diagram, and no close-ups of unknown parts |
| The product never appears, only charts and numbers | put a parametric model or cut-away on the stage and label the selling points on it |
| Product image distorted or covered by captions | keep its aspect ratio (contain); keep the caption band empty |
| Three selling points labelled in one scene | split into three scenes |
| Screenshot text unreadable | crop and enlarge the part, or push in to 2x |
| Logo recoloured or outlined | use the original, no effects (unless the user allows) |
| The whole film is product-image pans, no rhythm | add a contrast scene, rolling numbers, an end-card gather |

---

## `footage-edit` - Footage edit

**What it is**: cuts the user's uploaded clips and images into one film: highlight reel,
event recap, demo edit, vlog-style chain, best-of from several clips.
- **Success**: the most watchable seconds are taken; order and rhythm make people watch to
  the end; added text and effects help understanding and never cover the picture.
- **Length**: unless asked, highlights are 10-30% of the total source length, recaps
  30-50%; a length the user gave wins.
- **No new pictures**: only the user's images and footage; extra information comes from text
  cards, captions and graphics. AI images only if the user explicitly asks, and never
  showing the people or products of the footage.

**Sources**

| Source | How |
|---|---|
| User footage (the body) | for each clip check duration, audio, and a contact sheet; find the watchable moments (an action completing, a facial expression, a result appearing, a UI change); note start and end seconds for each |
| User images | freeze frames, covers, chapter-card backgrounds, or a fast "photo wall" between clips; camera move 2-4 s |
| User material and request | text for cards, names, scores, dates, points, all as the user gave them |
| Code animation | opening title card, chapter titles, captions, scoreboard, progress bar, joining graphics, end card, in the style |
| Original sound | keep live sound or commentary if it has value (adjust level), else mute and let music and voice carry it |

**Editing**
- **Select before cutting**: pick 1-3 usable moments per clip and write them into `visual`
  (which clip, from second x to y, why). Work back the number of moments from the target
  length: on average 2-5 s per scene.
- **Cut on the beat**: with music, cut points fall on beats; without, cut when an action
  completes.
- **J cut / L cut**: the next clip's sound starts before its picture (J), or the previous
  clip's sound runs into the next picture (L), so sections don't jar. Build it with
  overlapping audio and picture timing.
- **Speed**: slow the highlight moment (0.5x), speed up build-up (1.5-2x), keep 0.5 s of
  normal speed before and after a speed change; at most two slow-motions per film.
- **Jump cut**: inside one clip, remove pauses and jump to the next action; hide it with a
  slight push.
- **Freeze**: freeze a key moment 0.5-1 s with one line of text (name, score, point), then
  continue.
- **Highlight structure**: cold open (the strongest 1-2 s) -> title card -> clips by time or
  theme -> the strongest clip last -> end card.
- **Voice**: if there is narration, only one or two lines at section starts and turns, never
  per scene; captions never cover the footage's subject.

**Compositing**
- Footage full screen; text cards and graphics in safe-area corners or the bottom.
- Split screen: two angles of one event, or before/after, in sync, same scale, a thin
  divider.
- Picture-in-picture: a recording with the presenter, or a second angle in a small window
  with a frame and shadow.
- Photo-wall join: several images in quick turn (0.3-0.5 s each) between chapters.
- Chapter card: in the style, text from the user's material (as a scene, at least the
  contract minimum length).
- Scoreboard / info bar: a persistent bar for matches or data footage, updated over time.
- Portrait footage in a landscape film: blurred enlarged copy of the same clip (or the
  style's stage colour) on both sides; never stretch.

**Transitions** (mostly cuts)

| Moment | Join |
|---|---|
| inside one event | `cut`, on the beat or on action completion |
| between sections | `fade`, or a short white flash join, or the style's own wipe; with one short SFX |
| time jump | chapter card from a dark join, or freeze -> text -> `cut` |
| into a highlight | slow-motion inside the scene, plus a zoom join |
| end card | a dark join, or the style's end-card join |

No spins, page turns, heavy light effects; at most two kinds of join per film.

**Fallback**: only images, no footage: a photo recap with camera moves and text cards,
3-4 s per image.

| Symptom | Fix |
|---|---|
| A whole clip dropped in unedited | go back to the contact sheet, reselect, 2-5 s per scene |
| Cut in the middle of an action, feels stuck | cut at action completion or on the beat; check the first and last frames |
| Portrait footage stretched to landscape | contain it, blurred background or stage colour |
| Captions cover faces or scores | captions in the safe area; smaller or moved if needed |
| Original sound fights the music | lower the music where original sound is kept; never both at full level |
| New pictures were generated | remove them; use the user's footage (unless the user asked) |
| Far longer than the source's best parts | cut back to 10-30%; merge or drop weak sections |

---

## `feed-ad` - Feed ad

**What it is**: short traffic ads for feeds, short-video platforms and social media. Goal:
make people stop, remember one selling point, tap once. 15-30 s, portrait first (if the user
gave no ratio, suggest 9:16 and record the choice).
- **Derive, never invent**: the user's material and copy are the base: selling point, price,
  campaign, audience exactly as given. Visuals may extend it (the audience using it, the
  feeling), but no feature, effect, promise or price the material lacks.
- **One selling point per film**: said in the first 3 s, repeated two or three ways in the
  middle (visual proof, number, contrast, another person's view), action at the end. More
  selling points = more versions, never in one film.
- **Versions**: the same material with another hook (pain / result / question), another
  ratio, another action makes 2-3 versions. If the user didn't ask, make one and suggest
  variants in the summary.

**Sources**

| Source | When | How |
|---|---|---|
| User images and footage | the real product, real users, the logo | the product is always the user's image or clip; logo unchanged |
| AI images: optional | the audience's scenes, emotions, pain scenes, result scenes | describe audience, scene, emotion, light, "candid, no text"; leave the product for code compositing |
| Code animation | big text, prices, countdowns, contrasts, rating stars, buttons, progress, end card | big text is one of the stars of a feed ad |
| Music and SFX | the whole film | brisk; cut on the beat; buttons, numbers and joins each get one kind of SFX |
| User copy | narration and on-screen text | spoken, short; keywords from the copy go on screen unchanged |

**Editing**
- **30 s structure**: 0-3 s hook (pain / result / question + big text) -> 3-8 s the selling
  point (product or solution first appears) -> 8-20 s proof (visual proof + number +
  contrast, repeating the selling point 2-3 times) -> 20-30 s action (price, offer, button,
  platform). **15 s**: hook 3 s, selling point 5 s, proof 4 s, action 3 s.
- **First-3-seconds rule**: frame 1 has someone or something moving; text in second 1; the
  selling point said or shown before second 3. Never open with a logo, brand name or an
  empty shot.
- **Big text**: portrait, one line per screen, at most 12 Chinese chars (6 words), text width
  60-80% of the frame width; it enters with a pop or typewriter; holds by the hold formula;
  keywords change colour or scale up.
- **Repeat the selling point**: the same line at least 3 times (said at the start, as text
  in the middle, beside the button at the end), each time over a different picture.
- **Fast cuts**: middle shots 1-2.5 s each (shots shorter than the contract's minimum scene
  are cuts inside one scene); the action part may hold 3 s.
- **Contrast**: pain / solution, before / after, split or one after the other, same
  composition on both sides.
- **Action**: the button pulses; text starts with a verb ("Tap to claim", "Try it now");
  price or offer in big text with a countdown for urgency, only if in the material.

**Compositing**
- **Scene + big text**: a scene image or footage underneath, big text in the safe area
  (portrait: upper or lower third), never over faces.
- **Product placement**: scenes never contain a drawn product; the user's cut-out product is
  placed on top with roughly matched perspective and light; or the product gets its own
  full-screen scene.
- **UGC feel**: a phone frame, a simple caption bar, slight hand-held shake (code: 1% random
  drift, seeded), so it looks user-made.
- **Data cards**: ratings, sales, review counts slide in as cards, numbers roll; from the
  material.
- **Split contrast**: top/bottom or left/right "before / after", a divider in the middle.
- **End card**: logo, one-line selling point, button, platform, offer code; may keep 1 s of
  calm (dark, not pure black) to settle the fast pace.

**Transitions**

| Moment | Join |
|---|---|
| hook -> selling point | `cut`, or a short white flash join with `whoosh` |
| inside the proof | `cut` on the beat, or a wipe join with big text |
| contrast | push join, split screen inside the scene |
| into the action | dark join, or the picture dims and blurs inside the scene while button and price pop |
| versions | the same joins; only the hook part changes |

A noticeable transition at most once every 2-3 s; no page turns, spins or long fades (the
pace drops).

**Fallback without AI images**: everything in code: big-text-driven layouts, geometric
illustration, product-image camera moves, contrast cards; pure text + rhythm works in feeds
too. Landscape requested: big text goes to the left or right third.

| Symptom | Fix |
|---|---|
| Opens with the logo or brand name | open with pain, result or a question; logo on the end card |
| Four selling points in one film | keep one; the rest become variant versions |
| An effect or price not in the material | delete it |
| Big text covers a face or the product | move it into the safe area, or make it smaller |
| A pictured person seems to speak with wrong mouth movement | use stills or footage without speaking; tell the story with expression and action |
| The product was generated and doesn't match | use the user's image on top |
| Fast all the way, no landing | the action part holds 3 s, button pulses |

---

## `marketing` - Marketing showcase

**What it is**: a marketing film around one product: show its look in high quality, put it
into scenes of use, show details, selling points and price, end with conversion (buy, ask,
visit). 15-60 s.
- **The user's product image is the only "product truth"**: every extension (other scenes,
  other light, a hand holding it, motion) uses it as the reference; shape, colour, logo and
  proportions never change. Selling points, material, size and price copied from the
  material; nothing added.
- **Three kinds of shot** alternate: **showcase** (the product itself, clean background,
  camera move or turning feel), **scene** (the product in daily life), **detail** (material,
  ports, craft). Together they give "see it clearly -> imagine it -> believe it".
- **Conversion**: the end tells clearly what to do, where, and which offer (only from the
  material).

**Sources**

| Source | When | How |
|---|---|---|
| User product image (the base) | every appearance of the product | white background cut out to transparent PNG on the style's stage; also the reference for any extension |
| User footage | real use, unboxing | trim the useful seconds, overlay text |
| AI images: optional | scene images, other backgrounds, other light, hands | prompt "keep the product exactly as in the reference, same shape, same color, same logo, no text"; check each result |
| Code animation | selling-point cards, specs, prices, leader lines, contrasts, gloss sweeps, end card | in the style |
| Music and SFX | the whole film | textured music; few, precise SFX |

**Consistency check is the core step of this mode**: put every AI image (and every frame you
take from any generated material) side by side with the user's original and ask "are shape,
colour, logo and proportions the same?". If not, it does not go into the film.

**Editing**
- **30 s structure**: reveal (showcase, 2-4 s) -> scene (in daily life, 6-8 s) -> details (2-3
  close-ups of 2-3 s each) -> selling points and price (6-8 s) -> conversion (3-4 s). The
  60 s version doubles each part and adds one more scene.
- **Slow camera**: showcase scenes use slow push, pan, slight 3D tilt and a gloss sweep, one
  move per scene, 2-4 s; no fast cuts (they make the product unreadable).
- **Detail close-ups**: from the whole into a part (crop and enlarge 2-3x), a leader line, one
  line of material or craft text.
- **Scene shots**: the scene underneath, the product is the visual centre; nothing covers it
  for more than an instant.
- **Selling points**: one per scene; the card slides in from one side, the product on the
  other; numbers roll in.
- **Rhythm**: medium pace with textured music; only the conversion part is a bit faster.

**Compositing**
- **Stage showcase**: the style's stage (or a clean background) + the cut-out product +
  shadow + gloss sweep; switch 2-3 angles if the user gave multi-angle images.
- **Scene compositing**: a scene background + the user's cut-out product on top (matched
  perspective and light direction, a shadow). This is more faithful than letting a model
  draw the product, so prefer it.
- **Magnifier close-up**: circle a part, enlarge it beside, leader line back.
- **Three in a row**: three use scenes side by side (one third each), lighting up in turn.
- **Spec strip**: size, weight, material, capacity as icon + number in one row, one by one.
- **End card**: logo (the user's, unchanged), product name, one-line selling point, price or
  offer, call to action; holds 2-3 s.

**Transitions** (soft, at most two kinds)

| Moment | Join |
|---|---|
| between showcase scenes | product still, background changes inside the scene; `cut`, or the style's light sweep |
| into a scene | the product "drops" from the stage into the scene inside the scene (move + scale), background fades in; `fade` between scenes |
| into a close-up | zoom join; the pushed part stays at the same screen position |
| between selling points | cards slide in and out inside the scene; push join between scenes |
| into conversion | the picture gathers to the centre inside the scene, logo and price appear; `fade` |

No white flashes, spins or page turns.

**Fallback**: without AI images, the style's stage, gradients and geometric backgrounds,
with product-image camera moves and close-ups carrying the film. Only one product image:
never make "other angles" (they drift); use the one image for showcase, close-ups and scene
compositing, and suggest in the summary that the user adds angle shots.

| Symptom | Fix |
|---|---|
| The product changed shape, colour or logo in an image | drop it; use scene + user cut-out instead |
| The product is covered for long | choose other material or shorter seconds |
| Fast cuts all film, product unreadable | showcase scenes 2-4 s, slow camera |
| Material, size or offer not in the material | delete it |
| A selling-point card covers the product | card on the other side; product stays at the visual centre |
| No conversion part | add the end card: what to do, where, which offer |
| The composited product looks pasted on | add a shadow, match light and perspective, scale it sensibly |

---

## `game-promo` - Game promo

**What it is**: ads for a game (feeds, app stores, social): make target players stop, get
the gameplay, want to download. Mostly 15-30 s; 60 s for a long version.
- **Derive, never invent**: rules, character names, numbers and selling points from the
  user's material; visuals may extend the material's own look, but no gameplay or promise
  it lacks.
- **Three seconds decide**: the strongest visual or the most direct thrill (an ultimate
  skill, a critical-hit number, a comeback, a finished grand build) in the first 3 s.
- **Explain the gameplay**: in the middle the viewer understands "how to play" within 10 s:
  the core loop (do what -> get what -> get stronger), shown by an operation demo or a
  breakdown, not only piled effects.

**Sources**

| Source | When | How |
|---|---|---|
| User screenshots and key art | character reveals, UI, real gameplay pictures | never redraw; camera moves + impact frames; push into the UI's operation area |
| User gameplay recordings | core gameplay demo, highlight plays | pick 2-5 s thrills from the contact sheet; speed changes, overlaid numbers and text |
| AI images: optional | character variants, big scene images, bosses, item close-ups | keep the same character by using the user's character art as reference; 3-6 per film |
| Code animation | big text, critical-hit numbers, progress bars, stat growth, UI highlights, countdowns, download button, end card | in the style, timed to the music |
| Music and SFX | the whole film | strong rhythm; hits, skills, level-ups with `thud`, `rise`, `ding` |

**Editing**
- **Structure**: 3 s hook (strongest visual + one line) -> core gameplay (10-12 s,
  operation -> feedback -> growth) -> stacked thrills (8-10 s, back-to-back highlights,
  faster and faster) -> call to action (3 s, download button + platform + offer). The 30 s
  version scales; the 15 s version halves the stacked thrills.
- **Fast cuts**: middle and later scenes 0.8-2 s each (several beats may live inside one
  scene to respect the contract minimum); hook and call to action up to 3 s. Cut points on
  the beat.
- **Impact frame**: at each thrill, scale up to 1.1-1.2x and spring back, with a short white
  or colour flash (0.1 s) and a hit SFX.
- **Camera shake**: on crits, explosions, a boss landing: 0.2-0.4 s of seeded random offset,
  amplitude at most 2% of the frame. Never shake the whole film.
- **Flying numbers**: damage, coins, XP fly out of the hit point, scale up and fade; growth
  as a progress bar or a stat rolling up.
- **Speed**: slow to 0.5x just before a skill fires, back to normal or faster right after
  the hit.
- **Text**: one line per screen, at most 10 Chinese chars (5 words), big, centred or to one
  side, popping in on the beat.

**Compositing**
- **Picture + code overlay**: the picture underneath, code overlays UI frames, numbers, skill
  icons, big text on top; overlays never cover the subject.
- **Character reveal**: character art slides in from one side or scales in, a matching scene
  behind it, foreground particles and light in code.
- **Gameplay breakdown**: footage in the middle, leader lines around it marking "operation ->
  effect", one step per scene.
- **Growth contrast**: split screen "early / late" character or base, same composition.
- **Triple thrill**: three 1 s highlights in a row, the last one freezes with big text.
- **Call-to-action end card**: game logo (the user's, unchanged), one line, a download
  button with a slight pulse, platform icons, offer code.

**Transitions** (cut-driven; flashes are accents at least 3 s apart)

| Moment | Join |
|---|---|
| between thrills | `cut`; impact frame on the incoming scene's first frames, with a hit SFX |
| hook -> gameplay | short white flash join, or the style's light sweep, with `whoosh` |
| between gameplay steps | zoom or push join along the operation's direction |
| into stacked thrills | the beat speeds up; the first thrill enters with a shake; `cut` |
| into the call to action | the last thrill freezes; dark join into the end card; logo pops |

No page turns or spins.

**Fallback without AI images**: geometric characters and scenes in code, camera moves on the
user's screenshots, big text and numbers carry the rhythm.

| Symptom | Fix |
|---|---|
| First 3 s are a logo or slow build-up | move the strongest visual to scene 1; logo to the end card |
| All effects, no idea how to play | add an operation -> feedback breakdown in the middle |
| The character looks different in each scene | one character reference for every image; drop outliers |
| Gameplay or rewards not in the material | delete them |
| Shaking and flashing all film | only on thrills; keep the rest steady |
| Too much text, too small | one line per screen, at most 10 chars, larger for the portrait safe area |
| Cuts off the beat | fix the music first, then align scenes to its beat times |

---

## `game-drama` - Game ad short drama

**What it is**: a 9:16 feed UA ad of 60-75 s for a web or mobile game. One named hero with a
look (e.g. bald Ah-Qiang in a white vest) plays the game for the viewer: wronged opening ->
fight and loot -> merge and star-up -> beat the boss for a set -> grind the realm -> the most
special system -> two paths -> "come join". Success: in the first 5 s the hero's face and the
grievance contrast; every gameplay noun on screen as a sticker; readable on mute; a button at
the end. Pair with the method `game-drama` and the style `live-drama`.
- **Input**: usually a few hundred words of game description (gameplay, quality chain,
  bosses, realms, special system), a few game screenshots (often low-poly or cartoon), maybe
  the user's script or slogan.
- **Pictures are photoreal, not gameplay capture**: the hero and the big moments are
  photoreal drama stills (AI images: the main use case of this mode, optional). The user's
  screenshots **never become the hero picture**: low-poly screenshots in a photoreal scene
  break the illusion. They supply facts and UI layout only; say in the brief why they are
  unused. Exception: the user explicitly asks for real gameplay: show it inside a glass
  panel as an "in-game" window.
- **The plate is only a plate**: stickers, cards, numbers and panels are always the code
  layer on top; most of the motion comes from the sticker layer.
- **Facts only from the material**: quality chain, multipliers, quiz counts, monsters per
  wave, rounds per boss, platform, all unchanged; nothing invented.

**Sources**

| Source | When | How |
|---|---|---|
| User description / script | all content | gameplay nouns, numbers, rules, slogan; the user's own narration may become hero shouting, facts unchanged |
| User screenshots | facts and UI layout only | read the quality colour order, panel look, quiz categories, UI wording |
| AI images: optional (hero stills) | a character sheet first, then hero stills | 4-6 stills for 60-75 s, each reused across 2-3 scenes (new camera move, reframing, or darkened and blurred as a base); bosses, sea monsters, portals inside the stills |
| Code animation (the body) | every sticker, big word, gear card, quality bar, star-up, number, health bar, counter, warning bar, panel, stamp, button, caption | values and nouns are always typeset in code, never written by an image model |
| Music and SFX | the whole film | high-energy comedic; `thud` hit/merge/defeat, `rise` level-up/doubling, `ding` star-up/correct answer/coins, `pop` stickers, `whoosh` warning bar; within the contract's SFX limits |

**Editing**
- **Voice first, then durations**: narration per the method (60 s 230-260 zh chars, 75 s
  270-300), TTS, durations from the measured voice; stickers timed to `words.json`.
- **13 scenes / about 70 s skeleton** (60 s version: merge 03+04 and 10+11):

| # | Segment | Length | Picture |
|---|---|---|---|
| S01 | grievance hook | 4-5 s | raft still pans to the hero's face; "them: ..." and "me: ..." stickers + a sweat drop; big "Why?!" + impact frame |
| S02 | fight and loot | 4 s | fight still; impact frame + damage number on the first frames; a green card flies out; two cards collide: "merge" |
| S03 | quality chain | 4-5 s | same still, darkened and blurred; the big card changes green, blue, purple, orange + halo; quality bar lights; number 100 -> 800 rolls + x2 |
| S04 | star-up | 3-4 s | the orange card moves up and shrinks; five stars -> moon -> five moons -> sun; gold burst + "sun" lands |
| S05 | key -> gate -> boss | 6 s | abyss still framed at the gate top; three colour shards join; tilt down to the whole boss; name plate + health bar drop |
| S06 | set + effects | 7 s | same still, push in to the hero; health bar empties, "defeated" slams with shake; red halo + sparks; four skill stickers fly in one by one |
| S07 | realm | 7 s | portal still pushing in; big realm name; counter 0/50 fills, round 1 -> 4, strength bar turns red |
| S08 | boss arrives | 4-5 s | same still tilts up to the boss; round jumps to 5; warning bar sweeps + shake; "3 minutes a run", "pure relief" stickers |
| S09 | the most special | 8-9 s | raft still, the floating-house area enlarged and blurred; a plaque drops; the quiz panel slides in; question count rolls; three tabs light up |
| S10 | quiz makes you strong | 7 s | panel: select -> green tick -> "knowledge +10" flies to the knowledge bar -> rainbow card pops over the red card, which greys -> a wrong answer is crossed and flies into the mistake book +1 |
| S11 | settlement x2 | 4 s | settlement panel: gold 500 -> 1000 rolls, x2 stamp slams, gold particles burst |
| S12 | two paths | 8 s | tub still tilts from the floating fort down to the hero; big "two paths"; two path cards left and right |
| S13 | call to action | 5 s | same still pushes in; the title drops in two lines; CTA button pops and pulses; a cursor taps, a flash; hold to the end |

- **Impact frame**: once per thrill (hit, merge, defeat, sun, boss): scale 1.1 -> 1 + a 0.1 s
  white flash + 0.3 s shake; at most 8 flashes per film.
- **Shake**: only on boss landing, defeat, warning bar; 0.2-0.4 s, at most 2% of the frame.
- **Number rolls**: doubling, kills, quiz counts, gold; 0.6 s each, scale up on arrival,
  starting the moment the number is spoken (a roll of 0.6 s is a deliberate fast beat; it
  still respects the 0.25 s move floor).
- **One concept per scene, one sticker per word**: pop the sticker for each noun as it is
  spoken; stickers stay until the scene changes; at most 4 stickers on screen.
- **On-screen text**: stickers at most 8 Chinese chars (4 words), a big word at most 6 chars
  (3 words) in one line, one big word per screen; full sentences only in captions.

**Compositing**
- **Plate + sticker layer**: stills full-frame, not recoloured; stickers in the empty space
  beside the person, never over the face or hands; 3-6 sticker-layer elements per scene,
  each entering on the narration beat with its own motion; captions on top and never shaking.
- **One still, several scenes**: adjacent 2-3 scenes reuse one still with a new camera move
  (push, tilt, reframe) or darkened and blurred as a base with a new sticker set; 13 scenes
  from 4-5 stills.
- **Sticker colours by meaning**: yellow default, red danger / strong, cyan systems / numbers,
  white "me".
- **Gear choreography**: merging = two small cards collide; the quality chain = one big card
  changing its frame in place + a matching halo + quality bar + number; the star-up continues
  below the same card.
- **Boss reveal by tilt**: frame the gate or clouds first; after the shards join or the round
  jumps, tilt down (or up) to the boss; name plate or warning bar at the same time.
- **Set on the body**: a red halo around the hero + sparks; four skill stickers fly in from
  the right, stacked.
- **Panel scenes**: blurred base + glass panel; inside it, step by step with the voice.
- **Two paths**: big word + two contrast cards (red glow vs rainbow).
- **End card**: title in two lines + CTA button + cursor tap; no logo -> use the title; a user
  logo goes above the title.

**Transitions**: same still continuing (S03->S04, S05->S06, S07->S08, S09->S10, S12->S13):
`cut`. Scene change (S01->S02, S04->S05, S06->S07, S08->S09, S11->S12): `cut` + a splash
wipe built into the incoming scene. Into a thrill or the boss (S02->S03 after the merge;
S04->S05 also possible): `cut` + punch-in zoom on the incoming scene. Other changes
(S10->S11): `cut` + a short flash. No `fade`, no dark frames. A noticeable transition at most
every 3 s.

**Fallbacks**: no hero stills: the style falls back to a deep-sea blue gradient + the sticker
layer; the hero becomes a large badge or a hero-name sticker; screenshots may appear in a
glass panel as a small window; say so in the summary. No voice: stickers and big words carry
every noun; the hero's shouting becomes big words; hold formula for each. Another style
chosen: same structure and method; stills follow that style or are dropped; the style's
cards or labels replace stickers. 16:9: stickers left and right, hero in the centre.

| Symptom | Fix |
|---|---|
| A pixel or cartoon style chosen, but the request wants live-action drama | follow this mode: photoreal stills, style `live-drama`, and tell the user the style was switched and how to keep the pixel look |
| A screenshot used as the hero picture | remove it; screenshots give facts only |
| The hero is not the same person in each still | every still uses the character sheet as reference; redo outliers |
| Stills look cartoon, anime, broken face or hands | prompt starts "cinematic live-action still, photorealistic", ends with the negative list; simpler hand poses |
| Boss scene dark and scary | "bright mysterious blue-violet light, not dark, not horror" |
| No face and contrast in the first 5 s | S01 = raft still panning to the face + two contrast stickers |
| Qualities or skills summarised | name each in the voice, pop each on screen |
| Sticker covers a face or hands | move it to the other side or make it smaller |
| More than 4 stickers or 3+ colours on a screen | split into two scenes or delete non-noun stickers |
| Shake and flash everywhere | keep only hit, defeat, boss, sun |
| 75 s over time | narration at most 300 zh chars; first cut the differentiator's third scene |
| Stickers or the CTA in the caption band | CTA above 72% of height; stickers clear of the caption band |
| A number not in the material | delete it |

---

## `warmup` - Event warm-up loop

**What it is**: plays in a loop before a conference, launch, annual meeting or forum, while
people take their seats and chat. It gives the room a mood and says "what event this is and
when it starts"; it does not teach content. 1-3 min, usually loopable.
- **Input and copy first**: event name, theme, slogan, time, agenda, speakers, brand logo and
  key visual all come from the user; imagery may be drawn from the theme (technology ->
  light and particles; nature -> mountains, sea, mist; city -> night views and traffic), but
  no information the material lacks.
- **Music-led, little or no voice**: no narration by default (only if the user asks); music
  and picture carry the mood; on screen only big titles and a little information.
- **Slow and loopable**: scenes 5-10 s with slow motion; the first and last scene use the same
  image so the loop does not jump.

**Sources**

| Source | When | How |
|---|---|---|
| User key visual, logo, speaker photos | title, agenda and speaker pages | never altered; logo shape and colour unchanged; photos cropped to one common ratio |
| User footage | last year's recap, venue shots | slowed or normal speed, the style's colour wash over it; 5-10 s |
| AI images: optional | plates for ambient shots (then camera-moved), key-visual extensions, agenda backgrounds | keep the key visual's colours (use it as reference); style anchor words in the prompt |
| Code animation | big titles, slogan, countdown, agenda, name plates, date and venue, particles and light, the loop join | in the style |
| Music | the whole film | calm, spacious, no vocals, loopable |

**Editing**
- **Structure (2 min, loopable)**: key image + event name (10 s) -> theme slogan (10 s) ->
  mood part 1 (ambient + date and venue, 20 s) -> agenda (20-30 s, optional) -> mood part 2
  (speakers or last year's pictures, 20-30 s, optional) -> countdown or "starting soon"
  (10-20 s, optional; a countdown needs the user's start time or a fixed length) -> back to
  the key image + event name (10 s, joins the opening).
- **Slow**: each scene 5-10 s with one slow motion only; text fades in over 1-1.5 s or rises
  slowly from below, holds at least 4 s, fades out over 1 s.
- **Big type**: event name and slogan one line per screen, large, loose letter spacing,
  centred or per the style's layout; information lines (date, venue) one small line.
- **Breathing gaps**: 1-2 s between parts with picture only, no text (keep the slow motion).
- **Loop**: the last scene's picture and music join the first; the music's length equals the
  film and ends at the opening's level.
- **Countdown**: code-drawn digits or a progress ring for the user's given length; without a
  given time, no countdown: a "starting soon" card.

**Compositing**
- **Ambient + big text**: an image or footage plate, big text in the centre or lower third;
  a translucent band or gradient behind text for legibility.
- **Key-visual extension**: the user's key visual in the centre, a matching background
  extending around it, slow camera.
- **Agenda page**: a timeline on the left, topics on the right, lighting up one by one; the
  current item highlighted if times are given, else all lit equally.
- **Speaker page**: photos cut to circles or one rectangle ratio, name and title in two lines,
  three or four per group appearing in turn.
- **Brand**: a small logo resident in a corner; large and centred on the end card.
- **Particles and light**: slow, low-density code particles and light spots over the
  pictures for depth.

**Transitions**: only `fade` and slow push joins; no hard cuts, no flashes, nothing fast.
Between ambient scenes: `fade` plus slow in-scene opacity and drift (the contract fade is
0.4 s; build the longer softness inside the scenes). Into titles: the picture dims slightly
inside the scene, text fades in. Into agenda and speakers: the background stays, the content
layer slides in. Loop join: the end fades to the key image, the same as the opening scene.

**Fallbacks**: without images, gradients, mist-like light spots, particles and the style's
stage textures; camera moves on the key visual. With voice on: only an opening welcome and a
"starting soon" line, slow. No start time: "starting soon" card instead of a countdown.

| Symptom | Fix |
|---|---|
| Fast pace, hard cuts | scenes 5-10 s, only fades and slow pushes |
| People talking or text in the pictures | choose other plates; "no people talking, no text" |
| Big text unreadable over the brightest part | add a translucent band or gradient, or move it to the other side |
| The loop jumps | the same image first and last; music ends at the opening level |
| Agenda items or speakers not in the material | delete them |
| Countdown doesn't match the real start | without a reliable time, "starting soon" |
| Music has vocals or a strong beat | choose calm, ambient, instrumental music |

---

## `meeting-share` - Meeting share

**What it is**: turns a written record (minutes, talk script, report outline, sharing notes)
into an internal film of about 2 minutes for colleagues, all-hands or management who were not
there. Success: viewers see the source's structure (every section on screen), hear what the
source says, and the length is close to the source's timing. Pair with the method
`meeting-share`; style `exec-dark` (white version `exec-brief`).
- **Input**: usually pasted text of a few hundred to a few thousand words, with section
  headings, often with per-section lengths or time stamps, maybe a line "made by team X";
  usually no images or video.
- **One theme per film**: the source's conclusion line appears once on the title card and
  once again at the end.
- **Source numbers, names, team names, dates, decisions and the closing line unchanged**;
  approximations stay approximate; quotes of public figures or public data verified before
  they go on screen, else dropped.

**Sources**

| Source | When | How |
|---|---|---|
| The source text | all content | narration, on-screen text and section names; no facts the source lacks |
| Web check | the source quotes public figures or public data | verify the original and the year; cite it |
| AI images: optional | **one photo plate per main scene** (role `plate`) | office or meeting scenes matching the source; people only from behind or as hands; **subject to one side, the other side empty** for content; opening and closing share one plate; adjacent scenes of one section reuse one; at most 12, else reuse |
| Code animation (the body) | all text, kickers, progress bar, big numbers, dots, rings, cards, timelines, dialogue cards | **no charts** (bar, line, pie); only big numbers, dots, dot matrices, meters, timeline cards |
| User images | logo, photos, meeting screenshots | logo on opening and closing cards; photos darkened as plates instead of generated ones; screenshots inside glass cards |

**Editing**
- **Time follows the source**: split by section first (each within 15%, all shrunk in
  proportion if the total exceeds the target, never one section alone), a chapter card or new
  kicker at each section edge, then split inside the section by the measured voice. Without
  source timing, split by content, at least one scene per section.
- **Narration is commentary, not reading**: rewrite into short spoken sentences per the
  method; content, order, facts, numbers, names and the meaning of the closing line stay;
  room talk removed and never added. Narration cap about target seconds x 4 Chinese chars
  (the method's x 4.5 x 0.9).
- **Headings on screen only**: section names in the kicker or chapter card (noted in
  `visual`), never in the voice.
- **Scene skeleton**: opening card 3 s -> (the source's opening, 1-2 scenes, if any) -> title
  card (title + theme line) -> each section 1-3 scenes (a major section's first scene carries
  a chapter card, a minor section changes the kicker; a point with numbers gets its own
  scene) -> closing line 1 scene -> closing card 3 s. About 12-16 scenes for 2 min.
- **Voice first**: TTS all scenes (calm formal voice, rate +5% to +10%), durations from the
  measured voice; opening and closing cards set by hand at 3 s; if the closing line needs a
  longer hold, add a short no-narration end scene. Entrances timed to `words.json`: a count
  starts on the spoken number, an item slides in when it is named.
- **One concept per scene**: as the voice names it, the element appears; explained elements
  stay in place.
- **Rhythm**: opening and scene-setting scenes 4-8 s, statements 5-7 s, data and structure
  8-12 s (the 12 s scene cap applies; split longer ones).
- **On-screen text**: kicker (section name) + **one conclusion headline per scene, at most
  12 chars (6 words)** (`statement`, rising from blur to sharp; the scene's conclusion, not
  the heading) + card lines at most 12 chars each; one big number per screen.
- **Pictures, not the script**: no full source sentence on screen. Besides the headline only
  keywords, numbers, names or role names, and labels of at most 6 chars (3 words). Draw the
  rest:
  - relation, handover -> two role circles + an arrow, a "task" card slides along it
  - quantity -> big number counting up, dots popping one by one, a dot matrix filling
    (`big-number`, `dot-count`)
  - dilemma, contrast -> two cards or two doors, an empty symbol between them
  - ratio, gap -> a `meter`: the tick at "should", the fill stops at "actual"
  - process, plan -> a timeline or week cards lighting up one by one; ownership -> column
    cards with items sliding in; filtering -> a funnel with particles
  - scene, dialogue -> a `dialog-card` acting it out; the line is the source line, no extra
    big text
  - mechanism, cycle -> one recurring symbol (ring, node) that is missing, then completed,
    then lit across scenes
  Ask for every scene "what is this sentence as a thing on screen?"; only if there is no
  answer fall back to headline + one short phrase.

**Compositing**
- **Section = chapter card + kicker**: each source section is a chapter card (major) or a
  kicker name (minor); never mix two sections in one scene; a section's budget is the total of
  its scenes.
- **Layered stage**: photo plate (darkened, slow push) -> glass cards / big text -> kicker ->
  progress bar -> captions. Separate layers so one can move alone. The progress bar's marks
  come from the cumulative scene durations.
- **One recurring symbol**: from the source's core mechanism or theme (a flow, a node, a
  cycle), one geometric symbol through the film: dashed or missing in the problem part,
  completed in the solution part, lit at the end. If none exists, don't force one.
- **Opening**: dialogue acted as a dialogue card (role or invented names, never real names);
  a scene as one plate + one big line; otherwise straight to the title card.
- **Three ways to draw numbers**: tens = dots popping; tens of thousands = a dot matrix
  filling; ratios = a meter.
- **Ownership** in column cards; **plans and milestones** in timeline cards by week or month,
  the current or key cell accented.
- **Closing line**: serif, centred, the key phrase in the accent colour; below it, the
  recurring symbol's final form.

**Transitions**: usual change: `fade` (or the style's soft fade). Same scene continuing
(dialogue in two scenes): `cut`. New major section: `cut` or `fade` into a scene that rises
from near-black (#0B0D11, never pure black) with the chapter card inside it. Opening card ->
opening, closing line -> closing card: `fade`. At most one noticeable transition every 10 s;
no hard black frames.

**Fallbacks**: without plates, the style's radial gradient plus a faint dot or grid texture;
glass cards at 70% opacity. Without voice, kickers and card text carry everything, hold
formula per block, the source timing still kept. SFX only: `whoosh` + `thud` on a major
section's first scene, `ding` for dialogue messages, `click` when the symbol completes
(within the contract limits).

| Symptom | Fix |
|---|---|
| A section much longer or shorter than the source timing | adjust its narration to its budget +-15%; cut discussion and transition words, never facts |
| Sections merged, reordered or missing | restore source sections and order, at least one scene each |
| Headings spoken | remove from `narration`; keep in kicker or chapter card |
| A scene without a headline | add a conclusion `statement` of at most 12 chars |
| Source sentences on screen, slide-deck look | delete them (voice and captions carry them); keywords + shapes |
| Narration sounds read out, with "hello everyone", "thank you" | spoken commentary per the method; remove room talk |
| Narration changed a fact, number or the closing line | restore the source fact |
| A number made exact ("tens of thousands" -> 9,325) | restore the approximation; show "10,000+" |
| Bar, line or pie charts appear | big number + dots, dot matrix or meter |
| The plate is brighter than the text | move content to the darker side; small text into a glass card; don't darken the whole plate |
| The plate is invisible, near-black frame | plate brightness back to about 0.5-0.6; subject on one side |
| A black blink between scenes | `fade` or `cut`; chapters rise from near-black, never pure black |
| A quote with the wrong year | verify against the original; else delete |
| A plate shows faces or text | choose another plate: "seen from behind, no faces, no text" |
