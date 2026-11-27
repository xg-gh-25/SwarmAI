# Writing methods

The writing method (`storyboard.method`) decides how the script is organised: structure,
opening, scene order, rhythm and narration tone. It does not decide colour, type, layout or
motion (that is the style). Read the method together with its group playbook
(`explainer-playbook.md` or `ad-playbook.md`); the method wins where they differ.

Unsure which to pick: explainer -> `principle` or `science`; ad -> `product`.

Style ids below refer to the skill's style list; if one is not installed, use the closest
installed style. A user-chosen style always wins; the method's structure stays the same and
is told with that style's visual means. English word counts use chars x 0.58.

| id | Name | Group | One-line test |
|---|---|---|---|
| `science` | Popular science | explainer | everyday curiosity; the motive is "that's interesting" |
| `principle` | Mechanism explainer | explainer | the viewer wants to really understand how it works |
| `tutorial` | How-to steps | explainer | the viewer must be able to *do* it afterwards |
| `listicle` | List | explainer | parallel items with no order |
| `story` | Case story | explainer | a real case, event or history; tension carries it |
| `data` | Data story | explainer | numbers are the hero |
| `meeting-share` | Meeting record share | explainer | a structured document told faithfully |
| `product` | Product introduction | ad | explain a product clearly and credibly |
| `launch` | New product launch | ad | first appearance, built around one hero reveal |
| `feature` | Feature update | ad | existing users, "before vs now" |
| `brand` | Brand story | ad | attitude and identification, not features |
| `promo` | Promotion | ad | offer + deadline + action |
| `game-drama` | Game ad short drama | ad | a named hero plays the game for the viewer |

---

## Explainer methods

### `science` - Popular science

**When**: questions ordinary people are curious about: nature, the body, animals, daily
life, history trivia. No expertise; the motive is "interesting", not "I need it for work".

**Versus `principle`**: principle goes down to the mechanism so the viewer can retell it;
science puts fun first, stops at "deep enough", and leaves one point the viewer remembers
and wants to share.

**Think first**

| Element | Ask yourself |
|---|---|
| Curiosity point | the "why" the viewer would ask, in their words, no jargon |
| Intuitive answer | what most people think; often wrong or half right |
| Truth | one sentence; this is the thesis |
| Life landing | when, in daily life, the viewer will remember it |

One film answers **one** question. If the material has several fun points, keep the most
fun one and delete the rest.

**Beats**: curiosity hook -> set up the intuitive answer, then overturn it -> the truth
layer by layer (2-4 scenes, each "one layer deeper") -> an everyday example or a small
experiment -> call back to the opening question, plus "Next time you see ..., you'll know
...".

**Beat rules**
- End each scene with a small hook into the next: "But here's the problem ..." (可问题来了……),
  "And that's not all." (但这还不是全部。)
- Each layer of truth is more surprising than the last, or closer to the viewer's own life.
- Opening: a question, a counter-intuitive fact, or an oddity in daily life: "Have you
  noticed that the mirror flips you left to right, but not top to bottom?" Never a
  definition, never "let's do some science today".
- At most two technical terms in the whole film, each followed at once by plain words. Use
  a physical comparison ("about the weight of a grain of rice") rather than unit conversion.
- Simplify, never falsify: say "simply put" where you simplify, "the main current
  explanation is" where it is disputed. Every fact and number has a source.
- No exaggeration, no fear-mongering ("eating this every day causes cancer"). Health and
  safety advice only with a reliable source.

**By length**: 15 s question -> one-line truth with the most direct picture; 30 s question
-> intuitive answer overturned -> truth -> life landing; 60 s full structure, truth in two
or three layers; 3 min+ two or three linked questions, each raised by the previous answer.

**Voice**: like chatting with a friend: "you", a little surprise and humour, many short
sentences. Friendly-chat voice; cartoon voice for children. Music bright or calm.

**Shots**: `question-card`, `myth-fix`, `analogy-map`, `zoom-detail`, `structure-reveal`,
`big-number` with a physical reference, `summary-card` back to the opening image.

**Default style**: `iso-infographic`. Alternatives: warm hand-drawn `watercolor`; children
`crayon-book`; light humour `midcentury-toon`; pop-up book `paper-popup`.

| Symptom | Fix |
|---|---|
| Sounds like a textbook | open with the viewer's question; delete definitions and background |
| Several knowledge points | keep one question, delete the rest |
| Simplified into something wrong | say "simply put ...", keep the correct part; drop what you are unsure of |
| No landing | call back to the opening question; give a "next time you see ..." |

### `principle` - Mechanism explainer

**When**: how a concept, mechanism or technology works (RAG, compound interest, HTTPS, an
engine). The viewer wants to really understand, often for work or study.

**Versus `science`**: goes down to the mechanism; afterwards the viewer can say in their own
words "why it can do that". Needed terms are allowed, each one explained.

**Think first**
- Answer the explainer six questions, especially the thesis.
- The mechanism must be drawable as **one continuous change on one stage**: what goes in,
  how many steps it passes, what each step changes, what comes out. If you cannot draw it,
  you have not understood the mechanism yet.
- Find one analogy or one "master image" that runs through the film. The master image grows
  scene by scene in the body; the opening and the end both return to it.

**Beats** (Concept structure): confusion or question -> one-line thesis -> frame first
("the whole thing has three steps") -> the mechanism revealed layer by layer (3-5 scenes,
same stage) -> a worked example with real numbers, or a "when it fails" counter-example ->
back to the master image, state the thesis again.

**Beat rules**
- If the mechanism has a clear order, tell it as a process, one step per scene, with a
  header "2 / 4".
- To explain the difference between two options, make the body a `compare-columns` run,
  row by row on the same dimension.
- Calm and clear; many signpost words: "first ... next ... the problem is ... that's why
  ...". A term gets a plain-words line the first time, then the same name all film.

**By length**: 15 s what problem it solves plus its one core step; 30 s one mechanism fully;
60 s a 3-4 step mechanism plus a worked example; 3 min+ a roadmap at the start, 2-3 parts,
one sub-mechanism each, one-line summary at the end of each part.

**Voice**: formal broadcast, or sincere friendly chat. Music calm, tech, or none.

**Shots**: `structure-reveal`, `flowchart`, `cause-chain`, `formula-breakdown`,
`analogy-map`, `zoom-detail`, `compare-columns`.

**Default style**: `iso-infographic`. Alternatives: hard mechanical and engineering
(one machine through the whole film) `anatomy`; engineering drawing `blueprint`; derive as
you talk `whiteboard`.

| Symptom | Fix |
|---|---|
| Only nouns, no mechanism | draw the change: what each step looks like before and after |
| The body is one scene | split into 3-5 scenes on one stage |
| The analogy is harder than the concept | use an everyday thing; state two or three correspondences and the limit |
| Viewer can't say the thesis | state it by scene 2, and once more in the last scene |

### `tutorial` - How-to steps

**When**: teaching something the viewer can follow: software settings, procedures, recipes,
paperwork.

**Versus `principle`**: the viewer must be able to *do* it, not only understand. Order and
doability beat everything; every step is concrete down to the action.

**Think first**

| Element | Ask yourself |
|---|---|
| Result | what it looks like when done; what trouble it saves |
| Preparation | tools, materials, accounts, permissions needed before starting |
| Steps | 3-6 steps; more than 6 -> group into phases (prepare, do, check) |
| Pitfalls | where each step most often goes wrong |

Steps and names follow the user's material: button, menu and form names exactly as on the
interface; never invent them.

**Beats**: result first (the finished state, or the trouble saved) -> preparation (one scene)
-> step 1 to N (one step per scene, same stage, header "Step 2 / 4") -> check (how to know
it worked) -> close (one common mistake, or what to do next).

**Beat rules**
- Narration in imperative, verb first: "Open Settings", "Tap the plus in the top right",
  "Pour the flour into the bowl". One action per sentence.
- At most one "Note:" per step, only when there is a pitfall.
- On screen: the current step highlighted; finished steps ticked and faded.
- Operations must be visible: use the user's screenshots, push in to where the action is,
  show it with cursor and ripple (`ui-walkthrough`, `cursor-action`).
- After each step, hold about 1 s so the viewer sees the result before the next step.

**By length**: 15 s only a one- or two-step tip; 30 s 3 steps; 60 s 4-6 steps; 3 min+ two or
three phases, each opening with what this phase achieves.

**Voice**: steady, clear, unhurried. Friendly-chat voice. Music calm or none; music never
covers the rhythm of the steps.

**Shots**: `numbered-list`, `flowchart`, `ui-walkthrough`, `cursor-action`,
`compare-columns` (wrong way vs right way).

**Default style**: `whiteboard`. Alternatives: software operation `living-screencast`;
assembly and engineering steps `blueprint`.

| Symptom | Fix |
|---|---|
| A step is skipped | walk through the real operation again; add the missing step |
| Two actions in one scene | split into two scenes |
| Names don't match the interface | correct them from the user's screenshots and material |
| Viewer can't tell if it worked | add a "check" scene: what you should see if it is right |

### `listicle` - List

**When**: several parallel points: myths, tips, reasons, picks, comparisons.

**Versus `tutorial`**: items are parallel, with no order (ordered items -> `tutorial`). Lists
run fast with a uniform layout; very good for portrait and feeds.

**Think first**
- Topic as one "N ..." sentence: "Three habits that drain your phone battery".
- Count: 3 is strongest, usually no more than 5; long films at most 7-10, in two groups.
- Each item: a short title plus one reason or one concrete example.
- Order: **strongest last, second strongest first**, the rest in the middle.

**Beats**: hook (say "N" and the viewer's stakes directly) -> item 1 to N (one or two scenes
each, same layout, header counter "01 / 05") -> close (one-line summary, or "How many of
these do you do?").

**Beat rules**
- Titles at most 12 chars (6 words), all in the same grammatical pattern (all verb-first,
  or all "Don't ...").
- Items of similar length and weight. If one is clearly more important, put it last.
- One concrete example per item beats a vague explanation.
- Rhythm: similar item lengths make a steady beat; break the pattern once in the middle
  (e.g. item 3 as a `myth-fix`, or a `big-number` insert).
- Crisp: the first sentence of each item is its conclusion.

**By length**: 15 s 3 items, one sentence each; 30 s 3-4 items; 60 s 5 items; 3 min+ 7-10
items in two groups with one transition line between groups.

**Voice**: friendly chat or comedic. Music bright.

**Shots**: `numbered-list`, `myth-fix`, `compare-columns`, `big-number`; the opening may use
`feature-grid` to preview all N titles.

**Default style**: `swiss-motion`. Alternatives: game-show feel `game-show`; retro archive
`halftone-dossier`; icon-based and light `pictogram-motion`.

| Symptom | Fix |
|---|---|
| Items of uneven weight | merge or delete the weak ones |
| Items have an order | switch to `tutorial` |
| Each item runs too long | keep a title plus one example |
| No close | add a summary scene, or throw a question to the viewer |

### `story` - Case story

**When**: a real case, event or piece of history: a company's rise and fall, an incident
review, a scientific discovery, a person's journey.

**Versus other explainers**: story tension carries the viewer to the end; the lesson comes
last. The spine is time and cause.

**Think first**

| Element | Ask yourself |
|---|---|
| Protagonist | a person, a company or a team? |
| Want | the protagonist's goal |
| Conflict | what stands in the way |
| Turn | the key decision, discovery or accident |
| Outcome | how it ended |
| Lesson | one sentence that applies elsewhere; this is the thesis |

**Beats**: cold open (the outcome or the most dramatic moment first) -> background (who,
wants what) -> escalating conflict (2-3 scenes) -> turn -> outcome -> lesson, with a callback
to the opening.

**Beat rules**
- Cold-open recipe: one concrete time and one action, showing a corner of the outcome:
  "那一年，他们做出了第一台样机。然后，把它锁进了柜子。" (That year they built the first
  prototype. Then they locked it in a cupboard.)
- A breather scene before the turn: one sentence, almost still.
- The lesson is said once, without preaching.
- The case must be real and sourced; times, numbers and names verified. Never invent
  details or dialogue for real people; quotes only with a source. For the user's internal
  cases, use only what the user gave.
- Storytelling tone: concrete time, place, person and action; many verbs. Sentences get
  shorter as the turn approaches.
- Picture: a `timeline` runs through the film with nodes at true intervals; people and
  places may be stylised illustrations; key moments get a `zoom-detail` or a freeze.

**By length**: 15 s no full story, one moment plus one lesson; 30 s cold open -> turn ->
lesson; 60 s full structure; 3 min+ three acts, or two or three alternating threads.

**Voice**: storytelling. Music calm.

**Shots**: `timeline`, `question-card` as a suspense card, `compare-columns`, `big-number`
showing the cost, `breather-card`, `summary-card`.

**Default style**: `halftone-dossier` (retro archive). Alternatives: warm stories
`watercolor`, `urban-sketch`; ancient and traditional subjects `ink-wash`,
`shadow-puppet`; period feel `silent-film`, `engraving`.

| Symptom | Fix |
|---|---|
| Reads like a diary | find the conflict and the turn; cut everything unrelated |
| Too many lessons | keep one, at the end |
| Invented details | delete, or replace with sourced facts |
| Flat opening | move the outcome or the most dramatic moment to scene 1 |

### `data` - Data story

**When**: a set of numbers tells a change or a conclusion: industry trends, business review,
the conclusion of a survey report.

**Versus other explainers**: numbers are the hero. Each number has a reference and a
meaning, and only credible data or the user's data is used.

**Think first**
- **One conclusion**, written as a judgement; it is the film's title line and its thesis.
- **3-5 numbers** that support it; more and nobody remembers them.
- **A reference for each number**: year over year, period over period, against another, or
  converted into physical things.
- **What it means**: the effect on the viewer, what happens next.

**Beats**: an odd number opens -> state the conclusion -> break it down (composition, trend,
comparison; 3-4 scenes in **one chart space where only the values change**) -> cause ->
what it means, or what comes next.

**Beat rules**
- If the user gave a table or report, use only its numbers; numbers you computed state how.
- Axes from 0; proportions drawn true; time on true intervals.
- One chart says one conclusion, and the conclusion is the chart's title.
- The same data always uses the same chart type; change the style and the viewer thinks the
  topic changed.
- At most one number per sentence: reference before, meaning after. Exact value on screen,
  rounded value in the voice.

**By length**: 15 s one number, one conclusion; 30 s 2-3 numbers; 60 s full structure;
3 min+ 2-3 questions, one set of numbers each.

**Voice**: formal broadcast. Music tech or calm.

**Shots**: `big-number`, `data-chart`, `compare-columns`, `timeline`, `structure-reveal`.

**Default style**: `dataviz`. Alternatives: isometric infographic `iso-infographic`;
brand-grade annual report `swiss-motion`.

| Symptom | Fix |
|---|---|
| Too many numbers | keep the 3-5 that support the conclusion |
| Chart styles keep changing | one chart space per data set; only values change |
| Numbers, no conclusion | every chart's title is its conclusion |
| Numbers without reference | add YoY, a comparison, or a physical conversion |

### `meeting-share` - Meeting record share

**When**: turning meeting minutes, a talk script, a report outline or sharing notes into a
short film for colleagues and managers who were not there. The source is already a
structured document (agenda, section headings, points, conclusions, action items). The job
is to **tell it faithfully**, not to write a new piece.

**Versus `principle` and `story`**: the structure is **set by the source**, not by the
method. Narration **tells** the source, it does not read it: same content, order and facts,
phrased as natural spoken commentary. Facts, numbers, names and decisions never change.

**Think first**

| Element | Ask yourself |
|---|---|
| Source structure | its sections, headings and order: that is the film's structure |
| Source timing | per-section lengths ("about 50 s"), time stamps or a total? If yes, split by them |
| Theme in one line | what the record must leave behind; use the source's own conclusion line if it has one (thesis) |
| Facts | numbers, people, team names, dates, decisions (as given; approximations stay approximate) |
| Ending | the source's conclusion, action items or slogan, as given, last |

**Beats**: title card -> (the source's opening scene or question, if it has one) -> each
section in 1-3 scenes, in source order -> the source's conclusion line alone in the last
scene.

**Beat rules**
- **Source sections are film sections**: follow the source headings (or agenda items) in
  order; never merge, reorder or force another skeleton. Each section is a `chapter-card`
  (major section) or a kicker label (minor section).
- **Keep the source's timing**: split time by section first; each section within 15% of
  its source budget; if all sections exceed the target, shrink them all in proportion, never
  one alone; a chapter card or a new kicker at each section edge; inside a section, split by
  the measured voice. Without source timing, split by content, at least one scene per
  section.
- **Headings go on screen, never into the voice**: write the heading into `visual` (kicker
  or chapter card); `narration` never says "Problems and impact:".
- Opening: if the source has an opening (a scene, a line of dialogue, a question), open with
  it (dialogue may be acted as a `dialog-card`); otherwise title card plus the theme line;
  never invent a scenario.
- Long sources (minutes of several thousand words): **keep every section, choose points
  inside it**: keep each section's conclusion and its key facts, drop the discussion; never
  drop a section.
- **Tell, don't read, but never change content or meaning**: when the source is already a
  script, keep its content and meaning with minimal edits for the ear: split long sentences,
  make it spoken, add links ("But three days later ...", "and delegation is inevitable"),
  one thing per sentence, the conclusion where the stress falls. Add nothing the source
  lacks; drop none of its arguments. Facts, numbers, names, team names, dates, decisions and
  the meaning of the closing line never change; quotes as verified in the source.
- **Strip room talk**: "Hello everyone", "thank you", "let me make three points", "today's
  topic is", "that's all" (大家好, 谢谢, 下面我讲三点, 今天的主题是, 以上就是) are removed if
  present and never added.
- **A hooking first line**: not background, but the most tense sentence, scene or question
  in the source. State the claim in one line ("X is not the key; Y is"), once on the title
  card and once more, rephrased, at the end.
- **A vivid conclusion headline per scene**: each scene shows one big headline of at most
  12 chars (6 words): the scene's conclusion, not the section heading repeated, using the
  source's keywords; it may be a contrast, an equation, a question or a verb phrase
  ("授权，成了甩出去" delegation became dumping; "周报机制 = 控制点" weekly report = control
  point; "让 AI 先筛选" let AI screen first). The storyboard `title` is a shorter label
  (contract limit). A viewer reading only the headlines understands the film.
- **No source sentences on screen**: full sentences go to voice and captions. The screen
  shows the headline, keywords, numbers, role names and short labels; meaning is drawn
  (relations = arrows, quantities = dots, contrasts = two cards, plans = a timeline, scenes =
  acted dialogue). Each `visual` answers "what is this sentence as a thing on screen?"
- **Flow**: one linking line between sections states the cause or turn; never rely on a
  chapter card alone. Inside a section: phenomenon -> cause -> what it means, or what ->
  who -> when.
- **Length cap**: narration at most target seconds x rate x 0.9 (zh 2 min about 480 chars,
  3 min about 720; en about 280 and 420 words). Cut in this order: spoken headings and
  transition words, then the discussion, examples last.
- Quotes of public figures or public data: verify the original and the source; show source
  and year on screen; if you cannot verify, drop the quote.
- **Sounds like a real person**: read the whole narration aloud; rewrite any line a person
  would not say to a colleague face to face. Mix long and short; a sentence is what one
  breath can say. Keep the source speaker's person ("we" stays "we"). A breath before key
  conclusions; a slight pause before numbers and names. Fewer written connectors ("firstly,
  secondly, lastly"), more spoken ones ("but", "so", "at that point", "in other words"). Do
  not repeat a word in two adjacent sentences.
- Voice: a calm formal-broadcast voice, rate +5% to +10%; no melodrama, not like news.

**By length** (when the source gives no timing; source timing wins):

| Length | Plan | Narration cap |
|---|---|---|
| 60 s | title -> 1 scene per section (conclusion only) -> closing line | 240 zh chars / about 140 words |
| 90 s | title -> 1-2 scenes per section -> closing line | 360 / about 210 |
| 2 min | title -> 1-3 scenes per section (a section with numbers gets its own scene) -> closing line; about 12-16 scenes | 480 / about 280 |
| 3 min | as 2 min, one more example sentence per section allowed | 720 / about 420 |

**Shots**: `title-card`, `chapter-card`, `statement`, `big-number` with `dot-count`,
`dialog-card` (when the source has dialogue), role/ownership cards (`compare-columns` with
more columns), `timeline` (plans, milestones), `meter` (ratios, gaps), `summary-card`
(conclusion line plus action items).

**Default style**: `exec-dark`; white paper look `exec-brief`; 3D stage look `3d-stage`.
Recommended mode: `meeting-share`.

| Symptom | Fix |
|---|---|
| Much longer than the source timing | check each section against its budget +-15%; cut discussion and transition words, never facts |
| Sections merged or reordered | restore source sections and order, at least one scene each |
| Headings are spoken | remove them from `narration`; keep them in the kicker or chapter card |
| Source sentences pasted on screen | delete them (voice and captions carry them); use keywords plus shapes (arrows, dots, two cards, meter, timeline, dialogue card) |
| A scene has no headline, or it repeats the heading | write a conclusion headline of at most 12 chars from source keywords |
| Narration sounds read out | short spoken sentences, linking words, conclusion at the stress point; remove room talk |
| Narration changed a fact, number or the closing line | restore the source fact; wording may change, meaning may not |
| A number was rewritten | restore it; "tens of thousands" (上万) never becomes an exact number |
| Quote with wrong year or source | verify; if you cannot, delete the quote |
| No closing line from the source | put the source's conclusion, actions or slogan alone in the last scene |

---

## Ad methods

### `product` - Product introduction

**When**: explaining a product (app, software, enterprise service, hardware) to prospects;
homepage, sales decks, booth loops.

**Versus others**: versus `launch`, clarity and credibility instead of suspense and
ceremony; versus a feed ad, more information (3-4 core capabilities) and a plainer tone;
versus `tutorial`, "what it can do and why it is worth it", not "where to click at each
step".

**Think first**
- **One-line positioning**: for whom, what problem, how it solves it (the promise).
- **3-4 core capabilities**, each translated into a user benefit.
- **Proof**: demos, data, customers, certifications. Only from the user.
- **Who it is for**: one sentence; if needed also who it is not for.
- **One action**.

**Beats**: one concrete trouble of the target user -> the product plus its one-line
positioning -> capability 1 to N (each: scenario -> how it does it (demo) -> result) -> proof
(customers, data, certifications) -> who it is for plus how to start (end card).

**Beat rules**
- Software and apps: `ui-walkthrough` with the user's screenshots; one capability demoed
  over 2-3 consecutive scenes (input -> response -> result).
- Physical products: the product image in a `hero-reveal`, then `detail-push` for selling
  points.
- Demo only features the product really has. Never invent an interface the screenshots
  don't show.
- Plain, concrete, credible; few adjectives; many "can ...", "just ...", "no more ..."
  (能……, 只要……, 不用再……). No superlatives.

**By length**: 30 s positioning, 1-2 capabilities, action; 60 s 3 capabilities; 2-3 min 4
capabilities, proof, plus one scene answering a common doubt.

**Voice**: sincere friendly chat or formal broadcast. Music: tech products tech; enterprise
services calm.

**Shots**: `ui-walkthrough`, `cursor-action`, `input-generate`, `callout`, `before-after`,
`social-proof`, `logo-endcard`.

**Default style**: software and apps `living-screencast`; physical products
`glass-product`. Also common: dark keynote `dark-keynote`; explaining how the product works
`iso-infographic`.

| Symptom | Fix |
|---|---|
| Reads like a feature list | translate every feature into a benefit, each with a demo |
| Each feature rushed in one scene | fewer features, each told fully |
| Demoed a feature the product lacks | delete; use only interfaces in the screenshots |
| Viewer doesn't know who it's for | add a scene: who it is for, how to start |

### `launch` - New product launch

**When**: a product's first appearance: launch event video, pre-launch teaser, debut ad.

**Versus `product`**: ceremonial and suspenseful; the whole film revolves around one hero
reveal. Few but strong selling points.

**Think first**
- **One-line positioning**: product name plus "what it is".
- **2-3 selling points**, written feature -> benefit; the last one weighs most.
- **Proof**: specs, data, test results; only from the user, with test conditions.
- **Price, release date, channels**: only if the user gave them. If not, never write them;
  the end card says "coming soon" (敬请期待) or shows only the product name.

**Beats**: suspense or spectacle hook -> hero reveal (name plus positioning) -> 2-3 selling
points (feature-benefit chain) -> proof -> price and release date -> end card.

**Beat rules**
- A new-category product may use the future-scene storyline: imagine a new way of living ->
  name the product -> the pain disappears -> the result.
- **The hero reveal is the most ceremonial moment**: a mask reveal plus a light push, the
  product 40-50% of frame height, with a "stop" right before it.
- Full-screen impacts 2-3 per film: one at the hero reveal, one at the end card, at most one
  more.
- Short, strong sentences. Three selling points in parallel, short-short-long.
- Brand or product name said at least twice: at the reveal and on the end card.

**By length**: 15 s hook -> hero reveal -> end card; 30 s plus 1-2 selling points; 60 s full
structure.

**Voice**: formal broadcast or sincere friendly chat. Music tech.

**Shots**: `hero-reveal`, `detail-push`, `number-flip`, `callout`, `logo-endcard`.

**Default style**: `dark-keynote`. Alternatives: material feel `glass-product`; young and
trendy `swiss-motion`.

| Symptom | Fix |
|---|---|
| Product shown at once, no suspense | move the reveal later; hold the viewer with a hook first |
| Reveal not ceremonial enough | a stop before it; the film's biggest action at the reveal |
| Too many selling points | keep 2-3; the heaviest last |
| Invented price or date | delete; only what the user gave |

### `feature` - Feature update

**When**: telling existing users about a new feature or a redesign: update videos, in-app
announcements, social teasers.

**Versus `product`**: viewers already know the product; never reintroduce it. The core is
"before vs now".

**Think first**
- **The old trouble**: what users put up with every day; the more concrete the better.
- **The new feature**: its name and where to find it, exactly as in the user's material.
- **The result**: time saved, steps removed; use the user's numbers if given.
- **One action**: "update to the latest version", or "turn it on in Settings".

**Beats**: the old way's trouble (ideally the real interface) -> "now" -> 2-3 step demo of
the new feature -> result -> action.

**Beat rules**
- The key shot is **`before-after`**: the same task done two ways, side by side (portrait:
  top and bottom) or one after the other; the old one greyed.
- Demo with the user's screenshots; never invent an interface.
- Direct and light: "before ..., now ...". Never introduce the product or the company.
- The end card says where to find the feature.

**By length**: 15 s one before/after plus the action; 30 s full structure; 60 s two related
new features, each with its own before/after.

**Voice**: friendly chat. Music bright or tech.

**Shots**: `before-after`, `ui-walkthrough`, `cursor-action`, `number-flip` (time or steps
saved), `logo-endcard`.

**Default style**: `living-screencast`. Alternatives: typographic announcement
`swiss-motion`; icon-based and light `pictogram-motion`.

| Symptom | Fix |
|---|---|
| Reintroduced the whole product | delete; start from the old trouble |
| No before/after | add a scene: the same task, then and now |
| Feature name doesn't match the UI | correct from the screenshots and material |
| Viewer doesn't know where to find it | the end card says where to turn it on |

### `brand` - Brand story

**When**: brand image films, corporate films, city and tourism films, heritage brands,
public-service films.

**Versus others**: sells attitude and identification, not features. Emotion first; little
but precise information; picture and music carry more weight than in other methods.

**Think first**
- **Value proposition**: what the brand believes, one sentence (thesis).
- **Who identifies with it**: the target group.
- **Three proofs**: scenes, people, numbers or history.
- **Slogan**: the user's, unchanged, if given.

**Beats**: a moment the target group nods at -> value proposition -> three proofs
(`anchored-rotation`: brand name or main line fixed, scenes and people rotate beside it) ->
emotional peak -> slogan end card.

**Variants**
- **City and tourism**: open with one unique feeling -> three places or three moments ->
  an invitation ("Come ...").
- **Heritage brand**: follow a timeline: the beginning -> what it has always kept -> today
  -> slogan.

**Beat rules**
- Little narration with weight; much silence. Total words may be 20-30% fewer than other
  methods; leave the time to picture and music.
- Poetic short lines allowed, but each one concrete: a real place, action or object. Never
  "craftsmanship" (匠心), "empower" (赋能), "a century of heritage" (传承百年).
- Rhythm pattern usually slow-slow-fast-stop; breather scenes may be more frequent.
- The last scene holds only the slogan and the brand, still for at least 2 s.

**By length**: 15 s one moment of recognition plus the slogan; 30 s recognition -> value ->
one or two proofs -> slogan; 60 s full structure; 3 min+ a timeline or three chapters, one
proof each.

**Voice**: storytelling or formal broadcast. Music: premium and humanistic calm; young brands
bright.

**Shots**: `anchored-rotation`, `timeline`, `hero-reveal` as a hero picture,
`breather-card`, `logo-endcard`.

**Default style**: `swiss-motion`. Alternatives: humanities and tradition `ink-wash`,
`papercut-red`, `paper-lantern`; city and tourism `urban-sketch`, `silkscreen-poster`,
`tilt-shift`.

| Symptom | Fix |
|---|---|
| All empty phrases | replace each line with a concrete place, person, action or number |
| Too much information | cut to three proofs; give time to picture and music |
| No landing | last scene only slogan and brand, still for at least 2 s |

### `promo` - Promotion

**When**: limited-time campaigns, holiday marketing, opening offers, discounts, pre-sales.

**Versus others**: the core is "offer + deadline + action"; the shorter the better. Prices
and rules must be exact.

**Think first**
- **Offer**: price, discount, gift, exactly as the user gave it.
- **Deadline**: from when to when.
- **How to join**: one action ("scan to get the coupon", "show it in store").
- **Featured products**: one or two.

Prices, discounts, deadlines and rules only as the user gave them, not one character
changed. Nothing invented. No "lowest price", no "best on the whole internet" (最低价,
全网最).

**Beats**: offer hook (price or deadline in the first second) -> product hero -> 1-2 selling
points -> price reveal (original struck) -> deadline plus action.

**Beat rules**
- Holiday campaigns may use holiday elements in the opening and the end card (e.g. Spring
  Festival, Mid-Autumn); how they look is the style's job.
- Must read on mute: offer, price and deadline appear as big text on screen, not only in
  captions.
- Fast, short, punchy; the first sentence states the offer.

**By length**: 15 s offer -> product -> action; 30 s plus selling points and the price
reveal; 60 s two or three products, one price reveal each.

**Voice**: friendly chat or comedic, lively. Music bright.

**Shots**: `price-reveal`, `number-flip` (countdown, discount), `hero-reveal` as product
hero, `logo-endcard`.

**Default style**: `swiss-motion`. Alternatives: holidays `papercut-red`, `paper-lantern`;
young and playful `pictogram-motion`, `microgame`, `rubber-hose`.

| Symptom | Fix |
|---|---|
| Invented offer or deadline | delete; only what the user gave |
| Unclear or several actions | keep one, as verb + object + where |
| Price not prominent | the final price is the biggest text in the scene; original struck beside it |
| Too long a warm-up | the offer in scene 1 |

### `game-drama` - Game ad short drama

**When**: user-acquisition feed ads for web or mobile games: one named hero acts a silly
power-fantasy drama from "wronged -> stronger -> come join me". Many systems (monsters,
merging, star-ups, dungeons, secret realms, quizzes) all touched within 60-75 s. Usually
portrait 9:16.

**Versus other ads**: it does not explain a product; **the hero enjoys the game on the
viewer's behalf**. Every line is the hero shouting, not a narrator presenting. Every
quality, stat, skill and mode is **named, never summarised**, and a sticker pops on screen
for each named word.

**Think first**
- **Hero**: a name plus a look remembered at a glance (e.g. "光头阿强，白背心", bald Ah-Qiang
  in a white vest). Use the user's; if none, take a role from the material and give a
  two- or three-syllable spoken name; keep it all film; the closing line includes it.
- **World**: one sentence (e.g. "a flooded sea wasteland: others live in floating forts, I
  drift on a raft").
- **The grievance**: what others have and I don't; it is the first line.
- **Core loop**: do what -> what drops -> how you get stronger (merge, upgrade, star-up).
  Copy the material's **quality chain** (green -> blue -> purple -> orange ...) and
  **multipliers** (doubles, x2) exactly.
- **Deep challenge**: names of the key, the gate and the boss; the dropped set's name; **at
  most 4 named effects**.
- **Extreme mode**: the name of the realm / endless / wave mode, monsters per wave, rounds
  per boss, minutes per run.
- **The special one**: what this game has and others don't (e.g. a knowledge school where
  answering quiz questions makes you stronger); it is segment 5 and opens with "And the most
  special part is here!" (最特别的来了！).
- **Call to action**: platform (open the web page / download) plus the hero's name.

Names, qualities, numbers, quiz counts and mode rules only from the material, unchanged. No
invented gameplay or rewards. If the material has no numbers, say no numbers.

**Beats** (7 segments, 1-3 scenes each)
1. **Grievance hook** (1 scene, 4-5 s): a contrast line "别人都有 X，凭什么我只能 Y！"
   (Everyone else has X, why do I only get Y?!). The hero's face in the first second.
2. **Core loop** (2-3 scenes, 10-12 s): fight -> gear drops -> merge two into one -> name
   each quality level in turn + "every level doubles your power" -> the star-up chain (the
   material's own words, e.g. "five stars make a moon, five moons make a sun").
3. **Deep challenge** (2 scenes, 12-13 s): collect what -> open which gate -> fight whom ->
   "win and the X set drops" + name each effect, "maxed out".
4. **Extreme mode** (2 scenes, 10-12 s): open with a question "想挑战极限？进 X！" (Want to
   test your limits? Enter X!) -> one rule line (monsters per wave, it gets harder, a boss
   every few rounds) -> "X 分钟一局，巨爽巨解压！" (X minutes a run, pure fun, pure relief!).
5. **Differentiator** (2-3 scenes, 16-20 s): **"最特别的来了！"** -> this game's unique system:
   do what -> get what -> why it beats the earlier power path (e.g. answer questions to
   earn knowledge -> a rainbow sage set crushes the red gear -> a mistake book -> answering
   after a realm doubles the gold).
6. **Two paths** (1 scene, 7-9 s): "主角变强有两条路！A 爆 X，B 拿 Y！" (The hero has two ways
   to get stronger! A drops X, B earns Y!) + one value line.
7. **Call to action** (1 scene, 4-5 s): "打开网页 / 下载，跟着<主角名>出海冒险！" (Open the
   page / download, and sail with <hero>!).

**Beat rules**
- Must read on mute: every segment's key nouns (qualities, set names, mode names, minutes,
  x2) appear as stickers or big text.
- **All short sentences with exclamation marks**, spoken, like the hero shouting at you.
  Never "This is a game that ...", "Players can ...".
- **One word, one picture**: when the voice says "green, blue, purple, orange" the card
  changes colour four times; when it names four skills, four stickers fly in. Write the
  narration in that order.
- Fixed patterns: hook "Everyone else has ..., why do I only ...!"; extreme "Want to test
  your limits? Enter ...!"; differentiator opens "The most special part is here!"; summary
  "... has two ways to get stronger!"; call to action "Open / download, and ... with
  <hero>!".
- No greetings, no "hello everyone", no brand self-description, no "AI-generated" wording.
- Portrait captions are short per line, so one narration sentence of 8-14 Chinese chars
  (about 5-8 words) flows best.

**By length**

| Length | Plan | Narration |
|---|---|---|
| 15 s | grievance hook -> core loop (merge plus one "doubles" line) -> call to action | about 60 zh chars / 35 words |
| 30 s | hook -> core loop -> deep challenge (boss plus one set line) -> call to action | about 120 / 70 |
| 60 s | all 7 segments; differentiator in 2 scenes | 230-260 / 135-150 |
| 75 s | all 7; differentiator 3 scenes; two paths plus the value line | 270-300 / 155-175 |

**Voice**: a comedic, cheeky voice, rate about +10%. Music: high-energy, silly, strong drums,
electronic plus percussion, about 130 BPM, a short sulky minor intro at the hook, no vocals.

**Shots**: `crash-in` or `before-after` as the grievance contrast ("them / me" stickers),
`number-flip` (doubling, kill counts, quiz counts, gold), `statement` for big words,
`compare-columns` for the two paths, `logo-endcard` as the call-to-action button. Mode
`game-drama` gives the 13-scene skeleton.

**Default style**: `live-drama` (photoreal drama stills plus a sticker layer). For a game
look instead of live action, `pixel-rpg`; the sticker naming logic stays the same.

**Taboos** (built-in negative list for any generated stills)
- No cartoonising: never chibi, anime or 3D cartoon faces; always "photoreal drama still".
- No dark horror: abyss, sea king and bosses are "bright, mysterious, blue-violet light";
  no gore, darkness or horror.
- No deformities: one action and one expression per person; no stacked crowds; simple hand
  poses (fist, pointing at camera, sitting cross-legged).
- No gameplay, numbers, rewards or launch dates the game lacks; no competitors; no "best on
  the whole internet".
- Never use the user's game screenshots as the hero picture (they only supply gameplay facts
  and UI layout).

| Symptom | Fix |
|---|---|
| Narration like a product manual ("a browser RPG") | turn it into the hero shouting: "Play right in your browser, no download!" |
| Qualities or skills summarised ("many qualities") | name each one; one pop per name |
| No "the most special part" turn | add it at the start of segment 5 |
| No hero name at the end | the call to action includes the hero's name |
| 75 s over 300 chars | first cut the differentiator's third scene, then the value line; never cut nouns |
| Hero's name changes or is missing | fix one at the start and keep it |
| Cartoon or chibi words in prompts | delete; use "photoreal drama still" |
