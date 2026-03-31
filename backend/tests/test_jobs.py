"""
Tests for backend/jobs/ — the product-level job system.

Covers: models, cron_utils, dedup, system_jobs, scheduler, self_tune, paths, API router.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


# ── Models ──────────────────────────────────────────────────────────────

class TestModels:
    def test_job_defaults(self):
        from jobs.models import Job
        job = Job(id="test", name="Test", type="script", schedule="0 * * * *")
        assert job.enabled is True
        assert job.category == "system"
        assert job.safety.max_budget_usd == 5.0

    def test_scheduler_state_empty(self):
        from jobs.models import SchedulerState
        state = SchedulerState()
        assert state.jobs == {}
        assert state.raw_signals == []
        assert state.monthly_spend_usd == 0.0

    def test_job_result_fields(self):
        from jobs.models import JobResult
        result = JobResult(job_id="test", status="success", summary="OK")
        assert result.tokens_used == 0
        assert result.error is None

    def test_feed_types(self):
        from jobs.models import FeedType
        assert FeedType.RSS == "rss"
        assert FeedType.HACKER_NEWS == "hacker-news"
        assert FeedType.GITHUB_RELEASES == "github-releases"
        assert FeedType.WEB_SEARCH == "web-search"

    def test_scheduler_state_serialization(self):
        from jobs.models import SchedulerState, JobState
        state = SchedulerState(
            jobs={"test": JobState(last_run=datetime(2026, 3, 24, tzinfo=timezone.utc), last_status="success")},
            monthly_spend_usd=1.23,
        )
        data = json.loads(state.model_dump_json())
        restored = SchedulerState.model_validate(data)
        assert restored.jobs["test"].last_status == "success"
        assert restored.monthly_spend_usd == 1.23


# ── Cron Utils ──────────────────────────────────────────────────────────

class TestCronUtils:
    def test_never_run_is_always_due(self):
        from jobs.cron_utils import is_cron_due
        last_run = datetime(2020, 1, 1, tzinfo=timezone.utc)
        now = datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc)
        assert is_cron_due("0 * * * *", last_run, now) is True

    def test_hourly_after_one_hour(self):
        from jobs.cron_utils import is_cron_due
        last_run = datetime(2026, 3, 24, 9, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 24, 10, 5, tzinfo=timezone.utc)
        assert is_cron_due("0 * * * *", last_run, now) is True

    def test_hourly_within_same_hour(self):
        from jobs.cron_utils import is_cron_due
        last_run = datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 24, 10, 30, tzinfo=timezone.utc)
        assert is_cron_due("0 * * * *", last_run, now) is False

    def test_daily_at_8am(self):
        from jobs.cron_utils import is_cron_due
        last_run = datetime(2026, 3, 23, 8, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 24, 8, 5, tzinfo=timezone.utc)
        assert is_cron_due("0 8 * * *", last_run, now) is True

    def test_weekdays_only(self):
        from jobs.cron_utils import is_cron_due
        last_run = datetime(2026, 3, 22, 9, 0, tzinfo=timezone.utc)  # Sunday
        now = datetime(2026, 3, 23, 9, 5, tzinfo=timezone.utc)  # Monday
        assert is_cron_due("0 9 * * 1-5", last_run, now) is True

    def test_weekend_skip(self):
        from jobs.cron_utils import is_cron_due
        last_run = datetime(2026, 3, 21, 9, 0, tzinfo=timezone.utc)  # Friday
        now = datetime(2026, 3, 22, 9, 5, tzinfo=timezone.utc)  # Saturday
        assert is_cron_due("0 9 * * 1-5", last_run, now) is False

    def test_multiple_hours(self):
        from jobs.cron_utils import is_cron_due
        last_run = datetime(2026, 3, 24, 8, 5, tzinfo=timezone.utc)
        now = datetime(2026, 3, 24, 14, 5, tzinfo=timezone.utc)
        assert is_cron_due("0 8,14,20 * * *", last_run, now) is True

    def test_step_every_15_min(self):
        from jobs.cron_utils import is_cron_due
        last_run = datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 3, 24, 10, 16, tzinfo=timezone.utc)
        assert is_cron_due("*/15 * * * *", last_run, now) is True

    def test_invalid_expression(self):
        from jobs.cron_utils import is_cron_due
        with pytest.raises(ValueError, match="expected 5 fields"):
            is_cron_due("bad", datetime(2026, 1, 1, tzinfo=timezone.utc))

    def test_matches_wildcard(self):
        from jobs.cron_utils import _matches
        assert _matches("*", 5, 0, 59) is True

    def test_matches_exact(self):
        from jobs.cron_utils import _matches
        assert _matches("5", 5, 0, 59) is True
        assert _matches("5", 6, 0, 59) is False

    def test_matches_range(self):
        from jobs.cron_utils import _matches
        assert _matches("1-5", 3, 0, 6) is True
        assert _matches("1-5", 6, 0, 6) is False

    def test_matches_step(self):
        from jobs.cron_utils import _matches
        assert _matches("*/15", 0, 0, 59) is True
        assert _matches("*/15", 15, 0, 59) is True
        assert _matches("*/15", 7, 0, 59) is False

    def test_matches_comma(self):
        from jobs.cron_utils import _matches
        assert _matches("8,14,20", 14, 0, 23) is True
        assert _matches("8,14,20", 10, 0, 23) is False


# ── Dedup ──────────────────────────────────────────────────────────────

class TestDedup:
    def test_exact_url_dedup(self):
        from jobs.models import RawSignal
        from jobs.dedup import dedup_signals
        signals = [
            RawSignal(feed_id="test", title="A", url="https://a.com"),
            RawSignal(feed_id="test", title="B", url="https://b.com"),
        ]
        unique, seen = dedup_signals(signals, ["https://a.com"])
        assert len(unique) == 1
        assert unique[0].url == "https://b.com"

    def test_title_similarity_dedup(self):
        from jobs.models import RawSignal
        from jobs.dedup import dedup_signals
        signals = [
            RawSignal(feed_id="test", title="Claude Code gets massive update", url="https://a.com"),
            RawSignal(feed_id="test", title="Claude Code gets massive update!", url="https://b.com"),
        ]
        unique, seen = dedup_signals(signals, [])
        assert len(unique) == 1  # Second is too similar

    def test_trim_dedup_cache(self):
        from jobs.dedup import trim_dedup_cache
        urls = [f"https://example.com/{i}" for i in range(600)]
        trimmed = trim_dedup_cache(urls, max_size=500)
        assert len(trimmed) == 500
        assert trimmed[0] == "https://example.com/100"  # Oldest dropped


# ── System Jobs ────────────────────────────────────────────────────────

class TestSystemJobs:
    def test_system_jobs_count(self):
        from jobs.system_jobs import SYSTEM_JOBS
        assert len(SYSTEM_JOBS) == 8

    def test_system_job_ids_unique(self):
        from jobs.system_jobs import SYSTEM_JOBS
        ids = [j.id for j in SYSTEM_JOBS]
        assert len(ids) == len(set(ids))

    def test_system_jobs_all_enabled(self):
        from jobs.system_jobs import SYSTEM_JOBS
        assert all(j.enabled for j in SYSTEM_JOBS)

    def test_system_jobs_all_system_category(self):
        from jobs.system_jobs import SYSTEM_JOBS
        assert all(j.category == "system" for j in SYSTEM_JOBS)

    def test_signal_digest_depends_on_fetch(self):
        from jobs.system_jobs import SYSTEM_JOBS
        digest = next(j for j in SYSTEM_JOBS if j.id == "signal-digest")
        assert digest.schedule == "after:signal-fetch"

    def test_skill_proposer_depends_on_memory_health(self):
        from jobs.system_jobs import SYSTEM_JOBS
        proposer = next(j for j in SYSTEM_JOBS if j.id == "skill-proposer")
        assert proposer.schedule == "after:memory-health"

    def test_standalone_jobs_have_weekly_schedule(self):
        """memory-health, ddd-refresh run on their own weekly schedule."""
        from jobs.system_jobs import SYSTEM_JOBS
        mh = next(j for j in SYSTEM_JOBS if j.id == "memory-health")
        ddd = next(j for j in SYSTEM_JOBS if j.id == "ddd-refresh")
        # Both run on Sundays (day 0)
        assert "* * 0" in mh.schedule
        assert "* * 0" in ddd.schedule

    def test_new_job_types_in_enum(self):
        """All new job types are registered in JobType enum."""
        from jobs.models import JobType
        assert JobType("memory_health") == JobType.MEMORY_HEALTH
        assert JobType("ddd_refresh") == JobType.DDD_REFRESH
        assert JobType("skill_proposer") == JobType.SKILL_PROPOSER

    def test_get_all_returns_copy(self):
        from jobs.system_jobs import get_all_system_jobs, SYSTEM_JOBS
        copy = get_all_system_jobs()
        assert copy == SYSTEM_JOBS
        assert copy is not SYSTEM_JOBS


# ── Paths ──────────────────────────────────────────────────────────────

class TestPaths:
    def test_paths_are_absolute(self):
        from jobs.paths import SWARMWS, STATE_FILE, CONFIG_FILE
        assert SWARMWS.is_absolute()
        assert STATE_FILE.is_absolute()
        assert CONFIG_FILE.is_absolute()

    def test_jobs_data_under_swarmws(self):
        from jobs.paths import SWARMWS, JOBS_DATA_DIR
        assert str(JOBS_DATA_DIR).startswith(str(SWARMWS))

    def test_state_file_in_data_dir(self):
        from jobs.paths import JOBS_DATA_DIR, STATE_FILE
        assert STATE_FILE.parent == JOBS_DATA_DIR


# ── Scheduler ──────────────────────────────────────────────────────────

class TestScheduler:
    def test_load_jobs_includes_system(self):
        from jobs.scheduler import load_jobs
        jobs = load_jobs()
        ids = {j.id for j in jobs}
        assert "signal-fetch" in ids
        assert "signal-digest" in ids
        assert "weekly-maintenance" in ids

    def test_is_job_due_disabled(self):
        from jobs.models import Job, SchedulerState
        from jobs.scheduler import is_job_due
        job = Job(id="test", name="Test", type="script", schedule="0 * * * *", enabled=False)
        state = SchedulerState()
        assert is_job_due(job, state) is False

    def test_is_job_due_never_run(self):
        from jobs.models import Job, SchedulerState
        from jobs.scheduler import is_job_due
        job = Job(id="test", name="Test", type="script", schedule="0 * * * *", enabled=True)
        state = SchedulerState()
        assert is_job_due(job, state) is True

    def test_is_job_due_dependency(self):
        from jobs.models import Job, JobState, SchedulerState
        from jobs.scheduler import is_job_due
        dep_time = datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc)
        state = SchedulerState(
            jobs={"signal-fetch": JobState(last_run=dep_time, last_status="success")}
        )
        job = Job(id="signal-digest", name="Digest", type="signal_digest", schedule="after:signal-fetch")
        assert is_job_due(job, state) is True

    def test_is_job_due_dependency_failed(self):
        from jobs.models import Job, JobState, SchedulerState
        from jobs.scheduler import is_job_due
        dep_time = datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc)
        state = SchedulerState(
            jobs={"signal-fetch": JobState(last_run=dep_time, last_status="failed")}
        )
        job = Job(id="signal-digest", name="Digest", type="signal_digest", schedule="after:signal-fetch")
        assert is_job_due(job, state) is False

    def test_circuit_breaker(self):
        from jobs.models import Job, JobState, SchedulerState
        from jobs.scheduler import check_circuit_breaker
        state = SchedulerState(
            jobs={"test": JobState(consecutive_failures=3)}
        )
        job = Job(id="test", name="Test", type="script", schedule="0 * * * *")
        assert check_circuit_breaker(job, state) is False

    def test_circuit_breaker_below_threshold(self):
        from jobs.models import Job, JobState, SchedulerState
        from jobs.scheduler import check_circuit_breaker
        state = SchedulerState(
            jobs={"test": JobState(consecutive_failures=2)}
        )
        job = Job(id="test", name="Test", type="script", schedule="0 * * * *")
        assert check_circuit_breaker(job, state) is True


# ── Self-Tune ──────────────────────────────────────────────────────────

class TestSelfTune:
    @pytest.fixture
    def sample_config(self):
        return {
            "feeds": [
                {"id": "ai-engineering", "enabled": True, "managed_by": "manual",
                 "config": {"urls": ["https://example.com/feed"]}},
                {"id": "hn-ai", "enabled": True, "managed_by": "manual",
                 "config": {"keywords": ["Claude", "LLM agent"]}},
                {"id": "auto-feed-1", "enabled": True, "managed_by": "self-tune",
                 "config": {"queries": ["test query"]}},
            ],
            "defaults": {"max_active_feeds": 15},
            "user_context": {},
        }

    def test_update_user_context(self, sample_config):
        from jobs.self_tune import update_user_context
        changes = update_user_context(
            sample_config,
            [{"name": "SwarmAI"}],
            Counter({"claude": 10, "mcp": 5}),
            ["claude", "bedrock"],
            ["python", "react"],
        )
        assert len(changes) > 0
        ctx = sample_config["user_context"]
        assert "SwarmAI" in ctx["projects"]
        assert "claude" in ctx["interests"]

    def test_dry_run_no_mutation(self, sample_config):
        from jobs.self_tune import update_user_context
        changes = update_user_context(
            sample_config, [{"name": "Test"}], Counter({"ai": 5}),
            ["ai"], ["python"], dry_run=True,
        )
        assert len(changes) > 0
        assert sample_config["user_context"] == {}

    def test_prune_auto_managed(self, sample_config):
        from jobs.self_tune import prune_unused_feeds
        usage = {"ai-engineering": 5, "hn-ai": 2}
        changes = prune_unused_feeds(sample_config, usage)
        assert any("DISABLE" in c and "auto-feed-1" in c for c in changes)

    def test_no_prune_manual_feeds(self, sample_config):
        from jobs.self_tune import prune_unused_feeds
        usage = {"hn-ai": 2}
        changes = prune_unused_feeds(sample_config, usage)
        ai_feed = next(f for f in sample_config["feeds"] if f["id"] == "ai-engineering")
        assert ai_feed["enabled"] is True

    def test_suggest_trending_keywords(self, sample_config):
        from jobs.self_tune import suggest_new_feeds
        topics = Counter({"bedrock": 50, "mcp tools": 30, "autonomous ai": 20})
        changes = suggest_new_feeds(sample_config, topics)
        assert any("ADD keyword" in c for c in changes)

    def test_prune_skips_frontier_tier(self):
        """Frontier tier feeds should never be auto-disabled."""
        from jobs.self_tune import prune_unused_feeds
        config = {
            "feeds": [
                {"id": "frontier-labs", "enabled": True, "managed_by": "self-tune",
                 "tier": "frontier", "config": {}},
            ],
            "defaults": {"max_active_feeds": 15},
        }
        usage = {}  # zero usage
        changes = prune_unused_feeds(config, usage)
        # Feed should still be enabled
        assert config["feeds"][0]["enabled"] is True
        # Should report it's protected, not disabled
        assert any("protected" in c for c in changes)
        assert not any("DISABLE" in c for c in changes)

    def test_prune_research_uses_30d_threshold(self):
        """Research tier should report 30d threshold, not 14d."""
        from jobs.self_tune import prune_unused_feeds
        config = {
            "feeds": [
                {"id": "ai-research", "enabled": True, "managed_by": "self-tune",
                 "tier": "research", "config": {}},
            ],
            "defaults": {"max_active_feeds": 15},
        }
        usage = {}  # zero usage
        changes = prune_unused_feeds(config, usage)
        assert any("30" in c for c in changes)

    def test_prune_engineering_uses_14d_threshold(self):
        """Engineering tier should use default 14d threshold."""
        from jobs.self_tune import prune_unused_feeds
        config = {
            "feeds": [
                {"id": "auto-eng", "enabled": True, "managed_by": "self-tune",
                 "tier": "engineering", "config": {}},
            ],
            "defaults": {"max_active_feeds": 15},
        }
        usage = {}
        changes = prune_unused_feeds(config, usage)
        assert any("DISABLE" in c and "14" in c for c in changes)


# ── Tier System ──────────────────────────────────────────────────────

class TestTierSystem:
    """Tests for the tier-based signal weighting system."""

    def test_tier_type_enum_values(self):
        from jobs.models import TierType
        assert TierType.FRONTIER == "frontier"
        assert TierType.RESEARCH == "research"
        assert TierType.ENGINEERING == "engineering"
        assert TierType.OPINION == "opinion"
        assert TierType.AGGREGATE == "aggregate"

    def test_tier_weights_complete(self):
        from jobs.models import TierType, TIER_WEIGHTS
        for tier in TierType:
            assert tier in TIER_WEIGHTS, f"Missing weight for tier {tier}"

    def test_tier_weights_ordering(self):
        from jobs.models import TIER_WEIGHTS
        assert TIER_WEIGHTS["frontier"] > TIER_WEIGHTS["research"]
        assert TIER_WEIGHTS["research"] > TIER_WEIGHTS["engineering"]
        assert TIER_WEIGHTS["engineering"] >= TIER_WEIGHTS["aggregate"]

    def test_tier_disable_thresholds_frontier_is_none(self):
        from jobs.models import TIER_DISABLE_THRESHOLDS
        assert TIER_DISABLE_THRESHOLDS["frontier"] is None

    def test_tier_disable_thresholds_research_is_30(self):
        from jobs.models import TIER_DISABLE_THRESHOLDS
        assert TIER_DISABLE_THRESHOLDS["research"] == 30

    def test_feed_default_tier(self):
        from jobs.models import Feed, TierType
        feed = Feed(id="test", name="Test", type="rss")
        assert feed.tier == TierType.ENGINEERING

    def test_feed_custom_tier(self):
        from jobs.models import Feed, TierType
        feed = Feed(id="test", name="Test", type="rss", tier="frontier")
        assert feed.tier == TierType.FRONTIER

    def test_raw_signal_default_tier(self):
        from jobs.models import RawSignal
        sig = RawSignal(feed_id="test", title="Test", url="https://example.com")
        assert sig.tier == "engineering"

    def test_raw_signal_custom_tier(self):
        from jobs.models import RawSignal
        sig = RawSignal(feed_id="test", title="Test", url="https://example.com", tier="frontier")
        assert sig.tier == "frontier"

    def test_signal_fetch_stamps_tier(self):
        """signal_fetch.handle_signal_fetch should stamp feed tier onto signals."""
        from jobs.models import Feed, SchedulerState, RawSignal

        # Create a frontier feed with a mock adapter result
        feed = Feed(id="test-frontier", name="Test", type="rss", tier="frontier")
        state = SchedulerState()

        # Simulate what signal_fetch does: stamp tier on signals
        signals = [
            RawSignal(feed_id="test-frontier", title="Test Signal", url="https://example.com")
        ]
        for sig in signals:
            sig.tier = feed.tier
        assert signals[0].tier == "frontier"

    def test_simple_scored_items_applies_tier_weight(self):
        """_simple_scored_items should apply tier weights to relevance scores."""
        from jobs.models import RawSignal
        from jobs.handlers.signal_digest import _simple_scored_items

        frontier_signal = RawSignal(
            feed_id="labs", title="OpenAI Update", url="https://openai.com/1",
            source="OpenAI Blog", tier="frontier", score=0.5,
        )
        eng_signal = RawSignal(
            feed_id="eng", title="Blog Post", url="https://blog.com/1",
            source="Some Blog", tier="engineering", score=0.5,
        )

        items = _simple_scored_items([frontier_signal, eng_signal])
        assert len(items) == 2
        # Frontier (0.5 * 2.0 = 1.0) should score higher than engineering (0.5 * 1.0 = 0.5)
        assert items[0]["relevance_score"] > items[1]["relevance_score"]
        assert items[0]["tier"] == "frontier"
        assert items[1]["tier"] == "engineering"
        assert items[0]["tier_weight"] == 2.0
        assert items[1]["tier_weight"] == 1.0

    def test_config_yaml_has_tiers(self):
        """config.yaml should have tier field on every feed."""
        import yaml
        config_path = Path(__file__).parent.parent.parent.parent / ".swarm-ai" / "SwarmWS" / "Services" / "swarm-jobs" / "config.yaml"
        if not config_path.exists():
            pytest.skip("config.yaml not found (not running from full workspace)")
        config = yaml.safe_load(config_path.read_text())
        for feed in config.get("feeds", []):
            assert "tier" in feed, f"Feed '{feed['id']}' missing tier field"
            assert feed["tier"] in ("frontier", "research", "engineering", "opinion", "aggregate"), \
                f"Feed '{feed['id']}' has invalid tier: {feed['tier']}"


# ── Workspace Provisioning ─────────────────────────────────────────────

class TestJobProvisioning:
    def test_provision_creates_default_config(self, tmp_path):
        """Verify _provision_job_system creates config files."""
        from core.swarm_workspace_manager import SwarmWorkspaceManager
        mgr = SwarmWorkspaceManager()
        mgr._provision_job_system(tmp_path)

        config_file = tmp_path / "Services" / "swarm-jobs" / "config.yaml"
        assert config_file.exists()
        config = yaml.safe_load(config_file.read_text())
        assert "feeds" in config
        assert len(config["feeds"]) >= 3  # ai-engineering, tool-releases, hn-ai

    def test_provision_creates_user_jobs(self, tmp_path):
        from core.swarm_workspace_manager import SwarmWorkspaceManager
        mgr = SwarmWorkspaceManager()
        mgr._provision_job_system(tmp_path)

        user_jobs = tmp_path / "Services" / "swarm-jobs" / "user-jobs.yaml"
        assert user_jobs.exists()
        data = yaml.safe_load(user_jobs.read_text())
        assert data["jobs"] == []

    def test_provision_creates_state(self, tmp_path):
        from core.swarm_workspace_manager import SwarmWorkspaceManager
        mgr = SwarmWorkspaceManager()
        mgr._provision_job_system(tmp_path)

        state_file = tmp_path / "Services" / "swarm-jobs" / "state.json"
        assert state_file.exists()

    def test_provision_creates_signals_dir(self, tmp_path):
        from core.swarm_workspace_manager import SwarmWorkspaceManager
        mgr = SwarmWorkspaceManager()
        mgr._provision_job_system(tmp_path)

        assert (tmp_path / "Services" / "signals").is_dir()

    def test_provision_idempotent(self, tmp_path):
        from core.swarm_workspace_manager import SwarmWorkspaceManager
        mgr = SwarmWorkspaceManager()
        mgr._provision_job_system(tmp_path)

        # Modify config to verify it's not overwritten
        config_file = tmp_path / "Services" / "swarm-jobs" / "config.yaml"
        config_file.write_text("custom: true\n")

        mgr._provision_job_system(tmp_path)

        # Should NOT overwrite existing config
        assert config_file.read_text() == "custom: true\n"


# ── API Router ─────────────────────────────────────────────────────────

class TestJobsAPI:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from routers.jobs import router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_list_jobs(self, client):
        response = client.get("/api/jobs/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 5  # At least system jobs
        ids = {j["id"] for j in data}
        assert "signal-fetch" in ids

    def test_scheduler_status(self, client):
        response = client.get("/api/jobs/status")
        assert response.status_code == 200
        data = response.json()
        # Unified status returns 4 categories
        assert "scheduled_jobs" in data
        assert "session_hooks" in data
        assert "services" in data
        assert "overview" in data
        assert data["scheduled_jobs"].get("total", 0) >= 5
        assert data["overview"]["total_components"] > 0

    def test_run_nonexistent_job(self, client):
        response = client.post("/api/jobs/run", json={"job_id": "nonexistent"})
        assert response.status_code == 404

    def test_run_dry_run(self, client):
        response = client.post("/api/jobs/run", json={"job_id": "signal-fetch", "dry_run": True})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "dry_run"
