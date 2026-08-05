"""Tests for project_registry.get_pinned_projects — the backend-owned pinned order
for the DDD gallery / Welcome Top-3 (run_9ada46ae).

Contract: SwarmAI is ALWAYS first (protected primary brain); the rest come from a
mutable const, existence-guarded so a deleted/local-only project (e.g. CMHK on a
public checkout) silently drops rather than producing a broken pin.
"""
import pytest


class TestGetPinnedProjects:
    def test_swarmai_always_first(self, monkeypatch, tmp_path):
        from core import project_registry as pr
        # a projects dir where SwarmAI + AIDLC exist, CMHK does NOT
        projects = tmp_path / "Projects"
        (projects / "SwarmAI").mkdir(parents=True)
        (projects / "AIDLC").mkdir(parents=True)
        monkeypatch.setattr(pr, "get_projects_dir", lambda: projects)
        pinned = pr.get_pinned_projects()
        assert pinned[0] == "SwarmAI", "SwarmAI must always be first"

    def test_missing_pin_is_filtered_not_crashing(self, monkeypatch, tmp_path):
        """A pinned name whose dir doesn't exist (deleted / local-only on public
        checkout) is DROPPED, never emitted as a broken pin."""
        from core import project_registry as pr
        projects = tmp_path / "Projects"
        (projects / "SwarmAI").mkdir(parents=True)
        (projects / "AIDLC").mkdir(parents=True)
        # CMHK_SalesIntel intentionally absent
        monkeypatch.setattr(pr, "get_projects_dir", lambda: projects)
        pinned = pr.get_pinned_projects()
        assert "CMHK_SalesIntel" not in pinned, "non-existent pin must be filtered"
        assert "AIDLC" in pinned and "SwarmAI" in pinned

    def test_all_pins_present_when_all_exist(self, monkeypatch, tmp_path):
        from core import project_registry as pr
        projects = tmp_path / "Projects"
        for n in ("SwarmAI", "AIDLC", "CMHK_SalesIntel"):
            (projects / n).mkdir(parents=True)
        monkeypatch.setattr(pr, "get_projects_dir", lambda: projects)
        pinned = pr.get_pinned_projects()
        assert pinned == ["SwarmAI", "AIDLC", "CMHK_SalesIntel"], "order: SwarmAI first, then the configured rest"
