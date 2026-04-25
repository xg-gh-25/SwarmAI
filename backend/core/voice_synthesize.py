"""Text-to-speech via Amazon Polly.

Converts text to MP3 audio using Polly Neural voices.
Reuses existing AWS SSO credentials (same as Transcribe).

Public API:
    synthesize_speech(text, voice_id, language, region) → bytes (MP3)
    get_voice_for_language(language) → tuple[str, str] (voice_id, engine)
    VOICE_MAP — language → (voice_id, engine) mapping
"""

import asyncio
import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

# Voice mapping: BCP-47 language_code → (voice_id, engine)
VOICE_MAP: dict[str, tuple[str, str]] = {
    "en-US": ("Matthew", "neural"),
    "en-GB": ("Arthur", "neural"),
    "zh-CN": ("Zhiyu", "neural"),
    "ja-JP": ("Kazuha", "neural"),
    "ko-KR": ("Seoyeon", "neural"),
    "de-DE": ("Daniel", "neural"),
    "fr-FR": ("Lea", "neural"),
    "es-ES": ("Sergio", "neural"),
}

DEFAULT_VOICE = ("Matthew", "neural")

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


def get_voice_for_language(language: str) -> tuple[str, str]:
    """Return (voice_id, engine) for a BCP-47 language code.

    Tries exact match first, then prefix match (e.g., "en" matches "en-US").
    Falls back to DEFAULT_VOICE if no match found.

    Args:
        language: BCP-47 language code (e.g., "en-US", "zh-CN", "ja-JP")

    Returns:
        Tuple of (voice_id, engine) for Amazon Polly
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


async def synthesize_speech(
    text: str,
    voice_id: str | None = None,
    language: str = "en-US",
) -> bytes:
    """Synthesize text to MP3 audio via Amazon Polly.

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

    # Resolve voice + matching LanguageCode
    # When voice_id is overridden, we must send the correct LanguageCode
    # or Polly rejects the request (e.g., Zhiyu requires zh-CN, not en-US).
    if voice_id:
        vid, engine = voice_id, "neural"
        # Reverse-lookup: find the language for this voice
        lang_code = language  # default to caller's language
        for lc, (v, _e) in VOICE_MAP.items():
            if v == voice_id:
                lang_code = lc
                break
    else:
        vid, engine = get_voice_for_language(language)
        lang_code = language

    client = _get_polly_client()

    # Run synchronous boto3 call in executor to avoid blocking event loop
    loop = asyncio.get_event_loop()
    try:
        response = await loop.run_in_executor(
            None,
            lambda: client.synthesize_speech(
                Text=clean_text,
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
        "Polly TTS: %d chars → %d bytes MP3 (voice=%s, lang=%s, region=%s)",
        len(clean_text), len(audio_stream), vid, language, DEFAULT_REGION,
    )

    return audio_stream
