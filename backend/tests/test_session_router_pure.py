"""Tests for pure functions in session_router.py.

These functions have zero external dependencies (no async, no DB, no SDK).
Direct import + exhaustive path coverage.

Targets:
- _extract_query_keywords: keyword extraction from user messages
- _get_access_hint: file extension → guidance string mapping
"""
import pytest

from core.session_router import _extract_query_keywords, _get_access_hint


class TestExtractQueryKeywords:
    """Exhaustive path coverage for _extract_query_keywords."""

    def test_empty_string(self):
        assert _extract_query_keywords("") == ""

    def test_none_input(self):
        assert _extract_query_keywords(None) == ""

    def test_too_short(self):
        """< 3 chars after strip → empty."""
        assert _extract_query_keywords("hi") == ""
        assert _extract_query_keywords("  x ") == ""

    def test_only_stop_words(self):
        """All words are stop words → empty."""
        assert _extract_query_keywords("the this that") == ""

    def test_conversational_filler_stripped(self):
        """Leading filler (hey, hi, please, can you, etc) removed."""
        result = _extract_query_keywords("hey can you check the deployment")
        assert "hey" not in result.lower()
        assert "deployment" in result.lower()

    def test_url_stripped(self):
        """URLs removed before extraction."""
        result = _extract_query_keywords("check https://github.com/repo/issues and fix bugs")
        assert "github" not in result
        assert "bugs" in result.lower()

    def test_file_path_stripped(self):
        """File paths starting with / or ~ removed."""
        result = _extract_query_keywords("read ~/Documents/report.md and summarize")
        assert "Documents" not in result
        assert "summarize" in result.lower()

    def test_hyphenated_compounds(self):
        """Hyphenated terms preserved as single tokens (e.g. session-router)."""
        result = _extract_query_keywords("fix the session-router bug in pre-tool-use")
        assert "session-router" in result
        assert "pre-tool-use" in result

    def test_english_words_extracted(self):
        """Substantive English words (>2 chars, not stop words) extracted."""
        result = _extract_query_keywords("implement the database migration for users table")
        words = result.split()
        assert "implement" in words
        assert "database" in words
        assert "migration" in words
        # Stop words excluded
        assert "the" not in words
        assert "for" not in words

    def test_cjk_characters_extracted(self):
        """CJK runs preserved as search terms."""
        result = _extract_query_keywords("帮我分析这个竞品")
        assert "帮我分析这个竞品" in result or any(
            c in result for c in "分析竞品"
        )

    def test_mixed_english_cjk(self):
        """Mixed messages extract both English and CJK terms."""
        result = _extract_query_keywords("analyze 竞品分析 report for SwarmAI")
        assert "analyze" in result.lower() or "SwarmAI" in result
        # CJK should be present
        assert "竞品分析" in result

    def test_max_terms_capped(self):
        """Output capped at reasonable length (compounds:3 + words:10 + cjk:5)."""
        long_msg = " ".join(f"word{i}" for i in range(50))
        result = _extract_query_keywords(long_msg)
        terms = result.split()
        assert len(terms) <= 18  # 3 + 10 + 5

    def test_short_words_excluded(self):
        """Words ≤2 chars excluded (except CJK)."""
        result = _extract_query_keywords("go to db and fix it now")
        words = result.split()
        # "go", "to", "db", "it" should not be in results (2 chars or stop)
        assert "go" not in words
        assert "to" not in words

    def test_filler_only_message(self):
        """Message that's only filler after strip → empty."""
        assert _extract_query_keywords("hey please help") == ""
        assert _extract_query_keywords("hello can you") == ""


class TestGetAccessHint:
    """Exhaustive path coverage for _get_access_hint."""

    @pytest.mark.parametrize("ext,expected_substring", [
        (".pdf", "Read tool to read this PDF"),
        (".pptx", "s_pptx"),
        (".ppt", "s_pptx"),
        (".docx", "s_docx"),
        (".doc", "s_docx"),
        (".xlsx", "s_xlsx"),
        (".xls", "s_xlsx"),
        (".mp3", "s_whisper-transcribe"),
        (".m4a", "s_whisper-transcribe"),
        (".wav", "s_whisper-transcribe"),
        (".ogg", "s_whisper-transcribe"),
        (".flac", "s_whisper-transcribe"),
        (".aac", "s_whisper-transcribe"),
        (".mp4", "video file"),
        (".mov", "video file"),
        (".avi", "video file"),
        (".mkv", "video file"),
        (".webm", "video file"),
        (".png", "Read tool to view this image"),
        (".jpg", "Read tool to view this image"),
        (".jpeg", "Read tool to view this image"),
        (".gif", "Read tool to view this image"),
        (".webp", "Read tool to view this image"),
        (".svg", "non-native image"),
        (".bmp", "non-native image"),
        (".tiff", "non-native image"),
        (".tif", "non-native image"),
        (".heic", "non-native image"),
        (".heif", "non-native image"),
        (".py", "Read tool to read this text file"),
        (".md", "Read tool to read this text file"),
        (".ts", "Read tool to read this text file"),
        (".json", "Read tool to read this text file"),
    ])
    def test_known_extensions(self, ext, expected_substring):
        """Each known extension returns appropriate guidance."""
        result = _get_access_hint(ext, f"test{ext}")
        assert expected_substring in result, f"ext={ext}, got: {result}"

    def test_unknown_extension_fallback(self):
        """Unknown extension returns generic Read hint."""
        result = _get_access_hint(".xyz", "data.xyz")
        assert "Read tool" in result

    def test_case_insensitive(self):
        """Extension matching is case-insensitive."""
        result = _get_access_hint(".PDF", "report.PDF")
        assert "PDF" in result

    def test_uppercase_extension(self):
        """.XLSX (uppercase) still matches."""
        result = _get_access_hint(".XLSX", "data.XLSX")
        assert "s_xlsx" in result
