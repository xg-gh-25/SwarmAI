---
name: Whisper Transcribe
description: >
  Transcribe audio and video files to text using OpenAI Whisper API or local Whisper CLI.
  TRIGGER: "transcribe", "transcription", "speech to text", "convert audio to text", "meeting recording", "voice note".
  DO NOT USE: for text-to-speech (use podcast-gen), music recognition, or real-time transcription.
---

# Whisper Transcribe

**Why?** Turn meeting recordings, voice notes, interviews, and any audio/video into searchable text. Supports 50+ languages with automatic detection.

---

## Quick Start

```
"Transcribe this recording" + file path -> text transcript
"Transcribe and summarize the meeting" -> transcript + key points
```

---

## Tool Detection

This skill adapts to available transcription tools:

| Priority | Tool | Detection | Quality | Cost |
|----------|------|-----------|---------|------|
| 1 | OpenAI Whisper API | `OPENAI_API_KEY` env var | Best, fast | ~$0.006/min |
| 2 | Local Whisper CLI | `which whisper` | Great, slower | Free (local GPU/CPU) |
| 3 | None available | Neither found | -- | -- |

At skill start, detect which is available:

```bash
# Check for API key
[ -n "$OPENAI_API_KEY" ] && echo "API available" || echo "No API key"

# Check for local whisper
which whisper 2>/dev/null && echo "Local whisper available" || echo "No local whisper"
```

If neither is available, guide the user:
- **Fastest setup:** Set `OPENAI_API_KEY` environment variable
- **Free setup:** `brew install openai-whisper` (requires Python, downloads models on first run)

---

## Workflow

### Step 1: Identify Input File

Accept:
- Audio files: `.mp3`, `.m4a`, `.wav`, `.ogg`, `.flac`, `.webm`
- Video files: `.mp4`, `.mov`, `.avi`, `.mkv` (audio track extracted)
- URLs: Download first, then transcribe

Verify the file exists:
```bash
ls -lh "/path/to/file"
```

Check duration (helps estimate time/cost):
```bash
ffprobe -v quiet -show_entries format=duration -of csv=p=0 "/path/to/file" 2>/dev/null
```

If ffprobe is not available, skip duration check and proceed.

### Step 2: Check File Size

OpenAI API limit: **25 MB per request**

```bash
stat -f%z "/path/to/file" 2>/dev/null || stat -c%s "/path/to/file" 2>/dev/null
```

If file > 25MB and using API:
1. Split with ffmpeg (if available):
   ```bash
   # Split into 10-minute chunks
   ffmpeg -i input.mp3 -f segment -segment_time 600 -c copy chunk_%03d.mp3
   ```
2. Transcribe each chunk sequentially
3. Concatenate results

### Step 3: Transcribe

#### Option A: OpenAI Whisper API

```bash
curl -s https://api.openai.com/v1/audio/transcriptions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: multipart/form-data" \
  -F file="@/path/to/audio.m4a" \
  -F model="whisper-1" \
  -F response_format="verbose_json"
```

**Optional parameters:**
- `-F language="en"` -- Force language (ISO 639-1 code). Improves accuracy if known.
- `-F prompt="Meeting about Q1 roadmap with Alice and Bob"` -- Provide context for better accuracy on names, jargon, acronyms.
- `-F response_format="srt"` -- Get subtitles with timestamps.
- `-F response_format="text"` -- Plain text only.
- `-F temperature=0` -- More deterministic output.

**Response formats:**
| Format | Use When |
|--------|----------|
| `text` | Just need the words |
| `verbose_json` | Need timestamps, language detection, segments |
| `srt` | Creating subtitles |
| `vtt` | Web video subtitles |

#### Option B: Local Whisper CLI

```bash
whisper "/path/to/audio.m4a" \
  --model medium \
  --output_format txt \
  --output_dir /tmp/whisper_output
```

**Model selection:**
| Model | Speed | Accuracy | VRAM | Use When |
|-------|-------|----------|------|----------|
| tiny | Fastest | Basic | ~1GB | Quick draft, short clips |
| base | Fast | Good | ~1GB | Short recordings, clear audio |
| small | Medium | Better | ~2GB | Most recordings |
| medium | Slow | Great | ~5GB | Important recordings, accented speech |
| large | Slowest | Best | ~10GB | Critical transcripts, difficult audio |
| turbo | Fast | Great | ~6GB | Default -- best speed/quality balance |

Default to `turbo` if unsure. Use `medium` or `large` for non-English or noisy audio.

### Step 4: Save Output

Save transcript to:
```
~/.swarm-ai/SwarmWS/Knowledge/Notes/transcripts/
```

Filename format: `YYYY-MM-DD-<descriptive-name>.md`

```markdown
# Transcript: {description}

**Source:** {filename}
**Date:** {date}
**Duration:** {duration}
**Language:** {detected language}

---

{transcript text}

---

*Transcribed by SwarmAI on YYYY-MM-DD using {Whisper API / Local Whisper}*
```

### Step 5: Post-Processing (Optional)

Offer based on content:

| Offer | When |
|-------|------|
| "Want a summary?" | Recording > 5 minutes |
| "Want me to extract action items?" | Sounds like a meeting |
| "Want speaker labels?" | Multiple voices detected |
| "Want me to clean up the text?" | Filler words, false starts present |
| "Want subtitles (SRT)?" | Video file input |

**Summary generation:** Use the transcript text and summarize key points, decisions, and action items.

**Text cleanup:** Remove filler words (um, uh, like, you know), fix obvious transcription errors, add paragraph breaks.

---

## Language Support

Whisper auto-detects language. Override with `language` parameter if detection is wrong.

Common language codes:
| Language | Code | Language | Code |
|----------|------|----------|------|
| English | en | Japanese | ja |
| Chinese | zh | Korean | ko |
| Spanish | es | French | fr |
| German | de | Portuguese | pt |

---

## Video File Handling

For video files, extract audio first (if ffmpeg available):

```bash
ffmpeg -i input.mp4 -vn -acodec libmp3lame -q:a 4 /tmp/audio_extract.mp3
```

Then transcribe the extracted audio. This is faster and uses less bandwidth for API calls.

If ffmpeg is not available, the OpenAI API and local Whisper can both accept video files directly (they extract audio internally), but file size limits still apply.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "OPENAI_API_KEY not set" | Export key: `export OPENAI_API_KEY=sk-...` or add to shell profile |
| File too large (>25MB) | Split with ffmpeg or use local Whisper (no size limit) |
| Wrong language detected | Add `-F language="xx"` parameter |
| Names/jargon mangled | Add `-F prompt="..."` with expected names and terms |
| Local whisper very slow | Use smaller model (`tiny`/`base`) or switch to API |
| "whisper: command not found" | `brew install openai-whisper` or `pip install openai-whisper` |
| ffmpeg not available | `brew install ffmpeg` -- needed for splitting and video extraction |
| API returns 429 (rate limit) | Wait 60s and retry, or use local Whisper |

---

## Cost Estimation (API)

| Duration | Estimated Cost |
|----------|---------------|
| 1 minute | ~$0.006 |
| 10 minutes | ~$0.06 |
| 1 hour | ~$0.36 |
| 3 hours | ~$1.08 |

Mention cost estimate to user before transcribing long files (> 30 minutes).

---

## Quality Rules

- Always confirm the file exists and show duration before transcribing
- For files > 30 min, show cost estimate (API) or time estimate (local)
- Save transcripts to the standard output location
- Offer post-processing appropriate to the content type
- Never assume language -- let Whisper detect or ask the user
- For meetings, always offer to extract action items
- Include source filename and date in the saved transcript
