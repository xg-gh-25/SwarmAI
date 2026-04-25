"""Amazon Polly TTS backend for Pollinate (self-contained).

Uses existing AWS SSO credentials (same as voice_synthesize.py).
Default voice: Ruth (generative, en-US) / Zhiyu (neural, cmn-CN).

Polly provides word-level speech marks for precise subtitle timing.
Generative engine gives natural, conversational speech — same voice
used in Swarm's voice conversation mode.

SSML features (podcast-tuned, slower pacing than voice conversation):
- <phoneme> for Chinese polyphone disambiguation (16 curated entries)
- <say-as> for dates, phone numbers, large cardinals
- <sub> for abbreviation expansion (API→A P I, AWS→A W S, etc.)
- <lang xml:lang="en-US"> wrapping for English terms in Chinese narration
- <break> tags after sentences for natural pacing (500ms sentence, 250ms clause)
- 150ms boundary pauses at CJK↔English transitions
- <prosody> for speech rate control
- XML escaping for user text safety

Battle-tested patterns preserved from video-podcast-maker:
- 3-attempt retry with linear backoff (2s, 4s)
- Resume via ffprobe validation
- Accumulated duration tracking for absolute word boundary offsets
- 48kHz mono WAV normalization via ffmpeg

NOTE: SSML utility functions (_apply_phonemes, _format_numbers, etc.) are
duplicated from core/voice_synthesize.py intentionally — skills must be
self-contained with no cross-module dependencies.
"""
import json
import os
import re
import struct
import subprocess
import time
from functools import lru_cache

from .base import check_resume

# Voice mapping: matches core/voice_synthesize.py VOICE_MAP
# (voice_id, engine, polly_language_code)
VOICE_MAP = {
    "en-US": ("Ruth", "generative", "en-US"),
    "en-GB": ("Amy", "generative", "en-GB"),
    "zh-CN": ("Zhiyu", "neural", "cmn-CN"),       # generative not available
    "ja-JP": ("Kazuha", "neural", "ja-JP"),        # generative not available
    "ko-KR": ("Seoyeon", "generative", "ko-KR"),
}

# Polly neural/generative max text length
MAX_TEXT_LENGTH = 3000


@lru_cache(maxsize=1)
def _get_polly_client(region=None):
    """Lazy singleton Polly client — same credential chain as voice_synthesize.py."""
    import boto3
    region = region or os.environ.get("TRANSCRIBE_REGION", "us-east-1")
    return boto3.client("polly", region_name=region)


def _get_voice_config(language: str) -> tuple:
    """Return (voice_id, engine, polly_language_code) for language."""
    if language in VOICE_MAP:
        return VOICE_MAP[language]
    # Prefix match
    prefix = language.split("-")[0]
    for lang_code, info in VOICE_MAP.items():
        if lang_code.startswith(prefix):
            return info
    return VOICE_MAP["en-US"]  # fallback


def _get_speech_marks(client, text: str, voice_id: str, engine: str,
                      language_code: str, text_type: str = "text") -> list:
    """Get word-level speech marks from Polly.

    IMPORTANT: text_type must match the format used for audio synthesis.
    If audio is synthesized with TextType='ssml', marks must also use 'ssml'
    on the same SSML string. Otherwise timing will drift because <break>
    tags add duration to audio but not to plain-text marks.

    Returns list of {time (ms), type, start, end, value} dicts.
    """
    try:
        response = client.synthesize_speech(
            Text=text,
            TextType=text_type,
            OutputFormat="json",  # speech marks format
            VoiceId=voice_id,
            Engine=engine,
            LanguageCode=language_code,
            SpeechMarkTypes=["word"],
        )
        # Polly returns newline-delimited JSON
        marks_text = response["AudioStream"].read().decode("utf-8")
        marks = []
        for line in marks_text.strip().split("\n"):
            if line.strip():
                marks.append(json.loads(line))
        return marks
    except Exception as e:
        # Speech marks are optional — fall back to no word boundaries
        print(f"  ⚠ Speech marks failed: {e} (will use approximate timing)")
        return []


# ---------------------------------------------------------------------------
# SSML utility functions (self-contained, duplicated from core/voice_synthesize.py)
# Skills must be self-contained — no cross-module dependencies.
# ---------------------------------------------------------------------------

# Chinese polyphones: only words where Polly's default reading is wrong.
POLYPHONE_MAP = {
    "重点": "zhong4dian3", "重新": "chong2xin1", "重要": "zhong4yao4",
    "重复": "chong2fu4",  "行业": "hang2ye4",    "行为": "xing2wei2",
    "长期": "chang2qi1",  "长大": "zhang3da4",    "了解": "liao3jie3",
    "得到": "de2dao4",    "地方": "di4fang1",     "数据": "shu4ju4",
    "还是": "hai2shi4",   "差不多": "cha4bu4duo1", "处理": "chu3li3",
    "调整": "tiao2zheng3",
}

# Common abbreviations Polly misreads in CJK context.
ABBREVIATION_MAP = {
    "AWS": "A W S", "API": "A P I", "SDK": "S D K", "UI": "U I",
    "URL": "U R L", "SQL": "S Q L", "CSS": "C S S", "HTML": "H T M L",
    "AI": "A I",   "LLM": "L L M", "TTS": "T T S", "STT": "S T T",
}

_PHONE_RE = re.compile(r'(\d{3}[-\s]?\d{4}[-\s]?\d{4})')
_DATE_RE = re.compile(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})')
_CARDINAL_RE = re.compile(r'(\d{1,3}(?:,\d{3})+)')


def _apply_phonemes(text: str) -> str:
    """Replace known polyphones with <phoneme> SSML tags (x-amazon-pinyin)."""
    for word, pinyin in POLYPHONE_MAP.items():
        if word in text:
            text = text.replace(
                word,
                f'<phoneme alphabet="x-amazon-pinyin" ph="{pinyin}">{word}</phoneme>',
            )
    return text


def _format_numbers(text: str) -> str:
    """Wrap numbers and dates in <say-as> for correct reading."""
    text = _PHONE_RE.sub(r'<say-as interpret-as="telephone">\1</say-as>', text)
    text = _DATE_RE.sub(r'<say-as interpret-as="date" format="ymd">\1</say-as>', text)
    text = _CARDINAL_RE.sub(r'<say-as interpret-as="cardinal">\1</say-as>', text)
    return text


def _expand_abbreviations(text: str) -> str:
    """Expand abbreviations via <sub>. CJK-aware boundary (not word-boundary)."""
    for abbr, expansion in ABBREVIATION_MAP.items():
        text = re.sub(
            rf'(?<![A-Za-z]){abbr}(?![A-Za-z])',
            f'<sub alias="{expansion}">{abbr}</sub>',
            text,
        )
    return text


def _add_language_switch_breaks(ssml: str) -> str:
    """Add 150ms micro-pauses at CJK-English switch boundaries."""
    ssml = re.sub(r'(</(?:lang|sub|say-as)>)(\s*[一-鿿㐀-䶿])', r'\1<break time="150ms"/>\2', ssml)
    ssml = re.sub(r'([一-鿿㐀-䶿]\s*)(<(?:lang|sub|phoneme|say-as))', r'\1<break time="150ms"/>\2', ssml)
    return ssml


def _escape_non_tags(text: str) -> str:
    """XML-escape user text while preserving SSML tags."""
    tag_re = re.compile(
        r'(<(?:lang|/lang|phoneme|/phoneme|say-as|/say-as|sub|/sub|break|prosody|/prosody)[^>]*>)'
    )
    parts = tag_re.split(text)
    out = []
    for part in parts:
        if part.startswith("<") and tag_re.match(part):
            out.append(part)
        else:
            out.append(part.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return "".join(out)


# ---------------------------------------------------------------------------
# Main SSML pipeline (podcast-tuned — slower pacing than voice conversation)
# ---------------------------------------------------------------------------

def _text_to_ssml(text: str, speech_rate: str = "+0%", language: str = "zh-CN") -> str:
    """Convert plain text to SSML with full enhancement pipeline.

    Pipeline (order matters):
    1. Phoneme disambiguation (Chinese polyphones)
    2. Number/date formatting (<say-as>)
    3. Abbreviation expansion (<sub>)
    4. English term <lang> wrapping (protect existing tags first)
    5. XML-escape non-tag portions
    6. Break injection (podcast timing: 500ms sentence, 250ms clause)
    7. Language switch boundary pauses (150ms)
    8. Wrap in <speak> with optional <prosody> rate
    """
    result = text

    # --- Step 1: Phoneme disambiguation (Chinese only) ---
    if language in ("zh-CN", "cmn-CN"):
        result = _apply_phonemes(result)

    # --- Step 2: Number/date formatting ---
    result = _format_numbers(result)

    # --- Step 3: Abbreviation expansion ---
    result = _expand_abbreviations(result)

    # --- Step 4: Wrap English terms for native pronunciation ---
    # Protect SSML tags from steps 1-3 so regex doesn't match attribute
    # names ("alphabet", "interpret") as English words.
    _tag_re = re.compile(r'(<[^>]+>)')
    _tag_map = {}
    _n = [0]

    def _ph(m):
        key = chr(0xE000 + _n[0])
        _tag_map[key] = m.group(0)
        _n[0] += 1
        return key

    result = _tag_re.sub(_ph, result)

    en_pattern = r'\b([A-Za-z][A-Za-z0-9\-\.]*[A-Za-z0-9])\b|\b([A-Za-z]{2,})\b'
    def wrap_en(m):
        term = m.group(1) or m.group(2)
        if not term or len(term) < 2:
            return m.group(0)
        return f'<lang xml:lang="en-US">{term}</lang>'

    result = re.sub(en_pattern, wrap_en, result, flags=re.ASCII)

    for key, tag in _tag_map.items():
        result = result.replace(key, tag)

    # --- Step 5: XML-escape non-tag portions ---
    result = _escape_non_tags(result)

    # --- Step 6: Breaks (podcast timing — slower than conversation) ---
    result = re.sub(r'([。！？])', r'\1<break time="500ms"/>', result)
    result = re.sub(r'([，；：])', r'\1<break time="250ms"/>', result)
    result = re.sub(r'\n\n+', '<break time="800ms"/>', result)
    result = result.replace('\n', '<break time="300ms"/>')

    # --- Step 7: Language switch boundary pauses ---
    result = _add_language_switch_breaks(result)

    # --- Step 8: Wrap in <speak> with prosody ---
    rate_attr = ""
    if speech_rate and speech_rate != "+0%":
        rate_attr = f' rate="{speech_rate}"'

    if rate_attr:
        ssml = f'<speak><prosody{rate_attr}>{result}</prosody></speak>'
    else:
        ssml = f'<speak>{result}</speak>'

    return ssml


def synthesize(chunks, config, output_dir, resume=False):
    """Synthesize using Amazon Polly with SSML, word boundary tracking.

    config keys: voice, speech_rate, language, phoneme_dict
    Returns: (part_files, word_boundaries, accumulated_duration)
    """
    language = config.get("language", "zh-CN")
    speech_rate = config.get("speech_rate", "+0%")
    voice_override = config.get("voice")

    if voice_override:
        # Find engine + language_code for this voice
        voice_id = voice_override
        engine = "generative"
        polly_lang = language
        for _lc, (v, e, plc) in VOICE_MAP.items():
            if v == voice_override:
                engine = e
                polly_lang = plc
                break
    else:
        voice_id, engine, polly_lang = _get_voice_config(language)

    client = _get_polly_client()
    part_files = []
    word_boundaries = []
    accumulated_duration = 0

    for i, chunk in enumerate(chunks):
        part_file = os.path.join(output_dir, f"part_{i}.wav")
        part_files.append(part_file)

        # Resume support: skip if valid audio exists
        if resume:
            dur = check_resume(part_file)
            if dur is not None:
                print(f"  ⏭ Part {i + 1}/{len(chunks)} skipped (resume, {dur:.1f}s)")
                accumulated_duration += dur
                continue

        # Convert plain text to SSML for natural pacing + English pronunciation
        ssml_text = _text_to_ssml(chunk, speech_rate, language)

        # 3-attempt retry with linear backoff
        success = False
        for attempt in range(1, 4):
            try:
                # Synthesize audio (MP3) with SSML
                response = client.synthesize_speech(
                    Text=ssml_text,
                    TextType="ssml",
                    OutputFormat="mp3",
                    VoiceId=voice_id,
                    Engine=engine,
                    LanguageCode=polly_lang,
                )
                audio_bytes = response["AudioStream"].read()

                if not audio_bytes:
                    raise RuntimeError("No audio data received")

                # Write MP3 and convert to 48kHz mono WAV
                mp3_file = part_file.replace(".wav", ".mp3")
                with open(mp3_file, "wb") as f:
                    f.write(audio_bytes)

                subprocess.run(
                    ["ffmpeg", "-y", "-i", mp3_file, "-ar", "48000", "-ac", "1", part_file],
                    capture_output=True,
                )
                os.remove(mp3_file)

                # Get duration via ffprobe
                probe = subprocess.run(
                    ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "csv=p=0", part_file],
                    capture_output=True, text=True,
                )
                chunk_duration = float(probe.stdout.strip()) if probe.stdout.strip() else 0

                # Get word-level speech marks for timing
                # MUST use same SSML text as audio synthesis — plain text marks
                # won't include <break> durations, causing subtitle drift.
                marks = _get_speech_marks(client, ssml_text, voice_id, engine, polly_lang, text_type="ssml")
                if marks:
                    # Convert Polly speech marks to our universal format.
                    # Marks are from SSML text so timing matches audio exactly.
                    # For punctuation injection, we find each mark's word value
                    # in the original plain text and check what punctuation
                    # follows it — this avoids dealing with SSML tag offsets.
                    #
                    # Build a position tracker: walk through original chunk text
                    # and for each mark, find the word and any trailing punctuation.
                    plain_pos = 0  # current scan position in original chunk

                    for j, mark in enumerate(marks):
                        word_value = re.sub(r'<[^>]+/?>', '', mark["value"]).strip()
                        if not word_value:
                            continue  # Pure SSML tag, no text

                        # Find this word in original text starting from plain_pos
                        word_idx = chunk.find(word_value, plain_pos)
                        if word_idx >= 0:
                            # Check for punctuation between previous word end and this word start
                            if word_idx > plain_pos:
                                gap = chunk[plain_pos:word_idx]
                                for ch in gap:
                                    if ch in "，。！？、：；,.\n":
                                        word_boundaries.append({
                                            "text": ch,
                                            "offset": accumulated_duration + mark["time"] / 1000.0,
                                            "duration": 0,
                                        })
                            # Advance past this word
                            plain_pos = word_idx + len(word_value)
                        # else: word not found in plain text (shouldn't happen)

                        offset_s = mark["time"] / 1000.0  # Polly uses milliseconds
                        # Estimate duration: gap to next mark, or 0.2s for last
                        if j + 1 < len(marks):
                            dur = (marks[j + 1]["time"] - mark["time"]) / 1000.0
                        else:
                            dur = 0.2
                        # Strip SSML tags from value — SSML marks may include
                        # <break>, <lang> etc. in the value field
                        clean_val = re.sub(r'<[^>]+/?>', '', mark["value"]).strip()
                        if not clean_val:
                            continue  # Skip pure-tag entries (standalone <break>)
                        word_boundaries.append({
                            "text": clean_val,
                            "offset": accumulated_duration + offset_s,
                            "duration": dur,
                        })
                else:
                    # Fallback: uniform distribution across characters
                    chars = [c for c in chunk if c.strip()]
                    if chars and chunk_duration > 0:
                        per = chunk_duration / len(chars)
                        for idx, ch in enumerate(chars):
                            word_boundaries.append({
                                "text": ch,
                                "offset": accumulated_duration + idx * per,
                                "duration": per,
                            })

                print(f"  ✓ Part {i + 1}/{len(chunks)} done ({len(chunk)} chars, {chunk_duration:.1f}s) [Polly {voice_id}/{engine}]")
                accumulated_duration += chunk_duration
                success = True
                break

            except Exception as e:
                print(f"  ✗ Part {i + 1} failed (attempt {attempt}/3): {e}")
                if attempt < 3:
                    time.sleep(attempt * 2)  # Linear backoff: 2s, 4s

        if not success:
            raise RuntimeError(f"Part {i + 1} synthesis failed after 3 attempts")

    return part_files, word_boundaries, accumulated_duration
