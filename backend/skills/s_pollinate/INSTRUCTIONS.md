# Pollinate -- Content Production Pipeline

Drive the full content lifecycle from topic to published deliverables. You ARE
the orchestrator -- execute each stage's behavior inline within this session,
don't invoke separate skills.

## Core Loop

For every pipeline run, follow this loop:

```
1. INIT     -- parse topic, detect domain, load or create pipeline run
2. STAGE    -- for each stage in the pipeline:
               a. Gate check (budget, retries, escalations)
               b. Load context (Knowledge/ + upstream outputs)
               c. Execute stage behavior
               d. Classify decisions (mechanical/taste/judgment)
               e. Verify output (checklist + files exist)
               f. Handle result (advance / retry / checkpoint)
3. DELIVER  -- at delivery stage, run the Delivery Gate
4. COMPLETE -- summarize, reflect, record metrics
```

---

## Step 1: INIT

### Starting a New Pipeline

Parse the user's message to extract:
- **Topic:** what the content is about
- **Domain:** which knowledge area (AIDLC, AI Architecture, Industry Insights, etc.)
- **Formats:** default video; user may request article/poster (Phase 2)
- **Platforms:** default all 5; user may specify subset

Create the content directory:
```bash
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTENT_DIR="$HOME/.swarm-ai/SwarmWS/Services/pollinate-studio/content/{name}"
mkdir -p "$CONTENT_DIR/tracks/video"
mkdir -p "$CONTENT_DIR/tracks/narrative"
mkdir -p "$CONTENT_DIR/tracks/poster"
mkdir -p "$CONTENT_DIR/tracks/shorts"
mkdir -p "$CONTENT_DIR/deliver"
```

Create `content/{name}/run.json`:
```json
{
  "id": "run_p_{8-char-uuid}",
  "type": "pollinate",
  "topic": "...",
  "domain": "...",
  "formats": ["video"],
  "platforms": ["bilibili", "youtube", "xiaohongshu", "douyin", "weixin_video"],
  "status": "running",
  "stages": [],
  "taste_decisions": [],
  "created_at": "<ISO timestamp>",
  "updated_at": "<ISO timestamp>"
}
```

Announce:
```
Pollinate started: "{topic}" (run_p_{id})
Domain: {domain} | Formats: Video
Platforms: B站, YouTube, 小红书, 抖音, 视频号
```

### Resuming a Pipeline

When the user says "resume pollinate" or drags a Radar todo:

1. Read `content/{name}/run.json`
2. Check pending escalations -- if any still open, report and wait
3. Skip completed stages, resume from the checkpoint stage
4. Announce:
```
Pollinate RESUMED: "{topic}" (run_p_{id})
Completed: evaluate, think, plan
Resuming from: build
```

---

## Execution Modes

| Mode | Trigger | Behavior |
|------|---------|----------|
| **Auto** | Default / "make content about..." | Full pipeline with defaults, mandatory stop at Studio preview |
| **Interactive** | "interactive" / "I want to control each step" | Prompts at every decision point |
| **Resume** | "resume pollinate" / drag Radar todo | Load checkpoint, skip completed stages |

---

## Decision Classification

Every non-trivial decision during stage execution MUST be classified:

| Classification | Definition | Action | Content Example |
|---|---|---|---|
| **Mechanical** | One correct answer, deterministic | Auto-approve | "ROI = 4.2, threshold is 3.0 -> GO" |
| **Taste** | Reasonable default, human might differ | Accumulate for delivery gate | "Dark theme for AI architecture topic" |
| **Judgment** | Genuinely ambiguous, needs human | Block, checkpoint | "Should we include controversial claim X?" |

**Content-specific decision examples:**

| Stage | Decision | Classification | Default |
|-------|----------|---------------|---------|
| EVALUATE | ROI calculation | Mechanical | Formula output |
| EVALUATE | Format recommendation | Mechanical | From lookup table |
| THINK | Differentiation angle | Taste | Agent's best analysis |
| PLAN | Content Package structure | Taste | 5-7 key points |
| PLAN | Component selection per section | Taste | From content-type table |
| PLAN | Script length adjustment | Mechanical | Against duration target |
| BUILD | TTS backend selection | Mechanical | user_prefs > env > edge |
| BUILD | Speech rate adjustment | Taste | +0% default |
| BUILD | Color theme override | Taste | From domain_themes |
| REVIEW | Fix RP-V finding | Mechanical | Must fix all failures |
| DELIVER | Metadata tone/style | Taste | Per-platform defaults |
| REFLECT | Which patterns to record | Mechanical | Record all observations |

Log each decision in the pipeline run state:
```json
{
  "stage": "plan",
  "description": "Used dark theme for AI architecture topic",
  "classification": "taste",
  "reasoning": "domain_themes.ai_architecture.theme=dark in identity.yaml, matches technical depth"
}
```

---

## Checkpoint Protocol

### When to Checkpoint

Checkpoint (pause the pipeline) when ANY of:
- Judgment decision (e.g., "Should we include this controversial opinion?")
- Stage retry exhaustion (>= max_retries failures)
- Context budget >60% consumed
- Taste decision unresolvable (agent cannot pick a reasonable default)
- User absent (no response after judgment escalation)

### How to Checkpoint

1. Save pipeline state to `content/{name}/run.json` with status "paused"
2. Present to user:
```
Pollinate PAUSED at {STAGE} (run_p_{id})
Reason: {why}

  Completed: evaluate, think, plan
  Next: build
  Pending: {escalation summary}

  Resume: resolve the issue, then "resume pollinate for {topic}"
```

---

## Progress Display

Show progress after each stage completes. Use this format:

```
Pollinate: "{topic}" (run_p_{id})
Domain: {domain} | Formats: Video
Platforms: B站, YouTube, 小红書

  [done] EVALUATE   GO, ROI 4.2
  [done] THINK      Differentiation: "两大框架创始人同时验证"
  [>>>>] STRATEGIZE PR/FAQ + channel matrix...
  [    ] PLAN
  [    ] BUILD
  [    ] REVIEW
  [    ] TEST
  [    ] DELIVER
  [    ] REFLECT
```

Stage status indicators:
- `[done]` = completed successfully
- `[>>>>]` = currently executing
- `[skip]` = skipped (not in profile)
- `[FAIL]` = failed, will retry or checkpoint
- `[STOP]` = checkpointed (pipeline paused)
- `[    ]` = pending

---

## Max Retries Per Stage

| Stage | Max Retries |
|-------|-------------|
| EVALUATE | 2 |
| THINK | 2 |
| STRATEGIZE | 2 |
| PLAN | 2 |
| BUILD | 3 |
| REVIEW | 2 |
| TEST | 3 |
| DELIVER | 1 |
| REFLECT | 1 |

After exhaustion -> checkpoint with all failure details.

---

## Stage 1: EVALUATE -- Is this topic worth producing?

### Procedure

1. **Parse topic intent:** what claim, for whom, why now?

2. **Scan internal knowledge:**
   ```bash
   grep -rl "{keywords}" ~/.swarm-ai/SwarmWS/Knowledge/ ~/.swarm-ai/SwarmWS/.context/MEMORY.md
   ```
   List available assets: diagrams, data, code, quotes, prior analysis.

3. **Run evaluation script:**
   ```bash
   python "$SKILL_DIR/scripts/evaluate_topic.py" "{topic}" \
     --domain "{domain}" \
     --diff N --audience N --readiness N --timeliness N --complexity N \
     --json
   ```
   Where N is the agent's score for each dimension (0-5).

4. **Score on 5 dimensions (each 0-5):**

   | Dimension | Weight | Question | Scoring Guide |
   |-----------|--------|----------|---------------|
   | Knowledge Differentiation | 0.30 | Do we know something others don't? | 5=nobody else has this depth, 1=common knowledge |
   | Audience Match | 0.25 | Will AI practitioners/developers care? | 5=core audience, 1=irrelevant |
   | Asset Readiness | 0.20 | How much exists in Knowledge/ already? | 5=complete, 1=nothing |
   | Timeliness | 0.15 | Evergreen(1), trending(3), breaking(5) | Time sensitivity |
   | Production Complexity | 0.10 | Text-only(5) to custom 3D animation(1) | Execution difficulty |

5. **Calculate ROI:**
   ```
   ROI = (Differentiation * 0.30) + (Audience * 0.25) + (Readiness * 0.20)
       + (Timeliness * 0.15) + (Complexity * 0.10)
   ```

6. **Recommend:**
   - **GO** (>= 3.0) -- proceed with pipeline
   - **DEFER** (2.0-2.9) -- log reason, pipeline ends
   - **REJECT** (< 2.0) -- log reason, pipeline ends

7. **Recommend format combination:**

   | Content Type | Recommended Formats |
   |-------------|-------------------|
   | Deep technical teardown | Video (B站, horizontal) + Article |
   | Industry trend / opinion | Video + Poster |
   | Quick knowledge point | Poster only |
   | Breaking news / hot take | Poster first, Video later if traction |
   | Framework / methodology | Video + Article + Poster (全格式) |

8. **Save** `content/{name}/evaluation.json`

If DEFER or REJECT -> pipeline ends. Log reason and exit.

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| ROI calculation | Mechanical | Formula output |
| Format recommendation | Mechanical | From lookup table |
| "Is this the right time to publish?" | Taste | Agent's timeliness assessment |
| "Should we cover this controversial topic?" | Judgment | Block, ask user |

### Verification Gate

Before advancing to THINK, ALL must be true:
- [ ] `content/{name}/evaluation.json` exists and is valid JSON
- [ ] ROI score is calculated with all 5 dimensions scored
- [ ] Recommendation is explicitly GO (not DEFER or REJECT)
- [ ] Internal knowledge scan completed (grep output reviewed)
- [ ] Format combination recommended
- [ ] Available internal assets listed

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Topic is obviously good, skip scoring" | Every topic gets scored. Gut feel is not evaluation. |
| "Knowledge scan found nothing, but I know the topic" | Score Asset Readiness 0-1 honestly. Low readiness raises production risk. |
| "ROI is 2.8, close enough to GO" | 3.0 is the threshold. DEFER at 2.8. No rounding up. |
| "Skip format recommendation, just do video" | Recommend the right formats. Video-only is valid but must be a conscious choice. |

### Max Retries

2. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/evaluation.json` -- topic scores, ROI, recommendation, format plan, asset inventory

---

## Stage 2: THINK -- Research + Differentiation

### Procedure

1. **Internal knowledge scan:**
   - Read identified Knowledge/ files from EVALUATE's asset inventory
   - Scan these directories:
     ```bash
     ls ~/.swarm-ai/SwarmWS/Knowledge/Notes/
     ls ~/.swarm-ai/SwarmWS/Knowledge/Designs/
     ls ~/.swarm-ai/SwarmWS/Knowledge/Reports/
     ```
   - Extract key data points, quotes, code examples, architecture decisions
   - Note connections to MEMORY.md entries (lessons, corrections, COEs)

2. **External competitive research** (use web search):
   - B站/YouTube: search top 5 videos on same topic
   - For each video, record in this template:

     | Field | Value |
     |-------|-------|
     | Title | ... |
     | Views | ... |
     | Duration | ... |
     | Structure | ... |
     | Top comments | ... |
     | Weaknesses | ... |

   - Articles: search 掘金/知乎/Medium for same topic
   - Identify: what's missing, wrong, or shallow

3. **Differentiation framing** -- answer all 3 questions:
   - "What do we know that others don't?"
   - "What did others get wrong or oversimplify?"
   - "What angle hasn't been covered?"

4. **Write** `content/{name}/research.md` with ALL required sections:
   - Core thesis (1 sentence)
   - Target audience profile
   - Differentiation angle
   - Internal asset manifest (file paths + excerpts)
   - Competitive content analysis (top 3-5)
   - Recommended narrative arc

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Competitive research scope | Mechanical | Top 5 videos + top 3 articles |
| Differentiation angle | Taste | Agent's best analysis |
| Narrative arc recommendation | Taste | Based on content type |
| "Is competitor's approach better than ours?" | Judgment | Block if our angle is weaker |

### Verification Gate

Before advancing to STRATEGIZE, ALL must be true:
- [ ] `content/{name}/research.md` exists
- [ ] Core thesis is a single sentence (not a paragraph)
- [ ] Target audience profile is specific (not "everyone")
- [ ] Differentiation angle explicitly answers at least 1 of the 3 framing questions
- [ ] Internal asset manifest lists actual file paths (not placeholders)
- [ ] Competitive analysis covers >= 3 external sources with the template fields filled
- [ ] Recommended narrative arc is present

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "No competitors found, skip competitive analysis" | Search harder. Every technical topic has existing content somewhere. |
| "Internal assets are sufficient, skip external research" | External research validates differentiation. Internal-only content risks being redundant. |
| "Differentiation is obvious" | Write it down explicitly. Obvious to you is not obvious to the audience. |
| "Audience is developers, that's specific enough" | Which developers? Junior/senior? Backend/ML? What pain point? |

### Max Retries

2. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/research.md` -- thesis, audience, differentiation, assets, competitive analysis

---

## Stage 3: STRATEGIZE -- PR/FAQ + Channel × Format Matrix

### Procedure

#### Step 3a: Draft PR/FAQ

Every Pollinate run produces a PR/FAQ as the single source document — even for
a poster. All downstream formats extract from this PR/FAQ.

Create `content/{name}/PRFAQ.md`:

```markdown
# PRESS RELEASE

**Headline:** [one sentence — the value delivered]

**Problem:** [why this matters to the audience]

**Solution:** [what we produce and why it's different]

**Real Example:** [concrete, specific, verifiable proof]

**Quote:** [the "aha" sentence that captures the insight]

# FAQ

**Q: How is this different from X?**
A: ...

**Q: Who is this for?**
A: ...

**Q: What can I do with this?**
A: ...
```

The PR/FAQ must be concrete, not generic. "Real Example" requires actual data,
code, benchmarks, or specific names. No placeholder text like "demonstrates the
value" — show the actual value with numbers or quotes.

#### Step 3b: Channel × Format Decision Matrix

For each enabled channel (from `~/.swarm-ai/pollinate-accounts.yaml`), assess
audience fit and select optimal format.

**Decision rules (all mechanical unless controversial):**

| Condition | Format Decision | Classification |
|-----------|----------------|---------------|
| Breaking news + high timeliness | Poster-first (ship fast), video later if traction | Mechanical |
| Deep technical + high differentiation | Video + narrative (long-form depth) | Mechanical |
| Product launch | Full mix: poster + video + narrative + README | Mechanical |
| Audience fit < 3 for a channel | Skip channel | Mechanical |
| Controversial topic (sensitive claim) | Escalate for judgment | Judgment |

**Audience fit scoring (per channel):**
- 5 = Core audience, perfect match
- 4 = Strong fit, minor gaps
- 3 = Moderate fit, some friction
- 2 = Weak fit, low relevance
- 1 = No fit, wrong audience

Example assessment:
```
Channel Assessment:
┌─────────────┬─────────────┬────────────────────┬──────────┐
│ Channel     │ Audience Fit│ Best Format        │ Priority │
├─────────────┼─────────────┼────────────────────┼──────────┤
│ xiaohongshu │ 5           │ Poster + short text│ P0       │
│ bilibili    │ 5           │ Video + poster     │ P0       │
│ gongzhonghao│ 4           │ Narrative          │ P1       │
│ douyin      │ 3           │ Shorts (vertical)  │ P2       │
│ youtube     │ 2           │ Skip (EN audience) │ Skip     │
└─────────────┴─────────────┴────────────────────┴──────────┘
```

#### Step 3c: Write strategy.json

Save `content/{name}/strategy.json`:

```json
{
  "message": "...",
  "audience": "...",
  "desired_outcome": "awareness -> trial",
  "prfaq_path": "content/{name}/PRFAQ.md",
  "channel_matrix": [
    {"channel": "xiaohongshu", "format": ["poster", "short_text"], "priority": "P0", "audience_fit": 5},
    {"channel": "bilibili", "format": ["video", "poster"], "priority": "P0", "audience_fit": 5},
    {"channel": "gongzhonghao", "format": ["narrative"], "priority": "P1", "audience_fit": 4}
  ],
  "production_tracks": ["poster", "video", "narrative"]
}
```

The `production_tracks` array drives Stage 4 (PLAN) — one spec per track.

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| PR/FAQ structure | Mechanical | Standard template |
| Audience fit scoring | Mechanical | Formula per channel config |
| Format selection per channel | Mechanical | From decision rules table |
| Tone (professional/casual) | Taste | From domain in identity.yaml |
| "Should we cover this angle?" | Judgment | Block if controversial |

### Verification Gate

Before advancing to PLAN, ALL must be true:
- [ ] `content/{name}/PRFAQ.md` exists with all sections filled (no placeholders)
- [ ] PR/FAQ "Real Example" has concrete data (not "demonstrates value")
- [ ] `content/{name}/strategy.json` exists and is valid JSON
- [ ] Every enabled channel has an audience_fit score
- [ ] Channels with audience_fit < 3 are either skipped or have explicit override
- [ ] `production_tracks` array is populated with at least 1 track
- [ ] If controversial topic detected, escalation was handled

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "PR/FAQ feels redundant with research.md" | PR/FAQ is the source doc for all formats. Write it. |
| "Poster doesn't need a PR/FAQ" | Every format derives from PR/FAQ. Write one sentence if minimal. |
| "Channel selection is obvious" | Show the audience_fit scores. Obvious to you is not obvious to audit. |
| "Skip low-fit channels manually" | Let the mechanical rule skip them. Log the reason. |

### Max Retries

2. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/PRFAQ.md` -- source document for all formats
- `content/{name}/strategy.json` -- channel matrix + production tracks

---

## Stage 4: PLAN -- Content Package + Per-Track Specs

### Procedure

Load `strategy.json` to determine which production tracks to plan. For each
track in `production_tracks`, generate the corresponding spec.

#### Step 4a: Content Package (format-agnostic core)

Create `content/{name}/content_package.md`:

```markdown
# {Title}

## Core Thesis
{One sentence -- the single idea this content exists to communicate}

## Key Points (5-7)
1. {Point} -- {Evidence/data}
2. ...

## Narrative Arc
- Hook: {10-15s -- question/contradiction/surprise}
- Setup: {Context the audience needs}
- Development: {Build the argument, section by section}
- Climax: {The "aha" moment / strongest evidence}
- Resolution: {What this means for the audience + CTA}

## Evidence Bank
- Data: {specific numbers, benchmarks, dates}
- Quotes: {expert opinions, source attribution}
- Code: {actual code snippets if applicable}
- Visuals: {diagrams, screenshots, existing assets}

## Internal References
- {file path}: {what to extract}
- ...
```

#### Step 4b: Video Script (if "video" in production_tracks)

Create `content/{name}/tracks/video/podcast.txt` with `[SECTION:xxx]` markers.

**Script structure:**
```
[SECTION:hero]
{Hook -- 10-15 seconds, grab attention}

[SECTION:setup]
{Context and problem framing -- 60-90s}

[SECTION:core_1]
{First key argument -- 60-90s}

[SECTION:core_2]
{Second key argument -- 60-90s}

...

[SECTION:climax]
{Strongest evidence / "aha" moment -- 60-90s}

[SECTION:outro]
{Summary + CTA -- 10-15s}
```

**Script rules:**
- Chinese: ~4 chars/second, concise paragraphs (50-80 chars)
- English: ~3 words/second
- `[SECTION:xxx]` markers are MANDATORY for timing.json generation
- Number formatting: digits OK with Chinese units; spell out dates, versions, long integers
- Platform-specific outro (B站 一键三连, YouTube subscribe, etc.)

#### Step 3c: Visual Composition Plan

For each section, select Remotion components:

| Content Type | Primary Component | Supporting Components |
|-------------|-------------------|----------------------|
| Architecture / flow | FlowChart | DiagramReveal |
| Code example | CodeBlock | -- |
| A vs B comparison | ComparisonCard | DataBar |
| Chronological story | Timeline | IconCard |
| Data / metrics | StatCounter, DataBar | DataTable |
| Expert opinion | QuoteBlock | -- |
| Feature list | FeatureGrid | IconCard |
| Concept introduction | IconCard | SectionLayouts |

**Visual composition rules:**
- No same component type in consecutive sections
- Content width >= 85% of screen
- Bottom 100px reserved for subtitles
- Hero title >= 84px, section title >= 72px, body >= 32px
- Apply domain theme from brand/identity.yaml `domain_themes`

#### Step 4c: Duration Dry-Run (if "video" in production_tracks)

```bash
python "$SKILL_DIR/scripts/generate_tts.py" \
  --input "content/{name}/tracks/video/podcast.txt" \
  --output-dir "content/{name}/tracks/video/" \
  --dry-run
```

Target durations:
- B站 horizontal: 3-8min (ideal), max 12min
- Shorts (小红书/抖音/视频号): 30-120s per section

If dry-run reports >12min -> revise script (trim sections).
If dry-run reports <3min -> revise script (add depth).

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Content Package structure | Taste | 5-7 key points, standard arc |
| Component selection per section | Taste | From content-type table |
| Script length adjustment | Mechanical | Against duration target |
| Section count | Taste | 4-6 sections typical |
| Domain theme override | Taste | From identity.yaml domain_themes |

### Verification Gate

Before advancing to BUILD, ALL must be true:
- [ ] `content/{name}/content_package.md` exists with all template sections filled
- [ ] `content/{name}/video/podcast.txt` exists with `[SECTION:xxx]` markers
- [ ] Every section has a `[SECTION:xxx]` marker (no unmarked content)
- [ ] Dry-run duration is within target range (3-12min for B站)
- [ ] Visual composition plan maps every section to at least one component
- [ ] No same component type appears in consecutive sections
- [ ] Core thesis matches between content_package.md and research.md

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Script is fine without section markers" | Markers drive timing.json. No markers = no audio-video sync. Always add them. |
| "Duration is 13 minutes, close enough" | 12:00 is max. Trim. Every extra minute loses viewers. |
| "Skip visual composition plan, I'll figure it out in BUILD" | Visual plan prevents BUILD rework. Plan every section now. |
| "Same component twice is fine for this content" | Variety keeps attention. Find a different component even if the content type is similar. |
| "Dry-run is slow, skip it" | 5 seconds of dry-run prevents 30 minutes of re-render. Always run it. |

### Max Retries

2. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/content_package.md` -- core narrative, key points, evidence bank
- `content/{name}/video/podcast.txt` -- narration script with section markers
- `content/{name}/visual_plan.md` -- component mapping per section (if separate from content_package)

---

## Stage 5: BUILD -- TTS + Remotion + Preview

### Procedure

#### Step 4.1: Prerequisites Check

```bash
python "$SKILL_DIR/scripts/check_prereqs.py"
```

This verifies: Node.js, npm, ffmpeg, ffprobe, Python dependencies.

#### Step 4.2: Remotion Bootstrap (first run only)

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

## Stage 6: REVIEW -- Quality Audit

### Procedure

1. **Load REVIEW_PATTERNS.md** and run ALL 12 RP-V patterns. For each pattern,
   write explicit pass/fail with evidence:

   | # | Pattern | What to Verify |
   |---|---------|----------------|
   | RP-V1 | **Audio-video sync** | timing.json: each section start/end within +/-0.5s of audio |
   | RP-V2 | **Subtitle safe zone** | No visual content in bottom 100px (reserved for subtitles) |
   | RP-V3 | **Information density** | Each screen shows <= 3 key points simultaneously |
   | RP-V4 | **Subtitle accuracy** | SRT text vs podcast.txt: diff <= 2% (character-level) |
   | RP-V5 | **Thumbnail specs** | 16:9 AND 4:3 files exist, correct dimensions. 3:4 for 小红书 |
   | RP-V6 | **Polyphone coverage** | All domain-specific terms in phonemes.json (zh-CN only) |
   | RP-V7 | **Resolution & codec** | ffprobe: 3840x2160 (or 2160x3840), H.264, >= 8Mbps video, AAC >= 192kbps |
   | RP-V8 | **Duration target** | B站: 3-12min, shorts: 30-120s per section |
   | RP-V9 | **Brand consistency** | Swarm color palette (identity.yaml), font family, intro/outro present |
   | RP-V10 | **Component variety** | No same component type in consecutive sections |
   | RP-V11 | **Text readability** | All text >= 24px, hero >= 84px, section title >= 72px |
   | RP-V12 | **Content width** | >= 85% of screen width utilized |

2. **Output format** -- write result for EVERY pattern:
   ```
   RP-V1:  PASS  All 6 sections within +/-0.3s
   RP-V2:  PASS  Bottom 100px clear
   RP-V3:  WARN  Section 3 has 4 points -- consider splitting
   RP-V4:  PASS  SRT diff 0.8%
   RP-V5:  PASS  16:9 (1920x1080), 4:3 (1200x900), 3:4 (1080x1440)
   RP-V6:  PASS  12 terms in phonemes.json
   RP-V7:  PASS  3840x2160, H.264, 16.2Mbps, AAC 192kbps
   RP-V8:  PASS  6:42 (within 3-12min)
   RP-V9:  PASS  Swarm Orange #FF6B35, PingFang SC, outro present
   RP-V10: PASS  FlowChart -> QuoteBlock -> Timeline -> CodeBlock -> StatCounter
   RP-V11: PASS  Min text 32px, hero 96px
   RP-V12: PASS  Content width 88%
   ```

3. **For each FAIL result:**
   - Identify the exact issue
   - Fix it immediately (adjust composition, re-generate asset, trim script)
   - Re-verify after fix
   - Log the fix

4. **For each WARN result:**
   - Assess severity
   - Fix if clearly wrong; log as taste decision if borderline
   - Document reasoning

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Fix FAIL findings | Mechanical | Must fix all |
| Fix WARN findings | Taste | Fix unless borderline |
| "Information density is 4 points but they're related" | Taste | Split if possible |
| "Brand color is #FF6C36, close to #FF6B35" | Mechanical | Must match exactly |

### Verification Gate

Before advancing to TEST, ALL must be true:
- [ ] All 12 RP-V results are shown (no silence -- every pattern has a result)
- [ ] Zero FAIL results remain unfixed
- [ ] All WARN results have documented reasoning (fix or accepted with justification)
- [ ] Brand colors match identity.yaml exactly (not "close enough")
- [ ] Subtitle safe zone verified (no visual content in bottom 100px)
- [ ] Audio-video sync verified per section

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Script is short, skip polyphone check" | Short scripts have higher per-word impact. Check every term. |
| "Brand colors are close enough" | Brand consistency is binary. Match identity.yaml hex values or fix. |
| "Duration is 12:30, close enough to 12min" | 12:00 is the max. Trim the script. |
| "It looked fine in Studio, skip review" | Studio preview is not quality audit. Check every RP pattern. |
| "Only targeting B站, skip other platform specs" | Generate metadata for all platforms. Distribution is free. |
| "Thumbnails can wait" | Thumbnails drive click-through rate. Verify all sizes exist now. |

### Max Retries

2. After exhaustion -> checkpoint.

### Output Files

- Review results appended to `content/{name}/review_results.md`
- Any fixed assets (updated composition, re-rendered thumbnails, trimmed script)

---

## Stage 7: TEST -- Render + Platform Validation

### Procedure

#### Step 7.1: 4K Render

```bash
cd "$STUDIO_DIR"
npx remotion render src/remotion/index.ts {CompositionId} \
  "content/{name}/tracks/video/output.mp4" \
  --video-bitrate 16M \
  --public-dir "content/{name}/tracks/video/"
```

#### Step 7.2: Verify Render Output

```bash
ffprobe -v quiet -show_entries stream=width,height,codec_name,bit_rate -of json \
  "content/{name}/tracks/video/output.mp4"
```

**Required results:**
- Width: 3840, Height: 2160 (horizontal) or 2160x3840 (vertical)
- Codec: h264
- Video bitrate: >= 8Mbps
- Audio codec: aac

If ffprobe fails any check -> fix and re-render.

#### Step 7.3: BGM Mix

```bash
BGM_VOL=$(python "$SKILL_DIR/scripts/get_pref.py" global.bgm.volume 2>/dev/null || echo "0.05")
ffmpeg -y \
  -i "content/{name}/tracks/video/output.mp4" \
  -i "$SKILL_DIR/brand/assets/bgm/calm-piano.mp3" \
  -filter_complex "[1:a]volume=${BGM_VOL}[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]" \
  -map 0:v -map "[a]" \
  -c:v copy -c:a aac -b:a 192k \
  "content/{name}/tracks/video/video_with_bgm.mp4"
```

#### Step 7.4: Subtitles (optional)

Prefer Remotion-native `<Subtitles>` component (no re-encode needed).
Fallback if not using Remotion subtitles:
```bash
ffmpeg -y \
  -i "content/{name}/tracks/video/video_with_bgm.mp4" \
  -vf "subtitles=content/{name}/tracks/video/podcast_audio.srt:force_style='FontName=PingFang SC,FontSize=24,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=2,Bold=1,Alignment=2,MarginV=30'" \
  -c:v libx264 -crf 18 -c:a copy \
  "content/{name}/tracks/video/video_with_subs.mp4"
```

#### Step 7.5: Final Assembly

```bash
cp "content/{name}/tracks/video/video_with_bgm.mp4" "content/{name}/tracks/video/final_video.mp4"
```

(If subtitle burn-in was used, copy `video_with_subs.mp4` instead.)

#### Step 7.6: Platform Spec Validation

```bash
python "$SKILL_DIR/scripts/check_specs.py" \
  "content/{name}/tracks/video/final_video.mp4" \
  --platforms bilibili,youtube,xiaohongshu,douyin,weixin_video
```

**Platform spec requirements:**

| Check | B站 | YouTube | 小红書 | 抖音 | 视频号 |
|-------|-----|---------|--------|------|--------|
| Resolution | 3840x2160 | 3840x2160 | 2160x3840 | 2160x3840 | 2160x3840 |
| Codec | H.264 | H.264 | H.264 | H.264 | H.264 |
| Bitrate | >= 8Mbps | >= 8Mbps | >= 6Mbps | >= 6Mbps | >= 6Mbps |
| Duration | 3-12min | 3-12min | 30-120s | 30-120s | 30-120s |
| Audio | AAC 192k | AAC 192k | AAC 192k | AAC 192k | AAC 192k |

Must pass ALL checks for ALL target platforms.

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| All spec validation | Mechanical | Binary pass/fail |
| BGM volume level | Taste | 0.05 from identity.yaml |
| Subtitle burn-in vs Remotion-native | Mechanical | Remotion-native preferred |

### Verification Gate

Before advancing to DELIVER, ALL must be true:
- [ ] ffprobe confirms 3840x2160 (or 2160x3840), H.264, >= 8Mbps, AAC >= 192kbps
- [ ] `check_specs.py` passes for ALL target platforms
- [ ] `final_video.mp4` exists and is playable
- [ ] BGM is mixed (voice audible, BGM subtle)
- [ ] Video duration matches dry-run estimate (+/- 10%)
- [ ] No rendering artifacts (black frames, frozen sections, audio gaps)

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "ffprobe shows 1920x1080, that's HD enough" | 4K is the spec. Re-render at 3840x2160. |
| "Platform specs mostly pass" | ALL must pass. Fix every failure. |
| "BGM sounds fine at default volume" | Verify voice is clearly audible. If technical content, consider lower BGM. |
| "Skip subtitle verification, we used Remotion-native" | Remotion subtitles still need accuracy check. Compare SRT against script. |
| "Video plays in my player, skip ffprobe" | Your player is tolerant. Platforms are not. ffprobe is the authority. |

### Max Retries

3. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/tracks/video/output.mp4` -- raw 4K render
- `content/{name}/tracks/video/video_with_bgm.mp4` -- with background music
- `content/{name}/tracks/video/final_video.mp4` -- final deliverable
- Platform spec validation results (printed to console)

---

## Stage 8: DELIVER -- Publish Package + Report

### Procedure

#### Step 8.1: Run the Delivery Gate FIRST

Collect ALL taste decisions from ALL prior stages and present as a batch:

```
DELIVERY GATE -- N taste decisions for review:

  1. [THINK]   Differentiation angle: "两大框架创始人同时验证"
  2. [PLAN]    Used dark theme for AI architecture topic
  3. [PLAN]    5 sections instead of 7 (tighter narrative)
  4. [BUILD]   Speech rate +10% for technical content
  5. [BUILD]   FlowChart over DiagramReveal (simpler animation)

  [Approve All]  [Override #1]  [Override #2]  ...  [Discuss]
```

**If no taste decisions accumulated:** skip the gate, proceed.

**If user approves all:** proceed.

**If user overrides any:** re-run the affected stage with the override as a
constraint. This may cascade (overriding a PLAN decision re-runs PLAN, which
may change BUILD). Re-run the minimum set of affected downstream stages.

**If user wants to discuss:** enter conversational mode. Once resolved, resume.

#### Step 8.2: Generate Platform Metadata

```bash
python "$SKILL_DIR/scripts/publish_meta.py" \
  "content/{name}/" \
  --platforms bilibili,youtube,xiaohongshu,douyin,weixin_video
```

Output: `content/{name}/deliver/publish_info.md` with per-platform:

| Platform | Title Rules | Description | Tags | CTA |
|----------|-------------|-------------|------|-----|
| B站 | Number + topic + hook (max 80 chars) | 100-200 chars, knowledge style | 10 tags | 一键三连 |
| YouTube | SEO < 70 chars | Keyword-rich + chapters from 0:00 | Tags + hashtags | Subscribe |
| 小红書 | <= 20 chars, emoji-friendly | 200-500 chars, 种草 style | 5-10 `#tag#` | 点赞收藏加关注 |
| 抖音 | Short, punchy | 100-200 chars, casual + emoji | 3-8 `#tag` | 点赞关注 |
| 视频号 | Knowledge-sharing | 100-300 chars, forwarding-friendly | 3-8 `#tag` | 点赞关注转发 |

#### Step 8.3: Confidence Scoring

Calculate the confidence score using this explicit formula. Each item must be
evaluated and the contribution (+/-) shown:

```
confidence_score (1-10):
  +2 if all RP-V checks passed
  +2 if Studio preview was reviewed and approved by user
  +1 if TTS dry-run duration within target range (3-8min B站)
  +1 if all platform specs validated by check_specs.py
  +1 if no REVIEW findings above warning level
  +1 if polyphone pre-flight completed (zh-CN only, +1 if N/A for en-US)
  +1 if all thumbnail sizes generated (16:9, 4:3, 3:4 if applicable)
  +1 if BGM mixed successfully with correct volume level
  -2 if any RP-V check failed and remains unfixed
  -2 if Studio preview was skipped or user did not approve
  -1 if duration outside target range
  -1 per platform spec validation failure
  -1 if brand colors do not match identity.yaml
```

**Show the full breakdown, not just the final number:**
```
Confidence: 9/10
  +2  All 12 RP-V checks passed
  +2  Studio preview approved by user
  +1  Duration 6:42 within 3-8min target
  +1  All platform specs pass (bilibili, youtube)
  +1  Zero REVIEW findings above warning
  +1  Polyphone pre-flight: 12 terms corrected
  +1  Thumbnails: 16:9, 4:3, 3:4 all generated
  +1  BGM mixed at 0.05 volume
  -1  Brand accent color was #4ECDC5 (fixed to #4ECDC4)
```

If confidence < 7 -> flag for human review before publishing.

#### Step 8.4: Generate REPORT.md

Save to `content/{name}/REPORT.md`:

```markdown
# Pollinate Report: {title}

**Run ID:** run_p_{id} | **Date:** {date} | **Confidence:** {score}/10
**Domain:** {domain} | **Formats:** Video
**Platforms:** {list}

## 1. Topic Evaluation
| Dimension | Score | Rationale |
|---|---|---|
| Knowledge Differentiation | X/5 | ... |
| Audience Match | X/5 | ... |
| Asset Readiness | X/5 | ... |
| Timeliness | X/5 | ... |
| Production Complexity | X/5 | ... |
| **ROI** | **X.X** | **GO** |

## 2. Content Package
- **Core Thesis:** {one sentence}
- **Key Points:** {count}
- **Differentiation:** {angle}
- **Internal Sources:** {count} files referenced

## 3. Production Summary
| Metric | Value |
|---|---|
| Script length | {chars} chars / {est_duration} |
| TTS engine | {backend} / {voice} |
| Sections | {count} |
| Components used | {list} |
| Thumbnails | {sizes generated} |
| BGM | {track} at {volume} |

## 4. Quality Gates
| Gate | Result |
|---|---|
| RP-V1 Audio sync | ... |
| RP-V2 Safe zone | ... |
| RP-V3 Information density | ... |
| RP-V4 Subtitle accuracy | ... |
| RP-V5 Thumbnail specs | ... |
| RP-V6 Polyphone coverage | ... |
| RP-V7 Resolution & codec | ... |
| RP-V8 Duration target | ... |
| RP-V9 Brand consistency | ... |
| RP-V10 Component variety | ... |
| RP-V11 Text readability | ... |
| RP-V12 Content width | ... |
| Studio preview | ... |
| Platform specs | ... |

## 5. Decision Log
| Stage | Decision | Classification | Reasoning |
|---|---|---|---|
| EVALUATE | ... | mechanical | ... |
| PLAN | ... | taste | ... |
| BUILD | ... | taste | ... |

## 6. Files Produced
- `final_video.mp4` -- {resolution}, {duration}, {size}
- `publish_info.md` -- {platforms}
- `thumbnail_16x9.png` -- 1920x1080
- `thumbnail_4x3.png` -- 1200x900
- `thumbnail_3x4.png` -- 1080x1440 (if applicable)

## 7. Lessons (from REFLECT)
- ...

## 8. Known Gaps & Attention Flags
- ...

---
Generated by Pollinate | Swarm Content Engine | {date}
```

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Metadata tone/style per platform | Taste | Per-platform defaults from channels/*.yaml |
| Title formula selection | Taste | B站: number + topic + hook |
| Tag selection | Taste | Mix of broad + specific |
| Confidence score calculation | Mechanical | Formula above |

### Verification Gate

Before advancing to REFLECT, ALL must be true:
- [ ] Delivery Gate completed (taste decisions reviewed or none accumulated)
- [ ] `content/{name}/REPORT.md` saved with all sections filled
- [ ] Confidence breakdown shown (not just final number)
- [ ] `content/{name}/deliver/publish_info.md` exists with per-platform metadata
- [ ] Confidence score >= 7 (or flagged for human review if < 7)
- [ ] All files listed in "Files Produced" section of REPORT.md actually exist

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Skip delivery gate, no taste decisions" | Verify there are truly zero. Check every stage's decision log. |
| "Confidence is 10/10, everything is perfect" | Show the breakdown. Confidence without evidence is fiction. |
| "Metadata can be written manually on each platform" | Automated metadata ensures consistency. Generate now. |
| "REPORT.md is boilerplate, skip it" | REPORT.md is the permanent record. Every run produces one. |
| "Only publishing to B站, skip other platform metadata" | Distribution is free. Generate for all target platforms. |

### Max Retries

1. After exhaustion -> checkpoint.

### Output Files

- `content/{name}/REPORT.md` -- full production report
- `content/{name}/deliver/publish_info.md` -- per-platform titles, descriptions, tags, CTAs

---

## Stage 9: REFLECT -- Learn + Improve

### Procedure

1. **Write production lessons** to IMPROVEMENT.md (Video Production section):

   Use this structure:
   ```markdown
   ### Pollinate Run: {topic} ({date})

   **What Worked:**
   - {observation with specific evidence}

   **What Failed:**
   - {observation with specific evidence}

   **Process Insights:**
   - {observation about pipeline efficiency}
   ```

2. **Update user_prefs.json** with learned preferences:
   - Record color/font/speed choices that worked (user approved without changes)
   - Record component combinations that the user kept as-is
   - Per-domain style patterns (e.g., "AI architecture -> dark theme + FlowChart heavy")
   - Add entry to `learning_history` array:
     ```json
     {
       "run_id": "run_p_{id}",
       "topic": "...",
       "domain": "...",
       "learned": ["dark theme works for architecture", "6 sections optimal"],
       "date": "..."
     }
     ```

3. **Log to DailyActivity:**
   - Production record with topic, domain, duration, confidence score
   - Link to REPORT.md

4. **Pattern extraction** (after >= 3 runs):
   - Which domains produce best content?
   - Average production time per domain?
   - Which components get most "keep as-is"?
   - Which sections always need revision? (-> improve templates)

5. **Checklist maintenance** -- if any post-pipeline review or user feedback
   found issues the pipeline missed:
   a. Classify each missed issue: does it fit an existing RP-V pattern?
   b. If yes -> investigate why the pattern check missed it
   c. If no -> propose adding a new RP-V pattern to REVIEW_PATTERNS.md
   d. Document the proposed addition in IMPROVEMENT.md

### Decisions

| Decision | Classification | Default |
|----------|---------------|---------|
| Which patterns to record | Mechanical | Record all observations |
| Style preference updates | Mechanical | Record approved choices |
| New RP-V pattern proposal | Taste | Propose if gap identified |

### Verification Gate

Before marking pipeline COMPLETE, ALL must be true:
- [ ] IMPROVEMENT.md updated with What Worked / What Failed / Process sections
- [ ] `user_prefs.json` has new `learning_history` entry for this run
- [ ] DailyActivity logged with production record
- [ ] All observations are specific (not generic like "went well")

### Anti-Rationalization

| Shortcut | Required Response |
|----------|-------------------|
| "Nothing to learn from this run" | Every run teaches something. Review decision log for patterns. |
| "Preferences haven't changed" | Check if any taste decisions were made. Each one is a potential preference update. |
| "Skip DailyActivity, report is enough" | DailyActivity feeds cross-session learning. Log it. |
| "Pattern extraction needs more runs" | Record observations even before 3 runs. Early data is valuable. |

### Max Retries

1. After exhaustion -> checkpoint.

### Output Files

- Updated IMPROVEMENT.md (appended Video Production section)
- Updated `user_prefs.json` (new learning_history entry)
- DailyActivity log entry

---

## Pipeline Completion

After REFLECT stage, present the completion summary:

```
Pollinate COMPLETE (run_p_{id}) -- 9 stages, 0 skipped, 0 escalations
Confidence: {score}/10

  Artifacts:
    evaluation    -> evaluation.json (GO, ROI {X.X})
    research      -> research.md (thesis: "{one-liner}")
    strategy      -> PRFAQ.md + strategy.json (channel matrix)
    content_pkg   -> content_package.md ({N} key points)
    tracks        -> video/, narrative/, poster/, shorts/
    deliver       -> per-channel publish packages
    report        -> REPORT.md

  Quality: {N}/12 RP-V checks passed, all platform specs validated
  Decisions: {X} mechanical, {Y} taste (all approved), {Z} judgment
  Lessons: {N} written to IMPROVEMENT.md

  Report: content/{name}/REPORT.md
```

---

## Rules

1. **Execute inline, never invoke skills.** You ARE the pipeline. Run each
   stage's behavior directly. Do not use `/evaluate` or `/qa` as slash commands.

2. **Studio preview is MANDATORY.** NEVER render 4K until user explicitly
   confirms in the Studio preview. This gate cannot be bypassed by any
   automation, any shortcut, or any rationalization. The user says "render 4K"
   or the pipeline does not advance.

3. **Classify every decision.** No unclassified decisions. If unsure, default
   to "taste" (surface at delivery gate rather than block or ignore).

4. **Save timing before concat.** timing.json + SRT must be saved before
   audio concatenation. If the concat step fails, timing data would be lost
   without this safeguard.

5. **Copy patterns, don't simplify.** When using code from video-podcast-maker
   or templates, copy the ENTIRE pattern including edge case handling. Do not
   "simplify" by removing error handling or fallback paths. See design doc
   Section 9 for migration rules.

6. **Never loop forever.** Respect max_retries per stage. After exhaustion,
   checkpoint. Three attempts at the same stage is enough.

7. **Taste decisions batch at delivery.** Don't interrupt the user mid-pipeline
   for taste decisions. Accumulate them, present once at the Delivery Gate.

8. **Always generate REPORT.md.** Every pipeline run produces a markdown
   report at `content/{name}/REPORT.md`. This is the permanent record.

9. **Brand consistency is non-negotiable.** Apply `brand/identity.yaml`
   colors, fonts, and voice configuration in every output. Swarm Orange is
   #FF6B35, not #FF6C36, not "close enough." PingFang SC, not a substitute.

10. **Platform specs are non-negotiable.** `check_specs.py` must pass for
    every target platform before delivery. No exceptions, no "mostly passes,"
    no manual overrides.
