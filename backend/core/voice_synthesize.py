"""Text-to-speech via Amazon Polly.

Converts text to MP3 audio using Polly Generative voices (female persona).
Falls back to Neural where Generative is unavailable (zh-CN, ja-JP).
Reuses existing AWS SSO credentials (same as Transcribe).

For CJK languages with mixed English terms, auto-wraps English words in
SSML <lang xml:lang="en-US"> tags so Polly uses the English pronunciation
engine instead of reading them as Chinese/Japanese/Korean phonemes.

Public API:
    synthesize_speech(text, voice_id, language, region) → bytes (MP3)
    get_voice_for_language(language) → tuple[str, str, str]
    VOICE_MAP — language → (voice_id, engine, polly_language_code) mapping
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


async def synthesize_speech(
    text: str,
    voice_id: str | None = None,
    language: str = "en-US",
) -> bytes:
    """Synthesize text to MP3 audio via Amazon Polly.

    For CJK languages, auto-wraps English terms in SSML <lang> tags so Polly
    uses native English pronunciation instead of reading them as CJK phonemes.

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

    # Convert to SSML for CJK languages (wraps English terms for pronunciation)
    polly_text, text_type = _to_ssml(clean_text, language)

    client = _get_polly_client()

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
            ),
        )
    except Exception as e:
        raise RuntimeError(f"Polly synthesis failed: {e}") from e

    audio_stream = response["AudioStream"].read()

    logger.info(
        "Polly TTS: %d chars → %d bytes MP3 (voice=%s, lang=%s, type=%s, region=%s)",
        len(clean_text), len(audio_stream), vid, language, text_type, DEFAULT_REGION,
    )

    return audio_stream
