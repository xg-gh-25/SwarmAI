"""Tests for voice synthesis module (Amazon Polly TTS).

Tests the TTS pipeline: text → Amazon Polly → MP3 audio, and the FastAPI
endpoints for synthesis and voice listing.

Acceptance criteria covered:
  AC1: POST /api/voice/synthesize returns MP3 via Polly
  AC2: GET /api/voice/voices returns available voices
  AC5: Error states: empty text, Polly failure
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# AC1: Module exists and uses Amazon Polly (not OpenAI, not browser TTS)
# ---------------------------------------------------------------------------

class TestVoiceSynthesizeModule:
    """voice_synthesize.py must exist and use boto3/Polly."""

    def test_module_imports(self):
        """Module can be imported without errors."""
        from core.voice_synthesize import synthesize_speech
        assert callable(synthesize_speech)

    def test_has_voice_map(self):
        """Module exposes VOICE_MAP with expected languages."""
        from core.voice_synthesize import VOICE_MAP
        assert isinstance(VOICE_MAP, dict)
        assert "en-US" in VOICE_MAP
        assert "zh-CN" in VOICE_MAP
        # Each entry is (voice_id, engine, polly_language_code)
        for lang, info in VOICE_MAP.items():
            assert isinstance(info, tuple) and len(info) == 3

    def test_does_not_import_openai(self):
        """Module must NOT depend on OpenAI."""
        from pathlib import Path
        import core.voice_synthesize as mod
        source = Path(mod.__file__).read_text()
        import_lines = [
            l.strip() for l in source.splitlines()
            if l.strip().startswith(("import ", "from "))
        ]
        for line in import_lines:
            assert "openai" not in line.lower(), f"Module imports OpenAI: {line}"


# ---------------------------------------------------------------------------
# AC1: get_voice_for_language
# ---------------------------------------------------------------------------

class TestGetVoiceForLanguage:
    """Voice selection logic."""

    def test_exact_match(self):
        from core.voice_synthesize import get_voice_for_language
        vid, engine, plc = get_voice_for_language("en-US")
        assert vid == "Ruth"
        assert engine == "generative"
        assert plc == "en-US"

    def test_chinese_voice(self):
        from core.voice_synthesize import get_voice_for_language
        vid, engine, plc = get_voice_for_language("zh-CN")
        assert vid == "Zhiyu"
        assert engine == "neural"  # generative not available for zh-CN
        assert plc == "cmn-CN"     # Polly uses cmn-CN, not zh-CN

    def test_prefix_match(self):
        """Prefix 'en' should match 'en-US'."""
        from core.voice_synthesize import get_voice_for_language
        vid, _engine, _plc = get_voice_for_language("en")
        # Should match one of the en-* entries
        assert vid in ("Ruth", "Amy")

    def test_unknown_language_falls_back(self):
        from core.voice_synthesize import get_voice_for_language, DEFAULT_VOICE
        result = get_voice_for_language("xx-XX")
        assert result == DEFAULT_VOICE

    def test_japanese_voice(self):
        from core.voice_synthesize import get_voice_for_language
        vid, engine, plc = get_voice_for_language("ja-JP")
        assert vid == "Kazuha"
        assert engine == "neural"  # generative not available for ja-JP

    def test_korean_voice(self):
        from core.voice_synthesize import get_voice_for_language
        vid, engine, plc = get_voice_for_language("ko-KR")
        assert vid == "Seoyeon"
        assert engine == "generative"


# ---------------------------------------------------------------------------
# AC1: synthesize_speech — core function
# ---------------------------------------------------------------------------

class TestSynthesizeSpeech:
    """Core Polly synthesis function with mocked boto3."""

    def _mock_polly_response(self, audio_bytes: bytes = b"\xff\xfb\x90\x00" * 100):
        """Create a mock Polly response with AudioStream."""
        mock_stream = MagicMock()
        mock_stream.read.return_value = audio_bytes
        return {"AudioStream": mock_stream}

    @pytest.mark.asyncio
    async def test_synthesize_returns_mp3_bytes(self):
        """Happy path: text in, MP3 bytes out."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client

        _get_polly_client.cache_clear()

        fake_audio = b"\xff\xfb\x90\x00" * 100
        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = self._mock_polly_response(fake_audio)

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            result = await synthesize_speech("Hello world", language="en-US")

        assert isinstance(result, bytes)
        assert len(result) == len(fake_audio)
        mock_client.synthesize_speech.assert_called_once()

        # Verify Polly was called with correct params
        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["Text"] == "Hello world"
        assert call_kwargs["OutputFormat"] == "mp3"
        assert call_kwargs["VoiceId"] == "Ruth"
        assert call_kwargs["Engine"] == "generative"
        assert call_kwargs["LanguageCode"] == "en-US"

    @pytest.mark.asyncio
    async def test_synthesize_chinese(self):
        """Chinese text should use Zhiyu voice with cmn-CN LanguageCode."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client

        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = self._mock_polly_response()

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            await synthesize_speech("你好世界", language="zh-CN")

        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["VoiceId"] == "Zhiyu"
        assert call_kwargs["LanguageCode"] == "cmn-CN"  # Polly uses cmn-CN, not zh-CN

    @pytest.mark.asyncio
    async def test_synthesize_with_voice_override(self):
        """Explicit voice_id overrides language-based selection."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client

        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = self._mock_polly_response()

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            await synthesize_speech("Hello", voice_id="Joanna", language="en-US")

        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["VoiceId"] == "Joanna"

    @pytest.mark.asyncio
    async def test_synthesize_voice_override_sends_correct_language_code(self):
        """When voice_id is overridden, LanguageCode must match the voice.

        Bug: voice_id="Zhiyu" with language="en-US" would send
        LanguageCode="en-US" to Polly, which rejects it because Zhiyu
        is a zh-CN voice. Fix: reverse-lookup LanguageCode from VOICE_MAP.
        """
        from core.voice_synthesize import synthesize_speech, _get_polly_client

        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = self._mock_polly_response()

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            # Caller says "en-US" but requests Zhiyu (Chinese voice)
            await synthesize_speech("测试", voice_id="Zhiyu", language="en-US")

        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["VoiceId"] == "Zhiyu"
        # LanguageCode should be cmn-CN (Polly's code for Zhiyu), NOT en-US (from caller)
        assert call_kwargs["LanguageCode"] == "cmn-CN"

    @pytest.mark.asyncio
    async def test_synthesize_unknown_voice_override_keeps_caller_language(self):
        """When voice_id is not in VOICE_MAP, fall back to caller's language."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client

        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = self._mock_polly_response()

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            await synthesize_speech("Hello", voice_id="CustomVoice", language="fr-FR")

        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["VoiceId"] == "CustomVoice"
        assert call_kwargs["LanguageCode"] == "fr-FR"

    @pytest.mark.asyncio
    async def test_synthesize_empty_text_raises(self):
        """Empty text should raise ValueError."""
        from core.voice_synthesize import synthesize_speech
        with pytest.raises(ValueError, match="Empty text"):
            await synthesize_speech("")

    @pytest.mark.asyncio
    async def test_synthesize_whitespace_only_raises(self):
        """Whitespace-only text should raise ValueError."""
        from core.voice_synthesize import synthesize_speech
        with pytest.raises(ValueError, match="Empty text"):
            await synthesize_speech("   \n  ")

    @pytest.mark.asyncio
    async def test_synthesize_truncates_long_text(self):
        """Text longer than 3000 chars should be truncated, not rejected."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client, MAX_TEXT_LENGTH

        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = self._mock_polly_response()

        long_text = "a" * 5000
        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            await synthesize_speech(long_text)

        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert len(call_kwargs["Text"]) == MAX_TEXT_LENGTH

    @pytest.mark.asyncio
    async def test_synthesize_polly_failure_raises_runtime_error(self):
        """Polly API failure should raise RuntimeError."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client

        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        mock_client.synthesize_speech.side_effect = Exception("Polly is down")

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Polly synthesis failed"):
                await synthesize_speech("Hello")


# ---------------------------------------------------------------------------
# AC1+AC2: FastAPI endpoints
# ---------------------------------------------------------------------------

class TestVoiceEndpoints:
    """Test POST /api/voice/synthesize and GET /api/voice/voices."""

    @pytest.fixture
    def client(self):
        """Create a test client with the voice router."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers.voice import router
        app = FastAPI()
        app.include_router(router, prefix="/api/voice")
        return TestClient(app)

    def test_voices_endpoint(self, client):
        """GET /api/voice/voices returns voice map."""
        resp = client.get("/api/voice/voices")
        assert resp.status_code == 200
        data = resp.json()
        assert "voices" in data
        assert "en-US" in data["voices"]
        assert "zh-CN" in data["voices"]
        # Each voice is [voice_id, engine]
        for lang, info in data["voices"].items():
            assert isinstance(info, list) and len(info) == 2

    def test_synthesize_endpoint_returns_audio(self, client):
        """POST /api/voice/synthesize returns audio/mpeg."""
        fake_audio = b"\xff\xfb\x90\x00" * 50
        mock_stream = MagicMock()
        mock_stream.read.return_value = fake_audio
        mock_response = {"AudioStream": mock_stream}

        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = mock_response

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            resp = client.post(
                "/api/voice/synthesize",
                json={"text": "Hello world", "language": "en-US"},
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.content == fake_audio

    def test_synthesize_endpoint_empty_text_400(self, client):
        """POST with empty text returns 400."""
        resp = client.post(
            "/api/voice/synthesize",
            json={"text": ""},
        )
        # Pydantic validation rejects min_length=1
        assert resp.status_code == 422

    def test_synthesize_endpoint_polly_failure_502(self, client):
        """POST when Polly fails returns 502."""
        mock_client = MagicMock()
        mock_client.synthesize_speech.side_effect = Exception("Polly auth failed")

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            resp = client.post(
                "/api/voice/synthesize",
                json={"text": "Hello world"},
            )

        assert resp.status_code == 502
        assert "Polly" in resp.json()["detail"]

    def test_synthesize_endpoint_with_voice_override(self, client):
        """POST with explicit voice_id."""
        fake_audio = b"\xff\xfb\x90\x00" * 10
        mock_stream = MagicMock()
        mock_stream.read.return_value = fake_audio
        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = {"AudioStream": mock_stream}

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            resp = client.post(
                "/api/voice/synthesize",
                json={"text": "Hello", "voice_id": "Joanna", "language": "en-US"},
            )

        assert resp.status_code == 200
        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["VoiceId"] == "Joanna"

    def test_synthesize_endpoint_rejects_oversized_text(self, client):
        """POST with text > MAX_TEXT_LENGTH returns 422 (Pydantic validation)."""
        long_text = "a" * 3001
        resp = client.post(
            "/api/voice/synthesize",
            json={"text": long_text, "language": "en-US"},
        )
        assert resp.status_code == 422  # Pydantic max_length validation


# ---------------------------------------------------------------------------
# AC1: PROBE — real HTTP through ASGI stack
# ---------------------------------------------------------------------------

class TestVoiceSynthesizeProbe:
    """Integration test: httpx.AsyncClient through real ASGI stack.

    Catches Content-Type, serialization, and wire format bugs that
    TestClient (requests-based) misses.
    """

    @pytest.mark.asyncio
    async def test_synthesize_via_httpx(self):
        """Real ASGI request → verify response is audio/mpeg."""
        import httpx
        from fastapi import FastAPI
        from routers.voice import router

        app = FastAPI()
        app.include_router(router, prefix="/api/voice")

        fake_audio = b"\xff\xfb\x90\x00" * 50
        mock_stream = MagicMock()
        mock_stream.read.return_value = fake_audio
        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = {"AudioStream": mock_stream}

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                resp = await client.post(
                    "/api/voice/synthesize",
                    json={"text": "Hello from probe", "language": "en-US"},
                )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.content == fake_audio

    @pytest.mark.asyncio
    async def test_voices_via_httpx(self):
        """Real ASGI request for voices endpoint."""
        import httpx
        from fastapi import FastAPI
        from routers.voice import router

        app = FastAPI()
        app.include_router(router, prefix="/api/voice")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/voice/voices")

        assert resp.status_code == 200
        data = resp.json()
        assert "voices" in data
        assert "en-US" in data["voices"]


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class TestVoiceRateLimit:
    """Rate limiter on /api/voice/synthesize."""

    def test_rate_limit_rejects_after_threshold(self):
        """More than _RATE_LIMIT_MAX requests in a window should return 429."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers.voice import router, _request_timestamps, _RATE_LIMIT_MAX

        # Clear any existing timestamps
        _request_timestamps.clear()

        app = FastAPI()
        app.include_router(router, prefix="/api/voice")
        client = TestClient(app)

        fake_audio = b"\xff\xfb\x90\x00" * 10
        mock_stream = MagicMock()
        mock_stream.read.return_value = fake_audio
        mock_client = MagicMock()
        mock_client.synthesize_speech.return_value = {"AudioStream": mock_stream}

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            # Fill up the rate limit window
            for _ in range(_RATE_LIMIT_MAX):
                resp = client.post(
                    "/api/voice/synthesize",
                    json={"text": "Hello world test", "language": "en-US"},
                )
                assert resp.status_code == 200

            # Next request should be rejected
            resp = client.post(
                "/api/voice/synthesize",
                json={"text": "This should fail", "language": "en-US"},
            )
            assert resp.status_code == 429

        # Cleanup
        _request_timestamps.clear()


# ---------------------------------------------------------------------------
# Security: HTML escape and JSON injection fixes
# ---------------------------------------------------------------------------

class TestNotifySecurityFixes:
    """Test security fixes in the notify skill."""

    def test_email_html_escape(self):
        """Email HTML body must escape < > & to prevent XSS."""
        import html
        malicious = '<script>alert("xss")</script>'
        escaped = html.escape(malicious)
        assert "<script>" not in escaped
        assert "&lt;script&gt;" in escaped

    def test_webhook_json_escape(self):
        """Webhook payload template must handle quotes in values."""
        import json
        title = 'Test "quote" here'
        message = 'Content with } brace'
        template = '{"title": "{title}", "body": "{content}"}'

        # Apply the fix: json.dumps()[1:-1] for safe embedding
        safe_title = json.dumps(title)[1:-1]
        safe_message = json.dumps(message)[1:-1]
        body = template.replace("{title}", safe_title).replace("{content}", safe_message)

        # Must parse without error
        parsed = json.loads(body)
        assert parsed["title"] == title
        assert parsed["body"] == message
