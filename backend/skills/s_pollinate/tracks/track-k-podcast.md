# Track K: Podcast — Two-Host Audio Content

> Transform content_package into an engaging two-host podcast dialogue. Generates
> structured script + synthesized MP3 audio (when TTS available). Commute-friendly
> format for audiences who prefer listening over reading.

## When This Track Runs

This track executes during BUILD stage when `"podcast"` is in
`confirmed_tracks` (from discovery.json). Read this file at BUILD time — you do NOT need the full INSTRUCTIONS.md.

## Inputs Required

- `content/{name}/content_package.md` — Narrative Layer (arc, transitions, hooks) + Core Layer
- `content/{name}/discovery.json` — audience, duration target, tone
- `content/{name}/PRFAQ.md` — press release (source for key claims)

## Key Principle: Spoken ≠ Written

A podcast script is NOT the article read aloud. It's a **conversation** between
two hosts who bring different perspectives:

| Written Content (Track C) | Spoken Content (Track K) |
|--------------------------|------------------------|
| Long complex sentences | Short sentences (<20 words) |
| Technical jargon OK | Jargon explained via dialogue |
| Passive voice acceptable | Active voice mandatory |
| Reader controls pace | Speaker controls pace |
| Can re-read | Must understand first time |
| Structure via headers | Structure via host transitions |

---

## Production Flow

```
Step 1: Determine podcast parameters from discovery context

Step 2: Extract narrative arc from content_package.md
        - Hook (why listen?) from Narrative Layer
        - Key points from Core Layer (reordered for oral flow)
        - Evidence/quotes from Evidence Layer (make them conversational)

Step 3: Generate two-host dialogue script
        - Host A = lead (drives topic forward)
        - Host B = color (challenges, asks "why?", provides analogies)
        - Natural turn-taking with reactions

Step 4: Detect TTS tool and generate audio (or export script only)

Step 5: Quality verification (RP-K)
```

---

## Step 1: Podcast Parameters

From `discovery.json` and context:

| Parameter | Default | Detection |
|-----------|---------|-----------|
| **Duration** | Medium (8-12 min) | "short" → 3-5min, "long" → 15-20min |
| **Language** | Match content_package | Chinese content → Chinese hosts |
| **Tone** | Casual-professional | Match audience (leadership = professional, community = casual) |
| **Host names** | Alex & Jamie (EN) / 小明 & 小红 (CN) | User override in discovery |

**Word count targets by duration:**

| Duration | Words (EN) | Characters (CN) | Dialogue turns |
|----------|-----------|----------------|---------------|
| Short (3-5 min) | 600-1000 | 1500-2500 | 15-25 |
| Medium (8-12 min) | 1600-2400 | 4000-6000 | 40-60 |
| Long (15-20 min) | 3000-4000 | 7500-10000 | 75-100 |

---

## Step 2: Narrative Arc Extraction

From `content_package.md` Narrative Layer:

1. **Hook** — the opening question or surprising fact that earns attention
2. **Setup** — context that the audience needs before the core argument
3. **Development** — the 3-5 key points, reordered for oral flow:
   - Start with most surprising/counterintuitive
   - Build to the "aha" moment
   - End with practical takeaway
4. **Evidence** — specific data/quotes woven into dialogue (not dumped)
5. **Resolution** — what the listener should think/do differently

**Reordering rule:** Written articles often build from foundation → conclusion.
Podcasts START with the conclusion (hook) then explain WHY. Invert the pyramid.

---

## Step 3: Generate Dialogue Script

Output format: `content/{name}/tracks/podcast/script.json`

```json
{
  "title": "Episode Title — Subtitle",
  "description": "One-line episode summary for show notes",
  "duration_target": "medium",
  "language": "en",
  "hosts": {
    "host_a": {"name": "Alex", "voice_id": "en-US-GuyNeural", "role": "lead"},
    "host_b": {"name": "Jamie", "voice_id": "en-US-JennyNeural", "role": "color"}
  },
  "dialogue": [
    {"host": "host_a", "text": "So here's what nobody's talking about..."},
    {"host": "host_b", "text": "Wait, really? I thought..."},
    {"host": "host_a", "text": "That's exactly the misconception. Let me explain..."}
  ],
  "show_notes": [
    "Key point 1: ...",
    "Key point 2: ...",
    "Resources: ..."
  ]
}
```

**Dialogue writing rules:**

| Rule | Example | Why |
|------|---------|-----|
| Short sentences (<20 words) | "That's wild. So what does this mean for us?" | TTS-friendly, natural |
| Reactions are mandatory | "Hmm." / "Wait—" / "That's interesting." | Feels like real conversation |
| Host B challenges | "But couldn't you argue that..." | Creates tension, depth |
| No wall-of-text monologues | Max 3 sentences per turn | Keeps listener engaged |
| Explain jargon via dialogue | B: "MCP?" A: "Model Context Protocol — basically..." | Natural education |
| Data via story | "...and when we measured it, 96% of the time..." | Not: "The recall rate is 96.6%" |
| Callbacks to earlier points | "Remember what you said about X? This connects..." | Creates coherence |
| Natural transitions | "Speaking of which..." / "That reminds me..." | Not: "Moving on to point 3" |

**Chinese dialogue rules (if language = CN):**

| Rule | Example |
|------|---------|
| Use 口语 not 书面语 | "这个挺有意思的" not "这是一个有趣的现象" |
| Sentence-final particles | "对吧", "嗯", "真的假的" |
| Length: 15 characters max per clause | Natural breathing rhythm |
| Avoid English-heavy mixing | Use Chinese equivalents unless the English term is industry standard |

---

## Step 4: TTS Detection & Audio Generation

Detect available tools in priority order:

| Priority | Tool | Detection | Voice Quality | Cost |
|----------|------|-----------|---------------|------|
| 1 | Edge TTS | `pip show edge-tts` | Good, many voices | Free |
| 2 | OpenAI TTS | `OPENAI_API_KEY` env | High, natural | ~$0.015/1K chars |
| 3 | Amazon Polly | AWS credentials + `boto3` | Good, SSML support | ~$0.004/1K chars |
| 4 | macOS `say` | `which say` (macOS only) | Basic, fast | Free |
| 5 | **Script-only export** | Always available | N/A | N/A |

**If TTS available (Priority 1-4):**

```bash
# Per-host audio generation
for each dialogue turn:
    generate_audio(text, voice_id) → segment_{n}.mp3

# Concatenation
ffmpeg -i "concat:segment_1.mp3|segment_2.mp3|..." -c copy output.mp3

# Or via edge-tts:
edge-tts --voice "en-US-GuyNeural" --text "..." --write-media segment.mp3
```

**Voice assignment by language:**

| Language | Host A (Lead) | Host B (Color) |
|----------|--------------|---------------|
| English | en-US-GuyNeural | en-US-JennyNeural |
| Chinese | zh-CN-YunxiNeural | zh-CN-XiaoxiaoNeural |

**If NO TTS available:**
- Save `script.json` + `transcript.md` (readable version)
- Track is COMPLETE (script IS the deliverable)
- Note in output: "Audio generation requires edge-tts. Install: `pip install edge-tts`"

---

## Step 5: Quality Verification (RP-K)

### RP-K1: Script Completeness

- [ ] Dialogue covers ALL key points from content_package Core Layer
- [ ] Opening has a hook (not a generic intro like "Welcome to the show")
- [ ] Closing has clear takeaway for listener
- [ ] Show notes list all key points + resources mentioned
- [ ] Word/character count within target duration range

### RP-K2: Conversational Quality

- [ ] No monologue > 3 sentences without host switch
- [ ] Host B challenges or questions at least every 4-5 turns
- [ ] Reactions present (not just pure information exchange)
- [ ] No written-language constructions ("Furthermore," / "In conclusion,")
- [ ] Each host has distinct voice (lead = assertive, color = curious)

### RP-K3: TTS Compatibility

- [ ] No special characters that break TTS (em-dashes, unusual punctuation)
- [ ] Abbreviations spelled out or have pronunciation guide
- [ ] Numbers written as words for natural reading ("twenty-five" not "25")
- [ ] Pauses marked with `...` or comma placement (TTS interprets these)
- [ ] No sentences > 200 characters (TTS may clip or rush)

### RP-K4: Duration Accuracy

- [ ] Word count matches target duration (±20%)
- [ ] If audio generated: actual duration within target range
- [ ] Pacing feels natural (not rushed or padded)

### RP-K5: Content Fidelity

- [ ] Claims in dialogue traceable to Evidence Layer
- [ ] No hallucinated data or statistics
- [ ] Technical terms used correctly (Host A explains, Host B verifies)
- [ ] Quotes attributed (if using direct quotes from content)

### RP-K6: Audio Quality (if generated)

- [ ] No clipping or distortion
- [ ] Volume normalized across segments (Host A and B at similar levels)
- [ ] Natural pause between speakers (200-500ms gap)
- [ ] File format: MP3, bitrate ≥128kbps, sample rate 44.1kHz

---

## Output Files

```
content/{name}/tracks/podcast/
├── script.json           — structured dialogue (ALWAYS produced)
├── transcript.md         — human-readable transcript (ALWAYS produced)
├── {topic}-podcast.mp3   — generated audio (if TTS available)
├── show_notes.md         — episode description + key points + resources
└── generation_log.json   — TTS tool used, voice IDs, duration
```

## Transcript Format

`transcript.md` is the human-readable version of the script:

```markdown
# {title}

**Duration:** ~{N} minutes | **Hosts:** {Host A name} & {Host B name}

---

**{Host A}:** So here's what nobody's talking about...

**{Host B}:** Wait, really? I thought the whole point was...

**{Host A}:** That's exactly the misconception. Let me break it down...

---

## Show Notes

- Key point 1: [description]
- Key point 2: [description]
- Referenced: [links/sources]
```
