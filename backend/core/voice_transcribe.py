"""Voice transcription via Amazon Transcribe Streaming.

Converts audio to PCM via ffmpeg, streams to Amazon Transcribe, returns text.
Uses existing AWS SSO credentials — no OpenAI dependency.

Public API:
    transcribe_audio(audio_data, language) → dict with transcript, language, duration_ms
    convert_audio_to_pcm(audio_data) → bytes (16kHz mono 16-bit PCM)
"""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent

logger = logging.getLogger(__name__)

# PCM format expected by Amazon Transcribe Streaming
SAMPLE_RATE = 16000
CHANNELS = 1
BITS_PER_SAMPLE = 16
CHUNK_SIZE = 16 * 1024  # 16KB chunks for streaming

# Minimum audio size (~1s at 16kHz mono 16-bit = 32000 bytes)
MIN_AUDIO_BYTES = SAMPLE_RATE * (BITS_PER_SAMPLE // 8) * CHANNELS

# Default AWS region — override via TRANSCRIBE_REGION env var or parameter
DEFAULT_REGION = os.environ.get("TRANSCRIBE_REGION", "us-east-1")


class _TranscriptHandler(TranscriptResultStreamHandler):
    """Collects final (non-partial) transcript results."""

    def __init__(self, output_stream):
        super().__init__(output_stream)
        self.segments: list[str] = []

    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        results = transcript_event.transcript.results
        for result in results:
            if not result.is_partial and result.alternatives:
                self.segments.append(result.alternatives[0].transcript)


async def convert_audio_to_pcm(audio_data: bytes) -> bytes:
    """Convert any audio format to 16kHz mono 16-bit PCM using ffmpeg.

    Args:
        audio_data: Raw audio bytes (WAV, MP4, WebM, OGG, etc.)

    Returns:
        PCM bytes (16-bit signed LE, 16kHz, mono)

    Raises:
        ValueError: If audio_data is empty
        RuntimeError: If ffmpeg is missing, conversion fails, or times out
    """
    if not audio_data:
        raise ValueError("Empty audio data")

    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp_in:
        tmp_in.write(audio_data)
        tmp_in_path = Path(tmp_in.name)

    tmp_out_path = tmp_in_path.with_suffix(".pcm")
    proc = None

    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-i", str(tmp_in_path),
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ar", str(SAMPLE_RATE),
                "-ac", str(CHANNELS),
                str(tmp_out_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "ffmpeg not found — install via: brew install ffmpeg (macOS) "
                "or apt-get install ffmpeg (Linux)"
            )

        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("ffmpeg conversion timed out (>30s)")

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace")[-500:]
            raise RuntimeError(f"ffmpeg conversion failed (exit {proc.returncode}): {err_msg}")

        pcm_data = tmp_out_path.read_bytes()
        if not pcm_data:
            raise RuntimeError("ffmpeg produced empty output")

        return pcm_data

    finally:
        # Always kill orphaned subprocess
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        tmp_in_path.unlink(missing_ok=True)
        tmp_out_path.unlink(missing_ok=True)


async def _transcribe_with_streaming(
    pcm_data: bytes,
    language: str = "en-US",
    region: str | None = None,
) -> str:
    """Stream PCM audio to Amazon Transcribe and return transcript text.

    Args:
        pcm_data: 16-bit signed LE PCM at 16kHz mono
        language: BCP-47 language code (default en-US)
        region: AWS region (defaults to TRANSCRIBE_REGION env var or us-east-1)

    Returns:
        Transcribed text string
    """
    client = TranscribeStreamingClient(region=region or DEFAULT_REGION)

    stream = await client.start_stream_transcription(
        language_code=language,
        media_sample_rate_hz=SAMPLE_RATE,
        media_encoding="pcm",
    )

    handler = _TranscriptHandler(stream.output_stream)

    # Feed audio chunks to input stream
    async def _send_audio():
        for offset in range(0, len(pcm_data), CHUNK_SIZE):
            chunk = pcm_data[offset : offset + CHUNK_SIZE]
            await stream.input_stream.send_audio_event(audio_chunk=chunk)
        await stream.input_stream.end_stream()

    # Run sender and handler concurrently
    await asyncio.gather(_send_audio(), handler.handle_events())

    return " ".join(handler.segments).strip()


async def transcribe_audio(
    audio_data: bytes,
    language: str | None = None,
    region: str | None = None,
) -> dict:
    """Transcribe audio bytes to text using Amazon Transcribe Streaming.

    Args:
        audio_data: Raw audio bytes in any format supported by ffmpeg
        language: BCP-47 language code (default "en-US"). Pass "zh-CN" for Chinese.
        region: AWS region (defaults to TRANSCRIBE_REGION env var or us-east-1)

    Returns:
        {"transcript": str, "language": str, "duration_ms": int}

    Raises:
        ValueError: If audio_data is empty or too short
        RuntimeError: If conversion or transcription fails
    """
    if not audio_data:
        raise ValueError("Empty audio data — nothing to transcribe")

    lang = language or "en-US"
    start = time.monotonic()

    # Convert to PCM
    pcm_data = await convert_audio_to_pcm(audio_data)

    if len(pcm_data) < MIN_AUDIO_BYTES:
        raise ValueError("Audio too short — need at least 1 second")

    # Calculate duration from PCM size
    bytes_per_sample = BITS_PER_SAMPLE // 8
    num_samples = len(pcm_data) // (bytes_per_sample * CHANNELS)
    duration_ms = int(num_samples / SAMPLE_RATE * 1000)

    # Transcribe
    try:
        transcript = await _transcribe_with_streaming(pcm_data, language=lang, region=region)
    except Exception as e:
        raise RuntimeError(f"Transcription failed: {e}") from e

    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "Voice transcription: %dms audio → %d chars in %dms (lang=%s)",
        duration_ms, len(transcript), elapsed_ms, lang,
    )

    return {
        "transcript": transcript,
        "language": lang,
        "duration_ms": duration_ms,
    }
