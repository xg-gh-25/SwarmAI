"""Text-to-speech via Amazon Polly — engine-aware SSML strategy.

Converts text to MP3 audio using Polly Generative voices (female persona).
Falls back to Neural where Generative is unavailable (zh-CN, ja-JP).
Reuses existing AWS SSO credentials (same as Transcribe).

Engine-aware SSML strategy (validated by internal Amazon codebases):
  - Generative engine: plain text, no SSML. The billion-parameter transformer
    produces natural prosody from context. Adding SSML can interfere.
  - Neural engine: rich SSML pipeline (break + phoneme + say-as + sub + lang
    + language switch boundary pauses) to compensate for weaker model.

Public API:
    synthesize_speech(text, voice_id, language, region) → bytes (MP3)
    get_voice_for_language(language) → tuple[str, str, str]
    VOICE_MAP — language → (voice_id, engine, polly_language_code) mapping

Internal (exported for tests):
    _prepare_generative(text) → (text, "text")
    _prepare_neural(text, language) → (ssml, "ssml")
    _apply_phonemes(text) → text with <phoneme> tags
    _format_numbers(text) → text with <say-as> tags
    _expand_abbreviations(text) → text with <sub> tags
    _add_language_switch_breaks(ssml) → ssml with boundary pauses
"""

import asyncio
import logging
import os
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# Voice mapping: BCP-47 language_code → (voice_id, engine, polly_language_code)
# Persona: female voice, generative engine where available for natural speech.
# Polly uses cmn-CN (not zh-CN) and requires matching LanguageCode per voice.
VOICE_MAP: dict[str, tuple[str, str, str]] = {
    "en-US": ("Ruth", "generative", "en-US"),
    "en-GB": ("Amy", "generative", "en-GB"),
    "zh-CN": ("Zhiyu", "neural", "cmn-CN"),       # generative not available
    "ja-JP": ("Kazuha", "neural", "ja-JP"),        # generative not available
    "ko-KR": ("Seoyeon", "generative", "ko-KR"),
    "de-DE": ("Vicki", "generative", "de-DE"),
    "fr-FR": ("Lea", "generative", "fr-FR"),
    "es-ES": ("Lucia", "generative", "es-ES"),
}

DEFAULT_VOICE = ("Ruth", "generative", "en-US")

# Polly neural engine limit
MAX_TEXT_LENGTH = 3000

# Default AWS region — reuse TRANSCRIBE_REGION or fall back to us-east-1
DEFAULT_REGION = os.environ.get("TRANSCRIBE_REGION", "us-east-1")


@lru_cache(maxsize=1)
def _get_polly_client():
    """Lazy singleton Polly client — created once, cached.

    Always uses DEFAULT_REGION. The lru_cache(maxsize=1) is safe because
    there's no region parameter to vary — one client per process lifetime.

    Uses existing AWS SSO credentials (same credential chain as Transcribe).
    """
    import boto3
    return boto3.client("polly", region_name=DEFAULT_REGION)


def get_voice_for_language(language: str) -> tuple[str, str, str]:
    """Return (voice_id, engine, polly_language_code) for a BCP-47 language code.

    Tries exact match first, then prefix match (e.g., "en" matches "en-US").
    Falls back to DEFAULT_VOICE if no match found.

    Args:
        language: BCP-47 language code (e.g., "en-US", "zh-CN", "ja-JP")

    Returns:
        Tuple of (voice_id, engine, polly_language_code) for Amazon Polly
    """
    # Exact match
    if language in VOICE_MAP:
        return VOICE_MAP[language]

    # Prefix match (e.g., "en" → "en-US")
    prefix = language.split("-")[0] if "-" in language else language
    for lang_code, voice_info in VOICE_MAP.items():
        if lang_code.startswith(prefix):
            return voice_info

    return DEFAULT_VOICE


# Languages where English terms need explicit <lang> wrapping.
# CJK voices read "API" as individual Chinese/Japanese/Korean characters otherwise.
_CJK_LANGUAGES = {"zh-CN", "ja-JP", "ko-KR", "cmn-CN"}

# Minimum length for English terms to wrap (skip 1-char like "I", "a")
_MIN_EN_TERM_LEN = 2

# Regex for English words/acronyms in CJK text (2+ chars, allows hyphens/dots)
_EN_TERM_RE = re.compile(
    r'[A-Za-z][A-Za-z0-9\-\.]*[A-Za-z0-9]|[A-Za-z]{2,}',
    re.ASCII,
)


def _wrap_english_terms(text: str) -> str:
    """Wrap English terms in SSML <lang> tags for native pronunciation.

    In CJK text, Polly reads "API" as three Chinese characters (ā-pī-ài).
    Wrapping in <lang xml:lang="en-US"> switches to the English phoneme engine.

    Only applies to terms with 2+ ASCII letters. Single characters and numbers
    embedded in Chinese (like "第3个") are left alone.
    """
    def _wrap(match: re.Match) -> str:
        term = match.group(0)
        if len(term) < _MIN_EN_TERM_LEN:
            return term
        return f'<lang xml:lang="en-US">{term}</lang>'

    return _EN_TERM_RE.sub(_wrap, text)


def _to_ssml(text: str, language: str) -> tuple[str, str]:
    """Convert text to SSML if the language benefits from it.

    Returns (text_or_ssml, text_type) where text_type is "ssml" or "text".
    Only CJK languages get SSML wrapping — English/European languages already
    pronounce English terms correctly.
    """
    if language not in _CJK_LANGUAGES:
        return text, "text"

    # Wrap English terms FIRST on clean text (before any escaping).
    wrapped = _wrap_english_terms(text)

    # Only emit SSML if we actually inserted <lang> tags
    if wrapped == text:
        return text, "text"

    # Escape XML specials in the non-tag portions only.
    # Split on our <lang ...>...</lang> tags, escape non-tag parts, rejoin.
    _LANG_TAG = re.compile(r'(<lang xml:lang="en-US">.*?</lang>)')
    parts = _LANG_TAG.split(wrapped)
    escaped_parts = []
    for part in parts:
        if part.startswith("<lang"):
            # Our SSML tag — don't escape
            escaped_parts.append(part)
        else:
            # User text — escape XML specials
            escaped_parts.append(
                part.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )

    return f"<speak>{''.join(escaped_parts)}</speak>", "ssml"


# ---------------------------------------------------------------------------
# Engine-aware SSML strategy
# ---------------------------------------------------------------------------
# Generative: plain text (model handles prosody natively)
# Neural: rich SSML (break + phoneme + say-as + sub + lang + boundary pauses)
# Source: AGISPresentGen internal codebase — "Generative works best with plain text"


def _prepare_generative(text: str) -> tuple[str, str]:
    """Generative engine: plain text, no SSML.

    The billion-parameter transformer produces natural prosody from context.
    Adding SSML tags can interfere with its internal rhythm model.
    Source: AGISPresentGen internal codebase (verified best practice).
    """
    return text.strip(), "text"


# --- Neural SSML pipeline components ---

# Chinese polyphones: only words where Polly's default reading is wrong.
# Curated list — not a full dictionary. Add entries only when verified.
POLYPHONE_MAP: dict[str, tuple[str, str]] = {
    "重点":   ("zhong4dian3", "重点"),
    "重新":   ("chong2xin1",  "重新"),
    "重要":   ("zhong4yao4",  "重要"),
    "重复":   ("chong2fu4",   "重复"),
    "行业":   ("hang2ye4",    "行业"),
    "行为":   ("xing2wei2",   "行为"),
    "长期":   ("chang2qi1",   "长期"),
    "长大":   ("zhang3da4",   "长大"),
    "了解":   ("liao3jie3",   "了解"),
    "得到":   ("de2dao4",     "得到"),
    "地方":   ("di4fang1",    "地方"),
    "数据":   ("shu4ju4",     "数据"),
    "还是":   ("hai2shi4",    "还是"),
    "差不多":  ("cha4bu4duo1", "差不多"),
    "处理":   ("chu3li3",     "处理"),
    "调整":   ("tiao2zheng3", "调整"),
}


def _apply_phonemes(text: str) -> str:
    """Replace known polyphones with <phoneme> SSML tags.

    Uses x-amazon-pinyin alphabet for Mandarin tone marks.
    Only applies to curated POLYPHONE_MAP entries — conservative approach.
    """
    for word, (pinyin, _display) in POLYPHONE_MAP.items():
        if word in text:
            tag = f'<phoneme alphabet="x-amazon-pinyin" ph="{pinyin}">{word}</phoneme>'
            text = text.replace(word, tag)
    return text


# Phone: 138-1234-5678 or 138 1234 5678
_PHONE_RE = re.compile(r'(\d{3}[-\s]?\d{4}[-\s]?\d{4})')
# Date: 2026-04-26 or 2026/04/26
_DATE_RE = re.compile(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})')
# Large number with commas: 1,234,567
_CARDINAL_RE = re.compile(r'(\d{1,3}(?:,\d{3})+)')


def _format_numbers(text: str) -> str:
    """Wrap numbers and dates in <say-as> for correct reading."""
    text = _PHONE_RE.sub(r'<say-as interpret-as="telephone">\1</say-as>', text)
    text = _DATE_RE.sub(r'<say-as interpret-as="date" format="ymd">\1</say-as>', text)
    text = _CARDINAL_RE.sub(r'<say-as interpret-as="cardinal">\1</say-as>', text)
    return text


# Common abbreviations that Polly misreads as words in CJK context.
# Expansion adds spaces so Polly spells them out letter-by-letter.
ABBREVIATION_MAP: dict[str, str] = {
    "AWS":  "A W S",
    "API":  "A P I",
    "SDK":  "S D K",
    "UI":   "U I",
    "URL":  "U R L",
    "SQL":  "S Q L",
    "CSS":  "C S S",
    "HTML": "H T M L",
    "AI":   "A I",
    "LLM":  "L L M",
    "TTS":  "T T S",
    "STT":  "S T T",
}


def _expand_abbreviations(text: str) -> str:
    """Expand common abbreviations via <sub> for clearer pronunciation.

    Only matches standalone abbreviations, not substrings.
    So "RAPID" does NOT match "API".

    Uses negative lookbehind/lookahead for ASCII letters instead of
    word boundary, because it doesn't fire at CJK-ASCII transitions.
    """
    for abbr, expansion in ABBREVIATION_MAP.items():
        text = re.sub(
            rf'(?<![A-Za-z]){abbr}(?![A-Za-z])',
            f'<sub alias="{expansion}">{abbr}</sub>',
            text,
        )
    return text


# Break timing for punctuation (conversation-tuned, faster than podcast)
_BREAK_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r'([。！？])'), r'\1<break time="400ms"/>'),     # Sentence end (zh)
    (re.compile(r'([.!?])\s'),  r'\1<break time="350ms"/> '),   # Sentence end (en, in zh text)
    (re.compile(r'([，、])'),    r'\1<break time="200ms"/>'),     # Clause break (zh)
    (re.compile(r'([；：])'),    r'\1<break time="300ms"/>'),     # Strong clause (zh)
    (re.compile(r'\n\n+'),      '<break time="600ms"/>'),        # Paragraph
    (re.compile(r'\n'),          '<break time="250ms"/>'),        # Line break
]


def _inject_breaks(text: str) -> str:
    """Add <break> tags after punctuation for natural pacing."""
    for pattern, replacement in _BREAK_RULES:
        text = pattern.sub(replacement, text)
    return text


# CJK Unicode range for boundary detection
_CJK_CHAR = re.compile(r'[一-鿿㐀-䶿]')


def _add_language_switch_breaks(ssml: str) -> str:
    """Add micro-pauses at language switch boundaries for natural transition.

    When switching between Chinese and English within a sentence,
    a 150ms pause helps the voice engine transition smoothly.
    Matches closing tags (</lang>, </sub>, </say-as>) followed by CJK,
    and CJK followed by opening tags (<lang, <sub, <phoneme, <say-as).
    """
    # After closing English-related tag followed by CJK
    ssml = re.sub(
        r'(</(?:lang|sub|say-as)>)(\s*[一-鿿㐀-䶿])',
        r'\1<break time="150ms"/>\2',
        ssml,
    )
    # Before opening English-related tag preceded by CJK
    ssml = re.sub(
        r'([一-鿿㐀-䶿]\s*)(<(?:lang|sub|phoneme|say-as))',
        r'\1<break time="150ms"/>\2',
        ssml,
    )
    return ssml


def _escape_non_tags(text: str) -> str:
    """XML-escape user text while preserving our SSML tags.

    Splits on known SSML tag patterns, escapes the non-tag portions.
    """
    tag_pattern = re.compile(
        r'(<(?:lang|/lang|phoneme|/phoneme|say-as|/say-as|sub|/sub|break)[^>]*>)'
    )
    parts = tag_pattern.split(text)
    escaped = []
    for part in parts:
        if part.startswith("<") and tag_pattern.match(part):
            escaped.append(part)  # SSML tag — don't escape
        else:
            escaped.append(
                part.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
    return "".join(escaped)


def _prepare_neural(text: str, language: str) -> tuple[str, str]:
    """Neural engine: rich SSML for natural pacing and pronunciation.

    Pipeline order matters:
    1. Phoneme disambiguation (before any XML escaping)
    2. Number/date formatting
    3. Abbreviation expansion
    4. English term <lang> wrapping (existing logic)
    5. XML-escape non-tag portions
    6. Break injection
    7. Language switch boundary pauses
    8. Wrap in <speak>
    """
    result = text.strip()

    # 1. Polyphone disambiguation (Chinese only)
    if language in ("zh-CN", "cmn-CN"):
        result = _apply_phonemes(result)

    # 2. Number/date formatting
    result = _format_numbers(result)

    # 3. Abbreviation expansion
    result = _expand_abbreviations(result)

    # 4. English term wrapping (CJK languages only)
    # Protect existing SSML tags from steps 1-3 so _wrap_english_terms
    # doesn't match attribute names ("alphabet", "interpret") as English words.
    # Placeholders use CJK private-use chars so the ASCII-only regex ignores them.
    if language in _CJK_LANGUAGES:
        _tag_re = re.compile(r'(<[^>]+>)')
        _tag_map: dict[str, str] = {}
        _tag_n = [0]

        def _ph_tag(m: re.Match) -> str:
            key = f"{_tag_n[0]}"
            _tag_map[key] = m.group(0)
            _tag_n[0] += 1
            return key

        result = _tag_re.sub(_ph_tag, result)
        result = _wrap_english_terms(result)
        for key, tag in _tag_map.items():
            result = result.replace(key, tag)

    # 5. XML-escape non-tag portions
    result = _escape_non_tags(result)

    # 6. Break injection
    result = _inject_breaks(result)

    # 7. Language switch boundary pauses
    result = _add_language_switch_breaks(result)

    # 8. Wrap in <speak>
    return f"<speak>{result}</speak>", "ssml"


async def synthesize_speech(
    text: str,
    voice_id: str | None = None,
    language: str = "en-US",
) -> bytes:
    """Synthesize text to MP3 audio via Amazon Polly.

    Engine-aware strategy:
    - Generative: plain text (model handles prosody natively)
    - Neural: rich SSML (break + phoneme + say-as + sub + lang + boundary pauses)

    Args:
        text: Text to speak (max 3000 chars for neural engine)
        voice_id: Polly voice ID override (auto-selected from language if None)
        language: BCP-47 language code for voice selection

    Returns:
        MP3 audio bytes

    Raises:
        ValueError: If text is empty or only whitespace
        RuntimeError: If Polly synthesis fails
    """
    if not text or not text.strip():
        raise ValueError("Empty text — nothing to synthesize")

    # Truncate to Polly neural limit (3000 chars)
    clean_text = text.strip()
    if len(clean_text) > MAX_TEXT_LENGTH:
        clean_text = clean_text[:MAX_TEXT_LENGTH]

    # Resolve voice + engine + Polly LanguageCode.
    # Polly requires exact LanguageCode per voice (e.g., Zhiyu = cmn-CN, not zh-CN).
    if voice_id:
        # Reverse-lookup: find the engine + polly_language_code for this voice
        vid = voice_id
        engine = "generative"  # default
        lang_code = language
        for _lc, (v, e, plc) in VOICE_MAP.items():
            if v == voice_id:
                engine = e
                lang_code = plc
                break
    else:
        vid, engine, lang_code = get_voice_for_language(language)

    # Engine-aware text preparation
    if engine == "generative":
        polly_text, text_type = _prepare_generative(clean_text)
    else:
        polly_text, text_type = _prepare_neural(clean_text, language)

    client = _get_polly_client()

    # Explicit sample rate for quality
    sample_rate = "24000" if engine == "generative" else "22050"

    # Run synchronous boto3 call in executor to avoid blocking event loop
    loop = asyncio.get_running_loop()
    try:
        response = await loop.run_in_executor(
            None,
            lambda: client.synthesize_speech(
                Text=polly_text,
                TextType=text_type,
                OutputFormat="mp3",
                VoiceId=vid,
                Engine=engine,
                LanguageCode=lang_code,
                SampleRate=sample_rate,
            ),
        )
    except Exception as e:
        raise RuntimeError(f"Polly synthesis failed: {e}") from e

    audio_stream = response["AudioStream"].read()

    logger.info(
        "Polly TTS: %d chars → %d bytes MP3 (voice=%s, lang=%s, engine=%s, type=%s, region=%s)",
        len(clean_text), len(audio_stream), vid, language, engine, text_type, DEFAULT_REGION,
    )

    return audio_stream
