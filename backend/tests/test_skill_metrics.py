"""Tests for SkillMetricsStore — skill invocation tracking and analytics.

Validates recording, aggregation, evolution candidate detection,
thread safety, and database auto-creation.
"""
from __future__ import annotations

import threading
import pytest
from pathlib import Path


class TestSkillMetricsStore:
    """Test suite for SkillMetricsStore."""

    def _make_store(self, tmp_path: Path):
        from core.skill_metrics import SkillMetricsStore
        return SkillMetricsStore(db_path=tmp_path / "metrics.db")

    def test_record_and_get_stats(self, tmp_path):
        """Record 5 invocations, verify stats."""
        store = self._make_store(tmp_path)
        for i in range(5):
            store.record(
                skill_name="test-skill",
                session_id=f"sess-{i}",
                outcome="success",
                duration_seconds=1.0 + i,
                user_satisfaction="accepted",
            )
        stats = store.get_stats("test-skill")
        assert stats is not None
        assert stats.skill_name == "test-skill"
        assert stats.invocation_count == 5
        assert stats.success_rate == 1.0
        assert stats.avg_duration == pytest.approx(3.0, abs=0.01)  # (1+2+3+4+5)/5

    def test_success_rate_calculation(self, tmp_path):
        """3 success + 2 failure = 0.6 success rate."""
        store = self._make_store(tmp_path)
        for _ in range(3):
            store.record("calc-skill", "s1", "success", 1.0)
        for _ in range(2):
            store.record("calc-skill", "s1", "failure", 1.0)
        stats = store.get_stats("calc-skill")
        assert stats is not None
        assert stats.success_rate == pytest.approx(0.6, abs=0.01)

    def test_correction_rate(self, tmp_path):
        """user_satisfaction tracking: 2 corrections out of 5."""
        store = self._make_store(tmp_path)
        for i in range(3):
            store.record("corr-skill", f"s{i}", "success", 1.0, "accepted")
        for i in range(2):
            store.record("corr-skill", f"s{i+3}", "success", 1.0, "correction")
        stats = store.get_stats("corr-skill")
        assert stats is not None
        assert stats.correction_rate == pytest.approx(0.4, abs=0.01)

    def test_evolution_candidates(self, tmp_path):
        """High correction rate returns candidate."""
        store = self._make_store(tmp_path)
        # 5 invocations, 3 corrections (60% correction rate > 0.3 threshold)
        for i in range(2):
            store.record("evo-skill", f"s{i}", "success", 1.0, "accepted")
        for i in range(3):
            store.record("evo-skill", f"s{i+2}", "success", 1.0, "correction")
        candidates = store.get_evolution_candidates()
        assert "evo-skill" in candidates

    def test_evolution_candidates_min_count(self, tmp_path):
        """Skills with <5 invocations are not returned as candidates."""
        store = self._make_store(tmp_path)
        # Only 3 invocations, all corrections — shouldn't qualify
        for i in range(3):
            store.record("low-count", f"s{i}", "failure", 1.0, "correction")
        candidates = store.get_evolution_candidates()
        assert "low-count" not in candidates

    def test_get_all_stats(self, tmp_path):
        """Multiple skills tracked and returned."""
        store = self._make_store(tmp_path)
        for name in ("skill-a", "skill-b", "skill-c"):
            store.record(name, "s1", "success", 1.0)
        all_stats = store.get_all_stats()
        names = {s.skill_name for s in all_stats}
        assert names == {"skill-a", "skill-b", "skill-c"}

    def test_empty_stats(self, tmp_path):
        """Nonexistent skill returns None."""
        store = self._make_store(tmp_path)
        assert store.get_stats("nonexistent") is None

    def test_concurrent_writes(self, tmp_path):
        """Thread safety: concurrent writes don't corrupt."""
        store = self._make_store(tmp_path)
        errors = []

        def _write(thread_id):
            try:
                for i in range(10):
                    store.record(
                        f"thread-skill-{thread_id}",
                        f"sess-{thread_id}-{i}",
                        "success",
                        0.5,
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_write, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent write errors: {errors}"
        all_stats = store.get_all_stats()
        total = sum(s.invocation_count for s in all_stats)
        assert total == 40  # 4 threads * 10 writes each

    def test_db_created_on_init(self, tmp_path):
        """Table auto-created when store is initialized."""
        db_path = tmp_path / "auto.db"
        assert not db_path.exists()
        from core.skill_metrics import SkillMetricsStore
        store = SkillMetricsStore(db_path=db_path)
        assert db_path.exists()
        # Should be able to record immediately
        store.record("init-skill", "s1", "success", 1.0)
        stats = store.get_stats("init-skill")
        assert stats is not None
        assert stats.invocation_count == 1

    def test_evolution_candidates_low_success_rate(self, tmp_path):
        """Skills with success_rate < 0.7 and >= 5 invocations are candidates."""
        store = self._make_store(tmp_path)
        # 2 success + 3 failure = 0.4 success rate
        for _ in range(2):
            store.record("bad-skill", "s1", "success", 1.0)
        for _ in range(3):
            store.record("bad-skill", "s2", "failure", 1.0)
        candidates = store.get_evolution_candidates()
        assert "bad-skill" in candidates
