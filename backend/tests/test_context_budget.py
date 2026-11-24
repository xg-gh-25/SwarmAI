"""Tests for Context Budget Optimizer — token measurement and compression.

Tests the two new capabilities:
1. Token budget measurement in context_health_hook (_check_token_budget)
2. EVOLUTION auto-compression in memory_health (Phase 1 Rule 1)

run_3f25a73a: _check_token_budget now delegates to the CANONICAL
ContextDirectoryLoader.estimate_tokens (calibrated CJK 1.1 tok/char,
Latin 2.2 tok/word). It measures each file's prompt form
(ContextDirectoryLoader.render_section, through core.context_brain's token
block), not the file on disk. The old local `cjk*1.5 + ascii/3.5` formula and the
75K/85K thresholds were calibrated against an empirically-wrong coefficient;
these tests were updated to the calibrated estimator + observability
thresholds (WARNING 91K = assembly budget, EMERGENCY 130K).
"""

from pathlib import Path



# ── Token Measurement Tests ──────────────────────────────────────────


class TestTokenBudgetMeasurement:
    """Test _check_token_budget() in context_health_hook."""

    def _make_context_dir(self, tmp_path: Path, files: dict[str, str]) -> Path:
        """Create a mock .context/ directory with given files."""
        ctx = tmp_path / ".context"
        ctx.mkdir()
        for name, content in files.items():
            (ctx / name).write_text(content, encoding="utf-8")
        return ctx

    def test_healthy_budget_no_findings(self, tmp_path):
        """Under WARNING threshold → no findings emitted."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        # ~200 ASCII chars (no spaces) ≈ 1 word ≈ 2 tokens. Tiny — well under 91K.
        ctx = self._make_context_dir(tmp_path, {
            "SOUL.md": "x " * 50,
            "AGENT.md": "x " * 50,
            "STEERING.md": "x " * 50,
            "MEMORY.md": "x " * 50,
            "EVOLUTION.md": "x " * 50,
            "PROJECTS.md": "x " * 50,
            "USER.md": "x " * 50,
            "TOOLS.md": "x " * 50,
        })
        findings = hook._check_token_budget(ctx)
        assert findings == []
        # Threshold-relative (raised 2026-09-30: WARNING 91K→141K).
        assert hook._token_measurement["total_tokens"] < hook._WARNING_THRESHOLD
        assert hook._token_measurement["over_budget"] is False

    def test_warning_threshold_emits_finding(self, tmp_path):
        """Over WARNING (141K) but under EMERGENCY (180K) → WARNING finding.

        Thresholds raised 2026-09-30 with the BUDGET_1M_MODEL 100K→150K bump
        (WARNING 91K→141K, EMERGENCY 130K→180K).
        """
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        # Latin 2.2 tok/word: ~72K space-separated words ≈ 158K tokens
        # → over WARNING (141K) but under EMERGENCY (180K)
        ctx = self._make_context_dir(tmp_path, {
            "MEMORY.md": "word " * 72_000,
        })
        findings = hook._check_token_budget(ctx)
        total = hook._token_measurement["total_tokens"]
        assert hook._WARNING_THRESHOLD < total < hook._EMERGENCY_THRESHOLD, total
        assert len(findings) == 1
        assert "WARNING" in findings[0]
        assert hook._token_measurement["over_budget"] is True

    def test_emergency_threshold_emits_finding(self, tmp_path):
        """Over EMERGENCY (180K) → EMERGENCY finding."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        # Latin 2.2 tok/word: ~90K words ≈ 198K tokens → over EMERGENCY (180K)
        ctx = self._make_context_dir(tmp_path, {
            "MEMORY.md": "word " * 90_000,
        })
        findings = hook._check_token_budget(ctx)
        assert hook._token_measurement["total_tokens"] > hook._EMERGENCY_THRESHOLD
        assert len(findings) == 1
        assert "EMERGENCY" in findings[0]

    def test_matches_canonical_estimator(self, tmp_path):
        """_check_token_budget must equal ContextDirectoryLoader.estimate_tokens of
        the file's prompt form (Gate-1: no second formula — health delegates to the
        canonical, on render_section's text)."""
        from hooks.context_health_hook import ContextHealthHook
        from core.context_directory_loader import ContextDirectoryLoader

        hook = ContextHealthHook()
        content = "SwarmAI 是自进化 Agent OS. The READ path is the differentiator " * 50
        ctx = self._make_context_dir(tmp_path, {"MEMORY.md": content})
        hook._check_token_budget(ctx)
        assert (
            hook._token_measurement["per_file"]["MEMORY.md"]
            == _prompt_tokens(ctx, "MEMORY.md")
        )
        assert (
            hook._token_measurement["disk_per_file"]["MEMORY.md"]
            == ContextDirectoryLoader.estimate_tokens(content)
        )

    def test_cjk_aware_token_counting(self, tmp_path):
        """CJK counted via canonical (1.1 tok/char): 1000 中 ≈ 1100 tokens."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        content = "中" * 1000 + " " + "word " * 100
        ctx = self._make_context_dir(tmp_path, {"MEMORY.md": content})
        hook._check_token_budget(ctx)
        # equals canonical by construction; sanity: CJK dominates ~1100
        assert hook._token_measurement["per_file"]["MEMORY.md"] == \
            _prompt_tokens(ctx, "MEMORY.md")
        assert hook._token_measurement["per_file"]["MEMORY.md"] > 1000

    def test_no_local_cjk_formula_remains(self):
        """The divergent local cjk*1.5 formula must be GONE, and the check must
        measure through the overlay's block (render_section), not a copy of it."""
        import inspect
        from hooks.context_health_hook import ContextHealthHook
        src = inspect.getsource(ContextHealthHook._check_token_budget)
        assert "cjk_chars * 1.5" not in src
        assert "_is_cjk_like" not in src
        assert "build_context_token_block" in src
        assert "read_text" not in src

    def test_ignores_non_context_files(self, tmp_path):
        """Files that are not assembled context files are ignored."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        ctx = self._make_context_dir(tmp_path, {
            "SOUL.md": "x " * 30,
            "L1_SYSTEM_PROMPTS.md": "x " * 500_000,  # cache file, ignored
            ".memory-usage.json": "x " * 100_000,  # metadata, ignored
        })
        findings = hook._check_token_budget(ctx)
        # Only SOUL.md counted (~30 words ≈ 66 tokens)
        assert hook._token_measurement["total_tokens"] < 100


def _prompt_tokens(ctx: Path, filename: str) -> int:
    """What *filename* puts in the assembled prompt, in tokens (its section)."""
    from core.context_directory_loader import CONTEXT_FILES, ContextDirectoryLoader

    spec = next(s for s in CONTEXT_FILES if s.filename == filename)
    body = ContextDirectoryLoader(ctx).render_section(spec)
    return ContextDirectoryLoader.estimate_tokens(
        ContextDirectoryLoader.section_text(spec.section_name, body)) if body else 0


def _write_big_timeline(ctx: Path, events: int) -> None:
    """A TIMELINE.md in the CDF writer's block format: ONE month summary for the
    previous month (what select_timeline injects) and *events* events (which it
    does not)."""
    from datetime import date, timedelta

    month = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    summary = (f"### {month}-28 month summary\n- what: the month in short\n"
               "- outcome: summarised\n<!-- cand:summary-1 | event_key:ks | source:S0 | "
               "event_kind:month_summary -->")
    blocks = [
        f"### {month}-{(i % 27) + 1:02d} event number {i}\n"
        f"- what: {'a long event body with many words ' * 12}\n- outcome: done\n"
        f"<!-- cand:event-{i} | event_key:k{i} | source:S2 | event_kind:event -->"
        for i in range(events)
    ]
    (ctx / "TIMELINE.md").write_text(
        f"# Timeline\n\n## {month}\n{summary}\n\n" + "\n\n".join(blocks) + "\n",
        encoding="utf-8",
    )


class TestBudgetMeasuresThePrompt:
    """The deep check counts what each file puts in the prompt (render_section),
    the same figure the C&M overlay shows — not each file's size on disk."""

    def _ctx(self, tmp_path: Path) -> Path:
        ctx = tmp_path / ".context"
        ctx.mkdir()
        (ctx / "SWARMAI.md").write_text("core identity " * 200, encoding="utf-8")
        (ctx / "SOUL.md").write_text("principle words " * 200, encoding="utf-8")
        (ctx / "MEMORY.md").write_text("## Principles\n" + "memory note " * 300,
                                       encoding="utf-8")
        return ctx

    def test_big_timeline_total_is_the_assembled_prompt_and_no_warning(self, tmp_path):
        """A TIMELINE of many events and one month summary, over the warning line
        on disk: the total is about the assembled prompt and nothing warns."""
        from hooks.context_health_hook import ContextHealthHook
        from core.context_directory_loader import ContextDirectoryLoader

        ctx = self._ctx(tmp_path)
        _write_big_timeline(ctx, events=2500)
        hook = ContextHealthHook()
        findings = hook._check_token_budget(ctx)
        m = hook._token_measurement

        est = ContextDirectoryLoader.estimate_tokens
        assert est((ctx / "TIMELINE.md").read_text(encoding="utf-8")) > hook._WARNING_THRESHOLD
        assembled = ContextDirectoryLoader(ctx)._assemble_from_sources(token_budget=10**9)
        # equal up to the "\n\n" separators between sections
        assert abs(m["total_tokens"] - est(assembled)) <= 2 * len(m["per_file"]), (
            m["total_tokens"], est(assembled))
        assert m["per_file"]["TIMELINE.md"] < 200
        assert m["disk_per_file"]["TIMELINE.md"] > hook._WARNING_THRESHOLD
        assert findings == []
        assert m["over_budget"] is False

    def test_disk_only_size_in_a_file_raises_no_warning(self, tmp_path):
        """A file whose bulk is a metadata comment (stripped from the prompt) is over
        the line on disk only: no WARNING/EMERGENCY."""
        from hooks.context_health_hook import ContextHealthHook

        ctx = self._ctx(tmp_path)
        (ctx / "TOOLS.md").write_text(
            "## Notes\n- one index line\n<!-- " + "generated metadata " * 90_000 + "-->\n",
            encoding="utf-8")
        hook = ContextHealthHook()
        findings = hook._check_token_budget(ctx)
        assert hook._token_measurement["disk_per_file"]["TOOLS.md"] > hook._EMERGENCY_THRESHOLD
        assert findings == []
        assert hook._token_measurement["over_budget"] is False

    def test_large_memory_still_warns(self, tmp_path):
        """MEMORY.md is injected whole: a large one is over the line in the prompt
        and still raises the WARNING, naming MEMORY.md."""
        from hooks.context_health_hook import ContextHealthHook

        ctx = self._ctx(tmp_path)
        _write_big_timeline(ctx, events=50)
        (ctx / "MEMORY.md").write_text("## Principles\n" + "memoryword " * 68_000,
                                       encoding="utf-8")
        hook = ContextHealthHook()
        findings = hook._check_token_budget(ctx)
        m = hook._token_measurement
        assert hook._WARNING_THRESHOLD < m["total_tokens"] < hook._EMERGENCY_THRESHOLD
        assert len(findings) == 1 and "WARNING" in findings[0]
        assert "MEMORY.md" in findings[0]
        assert m["over_budget"] is True


class TestSelfReportDrift:
    """run_3f25a73a: catch context files that self-report a token size diverging
    from their real calibrated size (the drift that made the system claim ~44K
    when it was really ~152K). WARN-only — never mutates."""

    def _make_ctx(self, tmp_path, files):
        ctx = tmp_path / ".context"
        ctx.mkdir()
        for n, c in files.items():
            (ctx / n).write_text(c, encoding="utf-8")
        return ctx

    def test_stale_self_claim_triggers_warning(self, tmp_path):
        from hooks.context_health_hook import ContextHealthHook
        hook = ContextHealthHook()
        # A file that CLAIMS "~10K tokens" but is really ~50K+ (25K words × 2.2)
        body = "Context files: ~10K tokens total.\n\n" + ("word " * 25_000)
        ctx = self._make_ctx(tmp_path, {"TOOLS.md": body})
        findings = hook._check_self_report_drift(ctx)
        assert any("self-report-drift" in f and "TOOLS.md" in f for f in findings)

    def test_accurate_self_claim_no_warning(self, tmp_path):
        from hooks.context_health_hook import ContextHealthHook
        from core.context_directory_loader import ContextDirectoryLoader
        hook = ContextHealthHook()
        body_words = "word " * 25_000
        real = ContextDirectoryLoader.estimate_tokens(body_words)
        # Claim the REAL size (within 25%)
        body = f"Context files: ~{real // 1000}K tokens total.\n\n" + body_words
        ctx = self._make_ctx(tmp_path, {"TOOLS.md": body})
        findings = hook._check_self_report_drift(ctx)
        assert findings == []

    def test_small_inline_numbers_ignored(self, tmp_path):
        """'5 tokens' inline (not a file-size claim) must not false-fire."""
        from hooks.context_health_hook import ContextHealthHook
        hook = ContextHealthHook()
        body = "The function returns 5 tokens per call.\n\n" + ("word " * 25_000)
        ctx = self._make_ctx(tmp_path, {"MEMORY.md": body})
        findings = hook._check_self_report_drift(ctx)
        assert findings == []  # 5 < 5000 floor, skipped

    def test_budget_constants_are_not_self_claims(self, tmp_path):
        """A file documenting budget CONSTANTS (real lines from a live file);
        none is a claim about the file's own size, so none may fire drift."""
        from hooks.context_health_hook import ContextHealthHook
        hook = ContextHealthHook()
        body = (
            "**Effective budget = base_budget − EPHEMERAL_HEADROOM (9,000 tokens)**\n"
            "- For 1M model: effective = 150,000 − 9,000 = **141,000 tokens** for context files\n"
            "| **task_budget** | 128K tokens | 800K (desktop) |\n"
            "| Our context files | ✅ | Static load from `.context/`; measure live |\n"
            "\n" + ("word " * 25_000)
        )
        ctx = self._make_ctx(tmp_path, {"TOOLS.md": body})
        assert hook._check_self_report_drift(ctx) == []

    def test_explicit_self_claim_variants_still_detected(self, tmp_path):
        """The restricted regex must keep catching real self-size claims."""
        from hooks.context_health_hook import ContextHealthHook
        hook = ContextHealthHook()
        for i, claim in enumerate(("Context files: ~10K tokens total.",
                                   "This file is ≈ 10,000 tokens.",
                                   "Total context: 10K tok")):
            d = tmp_path / f"c{i}"
            d.mkdir()
            ctx = self._make_ctx(d, {"TOOLS.md": claim + "\n\n" + ("word " * 25_000)})
            findings = hook._check_self_report_drift(ctx)
            assert any("self-report-drift" in f for f in findings), claim

    def test_never_mutates_file(self, tmp_path):
        from hooks.context_health_hook import ContextHealthHook
        hook = ContextHealthHook()
        body = "Context: ~10K tokens.\n\n" + ("word " * 25_000)
        ctx = self._make_ctx(tmp_path, {"MEMORY.md": body})
        before = (ctx / "MEMORY.md").read_text()
        hook._check_self_report_drift(ctx)
        assert (ctx / "MEMORY.md").read_text() == before


# ── EVOLUTION Auto-Compression Tests ─────────────────────────────────


class TestEvolutionAutoCompress:
    """Test Phase 1 Rule 1: compress resolved/mitigated EVOLUTION corrections."""

    SAMPLE_EVOLUTION = """\
# SwarmAI Evolution Registry

## Corrections Captured

### C001 | 2025-12-01 [Bias B]
- **Correction**: Tab-switch streaming loss reported 4x without fix.
- **Pattern**: Streaming content loss on tab switch.
- **Status**: resolved — COE06/07, 3-layer guard deployed.

### C011 | 2026-04-25 [Bias A]
- **Correction**: Voice Conversation Mode — pipeline 10/10 confidence, feature 100% non-functional.
- **Pattern**: State machine declaration ≠ implementation.
- **Status**: active — root cause (confidence bias) recurred as C021, C025

### C013 | 2025-11-15 [Bias D]
- **Correction**: Ran full test suite proactively, caused deadlock.
- **Pattern**: Full suite has known xdist deadlock.
- **Status**: mitigated — PreToolUse hook blocks it.

## Capabilities Built

### E001 | 2026-03-15
- **Capability**: SSE Streaming Pipeline
- **Status**: active
"""


    def test_preserves_active_corrections(self, tmp_path):
        """Active corrections are never compressed regardless of age."""
        from jobs.handlers.memory_health import _compress_evolution_entries

        evo_path = tmp_path / "EVOLUTION.md"
        evo_path.write_text(self.SAMPLE_EVOLUTION, encoding="utf-8")

        result = _compress_evolution_entries(
            evo_path,
            active_bias_classes={"A"},
            recent_da_refs=set(),

        )

        content = evo_path.read_text(encoding="utf-8")
        # C011 (active) must stay full
        assert "- **Correction**: Voice Conversation Mode" in content
        assert "C011" not in result["compressed"]

    def test_preserves_protective_bias_class(self, tmp_path):
        """If bias class has any active correction, ALL of that class stay full."""
        from jobs.handlers.memory_health import _compress_evolution_entries

        evo_path = tmp_path / "EVOLUTION.md"
        evo_path.write_text(self.SAMPLE_EVOLUTION, encoding="utf-8")

        result = _compress_evolution_entries(
            evo_path,
            active_bias_classes={"A", "D"},  # D has active — protects C013
            recent_da_refs=set(),

        )

        content = evo_path.read_text(encoding="utf-8")
        # C013 has Bias D which is active → stays full
        assert "- **Correction**: Ran full test suite" in content
        assert "C013" not in result["compressed"]


    def test_dry_run_no_writes(self, tmp_path):
        """Dry run reports what would compress without writing."""
        from jobs.handlers.memory_health import _compress_evolution_entries

        evo_path = tmp_path / "EVOLUTION.md"
        evo_path.write_text(self.SAMPLE_EVOLUTION, encoding="utf-8")
        original = evo_path.read_text()

        result = _compress_evolution_entries(
            evo_path, active_bias_classes={"A"}, recent_da_refs=set()
        )

        # File unchanged
        assert evo_path.read_text() == original
        # But reports what it would do
        assert len(result["would_compress"]) > 0

