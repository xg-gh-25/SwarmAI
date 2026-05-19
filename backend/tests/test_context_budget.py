"""Tests for Context Budget Optimizer — token measurement and compression.

Tests the two new capabilities:
1. Token budget measurement in context_health_hook (_check_token_budget)
2. EVOLUTION auto-compression in memory_health (Phase 1 Rule 1)
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


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
        # ~200 ASCII chars ≈ 50 tokens. 9 files × 50 = 450 tokens (well under 75K)
        ctx = self._make_context_dir(tmp_path, {
            "SOUL.md": "x" * 200,
            "AGENT.md": "x" * 200,
            "STEERING.md": "x" * 200,
            "MEMORY.md": "x" * 200,
            "EVOLUTION.md": "x" * 200,
            "KNOWLEDGE.md": "x" * 200,
            "PROJECTS.md": "x" * 200,
            "USER.md": "x" * 200,
            "TOOLS.md": "x" * 200,
        })
        findings = hook._check_token_budget(ctx)
        assert findings == []
        assert hook._token_measurement["total_tokens"] < 75_000
        assert hook._token_measurement["over_budget"] is False

    def test_warning_threshold_emits_finding(self, tmp_path):
        """Over 75K tokens but under 85K → WARNING finding."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        # 280K ASCII chars ÷ 3.5 = 80K tokens → over WARNING (75K) but under EMERGENCY (85K)
        ctx = self._make_context_dir(tmp_path, {
            "MEMORY.md": "x" * 280_000,
        })
        findings = hook._check_token_budget(ctx)
        assert len(findings) == 1
        assert "WARNING" in findings[0]
        assert hook._token_measurement["over_budget"] is True

    def test_emergency_threshold_emits_finding(self, tmp_path):
        """Over 85K tokens → EMERGENCY finding."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        # 350K ASCII chars ÷ 3.5 ≈ 100K tokens → over EMERGENCY (85K)
        ctx = self._make_context_dir(tmp_path, {
            "MEMORY.md": "x" * 350_000,
        })
        findings = hook._check_token_budget(ctx)
        assert len(findings) == 1
        assert "EMERGENCY" in findings[0]

    def test_cjk_aware_token_counting(self, tmp_path):
        """CJK characters count as ~1.5 tokens each (widened range)."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        # 1000 CJK chars (U+4E00 block) × 1.5 = 1500 tokens
        # 1000 ASCII chars × (1/3.5) ≈ 286 tokens
        ctx = self._make_context_dir(tmp_path, {
            "MEMORY.md": "中" * 1000 + "x" * 1000,
        })
        findings = hook._check_token_budget(ctx)
        measurement = hook._token_measurement
        # CJK: 1000 × 1.5 = 1500, ASCII: 1000 / 3.5 ≈ 286 → total ~1786
        assert 1700 < measurement["per_file"]["MEMORY.md"] < 1900

    def test_cjk_fullwidth_counted(self, tmp_path):
        """Fullwidth punctuation and CJK Symbols also count as CJK-like."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        # 100 fullwidth chars (U+FF01-U+FF64, within 0xFF00-0xFFEF)
        # 50 CJK symbols (U+3001-U+3032, within 0x3000-0x303F)
        fullwidth = "".join(chr(c) for c in range(0xFF01, 0xFF01 + 100))
        cjk_symbols = "".join(chr(c) for c in range(0x3001, 0x3001 + 50))
        ctx = self._make_context_dir(tmp_path, {
            "MEMORY.md": fullwidth + cjk_symbols,
        })
        findings = hook._check_token_budget(ctx)
        measurement = hook._token_measurement
        # 150 CJK-like chars × 1.5 = 225 tokens
        assert 220 < measurement["per_file"]["MEMORY.md"] < 230

    def test_ignores_non_context_files(self, tmp_path):
        """Files not in the 9 context file list are ignored."""
        from hooks.context_health_hook import ContextHealthHook

        hook = ContextHealthHook()
        ctx = self._make_context_dir(tmp_path, {
            "SOUL.md": "x" * 100,
            "L1_SYSTEM_PROMPTS.md": "x" * 500_000,  # cache file, ignored
            ".memory-usage.json": "x" * 100_000,  # metadata, ignored
        })
        findings = hook._check_token_budget(ctx)
        # Only SOUL.md counted
        assert hook._token_measurement["total_tokens"] < 100


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
