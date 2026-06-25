"""Tests for DddCultivationOrchestrator — God Object extraction from context_health_hook.

Verifies:
- Orchestrator exists and has run() method (AC1)
- _deep_check delegates to orchestrator (AC2)
- Channel isolation — one failure doesn't affect others (AC3)
"""
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestOrchestratorExists:
    """AC1: DddCultivationOrchestrator class exists with run() method."""

    def test_import_and_instantiate(self):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        orch = DddCultivationOrchestrator()
        assert hasattr(orch, "run")

    def test_run_returns_findings_list(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        # Minimal workspace structure
        (tmp_path / ".context").mkdir()
        (tmp_path / "Projects" / "SwarmAI").mkdir(parents=True)

        orch = DddCultivationOrchestrator()
        findings = orch.run(tmp_path, str(tmp_path))
        assert isinstance(findings, list)

    def test_orchestrator_has_expected_channels(self):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        orch = DddCultivationOrchestrator()
        assert hasattr(orch, "channels")
        assert len(orch.channels) == 11  # 8 original + mechanical_refresh + memory_refresh + llm_refresh


class TestChannelIsolation:
    """AC3: Each channel executes independently — one crash doesn't stop others."""

    def test_one_channel_raises_others_still_run(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        (tmp_path / ".context").mkdir()
        (tmp_path / "Projects" / "SwarmAI").mkdir(parents=True)
        (tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "proposals").mkdir(parents=True)

        orch = DddCultivationOrchestrator()

        # Mock first channel to raise
        original_channels = orch.channels.copy()
        call_log = []

        def make_channel(name, should_raise=False):
            def channel_fn(root, ws_path):
                if should_raise:
                    raise RuntimeError(f"Channel {name} exploded")
                call_log.append(name)
                return []
            return channel_fn

        from core.cultivation_dispatcher import EventType
        orch.channels = [
            ("ch_raises", make_channel("ch_raises", should_raise=True), {EventType.SESSION_CLOSE}),
            ("ch_ok_1", make_channel("ch_ok_1"), {EventType.SESSION_CLOSE}),
            ("ch_ok_2", make_channel("ch_ok_2"), {EventType.SESSION_CLOSE}),
        ]

        findings = orch.run(tmp_path, str(tmp_path))

        # Other channels still executed despite first one crashing
        assert "ch_ok_1" in call_log
        assert "ch_ok_2" in call_log

    def test_channel_error_logged_as_finding(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        (tmp_path / ".context").mkdir()
        (tmp_path / "Projects" / "SwarmAI").mkdir(parents=True)
        (tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "proposals").mkdir(parents=True)

        orch = DddCultivationOrchestrator()

        def exploding_channel(root, ws_path):
            raise ValueError("boom")

        from core.cultivation_dispatcher import EventType
        orch.channels = [("exploder", exploding_channel, {EventType.SESSION_CLOSE})]

        findings = orch.run(tmp_path, str(tmp_path))
        # Error should be captured, not swallowed silently
        assert any("exploder" in f.lower() or "boom" in f.lower() for f in findings)

    def test_channels_return_findings_merged(self, tmp_path):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        (tmp_path / ".context").mkdir()
        (tmp_path / "Projects" / "SwarmAI").mkdir(parents=True)
        (tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "proposals").mkdir(parents=True)

        orch = DddCultivationOrchestrator()

        def channel_a(root, ws_path):
            return ["finding_A"]

        def channel_b(root, ws_path):
            return ["finding_B"]

        from core.cultivation_dispatcher import EventType
        orch.channels = [("a", channel_a, {EventType.SESSION_CLOSE}), ("b", channel_b, {EventType.SESSION_CLOSE})]

        findings = orch.run(tmp_path, str(tmp_path))
        assert "finding_A" in findings
        assert "finding_B" in findings


class TestDeepCheckDelegation:
    """AC2: context_health_hook._deep_check delegates DDD section to orchestrator."""

    def test_deep_check_calls_orchestrator(self, tmp_path):
        from hooks.context_health_hook import ContextHealthHook

        # Create minimal workspace
        context_dir = tmp_path / ".context"
        context_dir.mkdir()
        (context_dir / "MEMORY.md").write_text("# Memory\n## Open Threads\n")
        (tmp_path / "Knowledge" / "DailyActivity").mkdir(parents=True)
        (tmp_path / "Projects" / "SwarmAI" / ".artifacts" / "proposals").mkdir(parents=True)

        hook = ContextHealthHook()

        # Phase E: _deep_check uses event-driven path when dispatcher.loop is set,
        # fallback to legacy orchestrator.run() only when loop is None.
        # Force legacy path by ensuring dispatcher singleton has loop=None.
        from core.cultivation_dispatcher import get_dispatcher
        dispatcher = get_dispatcher()
        original_loop = dispatcher.loop
        dispatcher.loop = None  # Force legacy fallback

        try:
            # autospec=True: bind to the real DddCultivationOrchestrator API so a
            # renamed/removed/signature-changed .run() fails this test rather than
            # silently passing (mock-masks-breakage precedent — _create_health_todo).
            with patch("core.ddd_orchestrator.DddCultivationOrchestrator", autospec=True) as MockOrch:
                mock_instance = MockOrch.return_value
                mock_instance.run.return_value = ["test_finding"]

                hook._deep_check(tmp_path, str(tmp_path))

                # Verify orchestrator was called via legacy path
                MockOrch.assert_called_once()
                mock_instance.run.assert_called_once_with(tmp_path, str(tmp_path))
        finally:
            dispatcher.loop = original_loop


class TestSourceWatchPaths:
    """DDD staleness detects changes via watched source paths, not just commit message grep."""

    def test_source_watch_paths_config_exists(self):
        from core.ddd_orchestrator import _SOURCE_WATCH_PATHS

        assert "AIDLC" in _SOURCE_WATCH_PATHS
        assert "SwarmAI" in _SOURCE_WATCH_PATHS
        assert any("autonomous-pipeline" in p for p in _SOURCE_WATCH_PATHS["AIDLC"])

    def test_staleness_detected_via_watch_path(self, tmp_path):
        """When commit grep finds nothing but watched path has commits, still flags stale."""
        import time
        from core.ddd_orchestrator import DddCultivationOrchestrator

        # Setup: project with old TECH.md (>14 days)
        project_dir = tmp_path / "Projects" / "AIDLC"
        project_dir.mkdir(parents=True)
        tech_file = project_dir / "TECH.md"
        tech_file.write_text("# AIDLC Tech")
        # Set mtime to 20 days ago
        old_time = time.time() - (20 * 86400)
        os.utime(tech_file, (old_time, old_time))

        orch = DddCultivationOrchestrator()

        # Track cwd passed to subprocess calls
        cwd_values = []

        def mock_run(cmd, **kwargs):
            cwd_values.append(kwargs.get("cwd"))
            if "--grep" in cmd:
                # Strategy 1: no commits mention "AIDLC"
                result = MagicMock()
                result.stdout = ""
                return result
            elif "backend/skills/s_autonomous-pipeline/" in cmd:
                # Strategy 2: watched path has commits
                result = MagicMock()
                result.stdout = "abc123 feat(pipeline): add new specialist\n"
                return result
            else:
                result = MagicMock()
                result.stdout = ""
                return result

        # Mock _find_swarmai_root to return a known path
        fake_swarmai = tmp_path / "swarmai"
        (fake_swarmai / "backend").mkdir(parents=True)

        with patch("core.ddd_orchestrator.subprocess.run", side_effect=mock_run), \
             patch("core.ddd_orchestrator._find_swarmai_root", return_value=fake_swarmai):
            findings = orch._ch_ddd_staleness(tmp_path, str(tmp_path))

        assert len(findings) == 1
        assert "AIDLC/TECH.md" in findings[0]
        assert "DDD-STALE" in findings[0]

        # Verify cwd: Strategy 1 uses ws_path, Strategy 2 uses swarmai_root
        assert cwd_values[0] == str(tmp_path)  # Strategy 1: SwarmWS
        assert cwd_values[1] == str(fake_swarmai)  # Strategy 2: swarmai repo

    def test_no_false_positive_when_fresh(self, tmp_path):
        """Fresh DDD docs (<14 days) are NOT flagged even with watched path commits."""
        from core.ddd_orchestrator import DddCultivationOrchestrator

        project_dir = tmp_path / "Projects" / "AIDLC"
        project_dir.mkdir(parents=True)
        tech_file = project_dir / "TECH.md"
        tech_file.write_text("# AIDLC Tech")
        # mtime is NOW (fresh) — no staleness

        orch = DddCultivationOrchestrator()
        with patch("core.ddd_orchestrator.subprocess.run") as mock_run:
            findings = orch._ch_ddd_staleness(tmp_path, str(tmp_path))

        assert findings == []
        mock_run.assert_not_called()  # Should short-circuit on mtime check
