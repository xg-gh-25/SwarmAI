# Project contract (the files every script and every doc agree on)

One video = one project directory. Default location:
`${SWARM_WORKSPACE:-$HOME/.swarm-ai/SwarmWS}/Services/motion-video/projects/<yyyy-mm-dd>-<slug>/`.
Every script takes `--project <dir>` and reads/writes only inside it.

```
<project>/
  brief.md                 what the film must achieve (6 questions answered), audience, length, format
  script.md                fact-checked narration, one block per scene (optional; storyboard.json is the truth)
  storyboard.json          SOURCE OF TRUTH (schema: schemas/storyboard.schema.json)
  design.md                short design contract written after the sample scene (palette, type, motion, reused blocks)
  scenes/S01.html ...      one self-contained HTML page per scene (contract: references/scene-contract.md)
  scenes/shared.css        optional styles shared by all scenes
  assets/                  images, logos, generated art (local files only, no URLs)
  audio/voice/S01.mp3      narration per scene            (scripts/tts.py)
  audio/voice/S01.words.json   word timings  [{"text","start","end"}]  seconds from scene start
  audio/voice/S01.captions.json caption lines [{"text","start","end"}]  seconds from scene start
  audio/bgm.wav            background music               (scripts/bgm.py)
  audio/sfx/<name>.wav     synthesized sound effects      (scripts/sfx.py)
  build/S01.mp4            silent picture per scene       (scripts/render.py)
  build/picture.mp4        all scenes joined, silent
  build/mix.wav            final audio                    (scripts/mix.py)
  review/defects.json      flashes, jumps, freezes (scripts/defects.py)
  review/sheet-S01.jpg     contact sheets, full frames, seams, strip (scripts/review_evidence.py)
  out/<slug>.mp4           delivered film                 (scripts/render.py --final)
  review/                  evidence: contact sheets, defects.json, loudness.json, review.md
```

## storyboard.json (fields every tool relies on)

```json
{
  "title": "string",
  "lang": "zh-CN | en-US | ...",
  "width": 1920, "height": 1080, "fps": 30,
  "style": "exec-brief",            "method": "principle",        "mode": "explainer",
  "voice": {"name": "zh-CN-YunxiNeural", "rate": "+0%"},   // optional "engine": "edge" | "polly" (tts.py writes it)
  "bgm": {"mood": "calm", "volume": 0.32, "seed": 7},            // or null for no music
  "captions": true,
  "target_seconds": 75,             // optional; planned length (warn +10 %, fail +20 %)
  "scenes": [
    {
      "id": "S01",
      "title": "<= 10 chars / 5 words",
      "narration": "what the voice says",
      "visual": "what is on screen and when (cue points: element <- spoken keyword)",
      "shot_type": "one of references/shot-types.md",
      "duration": null,             // seconds; written by tts.py = max(voice + 0.4, 2.0); scenes without narration set it by hand
      "sfx": [{"name": "whoosh", "at": 1.2}],   // at most 2 per scene, 8 per film
      "transition": "cut",          // cut | fade (fade = 0.4 s crossfade into the NEXT scene, total length unchanged)
      "hero": false                 // optional; the signature scene, music lift snaps to it
    }
  ]
}
```

Rules:
- Scene ids are `S01`, `S02`, ... in play order; file names follow the id.
- `duration` is decided by the measured voice, never by the writer. `tts.py` writes it back.
- All times inside a scene are seconds from that scene's start.
- Fixed numbers (single source; docs must not restate different ones):
  - padding after the voice: 0.4 s; minimum scene: 2.0 s
  - narration estimate before TTS: zh 4.5 chars/s; en about 2.6 words/s (estimate only; TTS is the truth)
  - elements appear about 0.1 s before their keyword is spoken; last motion settles 0.3 s before scene end
  - every move lasts at least 0.25 s; the whole frame is never fully still for more than 2 s (except end-card hold)
  - key text hold: every text block the timeline reveals (not captions, not dimmed text) stays readable at least
    `CJK chars / 4.5 + other chars / 15 + 1.5` s before the scene ends (spaces and punctuation do not count), and
    no new text block lands in the last 1.2 s of a scene (`check.py` fails the scene; after `tts.py`,
    `check.py --storyboard` warns on a `'label' <- cue` whose label, landing about 0.5 s after its cue word starts,
    would hold too briefly)
  - SFX: at most 2 per scene, 8 per film, at 0.22 of voice level (max 0.35)
  - loudness: -14 LUFS integrated, -1 dBTP; music pre-levelled to -20 LUFS, then x volume (default 0.32), ducked under voice
  - captions: break at punctuation or 16 chars (12 in portrait); a line lasts until the next starts
  - video encode: H.264 (libx264) crf 18, yuv420p, bt709, AAC 192k, +faststart
