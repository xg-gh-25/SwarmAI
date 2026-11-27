# Brief for scene-writing sub-agents: <FILM TITLE>

<!-- The director fills this once per film, after design.md, film.json and the first finished scenes exist,
     saves it as <project>/scene-writer-brief.md and starts the writers in ONE message (2-3 scenes each),
     each prompt = this brief + "Your scenes: S07, S08". Replace every <...>; delete these comments.
     The rules live in references/scene-contract.md; write here only what is particular to this film. -->

Project `P` = `<absolute path of the project>`
Skill `SK` = `<absolute path of the s_motion-video skill>`
Python: `<python with playwright, e.g. python3>`

## Your scenes

`<S07, S08>`, listed in the prompt that hands you this brief. Nobody else edits them; you edit nothing else.

## Read first (fully, before writing)

1. `$SK/references/scene-contract.md` (the page rules, the film layer, § Writing rules) and
   `$SK/references/project-contract.md` (the fixed numbers).
2. The style: `$SK/references/styles/<style>.md` and `<style>.css`.
3. `$P/design.md` (this film's palette, type, shot grammar, shared blocks), `$P/scenes/shared.css`,
   `$P/film.json` (which shared parts are on) and any shared script the scenes load: `<scenes/xyz.js, or none>`.
4. Finished example scenes, one per layout this film uses (read the code, then the stills in `$P/review/`):
   `<S04.html (triptych)>`, `<S05.html (big number, split)>`, `<S07.html (stacked rows with a strike)>`, ...
5. `$P/storyboard.json`: your scenes' `narration`, `visual`, `shot_type`, `duration`, and the scenes just before
   and after yours (for § Neighbours differ).
6. Your scenes' word timings: `$P/audio/voice/SNN.words.json` (the only words you may cue).

## Rules

All of `scene-contract.md` § Writing rules and § Timeline rules hold. In particular: the storyboard
`visual` is the plan; every content scene calls `FL.setup(tl, {...})` right after creating its timeline
(this film: `<e.g. mask "left" on split layouts, origin on the focus>`); chapter cards `<are done / are yours>`.

For this film only:
- `<a film-specific rule, e.g. "red only for the three problem annotations in chapter 2">`
- `<words that must appear exactly, e.g. "Agent", "85", "≥3×">`

## Per scene

```bash
cd "$P"
perl -e 'alarm 170; exec @ARGV' <python> "$SK/scripts/check.py" --project "$P" --scene SNN
perl -e 'alarm 170; exec @ARGV' <python> "$SK/scripts/render.py" --project "$P" --scene SNN --preview
```

(`perl -e 'alarm N; exec @ARGV'` is a wall-clock cap; where perl is missing use `timeout N` or the
shell's own cap.) Then LOOK at `$P/review/SNN-a.jpg`, `SNN-b.jpg`, `SNN-c.jpg` (start, middle, end). Fix every must-fix problem:
unreadable, cut or overlapping text, text in the caption band, a blank frame, a missing element, a layout that
repeats the neighbour's. **At most 3 rounds per scene**, then stop and report what is left.

Never: render the film or a scene video, run `tts.py`, `bgm.py`, `sfx.py`, `mix.py` or `film.py`, or edit
`storyboard.json`, `film.json`, `scenes/film-data.js`, `design.md`, `shared.css` or another writer's scene.

## Report (your last message)

One line per scene: `SNN pass|fail: <what it shows, in a few words>`. Then, only if any: what the director
must handle (a shared style or block you need, a storyboard line that cannot be shown as written, a missing
asset, a cue word the voice does not say, a check you could not make pass in 3 rounds).
