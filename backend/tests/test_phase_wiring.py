"""Tests for phase 1+2 wiring — import checks."""
from __future__ import annotations

import pytest


class TestUserObserverHookImportable:
    def test_user_observer_hook_importable(self):
        from hooks.user_observer_hook import UserObserverHook
        assert UserObserverHook is not None


class TestSessionRecallImportable:
    def test_session_recall_importable(self):
        from core.session_recall import SessionRecall
        assert SessionRecall is not None


class TestSkillGuardImportable:
    def test_skill_guard_importable(self):
        from core.skill_guard import SkillGuard, TrustLevel
        assert SkillGuard is not None
        assert TrustLevel is not None


class TestSkillMetricsImportable:
    def test_skill_metrics_importable(self):
        from core.skill_metrics import SkillMetricsStore
        assert SkillMetricsStore is not None


class TestMemoryGuardImportable:
    def test_memory_guard_importable(self):
        from core.memory_guard import MemoryGuard, ScanResult
        assert MemoryGuard is not None
        assert ScanResult is not None
