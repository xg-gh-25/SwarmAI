"""Tests for pure functions in session_router.py.

These functions have zero external dependencies (no async, no DB, no SDK).
Direct import + exhaustive path coverage.

Targets:
- _extract_query_keywords: keyword extraction from user messages
- _get_access_hint: file extension → guidance string mapping
"""
import pytest

from core.session_router import _extract_query_keywords, _get_access_hint


class _FakeOpts:
    """Minimal stand-in for the SDK options object (only system_prompt matters)."""
    def __init__(self, base="BASE PROMPT"):
        self.system_prompt = base


class TestUnifiedRecallCfull:
    """C-full (run_ccd1b6c5): runtime recall unified onto recall_all's 5-domain
    path (minus ddd, which keeps its own pre-gate leg). Strangler-fig fallback."""

    def test_unified_body_covers_four_domains_not_ddd(self):
        """The unified body surfaces the 4 keyword-gated domains incl codeintel
        (the net-new one), and NOT ddd (excluded to avoid double-inject). Signal-1
        editor path resolves the SwarmAI project so codeintel has a graph."""
        from core.session_router import _unified_recall_body
        # 2nd arg is now editor_file_path (project detected inside, off-loop).
        s = _unified_recall_body(
            "session resume timeout",
            "/x/SwarmWS/Projects/SwarmAI/TECH.md",
        )
        assert "Code Symbols" in s, "codeintel is the net-new runtime domain"
        assert "[DDD:" not in s, "ddd must NOT be in unified path (own leg)"

    def test_unified_failclosed_no_project_still_recalls(self):
        """No editor path + a query that resolves to no single project →
        codeintel empty, but the other domains still recall (recall never
        degrades to empty just because there's no active project)."""
        from core.session_router import _unified_recall_body
        s = _unified_recall_body("resume cold start latency", None)
        assert "Code Symbols" not in s and len(s) > 100

    def test_unified_exception_returns_empty_for_fallback(self):
        """Gate-2 C1: an EXCEPTION in the unified path (not just empty result)
        must return "" so the caller falls back to legacy — NOT escape and leave
        recall empty. Fault-inject recall_all to raise; assert "" (fallback trigger)."""
        import core.recall_multi as rm
        from core.session_router import _unified_recall_body
        orig = rm.recall_all
        rm.recall_all = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            assert _unified_recall_body("test query", None) == "", \
                "unified exception must return '' so caller falls back to legacy"
        finally:
            rm.recall_all = orig

    def test_m4_drift_single_source_shared(self):
        """M4 drift-elimination: the unified runtime path and recall_all CLI share
        ONE library-recall function. Mutating _recall_library affects BOTH — proven
        by asserting both call the SAME symbol (not two divergent copies)."""
        import core.recall_multi as rm
        from core.session_router import _unified_recall_body
        calls = {"n": 0}
        orig = rm._recall_library
        def spy(*a, **k):
            calls["n"] += 1
            return orig(*a, **k)
        rm._recall_library = spy
        try:
            _unified_recall_body("session resume", None)   # runtime path
            rm.recall_all("session resume", domains=("library",))  # CLI path
        finally:
            rm._recall_library = orig
        # BOTH entry points routed through the same _recall_library → no drift.
        assert calls["n"] == 2, f"expected shared single-source, got {calls['n']} calls"


class TestDddRuntimeInjection:
    """M2 (run_91bc0651, DDD-alive) E1/E4/E5: runtime DDD injection into the
    system prompt, fail-closed, with anti-silent-death counter. Drives the REAL
    _inject_ddd_for_active_project (no mock of the function under test) against
    the REAL CMHK_SalesIntel DDD docs on disk (GUI32/PIT13: exercise real path)."""

    def test_e1_editor_path_injects_ddd_provenance(self):
        """E1: signal-1 editor path → system_prompt gains [DDD:<project>] token
        (assert the PROVENANCE token, NOT string length — L2 anti-vacuity)."""
        from core.session_router import _inject_ddd_for_active_project
        o = _FakeOpts()
        _inject_ddd_for_active_project(
            o, "weekly revenue forecast baseline",
            "/x/SwarmWS/Projects/CMHK_SalesIntel/TECH.md",
        )
        assert "[DDD:CMHK_SalesIntel]" in o.system_prompt, o.system_prompt
        assert o.system_prompt.startswith("BASE PROMPT"), "base must be preserved"

    def test_e4_no_signal_injects_nothing(self):
        """E4 fail-closed: no active project → NO [DDD:] token injected,
        prompt byte-unchanged.

        Asserts the SPECIFIC decline reason `declined:no_signal`, not merely the
        absence of injection: a downstream `no_ddd_hits` gate would ALSO produce
        'no [DDD:]' for a wrongly-selected project on a garbage query, masking a
        broken fail-closed. Keying on the reason gives the test real teeth —
        mutation-verified (break the no_signal return → this goes RED)."""
        from core.session_router import (
            _inject_ddd_for_active_project, _ddd_inject_count,
        )
        _ddd_inject_count.clear()
        o = _FakeOpts()
        _inject_ddd_for_active_project(o, "hello how are you", None)
        assert "[DDD:" not in o.system_prompt
        assert o.system_prompt == "BASE PROMPT"
        # teeth: decline must be at the DETECTION stage, not a downstream gate.
        assert _ddd_inject_count.get("declined:no_signal", 0) == 1, \
            f"expected fail-closed at detection; got {dict(_ddd_inject_count)}"

    def test_e4_ambiguous_injects_nothing(self):
        """E4: ambiguous query (2 project matches) → fail-closed at detection."""
        from core.session_router import (
            _inject_ddd_for_active_project, _ddd_inject_count,
        )
        _ddd_inject_count.clear()
        o = _FakeOpts()
        _inject_ddd_for_active_project(o, "compare cmhk and github community", None)
        assert "[DDD:" not in o.system_prompt
        assert _ddd_inject_count.get("declined:ambiguous", 0) == 1, \
            f"expected fail-closed on ambiguity; got {dict(_ddd_inject_count)}"

    def test_e5_counter_distinguishes_injected_from_declined(self):
        """E5 anti-silent-death (Gate-2 L1): the counter must record BOTH an
        inject AND a decline — proving the detector isn't permanently failing
        closed (which would be byte-identical to 'correctly declined')."""
        from core.session_router import (
            _inject_ddd_for_active_project, _ddd_inject_count,
        )
        _ddd_inject_count.clear()
        # one injected (signal-1) + one declined (no signal)
        _inject_ddd_for_active_project(
            _FakeOpts(), "weekly revenue",
            "/x/SwarmWS/Projects/CMHK_SalesIntel/TECH.md",
        )
        _inject_ddd_for_active_project(_FakeOpts(), "hi", None)
        assert _ddd_inject_count.get("injected", 0) >= 1, _ddd_inject_count
        assert any(k.startswith("declined:") for k in _ddd_inject_count), \
            f"no declined outcome recorded — counter can't detect silent-death: {dict(_ddd_inject_count)}"


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
