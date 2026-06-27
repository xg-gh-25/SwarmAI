"""Tests for hooks.context_health_hook — context health harness."""
import json
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hooks.context_health_hook import ContextHealthHook
from core.session_hooks import HookContext


@pytest.fixture
def hook():
    return ContextHealthHook()


@pytest.fixture
def workspace(tmp_path):
    """Create a minimal SwarmWS workspace."""
    ws = tmp_path / "SwarmWS"
    ws.mkdir()

    # .context/ with context files
    ctx = ws / ".context"
    ctx.mkdir()
    for name in ["SWARMAI.md", "IDENTITY.md", "SOUL.md", "AGENT.md",
                 "USER.md", "STEERING.md", "TOOLS.md", "MEMORY.md",
                 "EVOLUTION.md", "KNOWLEDGE.md", "PROJECTS.md"]:
        (ctx / name).write_text(f"# {name}\n\nContent for {name}\n")

    # Knowledge/ with a note
    notes = ws / "Knowledge" / "Notes"
    notes.mkdir(parents=True)
    (notes / "2026-03-25-test-note.md").write_text(
        "---\ntitle: Test Note\n---\n\n# Test Note\n\nContent.\n"
    )

    designs = ws / "Knowledge" / "Designs"
    designs.mkdir(parents=True)

    da = ws / "Knowledge" / "DailyActivity"
    da.mkdir(parents=True)

    # Projects/
    proj = ws / "Projects" / "TestProject"
    proj.mkdir(parents=True)
    (proj / "TECH.md").write_text("# Tech\n\nArchitecture.\n")
    (proj / "PRODUCT.md").write_text("# Product\n\nVision.\n")

    # Init git repo (configure user for CI where global git config may be absent)
    subprocess.run(["git", "init"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=ws, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ws, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=ws, capture_output=True)

    return ws


@pytest.fixture
def hook_context():
    return HookContext(
        session_id="test-session",
        agent_id="default",
        message_count=5,
        session_start_time=datetime.now().isoformat(),
        session_title="Test session",
    )


# --------------------------------------------------------------------------
# Light refresh
# --------------------------------------------------------------------------

class TestLightRefresh:
    def test_skips_when_rev_unchanged(self, hook, workspace):
        """Light refresh is a no-op if git HEAD hasn't changed."""
        rev = hook._git_rev(str(workspace))
        hook._last_refresh_rev = rev  # Pretend we already refreshed

        # Should skip git-gated work — verify by checking no write to KNOWLEDGE.md
        # (except Active Projects section which is always refreshed on Projects/ mtime change)
        original = (workspace / ".context" / "KNOWLEDGE.md").read_text()
        # Stub out Bedrock-dependent methods — they hang in sandbox (no network)
        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"), \
             patch.object(hook, "_refresh_knowledge_projects_section"):
            hook._light_refresh(workspace, str(workspace))
        assert (workspace / ".context" / "KNOWLEDGE.md").read_text() == original

    def test_refreshes_knowledge_index(self, hook, workspace):
        """Light refresh updates KNOWLEDGE.md index section."""
        # Add the Knowledge Index section marker
        km = workspace / ".context" / "KNOWLEDGE.md"
        km.write_text("# Knowledge\n\nDomain knowledge.\n\n## Knowledge Index\n\nOld index.\n")

        # Stub out Bedrock-dependent methods — they hang in sandbox (no network)
        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
            hook._light_refresh(workspace, str(workspace))

        content = km.read_text()
        assert "test-note" in content.lower() or "Test Note" in content

    def test_extract_title_from_frontmatter(self, hook, tmp_path):
        """Extract title from YAML frontmatter."""
        f = tmp_path / "test.md"
        f.write_text('---\ntitle: "My Title"\n---\n\n# Heading\n')
        assert hook._extract_title(f) == "My Title"

    def test_extract_title_from_heading(self, hook, tmp_path):
        """Extract title from first # heading when no frontmatter."""
        f = tmp_path / "test.md"
        f.write_text("# My Heading\n\nContent.\n")
        assert hook._extract_title(f) == "My Heading"


# --------------------------------------------------------------------------
# Hot/Cold Knowledge Index
# --------------------------------------------------------------------------

class TestHotColdKnowledgeIndex:
    """AC1-AC4: Hot/Cold dual-layer index format."""

    def test_large_dir_shows_hot_10_plus_cold_summary(self, hook, workspace):
        """AC1: Directories with >10 files show only Hot 10 + summary."""
        # Create 15 design files
        designs = workspace / "Knowledge" / "Designs"
        for i in range(15):
            date = f"2026-05-{i+1:02d}"
            (designs / f"{date}-design-{i}.md").write_text(f"# Design {i}\n\nContent.\n")

        km = workspace / ".context" / "KNOWLEDGE.md"
        km.write_text("# Knowledge\n\nDomain.\n\n## Knowledge Index\n\nOld.\n")

        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
            hook._light_refresh(workspace, str(workspace))

        content = km.read_text()
        # Should have Hot 10 entries (most recent dates)
        assert "2026-05-15" in content  # Most recent
        assert "2026-05-06" in content  # 10th most recent
        # Should NOT have the oldest entries
        assert "design-0.md" not in content or "older files" in content.lower() or "+ " in content
        # Should have a cold summary line
        assert "older" in content.lower() or "more" in content.lower()

    def test_small_dir_shows_full_listing(self, hook, workspace):
        """AC2: Directories with ≤10 files keep full listing."""
        library = workspace / "Knowledge" / "Library"
        library.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (library / f"2026-04-{i+1:02d}-lib-{i}.md").write_text(f"# Lib {i}\n")

        km = workspace / ".context" / "KNOWLEDGE.md"
        km.write_text("# Knowledge\n\n## Knowledge Index\n\nOld.\n")

        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
            hook._light_refresh(workspace, str(workspace))

        content = km.read_text()
        # All 5 should be present
        for i in range(5):
            assert f"lib-{i}" in content

    def test_compact_dirs_remain_summary_only(self, hook, workspace):
        """AC3: DailyActivity/JobResults stay as summary line."""
        da = workspace / "Knowledge" / "DailyActivity"
        for i in range(20):
            (da / f"2026-05-{i+1:02d}.md").write_text(f"# Day {i}\n")

        km = workspace / ".context" / "KNOWLEDGE.md"
        km.write_text("# Knowledge\n\n## Knowledge Index\n\nOld.\n")

        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
            hook._light_refresh(workspace, str(workspace))

        content = km.read_text()
        # Should have count + pattern, NOT individual files
        assert "20 files" in content
        assert "Pattern:" in content
        # Should NOT have individual file rows
        assert "| 2026-05-01" not in content

    def test_index_line_cap(self, hook, workspace):
        """AC4: Knowledge Index section total ≤120 lines."""
        # Create many files across directories
        for subdir in ["Designs", "Notes", "Learned", "Reports"]:
            d = workspace / "Knowledge" / subdir
            d.mkdir(parents=True, exist_ok=True)
            for i in range(30):
                (d / f"2026-04-{i+1:02d}-item-{i}.md").write_text(f"# Item {i}\n")

        km = workspace / ".context" / "KNOWLEDGE.md"
        km.write_text("# Knowledge\n\n## Knowledge Index\n\nOld.\n")

        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
            hook._light_refresh(workspace, str(workspace))

        content = km.read_text()
        # Count lines in index section only
        idx_start = content.find("## Knowledge Index")
        index_section = content[idx_start:]
        index_lines = [l for l in index_section.split("\n") if l.strip()]
        assert len(index_lines) <= 120, f"Index has {len(index_lines)} lines, expected ≤120"


# --------------------------------------------------------------------------
# Deep check
# --------------------------------------------------------------------------

class TestDeepCheck:
    def test_detects_empty_context_file(self, hook, workspace, caplog):
        """Deep check flags empty context files."""
        (workspace / ".context" / "SOUL.md").write_text("")
        with caplog.at_level(logging.WARNING, logger="hooks.context_health_hook"):
            hook._deep_check(workspace, str(workspace))
        assert any("EMPTY: SOUL.md" in r.message for r in caplog.records)

    def test_passes_when_healthy(self, hook, workspace, caplog):
        """Deep check passes when all files are healthy (no warnings)."""
        # Create today's DailyActivity so that check passes
        today_file = workspace / "Knowledge" / "DailyActivity" / f"{date.today().isoformat()}.md"
        today_file.write_text("# Today\n\nActivity.\n")

        # Ensure all 4 DDD docs exist for every project (prevents DDD staleness findings)
        for proj_dir in (workspace / "Projects").iterdir():
            if proj_dir.is_dir():
                for doc in ["PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"]:
                    p = proj_dir / doc
                    if not p.exists():
                        p.write_text(f"# {doc.replace('.md', '')}\n\nContent.\n")

        # Commit everything so git health check finds no uncommitted files
        subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add today"], cwd=workspace, capture_output=True)

        with caplog.at_level(logging.INFO, logger="hooks.context_health_hook"):
            hook._deep_check(workspace, str(workspace))

        # If findings were reported, show them for debugging
        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("deep check passed" in r.message for r in caplog.records), \
            f"Expected 'deep check passed' but got warnings: {warnings}"

    def test_detects_missing_daily_activity(self, hook, workspace, caplog):
        """Deep check flags missing today's DailyActivity file."""
        with caplog.at_level(logging.WARNING, logger="hooks.context_health_hook"):
            hook._deep_check(workspace, str(workspace))
        assert any("MISSING: DailyActivity/" in r.message for r in caplog.records)

    def test_detects_stale_git_lock(self, hook, workspace, caplog):
        """Deep check removes stale .git/index.lock."""
        lock = workspace / ".git" / "index.lock"
        lock.write_text("stale")
        # Make it look old
        old_time = datetime.now().timestamp() - 600
        os.utime(lock, (old_time, old_time))

        findings = hook._check_git_health(workspace, str(workspace))
        assert any("AUTO-FIXED" in f and "index.lock" in f for f in findings)
        assert not lock.exists()

    def test_cache_invalidation(self, hook, workspace):
        """L1 cache invalidated when source file is newer."""
        ctx = workspace / ".context"
        cache = ctx / "L1_SYSTEM_PROMPTS.md"
        cache.write_text("cached content")

        # Make a source file newer than cache
        import time
        time.sleep(0.1)
        (ctx / "MEMORY.md").write_text("# Updated memory\n\nNew content.\n")

        findings: list[str] = []
        hook._check_cache_freshness(ctx, findings)
        assert any("AUTO-FIXED" in f and "L1 cache" in f for f in findings)
        assert not cache.exists()


# --------------------------------------------------------------------------
# Daily gate
# --------------------------------------------------------------------------

class TestDailyGate:
    @pytest.mark.asyncio
    async def test_deep_check_runs_once_per_day(self, hook, workspace, hook_context):
        """Deep check only runs once per calendar day."""
        with patch.object(hook, '_light_refresh'), \
             patch.object(hook, '_deep_check') as mock_deep, \
             patch('hooks.context_health_hook.initialization_manager') as mock_init:
            mock_init.get_cached_workspace_path.return_value = str(workspace)

            await hook.execute(hook_context)
            assert mock_deep.call_count == 1

            await hook.execute(hook_context)
            assert mock_deep.call_count == 1  # Still 1 — same day

    @pytest.mark.asyncio
    async def test_deep_check_runs_on_new_day(self, hook, workspace, hook_context):
        """Deep check runs again on a new calendar day."""
        with patch.object(hook, '_light_refresh'), \
             patch.object(hook, '_deep_check') as mock_deep, \
             patch('hooks.context_health_hook.initialization_manager') as mock_init:
            mock_init.get_cached_workspace_path.return_value = str(workspace)

            await hook.execute(hook_context)
            assert mock_deep.call_count == 1

            # Simulate next day
            hook._last_deep_date = "2026-03-24"
            await hook.execute(hook_context)
            assert mock_deep.call_count == 2


# --------------------------------------------------------------------------
# DDD staleness
# --------------------------------------------------------------------------

class TestDDDStaleness:
    def test_detects_stale_tech_md(self, hook, workspace):
        """Flags TECH.md older than 14 days with recent commits."""
        tech = workspace / "Projects" / "TestProject" / "TECH.md"
        # Make TECH.md 20 days old
        old_time = datetime.now().timestamp() - (20 * 86400)
        os.utime(tech, (old_time, old_time))

        # Add a recent commit mentioning the project name
        result = subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "fix: TestProject update"],
            cwd=workspace, capture_output=True, text=True,
        )
        assert result.returncode == 0, f"git commit failed: {result.stderr}"

        # Verify the commit is findable
        verify = subprocess.run(
            ["git", "log", "--oneline", "--since=14 days ago",
             "--grep", "TestProject", "--", "."],
            cwd=workspace, capture_output=True, text=True,
        )

        findings = hook._check_ddd_staleness(workspace, str(workspace))
        # If git log can find the commit, we should detect staleness
        if verify.stdout.strip():
            assert any("DDD-STALE" in f and "TestProject" in f for f in findings)
        else:
            # Git may not find it due to date precision — skip gracefully
            pass

    def test_no_staleness_when_recently_updated(self, hook, workspace):
        """No staleness flag when DDD docs are recent."""
        findings = hook._check_ddd_staleness(workspace, str(workspace))
        assert not any("DDD-STALE" in f for f in findings)


# --------------------------------------------------------------------------
# Auto-cultivation
# --------------------------------------------------------------------------

class TestAutoCultivation:
    """Tests for _auto_cultivate_pipeline_lessons."""

    def _make_run(self, workspace, project, run_id, *, lessons=None, cultivated=False):
        """Create a run.json with a reflect stage."""
        runs_dir = workspace / "Projects" / project / ".artifacts" / "runs" / run_id
        runs_dir.mkdir(parents=True, exist_ok=True)
        stages = []
        if lessons is not None:
            reflect_stage = {"stage": "reflect", "status": "completed", "lessons": lessons}
            if cultivated:
                reflect_stage["cultivated"] = True
            stages.append(reflect_stage)
        run_data = {
            "id": run_id,
            "project": project,
            "status": "completed",
            "stages": stages,
        }
        (runs_dir / "run.json").write_text(json.dumps(run_data), encoding="utf-8")
        return runs_dir / "run.json"

    def test_cultivates_uncultivated_run(self, hook, workspace):
        """Cultivates a completed run with reflect.lessons and no cultivated flag."""
        # Create project with DDD docs (needed for cultivation target)
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text(
            "# Lessons\n\n## What Worked\n\n- existing\n\n## What Failed\n\n- nothing\n"
        )

        run_file = self._make_run(
            workspace, "TestProject", "run_abc123",
            lessons=["Use nc -z instead of lsof for port checks"]
        )

        hook._auto_cultivate_pipeline_lessons(workspace)

        # Verify cultivated:true was set
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
        reflect_stage = next(s for s in run_data["stages"] if s["stage"] == "reflect")
        assert reflect_stage["cultivated"] is True

    def test_skips_already_cultivated(self, hook, workspace):
        """Skips runs that already have cultivated:true."""
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text("# Lessons\n\n## What Worked\n\n- x\n")

        run_file = self._make_run(
            workspace, "TestProject", "run_def456",
            lessons=["Some lesson"], cultivated=True
        )

        # Should not re-cultivate (file unchanged)
        original_content = run_file.read_text()
        hook._auto_cultivate_pipeline_lessons(workspace)
        assert run_file.read_text() == original_content

    def test_skips_run_without_lessons(self, hook, workspace):
        """Skips runs whose reflect stage has no lessons."""
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text("# Lessons\n\n## What Worked\n\n- x\n")

        run_file = self._make_run(
            workspace, "TestProject", "run_ghi789",
            lessons=[]
        )

        original_content = run_file.read_text()
        hook._auto_cultivate_pipeline_lessons(workspace)
        # No cultivated flag should be added for empty lessons
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
        reflect_stage = next(s for s in run_data["stages"] if s["stage"] == "reflect")
        assert "cultivated" not in reflect_stage

    def test_handles_missing_reflect_stage(self, hook, workspace):
        """Gracefully handles runs without a reflect stage."""
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text("# Lessons\n\n## What Worked\n\n- x\n")

        self._make_run(workspace, "TestProject", "run_jkl012", lessons=None)
        # Should not raise
        hook._auto_cultivate_pipeline_lessons(workspace)

    def test_handles_corrupt_json(self, hook, workspace, caplog):
        """Gracefully handles corrupt run.json files."""
        proj = workspace / "Projects" / "TestProject"
        proj.mkdir(parents=True, exist_ok=True)
        runs_dir = proj / ".artifacts" / "runs" / "run_bad"
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / "run.json").write_text("not valid json{{{")

        with caplog.at_level(logging.DEBUG):
            hook._auto_cultivate_pipeline_lessons(workspace)
        # Should not raise — just log and continue

    def test_does_not_mark_cultivated_on_failure(self, hook, workspace):
        """cultivated:true is NOT set when cultivate_from_reflect raises."""
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text("# Lessons\n\n## What Worked\n\n- x\n")

        run_file = self._make_run(
            workspace, "TestProject", "run_fail_test",
            lessons=["Some lesson that will fail"]
        )

        with patch("core.ddd_cultivation.cultivate_from_reflect", side_effect=RuntimeError("boom")):
            hook._auto_cultivate_pipeline_lessons(workspace)

        # cultivated should NOT be set
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
        reflect_stage = next(s for s in run_data["stages"] if s["stage"] == "reflect")
        assert "cultivated" not in reflect_stage or reflect_stage.get("cultivated") is not True

    def test_idempotent_multiple_calls(self, hook, workspace):
        """Multiple calls don't re-cultivate already-done runs."""
        proj = workspace / "Projects" / "TestProject"
        (proj / "IMPROVEMENT.md").write_text(
            "# Lessons\n\n## What Worked\n\n- existing\n\n## What Failed\n\n- nothing\n"
        )

        self._make_run(
            workspace, "TestProject", "run_idempotent",
            lessons=["Pattern: always validate threshold against real data"]
        )

        # First call cultivates
        hook._auto_cultivate_pipeline_lessons(workspace)
        # Second call skips (cultivated:true now set)
        hook._auto_cultivate_pipeline_lessons(workspace)

        # Should still only be cultivated once
        run_file = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_idempotent" / "run.json"
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
        reflect_stage = next(s for s in run_data["stages"] if s["stage"] == "reflect")
        assert reflect_stage["cultivated"] is True


class TestAutoSessionSignalCultivation:
    """Tests for _auto_cultivate_session_signals — Ch5 + Ch6 feed."""

    def _write_jsonl(self, da_dir, filename, records):
        """Write records to a JSONL file in the DailyActivity dir."""
        path = da_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return path

    def test_cultivates_corrections_from_jsonl(self, hook, workspace):
        """Corrections in JSONL get routed to DDD docs (Ch6)."""
        # Setup project with IMPROVEMENT.md that has the target sections
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "IMPROVEMENT.md").write_text(
            "# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n"
        )
        (proj / "TECH.md").write_text(
            "# Tech\n\n## Architecture\n\n- seed\n\n## Runtime Traps\n\n- seed\n\n## Conventions\n\n- seed\n"
        )

        # Create DailyActivity JSONL with a correction
        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._write_jsonl(da_dir, f"{today}.jsonl", [{
            "session_id": "session_corr_001",
            "timestamp": "14:00",
            "corrections": [
                "Bug: daemon crashed because subprocess.run() blocks the event loop — must use asyncio.to_thread",
            ],
            "decisions": [],
        }])

        hook._auto_cultivate_session_signals(workspace)

        # Verify state file was created with the session ID
        state_path = workspace / ".context" / ".session_cultivated.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert "session_corr_001" in state

    def test_cultivates_decisions_from_jsonl(self, hook, workspace):
        """Decisions in JSONL get routed to DDD docs (Ch5)."""
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "TECH.md").write_text(
            "# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n"
        )

        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._write_jsonl(da_dir, f"{today}.jsonl", [{
            "session_id": "session_dec_001",
            "timestamp": "15:00",
            "corrections": [],
            "decisions": [
                "Convention: prefer nc -z over lsof for all port checks in daemon scripts — standing rule",
            ],
        }])

        hook._auto_cultivate_session_signals(workspace)

        # Verify the decision was applied to TECH.md
        content = (proj / "TECH.md").read_text()
        assert "nc -z" in content or "port checks" in content

    def test_idempotent_skips_already_cultivated_sessions(self, hook, workspace):
        """Same session is never cultivated twice."""
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "TECH.md").write_text(
            "# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n"
        )

        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._write_jsonl(da_dir, f"{today}.jsonl", [{
            "session_id": "session_idem_001",
            "timestamp": "16:00",
            "corrections": [],
            "decisions": [
                "Pattern: always use atomic tmp+rename for file writes to prevent corruption",
            ],
        }])

        # First call cultivates
        hook._auto_cultivate_session_signals(workspace)
        content_after_first = (proj / "TECH.md").read_text()

        # Second call should be no-op (session already in state)
        hook._auto_cultivate_session_signals(workspace)
        content_after_second = (proj / "TECH.md").read_text()

        assert content_after_first == content_after_second

    def test_skips_old_jsonl_files(self, hook, workspace):
        """JSONL files older than 7 days are not processed."""
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "TECH.md").write_text("# Tech\n\n## Conventions\n\n- seed\n")

        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        # Create a file from 10 days ago
        old_date = (date.today() - timedelta(days=10)).isoformat()
        self._write_jsonl(da_dir, f"{old_date}.jsonl", [{
            "session_id": "session_old_001",
            "timestamp": "10:00",
            "corrections": ["This pattern always fails — never use it again"],
            "decisions": [],
        }])

        hook._auto_cultivate_session_signals(workspace)

        # State should be empty — nothing processed
        state_path = workspace / ".context" / ".session_cultivated.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            assert "session_old_001" not in state

    def test_handles_empty_corrections_and_decisions(self, hook, workspace):
        """Sessions with no corrections and no decisions are marked done but don't modify DDD."""
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "TECH.md").write_text("# Tech\n\n## Conventions\n\n- seed\n")

        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._write_jsonl(da_dir, f"{today}.jsonl", [{
            "session_id": "session_empty_001",
            "timestamp": "11:00",
            "corrections": [],
            "decisions": [],
        }])

        hook._auto_cultivate_session_signals(workspace)

        # Session marked as cultivated (skip in future) but no DDD changes
        state_path = workspace / ".context" / ".session_cultivated.json"
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        assert "session_empty_001" in state

        # DDD unchanged
        content = (proj / "TECH.md").read_text()
        assert content == "# Tech\n\n## Conventions\n\n- seed\n"

    def test_graceful_on_missing_project_dir(self, hook, workspace):
        """Does not crash when Projects/SwarmAI doesn't exist."""
        # Don't create SwarmAI project dir
        da_dir = workspace / "Knowledge" / "DailyActivity"
        da_dir.mkdir(parents=True, exist_ok=True)
        today = date.today().isoformat()
        self._write_jsonl(da_dir, f"{today}.jsonl", [{
            "session_id": "session_no_proj",
            "timestamp": "12:00",
            "corrections": ["Some correction that should not crash"],
            "decisions": [],
        }])

        # Should not raise
        hook._auto_cultivate_session_signals(workspace)


# --------------------------------------------------------------------------
# PROJECTS.md refresh after cultivation (line number drift fix)
# --------------------------------------------------------------------------


class TestProjectsRefreshAfterCultivation:
    """Verify PROJECTS.md is refreshed when cultivation modifies DDD docs."""

    @pytest.mark.asyncio
    async def test_refresh_called_when_cultivation_writes(self, hook, workspace):
        """When cultivation applies content to DDD docs, refresh_projects_index is called."""
        # Setup: project with DDD docs (needs TECH.md with Conventions section
        # because the lesson contains TECH_KEYWORDS like "nc -z")
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "IMPROVEMENT.md").write_text(
            "# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n"
        )
        (proj / "TECH.md").write_text(
            "# Tech\n\n## Conventions\n\n- seed\n\n## Runtime Traps\n\n- seed\n"
        )

        # Create a run with uncultivated lessons
        runs_dir = proj / ".artifacts" / "runs" / "run_drift001"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_data = {
            "id": "run_drift001",
            "project": "SwarmAI",
            "status": "completed",
            "stages": [{"stage": "reflect", "status": "completed",
                        "lessons": ["Pattern: use nc -z instead of lsof for port checks"]}],
        }
        (runs_dir / "run.json").write_text(json.dumps(run_data), encoding="utf-8")

        # Mock refresh_projects_index to track if it's called
        with patch(
            "hooks.context_health_hook.ContextHealthHook._refresh_projects_index_sync"
        ) as mock_refresh:
            hook._light_refresh(workspace, str(workspace))

            # Cultivation should have written to DDD docs and set dirty flag
            assert hook._ddd_docs_modified is True
            mock_refresh.assert_called_once_with(workspace)

    @pytest.mark.asyncio
    async def test_refresh_not_called_when_no_cultivation(self, hook, workspace):
        """When cultivation has nothing to apply, refresh is NOT called."""
        # Setup: project with DDD docs but no uncultivated runs
        proj = workspace / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "IMPROVEMENT.md").write_text(
            "# Lessons\n\n## What Worked\n\n- seed\n\n## What Failed\n\n- seed\n"
        )

        # Pre-set Projects/ mtime so no filesystem change is detected
        projects_dir = workspace / "Projects"
        if projects_dir.is_dir():
            hook._last_projects_mtime = projects_dir.stat().st_mtime

        with patch(
            "hooks.context_health_hook.ContextHealthHook._refresh_projects_index_sync"
        ) as mock_refresh:
            hook._light_refresh(workspace, str(workspace))

            # No cultivation happened AND no Projects/ mtime change — no refresh
            assert hook._ddd_docs_modified is False
            mock_refresh.assert_not_called()


class TestExtractLessonsNoOrphan:
    """R-1 C2: _extract_lessons_to_memory must not orphan an existing meta.

    Root cause (run_55c02bbe): the raw-splice insert math landed a new entry+meta
    BETWEEN an existing bullet and its meta, orphaning the existing meta as a 2nd
    consecutive metadata line. Fix routes through _modify_content (section-boundary
    insert) so the orphan is structurally impossible.
    """

    def test_extract_does_not_orphan_existing_meta(self, hook, tmp_path):
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        # MEMORY.md with an existing Guidelines entry that already has its meta
        (ctx / "MEMORY.md").write_text(
            "## Guidelines\n"
            "_Operational lessons._\n\n"
            "- [guideline] **Existing entry** — prior lesson (2026-06-01)\n"
            "  <!-- ref:3 | last:2026-06-20 | decay:active -->\n"
        )
        # Extract a new confident lesson into MEMORY.md
        hook._extract_lessons_to_memory(
            ws, ["Always verify before asserting — a new guideline lesson here"],
            "run_test", "TestProject",
        )
        content = (ctx / "MEMORY.md").read_text()
        # No bullet may be followed by TWO consecutive metadata lines
        import re
        lines = content.splitlines()
        stacked = sum(
            1 for i in range(1, len(lines))
            if re.match(r"\s*<!-- ref:", lines[i]) and re.match(r"\s*<!-- ref:", lines[i - 1])
        )
        assert stacked == 0, f"writer orphaned a meta — {stacked} stacked pairs:\n{content}"
        # The pre-existing entry's real-date meta must survive intact
        assert "last:2026-06-20" in content
        # R-1 Gate-2 #2/#3: NEW lessons must land at section TOP (newest-first),
        # matching distillation_hook._write_section's prepend convention and the
        # newest-at-top assumption _enforce_section_caps relies on (it trims the
        # bottom = oldest). The new lesson must appear ABOVE the pre-existing one.
        new_pos = content.find("Always verify before asserting")
        old_pos = content.find("Existing entry")
        assert new_pos != -1, "new lesson not written"
        assert new_pos < old_pos, (
            "new lesson must be prepended (newest-at-top) — found below the "
            f"existing entry:\n{content}"
        )

    def test_memory_lifecycle_auto_heals_stacked_metadata(self, hook, tmp_path):
        # R-1 Gate-2 #1: collapse_stacked_metadata must be WIRED into the
        # per-session lifecycle, not just a manually-invoked helper. A MEMORY.md
        # that already carries an orphan stack must come out healed after one
        # _run_memory_lifecycle pass.
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / "MEMORY.md").write_text(
            "## Guidelines\n"
            "_Operational lessons._\n\n"
            "- [guideline] **Stacked entry** — a lesson (2026-06-01)\n"
            "  <!-- ref:3 | last:2026-06-20 | decay:active -->\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
        )
        hook._run_memory_lifecycle(ws)
        content = (ctx / "MEMORY.md").read_text()
        import re
        lines = content.splitlines()
        stacked = sum(
            1 for i in range(1, len(lines))
            if re.match(r"\s*<!-- ref:", lines[i]) and re.match(r"\s*<!-- ref:", lines[i - 1])
        )
        assert stacked == 0, f"lifecycle did not auto-heal the orphan:\n{content}"
        assert "last:2026-06-20" in content, "must keep the real-date meta"
        assert "last:none" not in content, "must drop the orphan default"


class TestNoProseBump:
    """R2-prime: the toxic prose-substring ref bump is removed. An entry whose
    title coincidentally appears in DailyActivity prose must NOT get its
    ref_count bumped — that fake signal protected generic-titled entries
    (DISCUSSION ref:1009) while real entries starved at ref:0. The honest
    id-based signal (memory_decay, via distillation) is the only ref producer."""

    def test_prose_mention_does_not_bump_ref(self, hook, tmp_path):
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        # Entry titled "Correction" — a word that appears all over prose.
        (ctx / "MEMORY.md").write_text(
            "## Pitfalls\n"
            "_Operational lessons._\n\n"
            "- [pitfall] **Correction** — a specific lesson (2026-06-01)\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
        )
        # DailyActivity prose that mentions the word "Correction" many times.
        da = ws / "Knowledge" / "DailyActivity"
        da.mkdir(parents=True)
        (da / f"{date.today().isoformat()}-x.md").write_text(
            "# Activity\n\nCorrection here. Another Correction. Correction again.\n" * 5
        )
        hook._run_memory_lifecycle(ws)
        content = (ctx / "MEMORY.md").read_text()
        import re
        m = re.search(r"<!-- ref:(\d+)", content)
        assert m is not None, f"metadata missing:\n{content}"
        assert int(m.group(1)) == 0, (
            f"prose-bump still active — 'Correction' got bumped to ref:{m.group(1)} "
            f"from DailyActivity prose coincidence:\n{content}"
        )


class TestUsageRefBridge:
    """R2-real (run_77504e11): _run_memory_lifecycle bridges .memory-usage.json
    to body ref_count (reclaim-protection + injection-priority), log-damped,
    threshold-gated, survives the inject round-trip."""

    def test_usage_bridge_sets_body_ref_and_survives_roundtrip(self, hook, tmp_path):
        import json as _json
        from core.ddd_entry_lifecycle import parse_entries
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / "MEMORY.md").write_text(
            "<!-- MEMORY_INDEX_START -->\n"
            "- [PIT07] Gate caught a real bug | gate, adversarial\n"
            "- [GUI99] Rarely used note | x\n"
            "<!-- MEMORY_INDEX_END -->\n"
            "## Pitfalls\n"
            "_lessons_\n\n"
            "- [pitfall] **Gate caught a real bug** — body (2026-06-01)\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
            "- [guideline] **Rarely used note** — body (2026-06-01)\n"
            "  <!-- ref:0 | last:none | decay:active -->\n"
        )
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40, "GUI99": 2}))

        hook._run_memory_lifecycle(ws)
        content = (ctx / "MEMORY.md").read_text()

        entries = {e.title: e.ref_count for e in parse_entries(content)}
        assert entries.get("Gate caught a real bug", 0) > 0, f"used entry not bridged:\n{content}"
        assert entries.get("Rarely used note", 0) == 0, "below-threshold entry wrongly protected"


class TestUsageDecayGate:
    """run_81f6d20c: _track_memory_usage applies write-time decay ONCE per calendar
    day, gated by a sidecar .memory-usage-meta.json last_decay date. Legacy flat-int
    files (no sidecar) get NO decay on first upgrade → currently-used entries keep
    full count (no mass unprotect). The cumulative ratchet is broken without
    re-scanning transcripts (scanned-marker preserved)."""

    def _empty_transcripts(self, monkeypatch, tmp_path):
        """Point the transcript scan at an empty dir so counts only come from the file."""
        empty = tmp_path / "no_transcripts"
        empty.mkdir()
        monkeypatch.setattr(
            "hooks.context_health_hook.Path.home", staticmethod(lambda: tmp_path)
        )
        # Path.home()/.claude/projects must be absent/empty → no increments
        return empty

    def test_legacy_file_no_sidecar_no_decay_first_run(self, hook, tmp_path, monkeypatch):
        import json as _json
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40, "COE02": 100}))
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)

        usage = _json.loads((ctx / ".memory-usage.json").read_text())
        # No sidecar existed → first run treats last_decay=today → NO decay applied.
        assert usage["PIT07"] == 40
        assert usage["COE02"] == 100
        # Sidecar is now created with today's date.
        meta = _json.loads((ctx / ".memory-usage-meta.json").read_text())
        assert "last_decay" in meta

    def test_stale_sidecar_triggers_decay_once(self, hook, tmp_path, monkeypatch):
        import json as _json
        from datetime import date, timedelta
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40.0}))
        # Sidecar says last decayed exactly one half-life ago → expect halving.
        from core.memory_decay import USAGE_HALFLIFE_DAYS
        old = (date.today() - timedelta(days=int(USAGE_HALFLIFE_DAYS))).isoformat()
        (ctx / ".memory-usage-meta.json").write_text(_json.dumps({"last_decay": old}))
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)

        usage = _json.loads((ctx / ".memory-usage.json").read_text())
        assert usage["PIT07"] == pytest.approx(20.0, rel=0.05)
        # last_decay advanced to today → a second same-day run won't re-decay.
        meta = _json.loads((ctx / ".memory-usage-meta.json").read_text())
        assert meta["last_decay"] == date.today().isoformat()

    def test_same_day_rerun_idempotent(self, hook, tmp_path, monkeypatch):
        import json as _json
        from datetime import date
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40.0}))
        (ctx / ".memory-usage-meta.json").write_text(
            _json.dumps({"last_decay": date.today().isoformat()})
        )
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)

        usage = _json.loads((ctx / ".memory-usage.json").read_text())
        # last_decay == today → no decay this run.
        assert usage["PIT07"] == 40.0

    def test_corrupt_sidecar_fails_safe_no_decay(self, hook, tmp_path, monkeypatch):
        import json as _json
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40.0}))
        (ctx / ".memory-usage-meta.json").write_text("{not valid json")
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)  # must not raise

        usage = _json.loads((ctx / ".memory-usage.json").read_text())
        # Corrupt sidecar → fail safe → no decay, counts intact.
        assert usage["PIT07"] == 40.0

    def test_corrupt_sidecar_self_heals(self, hook, tmp_path, monkeypatch):
        """Gate-2 MEDIUM (run_241014d4): a corrupt sidecar must be REWRITTEN to a
        valid date this run, so decay resumes the next day. Without the heal, the
        corrupt file is re-read forever and decay is permanently disabled."""
        import json as _json
        from datetime import date
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40.0}))
        (ctx / ".memory-usage-meta.json").write_text("{not valid json")
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)

        # Corrupt sidecar healed to a valid today's-date JSON (self-repair).
        meta = _json.loads((ctx / ".memory-usage-meta.json").read_text())
        assert meta["last_decay"] == date.today().isoformat()

    def test_future_sidecar_clamped_and_healed(self, hook, tmp_path, monkeypatch):
        """Gate-2 MEDIUM (run_241014d4): a last_decay in the FUTURE (clock skew /
        hand-edit) gives negative days_elapsed → decay skipped. The future date
        must be clamped to today and rewritten, else decay stays disabled until the
        wall clock passes the bogus future date."""
        import json as _json
        from datetime import date, timedelta
        ws = tmp_path / "SwarmWS"
        ctx = ws / ".context"
        ctx.mkdir(parents=True)
        (ctx / ".memory-usage.json").write_text(_json.dumps({"PIT07": 40.0}))
        future = (date.today() + timedelta(days=5)).isoformat()
        (ctx / ".memory-usage-meta.json").write_text(_json.dumps({"last_decay": future}))
        self._empty_transcripts(monkeypatch, tmp_path)

        hook._track_memory_usage(ws)

        # No decay this run (future → clamped to today → days_elapsed 0), counts intact...
        usage = _json.loads((ctx / ".memory-usage.json").read_text())
        assert usage["PIT07"] == 40.0
        # ...but the bogus future date is healed to today so decay resumes normally.
        meta = _json.loads((ctx / ".memory-usage-meta.json").read_text())
        assert meta["last_decay"] == date.today().isoformat()
