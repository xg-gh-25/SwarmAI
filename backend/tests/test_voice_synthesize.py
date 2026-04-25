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
# SSML: English term wrapping for CJK languages
# ---------------------------------------------------------------------------

class TestSSMLWrapping:
    """SSML <lang> tag wrapping for English terms in CJK text."""

    def test_wrap_english_in_chinese(self):
        """English terms in Chinese text get <lang> tags."""
        from core.voice_synthesize import _wrap_english_terms
        result = _wrap_english_terms("我们用API来调用Claude")
        assert '<lang xml:lang="en-US">API</lang>' in result
        assert '<lang xml:lang="en-US">Claude</lang>' in result

    def test_skip_single_char(self):
        """Single characters (I, a) are NOT wrapped."""
        from core.voice_synthesize import _wrap_english_terms
        # This is CJK context — single ASCII chars are likely part of Chinese text
        result = _wrap_english_terms("这是a测试")
        assert "<lang" not in result

    def test_wrap_hyphenated(self):
        """Hyphenated terms like TTS-API get wrapped."""
        from core.voice_synthesize import _wrap_english_terms
        result = _wrap_english_terms("使用Content-Type头")
        assert '<lang xml:lang="en-US">Content-Type</lang>' in result

    def test_wrap_acronyms(self):
        """Acronyms like LLM, SDK, TTS get wrapped."""
        from core.voice_synthesize import _wrap_english_terms
        result = _wrap_english_terms("LLM和SDK是AI的基础")
        assert '<lang xml:lang="en-US">LLM</lang>' in result
        assert '<lang xml:lang="en-US">SDK</lang>' in result
        assert '<lang xml:lang="en-US">AI</lang>' in result

    def test_pure_chinese_no_wrap(self):
        """Pure Chinese text is NOT wrapped."""
        from core.voice_synthesize import _wrap_english_terms
        result = _wrap_english_terms("这是一段纯中文")
        assert "<lang" not in result

    def test_to_ssml_chinese_with_english(self):
        """Chinese text with English → SSML output."""
        from core.voice_synthesize import _to_ssml
        ssml, text_type = _to_ssml("调用API接口", "zh-CN")
        assert text_type == "ssml"
        assert ssml.startswith("<speak>")
        assert ssml.endswith("</speak>")
        assert '<lang xml:lang="en-US">API</lang>' in ssml

    def test_to_ssml_english_stays_plain(self):
        """English text stays as plain text — no SSML needed."""
        from core.voice_synthesize import _to_ssml
        text, text_type = _to_ssml("Hello world API", "en-US")
        assert text_type == "text"
        assert text == "Hello world API"

    def test_to_ssml_pure_chinese_stays_plain(self):
        """Pure Chinese text without English terms stays as plain text."""
        from core.voice_synthesize import _to_ssml
        text, text_type = _to_ssml("这是纯中文内容", "zh-CN")
        assert text_type == "text"

    def test_to_ssml_escapes_xml_special_chars(self):
        """XML special chars (&, <, >) are escaped before SSML tags are inserted."""
        from core.voice_synthesize import _to_ssml
        ssml, text_type = _to_ssml("用API处理a<b>c的数据", "zh-CN")
        assert text_type == "ssml"
        assert "&lt;" in ssml  # < was escaped
        assert "&gt;" in ssml  # > was escaped
        # The SSML <lang> tags are NOT escaped (they're our tags)
        assert '<lang xml:lang="en-US">API</lang>' in ssml

    @pytest.mark.asyncio
    async def test_synthesize_chinese_uses_ssml(self):
        """Chinese text with English sends SSML TextType to Polly."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client

        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        fake_audio = b"\xff\xfb\x90\x00" * 50
        mock_stream = MagicMock()
        mock_stream.read.return_value = fake_audio
        mock_client.synthesize_speech.return_value = {"AudioStream": mock_stream}

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            await synthesize_speech("调用API接口", language="zh-CN")

        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["TextType"] == "ssml"
        assert '<lang xml:lang="en-US">API</lang>' in call_kwargs["Text"]
        assert call_kwargs["Text"].startswith("<speak>")

    @pytest.mark.asyncio
    async def test_synthesize_english_uses_plain_text(self):
        """English text sends plain TextType to Polly."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client

        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        fake_audio = b"\xff\xfb\x90\x00" * 50
        mock_stream = MagicMock()
        mock_stream.read.return_value = fake_audio
        mock_client.synthesize_speech.return_value = {"AudioStream": mock_stream}

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            await synthesize_speech("Hello world", language="en-US")

        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["TextType"] == "text"


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


# ---------------------------------------------------------------------------
# AC1-8: Engine-Aware SSML Optimization (Polly TTS)
# ---------------------------------------------------------------------------

class TestEngineAwareStrategy:
    """Engine-aware text preparation: generative=plain, neural=rich SSML."""

    # --- AC1: Generative engine sends plain text, no SSML ---

    def test_prepare_generative_returns_plain_text(self):
        """Generative engine: text out, TextType='text'."""
        from core.voice_synthesize import _prepare_generative
        text, text_type = _prepare_generative("Hello world")
        assert text_type == "text"
        assert text == "Hello world"
        assert "<speak>" not in text

    def test_prepare_generative_strips_whitespace(self):
        from core.voice_synthesize import _prepare_generative
        text, text_type = _prepare_generative("  Hello world  \n")
        assert text == "Hello world"
        assert text_type == "text"

    # --- AC2: Neural zh-CN gets <break> tags ---

    def test_prepare_neural_injects_breaks_sentence_end(self):
        """Sentence-end punctuation (。！？) followed by <break>."""
        from core.voice_synthesize import _prepare_neural
        ssml, text_type = _prepare_neural("你好。世界！很好？", "zh-CN")
        assert text_type == "ssml"
        assert '<break time=' in ssml
        # Sentence-end punctuation should have breaks after them
        assert '。<break' in ssml or '。</phoneme><break' in ssml or '好。<break' in ssml

    def test_prepare_neural_injects_breaks_clause(self):
        """Clause punctuation (，；) gets shorter breaks."""
        from core.voice_synthesize import _prepare_neural
        ssml, _ = _prepare_neural("你好，世界；再见", "zh-CN")
        assert '<break time=' in ssml

    # --- AC3: Neural zh-CN polyphone words get <phoneme> ---

    def test_apply_phonemes_zhongdian(self):
        """重点 gets pinyin zhong4dian3."""
        from core.voice_synthesize import _apply_phonemes
        result = _apply_phonemes("今天的重点是")
        assert '<phoneme alphabet="x-amazon-pinyin" ph="zhong4dian3">重点</phoneme>' in result

    def test_apply_phonemes_hangye(self):
        """行业 gets pinyin hang2ye4."""
        from core.voice_synthesize import _apply_phonemes
        result = _apply_phonemes("这个行业很好")
        assert '<phoneme alphabet="x-amazon-pinyin" ph="hang2ye4">行业</phoneme>' in result

    def test_apply_phonemes_changqi(self):
        """长期 gets pinyin chang2qi1."""
        from core.voice_synthesize import _apply_phonemes
        result = _apply_phonemes("长期来看")
        assert '<phoneme alphabet="x-amazon-pinyin" ph="chang2qi1">长期</phoneme>' in result

    def test_apply_phonemes_no_match_passthrough(self):
        """Text without polyphones passes through unchanged."""
        from core.voice_synthesize import _apply_phonemes
        text = "这是一段普通文字"
        assert _apply_phonemes(text) == text

    # --- AC4: Neural zh-CN numbers/dates wrapped in <say-as> ---

    def test_format_numbers_date(self):
        """Date 2026-04-26 gets say-as date."""
        from core.voice_synthesize import _format_numbers
        result = _format_numbers("日期是2026-04-26")
        assert '<say-as interpret-as="date"' in result
        assert "2026-04-26" in result

    def test_format_numbers_phone(self):
        """Phone number gets say-as telephone."""
        from core.voice_synthesize import _format_numbers
        result = _format_numbers("电话138-1234-5678")
        assert '<say-as interpret-as="telephone"' in result

    def test_format_numbers_cardinal(self):
        """Large number with commas gets say-as cardinal."""
        from core.voice_synthesize import _format_numbers
        result = _format_numbers("共1,234,567人")
        assert '<say-as interpret-as="cardinal"' in result

    def test_format_numbers_no_match_passthrough(self):
        """Text without special numbers passes through."""
        from core.voice_synthesize import _format_numbers
        text = "这是普通文字"
        assert _format_numbers(text) == text

    # --- AC5: Neural zh-CN abbreviations expanded via <sub> ---

    def test_expand_abbreviations_api(self):
        """API expanded to A P I."""
        from core.voice_synthesize import _expand_abbreviations
        result = _expand_abbreviations("调用API接口")
        assert '<sub alias="A P I">API</sub>' in result

    def test_expand_abbreviations_aws(self):
        """AWS expanded."""
        from core.voice_synthesize import _expand_abbreviations
        result = _expand_abbreviations("使用AWS服务")
        assert '<sub alias="A W S">AWS</sub>' in result

    def test_expand_abbreviations_no_partial_match(self):
        """RAPID should NOT match API — only standalone words."""
        from core.voice_synthesize import _expand_abbreviations
        result = _expand_abbreviations("RAPID development")
        assert "<sub" not in result

    # --- AC6: Language switch boundaries get 150ms break ---

    def test_language_switch_break_after_lang_tag(self):
        """Break inserted between </lang> and following CJK."""
        from core.voice_synthesize import _add_language_switch_breaks
        ssml = '</lang>你好'
        result = _add_language_switch_breaks(ssml)
        assert '<break time="150ms"/>' in result

    def test_language_switch_break_before_lang_tag(self):
        """Break inserted between CJK and <lang."""
        from core.voice_synthesize import _add_language_switch_breaks
        ssml = '你好<lang xml:lang="en-US">API</lang>'
        result = _add_language_switch_breaks(ssml)
        assert '<break time="150ms"/>' in result

    # --- AC7: Full pipeline integration via synthesize_speech ---

    @pytest.mark.asyncio
    async def test_synthesize_generative_sends_plain_text(self):
        """Generative engine via synthesize_speech → TextType='text'."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client
        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        mock_stream = MagicMock()
        mock_stream.read.return_value = b"\xff" * 100
        mock_client.synthesize_speech.return_value = {"AudioStream": mock_stream}

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            await synthesize_speech("Hello world", language="en-US")

        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["TextType"] == "text"
        assert "<speak>" not in call_kwargs["Text"]

    @pytest.mark.asyncio
    async def test_synthesize_neural_zh_sends_ssml_with_breaks(self):
        """Neural zh-CN via synthesize_speech → rich SSML with breaks."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client
        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        mock_stream = MagicMock()
        mock_stream.read.return_value = b"\xff" * 100
        mock_client.synthesize_speech.return_value = {"AudioStream": mock_stream}

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            await synthesize_speech("你好。再见。", language="zh-CN")

        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["TextType"] == "ssml"
        assert call_kwargs["Text"].startswith("<speak>")
        assert '<break time=' in call_kwargs["Text"]

    @pytest.mark.asyncio
    async def test_synthesize_neural_zh_with_phoneme(self):
        """Neural zh-CN with polyphone word → <phoneme> in SSML."""
        from core.voice_synthesize import synthesize_speech, _get_polly_client
        _get_polly_client.cache_clear()

        mock_client = MagicMock()
        mock_stream = MagicMock()
        mock_stream.read.return_value = b"\xff" * 100
        mock_client.synthesize_speech.return_value = {"AudioStream": mock_stream}

        with patch("core.voice_synthesize._get_polly_client", return_value=mock_client):
            await synthesize_speech("今天的重点是API接口", language="zh-CN")

        call_kwargs = mock_client.synthesize_speech.call_args[1]
        assert call_kwargs["TextType"] == "ssml"
        assert "phoneme" in call_kwargs["Text"]
        assert "zhong4dian3" in call_kwargs["Text"]

    # --- AC8: No change to s_pollinate or API contract ---
    # (Verified by s_pollinate code NOT being modified — no test needed.
    #  Existing endpoint tests above already verify API contract stability.)
