"""Voice synthesis API routes.

Endpoints:
    POST /api/voice/synthesize — Text to speech via Amazon Polly (returns MP3)
    GET  /api/voice/voices     — List available TTS voices per language
"""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core.voice_synthesize import (
    VOICE_MAP,
    synthesize_speech,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class SynthesizeRequest(BaseModel):
    """Request body for text-to-speech synthesis."""

    text: str = Field(..., min_length=1, description="Text to synthesize (max 3000 chars)")
    language: str = Field(default="en-US", description="BCP-47 language code")
    voice_id: str | None = Field(default=None, description="Polly voice ID override")


@router.post("/synthesize")
async def synthesize(request: SynthesizeRequest):
    """Convert text to speech. Returns MP3 audio.

    Accepts JSON body with text, optional language and voice_id.
    Returns raw MP3 bytes with audio/mpeg content type.
    """
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
