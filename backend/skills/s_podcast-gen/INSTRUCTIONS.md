# Podcast Generation

Convert articles, reports, documentation, or any text content into engaging two-host podcast scripts, then synthesize audio using available TTS tools.

## Output Location

Save scripts and audio to:
```
~/.swarm-ai/SwarmWS/Knowledge/Notes/podcasts/
```

Files per episode:
- `YYYY-MM-DD-<topic>-script.json` -- structured dialogue
- `YYYY-MM-DD-<topic>-transcript.md` -- readable transcript
- `YYYY-MM-DD-<topic>.mp3` -- generated audio (if TTS available)

## Tool Detection

This skill adapts to available TTS tools:

| Priority | Tool | Detection | Quality |
|----------|------|-----------|---------|
| 1 | MCP TTS server | Check for `mcp__*__text_to_speech` tools | Highest |
| 2 | OpenAI TTS API | `OPENAI_API_KEY` env var | High, natural voices |
| 3 | Edge TTS | `which edge-tts` or `pip show edge-tts` | Good, free, many languages |
| 4 | macOS `say` | `which say` (macOS only) | Basic, fast, offline |
| 5 | Script-only export | Always available | No audio -- script for user to produce |

**Recommended setup:** `pip install edge-tts` for free, high-quality, multilingual TTS.

## Workflow

### Step 1: Understand Requirements

| Dimension | Options | Default |
|-----------|---------|---------|
| **Source content** | Article URL, file path, pasted text, or topic | (required) |
| **Language** | English, Chinese, or other | Match source |
| **Duration target** | Short (3-5 min), Medium (8-12 min), Long (15-20 min) | Medium |
| **Tone** | Casual, educational, debate, interview, storytelling | Casual |
| **Host names** | Custom names for the two hosts | Alex & Jamie |

### Step 2: Research & Digest Source

If the source is a URL or topic (not raw text):
1. Fetch and read the full content
2. Extract key points, data, quotes, and interesting angles
3. Identify what would surprise or engage a listener

If the source is a file or pasted text:
1. Read and identify the core narrative
2. Extract the 5-8 most interesting/important points
3. Note any data, quotes, or stories that work well in spoken format

### Step 3: Create Podcast Script

Generate a structured JSON script:

```json
{
  "title": "Episode Title -- Subtitle",
  "description": "One-line episode summary for show notes",
  "hosts": {
    "host_a": {"name": "Alex", "voice": "male", "role": "lead/interviewer"},
    "host_b": {"name": "Jamie", "voice": "female", "role": "expert/color"}
  },
  "dialogue": [
    {"host": "host_a", "text": "Hey everyone, welcome back..."},
    {"host": "host_b", "text": "Thanks Alex, today we're diving into..."},
    ...
  ]
}
```

**Script writing rules:**

| Rule | Why |
|------|-----|
| Natural, conversational language | Sounds human, not read-from-script |
| Short sentences (under 20 words) | Easier to listen to and TTS-friendly |
| No markdown, no URLs, no special characters | TTS engines choke on these |
| No jargon without explanation | Listener might be new to the topic |
| Alternate hosts every 2-4 lines | Keeps energy up |
| Include reactions ("Wow", "Right", "That's wild") | Natural conversation flow |
| Open with a hook, not a summary | Grab attention in first 10 seconds |
| Close with a takeaway + call to action | Memorable ending |

**Duration guidelines:**

| Target Duration | Dialogue Lines | Word Count |
|----------------|---------------|------------|
| Short (3-5 min) | 20-30 | 600-1,000 |
| Medium (8-12 min) | 40-60 | 1,600-2,400 |
| Long (15-20 min) | 70-100 | 2,800-4,000 |

### Step 4: Generate Audio

Based on detected tool:

**Edge TTS (recommended free option):**
```bash
# Generate per-host audio segments, then concatenate
# Host A (male)
edge-tts --voice "en-US-GuyNeural" --text "{text}" --write-media segment_001.mp3

# Host B (female)
edge-tts --voice "en-US-JennyNeural" --text "{text}" --write-media segment_002.mp3

# Concatenate with ffmpeg
ffmpeg -f concat -safe 0 -i segments.txt -c copy output.mp3
```

**Voice options by language:**

| Language | Male Voice | Female Voice |
|----------|-----------|--------------|
| English (US) | en-US-GuyNeural | en-US-JennyNeural |
| English (UK) | en-GB-RyanNeural | en-GB-SoniaNeural |
| Chinese (Mandarin) | zh-CN-YunxiNeural | zh-CN-XiaoxiaoNeural |
| Chinese (Cantonese) | zh-HK-WanLungNeural | zh-HK-HiuGaaiNeural |
| Japanese | ja-JP-KeitaNeural | ja-JP-NanamiNeural |

**OpenAI TTS API:**
```bash
# Using the OpenAI CLI or Python SDK
openai audio speech create --model tts-1-hd --voice onyx --input "{text}" --output segment.mp3
```

**macOS `say` (basic fallback):**
```bash
say -v "Alex" -o segment.aiff "{text}" && ffmpeg -i segment.aiff segment.mp3
```

**No TTS available:**
Save the script JSON and transcript markdown. Tell the user they can:
1. Install `edge-tts` (`pip install edge-tts`) and re-run
2. Paste the script into NotebookLM, ElevenLabs, or similar
3. Record it themselves using the transcript as a guide

### Step 5: Generate Transcript

Create a readable markdown transcript:

```markdown
# {Episode Title}

**Duration:** ~{X} minutes | **Hosts:** {Host A}, {Host B}

---

**{Host A}:** Hey everyone, welcome back...

**{Host B}:** Thanks Alex, today we're diving into...

...

---

*Generated by SwarmAI on YYYY-MM-DD from: {source description}*
```

### Step 6: Deliver

Present to the user:
1. Audio file path (if generated)
2. Transcript file path
3. Offer to adjust: tone, length, host dynamics, specific sections

---

## Advanced Patterns

### Series/Recurring Format
When creating a series:
1. Define recurring intro/outro segments
2. Maintain consistent host personalities across episodes
3. Reference previous episodes naturally
4. Save a series template for reuse

### Interview Style
For interview-format episodes:
- Host A asks questions, Host B is the expert
- Prepare 5-7 questions that build on each other
- Include follow-up prompts: "Can you give an example?" "Why does that matter?"

### Bilingual Episode
For Chinese-English mixed content:
- Primary language for narration
- Switch languages for quotes, terminology, proper nouns
- Use appropriate TTS voice for each language segment

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| TTS sounds robotic | Use Edge TTS neural voices or OpenAI TTS; avoid basic `say` |
| Audio segments have gaps | Reduce silence trimming, add short pause markers between segments |
| Script too long/short | Adjust to duration guidelines, cut/expand middle sections |
| Edge TTS not installed | `pip install edge-tts` -- works on macOS, Linux, Windows |
| ffmpeg not available | `brew install ffmpeg` on macOS |
| Chinese TTS quality poor | Use zh-CN-YunxiNeural (male) or zh-CN-XiaoxiaoNeural (female) -- best quality |

