# Explainer playbook

Shared craft for the explainer methods: `science`, `principle`, `tutorial`, `listicle`,
`story`, `data`, `meeting-share`. Each method's own structure is in `writing-methods.md`
and wins over this file. Workflow, captions and self-review follow the director's manual
and the contract. Layout, rhythm and motion rules written in a style file win over this
file. English word counts below use chars x 0.58 (contract speech rates 4.5 chars/s vs
2.6 words/s).

---

## 1. Find the teaching spine first

Answer the explainer six questions (audience, gap, thesis, spine, evidence, landing) in
`director.md` section 2. The thesis is a judgement sentence, not a topic. Then reorder,
merge and cut the source: keep the spine, drop the branches. Retelling the source in its
paragraph order is the most common failure.

---

## 2. Pick one structure

| Structure | Fits | How the body moves |
|---|---|---|
| Concept | a concept, phenomenon or term the viewer half-knows | name it -> peel the mechanism layer by layer -> land on "so what" |
| Process | operations or mechanisms with a clear order | 3-6 steps on one visual stage, one step per scene |
| List | parallel points (myths, tips, reasons) | hook -> N items of similar weight -> close; 3 items is strongest |
| Story | a case, a history, a lesson | setup -> conflict -> turn -> outcome -> lesson (the lesson must apply elsewhere) |
| Data | a change shown by a set of numbers | one odd number -> break it down (composition or trend) -> cause -> what it means |

- How to choose: to make a concept understood, Concept; there is an order, Process; items
  are parallel, List; there is a concrete case, Story.
- Structures may nest, but say which inside which ("Concept; the mechanism part as a
  Process"). Never glue paragraphs of several structures together.
- The body almost always runs 3-6 scenes in a row, each advancing one mechanism, step or
  item. A one-scene body explains nothing.

---

## 3. The opening hook (scene 1, catches the viewer in 3 s)

A hook opens a knowledge gap, creates tension, or names the stakes. Never open with a
definition ("X refers to ..."), a source subheading, or "Hello everyone, today let's talk
about ..." (大家好，今天聊聊).

| Hook type | Use when | Example (Chinese, with gloss) |
|---|---|---|
| Shock number | a credible number shows the stakes | 人类造过的塑料，九成从来没被回收过。(Nine tenths of all plastic ever made has never been recycled.) |
| Question | to open the gap directly | 为什么年纪越大，觉得时间过得越快？(Why does time feel faster as we get older?) |
| Counter-intuitive | the truth is the opposite of common sense | 多修一条车道，堵车反而更严重。(Add one more lane, and traffic gets worse.) |
| Named confusion | the viewer already has this confusion | 人人都说要分散投资，可没人说清怎么分。(Everyone says diversify; nobody says how.) |
| Concrete metaphor | the concept is too abstract | 你的注意力是一盏聚光灯，每个 App 都在抢开关。(Your attention is a spotlight, and every app fights for the switch.) |
| Name the concept | the term itself is the hero and must be remembered | 这种现象有个名字，叫旁观者效应。(This has a name: the bystander effect.) |
| Call out the audience | the audience is very specific | 做到一半就放弃的菜谱，你收藏了几份？(How many recipes have you saved and given up halfway?) |
| Thought experiment | one scenario can carry the whole film | 想象一种钱，不花掉就会贬值。(Imagine money that loses value if you don't spend it.) |
| Stakes | the cost of getting it wrong is concrete | 这一步错了，整锅都得倒掉。(Get this step wrong and the whole pot goes in the bin.) |

- Hook narration: 15-25 Chinese chars (about 9-14 English words).
- Something is on screen at frame 0. Within 1.5 s the hook keyword is both spoken and on
  screen. Full opening timetable: `director.md` section 5.
- **The thesis is stated by scene 2 at the latest.** Every later scene is its evidence.
- The hook offers the understanding the viewer will get ("oh, that's why"), not the source's
  list of terms.
- Self-check: delete every evidence scene, the rest still states the thesis; delete the
  thesis scene, the film falls apart.

---

## 4. Ordering concepts, analogies, examples, the callback

**Ordering concepts**
- From what the viewer knows to what they don't; the simple case before the general case.
- One new concept per scene. Explain a new word the first time it appears; then use the
  same name for it all film.
- Frame before content: first "the whole process has three steps", then one step per scene.
- Signpost words link the scenes: "first ... next ... finally", "the problem is ...",
  "that's why ...".
- Body scenes share **one visual stage**: the same diagram grows, the same axis moves on,
  the same container changes, so several scenes read like one continuous shot.

**Analogies**
- Take them from the audience's daily life (expense claims, parcels, cooking, queues). An
  analogy must not be harder than the concept.
- State two or three correspondences explicitly: "retrieval is like reading the expense
  policy first; generation is like writing the reply by that policy".
- If the analogy has a limit, say "the analogy stops working here" so it does not mislead.
- The analogy image runs through the film: the image used at the opening comes back at the
  end. One main analogy per film.

**Examples**
- A worked example with real numbers beats an abstract description: "principal 10,000,
  5% a year, after 10 years it is 16,289".
- Examples must belong to this topic. If a prop could move unchanged into another film and
  still make sense, it did not come from the material; replace it.
- Facts and numbers have sources (list them in `brief.md`); computed numbers state how they
  were computed; if the user gave data, use only the user's data.

**Summary and callback (last scene)**
- Go back to the opening question or image and give the answer.
- Compress the thesis into one sentence, plus at most three points. No new content in the
  last scene.
- The explainer's call to action is a usable way to judge or a next step: "Next time you
  see X, first ask Y."

---

## 5. Give each scene one explaining technique

Name a concrete technique for every scene in `visual`; never just "explain it".

| Category | Techniques |
|---|---|
| Make it concrete | analogy; turn the abstract into an object; worked example with real numbers; anchor to a familiar reference ("about 3 football fields") |
| Reveal in order | one layer at a time; simple to general; signpost words |
| Contrast | before/after; common belief vs fact; two options side by side; counter-example ("here it fails") |
| Structure | rule of three; numbered list; question and answer; frame first, then fill |
| Evidence | data; source; show the mechanism running; cause chain (A -> B -> C) |
| Memory hook | callback to the opening; one-line summary; a catchy name; from one case to a principle |

Emotional arc: the opening opens a gap (curiosity, confusion, surprise) -> naming the point
orients (clarity, expectation) -> the body builds understanding ("oh, that's it",
conviction) -> the end lands (sudden clarity, satisfaction).

---

## 6. Plan scenes by length

Budget: narration ~ rate x (length - 0.4 s x scenes), with the contract's rate and padding.
The counts below are the upper end; land a little under, then let TTS measure.

| Length | Scenes | Narration zh chars | en words | Structure |
|---|---|---|---|---|
| 15 s | 2-3 | about 62-64 | about 36 | hook (with the thesis) -> one picture explains one point -> one-line conclusion; no multi-step body |
| 30 s | 3-5 | about 126-130 | about 73-75 | hook -> thesis -> 1 mechanism or 2 points -> summary |
| 60 s | 5-8 | about 255-260 | about 148-150 | hook -> thesis -> 3-4 body scenes on one stage -> example or data -> summary |
| 3 min | 18-24 | about 765-780 | about 440-450 | hook plus roadmap ("three things today") -> 2-3 parts of 4-6 scenes, each part opens with a small question and closes with a one-line mini-summary -> recap plus callback |

- Scene lengths: hook 4-6 s, body 6-10 s, summary 5-8 s. Never over 12 s; split instead.
- Inside a scene, a new event every 2-4 s: a new step appears, an emphasis, a number lands.
- After voicing, if the total is more than 10% off the target, rewrite the narration; never
  speed up the animation.
- A breather scene every 60-90 s: little content, almost still, one sentence; often right
  before a turn.
- Before writing, pick one or two reference works (science channels, title sequences,
  infographic animations) and note what to learn (structure, rhythm, shot grammar) and what
  not to take (characters, designs, specific shots).

---

## 7. Narration

**Length and rhythm**
- Per scene 1-3 sentences, 20-50 Chinese chars in total (about 12-29 English words). One
  sentence at most 25 chars (about 14 words). A caption line breaks at punctuation or the
  contract's line length, so keep each comma-piece within one line: one short clause =
  one caption line.
- Alternate short and long sentences: short ones judge, long ones explain. One thing per
  sentence.
- **Write in cue points**: each clause names one visual element, and it appears when spoken.
  "先查资料，再写回答，最后标上出处" (first look it up, then write the answer, finally add
  the sources) = three clauses = three entry times.
- The last clause of a scene introduces no new on-screen text; it only stresses or sums up.

**Spoken style**
- Write for the ear: "you", verbs; no long chains of modifiers (Chinese: avoid strings of
  的); no officialese (进行, 实现, 对……加以; no 之, 其, 该).
- Important information at the end of the sentence, where the stress falls:
  "最后决定答案质量的，是检索" (what finally decides the answer's quality is retrieval) is
  stronger than "检索决定了最后答案的质量" (retrieval decides the final answer's quality).
  English end-focus works the same way.
- Avoid homophones that are easy to mishear. A technical word is followed at once by a
  plain-words line the first time.
- No empty words: 赋能 (empower), 一站式 (one-stop), 全方位 (all-round), 深度解析 (in-depth
  analysis), 干货满满 (packed with value).
- Read it aloud once; fix what is awkward.
- Voice: popular science = friendly chat; serious topics and data = formal broadcast;
  stories = storytelling.

**Numbers and symbols** (TTS reads literally, and captions show the same text)

| Source | Write in narration as |
|---|---|
| 3:2, 1/3 | 三比二, 三分之一 (three to two, one third) |
| 5-8 个 | 5 到 8 个 (5 to 8) |
| 10x | 10 倍 (10 times) |
| 10/14, 8:00 | 10 月 14 日, 8 点 (October 14, 8 o'clock) |
| 12,345 人 | 一万两千多人 (over twelve thousand); the screen shows the exact number |
| rare English abbreviation | add a plain explanation the first time |

- Years, integers, percentages and decimals may stay as digits.
- At most one number per sentence. A reference before it, its meaning after it:
  "5%，也就是每 20 个里有 1 个" (5%, that is 1 in every 20).
- The screen may show the exact value; the voice says the rounded one.

**Pauses.** Where the viewer needs to digest, end the sentence before the key conclusion or
number, at most one or two per scene. Never write pause markup into `narration`.

**Verbatim mode.** If the user wants their text read as is, keep every word; only split it
into scenes by sentence. The rewriting rules above do not apply.

---

## 8. How the picture explains

**Growing the picture**
- The picture grows with the narration: at first only what is being said; the rest appears
  when spoken (about 0.1 s before the keyword, from `words.json`).
- At least one key reveal in the second half of the scene. Everything on screen in the first
  25% and then holding still is the typical "slide deck" look.
- A complex diagram first appears as a ghost (about 20% opacity) so the viewer sees the
  whole, then each part "inks" to solid colour as it is spoken.
- Parts already explained step back (fade or grey); the current one stays emphasised. At
  the end, restore everything and hold the full picture at least 1 s.
- When an element really turns into another (a node into a bar, a formula into a result),
  do it inside one scene; never cut.

**Emphasis**
- One emphasis at a time. The accent colour only for "being said" and "to remember".
- One or two emphasis devices at once: colour, bold, underline, circle, scale up 5-10%.
- A key number lands when spoken, then stays unchanged at least 0.5 s.

**On-screen text density**
- Captions show the whole sentence, so the screen **never repeats a full sentence**: only
  keywords, terms, numbers and labels.
- Text to read per screen (captions excluded): about 25 Chinese chars (about 12 words).
  Labels at most 6 chars (3 words). Picture headline at most 12 chars (6 words). The
  storyboard `title` at most 10 chars (5 words).
- Each text block stays visible at least Chinese chars / 4.5 + other chars / 15 + 1.5 s.
  New text appears no later than about 3 s before the scene ends. To go faster, show less
  text; never cut it away early.
- 3-6 main elements per scene; the main graphic 30-50% of frame height.

**Data graphics**
- A number gets a weighted shape (proportion bar, ring, area, a count of real objects);
  never a lone number in empty space.
- No pie charts, no dual axes, no six-panel dashboards. At most 3 metrics side by side. Few
  grid lines and legends; label directly next to the data.
- Several numbers about one concept live in the same visual space; only the values change.
  Change the chart style and the viewer thinks the topic changed.
- Axes start at 0; time on true intervals; proportions drawn true. The label explaining a
  number appears within 0.5 s of the number landing.

---

## 9. Explainer shot types

Use the explainer family in `shot-types.md` (`question-card`, `big-number`, `term-card`,
`analogy-map`, `flowchart`, `structure-reveal`, `cause-chain`, `timeline`,
`compare-columns`, `data-chart`, `formula-breakdown`, `zoom-detail`, `myth-fix`,
`numbered-list`, `breather-card`, `summary-card`).

- At least three different layouts across the film (centred, left/right split, top/bottom,
  full-width band, triptych); adjacent scenes never share a layout.
- Portrait: turn left/right splits and triptychs into vertical stacks; never copy the
  landscape split.

---

## 10. Symptoms and fixes

| Symptom | Cause | Fix |
|---|---|---|
| Sounds like reading an article | follows the source's paragraph order | back to section 1: reorder by thesis and spine, cut branches |
| Nobody wants to watch the opening | opens with a definition or greeting | use a hook from section 3; state the thesis in scene 2 |
| The body is not understood | two or three new concepts in one scene, or a one-scene body | one concept per scene; split the body into 3-6 scenes on one stage |
| Looks like a slide deck | everything shown at once, then still | reveal with the narration; at least one reveal in the second half |
| Screen full of text | narration copied onto the picture | keep keywords and numbers; leave sentences to the captions |
| Text flashes and is gone | last text too close to the scene end | work back from the hold-time formula; the last clause adds no new text |
| The analogy is harder than the concept | analogy too far from the audience, or the mapping not stated | use an everyday analogy; state each correspondence and its limit |
| Numbers are not remembered | several numbers per sentence, or no reference | one number per sentence, reference before, meaning after, plus a shape |
| Every scene looks the same | repeated layout template | at least three layouts, adjacent scenes differ; vary camera moves too (push, pull, pan, follow along an axis, zoom into a detail) |
| No climax | every scene has the same action strength | make the scene that states the thesis or key mechanism the biggest action of the film (the whole diagram inks, the structure opens, the formula produces the result); slow down the scene before it |
| Flat rhythm | every scene has the same information load and action | add a breather; concentrate information in the climax scene; slow the scene before it |
| The end does not land | the last scene still adds new content | the last scene only calls back and states the thesis in one line |
| Too long | narration estimated by written reading speed | recompute with the contract's rate and padding; cut words, do not speed up |
