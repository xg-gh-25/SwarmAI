# Track A: Video

> Remotion-composed MP4 video with TTS narration, timing-synced animations, and
> platform thumbnails. zh-CN pronunciation pre-flight, mandatory Studio preview gate.

## When This Track Runs

Executes during BUILD when `"video"` is in `confirmed_tracks` (discovery.json).
Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

(Legacy note: older strategy.json used `production_tracks`; STRATEGIZE copies
`confirmed_tracks` into it for back-compat — they are always equal. Scope is
authoritatively `confirmed_tracks`.)

---

#### Step 5.1: Prerequisites Check

```bash
python "$SKILL_DIR/scripts/check_prereqs.py"
```

This verifies: Node.js, npm, ffmpeg, ffprobe, Python dependencies.

#### Step 5.2: Remotion Bootstrap (first run only)

```bash
STUDIO_DIR="$HOME/.swarm-ai/SwarmWS/Services/pollinate-studio"
if [ ! -f "$STUDIO_DIR/package.json" ]; then
  npx create-video@latest "$STUDIO_DIR" --template blank
  cd "$STUDIO_DIR" && npm install
  # Copy templates from skill
  cp -r "$SKILL_DIR/templates/remotion/"* "$STUDIO_DIR/src/remotion/"
fi
```

#### Step 5.3: Pronunciation Pre-Flight (zh-CN only)

Three-pass LLM analysis of podcast.txt:

**Pass 1: Polyphone scan** -- context-dependent disambiguation
- Example: 行 (hang=行业 vs xing=行动), 重 (zhong=重要 vs chong=重复)
- For each ambiguous character, determine correct pronunciation from context

**Pass 2: English term review** -- hyphenated names, initialisms
- Example: "GPT-SoVITS" -> split handling, "AIDLC" -> letter-by-letter
- Tag all English terms that TTS engines may mispronounce

**Pass 3: Brand names** -- words with expected Chinese pronunciation
- Example: "Qwen" -> "qian wen", "Doubao" -> "dou bao"
- Cross-reference with brand/identity.yaml voice section

**Output:** `content/{name}/tracks/video/phonemes.json`

**Phoneme priority:** inline `word[pinyin]` > project phonemes.json > global phonemes.json

#### Step 5.4: TTS Audio Generation

```bash
python "$SKILL_DIR/scripts/generate_tts.py" \
  --input "content/{name}/tracks/video/podcast.txt" \
  --output-dir "content/{name}/tracks/video/" \
  --backend polly \
  [--resume]
```

**Backend resolution:** CLI `--backend` > env `TTS_BACKEND` > `user_prefs.json` > `"edge"` (default, free)

**Outputs:**
- `podcast_audio.wav` -- full narration audio
- `podcast_audio.srt` -- subtitle file
- `timing.json` -- per-section timestamps for Remotion sync

**CRITICAL: Save timing.json BEFORE audio concatenation.** If concatenation
fails or is interrupted, timing data is lost and must be regenerated from
scratch. The script handles this, but verify the file exists after TTS completes.

#### Step 5.5: Thumbnail Generation

Always generate via Remotion still render:
```bash
cd "$STUDIO_DIR"
npx remotion still src/remotion/index.ts Thumbnail16x9 \
  "content/{name}/tracks/video/thumbnail_16x9.png" \
  --public-dir "content/{name}/tracks/video/" \
  --props '{"title": "{Title}", "theme": "{theme}"}'
npx remotion still src/remotion/index.ts Thumbnail4x3 \
  "content/{name}/tracks/video/thumbnail_4x3.png" \
  --public-dir "content/{name}/tracks/video/" \
  --props '{"title": "{Title}", "theme": "{theme}"}'
```

For 小红书, also generate 3:4:
```bash
npx remotion still src/remotion/index.ts Thumbnail3x4 \
  "content/{name}/tracks/video/thumbnail_3x4.png" \
  --public-dir "content/{name}/tracks/video/" \
  --props '{"title": "{Title}", "theme": "{theme}"}'
```

#### Step 5.6: Remotion Composition

1. Copy component library to studio (if absent or updated):
   ```bash
   cp -r "$SKILL_DIR/templates/remotion/components/"* "$STUDIO_DIR/src/remotion/components/"
   ```
2. Create per-video composition: `src/remotion/{PascalCaseName}Video.tsx`
   - **NEVER overwrite Video.tsx template** -- create a new file per video
   - Register the new composition in Root.tsx
3. Apply visual preferences from `brand/identity.yaml` + `user_prefs.json`
4. Drive all animations from `timing.json` via `useTiming` hook
5. 4K output: design at 1080p, wrap in `<Scale4K>` component (`scale(2)` to 3840x2160)
6. Subtitles + ChapterProgressBar render OUTSIDE `<Scale4K>` wrapper

#### Step 5.7: Studio Preview (MANDATORY GATE)

```bash
cd "$STUDIO_DIR"
pkill -f "remotion studio" 2>/dev/null || true
npx remotion studio src/remotion/index.ts --public-dir "content/{name}/tracks/video/"
```

**THIS GATE CANNOT BE SKIPPED. NO AUTOMATION BYPASSES IT.**

- Agent reviews each section for timing sync, visual quality, subtitle placement
- User reviews in Remotion Studio (opens browser at localhost:3000)
- Iterate on any issues found
- Pipeline BLOCKS here until user explicitly says one of:
  - "render 4K"
  - "render final"
  - "looks good, proceed"
  - "approved"

**If user has not approved, DO NOT advance to REVIEW. Period.**

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| TTS backend selection | Mechanical | user_prefs > env > "edge" |
| Speech rate | Taste | "+0%" (from identity.yaml) |
| Color theme | Taste | From domain_themes in identity.yaml |
| Component animation style | Taste | Default animations per component |
| Subtitle font/size | Mechanical | From user_prefs.global.subtitle |

### Verification Gate

Before advancing to REVIEW, ALL must be true:
- [ ] `podcast_audio.wav` exists and is non-empty
- [ ] `timing.json` exists and has entries for ALL `[SECTION:xxx]` markers
- [ ] `podcast_audio.srt` exists and has > 0 subtitle entries
- [ ] All thumbnail files exist: `thumbnail_16x9.png`, `thumbnail_4x3.png` (and `thumbnail_3x4.png` if 小红书 targeted)
- [ ] Per-video `.tsx` composition file exists in `src/remotion/`
- [ ] Composition is registered in `Root.tsx`
- [ ] `phonemes.json` exists (zh-CN) or pronunciation pre-flight was N/A (en-US)
- [ ] User has explicitly approved the Studio preview ("render 4K" or equivalent)
- [ ] Timing sync verified: each section start/end in timing.json matches audio sections

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Studio preview looks fine from the terminal output" | Terminal output is not visual review. Open Studio, check every section. |
| "User hasn't responded, assume approval" | Approval must be EXPLICIT. Wait or checkpoint. |
| "Polyphone check is overkill for a short script" | Short scripts have higher per-word impact. One mispronounced term ruins credibility. |
| "Skip thumbnails, we can add them later" | Thumbnails drive click-through rate. Generate all sizes now. |
| "TTS audio sounds fine, skip timing verification" | Timing drift compounds. Verify timing.json matches every section marker. |
| "Remotion template works fine, no need for per-video composition" | Templates are shared. Per-video compositions allow content-specific layout. Always create one. |

### Max Retries

3. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/tracks/video/podcast_audio.wav` -- full narration audio
- `content/{name}/tracks/video/podcast_audio.srt` -- subtitle file
- `content/{name}/tracks/video/timing.json` -- per-section timestamps
- `content/{name}/tracks/video/phonemes.json` -- pronunciation corrections (zh-CN)
- `content/{name}/tracks/video/thumbnail_16x9.png` -- 1920x1080 playback thumbnail
- `content/{name}/tracks/video/thumbnail_4x3.png` -- 1200x900 recommendation feed
- `content/{name}/tracks/video/thumbnail_3x4.png` -- 1080x1440 小红書 feed (if applicable)
- `$STUDIO_DIR/src/remotion/{Name}Video.tsx` -- per-video Remotion composition

---

