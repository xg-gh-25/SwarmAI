"""Tests for Context Budget Optimizer — token measurement and compression.

Tests the two new capabilities:
1. Token budget measurement in context_health_hook (_check_token_budget)
2. EVOLUTION auto-compression in memory_health (Phase 1 Rule 1)

run_3f25a73a: _check_token_budget now delegates to the CANONICAL
ContextDirectoryLoader.estimate_tokens (calibrated CJK 1.1 tok/char,
Latin 2.2 tok/word). The old local `cjk*1.5 + ascii/3.5` formula and the
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
            "KNOWLEDGE.md": "x " * 50,
            "PROJECTS.md": "x " * 50,
            "USER.md": "x " * 50,
            "TOOLS.md": "x " * 50,
        })
        findings = hook._check_token_budget(ctx)
        assert findings == []
        assert hook._token_measurement["total_tokens"] < 91_000
        assert hook._token_measurement["over_budget"] is False

    def test_warning_threshold_emits_finding(self, tmp_path):
        """Over 91K tokens but under 130K → WARNING finding (observability)."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        # Latin 2.2 tok/word: ~45K space-separated words ≈ 99K tokens
        # → over WARNING (91K) but under EMERGENCY (130K)
        ctx = self._make_context_dir(tmp_path, {
            "MEMORY.md": "word " * 45_000,
        })
        findings = hook._check_token_budget(ctx)
        assert len(findings) == 1
        assert "WARNING" in findings[0]
        assert hook._token_measurement["over_budget"] is True

    def test_emergency_threshold_emits_finding(self, tmp_path):
        """Over 130K tokens → EMERGENCY finding."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        # Latin 2.2 tok/word: ~70K words ≈ 154K tokens → over EMERGENCY (130K)
        ctx = self._make_context_dir(tmp_path, {
            "MEMORY.md": "word " * 70_000,
        })
        findings = hook._check_token_budget(ctx)
        assert len(findings) == 1
        assert "EMERGENCY" in findings[0]

    def test_matches_canonical_estimator(self, tmp_path):
        """_check_token_budget must equal ContextDirectoryLoader.estimate_tokens
        (Gate-1: no second formula — health delegates to the canonical)."""
        from hooks.context_health_hook import ContextHealthHook
        from core.context_directory_loader import ContextDirectoryLoader

        hook = ContextHealthHook()
        content = "SwarmAI 是自进化 Agent OS. The READ path is the differentiator " * 50
        ctx = self._make_context_dir(tmp_path, {"MEMORY.md": content})
        hook._check_token_budget(ctx)
        assert (
            hook._token_measurement["per_file"]["MEMORY.md"]
            == ContextDirectoryLoader.estimate_tokens(content)
        )

    def test_cjk_aware_token_counting(self, tmp_path):
        """CJK counted via canonical (1.1 tok/char): 1000 中 ≈ 1100 tokens."""
        from hooks.context_health_hook import ContextHealthHook
        from core.context_directory_loader import ContextDirectoryLoader

        hook = ContextHealthHook()
        content = "中" * 1000 + " " + "word " * 100
        ctx = self._make_context_dir(tmp_path, {"MEMORY.md": content})
        hook._check_token_budget(ctx)
        # equals canonical by construction; sanity: CJK dominates ~1100
        assert hook._token_measurement["per_file"]["MEMORY.md"] == \
            ContextDirectoryLoader.estimate_tokens(content)
        assert hook._token_measurement["per_file"]["MEMORY.md"] > 1000

    def test_no_local_cjk_formula_remains(self):
        """The divergent local cjk*1.5 formula must be GONE (grep-style guard)."""
        import inspect
        from hooks.context_health_hook import ContextHealthHook
        src = inspect.getsource(ContextHealthHook._check_token_budget)
        assert "cjk_chars * 1.5" not in src
        assert "_is_cjk_like" not in src
        assert "estimate_tokens" in src

    def test_ignores_non_context_files(self, tmp_path):
        """Files not in the 9 context file list are ignored."""
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
        ctx = self._make_ctx(tmp_path, {"KNOWLEDGE.md": body})
        findings = hook._check_self_report_drift(ctx)
        assert any("self-report-drift" in f and "KNOWLEDGE.md" in f for f in findings)

    def test_accurate_self_claim_no_warning(self, tmp_path):
        from hooks.context_health_hook import ContextHealthHook
        from core.context_directory_loader import ContextDirectoryLoader
        hook = ContextHealthHook()
        body_words = "word " * 25_000
        real = ContextDirectoryLoader.estimate_tokens(body_words)
        # Claim the REAL size (within 25%)
        body = f"Context files: ~{real // 1000}K tokens total.\n\n" + body_words
        ctx = self._make_ctx(tmp_path, {"KNOWLEDGE.md": body})
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

    def test_compresses_resolved_dormant_entry(self, tmp_path):
        """Resolved + >60 days dormant + non-protective → compress to 1-line."""
        from jobs.handlers.memory_health import _compress_evolution_entries

        evo_path = tmp_path / "EVOLUTION.md"
        evo_path.write_text(self.SAMPLE_EVOLUTION, encoding="utf-8")

        # C001: resolved, Bias B (no active Bias B corrections) → compress
        # C013: mitigated, Bias D (no active Bias D corrections) → compress
        result = _compress_evolution_entries(
            evo_path,
            active_bias_classes={"A"},  # Only Bias A has active corrections
            recent_da_refs=set(),  # No recent references
            dry_run=False,
        )

        content = evo_path.read_text(encoding="utf-8")
        # C001 should be compressed to 1-line
        assert "### C001 | 2025-12-01 — RESOLVED:" in content
        assert "- **Correction**: Tab-switch" not in content
        # C013 also compressed (mitigated, Bias D not active)
        assert "### C013 | 2025-11-15 — MITIGATED:" in content
        assert "- **Correction**: Ran full test suite" not in content
        # C011 stays full (active)
        assert "- **Correction**: Voice Conversation Mode" in content
        assert set(result["compressed"]) == {"C001", "C013"}

    def test_preserves_active_corrections(self, tmp_path):
        """Active corrections are never compressed regardless of age."""
        from jobs.handlers.memory_health import _compress_evolution_entries

        evo_path = tmp_path / "EVOLUTION.md"
        evo_path.write_text(self.SAMPLE_EVOLUTION, encoding="utf-8")

        result = _compress_evolution_entries(
            evo_path,
            active_bias_classes={"A"},
            recent_da_refs=set(),
            dry_run=False,
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
            dry_run=False,
        )

        content = evo_path.read_text(encoding="utf-8")
        # C013 has Bias D which is active → stays full
        assert "- **Correction**: Ran full test suite" in content
        assert "C013" not in result["compressed"]

    def test_respects_max_compressions_per_run(self, tmp_path):
        """Max 5 compressions per run."""
        from jobs.handlers.memory_health import _compress_evolution_entries

        # Create 8 old resolved corrections
        entries = []
        for i in range(8):
            entries.append(f"""### C{90+i:03d} | 2025-01-{i+1:02d} [Bias B]
- **Correction**: Old issue {i}.
- **Pattern**: Pattern {i}.
- **Status**: resolved — fixed long ago.
""")
        evo_content = "# Evolution\n\n## Corrections Captured\n\n" + "\n".join(entries)
        evo_path = tmp_path / "EVOLUTION.md"
        evo_path.write_text(evo_content, encoding="utf-8")

        result = _compress_evolution_entries(
            evo_path, active_bias_classes=set(), recent_da_refs=set(), dry_run=False,
        )
        # Max 5
        assert len(result["compressed"]) == 5

    def test_dry_run_no_writes(self, tmp_path):
        """Dry run reports what would compress without writing."""
        from jobs.handlers.memory_health import _compress_evolution_entries

        evo_path = tmp_path / "EVOLUTION.md"
        evo_path.write_text(self.SAMPLE_EVOLUTION, encoding="utf-8")
        original = evo_path.read_text()

        result = _compress_evolution_entries(
            evo_path, active_bias_classes={"A"}, recent_da_refs=set(), dry_run=True,
        )

        # File unchanged
        assert evo_path.read_text() == original
        # But reports what it would do
        assert len(result["would_compress"]) > 0

    def test_backup_created_before_write(self, tmp_path):
        """Writes a .pre-compress backup before modifying the file."""
        from jobs.handlers.memory_health import _compress_evolution_entries

        evo_path = tmp_path / "EVOLUTION.md"
        evo_path.write_text(self.SAMPLE_EVOLUTION, encoding="utf-8")

        _compress_evolution_entries(
            evo_path, active_bias_classes={"A"}, recent_da_refs=set(), dry_run=False,
        )

        # Backup file should exist
        backups = list(tmp_path.glob("*.pre-compress-*"))
        assert len(backups) == 1
