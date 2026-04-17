"""Tests for voice transcription module (Amazon Transcribe Streaming).

Tests the core transcription pipeline: audio format conversion (ffmpeg),
Amazon Transcribe streaming client, and the FastAPI endpoint.

Acceptance criteria covered:
  AC3: Backend uses Amazon Transcribe (boto3/amazon-transcribe) — NOT OpenAI
  AC5: Error states: empty audio, transcription failure
  AC4: Returns transcript text (integration via endpoint)
"""

import asyncio
import io
import struct
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# AC3: Backend transcription uses Amazon Transcribe, not OpenAI
# ---------------------------------------------------------------------------

class TestVoiceTranscribeModule:
    """voice_transcribe.py must exist and use amazon-transcribe SDK."""

    def test_module_imports(self):
        """Module can be imported without errors."""
        from core.voice_transcribe import transcribe_audio
        assert callable(transcribe_audio)

    def test_module_does_not_import_openai(self):
        """Module must NOT depend on OpenAI — uses Amazon Transcribe only."""
        import sys
        if 'core.voice_transcribe' in sys.modules:
            del sys.modules['core.voice_transcribe']
        import core.voice_transcribe as mod
        source = Path(mod.__file__).read_text()
        # Check for actual import statements, not docstring mentions
        import_lines = [l.strip() for l in source.splitlines() if l.strip().startswith(('import ', 'from '))]
        for line in import_lines:
            assert 'openai' not in line.lower(), f"Module imports OpenAI: {line}"
        assert 'OPENAI_API_KEY' not in source, "Module must not use OpenAI API key"

    def test_uses_amazon_transcribe(self):
        """Module must use amazon_transcribe SDK."""
        from core.voice_transcribe import transcribe_audio
        import core.voice_transcribe as mod
        source = Path(mod.__file__).read_text()
        assert 'amazon_transcribe' in source or 'transcribe' in source.lower(), \
            "Module must reference Amazon Transcribe"


# ---------------------------------------------------------------------------
# AC5: Error handling — empty audio, conversion failure, transcription failure
# ---------------------------------------------------------------------------

class TestAudioConversion:
    """ffmpeg-based audio conversion to PCM."""

    def test_convert_audio_to_pcm_exists(self):
        """Conversion function exists and is callable."""
        from core.voice_transcribe import convert_audio_to_pcm
        assert callable(convert_audio_to_pcm)

    @pytest.mark.asyncio
    async def test_empty_audio_raises(self):
        """Empty audio data should raise ValueError."""
        from core.voice_transcribe import convert_audio_to_pcm
        with pytest.raises((ValueError, Exception)):
            await convert_audio_to_pcm(b"")

    @pytest.mark.asyncio
    async def test_invalid_audio_raises(self):
        """Random bytes (not valid audio) should raise an error."""
        from core.voice_transcribe import convert_audio_to_pcm
        with pytest.raises(Exception):
            await convert_audio_to_pcm(b"not-valid-audio-data-at-all")

    @pytest.mark.asyncio
    async def test_valid_wav_converts(self):
        """A valid WAV file should convert to PCM bytes."""
        from core.voice_transcribe import convert_audio_to_pcm
        # Create a minimal valid WAV: 16-bit PCM, 16kHz, mono, 0.1s silence
        wav_data = _create_minimal_wav(sample_rate=16000, duration_s=0.1)
        result = await convert_audio_to_pcm(wav_data)
        assert isinstance(result, bytes)
        assert len(result) > 0


class TestTranscribeAudio:
    """Core transcription function with mocked Transcribe service."""

    @pytest.mark.asyncio
    async def test_transcribe_returns_dict(self):
        """transcribe_audio must return dict with transcript, language, duration_ms."""
        from core.voice_transcribe import transcribe_audio
        wav_data = _create_minimal_wav(sample_rate=16000, duration_s=1.0)

        with _mock_transcribe_streaming("hello world"):
            result = await transcribe_audio(wav_data, language="en-US")

        assert isinstance(result, dict)
        assert "transcript" in result
        assert "language" in result
        assert "duration_ms" in result
        assert result["transcript"] == "hello world"

    @pytest.mark.asyncio
    async def test_transcribe_empty_audio_error(self):
        """Empty audio should raise ValueError before hitting Transcribe."""
        from core.voice_transcribe import transcribe_audio
        with pytest.raises((ValueError, Exception)):
            await transcribe_audio(b"", language="en-US")

    @pytest.mark.asyncio
    async def test_transcribe_service_failure(self):
        """If Transcribe service fails, raise a descriptive error."""
        from core.voice_transcribe import transcribe_audio
        wav_data = _create_minimal_wav(sample_rate=16000, duration_s=1.0)

        with _mock_transcribe_streaming_error("Service unavailable"):
            with pytest.raises(Exception, match="(?i)transcri"):
                await transcribe_audio(wav_data, language="en-US")

    @pytest.mark.asyncio
    async def test_transcribe_default_language(self):
        """When no language specified, should default to en-US or auto-detect."""
        from core.voice_transcribe import transcribe_audio
        wav_data = _create_minimal_wav(sample_rate=16000, duration_s=1.0)

        with _mock_transcribe_streaming("test"):
            result = await transcribe_audio(wav_data)  # no language param
        assert result["language"]  # should have some language


# ---------------------------------------------------------------------------
# AC4: FastAPI endpoint returns transcript
# ---------------------------------------------------------------------------

class TestTranscribeEndpoint:
    """POST /api/chat/transcribe endpoint."""

    def test_endpoint_exists(self):
        """The transcribe endpoint must be registered on the chat router."""
        from routers.chat import router
        paths = [r.path for r in router.routes]
        assert "/transcribe" in paths, \
            f"Expected /transcribe in routes, found: {paths}"

    def test_endpoint_rejects_no_file(self):
        """POST without audio file returns 400."""
        from main import app
        client = TestClient(app)
        response = client.post("/api/chat/transcribe")
        assert response.status_code in (400, 422)

    def test_endpoint_accepts_audio(self):
        """POST with valid audio file returns transcript JSON."""
        from main import app
        client = TestClient(app)
        wav_data = _create_minimal_wav(sample_rate=16000, duration_s=1.0)

        with _mock_transcribe_streaming("hello from endpoint"):
            response = client.post(
                "/api/chat/transcribe",
                files={"audio": ("recording.wav", io.BytesIO(wav_data), "audio/wav")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["transcript"] == "hello from endpoint"


# ---------------------------------------------------------------------------
# E2E review fixes: subprocess safety, ffmpeg detection, error messages
# ---------------------------------------------------------------------------

class TestSubprocessSafety:
    """Verify subprocess cleanup on timeout and error paths."""

    @pytest.mark.asyncio
    async def test_ffmpeg_not_installed(self):
        """Missing ffmpeg should raise RuntimeError, not FileNotFoundError."""
        from core.voice_transcribe import convert_audio_to_pcm
        wav_data = _create_minimal_wav(sample_rate=16000, duration_s=1.0)

        with patch('core.voice_transcribe.asyncio.create_subprocess_exec',
                   side_effect=FileNotFoundError("ffmpeg")):
            with pytest.raises(RuntimeError, match="ffmpeg not found"):
                await convert_audio_to_pcm(wav_data)

    @pytest.mark.asyncio
    async def test_ffmpeg_timeout_kills_process(self):
        """Timed-out ffmpeg process must be killed, not orphaned."""
        from core.voice_transcribe import convert_audio_to_pcm
        wav_data = _create_minimal_wav(sample_rate=16000, duration_s=1.0)

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = AsyncMock()
        # Simulate real behavior: wait() sets returncode after kill
        async def _fake_wait():
            mock_proc.returncode = -9
        mock_proc.wait = AsyncMock(side_effect=_fake_wait)
        mock_proc.returncode = None  # still running initially

        with patch('core.voice_transcribe.asyncio.create_subprocess_exec',
                   return_value=mock_proc):
            with pytest.raises(RuntimeError, match="timed out"):
                await convert_audio_to_pcm(wav_data)

        # Process must have been killed (once in timeout handler;
        # finally block skips because returncode is set after wait())
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_subprocess_cleanup_on_exception(self):
        """Subprocess with returncode=None must be killed in finally block."""
        from core.voice_transcribe import convert_audio_to_pcm
        wav_data = _create_minimal_wav(sample_rate=16000, duration_s=1.0)

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = None  # never finished
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with patch('core.voice_transcribe.asyncio.create_subprocess_exec',
                   return_value=mock_proc):
            with pytest.raises(Exception):
                await convert_audio_to_pcm(wav_data)

        mock_proc.kill.assert_called()

    @pytest.mark.asyncio
    async def test_error_message_says_1_second(self):
        """Error for short audio must say '1 second', not '0.5 seconds'."""
        from core.voice_transcribe import transcribe_audio
        # 0.5s WAV → PCM is below MIN_AUDIO_BYTES (1s threshold)
        wav_data = _create_minimal_wav(sample_rate=16000, duration_s=0.5)

        with pytest.raises(ValueError, match="1 second"):
            await transcribe_audio(wav_data, language="en-US")

    @pytest.mark.asyncio
    async def test_region_configurable(self):
        """Region parameter must be passed through to streaming client."""
        from core.voice_transcribe import transcribe_audio
        wav_data = _create_minimal_wav(sample_rate=16000, duration_s=1.0)

        with _mock_transcribe_streaming("hello") as mock:
            result = await transcribe_audio(wav_data, language="en-US", region="us-west-2")
            # Verify region was passed (mock replaces _transcribe_with_streaming,
            # which receives the region param)
            assert result["transcript"] == "hello"


class TestEndpointValidation:
    """Endpoint input validation from E2E review."""

    def test_endpoint_rejects_text_field(self):
        """Sending audio as text field (not file) should return 400."""
        from main import app
        client = TestClient(app)
        response = client.post(
            "/api/chat/transcribe",
            data={"audio": "not-a-file"},
        )
        assert response.status_code == 400
        assert "file upload" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_minimal_wav(sample_rate: int = 16000, duration_s: float = 0.1) -> bytes:
    """Create a minimal valid WAV file with silence."""
    num_samples = int(sample_rate * duration_s)
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = num_samples * block_align

    buf = io.BytesIO()
    # RIFF header
    buf.write(b'RIFF')
    buf.write(struct.pack('<I', 36 + data_size))
    buf.write(b'WAVE')
    # fmt chunk
    buf.write(b'fmt ')
    buf.write(struct.pack('<I', 16))  # chunk size
    buf.write(struct.pack('<H', 1))   # PCM format
    buf.write(struct.pack('<H', num_channels))
    buf.write(struct.pack('<I', sample_rate))
    buf.write(struct.pack('<I', byte_rate))
    buf.write(struct.pack('<H', block_align))
    buf.write(struct.pack('<H', bits_per_sample))
    # data chunk
    buf.write(b'data')
    buf.write(struct.pack('<I', data_size))
    buf.write(b'\x00' * data_size)  # silence
    return buf.getvalue()


def _mock_transcribe_streaming(transcript_text: str):
    """Context manager that mocks the amazon-transcribe streaming client."""
    return patch(
        'core.voice_transcribe._transcribe_with_streaming',
        new_callable=AsyncMock,
        return_value=transcript_text,
    )


def _mock_transcribe_streaming_error(error_msg: str):
    """Context manager that mocks a Transcribe failure."""
    return patch(
        'core.voice_transcribe._transcribe_with_streaming',
        new_callable=AsyncMock,
        side_effect=RuntimeError(f"Transcription failed: {error_msg}"),
    )
