"""Tests for DddCultivationOrchestrator — God Object extraction from context_health_hook.

Verifies:
- Orchestrator exists and has run() method (AC1)
- _deep_check delegates to orchestrator (AC2)
- Channel isolation — one failure doesn't affect others (AC3)
"""
import json
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

    def test_orchestrator_has_7_channels(self):
        from core.ddd_orchestrator import DddCultivationOrchestrator

        orch = DddCultivationOrchestrator()
        assert hasattr(orch, "channels")
        assert len(orch.channels) == 7


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
            with patch("core.ddd_orchestrator.DddCultivationOrchestrator") as MockOrch:
                mock_instance = MagicMock()
                mock_instance.run.return_value = ["test_finding"]
                MockOrch.return_value = mock_instance

                hook._deep_check(tmp_path, str(tmp_path))

                # Verify orchestrator was called via legacy path
                MockOrch.assert_called_once()
                mock_instance.run.assert_called_once_with(tmp_path, str(tmp_path))
        finally:
            dispatcher.loop = original_loop
