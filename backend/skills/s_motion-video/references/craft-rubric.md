# Craft rubric and self-review

Before delivery you review the rendered cut yourself, from evidence, in two layers:

1. **Defect review** - big problems a viewer notices at once. Must be fixed if you caused
   them.
2. **Craft score** - 12 items (K1-K9 picture craft, C1-C3 content), each 0 / 0.5 / 1.
   **Pass = mean of the scored items >= 0.75 and no item at 0.**

Write every scene against this table; review against the same table.

---

## 1. Evidence

Look only at evidence taken from **this** cut (made by `scripts/review_evidence.py` into
`review/`). Images from an older render are stale: never judge from them.

| Evidence | What it is for |
|---|---|
| Contact sheet per scene: 4 x 3 cells spread evenly over the scene, each labelled like `S03 t=4.2s` | motion, entrances, focus, reveal order, hold frames (K1-K4, C1-C3) |
| One full-resolution middle frame per scene | text legibility (sheets are downscaled) |
| Seam strip per boundary: 3 frames before and 3 after | seams (K7), empty frames, jumps |
| Film strip: the middle frame of every scene in a row | shot-size variety (K5), whole-film consistency (K6) |
| `review/defects.json` (measured) | flashes, jumps, still runs, motion mean and a 12-bin motion curve per scene, seam differences, frame-count match |
| `review/loudness.json` (measured) | integrated loudness, true peak, narration gaps |
| `storyboard.json`, `design.md`, `words.json` | what each scene must show, when, and the design contract |

Rules:
- **Measured numbers beat visual impressions.** If a metric and your eye disagree, trust the
  metric.
- **You cannot hear.** Judge sound only from `loudness.json` and the caption timings: the
  contract's loudness and true-peak targets, and no narration gap longer than 1.5 s.
  Captions come from TTS word timings; they are checked by tools, not by eye.
- A missing or empty measurement is **unknown, never clean**. Regenerate the evidence before
  scoring that item.

---

## 2. Defect review

Report only big problems: the kind a user sees and says "this film is broken". At most
**6 issues** per cut, ordered by severity. Severity is `high` (cannot ship) or `mid`
(clearly visible but tolerable); do not report `low`. No taste notes: no palette,
composition, brush stroke or "could be more refined" comments.

**Six defect classes**

| Class | Rules | What counts |
|---|---|---|
| layout | L1 legible text; L2 no overlap, overflow or out-of-frame; L3 clear subject that matches the narration | text too small, low contrast, cut by the frame edge, covered by another element, in the caption band, or not on screen long enough to read; main element cropped; frame empty or cluttered |
| content | P1 the picture matches what is said | something spoken does not appear; picture contradicts narration or storyboard; required information missing; scenes contradict each other; the whole film is clearly not the chosen style (report once, as `style`) |
| motion | M1 every move eased and at least 0.25 s, no one-frame jump, no full freeze over 2 s (end-card hold excepted); M2 entrance, transition and hold timing neither rushed nor stiff | a flash (any is a defect); jumps (4 or more in one scene; stepped styles such as pixel art are exempt); a still run over 2 s; motion too fast to follow |
| assets | E1 everything required is present | a storyboard image, icon, sound effect or music is missing or replaced by a placeholder block; images the user supplied for a scene are unused |
| request | R1 explicit requests met; R2 accurate | something the user explicitly asked for is missing, or something they refused is there; an edit broke something else; **an approximate number turned into an exact one** ("tens of thousands" shown as 9,325), or a number, name or data point the user never gave: always `high` |
| audio | A1 (measured only) | loudness or true peak off target; a narration gap over 1.5 s; captions out of sync with the voice per the word timings |

**Cause** (decides who fixes it):
- `agent` - the request and rules were clear and you did not deliver. **You fix it.**
- `prompt` - the request was unclear or ambiguous, and you made a reasonable choice.
  **Report it to the user; do not "fix" it.**
- `style` - the chosen style or the skill cannot do it. **Report it to the user.**

Fix only what you caused: every `agent` + `high` issue; `agent` + `mid` when cheap. Things the
user explicitly asked for are constraints of this cut, not defects: never flag them as
off-style, extra or off-topic; judge only how well they are done (position, clarity, whether
they cover other elements, whether done the way the user said).

Each issue names the scene and seconds, and gives a fix you can apply directly ("make S03's
title 1.5x larger"), never "improve it". Do not invent problems you cannot see; if you can't
tell, don't write it.

---

## 3. Craft score

Score units are fixed **in advance** from the storyboard: the content anchors (entities,
numbers, relations in the narration), each multi-step explanation, each key text block
(title, key number, conclusion), each seam. You may not drop a unit afterwards because it
fails. An item is N/A only if it has no units (e.g. no multi-step explanation -> C2 N/A);
the mean counts only scored items, and the review states how many were scored.

| # | Checks | 0 | 0.5 | 1 |
|---|---|---|---|---|
| K1 | Continuous motion | a stretch of at least 1.0 s where the motion curve is below 15% of the scene median | every stretch moves, but with only one kind of motion (e.g. only a slow push) | every stretch moves, with at least two kinds of motion |
| K2 | Layered entrances | in the first 40% of cells, elements are either absent or already settled; the curve is spike-zero-spike | entrances are caught in only one cell, or entrances run one after another without overlap | at least 2 cells catch elements entering, and entrances overlap |
| K3 | One focus at a time | two elements compete, the accent colour is used in several places, or more than 6 main elements | one focus most of the time, but in some cell a secondary element competes | one clear focus per cell; accent colour only on the focus |
| K4 | Full, balanced hold frame | in the last cells half the frame or a quadrant is empty, or the subject is under 25% of frame height | subject 25-30% of height, or the frame leans clearly to one side | content fills the middle of the safe area; subject about 30-50% of frame height |
| K5 | Shot-size and camera variety (whole film) | every scene in the film strip has the same shot size and layout | 2 shot sizes or 1 kind of camera move, or adjacent scenes repeat once | at least 3 shot sizes and at least 2 kinds of camera move; no adjacent repeat |
| K6 | Whole-film consistency | a scene's background, type scale, caption box or character colours differ from the others | one small drift in one scene (one type step, a slightly different tint) | every scene matches `design.md` |
| K7 | Seams | two unrelated compositions hard-cut, with a jump or empty frames; or a `fade` in a style that forbids generic transitions | no empty frame, but the composition jumps at a `cut` | compositions connect across each `cut`, or the style's own join is used |
| K8 | Opening and close | the first 1.5 s of S01 show only background or a title; or the last scene has no closing frame | the subject enters after 1.0 s but before 1.5 s; or the closing frame holds under 1.5 s | the subject enters and moves within 0.3-1.0 s; the end (summary, call to action, or callback to the opening) holds at least 1.5 s |
| K9 | One climax (signature shot) | per-scene motion means are nearly equal (coefficient of variation under 0.15); no picture stands out | a peak exists but not at the key point, or under 1 s, or not native to the style | at the key point, one biggest, medium-native action of the film (a shell opening, a page folding, ink spreading), lasting at least 1 s |
| C1 | Content anchors map to visual objects | entities, numbers or relations in the narration have no visual object; the sentence is just repeated as text | most anchors have objects, but relations appear only as text | every anchor has an object inside its window; relations are encoded by lines, order, position or charts |
| C2 | Stepwise reveal | an explanation of 2+ steps is fully visible from the first frame | revealed in steps, but earlier steps disappear, or two steps arrive at once | middle cells keep the explained parts and add only the current step |
| C3 | Text hold window | a title, key number or conclusion is covered or cut away right after it appears: its unblocked window is below max(1 s, Chinese chars / 5 + other chars / 15) | the window is above that floor but below the design hold (Chinese chars / 4.5 + other chars / 15 + 1.5 s) | every key text block has an unblocked window of at least the design hold |

**Evidence rule.** Any score below 1 must cite evidence: a contact-sheet cell label
(`S03 t=4.2s`), a seam-strip frame, or a measured value (motion curve bin, still run, jump
count, coefficient of variation, a timing from `words.json`). **A score below 1 without
evidence counts as 1.** This stops invented faults.

Each score below 1 also gets one edit you can apply in that scene's own file (scene id,
element, what to change). If you cannot write a concrete edit, write none.

**Fix templates** (fill the angle brackets):
- K1: "S<NN> a-b s barely moves: give <element> a <5% slow push / drift / counter> running
  through that stretch; change nothing else."
- K2: "S<NN> a-b s arrives in batches and stops: move the start of <B, C> to 0.2 s before
  <A> ends, so entrances overlap."
- K3: "S<NN> t=x s <A> and <B> compete: make <B> neutral and 60% of <A>'s size, or delay it
  until <A> has been explained."
- K4: "S<NN> end: <the right half> is empty: scale <subject> to about 40% of frame height and
  move it <x> px toward the centre, or put <a number column> in the empty space."
- K5: "S<NN> and S<MM> are both <wide, centred>: make S<NN> a <close-up>, pushing in on
  <subject>."
- K6: "S<NN> <title size / background> differs from the rest: restore <the value in
  design.md>."
- K7: "S<NN> -> S<MM> hard-cuts between unrelated compositions: switch the join to <fade /
  the style's join>, or end S<NN>'s camera on S<MM>'s opening composition."
- K8: "S01's first 1.5 s show only <background>: bring <the core object> in at 0.3 s; delay
  the title to 1.5 s."
- K9: "No climax: make S<NN>'s main action (where <the key point> is said) the biggest of the
  film, a <shell opening / page fold>, lasting at least 1 s; slow S<NN-1> down."
- C1: "S<NN> says <entity/number/relation> but shows no object: add <shape/arrow/bar> that
  appears at <keyword time - 0.1 s>."
- C2: "S<NN> shows all <n> steps at t=0: ghost them at 20%, ink step k at its keyword, keep
  earlier steps visible."
- C3: "S<NN> <text> is covered at t=x s: delay <the covering element> to <time>, or remove
  the text earlier in the narration."

---

## 4. The review loop

1. Read `defects.json` and `loudness.json` first. Every flash, still run over 2 s, jump
   cluster and loudness miss is a defect before you look at a single image.
2. Run the defect review and the craft score in one pass (prompt in section 5).
3. **Defect round**: fix every `agent` + `high` issue. After each fix, re-check the touched
   scene (`scripts/check.py`) and confirm on its new stills that the issue is gone before
   the next fix. Then re-render, re-mix, regenerate the evidence, and review again. In that
   review, every issue from the last round that is gone is `fixed`; one still there is
   listed again with the same scene and class and counts as `left`.
4. **Craft round** (only when no `agent` + `high` defect is left, and the craft score fails
   or the user asked for a polish): up to **3 edits**, taken from the lowest-scoring items,
   touching **at most 3 scenes**, each inside that scene's own file. Do not edit
   `scenes/shared.css` (it re-renders the whole film) unless the item scored 0. Do not argue
   with the score: the cut is the truth, not your memory of the code. Re-check, re-render,
   re-mix, review once more.
5. **Regression rule**: if the craft round produced more `agent` + `high` defects, or a lower
   craft mean, deliver the previous cut.
6. Limits: one defect round and one craft round per turn (plus the verifying review).
   Reaching the limit is not acceptance: if defects remain or the score fails, say so in the
   summary in plain words.
7. **Declined issues**: an issue that legitimately cannot change (the user asked for it; the
   storyboard cannot do it) is recorded in `review/review.md` with a one-sentence reason and
   not raised again unless it got clearly worse. Give the reason in the summary. Never
   decline an issue you can fix.
8. **Telling the user**: say what was found, what was fixed and what was left (and why: an
   unclear request, a style limit). Never mention scores, round counts or rule ids.

---

## 5. Self-review prompt (use it when you look at the evidence)

Read this to yourself before opening the images, then follow it literally.

```
I am the QA reviewer of this cut, not its author. I judge only what is visible in the
evidence images and what the measured files say. Knowing the code does not count: if a
frame shows it, it is there; if no frame shows it, it is not.

Inputs I have: storyboard.json (narration, visual, cue points, durations), design.md,
words.json timings, review/defects.json and review/loudness.json (measured), and in
review/: a 4x3 contact sheet per scene with cells labelled "Sxx t=s.s s", one
full-resolution middle frame per scene, a seam strip per boundary, and a film strip.
If any of these is missing or from an older render, I regenerate it first. Unknown is not
a pass.

Step 1. Measured facts. From defects.json and loudness.json list: flashes (scene, time),
jump counts per scene, still runs over 2 s, per-scene motion means and their coefficient
of variation, curve bins below 15% of the scene median, loudness, true peak, narration
gaps over 1.5 s, frame-count mismatch. These are facts; images do not overrule them.

Step 2. Fix the score units from the storyboard before looking: anchors (entities, numbers,
relations), multi-step explanations, key text blocks, seams. Write them down.

Step 3. Per scene, open the contact sheet, then the full-resolution frame. Check, cell by
cell, with the cell label:
  - text: readable size and contrast, inside the frame, not covered, not in the caption band;
  - main elements not cropped by the edge; no blank, black or broken frame;
  - each spoken anchor has its object in its time window (use words.json);
  - entrances: caught mid-move, overlapping; reveal order keeps earlier steps;
  - one focus per cell, accent only on it; at most 6 main elements;
  - the hold frame: subject 30-50% of height, no empty half or quadrant;
  - key text: its unblocked window against the floor and the design hold.

Step 4. Seams: for each boundary strip, look for empty frames, jumps, unrelated compositions,
captions doubling during a fade.

Step 5. Film strip: shot sizes and layouts across scenes; same background, type scale,
caption box and colours as design.md; where is the biggest action, and is it at the key
point.

Step 6. Opening and close: S01 cells before 1.5 s; the last scene's closing hold.

Step 7. Write the defects: only the six classes, at most 6, high or mid, each with scene,
seconds, class, rule, cause (agent / prompt / style), problem, direct fix. User-requested
elements are constraints, not defects. An approximate number shown as an exact one, or any
number or name the user never gave, is content-request / high.

Step 8. Score K1-K9 and C1-C3 as 0, 0.5 or 1. Below 1 needs a cell label or a measured
value, else it is 1. Below 1 gets one single-scene edit from the fix templates.

Step 9. Compute the mean of scored items and list zeros. Pass = mean >= 0.75 and no zero.
Decide: fix (agent/high defects exist) / craft (no such defect, score fails or polish
asked) / done.
```

**Write the result to `review/review.md`** in this shape:

```
# Review - cut <n>
Summary: <one line: the main problem of this cut, or "no visible problems">

## Measured
flashes: ... | jumps: ... | still > 2 s: ... | motion CV: ... | LUFS: ... | TP: ... | gaps > 1.5 s: ...

## Defects (max 6)
| # | scene | time | class | rule | cause | severity | problem | fix |

## Craft
| id | score | evidence | scene | edit |
(all 12 rows; N/A rows say why)
Mean: x.xx over n items | zeros: ... | pass: yes/no

## Previous round
fixed: ... | left: ...

## Declined
| issue | reason |

Decision: fix | craft | done
```

---

## 6. Quick scene check (sample scene and every re-check)

When you look at the stills `scripts/check.py` takes of one scene (first, middle, last
frame, labelled with their time), report only must-fix problems:
1. text unreadable, cut by the frame edge, or overflowing its box;
2. a main element cut by the frame edge;
3. text and graphics covering each other, or anything in the caption band;
4. a blank, black or visibly broken frame (broken image, misplaced element);
5. a clear mismatch with the scene's `visual` (a main element missing or wrong).
Composition, colour or style that "could be better" is not a problem here. If all is fine,
the answer is "pass"; otherwise one line per problem: cell (t=...), position, element,
what is wrong.
