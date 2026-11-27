---
name: motion-video
description: "Make explainer and ad videos as code-drawn animation: script, voice, HTML+GSAP scenes, frame-exact render, mix, MP4. TRIGGER: \"make a video\", \"explainer video\", \"做个视频\", \"讲解片\". NOT FOR: decks or posters (s_pollinate), editing camera footage."
tier: lazy
python-optional-deps: [edge_tts, playwright, numpy, PIL]
---

# Motion Video — a director, not a template

One description in, one finished MP4 out. You act as the director: decide the thesis,
write the narration, storyboard it, record the voice, write every scene as an HTML page
with a paused GSAP timeline, render it frame by frame, mix the sound, then measure and
look at the cut before delivering it.

Read `INSTRUCTIONS.md` before starting. It is the workflow; the craft lives in `references/`.

## Requirements

- system `python3` with `edge_tts`, `playwright` (Chromium cached), `numpy`, `PIL`
- `ffmpeg` / `ffprobe` on PATH
- Scripts run as `python3 "$SKILL_DIR/scripts/<name>.py" --project <dir>`;
  `$SKILL_DIR` is this skill's base directory (shown when the skill loads).

## Verification (show evidence for each before calling the film done)

- [ ] `scripts/check.py` exits 0 for every scene
- [ ] `scripts/defects.py` reports no flash; every freeze is fixed or explained
- [ ] `review/loudness.json`: about -14 LUFS, true peak ≤ -1 dBTP, no narration gap > 1.5 s
- [ ] contact sheets read against `references/craft-rubric.md`; defects fixed; rubric mean ≥ 0.75 with no zero
- [ ] `out/<slug>.mp4` plays, and its length matches the storyboard within one frame
- [ ] the project is registered as a Pollinate work (`work.py adopt --kind video`, done at the brief)
