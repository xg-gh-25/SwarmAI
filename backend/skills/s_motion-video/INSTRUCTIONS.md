# Motion Video — workflow

You are the director. The person gives a description (and maybe references, images, links);
you deliver `out/<slug>.mp4` plus a short summary. This file is the order of work. The craft is in:

| Need | Read |
|---|---|
| Files, storyboard fields, the fixed numbers | `references/project-contract.md` (single source for every number) |
| How to direct: thesis, hooks, timing, text, reveal, motion, end card, copy | `references/director.md` |
| Genre playbook | `references/explainer-playbook.md` or `references/ad-playbook.md` |
| Script structure | `references/writing-methods.md` (pick one method) |
| How the film is made | `references/production-modes.md` (pick one mode) |
| Shot types and their parameters | `references/shot-types.md` |
| The look | `references/styles/INDEX.md`, then `references/styles/<id>.md` + `<id>.css` |
| Writing a scene page | `references/scene-contract.md`, example `templates/scene.html` |
| The parts every scene shares (plate, mask, vignette, kicker, progress bar, push) | `references/scene-contract.md` § The film layer, `templates/film.json`, `scripts/film.py` |
| Briefing parallel scene writers | `templates/scene-writer-brief.md` |
| A new cut or version of an earlier film | § Revise or extend a film, `scripts/derive.py` |
| Judging the cut | `references/craft-rubric.md` |
| A new style from a reference video | `references/stylegen.md` |

**Three axes, and who wins a conflict.** The writing method decides structure, shot order and
narration. The style decides layout, palette, motion and in-shot rhythm. The mode decides
asset sources, editing and transitions. Never change the writing method because of the style.
A style defines only the look: if a technique is not described for HTML, build it yourself
from the style's rules; do not switch style or lower the bar.

**How this manual is written.** *Contract* = rules a script checks (follow them). *Budget* =
counted limits (stop when used up). *Goal* = intent and named anti-patterns (use judgement).

---

## 0. Intake (no tool calls that cost time yet)

1. Read the description and every reference the person gave. If the topic belongs to one of
   the person's projects (a DDD or knowledge folder exists), search it first:
   `"$SWARM_BACKEND_BIN" cognition find --project <P> --query "<topic words>"`. Their own
   strategy and evidence beat general knowledge.
2. **Ask at most once, and only before costly work**, only when: the topic is missing or
   unclear; two very different approaches fit and nothing points to one; or what they want
   cannot be done and they must pick an alternative. One sentence, 2–4 options, one marked
   recommended. Never ask about length, style, tone, voice or music: they have defaults.
3. Defaults: explainer 60–90 s or ad 15/30 s; 1920×1080, 30 fps (portrait 1080×1920 for
   phone feeds); narration in the person's language; captions on; music `calm` for
   explainers, `bright` for ads (moods: `bgm.py --help`); style picked from `INDEX.md`
   by audience and tone. Say in one line what you chose.

Create the project: `P="${SWARM_WORKSPACE:-$HOME/.swarm-ai/SwarmWS}/Services/motion-video/projects/<yyyy-mm-dd>-<slug>"`.

## 1. Research (when the film states facts)

Account for every reference: used or not, and why (`brief.md` § Sources). If facts come from
the web, keep the URLs. Facts, numbers and names are copied exactly; an approximate number
stays approximate ("上万" never becomes "9,325").

## 2. Brief — `brief.md`

Answer the six questions in `director.md` (audience, the one thing they must remember, the
thesis as a judgement not a topic, why now, proof, what they do after). Choose writing method,
mode, style, length, format, voice, music; mark each as *asked* or *decided by me*.

As soon as `brief.md` is written, register the project folder as a Pollinate work, so the
person sees the film in the Pollinate overlay while it is being made (its stage, brief →
storyboard → scenes → film, is read from the project's files; title and size from
`storyboard.json` once it exists):

```
"$SWARM_BACKEND_BIN" py "${SWARM_WORKSPACE:-$HOME/.swarm-ai/SwarmWS}/.claude/skills/s_pollinate/scripts/work.py" adopt --dir <project> --kind video
```

A project that already has a `work.json` is registered: skip it (adopt exits 2 and says so).
Pollinate only lists the film; this skill keeps making and revising it.

## 3. Script and storyboard — `script.md`, `storyboard.json`

- Read the chosen method in `writing-methods.md` plus its playbook. The method wins over the playbook.
- Write narration as cue points: one clause per visual element, so the picture can show each
  thing when the voice names it. Opening rules and hook types: `director.md`.
- Write `storyboard.json` per `schemas/storyboard.schema.json`; set `target_seconds`.
  Estimate length (zh 4.5 chars/s, en ~2.6 words/s); over target by +10 % → cut, +20 % → must cut.
- Confirm the storyboard with the person only if they asked for it or the film is longer
  than 2 minutes; otherwise show a 3-line summary (thesis, scene count, length) and continue.

## 4. Voice first — durations come from the voice

```bash
python3 "$SKILL_DIR/scripts/tts.py"  --project "$P"           # writes audio/voice/* and scene durations
python3 "$SKILL_DIR/scripts/film.py" --project "$P"           # film data for the shared layer: run after EVERY tts.py
```
For Chinese, put polyphones, English terms and brand names in `$P/pronounce.json` before running.
Scenes without narration get a hand-set `duration`. `film.py` writes `scenes/film-data.js` (film length,
scene starts, chapter marks, kickers) from the storyboard and `film.json`; a scene whose duration no longer
matches it fails `check.py` with a message naming `film.py`.

## 5. Sample scene, then the design contract

1. Write the most representative scene (usually S01 or the hero scene) per `scene-contract.md`
   and the style file. Use `MV.cue("keyword")` for every element the narration names.
2. `python3 "$SKILL_DIR/scripts/check.py" --project "$P" --scene S01` and
   `python3 "$SKILL_DIR/scripts/render.py" --project "$P" --scene S01 --preview`; read the 3
   stills. **Budget: at most 3 checks per scene**, then record the problem and move on.
3. When it passes, write `design.md` (≤ 30 lines): palette, type scale, caption look, shot
   grammar, how scenes join, shared blocks in `scenes/shared.css`. Write `film.json` (from
   `templates/film.json`): the shared parts the style asks for (exec-dark: plate, mask, vignette,
   kicker, progress), then re-run `film.py`. Never rebuild these parts in a scene or a project
   script: every scene calls `FL.setup(tl, opts)`. Freeze `shared.css` and `film.json` now.

## 6. All other scenes

- Write each scene following `design.md` and `scene-contract.md` § Writing rules. For more than 6
  scenes, fill `templates/scene-writer-brief.md` once (project and skill paths, style, the shared
  files, 3–9 finished example scenes covering the film's layouts, this film's own rules), save it in
  the project, and hand groups of 2–3 scenes to background sub-agents in one message, each prompt
  being the brief plus its scene ids. A writer edits only its own `scenes/SNN.html`, runs only
  `check.py --scene` and `render.py --scene --preview` (at most 3 rounds), never renders the film,
  and reports one line per scene plus anything the main agent must handle (a shared block it needs,
  a storyboard line that cannot be shown, a missing asset).
- Then `check.py --project "$P"` for all scenes; fix every failure.

## 7. Render, music, mix

```bash
python3 "$SKILL_DIR/scripts/render.py" --project "$P"            # changed scenes only (fingerprints)
python3 "$SKILL_DIR/scripts/render.py" --project "$P" --join     # build/picture.mp4
python3 "$SKILL_DIR/scripts/bgm.py"    --project "$P"            # if storyboard.bgm is set
python3 "$SKILL_DIR/scripts/sfx.py"    --project "$P"
python3 "$SKILL_DIR/scripts/mix.py"    --project "$P"            # build/mix.wav + review/loudness.json
python3 "$SKILL_DIR/scripts/render.py" --project "$P" --final    # out/<slug>.mp4
```
**Budget: one full render per draft.** Fix a batch, then render; never render after each edit.

## 8. Review — measure first, then look

1. `python3 "$SKILL_DIR/scripts/defects.py" --project "$P"` (flashes, jumps, freezes ≥ 2.5 s;
   move length and settle time are checked exactly by `check.py` from the timeline) and `review_evidence.py` (contact sheets, full frames, seams, strip).
2. Read the evidence images and score with `craft-rubric.md`: first the big defects (at most 6,
   only ones you caused), then K1–K9 and C1–C3. A score below 1 must cite a sheet cell or a
   measured number. Measured numbers beat what you think you see.
3. **Budget: at most 2 rounds after the first cut** — a defect round (fix every defect you
   caused), then an optional craft round (at most 3 edits in at most 3 scenes). If the craft
   round makes it worse, deliver the previous cut. A limit of the prompt or the style is
   reported to the person, not "fixed".

## 9. Deliver

Show `out/<slug>.mp4` to the person (open it on the Canvas). The summary says: what the film
argues, length and format, what you decided for them, what the review found and fixed, and
what is left. Never mention scores, draft counts or rule ids. Versions are theirs to keep.

The project was registered as a Pollinate work at the brief (§2); if it was not (no
`work.json` in the project), run that `adopt` command now. Once `out/<slug>.mp4` exists the
overlay lists it as done.

## Revision turns

- First send a per-scene change plan ("S02: …; S04: …"), then change only those scenes.
- "Polish" keeps narration and voice fixed and improves only the picture.
- Style swap keeps narration and voice; method swap rewrites script and storyboard, keeps the style.
- If the person edited the storyboard, their version is the truth: re-run `tts.py` for changed lines,
  then `film.py`.
- A new cut of a finished film (longer, shorter, re-ordered, a second language of the same picture) is a new
  project made from the old one: § Revise or extend a film.

## Revise or extend a film

A new version that keeps much of an earlier film starts from it instead of from scratch. The old project
stays as it was; the new one is its own Pollinate work.

1. **Plan with the person's material in hand.** List the old scenes and decide what the new cut keeps,
   changes, adds and drops. **Never shorten the person's material silently:** when the cut must drop
   content (a scene, a claim, a number, a step, a quote), name exactly what is dropped and ask before
   producing (one question, with the list). A longer cut adds; it does not rewrite what already works.
2. **Write `plan.json`** (format in `scripts/derive.py --help` and its header): the new scenes in play
   order; `{"reuse": "S04"}` keeps an old scene; fields next to `reuse` change it (`narration`, `visual`,
   `title`, `transition`); a new scene has `title`, `narration`, `visual`, `shot_type`. Film-level fields
   (`title`, `target_seconds`) go in `"storyboard"`.
3. **Derive** into a new folder (not inside the old one):
   ```bash
   OLD="<old project>"; P="${SWARM_WORKSPACE:-$HOME/.swarm-ai/SwarmWS}/Services/motion-video/projects/<yyyy-mm-dd>-<slug>"
   python3 "$SKILL_DIR/scripts/derive.py" --from "$OLD" --to "$P" --plan plan.json
   ```
   Reused pages are copied byte for byte with the files they load; a reused scene with identical narration
   keeps its voice; changed narration is marked for re-voicing; the output lists new scenes to write and the
   old scenes the cut drops (check that list against what the person agreed to). `derive.json` records it.
4. **Register** the new project, naming its origin:
   ```
   "$SWARM_BACKEND_BIN" py "${SWARM_WORKSPACE:-$HOME/.swarm-ai/SwarmWS}/.claude/skills/s_pollinate/scripts/work.py" adopt --dir "$P" --kind video --derived-from "$OLD"
   ```
5. **Voice, film data, carry:** `tts.py` (it re-voices changed and new lines and reuses the copied voices),
   `film.py`, then `derive.py --carry --to "$P"`: a reused scene keeps its finished render when its picture
   cannot have changed (same page and files, voice, duration, fade tail and film-layer entry). With the
   progress bar on, a scene whose place in the film moved has a different bar, so it re-renders.
6. **Write the new scenes** (§6, the brief works the same) and fix reused scenes whose cue words changed
   (`check.py` names them). Then `check.py` for all scenes and §7–§9 as for a new film: `render.py`
   renders only new and changed scenes.

## Relation to Pollinate

Pollinate (`s_pollinate`) makes decks, posters, long images and copy, and hands every video
request to this skill. A film this skill makes is registered as a Pollinate work at its brief (§2), so
the person finds it in the Pollinate overlay next to their other content, in progress until
the film exists. When a request asks
for a deck and a video on one topic, use the same thesis and evidence for both.
