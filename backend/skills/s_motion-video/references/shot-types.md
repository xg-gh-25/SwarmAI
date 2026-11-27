# Shot types

Every scene in `storyboard.json` names one `shot_type` from the ids below (the schema
enforces the list). The type decides the picture and its timing parameters; the style
decides how it looks. Write the cue points in `visual` (`element <- spoken keyword`).

All types obey the motion floor (`director.md` section 9) and the contract: moves at least
0.25 s, never fully still beyond the contract limit except an end-card hold, last motion
settles before the scene end. "Hold" below means the element stays readable and unchanged;
keep one slow drift in the frame. Chinese char limits convert to English at 2 chars = 1 word.

Layout rule for every family: at least three different layouts across the film (centred,
left/right split, top/bottom, full-width band, triptych); adjacent scenes never share one.
Portrait: splits and triptychs become vertical stacks.

---

## Explainer family (16)

| id | Name | Picture and motion | Use for | Parameters and rules |
|---|---|---|---|---|
| `question-card` | Question card | one big question in large type, or a small dialogue scene | opening hook | the question is concrete; keep the answer for later scenes |
| `big-number` | Big number | one number at the visual centre, one line of explanation beside it | shock-number hook, key evidence | number rolls 1.2-2.5 s and stops on the spoken word; then unchanged at least 0.5 s; give it a weighted shape or physical reference |
| `term-card` | Term card | the term plus a plain-words definition | naming a concept | term in the style's title font; definition at most 15 chars (7 words) |
| `analogy-map` | Analogy map | familiar thing on the left, new concept on the right, lines link the matching parts | making it concrete | link one correspondence per narration clause; colour-match each pair |
| `flowchart` | Flowchart | nodes appear in order, arrows draw segment by segment | process steps | horizontal in landscape, vertical in portrait; current step accented, the rest recede |
| `structure-reveal` | Structure reveal | whole structure as a ghost (about 20% opacity) -> inked layer by layer | architecture, composition | one layer per cue point; at the end restore all and hold the full picture at least 1 s |
| `cause-chain` | Cause chain | A -> B -> C, each arrow is a "so" | mechanism, "why" | the final result in the accent colour |
| `timeline` | Timeline | one horizontal axis, nodes light up in turn; the camera may track along the axis | history, evolution | nodes at true time intervals; labels alternate above and below |
| `compare-columns` | Two-column compare | two equal-width columns, compared row by row on the same dimension | before/after, option vs option | only two; same scale on both sides; the conclusion in accent colour once |
| `data-chart` | Data chart | bars grow, lines draw, rings fill | data stories | one chart, one conclusion, written as the chart title; axis from 0; true proportions; at most 3 metrics side by side; label within 0.5 s of the value landing; no pie, no dual axis |
| `formula-breakdown` | Formula breakdown | terms appear one by one, each with a note below | calculation, principle | one term per clause; end by substituting real numbers |
| `zoom-detail` | Detail zoom | push into one part of the whole picture | detail, key step | push 1.5-3x; hold at least 1.5 s before going on; magnifier at most 3x or the viewer loses position |
| `myth-fix` | Myth fix | wrong statement appears -> gets struck out -> replaced by the right one | counter-intuitive points, lists | the strike is a clear motion; the struck text stays legible |
| `numbered-list` | Numbered list | a counter like "01 / 03" runs through, one item per scene | list structure | the header counter advances scene by scene; every item in the same layout |
| `breather-card` | Breather card | one sentence, an almost still picture | before a turn, between parts | one restrained entrance, then only a slow drift (not fully still) |
| `summary-card` | Summary card | back to the opening image, plus the one-line thesis and at most three points | last scene | build it, then hold (end-card hold) while the voice states the conclusion; hold at least 1.5 s |

---

## Ad family (14)

| id | Name | Picture and motion | Use for | Parameters and rules |
|---|---|---|---|---|
| `hero-reveal` | Hero reveal | a mask or light band uncovers the product; light push 1.00 -> 1.05 | product reveal | the most ceremonial moment of the film; product 40-50% of frame height; a "stop" scene or beat right before it; a full-screen impact counts toward the 2-3 per film |
| `detail-push` | Detail push | after the frame settles 0.5-1.5 s, push into a part over 1-2 s to 1.5-3x | one key part or spec | hold at least 1.5 s after the push; before pushing, check that text is still inside the safe area at the end of the push |
| `callout` | Callout | a leader line draws from a point on the product to a label | 2-3 selling points on one picture | one leader line per narration clause; label at most 6 chars (3 words) |
| `number-flip` | Number flip | a number rolls, or flips digit by digit, to the target | battery life, speed, price, countdown, time saved | roll 1.2-2.5 s; land on the spoken word; then unchanged at least 0.5 s |
| `feature-grid` | Feature grid | 3-6 cards staggered into a grid | showing breadth of features | total stagger at most 0.5 s, or one card per clause; size cards by priority |
| `before-after` | Before / after | two columns (portrait: top/bottom), old first, then new | feature updates, result comparisons | only two; old greyed or struck, new in the accent colour; same framing on both sides |
| `ui-walkthrough` | UI walkthrough | one window or phone frame stays; the screen content changes along the flow | app demos | each UI state holds 1.5-3 s; one operation per scene; use the user's screenshots |
| `cursor-action` | Cursor action | the cursor moves to a button and presses; a ripple; the UI responds | showing one concrete operation | start pushing in to 1.4-2.2x 0.3-0.5 s before the click; only one protagonist moves at a time; about 1 s per step (move -> click ripple -> change) |
| `input-generate` | Input -> generate | typing into an input box, a waiting state, then the result | AI and search products | typing 7-10 chars/s; wait 0.5-1 s; the result appears in chunks |
| `anchored-rotation` | Anchored rotation | brand name or main line fixed; people, scenes or uses beside it rotate | brand films, "for everyone" | the anchor never moves after it enters; each rotating item at least 0.8 s |
| `crash-in` | Crash-in | "Is it A? Is it B?" rotates a few times, then the subject crashes in and pushes the words away | hooks, defining a category | the entry is a collision, not a fade |
| `social-proof` | Social proof | user counts and ratings roll; partner logo wall | proof | only numbers and logos the user gave; the biggest number comes last |
| `price-reveal` | Price reveal | original price appears -> struck -> final price flips and lands | promotions | the final price is the biggest text in the scene; currency sign and decimals one size smaller |
| `logo-endcard` | Logo end card | clear the frame -> logo draws or pops -> wordmark follows -> slogan and action | last scene | built within the first 40% of the scene, then still (end-card hold) at least 2-2.5 s; wordmark about 0.2 s after the logo settles; at most 4 elements; one action; QR at least 20% of the short side |

---

## Common family (8, used by the production modes)

| id | Name | Picture and motion | Use for | Parameters and rules |
|---|---|---|---|---|
| `title-card` | Title card | film title plus one theme line | opening and closing cards, `meeting-share` | open and close cards about 3 s, set by hand; the theme line said once here and once again at the end |
| `chapter-card` | Chapter card | section number and name | section edges | text from the user's material; as its own scene at least the contract minimum length; or inside the first scene of the section |
| `statement` | Statement | one conclusion headline rises from blur to sharp, plus keywords and shapes | `meeting-share`, brand lines | headline at most 12 chars (6 words), a conclusion, not the section heading repeated; no full source sentence on screen |
| `dialog-card` | Dialogue card | chat bubbles act out a dialogue from the source | openings, scenes with dialogue | speakers by role name, never real names; the line on screen is the source line; no extra big text |
| `dot-count` | Dot count | dots pop one by one (tens) or a dot matrix fills (thousands and more) | numbers without a chart | the count starts on the spoken number; approximate numbers stay approximate |
| `meter` | Meter | a slot with a tick at "should" and a fill that stops at "actual" | ratios, gaps | the fill moves at least 0.25 s and lands on the spoken word |
| `footage-clip` | Footage clip | a trimmed clip of the user's video, full screen or picture-in-picture, with overlays | `footage-edit`, demos, proof | 2-5 s of usable footage per scene; optional freeze 0.5-1 s with one line; slow-mo 0.5x and fast 1.5-2x, 0.5 s normal speed on each side of a speed change, at most 2 slow-mos per film; never stretch portrait footage |
| `ambient-plate` | Ambient plate | an image or clip plate with one slow camera move, big text over it | `warmup`, breathers, chapter mood | 5-10 s; one slow move only; text fades in over 1-1.5 s, holds at least 4 s, fades out over 1 s; a soft band behind text if the plate is bright |
