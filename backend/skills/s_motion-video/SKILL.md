---
name: motion-video
description: "Make explainer and ad videos as code-drawn animation: script, voice, HTML+GSAP scenes, frame-exact render, mix, MP4. TRIGGER: \"make a video\", \"explainer video\", \"做个视频\", \"讲解片\". NOT FOR: decks or posters (s_pollinate), editing camera footage."
tier: lazy
python-deps: [boto3, PIL, edge_tts]
python-optional-deps: [numpy, jsonschema]
---

# Motion Video — a director, not a template

One description in, one finished MP4 out. You act as the director: decide the thesis,
write the narration, storyboard it, record the voice, write every scene as an HTML page
with a paused GSAP timeline, render it frame by frame, mix the sound, then measure and
look at the cut before delivering it.

Read `INSTRUCTIONS.md` before starting. It is the workflow; the craft lives in `references/`.

## Requirements

**One way to run every script**, on macOS, Linux and Windows:
`"$SWARM_BACKEND_BIN" py "$SKILL_DIR/scripts/<name>.py" --project <dir>` (SwarmAI's own Python;
`$SKILL_DIR` is this skill's base directory, shown when the skill loads).

What that Python carries, and what each script needs beyond it:

| Need | Used by | Status in SwarmAI's Python |
|---|---|---|
| `boto3` (Amazon Polly) | `tts.py` | bundled |
| `PIL` (Pillow) | `check.py`, `review_evidence.py` | bundled |
| `numpy` | `bgm.py`, `sfx.py`, `defects.py` | not declared by the backend; present today only through another package, so treat it as optional |
| `jsonschema` | `check.py` (schema check; skipped with a warning without it) | as numpy |
| SwarmAI's pinned browser runtime (Node, Playwright, Chromium) | `render.py`, `check.py` | installed by SwarmAI on first use (`core.feature_map.runtime`); no Python Playwright |
| `edge_tts` | `tts.py` (the default Chinese voice, Yunxi) | bundled (LGPL-3.0, see NOTICE.md) |
| `ffmpeg` / `ffprobe` on PATH | every audio and video step | system tool (macOS `brew install ffmpeg`, Windows `winget install Gyan.FFmpeg`, Linux the distribution's package) |

A missing dependency is an environment error: the script exits 2 with one line that names it and how to get
it (`tts.py` does this for ffmpeg/ffprobe, edge-tts and boto3). Tell the person that line; do not work
around it.

**Voice engine.** `tts.py` speaks with edge-tts or Amazon Polly (neural voices, word timings from speech
marks); both give the same files, timings and duration rule. Without `--engine`, the engine is the one the
storyboard's voice name belongs to (`zh-CN-YunxiNeural` is edge-tts, `Zhiyu` / `Matthew` are Polly), so an
existing film never changes voice. **Default for a new film:** a Chinese film speaks with edge-tts's Yunxi;
another language takes Polly when SwarmAI's AWS account (Settings) can use it, else edge-tts. Voice a new film the
first time with `tts.py --project "$P" --engine auto`; it says which engine and why and writes the voice into
`storyboard.json`, so later runs need no flag. **When edge-tts fails** (its endpoint rejects the client, the
network is down, it is not installed) and no `--engine` was given, the whole film is voiced again with Polly's
default voice for its language, never only the failed scenes, and the storyboard records the Polly voice; say
so to the person. edge-tts uses an unofficial endpoint whose terms do not cover third-party clients (NOTICE.md),
which is why Polly, an official service on SwarmAI's AWS account (the one set in Settings, used for every
SwarmAI feature; the person's own `~/.aws` profiles are never used for it), is the fallback.

**Fonts.** Every style resolves on every OS: macOS families first, then the open-licence fonts bundled in
`references/styles/fonts/` (CJK sans, serif and Kai included), which is what Windows and Linux render.

## Verification (show evidence for each before calling the film done)

- [ ] `scripts/check.py` exits 0 for every scene
- [ ] `scripts/defects.py` reports no flash; every freeze is fixed or explained
- [ ] `review/loudness.json`: about -14 LUFS, true peak ≤ -1 dBTP, no narration gap > 1.5 s
- [ ] contact sheets read against `references/craft-rubric.md`; defects fixed; rubric mean ≥ 0.75 with no zero
- [ ] `out/<slug>.mp4` plays, and its length matches the storyboard within one frame
- [ ] the project is registered as a Pollinate work (`work.py adopt --kind video`, done at the brief)
