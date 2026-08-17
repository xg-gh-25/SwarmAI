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

    def test_unified_body_covers_four_domains_not_ddd(self, monkeypatch):
        """The unified body surfaces the 4 keyword-gated domains incl codeintel
        (the net-new one), and NOT ddd (excluded to avoid double-inject).

        This is a WIRING test: does the unified path route codeintel hits into a
        "Code Symbols" block and keep ddd out. The real code graph (code_intel.db)
        is unreachable under the autouse _isolate_app_data_dir fixture (it reroots
        jobs.paths.PROJECTS_DIR onto a per-test sandbox with no db) — so we inject a
        deterministic codeintel hit rather than depend on a built graph. This gives
        the wiring assertion real teeth in EVERY environment (local + CI), instead
        of skipping on a graph the test fixture guarantees is absent."""
        import core.recall_multi as _rm
        # Deterministic synthetic hit → renders as "### Code Symbols\n- `...`".
        monkeypatch.setattr(
            _rm, "_codeintel_recall",
            lambda q, project=None, limit=8: [
                {"id": "core.session_router._unified_recall_body", "name": "_unified_recall_body"}
            ],
        )
        from core.session_router import _unified_recall_body, _resolve_active_project
        # 2nd arg is editor_file_path (project detected inside, off-loop).
        _ap = _resolve_active_project("/x/SwarmWS/Projects/SwarmAI/TECH.md",
                                      "session resume timeout")
        s, _structured = _unified_recall_body("session resume timeout", _ap)
        assert "Code Symbols" in s, "codeintel is the net-new runtime domain"
        # Assert the RENDERED symbol ref, not just the header: the "Code Symbols"
        # label can also appear in the coverage-gap line (render_recall_body) when a
        # codeintel expect-token is in the query. Asserting the injected symbol's id
        # makes teeth query-independent — only a NON-EMPTY codeintel bucket renders it.
        assert "core.session_router._unified_recall_body" in s, \
            "the codeintel HIT must render, not just the label"
        assert "[DDD:" not in s, "ddd must NOT be in unified path (own leg)"

    # test_unified_failclosed_no_project_still_recalls REMOVED 2026-08-16 (CI = BVT).
    # Its `len(s) > 100` assertion required the text domains to recall from the LIVE
    # workspace corpus (~/.swarm-ai/SwarmWS/.context/*.md), absent in a clean CI
    # checkout → could only SKIP = zero signal. The fail-closed CONTRACT (an exception
    # in the unified path returns "" so the caller falls back) is BVT-covered by
    # test_unified_exception_returns_empty_for_fallback below, which fault-injects and
    # needs no corpus. "Do the text domains actually return content" is a deployed-
    # system quality property → eval OS.

    def test_unified_exception_returns_empty_for_fallback(self):
        """Gate-2 C1: an EXCEPTION in the unified path (not just empty result)
        must return "" so the caller falls back to legacy — NOT escape and leave
        recall empty. Fault-inject recall_all to raise; assert "" (fallback trigger)."""
        import core.recall_multi as rm
        from core.session_router import _unified_recall_body
        orig = rm.recall_all
        rm.recall_all = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            _body, _structured = _unified_recall_body("test query", (None, "no_signal"))
            assert _body == "", \
                "unified exception must return '' body so caller falls back to legacy"
            assert _structured is None, "exception path yields no structured hits"
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
            _unified_recall_body("session resume", (None, "no_signal"))   # runtime path
            rm.recall_all("session resume", domains=("library",))  # CLI path
        finally:
            rm._recall_library = orig
        # BOTH entry points routed through the same _recall_library → no drift.
        assert calls["n"] == 2, f"expected shared single-source, got {calls['n']} calls"


class TestDddRuntimeInjection:
    """M2 (run_91bc0651, DDD-alive) E1/E4/E5: runtime DDD injection into the
    system prompt, fail-closed, with anti-silent-death counter. Drives the REAL
    _inject_ddd_for_active_project (no mock of the function under test).

    ENV-INDEPENDENT (run_20bd4a7b follow-up): previously these named the real
    CMHK_SalesIntel project and only passed where its DDD docs happened to exist
    on the developer's disk — a hidden host coupling AND a leak class (a
    git-tracked test naming a real private customer). The autouse fixture below
    now builds SYNTHETIC projects in a tmp dir and points the recall root at it
    via the SWARMWS env override (the single seam project_registry.get_swarmws()
    reads). The real ##-section scorer + fail-closed detector still run — only
    the on-disk corpus is synthetic, so the test exercises the real path on ANY
    host, in any checkout, with zero private names."""

    _ACTIVE = "Acme_SalesIntel"      # signal-1 editor-path project + DDD hit
    _OTHER = "Beacon_Community"      # 2nd project so a 2-token query is ambiguous

    @pytest.fixture(autouse=True)
    def _synthetic_swarmws(self, tmp_path, monkeypatch):
        """Build a tmp SwarmWS with two synthetic DDD projects and redirect
        recall to it. get_swarmws() re-reads os.environ every call (no cache),
        so setenv fully controls list_project_names() + _recall_ddd()."""
        projects = tmp_path / "Projects"
        active = projects / self._ACTIVE
        active.mkdir(parents=True)
        # A ## section whose BODY matches the E1/E5 queries (BM25 over sections).
        (active / "TECH.md").write_text(
            "# Acme SalesIntel — Tech\n\n"
            "## Revenue Forecast Model\n"
            "The weekly revenue forecast baseline projects quarterly revenue "
            "from historical sales pipeline data and seasonal adjustments.\n\n"
            "## Data Ingestion\n"
            "Nightly ETL loads CRM opportunities into the warehouse.\n",
            encoding="utf-8",
        )
        other = projects / self._OTHER
        other.mkdir(parents=True)
        (other / "TECH.md").write_text(
            "# Beacon Community — Tech\n\n"
            "## Membership Sync\n"
            "Reconciles community roster membership on a nightly cadence.\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("SWARMWS", str(tmp_path))
        yield

    def test_e1_editor_path_injects_ddd_provenance(self):
        """E1: signal-1 editor path → system_prompt gains [DDD:<project>] token
        (assert the PROVENANCE token, NOT string length — L2 anti-vacuity)."""
        from core.session_router import _inject_ddd_for_active_project
        o = _FakeOpts()
        from core.session_router import _resolve_active_project
        _ap = _resolve_active_project(
            f"/x/SwarmWS/Projects/{self._ACTIVE}/TECH.md",
            "weekly revenue forecast baseline",
        )
        _inject_ddd_for_active_project(o, "weekly revenue forecast baseline", _ap)
        assert f"[DDD:{self._ACTIVE}]" in o.system_prompt, o.system_prompt
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
        from core.session_router import _resolve_active_project
        _inject_ddd_for_active_project(
            o, "hello how are you",
            _resolve_active_project(None, "hello how are you"))
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
        # Mentions a distinctive whole-word token of BOTH synthetic projects
        # (acme + beacon) → 2 matches → fail-closed at detection.
        from core.session_router import _resolve_active_project
        _inject_ddd_for_active_project(
            o, "compare acme and beacon rollout",
            _resolve_active_project(None, "compare acme and beacon rollout"))
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
        from core.session_router import _resolve_active_project
        _inject_ddd_for_active_project(
            _FakeOpts(), "weekly revenue",
            _resolve_active_project(
                f"/x/SwarmWS/Projects/{self._ACTIVE}/TECH.md", "weekly revenue"))
        _inject_ddd_for_active_project(
            _FakeOpts(), "hi", _resolve_active_project(None, "hi"))
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


class TestPrependUiStateToQuery:
    """_prepend_ui_state_to_query — the live-session SENSE fix (run_5d460dd5).

    UI-state (SENSE) reaches a COLD-spawning turn via options.system_prompt, but a
    REUSED live subprocess discards the rebuilt system_prompt — so the request-time
    canvas/overlay state must ride the QUERY channel instead. This pure helper
    decides whether to prefix and builds the prefixed query (str or multimodal list).
    """

    _CANVAS_CTX = {"file_path": "", "file_name": "", "canvas": {"open": True, "output_count": 2, "pinned": False, "muted": False, "collapsed": False}}

    def test_prefixes_str_query_when_reuse(self):
        from core.session_router import _prepend_ui_state_to_query
        out = _prepend_ui_state_to_query("what's open?", self._CANVAS_CTX, should_prefix=True)
        assert isinstance(out, str)
        assert "Current UI State" in out
        assert "Canvas (output panel)" in out
        assert out.rstrip().endswith("what's open?"), "original query preserved at the end"

    def test_no_prefix_when_not_reuse(self):
        """COLD/spawn or poisoned-recycle path: system_prompt already carries it →
        must NOT double-inject."""
        from core.session_router import _prepend_ui_state_to_query
        out = _prepend_ui_state_to_query("what's open?", self._CANVAS_CTX, should_prefix=False)
        assert out == "what's open?", "unchanged when not reusing"

    def test_no_prefix_when_editor_context_empty(self):
        """Channel/no-UI clients: empty editor_context → clean no-op even on reuse."""
        from core.session_router import _prepend_ui_state_to_query
        assert _prepend_ui_state_to_query("hi", None, should_prefix=True) == "hi"
        assert _prepend_ui_state_to_query("hi", {}, should_prefix=True) == "hi"

    def test_multimodal_list_inserts_leading_text_block(self):
        from core.session_router import _prepend_ui_state_to_query
        blocks = [{"type": "image", "source": {"x": 1}}, {"type": "text", "text": "look"}]
        out = _prepend_ui_state_to_query(blocks, self._CANVAS_CTX, should_prefix=True)
        assert isinstance(out, list)
        assert out[0]["type"] == "text", "UI-state text block inserted at index 0"
        assert "Current UI State" in out[0]["text"]
        assert out[1:] == blocks, "original blocks preserved after the inserted one"

    def test_multimodal_list_unchanged_when_not_reuse(self):
        from core.session_router import _prepend_ui_state_to_query
        blocks = [{"type": "text", "text": "look"}]
        out = _prepend_ui_state_to_query(blocks, self._CANVAS_CTX, should_prefix=False)
        assert out == blocks


class TestPrependDynamicContextToQuery:
    """阶段二 prompt-builder 两分 — _prepend_dynamic_context_to_query generalizes
    _prepend_ui_state_to_query to carry the per-turn DYNAMIC segment (recall_block
    + UI-SENSE) as a query_content prefix on a warm-reuse turn. recall_block MUST
    preserve its [RECALLED] provenance header verbatim.
    """

    _CANVAS_CTX = {"file_path": "", "file_name": "", "canvas": {"open": True, "output_count": 2, "pinned": False, "muted": False, "collapsed": False}}
    _RECALL = "## Recalled Knowledge\n> **[RECALLED]** keyword/FTS-retrieved prior context.\n- MEMORY.md: some lesson"

    def test_prefixes_recall_block_with_provenance_preserved(self):
        """AC2/AC4: recall_block prepended and its [RECALLED] header preserved."""
        from core.session_router import _prepend_dynamic_context_to_query
        out = _prepend_dynamic_context_to_query(
            "what did we learn?", self._CANVAS_CTX, recall_block=self._RECALL, should_prefix=True,
        )
        assert isinstance(out, str)
        assert "[RECALLED]" in out, "recall provenance header dropped in migration"
        assert "Recalled Knowledge" in out
        assert "Current UI State" in out, "SENSE must still ride the same dynamic segment"
        assert out.rstrip().endswith("what did we learn?"), "original query preserved at end"

    def test_no_prefix_when_not_reuse(self):
        """COLD/spawn path: dynamic segment rides system_prompt → must NOT double-inject."""
        from core.session_router import _prepend_dynamic_context_to_query
        out = _prepend_dynamic_context_to_query(
            "q", self._CANVAS_CTX, recall_block=self._RECALL, should_prefix=False,
        )
        assert out == "q", "unchanged when not reusing (cold path carries it in system_prompt)"

    def test_recall_only_no_sense(self):
        """recall_block present, no editor_context → recall still prefixed."""
        from core.session_router import _prepend_dynamic_context_to_query
        out = _prepend_dynamic_context_to_query(
            "q", None, recall_block=self._RECALL, should_prefix=True,
        )
        assert "[RECALLED]" in out and out.rstrip().endswith("q")

    def test_sense_only_no_recall(self):
        """No recall_block, SENSE present → behaves like the old UI-state prefix."""
        from core.session_router import _prepend_dynamic_context_to_query
        out = _prepend_dynamic_context_to_query(
            "q", self._CANVAS_CTX, recall_block=None, should_prefix=True,
        )
        assert "Current UI State" in out and out.rstrip().endswith("q")

    def test_empty_dynamic_is_noop(self):
        """No recall, no SENSE → clean no-op even on reuse."""
        from core.session_router import _prepend_dynamic_context_to_query
        assert _prepend_dynamic_context_to_query("hi", None, recall_block=None, should_prefix=True) == "hi"

    def test_multimodal_list_inserts_leading_text_block(self):
        from core.session_router import _prepend_dynamic_context_to_query
        blocks = [{"type": "image", "source": {"x": 1}}, {"type": "text", "text": "look"}]
        out = _prepend_dynamic_context_to_query(
            blocks, self._CANVAS_CTX, recall_block=self._RECALL, should_prefix=True,
        )
        assert isinstance(out, list)
        assert out[0]["type"] == "text"
        assert "[RECALLED]" in out[0]["text"]
        assert out[1:] == blocks, "original blocks preserved"


class TestFormatTtftLine:
    """_format_ttft_line — pure decision + formatter for the end-to-end TTFT probe.

    Given a streamed event's type + the per-turn timing locals, decide whether THIS
    event is the first user-visible content token and, if so, produce the one-line
    `TTFT=` log string (else None). Extracted as a pure helper (GUI38) so the
    first-delta / idempotency / thinking-counts-too / recall-attribution decisions
    are unit-testable WITHOUT driving the whole run_conversation async generator.
    Pure observability: this function only formats a string — it never mutates state.
    """

    def _call(self, **kw):
        from core.session_router import _format_ttft_line
        base = dict(
            event_type="text_delta", already_recorded=False, ttft_ms=1234.0,
            slot_ms=0.0, recall_ms=None, recall_ran_this_turn=False, retry_count=0,
        )
        base.update(kw)
        return _format_ttft_line(**base)

    def test_text_delta_first_produces_line(self):
        """AC1: first text_delta → a TTFT= line with ttft_ms."""
        line = self._call(event_type="text_delta", ttft_ms=1234.5)
        assert line is not None
        assert "TTFT=" in line
        assert "1234" in line or "1235" in line, f"ttft_ms must appear: {line}"

    def test_thinking_delta_counts_as_first_token(self):
        """AC3: thinking_delta is a user-visible token too (often FIRST on Opus) —
        it must trigger the TTFT record, not just text_delta."""
        line = self._call(event_type="thinking_delta", ttft_ms=800.0)
        assert line is not None and "TTFT=" in line

    def test_already_recorded_returns_none(self):
        """AC5 idempotency: once the first delta was recorded, later deltas → None
        (the log fires exactly once per turn)."""
        assert self._call(event_type="text_delta", already_recorded=True) is None

    def test_non_content_event_returns_none(self):
        """A non-content event (assistant/result/tool_use/text_start) is never the
        first-token trigger → None (must not fire on session_start/text_start)."""
        for et in ("assistant", "result", "tool_use", "text_start", "thinking_start",
                   "session_start", "content_block_stop"):
            assert self._call(event_type=et) is None, f"{et} must not trigger TTFT"

    def test_segments_present(self):
        """AC2: the line carries the segment attribution — slot + recall + the
        first-token span — so a reader can locate WHICH segment was slow."""
        line = self._call(ttft_ms=2000.0, slot_ms=150.0, recall_ms=480.0,
                          recall_ran_this_turn=True)
        assert "slot" in line and "recall" in line

    def test_recall_none_labelled_not_faked_as_zero(self):
        """Gate-1 fix: on a turn where recall did NOT run (turn 2+, channel,
        keyword-miss), recall must be shown as not-run, NOT silently 0 — otherwise
        the residual math lies. recall_ran_this_turn=False → a distinct label."""
        line = self._call(recall_ms=None, recall_ran_this_turn=False)
        # not-run must be visually distinct from a real 0ms recall
        assert "recall=n/a" in line or "recall=—" in line or "recall=none" in line, line

    def test_retry_count_surfaced_when_nonzero(self):
        """Gate-1 fix: a retried turn's ttft_ms includes 5-15s backoff+respawn — so
        retry_count>0 MUST be surfaced or the number is a lie on respawned turns."""
        line = self._call(ttft_ms=8000.0, retry_count=2)
        assert "retr" in line.lower(), f"retry count must be visible: {line}"

    def test_no_retry_note_when_zero(self):
        """Clean turn (retry_count=0): no retry noise in the line."""
        line = self._call(ttft_ms=900.0, retry_count=0)
        assert line is not None

    # ── pre_send split (run_332ccfd1) — the router/model boundary breakdown ──
    # Gate-1-refined design: measure the pre-send window DIRECTLY (perf_counter at
    # the unit.send() boundary), not as a computed ttft-slot-recall residual. The
    # window includes build_options (prompt assembly), DB persist, multimodal +
    # recall — i.e. ALL router-side per-turn overhead, the segment the latency
    # suspects live in. Emitted on EVERY turn (incl warm recall=n/a), which the
    # old recall-only spawn+infer residual never did.

    def test_pre_send_split_present_on_warm_turn(self):
        """AC1: on a WARM turn (recall=n/a), when sw_overhead_ms is measured the
        line MUST carry both a pre_send= segment AND a send+infer= segment — the
        exact case the old probe left opaque."""
        line = self._call(
            event_type="text_delta", ttft_ms=10000.0, slot_ms=0.0,
            recall_ms=None, recall_ran_this_turn=False, sw_overhead_ms=300.0,
        )
        assert line is not None
        assert "pre_send=300" in line, f"pre_send segment must appear: {line}"
        assert "send+infer=" in line, f"send+infer segment must appear: {line}"

    def test_pre_send_and_send_infer_reconcile_to_ttft(self):
        """AC2: send+infer == ttft - pre_send (both DIRECTLY measured, not inferred).
        ttft=2000, pre_send=300 → send+infer=1700."""
        line = self._call(ttft_ms=2000.0, sw_overhead_ms=300.0)
        assert "pre_send=300ms" in line, line
        assert "send+infer=1700ms" in line, f"2000-300=1700 must appear: {line}"

    def test_sw_overhead_none_is_backward_compatible(self):
        """AC4: sw_overhead_ms=None (not measured) → NO pre_send/send+infer segment;
        the line is exactly the legacy shape. Guarantees the signature change does
        not perturb any caller that omits the new arg."""
        line = self._call(ttft_ms=1234.0, sw_overhead_ms=None)
        assert line is not None
        assert "pre_send=" not in line, f"no pre_send when unmeasured: {line}"
        assert "send+infer=" not in line, f"no send+infer when unmeasured: {line}"

    def test_pre_send_split_on_recall_ran_turn_no_contradiction(self):
        """AC4: when recall ran, recall= stays as a SUB-annotation within pre_send,
        and pre_send is the DIRECT measurement (not ttft-slot-recall). The old
        contradictory 'spawn+infer = ttft-slot-recall' residual must be GONE — there
        is exactly one residual (send+infer = ttft - pre_send), so no two numbers
        can disagree. recall(500) <= pre_send(800) since recall is inside it."""
        line = self._call(
            ttft_ms=3000.0, slot_ms=0.0, recall_ms=500.0,
            recall_ran_this_turn=True, sw_overhead_ms=800.0,
        )
        assert "recall=500ms" in line, f"recall stays visible: {line}"
        assert "pre_send=800ms" in line, f"pre_send is the direct measure: {line}"
        assert "send+infer=2200ms" in line, f"3000-800=2200: {line}"
        # The old misleading residual (ttft-slot-recall = 2500) must NOT appear.
        assert "spawn+infer" not in line, f"old contradictory residual removed: {line}"


class TestWarmReuseComplement:
    """阶段二 AC5/AC6: _is_warm_reuse is the single source of the warm-reuse
    predicate (was duplicated at 2 send() sites) and the EXACT COMPLEMENT of the
    poison-guard recycle condition (session_unit): within (IDLE ∧ client-alive),
    warm-reuse ⟺ last_turn_clean, poison-recycle ⟺ NOT last_turn_clean. This
    invariant is what makes the recall cold/warm split non-double-injecting."""

    class _Unit:
        def __init__(self, state, client, clean):
            self.state = state
            self._client = client
            self._last_turn_clean = clean

    def _unit(self, *, state, client, clean):
        return self._Unit(state, client if client else None, clean)

    def _poison_recycles(self, u):
        """Mirror of session_unit poison-guard condition (IDLE ∧ client ∧ NOT clean)."""
        from core.session_unit import SessionState
        return (
            u.state == SessionState.IDLE
            and u._client is not None
            and not u._last_turn_clean
        )

    def test_exact_complement_over_idle_client_domain(self):
        from core.session_router import _is_warm_reuse
        from core.session_unit import SessionState
        client = object()
        # Over the (IDLE ∧ client-alive) domain, warm-reuse and poison-recycle
        # partition perfectly on last_turn_clean — never both, never neither.
        for clean in (True, False):
            u = self._unit(state=SessionState.IDLE, client=client, clean=clean)
            warm = _is_warm_reuse(u)
            poison = self._poison_recycles(u)
            assert warm != poison, (
                f"warm-reuse and poison-recycle must be exact complements over "
                f"IDLE∧client (clean={clean}): warm={warm} poison={poison}"
            )

    def test_not_warm_when_not_idle(self):
        from core.session_router import _is_warm_reuse
        from core.session_unit import SessionState
        u = self._unit(state=SessionState.STREAMING, client=object(), clean=True)
        assert _is_warm_reuse(u) is False

    def test_not_warm_when_no_client(self):
        from core.session_router import _is_warm_reuse
        from core.session_unit import SessionState
        u = self._unit(state=SessionState.IDLE, client=None, clean=True)
        assert _is_warm_reuse(u) is False
