"""Voice synthesis API routes.

Endpoints:
    POST /api/voice/synthesize — Text to speech via Amazon Polly (returns MP3)
    GET  /api/voice/voices     — List available TTS voices per language

Rate limited: synthesize is capped at 60 requests/minute to prevent Polly cost runaway.
"""

import logging
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core.voice_synthesize import (
    MAX_TEXT_LENGTH,
    VOICE_MAP,
    synthesize_speech,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory sliding window rate limiter for synthesize endpoint.
# 60 requests per 60 seconds — prevents Polly cost runaway from buggy loops.
_RATE_LIMIT_MAX = 60
_RATE_LIMIT_WINDOW = 60.0
_request_timestamps: deque[float] = deque()


class SynthesizeRequest(BaseModel):
    """Request body for text-to-speech synthesis."""

    text: str = Field(..., min_length=1, max_length=MAX_TEXT_LENGTH, description="Text to synthesize (max 3000 chars)")
    language: str = Field(default="en-US", description="BCP-47 language code")
    voice_id: str | None = Field(default=None, description="Polly voice ID override")


@router.post("/synthesize")
async def synthesize(request: SynthesizeRequest):
    """Convert text to speech. Returns MP3 audio.

    Accepts JSON body with text, optional language and voice_id.
    Returns raw MP3 bytes with audio/mpeg content type.

    Rate limited to 60 requests/minute to cap Polly costs.
    """
    # Sliding window rate limit
    now = time.monotonic()
    while _request_timestamps and _request_timestamps[0] < now - _RATE_LIMIT_WINDOW:
        _request_timestamps.popleft()
    if len(_request_timestamps) >= _RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Rate limit exceeded — max 60 TTS requests/minute")
    _request_timestamps.append(now)

    try:
        audio = await synthesize_speech(
            text=request.text,
            voice_id=request.voice_id,
            language=request.language,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error("TTS synthesis failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))

    return Response(
        content=audio,
        media_type="audio/mpeg",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/voices")
async def list_voices():
    """Return available TTS voices per language.

    Returns a mapping of BCP-47 language codes to [voice_id, engine] pairs.
    """
    return {
        "voices": {lang: list(voice_info) for lang, voice_info in VOICE_MAP.items()}
    }
