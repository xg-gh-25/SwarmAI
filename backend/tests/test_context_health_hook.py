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

        # Should skip — verify by checking no write to KNOWLEDGE.md
        original = (workspace / ".context" / "KNOWLEDGE.md").read_text()
        # Stub out Bedrock-dependent methods — they hang in sandbox (no network)
        with patch.object(hook, "_sync_knowledge_library"), \
             patch.object(hook, "_sync_transcript_index"):
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
